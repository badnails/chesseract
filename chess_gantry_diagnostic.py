#!/usr/bin/env python3
"""
Interactive Diagnostic & Gantry Controller for ATmega32 Chessboard.

Combines:
1. Reed-switch matrix sensing & live board visualization.
2. Human move detection (sensing piece lifted and placed).
3. Physical gantry movement with electromagnet transfer.
4. Automatic return-to-a1 homing after every move.
5. Dynamic microstepping (M 8) and speed adjustment (SPEED 200).
"""

from __future__ import annotations

import argparse
import re
import select
import sys
import time
from typing import Optional, Set, Tuple

from serial_connection import (
    SerialConnection, auto_detect_bluetooth_port, parse_board_frame,
    C_RESET, C_BOLD, C_GREEN, C_YELLOW, C_CYAN, C_RED, C_MAGENTA, C_DIM,
)


class ChessGantryDiagnostic:
    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        microstep: int = 8,
        speed: int = 200,
        poll_interval: Optional[float] = None
    ):
        self.port = port
        self.baudrate = baudrate
        self.microstep = microstep
        self.speed = speed
        self.poll_interval = poll_interval

        self.serial = SerialConnection(port, baudrate)
        self.current_squares: Set[str] = set()
        self.previous_squares: Set[str] = set()
        self.last_detected_move: Optional[Tuple[str, str]] = None
        self.gantry_pos: str = "a1"
        self.magnet_on: bool = False

    def connect(self) -> bool:
        print(f"{C_BOLD}Connecting to Bluetooth HC-05 at {self.port}...{C_RESET}")

        if not self.serial.connect_with_handshake():
            return False

        time.sleep(0.2)
        print("Zeroing gantry head to square a1...")
        self.send_and_wait_ack("ZERO", timeout=4.0)

        print(f"Setting microstepping to 1/{self.microstep}...")
        self.send_and_wait_ack(f"M {self.microstep}", timeout=3.0)

        print(f"Setting motor step delay to {self.speed} us...")
        self.send_and_wait_ack(f"SPEED {self.speed}", timeout=3.0)

        print("Requesting initial board scan...")
        self.serial.write("SCAN\r\n")
        return True

    def send_and_wait_ack(self, command: str, timeout: float = 25.0) -> bool:
        self.serial.flush_input()
        cmd_clean = command.strip() + "\r\n"
        self.serial.write(cmd_clean)

        start = time.time()
        while time.time() - start < timeout:
            line = self.serial.read_line(timeout=0.5)
            if line:
                if "STATUS SQ:" in line:
                    m = re.search(r"STATUS SQ:([a-h][1-8])", line, re.IGNORECASE)
                    if m:
                        self.gantry_pos = m.group(1).lower()
                    if "MAG:ON" in line:
                        self.magnet_on = True
                    elif "MAG:OFF" in line:
                        self.magnet_on = False
                if "OK" in line:
                    return True
                elif "ERR" in line:
                    print(f"    {C_RED}[MCU Error]{C_RESET} {line}")
                    return False
        return False

    def move_piece(self, from_sq: str, to_sq: str) -> bool:
        from_sq = from_sq.lower().strip()
        to_sq = to_sq.lower().strip()

        if not (len(from_sq) == 2 and from_sq[0] in "abcdefgh" and from_sq[1] in "12345678"):
            print(f"{C_RED}Invalid source square: {from_sq}{C_RESET}")
            return False
        if not (len(to_sq) == 2 and to_sq[0] in "abcdefgh" and to_sq[1] in "12345678"):
            print(f"{C_RED}Invalid destination square: {to_sq}{C_RESET}")
            return False
        print(f"\n{C_BOLD}Executing Move: {from_sq.upper()} -> {to_sq.upper()}{C_RESET}")

        print(f"  [1/2] Transferring piece ({from_sq.upper()} -> {to_sq.upper()}) with electromagnet... ", end="", flush=True)
        if not self.send_and_wait_ack(f"MOVE {from_sq}{to_sq}", timeout=60.0):
            print(f"{C_RED}FAILED{C_RESET}")
            return False
        self.gantry_pos = to_sq
        self.magnet_on = False
        print(f"{C_GREEN}OK{C_RESET}")

        print(f"  [2/2] Returning gantry home to a1 (SQ a1)... ", end="", flush=True)
        if not self.send_and_wait_ack("SQ a1", timeout=40.0):
            print(f"{C_RED}FAILED{C_RESET}")
            return False
        self.gantry_pos = "a1"
        print(f"{C_GREEN}OK{C_RESET}")

        print(f"{C_GREEN}✓ Physical move {from_sq.upper()} -> {to_sq.upper()} complete! Gantry at a1.{C_RESET}\n")
        return True

    def render_board(self, active: Set[str]) -> str:
        lines = []
        mag_badge = f"{C_RED}[MAGNET ON]{C_RESET}" if self.magnet_on else f"{C_DIM}[MAGNET OFF]{C_RESET}"
        lines.append(f"\n{C_BOLD}   --- Reed Matrix & Gantry State {mag_badge} ---{C_RESET}")
        lines.append(f"{C_BOLD}     a   b   c   d   e   f   g   h{C_RESET}")
        lines.append("   +---+---+---+---+---+---+---+---+")

        for rank in range(8, 0, -1):
            row = f" {rank} |"
            for file_idx in range(8):
                sq = f"{chr(ord('a') + file_idx)}{rank}"
                is_gantry = (sq == self.gantry_pos)
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
        lines.append(f" Active Pieces: {C_GREEN}{len(active)}{C_RESET} | Gantry Head: {C_CYAN}{self.gantry_pos.upper()}{C_RESET}")
        return "\n".join(lines)

    def handle_board_frame(self, frame_line: str) -> None:
        parsed = parse_board_frame(frame_line)
        if parsed is None:
            return
        active = set(parsed)

        self.previous_squares = self.current_squares
        self.current_squares = active

        print(self.render_board(active))

        if self.previous_squares:
            removed = self.previous_squares - self.current_squares
            added = self.current_squares - self.previous_squares

            if len(removed) == 1 and len(added) == 1:
                src = list(removed)[0]
                dst = list(added)[0]
                self.last_detected_move = (src, dst)
                print(f"\n{C_GREEN}{C_BOLD}✓ Human Move Detected: {src.upper()} -> {dst.upper()} ({src}{dst}){C_RESET}")
                print(f"{C_YELLOW}Tip: Type 'exec' or '{src}{dst}' to physically mirror this move with the gantry!{C_RESET}\n")
            elif removed or added:
                print(f"{C_YELLOW}Board delta: removed={sorted(list(removed))}, added={sorted(list(added))}{C_RESET}\n")

    def print_help(self) -> None:
        print(f"""
{C_BOLD}=== Interactive Chessboard Diagnostic Commands ==={C_RESET}
  {C_CYAN}<move>{C_RESET}              : Execute physical move with gantry (e.g. {C_GREEN}a1b2{C_RESET}, {C_GREEN}e2e4{C_RESET}, {C_GREEN}d7d5{C_RESET})
  {C_CYAN}exec{C_RESET}                : Mirror the last detected human move using the gantry
  {C_CYAN}scan{C_RESET}                : Request instant 64-reed switch scan from ATmega
  {C_CYAN}board{C_RESET}               : Display the current 8x8 ASCII board and gantry position
  {C_CYAN}sq <square>{C_RESET}         : Move gantry head to square without magnet (e.g. {C_GREEN}sq e4{C_RESET})
  {C_CYAN}mag <on|off>{C_RESET}        : Manually turn electromagnet ON or OFF
  {C_CYAN}zero{C_RESET}                : Zero current physical position as square a1 (0, 0)
  {C_CYAN}m <1|2|4|8|16|32>{C_RESET}   : Change microstepping (e.g. {C_GREEN}m 8{C_RESET})
  {C_CYAN}speed <us>{C_RESET}          : Change pulse delay us (e.g. {C_GREEN}speed 200{C_RESET} or {C_GREEN}speed 400{C_RESET})
  {C_CYAN}help{C_RESET}                : Show this menu
  {C_CYAN}exit / quit{C_RESET}         : Disconnect and exit
""")

    def run(self) -> None:
        if not self.connect():
            return

        print(f"\n{C_BOLD}System Ready!{C_RESET}")
        print(f"- Press the {C_YELLOW}PD2/INT0 button{C_RESET} on the board to trigger a scan, or type {C_CYAN}'scan'{C_RESET}.")
        print(f"- Type any move (e.g. {C_GREEN}'a1b2'{C_RESET} or {C_GREEN}'e2e4'{C_RESET}) to move gantry and return to a1.")
        print(f"- Type {C_CYAN}'help'{C_RESET} for all available commands.")
        print(f"- Press {C_CYAN}Ctrl+C{C_RESET} to exit.\n")

        last_poll = time.time()

        try:
            while True:
                now = time.time()
                if self.poll_interval and (now - last_poll >= self.poll_interval):
                    self.serial.write("SCAN\r\n")
                    last_poll = now

                rlist, _, _ = select.select([sys.stdin, self.serial.fd], [], [], 0.1)

                if self.serial.fd in rlist:
                    line = self.serial.read_line(timeout=0.1)
                    if line:
                        if line.startswith("BOARD:"):
                            self.handle_board_frame(line)
                        elif "STATUS SQ:" in line:
                            m = re.search(r"STATUS SQ:([a-h][1-8])", line, re.IGNORECASE)
                            if m:
                                self.gantry_pos = m.group(1).lower()
                            if "MAG:ON" in line:
                                self.magnet_on = True
                            elif "MAG:OFF" in line:
                                self.magnet_on = False

                if sys.stdin in rlist:
                    user_line = sys.stdin.readline()
                    if not user_line:
                        break
                    cmd = user_line.strip()
                    if not cmd:
                        continue

                    parts = cmd.split()
                    verb = parts[0].lower()

                    if verb in ("exit", "quit", "q"):
                        print("Exiting...")
                        break
                    elif verb == "help":
                        self.print_help()
                    elif verb == "board":
                        print(self.render_board(self.current_squares))
                    elif verb == "scan":
                        print("Requesting board scan...")
                        self.serial.write("SCAN\r\n")
                    elif verb == "zero":
                        print("Zeroing position to a1...")
                        if self.send_and_wait_ack("ZERO", timeout=4.0):
                            self.gantry_pos = "a1"
                            print(f"{C_GREEN}✓ Zeroed at a1{C_RESET}")
                    elif verb == "mag":
                        if len(parts) >= 2 and parts[1].lower() in ("on", "1"):
                            self.send_and_wait_ack("MAG 1", timeout=4.0)
                            self.magnet_on = True
                            print(f"{C_GREEN}Magnet turned ON{C_RESET}")
                        elif len(parts) >= 2 and parts[1].lower() in ("off", "0"):
                            self.send_and_wait_ack("MAG 0", timeout=4.0)
                            self.magnet_on = False
                            print(f"{C_GREEN}Magnet turned OFF{C_RESET}")
                        else:
                            print("Usage: mag <on|off>")
                    elif verb == "sq":
                        if len(parts) >= 2:
                            target = parts[1].lower()
                            print(f"Moving empty gantry to {target.upper()}...")
                            if self.send_and_wait_ack(f"SQ {target}", timeout=35.0):
                                self.gantry_pos = target
                                print(f"{C_GREEN}✓ Gantry at {target.upper()}{C_RESET}")
                        else:
                            print("Usage: sq <square> (e.g. sq e4)")
                    elif verb in ("m", "microstep"):
                        if len(parts) >= 2:
                            mode = parts[1]
                            print(f"Setting microstepping to 1/{mode}...")
                            if self.send_and_wait_ack(f"M {mode}", timeout=4.0):
                                self.microstep = int(mode)
                                print(f"{C_GREEN}✓ Microstepping set to 1/{mode}{C_RESET}")
                        else:
                            print(f"Current microstepping: 1/{self.microstep}. Usage: m <1|2|4|8|16|32>")
                    elif verb in ("speed", "delay"):
                        if len(parts) >= 2:
                            spd = parts[1]
                            print(f"Setting motor step delay to {spd} us...")
                            if self.send_and_wait_ack(f"SPEED {spd}", timeout=4.0):
                                self.speed = int(spd)
                                print(f"{C_GREEN}✓ Speed updated to {spd} us{C_RESET}")
                        else:
                            print(f"Current speed: {self.speed} us. Usage: speed <us>")
                    elif verb == "exec":
                        if self.last_detected_move:
                            src, dst = self.last_detected_move
                            self.move_piece(src, dst)
                        else:
                            print(f"{C_YELLOW}No detected move to execute. Trigger a scan first or specify a move (e.g. a1b2).{C_RESET}")
                    elif len(cmd) == 4 and cmd[0].isalpha() and cmd[1].isdigit() and cmd[2].isalpha() and cmd[3].isdigit():
                        src = cmd[0:2].lower()
                        dst = cmd[2:4].lower()
                        self.move_piece(src, dst)
                    elif verb == "move" and len(parts) >= 2:
                        arg = "".join(parts[1:])
                        if len(arg) == 4:
                            self.move_piece(arg[0:2], arg[2:4])
                        else:
                            print("Usage: move <from><to> (e.g. move a1 b2 or move a1b2)")
                    else:
                        self.serial.write(cmd + "\r\n")
                        line = self.serial.read_line(timeout=1.0)
                        if line:
                            print(f"  [MCU] {line}")

        except KeyboardInterrupt:
            print("\nShutting down diagnostic tool...")
        finally:
            self.serial.close()
            print("Port closed. Done.")


