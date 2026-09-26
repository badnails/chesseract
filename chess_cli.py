#!/usr/bin/env python3
"""
Interactive Chessboard Gantry Controller CLI.
Communicates over Bluetooth /dev/tty.HC-05 (or USB) with ATmega32.
Executes algebraic chess moves (a1b1, e2e4), controls CW020 electromagnet relay,
and renders a live ASCII chessboard showing gantry position.
"""

from __future__ import annotations

import argparse
import fcntl
import glob
import os
import re
import select
import sys
import termios
import time
from typing import Optional

BAUD_RATES = {
    1200: termios.B1200,
    2400: termios.B2400,
    4800: termios.B4800,
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}


class SerialConnection:
    """Zero-dependency POSIX serial connection for macOS / Linux."""
    def __init__(self, port: str, baudrate: int = 9600):
        self.port = port
        self.baudrate = baudrate
        self.fd: Optional[int] = None

    def open(self):
        if self.baudrate not in BAUD_RATES:
            raise ValueError(f"Unsupported baudrate: {self.baudrate}")

        self.fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            if hasattr(termios, "TIOCEXCL"):
                fcntl.ioctl(self.fd, termios.TIOCEXCL)

            settings = termios.tcgetattr(self.fd)
            settings[0] = termios.IGNPAR
            settings[1] = 0
            settings[2] &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB)
            settings[2] |= termios.CS8 | termios.CLOCAL | termios.CREAD
            if hasattr(termios, "CRTSCTS"):
                settings[2] &= ~termios.CRTSCTS
            settings[3] = 0
            settings[4] = BAUD_RATES[self.baudrate]
            settings[5] = BAUD_RATES[self.baudrate]
            settings[6][termios.VMIN] = 0
            settings[6][termios.VTIME] = 0

            termios.tcsetattr(self.fd, termios.TCSANOW, settings)
            termios.tcflush(self.fd, termios.TCIOFLUSH)
        except Exception:
            os.close(self.fd)
            self.fd = None
            raise

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def write(self, data: bytes):
        if self.fd is None:
            raise RuntimeError("Port not open")
        os.write(self.fd, data)

    def read_until(self, terminator: bytes = b"\n", timeout: float = 3.0) -> bytes:
        if self.fd is None:
            raise RuntimeError("Port not open")

        buf = bytearray()
        end_time = time.time() + timeout
        while time.time() < end_time:
            remaining = max(0.01, end_time - time.time())
            rlist, _, _ = select.select([self.fd], [], [], remaining)
            if rlist:
                try:
                    chunk = os.read(self.fd, 128)
                    if chunk:
                        buf.extend(chunk)
                        if terminator in buf:
                            break
                except BlockingIOError:
                    continue
                except OSError:
                    break
            else:
                break
        return bytes(buf)


class ChessGantryClient:
    def __init__(self, port: str, baudrate: int = 9600):
        self.port = port
        self.baudrate = baudrate
        self.conn = SerialConnection(port, baudrate)
        self.current_square = "a1"
        self.magnet_on = False

    def connect(self):
        print(f"Connecting to {self.port} at {self.baudrate} baud...")
        try:
            self.conn.open()
            time.sleep(1.5)
            initial = self.conn.read_until(timeout=0.5).decode("ascii", errors="replace")
            if initial.strip():
                print(f"[MCU Initial Output]\n{initial.strip()}")

            resp = self.send_command("PING", timeout=1.5)
            if "PONG" in resp or "Chessboard" in resp:
                print(">> Connected successfully to Chessboard Gantry Controller!")
            else:
                print(f">> Connected. (Device response: {resp.strip() if resp else 'No response'})")
        except Exception as e:
            print(f"Error opening serial port: {e}")
            sys.exit(1)

    def close(self):
        self.conn.close()

    def _parse_status(self, text: str):
        # Look for STATUS SQ:e4 | ... | MAG:ON/OFF
        match = re.search(r"STATUS SQ:([a-h][1-8])", text, re.IGNORECASE)
        if match:
            self.current_square = match.group(1).lower()
        if "MAG:ON" in text:
            self.magnet_on = True
        elif "MAG:OFF" in text:
            self.magnet_on = False

    def send_command(self, cmd: str, timeout: float = 20.0) -> str:
        full_cmd = cmd.strip() + "\r\n"
        self.conn.write(full_cmd.encode("ascii"))

        lines = []
        end_time = time.time() + timeout
        while time.time() < end_time:
            raw = self.conn.read_until(b"\n", timeout=max(0.05, end_time - time.time()))
            text = raw.decode("ascii", errors="replace").strip()
            if text:
                for line in text.splitlines():
                    clean = line.strip()
                    if clean:
                        lines.append(clean)
                        self._parse_status(clean)
                        if clean.startswith("OK") or clean.startswith("ERR") or "COMPLETE" in clean:
                            return "\n".join(lines)
            else:
                if lines:
                    break
        return "\n".join(lines)

    def print_board(self):
        files = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']
        curr_file = self.current_square[0]
        curr_rank = int(self.current_square[1])

        mag_badge = "[MAGNET ON]" if self.magnet_on else "[MAGNET OFF]"
        print(f"\n   --- Chessboard Gantry Position {mag_badge} ---")
        print("     a   b   c   d   e   f   g   h")
        print("   +---+---+---+---+---+---+---+---+")
        for rank in range(8, 0, -1):
            row_str = f" {rank} |"
            for f in files:
                if f == curr_file and rank == curr_rank:
                    marker = " M " if self.magnet_on else " X "
                else:
                    marker = "   "
                row_str += marker + "|"
            row_str += f" {rank}"
            print(row_str)
            print("   +---+---+---+---+---+---+---+---+")
        print("     a   b   c   d   e   f   g   h\n")
        print(f" Current Gantry Head: {self.current_square.upper()} (Origin is a1)\n")


