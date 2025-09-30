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


def file_tailer_thread(log_path: str, loop: asyncio.AbstractEventLoop, shutdown_event: threading.Event, client_event: threading.Event):
    """
    An efficient, event-driven log reader that only runs its file-watching
    machinery when a client is actually connected.
    """
    log.info(f"File tailer thread started for {log_path}. Waiting for first client to connect.")

    f = None
    current_inode = None
    observer = None

    try:
        while not shutdown_event.is_set():
            # This is the primary idle state. The thread sleeps here with ~0% CPU usage.
            client_event.wait()
            if shutdown_event.is_set(): break

            # --- A client is now connected, so we start work ---
            log.info("Client connected, starting file observation.")
            file_changed_event = threading.Event()
            directory = os.path.dirname(log_path) or '.'

            class ChangeHandler(FileSystemEventHandler):
                def on_any_event(self, event):
                    file_changed_event.set()

            if not os.path.isdir(directory):
                log.error(f"Log directory '{directory}' does not exist. Pausing until client disconnects.")
                # Go back to sleep until the client disconnects and resets the state.
                client_event.clear()
                continue

            # Start the watchdog observer now that we have a client.
            observer = Observer()
            observer.schedule(ChangeHandler(), directory, recursive=False)
            observer.start()

            # This inner loop runs only while clients are connected.
            while client_event.is_set() and not shutdown_event.is_set():
                if f is None:
                    try:
                        f = open(log_path, 'r', encoding='utf-8')
                        current_inode = os.fstat(f.fileno()).st_ino
                        log.info(f"Tailing log file '{log_path}' (inode: {current_inode})")
                        f.seek(0, os.SEEK_END)
                    except FileNotFoundError:
                        log.warning(f"Log file '{log_path}' not found. Waiting for it...")
                        file_changed_event.clear()
                        file_changed_event.wait(timeout=2.0)
                        continue # Retry opening the file
                    except Exception as e:
                        log.error(f"Error opening log file '{log_path}': {e}. Retrying in 5s.")
                        time.sleep(5)
                        continue

                line = f.readline()
                if line:
                    arrival_time = time.time()
                    try:
                        loop.call_soon_threadsafe(LOG_QUEUE.put_nowait, (arrival_time, line.strip()))
                    except asyncio.QueueFull:
                        pass # Drop if consumer is slow
                    continue

                # Wait efficiently for file changes
                file_changed_event.clear()
                file_changed_event.wait(timeout=1.0)

                # After waiting, check for log rotation/truncation
                try:
                    st = os.stat(log_path)
                    if st.st_ino != current_inode:
                        log.info(f"Log rotation detected for '{log_path}'. Re-opening.")
                        f.close(); f = None; continue
                    if f and f.tell() > st.st_size:
                        log.warning(f"Log truncation detected for '{log_path}'. Resetting.")
                        f.seek(0)
                except FileNotFoundError:
                    log.warning(f"Log file '{log_path}' disappeared. Will re-open.")
                    if f: f.close(); f=None
                except Exception as e:
                    log.error(f"Error checking log status: {e}", exc_info=True)
                    if f: f.close(); f=None
                    time.sleep(5)

            # --- Client has disconnected or shutdown, so we clean up ---
            log.info("Client disconnected or shutdown initiated. Stopping file observation.")
            if observer and observer.is_alive():
                observer.stop()
                observer.join()
            observer = None
            if f:
                f.close()
                f = None

    finally:
        log.info(f"File tailer for {log_path} has shut down.")
        if observer and observer.is_alive():
            observer.stop()
            observer.join()
        if f:
            f.close()

