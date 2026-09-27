#!/usr/bin/env python3
"""
Unified CLI for Automated Chessboard.
Single entry point with mode selection:
  1. Full Game — play against Stockfish
  2. Board Diagnostic — test reed switches
  3. Gantry Diagnostic — test motors and magnet
"""

from __future__ import annotations

import argparse
import sys

from serial_connection import (
    SerialConnection, auto_detect_bluetooth_port,
    C_RESET, C_BOLD, C_GREEN, C_YELLOW, C_CYAN, C_RED,
)


def connect_hardware(port: str | None, baudrate: int, microstep: int, speed: int) -> SerialConnection | None:
    """Shared hardware init: detect port, connect, handshake, configure."""
    if not port:
        port = auto_detect_bluetooth_port()
        if not port:
            print(f"{C_RED}No Bluetooth HC-05 device found.{C_RESET}")
            print(f"{C_YELLOW}Ensure HC-05 is paired in System Settings -> Bluetooth.{C_RESET}")
            return None
        print(f"Auto-detected Bluetooth port: {C_GREEN}{port}{C_RESET}")

    if port.startswith("/dev/tty."):
        port = port.replace("/dev/tty.", "/dev/cu.")

    serial = SerialConnection(port, baudrate)
    print(f"Connecting to {port}...")

    if not serial.connect_with_handshake():
        return None

    print("Zeroing gantry at a1...")
    serial.send_and_wait_ack("ZERO", timeout=5.0)

    print(f"Setting microstepping 1/{microstep}, speed {speed} us...")
    serial.send_and_wait_ack(f"M {microstep}", timeout=3.0)
    serial.send_and_wait_ack(f"SPEED {speed}", timeout=3.0)

    return serial


def print_banner():
    print(f"""
{C_BOLD}======================================================{C_RESET}
  {C_CYAN}Automated Chessboard System{C_RESET}
{C_BOLD}======================================================{C_RESET}
""")


def print_main_menu():
    print(f"""
{C_BOLD}Select Mode:{C_RESET}
  {C_GREEN}1{C_RESET}  Full Game         — Play against Stockfish
  {C_GREEN}2{C_RESET}  Board Diagnostic  — Test reed switches
  {C_GREEN}3{C_RESET}  Gantry Diagnostic — Test motors and magnet
  {C_GREEN}q{C_RESET}  Quit
""")


def run_full_game(serial: SerialConnection, depth: int, microstep: int, speed: int):
    from chess_orchestrator import ChessOrchestrator
    orch = ChessOrchestrator(depth=depth, microstep=microstep, speed=speed)
    orch.serial = serial
    orch.tracker.reset()

    print("Requesting board scan...")
    serial.write("SCAN\r\n")
    try:
        orch.run_hardware_listener()
    finally:
        orch.engine.close()


def run_board_diagnostic(serial: SerialConnection):
    from board_diagnostic import get_mux_and_channel, render_diagnostic_grid
    from serial_connection import parse_board_frame, C_DIM

    print(f"\n{C_BOLD}=== Board Diagnostic Mode ==={C_RESET}")
    print(f"Press INT0 button or type {C_CYAN}'scan'{C_RESET} to read sensors.")
    print(f"Type {C_CYAN}'back'{C_RESET} to return to main menu.\n")

    import select, time
    scan_count = 0

    try:
        while True:
            rlist, _, _ = select.select([sys.stdin, serial.fd], [], [], 0.1)

            if serial.fd in rlist:
                line = serial.read_line(timeout=0.1)
                if line and line.startswith("BOARD:"):
                    scan_count += 1
                    active = parse_board_frame(line)
                    if active is not None:
                        active_set = set(active)
                        print(f"\n{C_BOLD}=== Scan #{scan_count} ==={C_RESET}")
                        print(render_diagnostic_grid(active_set))
                        count = len(active_set)
                        if count == 0:
                            print(f"\n{C_YELLOW}No magnets detected.{C_RESET}")
                        else:
                            print(f"\n{C_GREEN}{C_BOLD}{count} sensor{'s' if count != 1 else ''} active:{C_RESET}")
                            for sq in sorted(active_set):
                                info = get_mux_and_channel(sq)
                                print(f"  {C_GREEN}{sq.upper()}{C_RESET}: {C_CYAN}{info}{C_RESET}")

            if sys.stdin in rlist:
                user_line = sys.stdin.readline().strip().lower()
                if user_line in ("back", "menu", "exit", "quit"):
                    return
                elif user_line == "scan":
                    serial.write("SCAN\r\n")

    except KeyboardInterrupt:
        pass


