#!/usr/bin/env python3
"""
Interactive Laptop Control CLI for ATmega32 DRV8825 Dual Stepper Gantry.
Works natively on macOS/Linux with zero external dependencies (pure Python standard library).
"""

from __future__ import annotations

import argparse
import fcntl
import glob
import os
import select
import sys
import termios
import time
import tty
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

        # Open non-blocking read/write
        self.fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            if hasattr(termios, "TIOCEXCL"):
                fcntl.ioctl(self.fd, termios.TIOCEXCL)

            settings = termios.tcgetattr(self.fd)
            # 8N1, ignore framing/parity errors
            settings[0] = termios.IGNPAR
            settings[1] = 0
            settings[2] &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB)
            settings[2] |= termios.CS8 | termios.CLOCAL | termios.CREAD
            if hasattr(termios, "CRTSCTS"):
                settings[2] &= ~termios.CRTSCTS
            settings[3] = 0  # Raw mode
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


class GantryClient:
    def __init__(self, port: str, baudrate: int = 9600):
        self.port = port
        self.baudrate = baudrate
        self.conn = SerialConnection(port, baudrate)
        self.step_size = 5.0  # mm default jog distance
        self.microstep = 16
        self.current_x = 0.0
        self.current_y = 0.0
        self.x_min = 0.0
        self.x_max = 450.0  # 45 cm rod length
        self.y_min = 0.0
        self.y_max = 420.0  # 42 cm rod length

    def connect(self):
        print(f"Connecting to {self.port} at {self.baudrate} baud...")
        try:
            self.conn.open()
            time.sleep(1.5)  # Allow MCU startup settling
            # Flush initial greeting banner
            initial = self.conn.read_until(timeout=0.5).decode("ascii", errors="replace")
            if initial.strip():
                print(f"[MCU Initial Output]\n{initial.strip()}")

            resp = self.send_command("PING", timeout=1.5)
            if "PONG" in resp or "ATmega32" in resp:
                print(">> Connected successfully to ATmega32 Gantry Controller!")
            else:
                print(f">> Connected. (Device response: {resp.strip() if resp else 'No response'})")
        except Exception as e:
            print(f"Error opening serial port: {e}")
            sys.exit(1)

    def close(self):
        self.conn.close()

    def _parse_position(self, text: str):
        # Look for STATUS X:12.34 Y:56.78
        import re
        match = re.search(r"STATUS X:(-?\d+\.\d+) Y:(-?\d+\.\d+)", text)
        if match:
            try:
                self.current_x = float(match.group(1))
                self.current_y = float(match.group(2))
            except ValueError:
                pass

    def send_command(self, cmd: str, timeout: float = 6.0) -> str:
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
                        self._parse_position(clean)
                        if clean.startswith("OK") or clean.startswith("ERR") or "COMPLETE" in clean:
                            return "\n".join(lines)
            else:
                if lines:
                    break
        return "\n".join(lines)

    def set_microstep(self, mode: int):
        if mode not in (1, 2, 4, 8, 16, 32):
            print("Invalid microstep mode! Must be 1, 2, 4, 8, 16, or 32.")
            return
        resp = self.send_command(f"M {mode}")
        print(resp)
        if "OK" in resp:
            self.microstep = mode

    def jog(self, axis: str, dist: float):
        ax = axis.upper()
        if ax == "X":
            target = self.current_x + dist
            if target > self.x_max or target < self.x_min:
                print(f"[LIMIT BLOCKED] X target {target:.1f}mm is outside 0.0 - {self.x_max:.0f}mm rod bounds!")
                return
        elif ax == "Y":
            target = self.current_y + dist
            if target > self.y_max or target < self.y_min:
                print(f"[LIMIT BLOCKED] Y target {target:.1f}mm is outside 0.0 - {self.y_max:.0f}mm rod bounds!")
                return
        resp = self.send_command(f"JOG {ax} {dist:.2f}", timeout=8.0)
        print(resp)

    def move_rel(self, dx: float, dy: float):
        target_x = self.current_x + dx
        target_y = self.current_y + dy
        if target_x > self.x_max or target_x < self.x_min:
            print(f"[LIMIT BLOCKED] X target {target_x:.1f}mm is outside 0.0 - {self.x_max:.0f}mm rod bounds!")
            return
        if target_y > self.y_max or target_y < self.y_min:
            print(f"[LIMIT BLOCKED] Y target {target_y:.1f}mm is outside 0.0 - {self.y_max:.0f}mm rod bounds!")
            return
        resp = self.send_command(f"R X{dx:.2f} Y{dy:.2f}", timeout=12.0)
        print(resp)

    def move_abs(self, x: float, y: float):
        if x > self.x_max or x < self.x_min:
            print(f"[LIMIT BLOCKED] X target {x:.1f}mm is outside 0.0 - {self.x_max:.0f}mm rod bounds!")
            return
        if y > self.y_max or y < self.y_min:
            print(f"[LIMIT BLOCKED] Y target {y:.1f}mm is outside 0.0 - {self.y_max:.0f}mm rod bounds!")
            return
        resp = self.send_command(f"G0 X{x:.2f} Y{y:.2f}", timeout=15.0)
        print(resp)

    def interactive_jog(self):
        print("\n" + "=" * 55)
        print("          INTERACTIVE KEYBOARD JOG MODE")
        print("=" * 55)
        print(" Controls:")
        print("   W / Up Arrow    : +Y")
        print("   S / Down Arrow  : -Y")
        print("   A / Left Arrow  : -X")
        print("   D / Right Arrow : +X")
        print(" Step Size:")
        print("   [1]: 0.1 mm   [2]: 1.0 mm   [3]: 5.0 mm   [4]: 10.0 mm   [5]: 50.0 mm")
        print(" Functions:")
        print("   [Z]: Zero (0, 0)   [E]: Enable Motors   [P]: Print Status")
        print("   [Q] or [ESC]       : Exit Jog Mode")
        print("=" * 55)
        print(f"Current Jog Step: {self.step_size} mm\n")

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            while True:
                rlist, _, _ = select.select([sys.stdin], [], [], 0.1)
                if not rlist:
                    continue

                ch = sys.stdin.read(1)

                # Arrow key handling (ESC sequence: \x1b[A, \x1b[B, \x1b[C, \x1b[D)
                if ch == "\x1b":
                    seq = sys.stdin.read(2)
                    if seq == "[A":
                        ch = "w"
                    elif seq == "[B":
                        ch = "s"
                    elif seq == "[C":
                        ch = "d"
                    elif seq == "[D":
                        ch = "a"
                    else:
                        break

                if ch.lower() == "q" or ch == "\x03":  # Q or Ctrl+C
                    break
                elif ch.lower() == "w":
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                    print(f"\n[JOG +Y {self.step_size}mm]")
                    self.jog("Y", self.step_size)
                    tty.setraw(sys.stdin.fileno())
                elif ch.lower() == "s":
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                    print(f"\n[JOG -Y {self.step_size}mm]")
                    self.jog("Y", -self.step_size)
                    tty.setraw(sys.stdin.fileno())
                elif ch.lower() == "d":
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                    print(f"\n[JOG +X {self.step_size}mm]")
                    self.jog("X", self.step_size)
                    tty.setraw(sys.stdin.fileno())
                elif ch.lower() == "a":
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                    print(f"\n[JOG -X {self.step_size}mm]")
                    self.jog("X", -self.step_size)
                    tty.setraw(sys.stdin.fileno())
                elif ch == "1":
                    self.step_size = 0.1
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                    print(f"\n[Step set to 0.1 mm]")
                    tty.setraw(sys.stdin.fileno())
                elif ch == "2":
                    self.step_size = 1.0
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                    print(f"\n[Step set to 1.0 mm]")
                    tty.setraw(sys.stdin.fileno())
                elif ch == "3":
                    self.step_size = 5.0
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                    print(f"\n[Step set to 5.0 mm]")
                    tty.setraw(sys.stdin.fileno())
                elif ch == "4":
                    self.step_size = 10.0
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                    print(f"\n[Step set to 10.0 mm]")
                    tty.setraw(sys.stdin.fileno())
                elif ch == "5":
                    self.step_size = 50.0
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                    print(f"\n[Step set to 50.0 mm]")
                    tty.setraw(sys.stdin.fileno())
                elif ch.lower() == "z":
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                    print(f"\n[Zeroing coordinates]")
                    print(self.send_command("ZERO"))
                    tty.setraw(sys.stdin.fileno())
                elif ch.lower() == "p":
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                    print(f"\n[Status]")
                    print(self.send_command("POS"))
                    tty.setraw(sys.stdin.fileno())
                elif ch.lower() == "e":
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                    print(f"\n[Enable Motors]")
                    print(self.send_command("EN 1"))
                    tty.setraw(sys.stdin.fileno())

        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            print("\nExited interactive jog mode.\n")


