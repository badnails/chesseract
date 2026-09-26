#!/usr/bin/env python3
"""
Master Orchestrator for Automated Chessboard System.
Follows Single Responsibility Principle (SRP):
Coordinates the end-to-end loop:
1. Ingests reed-switch matrix frames from ATmega32 (INT0 scan button).
2. Deduces human move and updates official FEN via GameTracker.
3. Queries native Stockfish via StockfishEngine for the best response move.
4. Plans physical gantry steps (least-obstructed path, parking, LIFO restoration) via MovePlanner.
5. Transmits step-by-step UART commands to ATmega32 gantry, homing always to a1.
6. Synchronizes game state for the next turn.

Supports both Live Hardware Mode and Interactive Simulation Mode (--simulate).
"""

from __future__ import annotations

import argparse
import glob
import os
import select
import sys
import termios
import time
from typing import Optional

from game_tracker import GameTracker, DeduceResult
from move_planner import MovePlanner, BoardState, MovePlan, ActionType
from stockfish_runner import StockfishEngine

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

# ANSI colors for clear console output
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_CYAN = "\033[36m"
C_RED = "\033[31m"
C_MAGENTA = "\033[35m"


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
            settings[3] = 0  # Raw mode
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
        payload = data.encode("utf-8")
        os.write(self.fd, payload)

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

    def send_and_wait_ack(self, command: str, timeout: float = 25.0) -> bool:
        """Sends command and waits for 'OK' acknowledgement from ATmega firmware."""
        cmd_clean = command.strip() + "\r\n"
        self.write(cmd_clean)

        start = time.time()
        while time.time() - start < timeout:
            line = self.read_line(timeout=1.0)
            if line:
                if "OK" in line:
                    return True
                elif "ERR" in line:
                    print(f"  {C_RED}[ATmega Error]{C_RESET} {line}")
                    return False
        return False


def auto_detect_serial_port() -> Optional[str]:
    """Scans for USB-Serial and Bluetooth HC-05 devices on macOS."""
    patterns = [
        "/dev/cu.usbserial*",
        "/dev/cu.usbmodem*",
        "/dev/cu.wchusbserial*",
        "/dev/tty.HC-05*",
        "/dev/cu.HC-05*",
    ]
    for pat in patterns:
        matches = sorted(glob.glob(pat))
        if matches:
            return matches[0]
    return None


