#!/usr/bin/env python3

# /// script
# dependencies = ["watchdog"]
# requires-python = ">=3.11"
# ///

import asyncio
import argparse
import logging
import os
import sys
import threading
import time
from typing import Set
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import functools

# --- Centralized Logging ---
log = logging.getLogger("StorjLogForwarder")

# --- Global State ---
LOG_QUEUE = asyncio.Queue(maxsize=10000)
CONNECTED_CLIENTS: Set[asyncio.StreamWriter] = set()
client_present_event = threading.Event()
# A separate event for file changes, which we can also use to wake the thread
file_changed_event = threading.Event()

def file_tailer_thread(log_path: str, loop: asyncio.AbstractEventLoop, shutdown_event: threading.Event, client_event: threading.Event, f_event: threading.Event):
    """
    A purely event-driven log reader. It only starts watchdog when a client is
    present and uses blocking waits to consume 0% CPU when the log file is idle.
    """
    log.info(f"File tailer thread started for {log_path}. Waiting for client.")

    def read_all_new_lines(f):
        """Reads all available lines from the current file position."""
        while True:
            line = f.readline()
            if not line:
                break
            try:
                loop.call_soon_threadsafe(LOG_QUEUE.put_nowait, (time.time(), line.strip()))
            except asyncio.QueueFull:
                pass # Drop if consumer is slow

    observer = None
    try:
        while not shutdown_event.is_set():
            # STATE 1: IDLE (No clients) - Zero CPU usage
            # The thread sleeps here until the main loop sets the event.
            client_event.wait()
            if shutdown_event.is_set(): break

            # STATE 2: ACTIVE (Clients are present) - Start monitoring
            log.info("Client connected, starting file observation.")
            directory = os.path.dirname(log_path) or '.'

            class ChangeHandler(FileSystemEventHandler):
                def on_any_event(self, event):
                    f_event.set() # Signal that something happened

            if not os.path.isdir(directory):
                log.error(f"Log directory '{directory}' does not exist. Pausing.")
                client_event.clear() # Go back to idle state
                continue

            observer = Observer()
            observer.schedule(ChangeHandler(), directory, recursive=False)
            observer.start()

            try:
                with open(log_path, 'r', encoding='utf-8') as f:
                    f.seek(0, os.SEEK_END)
                    current_inode = os.fstat(f.fileno()).st_ino
                    # Initial read in case the file was written to between open and now
                    read_all_new_lines(f)

                    # Inner loop: main active state
                    while client_event.is_set() and not shutdown_event.is_set():
                        # STATE 3: WAITING (Clients connected, log idle) - Zero CPU usage
                        # The thread sleeps here until watchdog OR the disconnect handler sets the event.
                        f_event.wait()
                        f_event.clear()

                        # If we were woken up but clients are now gone, break to cleanup.
                        if not client_event.is_set() or shutdown_event.is_set():
                            break

                        # Check for log rotation. If it happened, break inner loop to reopen.
                        try:
                            if os.stat(log_path).st_ino != current_inode:
                                log.info("Log rotation detected. Re-opening file.")
                                break
                        except FileNotFoundError:
                            log.warning("Log file disappeared during check. Re-opening.")
                            break

                        read_all_new_lines(f)
            except FileNotFoundError:
                log.warning(f"Log file '{log_path}' not found. Will retry after a delay.")
                time.sleep(2) # Don't spin if the file is missing
            except Exception as e:
                log.error(f"Error during file tailing: {e}. Retrying after delay.", exc_info=True)
                time.sleep(5)
            finally:
                # STATE 4: CLEANUP (Client disconnected) - Stop observer
                log.info("Client disconnected or file error. Stopping file observation.")
                if observer and observer.is_alive():
                    observer.stop()
                    observer.join()
                observer = None

    finally:
        log.info("File tailer thread has shut down.")
        if observer and observer.is_alive():
            observer.stop()
            observer.join()