def main():
    parser = argparse.ArgumentParser(
        description="Interactive Diagnostic & Gantry Controller for ATmega32 Chessboard."
    )
    parser.add_argument("-p", "--port", type=str, default=None, help="Bluetooth port (default: auto-detect /dev/cu.HC-05)")
    parser.add_argument("-b", "--baud", type=int, default=9600, help="Baud rate (default: 9600)")
    parser.add_argument("-m", "--microstep", type=int, default=8, choices=[1, 2, 4, 8, 16, 32], help="Microstep mode (default: 8 for 1/8)")
    parser.add_argument("-s", "--speed", type=int, default=200, help="Step pulse delay in us (default: 200 us)")
    parser.add_argument("--poll", type=float, default=None, help="Auto-poll board every N seconds (default: button trigger only)")

    args = parser.parse_args()

    port = args.port
    if not port:
        port = auto_detect_bluetooth_port()
        if not port:
            print(f"{C_RED}No Bluetooth HC-05 port found.{C_RESET}")
            print("Ensure HC-05 is paired in System Settings -> Bluetooth.")
            sys.exit(1)

    if port.startswith("/dev/tty."):
        port = port.replace("/dev/tty.", "/dev/cu.")

    diag = ChessGantryDiagnostic(
        port=port,
        baudrate=args.baud,
        microstep=args.microstep,
        speed=args.speed,
        poll_interval=args.poll
    )
    diag.run()


if __name__ == "__main__":
    main()
