import sys
import json
import os
from collections import defaultdict
from typing import Dict, List, Tuple, Optional, Set

# --- Configuration ---
# The maximum number of log lines to store for any single unique combination
MAX_EXAMPLES_PER_COMBINATION = 3
# Default log file path if none is provided via command-line arguments
DEFAULT_LOG_FILE_PATH = "storj.log"
# The structure of the log criteria keys: (Level, Source, Status, Action)
LogKey = Tuple[str, str, str, str]

# --- Core Logic ---

def parse_log_line(line: str) -> Optional[Tuple[LogKey, Optional[str]]]:
    """
    Parses a single Storj log line to extract the four core criteria
    (Log Level, Source, Status, Action) and the optional Piece ID for linking.

    Returns a tuple of (LogKey, Piece ID) on success, or None if parsing fails.
    """
    # Split the line by the tab character ('\t'). We only need the first 4 splits
    # plus the remainder (JSON part) as the 5th element.
    parts = line.strip().split('\t', 4)

    # A standard log line should have at least 5 parts:
    # 0: Timestamp | 1: LOG_LEVEL | 2: SOURCE | 3: STATUS | 4: JSON_DATA
    if len(parts) < 5:
        # Check if it's the newsyslog line (which doesn't have tabs or JSON)
        if "newsyslog" in line:
            # We skip this non-storj log line type
            return None
        return None

    try:
        log_level = parts[1]
        source = parts[2]
        status = parts[3]
        json_data_str = parts[4]

        # Safely parse the JSON payload
        json_data = json.loads(json_data_str)

        # Extract the 'Action' and 'Piece ID' fields.
        action = json_data.get("Action", "N/A")
        # Piece ID is used for linking; it might not exist in all log types.
        piece_id = json_data.get("Piece ID")

        log_key = (log_level, source, status, action)
        return (log_key, piece_id)

    except json.JSONDecodeError:
        # This handles non-JSON lines or malformed JSON payloads
        return None
    except Exception:
        # Catch any other unexpected issues during parsing
        return None


def extract_examples(file_path: str) -> Dict[LogKey, List[str]]:
    """
    Reads a large log file line by line and extracts unique examples, prioritizing
    lines whose 'Piece ID' links them to an already selected example.

    It does not load the entire file into memory, ensuring high performance
    for multi-GB files.
    """
    # Dictionary to store the unique combinations found and their examples
    # Key: (Log Level, Source, Status, Action)
    # Value: List of log lines (up to MAX_EXAMPLES_PER_COMBINATION)
    unique_examples: Dict[LogKey, List[str]] = defaultdict(list)

    # Secondary structure to track which Piece IDs have been collected, and under which LogKeys.
    # This allows us to check for the "companion" log line (same piece_id, different LogKey).
    # Key: Piece ID (str)
    # Value: Set of LogKeys (Tuple[str, str, str, str]) associated with this Piece ID.
    collected_piece_log_keys: Dict[str, Set[LogKey]] = defaultdict(set)

    total_lines_read = 0

    print(f"\n[*] Starting to process log file: {file_path}", file=sys.stderr)
    print(f"[*] Maximum examples per unique combination: {MAX_EXAMPLES_PER_COMBINATION}", file=sys.stderr)

    try:
        # 'errors='ignore'' handles potential encoding issues gracefully
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                total_lines_read += 1
                parsed_result = parse_log_line(line)

                if parsed_result:
                    log_key, piece_id = parsed_result
                    line_to_store = line.strip()

                    # 1. Check if the list for this key is already full
                    has_space = len(unique_examples[log_key]) < MAX_EXAMPLES_PER_COMBINATION

                    # 2. Determine if this line is a "linked" entry
                    # It's linked if its Piece ID is available and has already been recorded under a *different* LogKey.
                    is_linked_entry = piece_id and (piece_id in collected_piece_log_keys) and (log_key not in collected_piece_log_keys[piece_id])

                    # Priority 1: If it's a linked entry AND we have space, grab it.
                    # This ensures we prioritize matching a "start" with a "finished" line, for example.
                    if is_linked_entry and has_space:
                        unique_examples[log_key].append(line_to_store)
                        collected_piece_log_keys[piece_id].add(log_key)

                    # Priority 2: If we still have space and it hasn't been added yet (it's new or the first time seeing this combination).
                    # We store it only if it is not a linked entry (to prevent double-storing lines already handled by P1)
                    # OR if the piece_id is None (not a piece-related log line).
                    elif has_space and not is_linked_entry:
                        # Only proceed if we aren't adding a line that should have been prioritized by P1
                        # or if it's a non-piece log line (piece_id is None).
                        unique_examples[log_key].append(line_to_store)
                        if piece_id:
                            collected_piece_log_keys[piece_id].add(log_key)

                # Optional: Print progress every 1 million lines for massive files
                if total_lines_read % 1000000 == 0:
                    print(f"[*] Processed {total_lines_read:,} lines...", file=sys.stderr)

        print(f"[*] Finished processing. Total lines read: {total_lines_read:,}", file=sys.stderr)
        print(f"[*] Found {len(unique_examples)} unique log criteria combinations.", file=sys.stderr)
        return unique_examples

    except FileNotFoundError:
        print(f"[ERROR] File not found at path: {file_path}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[CRITICAL ERROR] An unexpected error occurred: {e}", file=sys.stderr)
        sys.exit(1)


def print_summary(examples: Dict[LogKey, List[str]]):
    """
    Prints only the raw collected unique log examples to standard output.
    """
    if not examples:
        # Keep essential error message if no examples are found
        print("No log lines matching the expected format were found. Check file path and log format.", file=sys.stderr)
        return

    # Iterate through all combinations (keys)
    # Sorting ensures a consistent output order, though not strictly required for raw output
    for log_key in sorted(examples.keys()):
        lines = examples[log_key]
        # Iterate through all collected lines for this combination
        for line in lines:
            # Print only the raw log line content, followed by a newline
            print(line)


def main():
    """
    Main execution function, handles command line arguments.
    """
    if len(sys.argv) > 1:
        log_path = sys.argv[1]
    else:
        log_path = DEFAULT_LOG_FILE_PATH
        print(f"*** WARNING: No file path provided. Using default: '{DEFAULT_LOG_FILE_PATH}' ***", file=sys.stderr)
        if not os.path.exists(log_path):
             print(f"*** ERROR: Default file path '{DEFAULT_LOG_FILE_PATH}' does not exist. Please run with a file path argument. ***", file=sys.stderr)
             sys.exit(1)

    examples = extract_examples(log_path)
    print_summary(examples)


if __name__ == "__main__":
    main()