async def broadcast_log_entries():
    """Pulls log entries from the queue and sends them to all connected clients."""
    while True:
        try:
            timestamp, line = await LOG_QUEUE.get()

            if not CONNECTED_CLIENTS:
                LOG_QUEUE.task_done()
                continue

            message = f"{timestamp} {line}\n".encode('utf-8')
            disconnected_clients = set()

            for writer in CONNECTED_CLIENTS:
                if writer.is_closing():
                    disconnected_clients.add(writer)
                    continue
                try:
                    writer.write(message)
                    await writer.drain()
                except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                    disconnected_clients.add(writer)
                except Exception:
                     log.error(f"Unexpected error writing to client {writer.get_extra_info('peername')}", exc_info=True)
                     disconnected_clients.add(writer)

            if disconnected_clients:
                 loop = asyncio.get_running_loop()
                 for writer in disconnected_clients:
                    if writer in CONNECTED_CLIENTS:
                        CONNECTED_CLIENTS.remove(writer)
                    if not writer.is_closing(): writer.close()

                 if not CONNECTED_CLIENTS:
                    log.info("Last client disconnected. Signaling file tailer to pause.")
                    await loop.run_in_executor(None, client_present_event.clear)

            LOG_QUEUE.task_done()
        except asyncio.CancelledError:
            log.info("Log broadcast task is shutting down.")
            break
        except Exception:
            log.error("Critical error in log broadcast task:", exc_info=True)


async def handle_new_connection(client_event: threading.Event, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Callback for when a new client connects to the server."""
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
        log.info(f"Client {peer_addr} closed the connection.")
    except (ConnectionResetError, asyncio.IncompleteReadError):
        log.info(f"Client {peer_addr} connection reset.")
    except asyncio.CancelledError:
        raise
    finally:
        if writer in CONNECTED_CLIENTS:
            CONNECTED_CLIENTS.remove(writer)

        if not CONNECTED_CLIENTS:
            log.info("Last client disconnected. Signaling file tailer to pause.")
            await loop.run_in_executor(None, client_event.clear)

        if not writer.is_closing():
            writer.close()
            await writer.wait_closed()
        log.info(f"Cleaned up connection for {peer_addr}. Total clients: {len(CONNECTED_CLIENTS)}")


async def main(args):
    """Main async function to set up and run the server."""
    loop = asyncio.get_running_loop()
    shutdown_event = threading.Event()

    tailer = threading.Thread(
        target=file_tailer_thread,
        args=(args.log_file, loop, shutdown_event, client_present_event),
        daemon=True,
        name="FileTailerThread"
    )
    tailer.start()

    broadcast_task = asyncio.create_task(broadcast_log_entries())

    connection_handler = functools.partial(handle_new_connection, client_present_event)
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
        client_present_event.set() # Wake up the tailer so it can see the shutdown event
        broadcast_task.cancel()
        server.close()
        await server.wait_closed()

        for client_writer in list(CONNECTED_CLIENTS):
            client_writer.close()
            await client_writer.wait_closed()

        await asyncio.gather(broadcast_task, return_exceptions=True)
        tailer.join(timeout=5)
        log.info("Shutdown complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="A lightweight log forwarder for Storj nodes.",
        epilog="This tool tails a log file, prepends each line with a high-resolution timestamp, and broadcasts it over a TCP socket."
    )
    parser.add_argument(
        '--log-file', type=str, required=True,
        help="Path to the storagenode log file to monitor."
    )
    parser.add_argument(
        '--host', type=str, default="0.0.0.0",
        help="The host address to bind the server to. Defaults to '0.0.0.0'."
    )
    parser.add_argument(
        '--port', type=int, required=True,
        help="The TCP port to listen on for incoming connections from the monitor."
    )
    parser.add_argument(
        '--debug', action='store_true', help="Enable verbose debug logging."
    )
    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    try:
        log.info("Starting log forwarder...")
        asyncio.run(main(args))
    except KeyboardInterrupt:
        log.info("Shutdown requested by user (Ctrl+C).")
    except Exception as e:
        log.critical(f"A critical error occurred: {e}", exc_info=True)
        sys.exit(1)
