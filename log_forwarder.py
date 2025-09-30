#!/usr/bin/env python3

# /// script
# # No external dependencies needed anymore, watchdog is removed.
# requires-python = ">=3.11"
# ///

import asyncio
import argparse
import logging
import os
import sys
import time
from typing import Set, Optional
import functools

# --- Centralized Logging ---
log = logging.getLogger("StorjLogForwarder")

# --- Global State ---
LOG_QUEUE = asyncio.Queue(maxsize=10000)
CONNECTED_CLIENTS: Set[asyncio.StreamWriter] = set()
# We now manage an asyncio Task and Process, not a thread.
TAIL_TASK: Optional[asyncio.Task] = None
TAIL_PROCESS: Optional[asyncio.subprocess.Process] = None

async def tail_log_file(log_path: str):
    """
    Starts a 'tail -F' subprocess and forwards its stdout to the LOG_QUEUE.
    This coroutine is designed to be cancelled when no clients are connected.
    """
    global TAIL_PROCESS
    log.info(f"Starting 'tail -F' on {log_path}")
    try:
        # -F: Follow by filename, handles log rotation.
        # -n 0: Start from the end of the file.
        TAIL_PROCESS = await asyncio.create_subprocess_exec(
            'tail', '-F', '-n', '0', log_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # Process stdout
        async for line in TAIL_PROCESS.stdout:
            # The timestamp is generated the moment we read the line from the pipe.
            # This is highly accurate and avoids any artificial delays.
            await LOG_QUEUE.put((time.time(), line.decode('utf-8', errors='replace').strip()))

        # If we exit the loop, check for errors from tail's stderr
        stderr_output = await TAIL_PROCESS.stderr.read()
        if stderr_output:
            log.error(f"'tail' process exited with error: {stderr_output.decode('utf-8', errors='replace').strip()}")

    except FileNotFoundError:
        log.critical(f"Fatal: 'tail' command not found. Please ensure it is installed and in your PATH.")
        # In a real-world scenario, you might want to trigger a more graceful shutdown.
        # For simplicity, we'll let the exception propagate to the main loop.
        raise
    except asyncio.CancelledError:
        log.info("'tail' task is being cancelled.")
    except Exception:
        log.error("An unexpected error occurred in the tail task.", exc_info=True)
    finally:
        if TAIL_PROCESS and TAIL_PROCESS.returncode is None:
            log.info("Terminating 'tail' process.")
            try:
                TAIL_PROCESS.terminate()
                await TAIL_PROCESS.wait()
            except ProcessLookupError:
                pass # Process already finished
        TAIL_PROCESS = None
        log.info("'tail' process stopped.")

async def stop_tailing_if_needed():
    """Cancels the tailing task if no clients remain."""
    global TAIL_TASK
    if not CONNECTED_CLIENTS and TAIL_TASK:
        log.info("Last client disconnected. Stopping file tailing.")
        try:
            TAIL_TASK.cancel()
            await TAIL_TASK
        except asyncio.CancelledError:
            pass # Expected
        TAIL_TASK = None

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
                 for writer in disconnected_clients:
                    if writer in CONNECTED_CLIENTS: CONNECTED_CLIENTS.remove(writer)
                    if not writer.is_closing(): writer.close()
                 # Check if the last client was in the disconnected batch
                 await stop_tailing_if_needed()

            LOG_QUEUE.task_done()
        except asyncio.CancelledError:
            break
        except Exception:
            log.error("Critical error in broadcast task:", exc_info=True)

async def handle_new_connection(log_file: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Callback for when a new client connects."""
    global TAIL_TASK
    peer_addr = writer.get_extra_info('peername')

    was_first_client = not CONNECTED_CLIENTS
    CONNECTED_CLIENTS.add(writer)
    log.info(f"Client connected: {peer_addr}. Total clients: {len(CONNECTED_CLIENTS)}")

    if was_first_client:
        log.info("First client connected. Starting file tailing.")
        TAIL_TASK = asyncio.create_task(tail_log_file(log_file))

    try:
        # Wait for client to close the connection from their end
        await reader.read(1)
    except (ConnectionResetError, asyncio.IncompleteReadError):
        log.info(f"Client {peer_addr} connection reset.")
    finally:
        if writer in CONNECTED_CLIENTS: CONNECTED_CLIENTS.remove(writer)

        await stop_tailing_if_needed()

        if not writer.is_closing():
            writer.close()
            await writer.wait_closed()
        log.info(f"Cleaned up for {peer_addr}. Total clients: {len(CONNECTED_CLIENTS)}")

async def main(args):
    """Main async function to set up and run the server."""
    # Work with an absolute path for clarity in logs.
    log_file_path = os.path.abspath(args.log_file)

    broadcast_task = asyncio.create_task(broadcast_log_entries())

    connection_handler = functools.partial(handle_new_connection, log_file_path)
    server = await asyncio.start_server(
        connection_handler, args.host, args.port
    )

    server_addr = server.sockets[0].getsockname()
    log.info(f"Log forwarder started. Listening on {server_addr[0]}:{server_addr[1]}")
    log.info(f"Forwarding logs from: {log_file_path}")

    try:
        await server.serve_forever()
    except asyncio.CancelledError:
        pass
    finally:
        log.info("Shutdown sequence initiated...")
        broadcast_task.cancel()
        server.close(); await server.wait_closed()

        for client in list(CONNECTED_CLIENTS):
            client.close(); await client.wait_closed()

        # Ensure tailing is stopped on server shutdown
        await stop_tailing_if_needed()

        await asyncio.gather(broadcast_task, return_exceptions=True)
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