def list_ports() -> list[str]:
    # Look for common serial and Bluetooth device paths on macOS and Linux
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
        # Fallback to broader list, keeping HC-05/Bluetooth if present
        ports = [p for p in glob.glob("/dev/cu.*") if "incoming-port" not in p.lower()]

    return sorted(list(set(ports)))


def pick_serial_port() -> str:
    ports = list_ports()
    if not ports:
        print("No serial or Bluetooth devices detected.")
        print("Please check that your adapter or HC-05 is paired and connected.")
        manual = input("Enter port manually (or press Enter to exit): ").strip()
        if manual:
            return manual
        sys.exit(1)

    if len(ports) == 1:
        print(f"Auto-detected serial device: {ports[0]}")
        return ports[0]

    print("\nAvailable serial ports:")
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


def print_cli_help():
    print("""
=== Available Commands ===
  jog                      : Enter interactive keyboard jog mode (WASD / Arrows)
  m <1|2|4|8|16|32>        : Set DRV8825 microstep mode (e.g. m 16)
  goto <X> <Y>             : Move to absolute coordinate in mm (e.g. goto 50 25)
  move <dX> <dY>           : Move relative distance in mm (e.g. move 10 -5)
  jog <X|Y> <dist>         : Jog single axis in mm (e.g. jog x 20)
  speed <us>               : Set step pulse delay in us (e.g. speed 800)
  zero                     : Set current position as origin (0, 0)
  pos / status             : Print current position, microstepping, and step delay
  enable / disable         : Enable (holding torque) or disable (release) motors
  test square [size]       : Run square test pattern (default 30 mm)
  test diag [size]         : Run diagonal test pattern (default 40 mm)
  raw <command>            : Send raw string directly to MCU
  help                     : Show this help menu
  exit / quit              : Exit controller
""")


