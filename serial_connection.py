#!/usr/bin/env python3
"""
Shared POSIX serial communication for ATmega32 Chessboard tools.
Zero external dependencies — uses only Python standard library.
"""

from __future__ import annotations

import glob
import os
import select
import shutil
import subprocess
import termios
import time
from typing import FrozenSet, List, Optional, Set

# ANSI terminal colors
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_CYAN = "\033[36m"
C_RED = "\033[31m"
C_MAGENTA = "\033[35m"
C_DIM = "\033[2m"

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


def _run_blueutil(args: List[str], timeout: float = 5.0) -> Optional[str]:
    """Runs a blueutil command with timeout. Returns stdout or None on failure."""
    blueutil = shutil.which("blueutil")
    if not blueutil:
        return None
    try:
        result = subprocess.run(
            [blueutil] + args,
            capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, OSError):
        return None


def _find_hc05_address() -> Optional[str]:
    """Finds HC-05 Bluetooth address from paired devices via blueutil."""
    output = _run_blueutil(["--paired", "--format", "address,name"])
    if output is None:
        return None
    for line in output.splitlines():
        if "HC-05" in line or "HC05" in line:
            addr = line.split(",")[0].strip()
            if len(addr) == 17 and addr.count("-") == 5 or addr.count(":") == 5:
                return addr
    return None


def bluetooth_reconnect() -> bool:
    """
    Attempts to fix stale macOS Bluetooth SPP by disconnecting and
    reconnecting the HC-05. Returns True if the serial port appears.
    """
    addr = _find_hc05_address()
    if addr is None:
        return False

    print(f"{C_YELLOW}HC-05 found ({addr}) but serial port missing. Reconnecting...{C_RESET}")

    _run_blueutil(["--disconnect", addr], timeout=5.0)
    time.sleep(1.0)

    _run_blueutil(["--connect", addr], timeout=10.0)

    for _ in range(10):
        time.sleep(1.0)
        port = _scan_bt_ports()
        if port:
            print(f"{C_GREEN}Serial port appeared: {port}{C_RESET}")
            return True

    print(f"{C_RED}Serial port did not appear after reconnect.{C_RESET}")
    print(f"{C_YELLOW}Manual fix: System Settings → Bluetooth → HC-05 → Forget, then re-pair.{C_RESET}")
    return False


def _scan_bt_ports() -> Optional[str]:
    """Scans /dev/cu.* for HC-05 or Bluetooth serial ports."""
    patterns = [
        "/dev/cu.HC-05*",
        "/dev/cu.*HC*",
        "/dev/cu.*Bluetooth*",
        "/dev/cu.*bt*",
    ]
    for pat in patterns:
        matches = sorted(glob.glob(pat))
        if matches:
            return matches[0]
    return None


def auto_detect_bluetooth_port() -> Optional[str]:
    """Scans for Bluetooth HC-05 serial port, attempting reconnect if missing."""
    port = _scan_bt_ports()
    if port:
        return port

    if bluetooth_reconnect():
        return _scan_bt_ports()

    return None


def parse_board_frame(line: str) -> Optional[FrozenSet[str]]:
    """
    Parses 'BOARD:r1/r2/.../r8' frame from ATmega32.
    '1' = occupied, '0' = empty. Returns frozenset of occupied square names.
    """
    cleaned = line.strip()
    if cleaned.startswith("BOARD:"):
        cleaned = cleaned[6:]

    ranks = cleaned.split("/")
    if len(ranks) != 8 or any(len(r) != 8 for r in ranks):
        return None
    if any(ch not in "01" for r in ranks for ch in r):
        return None

    occupied: set[str] = set()
    for rank_idx, rank_str in enumerate(ranks):
        for file_idx, ch in enumerate(rank_str):
            if ch == "1":
                sq = f"{chr(ord('a') + file_idx)}{rank_idx + 1}"
                occupied.add(sq)

    return frozenset(occupied)


class SerialConnection:
    """Zero-dependency POSIX serial communication for macOS and Linux."""

    def __init__(self, port: str, baudrate: int = 9600):
        self.port = port
        self.baudrate = baudrate
        self.fd: Optional[int] = None

    def open(self) -> None:
        if self.baudrate not in BAUD_RATES:
            raise ValueError(f"Unsupported baudrate: {self.baudrate}")

        self.fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
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
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            raise

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def write(self, data: str) -> None:
        if self.fd is None:
            raise RuntimeError("Serial port is not open.")
        os.write(self.fd, data.encode("utf-8"))

    def flush_input(self) -> None:
        if self.fd is not None:
            try:
                termios.tcflush(self.fd, termios.TCIFLUSH)
            except Exception:
                pass

    def read_line(self, timeout: float = 0.5) -> Optional[str]:
        """Reads a single line terminated by newline or carriage return."""
        if self.fd is None:
            raise RuntimeError("Serial port is not open.")

        buf = bytearray()
        end_time = time.time() + timeout
        while time.time() < end_time:
            remaining = max(0.01, end_time - time.time())
            rlist, _, _ = select.select([self.fd], [], [], remaining)
            if rlist:
                try:
                    chunk = os.read(self.fd, 1)
                    if chunk:
                        if chunk in (b"\n", b"\r"):
                            if len(buf) > 0:
                                return buf.decode("utf-8", errors="replace").strip()
                        else:
                            buf.extend(chunk)
                except BlockingIOError:
                    continue
        return buf.decode("utf-8", errors="replace").strip() if buf else None

    def send_and_wait_ack(self, command: str, timeout: float = 25.0,
                          on_progress=None) -> bool:
        """
        Sends command and waits for 'OK' acknowledgement from ATmega firmware.
        PROG: lines from firmware reset the timeout window and call on_progress
        if provided.
        """
        cmd_clean = command.strip() + "\r\n"
        self.write(cmd_clean)

        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.read_line(timeout=1.0)
            if line:
                if line.startswith("PROG:"):
                    deadline = time.time() + timeout
                    if on_progress:
                        on_progress(line)
                    continue
                if "OK" in line:
                    return True
                elif "ERR" in line:
                    print(f"  {C_RED}[ATmega Error]{C_RESET} {line}")
                    return False
        return False

    def connect_with_handshake(self, max_attempts: int = 8) -> bool:
        """
        Opens port and runs PING/PONG handshake with ATmega.
        Returns True if connection established.
        """
        try:
            self.open()
        except Exception as e:
            print(f"{C_RED}Failed to open port {self.port}: {e}{C_RESET}")
            return False

        connected = False
        for attempt in range(1, max_attempts + 1):
            print(f"  Handshake attempt {attempt}/{max_attempts}... ", end="", flush=True)
            try:
                self.write("\r\n")
                time.sleep(0.1)
                self.write("PING\r\n")
            except Exception:
                pass

            start_t = time.time()
            while time.time() - start_t < 1.0:
                line = self.read_line(timeout=0.3)
                if line and ("PONG" in line or "Chessboard" in line or "OK" in line):
                    print(f"{C_GREEN}✓ Connected!{C_RESET}")
                    connected = True
                    break
            if connected:
                break
            time.sleep(0.5)

        if not connected:
            print(f"\n{C_RED}ATmega is not responding to PING.{C_RESET}")
            print(f"{C_YELLOW}Check: 1) Is ATmega powered? 2) Is HC-05 TXD wired to ATmega PD0 (RXD)?{C_RESET}")

        return connected