def list_ports() -> list[str]:
    patterns = [
        "/dev/cu.HC-05*",
        "/dev/tty.HC-05*",
        "/dev/cu.*HC*",
        "/dev/cu.usbserial*",
        "/dev/cu.usbmodem*",
        "/dev/cu.wchusbserial*",
        "/dev/cu.SLAB*",
        "/dev/ttyUSB*",
        "/dev/ttyACM*",
    ]
    ports = []
    for pat in patterns:
        ports.extend(glob.glob(pat))

    if not ports:
        ports = [p for p in glob.glob("/dev/cu.*") if "incoming-port" not in p.lower()]

    return sorted(list(set(ports)))


def pick_serial_port() -> str:
    ports = list_ports()
    if not ports:
        print("No serial or Bluetooth devices detected.")
        manual = input("Enter port manually (e.g. /dev/tty.HC-05): ").strip()
        if manual:
            return manual
        sys.exit(1)

    if len(ports) == 1:
        print(f"Auto-detected device: {ports[0]}")
        return ports[0]

    print("\nAvailable ports:")
    for idx, p in enumerate(ports):
        print(f"  [{idx + 1}] {p}")

    while True:
        try:
            choice = input(f"Select port (1-{len(ports)}): ").strip()
            num = int(choice)
            if 1 <= num <= len(ports):
                return ports[num - 1]
        except (ValueError, KeyboardInterrupt):
            print("\nExiting.")
            sys.exit(0)


def print_help():
    print("""
=== Automated Chessboard Gantry Commands ===
  <move>               : Execute chess move (e.g. a1b1, e2e4, g1f3)
  move <from> <to>     : Alternative syntax (e.g. move a1 b1)
  sq <square>          : Move gantry head to square without magnet (e.g. sq e4)
  mag <on|off>         : Manually turn electromagnet relay ON or OFF
  zero                 : Set current physical position as square a1 (0, 0)
  board                : Display visual ASCII board with current gantry position
  pos / status         : Query current position, square, and relay state
  m <1|2|4|8|16|32>    : Set microstep mode (default: m 8)
  speed <us>           : Set cruising step delay in us (e.g. speed 400)
  enable / disable     : Manually engage/relax motor holding torque
  help                 : Show this menu
  exit / quit          : Disconnect and exit
""")


def main():
    parser = argparse.ArgumentParser(description="Automated Chessboard Gantry Controller")
    parser.add_argument("-p", "--port", type=str, default=None, help="Serial or Bluetooth port (/dev/tty.HC-05)")
    parser.add_argument("-b", "--baud", type=int, default=9600, help="Baud rate (default: 9600)")
    args = parser.parse_args()

    port = args.port if args.port else pick_serial_port()
    client = ChessGantryClient(port, args.baud)
    client.connect()

    print("\nChessboard Gantry Controller Ready.")
    print("Type a chess move (e.g. 'a1b1' or 'e2e4'), 'board' to view, or 'help' for commands.\n")

    try:
        while True:
            cmd = input(f"chess [{client.current_square.upper()}]> ").strip()
            if not cmd:
                continue

            parts = cmd.split()
            verb = parts[0].lower()

            if verb in ("exit", "quit", "q"):
                print("Exiting...")
                break
            elif verb == "help":
                print_help()
            elif verb == "board":
                client.print_board()
            elif verb == "zero":
                print(client.send_command("ZERO"))
                client.print_board()
            elif verb in ("pos", "status"):
                print(client.send_command("POS"))
            elif verb == "mag":
                if len(parts) < 2:
                    print("Usage: mag <on|off>")
                else:
                    state = 1 if parts[1].lower() == "on" else 0
                    print(client.send_command(f"MAG {state}"))
            elif verb == "sq":
                if len(parts) < 2:
                    print("Usage: sq <square> (e.g. sq e4)")
                else:
                    print(client.send_command(f"SQ {parts[1]}", timeout=25.0))
            elif verb == "move":
                if len(parts) == 2 and len(parts[1]) == 4:
                    print(f"Executing move {parts[1]}...")
                    print(client.send_command(f"MOVE {parts[1]}", timeout=40.0))
                elif len(parts) >= 3:
                    move_str = f"{parts[1]}{parts[2]}"
                    print(f"Executing move {move_str}...")
                    print(client.send_command(f"MOVE {move_str}", timeout=40.0))
                else:
                    print("Usage: move <from><to> (e.g. move a1b1 or move a1 b1)")
            elif len(cmd) == 4 and cmd[0].isalpha() and cmd[1].isdigit() and cmd[2].isalpha() and cmd[3].isdigit():
                # Direct algebraic move input like "a1b1" or "e2e4"
                print(f"Executing chess move {cmd}...")
                print(client.send_command(cmd, timeout=40.0))
            elif verb in ("m", "microstep"):
                if len(parts) < 2:
                    print("Usage: m <1|2|4|8|16|32>")
                else:
                    print(client.send_command(f"M {parts[1]}"))
            elif verb == "speed":
                if len(parts) < 2:
                    print("Usage: speed <us>")
                else:
                    print(client.send_command(f"SPEED {parts[1]}"))
            elif verb == "enable":
                print(client.send_command("EN 1"))
            elif verb == "disable":
                print(client.send_command("EN 0"))
            else:
                # Pass directly to MCU
                print(client.send_command(cmd))

    except (KeyboardInterrupt, EOFError):
        print("\nExiting...")
    finally:
        client.close()


if __name__ == "__main__":
    main()

