#!/usr/bin/env python3
"""
Board Diagnostic Tool for ATmega32 Reed-Switch Chessboard.
Dedicated to testing and verifying all 64 magnetic reed switches
without any chess rules, engine calculations, or motor movements.

Features:
- Connects over Bluetooth (/dev/cu.HC-05).
- Displays live colored 8x8 ASCII grid ([X] = Magnet Detected, [.] = Empty).
- Shows exact physical Multiplexer (0..3) and Channel (C0..C15) for every active switch.
- Auto-polls (--poll) or updates whenever the physical INT0 button is pressed.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Set

from serial_connection import (
    SerialConnection, auto_detect_bluetooth_port, parse_board_frame,
    C_RESET, C_BOLD, C_GREEN, C_YELLOW, C_CYAN, C_RED, C_DIM,
)


def get_mux_and_channel(square: str) -> str:
    """Returns hardware Mux number and Channel (C0..C15) for a given square."""
    file_char = square[0].lower()
    rank_num = int(square[1])

    file_idx = ord(file_char) - ord('a')
    mux_num = file_idx // 2
    is_second_file = (file_idx % 2 == 1)

    channel_idx = (rank_num - 1) + (8 if is_second_file else 0)
    sig_pin = f"PA{4 + mux_num}"
    return f"Mux {mux_num} ({sig_pin}), Channel C{channel_idx}"


def render_diagnostic_grid(active_squares: Set[str]) -> str:
    """Renders 8x8 ASCII board with active squares highlighted in green."""
    lines = []
    lines.append(f"{C_BOLD}     a   b   c   d   e   f   g   h{C_RESET}")
    lines.append("   +---+---+---+---+---+---+---+---+")

    for rank in range(8, 0, -1):
        row = f" {rank} |"
        for file_idx in range(8):
            sq = f"{chr(ord('a') + file_idx)}{rank}"
            if sq in active_squares:
                cell = f"{C_GREEN}{C_BOLD}[X]{C_RESET}"
            else:
                cell = f"{C_DIM} . {C_RESET}"
            row += f"{cell}|"
        row += f" {rank}"
        lines.append(row)
        lines.append("   +---+---+---+---+---+---+---+---+")

    lines.append(f"{C_BOLD}     a   b   c   d   e   f   g   h{C_RESET}")
    return "\n".join(lines)


def run_diagnostic(port: Optional[str] = None, poll_interval: Optional[float] = None):
    if not port:
        port = auto_detect_bluetooth_port()
        if not port:
            print(f"{C_RED}No Bluetooth HC-05 device found.{C_RESET}")
            print("Ensure your Mac is paired with the HC-05 in System Settings -> Bluetooth.")
            sys.exit(1)

    if port.startswith("/dev/tty."):
        port = port.replace("/dev/tty.", "/dev/cu.")

    print(f"\n{C_BOLD}======================================================{C_RESET}")
    print(f"  {C_CYAN}ATmega32 Reed-Switch Chessboard Diagnostic Tool{C_RESET}")
    print(f"{C_BOLD}======================================================{C_RESET}")
    print(f"Target Port : {C_GREEN}{port}{C_RESET}")
    print(f"Mode        : {'Auto-Polling every ' + str(poll_interval) + 's' if poll_interval else 'Button Trigger (PD2 / INT0)'}")
    print("Press Ctrl+C to exit at any time.\n")

    conn = SerialConnection(port, 9600)
    print(f"Connecting to Bluetooth module at {port}...")

    if not conn.connect_with_handshake():
        sys.exit(1)

    print("\nRequesting initial board scan...")
    conn.write("SCAN\r\n")

    import time
    last_poll = time.time()
    scan_count = 0

    try:
        while True:
            if poll_interval and (time.time() - last_poll >= poll_interval):
                conn.write("SCAN\r\n")
                last_poll = time.time()

            line = conn.read_line(timeout=0.1)
            if line and line.startswith("BOARD:"):
                scan_count += 1
                active_sqs = parse_board_frame(line)
                if active_sqs is not None:
                    active_set = set(active_sqs)
                    print("\033[2J\033[H")
                    print(f"{C_BOLD}=== Live Reed Switch Sensor Grid (Scan #{scan_count}) ==={C_RESET}")
                    print(render_diagnostic_grid(active_set))

                    count = len(active_set)
                    if count == 0:
                        print(f"\n{C_YELLOW}No magnets detected.{C_RESET} Place a magnet on any square to test.")
                    else:
                        print(f"\n{C_GREEN}{C_BOLD}✓ {count} Magnet{'s' if count != 1 else ''} Detected:{C_RESET}")
                        for sq in sorted(list(active_set)):
                            wiring_info = get_mux_and_channel(sq)
                            print(f"  • Square {C_BOLD}{C_GREEN}{sq.upper()}{C_RESET}: {C_CYAN}{wiring_info}{C_RESET}")

                    print(f"\n{C_DIM}Tip: Tap button on PD2 or place/remove a magnet. Press Ctrl+C to quit.{C_RESET}")

    except KeyboardInterrupt:
        print("\nExiting diagnostic tool.")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Live 64-Square Reed Switch Hardware Diagnostic Tool for ATmega32."
    )
    parser.add_argument("-p", "--port", type=str, default=None, help="Bluetooth port (default: auto-detect /dev/cu.HC-05)")
    parser.add_argument("--poll", type=float, default=None, nargs="?", const=0.8,
                        help="Auto-poll continuously every N seconds (default: 0.8s) so you don't have to press the button.")

    args = parser.parse_args()
    run_diagnostic(args.port, args.poll)


if __name__ == "__main__":
    main()
