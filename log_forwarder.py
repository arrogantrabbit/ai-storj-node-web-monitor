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

# --- Centralized Logging ---
log = logging.getLogger("StorjLogForwarder")

# --- Global State ---
# A thread-safe queue to pass logs from the file reader thread to the asyncio event loop
LOG_QUEUE = asyncio.Queue(maxsize=10000)
# A set of all connected client writers
CONNECTED_CLIENTS: Set[asyncio.StreamWriter] = set()

def file_tailer_thread(log_path: str, loop: asyncio.AbstractEventLoop, shutdown_event: threading.Event):
    """
    An efficient, event-driven log reader that runs in a separate thread.
    Uses watchdog for file system notifications and falls back to polling.
    Handles log rotation and truncation gracefully. Puts (arrival_time, line)
    tuples onto the asyncio queue.
    """
    log.info(f"Starting event-driven file reader for {log_path}")
    file_changed_event = threading.Event()
    directory = os.path.dirname(log_path) or '.'

    class ChangeHandler(FileSystemEventHandler):
        def on_any_event(self, event):
            # This robustly handles modification, creation, and move/rename rotations.
            file_changed_event.set()

    observer = Observer()
    handler = ChangeHandler()

    if not os.path.isdir(directory):
        log.critical(f"Log directory '{directory}' does not exist. Cannot start tailer.")
        return

    observer.schedule(handler, directory, recursive=False)
    observer.start()
    log.info(f"Watching directory '{directory}' for log file changes.")

    f = None
    current_inode = None
    try:
        while not shutdown_event.is_set():
            if f is None:
                try:
                    f = open(log_path, 'r', encoding='utf-8')
                    current_inode = os.fstat(f.fileno()).st_ino
                    log.info(f"Tailing log file '{log_path}' (inode: {current_inode})")
                    f.seek(0, os.SEEK_END)
                except FileNotFoundError:
                    log.warning(f"Log file '{log_path}' not found. Waiting for it to be created...")
                    shutdown_event.wait(5.0)
                    continue
                except Exception as e:
                    log.error(f"Error opening log file '{log_path}': {e}. Retrying in 5s.")
                    shutdown_event.wait(5.0)
                    continue

            line = f.readline()
            if line:
                # Capture high-resolution timestamp and pass to the asyncio event loop
                arrival_time = time.time()
                try:
                    loop.call_soon_threadsafe(LOG_QUEUE.put_nowait, (arrival_time, line.strip()))
                except asyncio.QueueFull:
                    # To prevent a fast-producing log from blocking this thread, we just drop
                    # if the asyncio side can't keep up. The alternative is unbounded memory growth.
                    pass
                continue

            # If no line, wait for a change notification. Timeout acts as a polling fallback.
            file_changed_event.clear()
            file_changed_event.wait(timeout=5.0)
            if shutdown_event.is_set():
                break

            # After wake-up, check for log rotation or truncation.
            try:
                st = os.stat(log_path)
                if st.st_ino != current_inode:
                    log.info(f"Log rotation detected for '{log_path}'. Re-opening.")
                    f.close()
                    f = None
                    continue

                if f.tell() > st.st_size:
                    log.warning(f"Log truncation detected for '{log_path}'. Resetting to start.")
                    f.seek(0)
            except FileNotFoundError:
                log.warning(f"Log file '{log_path}' has disappeared. Will attempt to re-open.")
                f.close()
                f = None
            except Exception as e:
                log.error(f"Error checking log status for '{log_path}': {e}", exc_info=True)
                f.close()
                f = None
                shutdown_event.wait(5)
    finally:
        observer.stop()
        observer.join()
        if f:
            f.close()
        log.info(f"File tailer for {log_path} has stopped.")


async def broadcast_log_entries():
    """Pulls log entries from the queue and sends them to all connected clients."""
    while True:
        try:
            timestamp, line = await LOG_QUEUE.get()

            # If there are no clients, we still need to consume from the queue to prevent it from filling up.
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
                    # We await drain() for each client to handle backpressure.
                    # This ensures one slow client doesn't cause buffer overflows for others.
                    await writer.drain()
                except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError) as e:
                    log.warning(f"Client {writer.get_extra_info('peername')} disconnected abruptly: {e}")
                    disconnected_clients.add(writer)
                except Exception:
                     log.error(f"Unexpected error writing to client {writer.get_extra_info('peername')}", exc_info=True)
                     disconnected_clients.add(writer)

            # Clean up any clients that disconnected during the broadcast
            if disconnected_clients:
                for writer in disconnected_clients:
                    if writer in CONNECTED_CLIENTS:
                        CONNECTED_CLIENTS.remove(writer)
                    if not writer.is_closing():
                        writer.close()

            LOG_QUEUE.task_done()
        except asyncio.CancelledError:
            log.info("Log broadcast task is shutting down.")
            break
        except Exception:
            log.error("Critical error in log broadcast task:", exc_info=True)


async def handle_new_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Callback for when a new client connects to the server."""
    peer_addr = writer.get_extra_info('peername')
    log.info(f"Client connected: {peer_addr}")
    CONNECTED_CLIENTS.add(writer)

    try:
        # We don't expect the client to send anything. We just wait here until the connection
        # is closed by the client or an error occurs.
        await reader.read(1)
        log.info(f"Client {peer_addr} closed the connection.")
    except (ConnectionResetError, asyncio.IncompleteReadError):
        log.info(f"Client {peer_addr} connection reset.")
    except asyncio.CancelledError:
        log.info(f"Connection task for {peer_addr} cancelled.")
        raise
    finally:
        if writer in CONNECTED_CLIENTS:
            CONNECTED_CLIENTS.remove(writer)
        if not writer.is_closing():
            writer.close()
            await writer.wait_closed()
        log.info(f"Cleaned up connection for {peer_addr}. Total clients: {len(CONNECTED_CLIENTS)}")


async def main(args):
    """Main async function to set up and run the server."""
    loop = asyncio.get_running_loop()
    shutdown_event = threading.Event()

    # The file tailer must run in a separate thread because watchdog is blocking
    tailer = threading.Thread(
        target=file_tailer_thread,
        args=(args.log_file, loop, shutdown_event),
        daemon=True,
        name="FileTailerThread"
    )
    tailer.start()

    # The broadcaster task runs in the main asyncio event loop
    broadcast_task = asyncio.create_task(broadcast_log_entries())

    server = await asyncio.start_server(
        handle_new_connection, args.host, args.port
    )

    server_addr = server.sockets[0].getsockname()
    log.info(f"Log forwarder started. Listening on {server_addr[0]}:{server_addr[1]}")
    log.info(f"Forwarding logs from: {os.path.abspath(args.log_file)}")

    try:
        await server.serve_forever()
    except asyncio.CancelledError:
        pass  # This is expected on shutdown
    finally:
        log.info("Shutdown sequence initiated...")
        shutdown_event.set()
        broadcast_task.cancel()
        server.close()
        await server.wait_closed()
        
        # Cleanly close all remaining client connections
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
        '--log-file',
        type=str,
        required=True,
        help="Path to the storagenode log file to monitor."
    )
    parser.add_argument(
        '--host',
        type=str,
        default="0.0.0.0",
        help="The host address to bind the server to. Defaults to '0.0.0.0' (all interfaces)."
    )
    parser.add_argument(
        '--port',
        type=int,
        required=True,
        help="The TCP port to listen on for incoming connections from the monitor."
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help="Enable verbose debug logging."
    )
    args = parser.parse_args()

    # Configure logging
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