def run_gantry_diagnostic(serial: SerialConnection, microstep: int, speed: int):
    print(f"\n{C_BOLD}=== Gantry Diagnostic Mode ==={C_RESET}")
    print(f"Type {C_CYAN}'help'{C_RESET} for commands, {C_CYAN}'back'{C_RESET} to return to main menu.\n")

    import re, select, time
    from serial_connection import parse_board_frame, C_MAGENTA, C_DIM

    gantry_pos = "a1"
    magnet_on = False
    current_squares: set[str] = set()
    last_detected_move: tuple[str, str] | None = None

    def send_and_wait(command: str, timeout: float = 25.0) -> bool:
        nonlocal gantry_pos, magnet_on
        serial.flush_input()
        serial.write(command.strip() + "\r\n")
        start = time.time()
        while time.time() - start < timeout:
            line = serial.read_line(timeout=0.5)
            if line:
                if "STATUS SQ:" in line:
                    m = re.search(r"STATUS SQ:([a-h][1-8])", line, re.IGNORECASE)
                    if m:
                        gantry_pos = m.group(1).lower()
                    if "MAG:ON" in line:
                        magnet_on = True
                    elif "MAG:OFF" in line:
                        magnet_on = False
                if "OK" in line:
                    return True
                elif "ERR" in line:
                    print(f"    {C_RED}[MCU Error]{C_RESET} {line}")
                    return False
        return False

    def render_board(active: set[str]) -> str:
        lines = []
        mag_badge = f"{C_RED}[MAGNET ON]{C_RESET}" if magnet_on else f"{C_DIM}[MAGNET OFF]{C_RESET}"
        lines.append(f"\n{C_BOLD}   --- Reed Matrix & Gantry State {mag_badge} ---{C_RESET}")
        lines.append(f"{C_BOLD}     a   b   c   d   e   f   g   h{C_RESET}")
        lines.append("   +---+---+---+---+---+---+---+---+")
        for rank in range(8, 0, -1):
            row = f" {rank} |"
            for fi in range(8):
                sq = f"{chr(ord('a') + fi)}{rank}"
                is_gantry = (sq == gantry_pos)
                is_piece = (sq in active)
                if is_gantry and is_piece:
                    cell = f"{C_MAGENTA}{C_BOLD}[*]{C_RESET}"
                elif is_gantry:
                    cell = f"{C_CYAN}{C_BOLD}[@]{C_RESET}"
                elif is_piece:
                    cell = f"{C_GREEN}{C_BOLD}[X]{C_RESET}"
                else:
                    cell = f"{C_DIM} . {C_RESET}"
                row += f"{cell}|"
            row += f" {rank}"
            lines.append(row)
            lines.append("   +---+---+---+---+---+---+---+---+")
        lines.append(f"{C_BOLD}     a   b   c   d   e   f   g   h{C_RESET}")
        lines.append(f" Active: {C_GREEN}{len(active)}{C_RESET} | Gantry: {C_CYAN}{gantry_pos.upper()}{C_RESET}")
        return "\n".join(lines)

    def move_piece(from_sq: str, to_sq: str):
        nonlocal gantry_pos, magnet_on
        print(f"\n{C_BOLD}Move: {from_sq.upper()} -> {to_sq.upper()}{C_RESET}")
        print(f"  [1/2] Transferring piece... ", end="", flush=True)
        if not send_and_wait(f"MOVE {from_sq}{to_sq}", timeout=60.0):
            print(f"{C_RED}FAILED{C_RESET}")
            return
        print(f"{C_GREEN}OK{C_RESET}")
        print(f"  [2/2] Homing to a1... ", end="", flush=True)
        if not send_and_wait("SQ a1", timeout=40.0):
            print(f"{C_RED}FAILED{C_RESET}")
            return
        print(f"{C_GREEN}OK{C_RESET}")
        print(f"{C_GREEN}✓ Done. Gantry at a1.{C_RESET}\n")

    def print_gantry_help():
        print(f"""
{C_BOLD}=== Gantry Diagnostic Commands ==={C_RESET}
  {C_CYAN}<move>{C_RESET}              : Move piece with magnet (e.g. {C_GREEN}e2e4{C_RESET})
  {C_CYAN}scan{C_RESET}                : Trigger board scan
  {C_CYAN}board{C_RESET}               : Show board with gantry position
  {C_CYAN}sq <square>{C_RESET}         : Move gantry without magnet (e.g. {C_GREEN}sq e4{C_RESET})
  {C_CYAN}mag <on|off>{C_RESET}        : Toggle electromagnet
  {C_CYAN}zero{C_RESET}                : Zero position as a1
  {C_CYAN}m <1|2|4|8|16|32>{C_RESET}   : Set microstepping
  {C_CYAN}speed <us>{C_RESET}          : Set step delay
  {C_CYAN}help{C_RESET}                : Show this menu
  {C_CYAN}back{C_RESET}                : Return to main menu
""")

    try:
        while True:
            rlist, _, _ = select.select([sys.stdin, serial.fd], [], [], 0.1)

            if serial.fd in rlist:
                line = serial.read_line(timeout=0.1)
                if line:
                    if line.startswith("BOARD:"):
                        parsed = parse_board_frame(line)
                        if parsed is not None:
                            prev = set(current_squares)
                            current_squares.clear()
                            current_squares.update(parsed)
                            print(render_board(current_squares))
                            removed = prev - current_squares
                            added = current_squares - prev
                            if len(removed) == 1 and len(added) == 1:
                                s = next(iter(removed))
                                d = next(iter(added))
                                last_detected_move = (s, d)
                                print(f"\n{C_GREEN}{C_BOLD}Human Move: {s.upper()} -> {d.upper()}{C_RESET}")
                                print(f"{C_YELLOW}Type '{s}{d}' or 'exec' to mirror with gantry.{C_RESET}\n")
                    elif "STATUS SQ:" in line:
                        m = re.search(r"STATUS SQ:([a-h][1-8])", line, re.IGNORECASE)
                        if m:
                            gantry_pos = m.group(1).lower()

            if sys.stdin in rlist:
                user_line = sys.stdin.readline().strip()
                if not user_line:
                    continue
                parts = user_line.split()
                verb = parts[0].lower()

                if verb in ("back", "menu"):
                    return
                elif verb in ("exit", "quit"):
                    sys.exit(0)
                elif verb == "help":
                    print_gantry_help()
                elif verb == "board":
                    print(render_board(current_squares))
                elif verb == "scan":
                    serial.write("SCAN\r\n")
                elif verb == "zero":
                    send_and_wait("ZERO", timeout=4.0)
                    gantry_pos = "a1"
                    print(f"{C_GREEN}✓ Zeroed at a1{C_RESET}")
                elif verb == "mag":
                    if len(parts) >= 2 and parts[1].lower() in ("on", "1"):
                        send_and_wait("MAG 1", timeout=4.0)
                        print(f"{C_GREEN}Magnet ON{C_RESET}")
                    elif len(parts) >= 2 and parts[1].lower() in ("off", "0"):
                        send_and_wait("MAG 0", timeout=4.0)
                        print(f"{C_GREEN}Magnet OFF{C_RESET}")
                    else:
                        print("Usage: mag <on|off>")
                elif verb == "sq":
                    if len(parts) >= 2:
                        target = parts[1].lower()
                        if send_and_wait(f"SQ {target}", timeout=35.0):
                            print(f"{C_GREEN}✓ Gantry at {target.upper()}{C_RESET}")
                    else:
                        print("Usage: sq <square>")
                elif verb in ("m", "microstep"):
                    if len(parts) >= 2:
                        mode = parts[1]
                        if send_and_wait(f"M {mode}", timeout=4.0):
                            print(f"{C_GREEN}✓ Microstepping 1/{mode}{C_RESET}")
                    else:
                        print("Usage: m <1|2|4|8|16|32>")
                elif verb in ("speed", "delay"):
                    if len(parts) >= 2:
                        spd = parts[1]
                        if send_and_wait(f"SPEED {spd}", timeout=4.0):
                            print(f"{C_GREEN}✓ Speed {spd} us{C_RESET}")
                    else:
                        print("Usage: speed <us>")
                elif verb == "exec":
                    if last_detected_move:
                        move_piece(last_detected_move[0], last_detected_move[1])
                    else:
                        print(f"{C_YELLOW}No detected move. Trigger a scan first.{C_RESET}")
                elif (len(user_line) == 4 and user_line[0].isalpha() and user_line[1].isdigit()
                      and user_line[2].isalpha() and user_line[3].isdigit()):
                    move_piece(user_line[0:2].lower(), user_line[2:4].lower())
                else:
                    serial.write(user_line + "\r\n")
                    resp = serial.read_line(timeout=1.0)
                    if resp:
                        print(f"  [MCU] {resp}")

    except KeyboardInterrupt:
        pass


def main():
    parser = argparse.ArgumentParser(description="Automated Chessboard CLI")
    parser.add_argument("--port", type=str, default=None)
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--depth", type=int, default=14)
    parser.add_argument("--microstep", type=int, default=8, choices=[1, 2, 4, 8, 16, 32])
    parser.add_argument("--speed", type=int, default=200)
    args = parser.parse_args()

    print_banner()

    serial = connect_hardware(args.port, args.baud, args.microstep, args.speed)
    if serial is None:
        print(f"\n{C_YELLOW}Tip: For simulation without hardware:{C_RESET}")
        print("    python3 chess_orchestrator.py --simulate")
        sys.exit(1)

    try:
        while True:
            print_main_menu()
            choice = input(f"{C_BOLD}> {C_RESET}").strip().lower()

            if choice in ("q", "quit", "exit"):
                break
            elif choice == "1":
                run_full_game(serial, args.depth, args.microstep, args.speed)
            elif choice == "2":
                run_board_diagnostic(serial)
            elif choice == "3":
                run_gantry_diagnostic(serial, args.microstep, args.speed)
            else:
                print(f"{C_RED}Invalid choice.{C_RESET}")

    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        serial.close()
        print("Done.")


if __name__ == "__main__":
    main()