class ChessOrchestrator:
    """
    Coordinates state tracking, engine evaluation, physical move planning,
    and ATmega32 serial communication.
    """

    def __init__(
        self,
        port: Optional[str] = None,
        baudrate: int = 9600,
        depth: int = 14,
        dry_run: bool = False
    ):
        self.port = port
        self.baudrate = baudrate
        self.dry_run = dry_run

        self.tracker = GameTracker()
        self.planner = MovePlanner(home_square="a1")
        self.engine = StockfishEngine(depth=depth)
        self.serial: Optional[SerialConnection] = None

    def initialize_hardware(self) -> bool:
        if self.dry_run:
            print(f"{C_YELLOW}[DRY-RUN MODE] Hardware serial transmissions disabled.{C_RESET}")
            return True

        if not self.port:
            detected = auto_detect_serial_port()
            if detected:
                self.port = detected
                print(f"Auto-detected ATmega32 serial port: {C_GREEN}{self.port}{C_RESET}")
            else:
                print(f"{C_RED}No USB-UART or Bluetooth port auto-detected.{C_RESET}")
                return False

        try:
            self.serial = SerialConnection(self.port, self.baudrate)
            self.serial.open()
            print(f"✓ Connected to ATmega32 at {self.port} @ {self.baudrate} baud.")
            time.sleep(1.5)  # Allow ATmega to reboot/settle

            # Initialize gantry coordinate system
            print("Zeroing gantry head to square a1 (0, 0)...")
            self.serial.send_and_wait_ack("ZERO")
            return True
        except Exception as e:
            print(f"{C_RED}Failed to open serial port {self.port}: {e}{C_RESET}")
            return False

    def execute_physical_plan(self, plan: MovePlan) -> bool:
        """Sends each planned step to the ATmega32 gantry sequentially."""
        total = len(plan.actions)
        print(f"\n{C_BOLD}Executing Physical Move Sequence ({total} steps):{C_RESET}")

        for idx, action in enumerate(plan.actions, start=1):
            cmd = action.to_gantry_command()

            if action.action_type == ActionType.EVACUATE:
                tag = f"{C_YELLOW}[1. EVACUATE]{C_RESET}"
            elif action.action_type == ActionType.MOVE_MAIN:
                tag = f"{C_GREEN}[2. MAIN MOVE]{C_RESET}"
            elif action.action_type == ActionType.RESTORE:
                tag = f"{C_CYAN}[3. RESTORE]{C_RESET}"
            elif action.action_type == ActionType.HOME_GANTRY:
                tag = f"{C_MAGENTA}[4. HOME GANTRY]{C_RESET}"
            else:
                tag = f"{C_RED}[CAPTURE]{C_RESET}"

            print(f"  Step {idx}/{total}: {tag} {action.piece} {action.from_sq}->{action.to_sq}")
            print(f"    UART TX => '{cmd}'")

            if self.dry_run or not self.serial:
                time.sleep(0.3)
                continue

            success = self.serial.send_and_wait_ack(cmd, timeout=30.0)
            if not success:
                print(f"  {C_RED}Step {idx} failed or timed out! Halting sequence.{C_RESET}")
                return False

        print(f"{C_GREEN}✓ Physical execution complete! Gantry returned to a1.{C_RESET}\n")
        return True

    def process_reed_frame(self, frame_line: str) -> None:
        """Processes an incoming reed-switch frame, runs Stockfish, and executes response."""
        print(f"\n{C_BOLD}------------------------------------------------------{C_RESET}")
        print(f"📥 Received ATmega Sensor Frame: {C_CYAN}{frame_line.strip()}{C_RESET}")

        # 1. Ingest frame and deduce human move
        result: DeduceResult = self.tracker.ingest_reed_frame(frame_line)
        if not result.move_detected:
            print(f"Status: {result.description}")
            return

        print(f"{C_GREEN}✓ Human Move Detected:{C_RESET} {C_BOLD}{result.san}{C_RESET} ({result.uci})")
        print(f"  Description: {result.description}")
        print(f"  Current FEN: {result.fen_after}")

        # 2. Query Stockfish for best response
        print(f"\n🤖 Consulting Stockfish Engine (Depth {self.engine.depth})...")
        best_move, eval_str = self.engine.get_evaluation_and_best_move(result.fen_after)
        print(f"  Best Response : {C_BOLD}{C_GREEN}{best_move}{C_RESET}")
        print(f"  Evaluation    : {eval_str}")

        # 3. Plan physical gantry motion
        print(f"\n📐 Planning physical path and obstacle parking for '{best_move}'...")
        # Create planner board state from tracker
        board_state = BoardState(dict(self.tracker.board))
        plan = self.planner.plan_move(best_move, board_state, home_at_end=True)

        print(f"  Planned Path : {' -> '.join(plan.path)}")
        if plan.parking_assignments:
            for obs, park in plan.parking_assignments.items():
                print(f"  {C_YELLOW}Obstacle Park: {obs} temporarily parks at {park}{C_RESET}")
        else:
            print("  Route is completely clear.")

        # 4. Physically execute moves on gantry
        success = self.execute_physical_plan(plan)

        # 5. Synchronize tracker state with Stockfish's move
        if success:
            src = best_move[0:2]
            dst = best_move[2:4]
            self.tracker.apply_move(src, dst)
            print(f"New FEN: {self.tracker.get_fen()}")
            print(f"{C_CYAN}Waiting for next user move (press board button INT0)...{C_RESET}")

    def run_hardware_listener(self) -> None:
        """Main listening loop reading serial input from ATmega32."""
        if not self.serial:
            return

        print(f"\n{C_BOLD}Listening for INT0 button triggers from ATmega32...{C_RESET}")
        print(f"Press Ctrl+C to terminate.")

        try:
            while True:
                line = self.serial.read_line(timeout=0.1)
                if line and line.startswith("BOARD:"):
                    self.process_reed_frame(line)
        except KeyboardInterrupt:
            print("\nShutting down orchestrator...")
        finally:
            if self.serial:
                self.serial.close()
            self.engine.close()

    def run_simulation(self) -> None:
        """Interactive terminal simulation without hardware."""
        print(f"\n{C_BOLD}======================================================{C_RESET}")
        print(f"  Automated Chessboard Orchestrator &mdash; Simulation Mode")
        print(f"{C_BOLD}======================================================{C_RESET}")
        print("Type standard moves (e.g. 'e2e4', 'd2d4', 'g1f3') to simulate")
        print("human moves, or type 'exit' to quit.\n")

        try:
            while True:
                user_input = input(f"{C_BOLD}Simulate Human Move > {C_RESET}").strip().lower()
                if not user_input:
                    continue
                if user_input in ("exit", "quit"):
                    break

                if len(user_input) != 4:
                    print(f"{C_RED}Format must be 4 characters (e.g. 'e2e4'){C_RESET}")
                    continue

                src = user_input[0:2]
                dst = user_input[2:4]

                if not self.tracker.board.get(src):
                    print(f"{C_RED}Square {src} is empty! Valid squares: {list(self.tracker.board.keys())[:8]}...{C_RESET}")
                    continue

                print(f"\nSimulating human physical move: {src} -> {dst}")
                res = self.tracker.apply_move(src, dst)
                print(f"  Human move registered: {res.san} ({res.uci})")
                print(f"  FEN after human move: {res.fen_after}")

                # Query Stockfish
                print(f"\n🤖 Stockfish calculating response...")
                best_move, eval_str = self.engine.get_evaluation_and_best_move(res.fen_after)
                print(f"  Stockfish Move: {C_BOLD}{C_GREEN}{best_move}{C_RESET} (Eval: {eval_str})")

                # Plan physical moves
                planner_board = BoardState(dict(self.tracker.board))
                plan = self.planner.plan_move(best_move, planner_board, home_at_end=True)
                print(f"\n{plan.summary()}")

                # Execute
                self.execute_physical_plan(plan)

                # Update state
                sf_src = best_move[0:2]
                sf_dst = best_move[2:4]
                self.tracker.apply_move(sf_src, sf_dst)
                print(f"Ready for next move. Current FEN: {self.tracker.get_fen()}\n")

        except KeyboardInterrupt:
            print("\nExiting simulation.")
        finally:
            self.engine.close()


def main():
    parser = argparse.ArgumentParser(
        description="Master Orchestrator for Automated Chessboard Gantry & Stockfish Engine."
    )
    parser.add_argument("--port", type=str, default=None, help="Serial port (e.g. /dev/cu.usbserial-110)")
    parser.add_argument("--baud", type=int, default=9600, help="Baud rate (default 9600)")
    parser.add_argument("--depth", type=int, default=14, help="Stockfish search depth (default 14)")
    parser.add_argument("--dry-run", action="store_true", help="Log commands without writing to hardware serial")
    parser.add_argument("--simulate", action="store_true", help="Run interactive terminal simulation mode")

    args = parser.parse_args()

    orchestrator = ChessOrchestrator(
        port=args.port,
        baudrate=args.baud,
        depth=args.depth,
        dry_run=args.dry_run
    )

    if args.simulate:
        orchestrator.run_simulation()
    else:
        if orchestrator.initialize_hardware():
            orchestrator.run_hardware_listener()
        else:
            print(f"\n{C_YELLOW}Tip: You can test the entire loop right now in simulation mode:{C_RESET}")
            print("    python3 chess_orchestrator.py --simulate\n")


if __name__ == "__main__":
    main()