async def broadcast_log_entries():
    """Pulls log entries from the queue and sends them to all connected clients."""
    while True:
        try:
            timestamp, line = await LOG_QUEUE.get()
            if not CONNECTED_CLIENTS:
                LOG_QUEUE.task_done(); continue

            message = f"{timestamp} {line}\n".encode('utf-8')
            disconnected_clients = set()

            for writer in CONNECTED_CLIENTS:
                if writer.is_closing():
                    disconnected_clients.add(writer); continue
                try:
                    writer.write(message)
                    await writer.drain()
                except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                    disconnected_clients.add(writer)
                except Exception:
                     log.error(f"Error writing to client {writer.get_extra_info('peername')}", exc_info=True)
                     disconnected_clients.add(writer)

            if disconnected_clients:
                 loop = asyncio.get_running_loop()
                 for writer in disconnected_clients:
                    if writer in CONNECTED_CLIENTS: CONNECTED_CLIENTS.remove(writer)
                    if not writer.is_closing(): writer.close()

                 if not CONNECTED_CLIENTS:
                    log.info("Last client disconnected. Signaling file tailer to pause.")
                    # This must unblock both client_event and file_changed_event
                    await loop.run_in_executor(None, lambda: (client_present_event.clear(), file_changed_event.set()))

            LOG_QUEUE.task_done()
        except asyncio.CancelledError:
            break
        except Exception:
            log.error("Critical error in broadcast task:", exc_info=True)

async def handle_new_connection(client_event: threading.Event, f_event: threading.Event, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Callback for when a new client connects."""
    peer_addr = writer.get_extra_info('peername')
    loop = asyncio.get_running_loop()

    was_first_client = not CONNECTED_CLIENTS
    CONNECTED_CLIENTS.add(writer)
    log.info(f"Client connected: {peer_addr}. Total clients: {len(CONNECTED_CLIENTS)}")

    if was_first_client:
        log.info("First client connected. Signaling file tailer to resume.")
        await loop.run_in_executor(None, client_event.set)

    try:
        await reader.read(1)
    except (ConnectionResetError, asyncio.IncompleteReadError):
        log.info(f"Client {peer_addr} connection reset.")
    finally:
        if writer in CONNECTED_CLIENTS: CONNECTED_CLIENTS.remove(writer)

        if not CONNECTED_CLIENTS:
            log.info("Last client disconnected. Signaling file tailer to pause.")
            # Wake the file tailer thread so it can notice client_event is now clear.
            await loop.run_in_executor(None, lambda: (client_event.clear(), f_event.set()))

        if not writer.is_closing():
            writer.close()
            await writer.wait_closed()
        log.info(f"Cleaned up for {peer_addr}. Total clients: {len(CONNECTED_CLIENTS)}")

async def main(args):
    """Main async function to set up and run the server."""
    loop = asyncio.get_running_loop()
    shutdown_event = threading.Event()

    tailer = threading.Thread(
        target=file_tailer_thread,
        args=(args.log_file, loop, shutdown_event, client_present_event, file_changed_event),
        daemon=True, name="FileTailerThread"
    )
    tailer.start()

    broadcast_task = asyncio.create_task(broadcast_log_entries())

    connection_handler = functools.partial(handle_new_connection, client_present_event, file_changed_event)
    server = await asyncio.start_server(
        connection_handler, args.host, args.port
    )

    server_addr = server.sockets[0].getsockname()
    log.info(f"Log forwarder started. Listening on {server_addr[0]}:{server_addr[1]}")
    log.info(f"Forwarding logs from: {os.path.abspath(args.log_file)}")

    try:
        await server.serve_forever()
    except asyncio.CancelledError:
        pass
    finally:
        log.info("Shutdown sequence initiated...")
        shutdown_event.set()
        client_present_event.set()
        file_changed_event.set() # Wake up the tailer from any wait state
        broadcast_task.cancel()
        server.close(); await server.wait_closed()

        for client in list(CONNECTED_CLIENTS):
            client.close(); await client.wait_closed()

        await asyncio.gather(broadcast_task, return_exceptions=True)
        tailer.join(timeout=5)
        log.info("Shutdown complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A lightweight log forwarder for Storj nodes.")
    parser.add_argument('--log-file', type=str, required=True, help="Path to the storagenode log file to monitor.")
    parser.add_argument('--host', type=str, default="0.0.0.0", help="Host address to bind the server to.")
    parser.add_argument('--port', type=int, required=True, help="TCP port to listen on.")
    parser.add_argument('--debug', action='store_true', help="Enable verbose debug logging.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    try:
        log.info("Starting log forwarder...")
        asyncio.run(main(args))
    except KeyboardInterrupt:
        log.info("Shutdown requested by user (Ctrl+C).")
    except Exception as e:
        log.critical(f"A critical error occurred: {e}", exc_info=True)
        sys.exit(1)