def main():
    parser = argparse.ArgumentParser(description="ATmega32 Dual DRV8825 Gantry Controller CLI")
    parser.add_argument("-p", "--port", type=str, default=None, help="Serial port (e.g. /dev/cu.usbserial-*)")
    parser.add_argument("-b", "--baud", type=int, default=9600, help="Baud rate (default: 9600)")
    parser.add_argument("--jog", action="store_true", help="Launch directly into interactive jog mode")
    args = parser.parse_args()

    port = args.port if args.port else pick_serial_port()
    client = GantryClient(port, args.baud)
    client.connect()

    if args.jog:
        client.interactive_jog()

    print("\nATmega32 Gantry Controller ready. Type 'help' for commands or 'jog' for keyboard mode.\n")

    try:
        while True:
            cmd = input("gantry> ").strip()
            if not cmd:
                continue

            parts = cmd.split()
            verb = parts[0].lower()

            if verb in ("exit", "quit", "q"):
                print("Closing connection...")
                break
            elif verb == "help":
                print_cli_help()
            elif verb == "jog":
                if len(parts) == 1:
                    client.interactive_jog()
                elif len(parts) == 3:
                    axis = parts[1].upper()
                    try:
                        dist = float(parts[2])
                        client.jog(axis, dist)
                    except ValueError:
                        print("Invalid distance!")
                else:
                    print("Usage: jog (for keyboard mode) OR jog <X|Y> <distance_mm>")
            elif verb in ("m", "microstep"):
                if len(parts) < 2:
                    print("Usage: m <1|2|4|8|16|32>")
                else:
                    try:
                        client.set_microstep(int(parts[1]))
                    except ValueError:
                        print("Invalid number!")
            elif verb == "goto":
                if len(parts) < 3:
                    print("Usage: goto <X_mm> <Y_mm>")
                else:
                    try:
                        x = float(parts[1])
                        y = float(parts[2])
                        client.move_abs(x, y)
                    except ValueError:
                        print("Coordinates must be numbers!")
            elif verb == "move":
                if len(parts) < 3:
                    print("Usage: move <dX_mm> <dY_mm>")
                else:
                    try:
                        dx = float(parts[1])
                        dy = float(parts[2])
                        client.move_rel(dx, dy)
                    except ValueError:
                        print("Offsets must be numbers!")
            elif verb in ("pos", "status"):
                print(client.send_command("POS"))
            elif verb == "zero":
                print(client.send_command("ZERO"))
            elif verb == "enable":
                print(client.send_command("EN 1"))
            elif verb == "disable":
                print(client.send_command("EN 0"))
            elif verb == "speed":
                if len(parts) < 2:
                    print("Usage: speed <delay_us>")
                else:
                    print(client.send_command(f"SPEED {parts[1]}"))
            elif verb == "test":
                if len(parts) < 2:
                    print("Usage: test <square|diag> [size_mm]")
                else:
                    test_type = parts[1].upper()
                    size = parts[2] if len(parts) > 2 else ""
                    print(client.send_command(f"TEST {test_type} {size}".strip(), timeout=15.0))
            elif verb == "raw":
                raw_cmd = " ".join(parts[1:])
                print(client.send_command(raw_cmd))
            else:
                print(client.send_command(cmd))

    except (KeyboardInterrupt, EOFError):
        print("\nExiting...")
    finally:
        client.close()


if __name__ == "__main__":
    main()

