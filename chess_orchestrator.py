#!/usr/bin/env python3
"""
Master Orchestrator for Automated Chessboard System.
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
import select
import sys
import time
from typing import Optional

from serial_connection import (
    SerialConnection, auto_detect_bluetooth_port,
    C_RESET, C_BOLD, C_GREEN, C_YELLOW, C_CYAN, C_RED, C_MAGENTA,
)
import chess

from game_tracker import GameTracker, DeduceResult
from move_planner import MovePlanner, MovePlan, MoveAction, ActionType
from stockfish_runner import StockfishEngine

CASTLING_ROOK_MOVES = {
    "e1g1": ("h1", "f1"),
    "e1c1": ("a1", "d1"),
    "e8g8": ("h8", "f8"),
    "e8c8": ("a8", "d8"),
}


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
        microstep: int = 8,
        speed: int = 200
    ):
        self.port = port
        self.baudrate = baudrate
        self.microstep = microstep
        self.speed = speed

        self.tracker = GameTracker()
        self.planner = MovePlanner(home_square="a1")
        self.engine = StockfishEngine(depth=depth)
        self.serial: Optional[SerialConnection] = None

    def initialize_hardware(self) -> bool:
        if not self.port:
            detected = auto_detect_bluetooth_port()
            if detected:
                self.port = detected
                print(f"Auto-detected Bluetooth HC-05 port: {C_GREEN}{self.port}{C_RESET}")
            else:
                print(f"{C_RED}No Bluetooth HC-05 device found.{C_RESET}")
                print(f"{C_YELLOW}Ensure your Mac is paired with the HC-05 in System Settings -> Bluetooth.{C_RESET}")
                return False

        if self.port.startswith("/dev/tty."):
            cu_alt = self.port.replace("/dev/tty.", "/dev/cu.")
            print(f"{C_YELLOW}Note: Switching '{self.port}' to call-up device '{cu_alt}' for macOS Bluetooth stability.{C_RESET}")
            self.port = cu_alt

        self.serial = SerialConnection(self.port, self.baudrate)
        print(f"Connecting to Bluetooth module at {self.port}...")

        if not self.serial.connect_with_handshake():
            return False

        print("Zeroing gantry head to square a1 (0, 0)...")
        self.serial.send_and_wait_ack("ZERO", timeout=5.0)

        print(f"Setting microstepping to 1/{self.microstep}...")
        self.serial.send_and_wait_ack(f"M {self.microstep}", timeout=3.0)

        print(f"Setting motor step delay to {self.speed} us...")
        self.serial.send_and_wait_ack(f"SPEED {self.speed}", timeout=3.0)

        print("Requesting current board scan from ATmega...")
        self.serial.write("SCAN\r\n")
        return True

    def _log_progress(self, line: str) -> None:
        print(f"    {C_CYAN}[MCU] {line}{C_RESET}")

    def execute_physical_plan(self, plan: MovePlan) -> bool:
        """Sends each planned step to the ATmega32 gantry sequentially."""
        total = len(plan.actions)
        print(f"\n{C_BOLD}Executing Physical Move Sequence ({total} steps):{C_RESET}")

        failed_at = None
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
                tag = f"[{action.action_type.value}]"

            print(f"  Step {idx}/{total}: {tag} {action.piece} {action.from_sq}->{action.to_sq}")
            print(f"    UART TX => '{cmd}'")

            if not self.serial:
                time.sleep(0.3)
                continue

            success = self.serial.send_and_wait_ack(
                cmd, timeout=60.0, on_progress=self._log_progress
            )
            if not success:
                print(f"  {C_RED}Step {idx} failed or timed out! Halting sequence.{C_RESET}")
                failed_at = idx
                break

        if failed_at is not None:
            self._recover_gantry()
            return False

        print(f"{C_GREEN}✓ Physical execution complete! Gantry returned to a1.{C_RESET}\n")
        return True

    def _is_castling_move(self, uci: str) -> bool:
        """Check if a UCI move is castling by verifying it's a king move to a castling square."""
        if uci not in CASTLING_ROOK_MOVES:
            return False
        from_sq = uci[0:2]
        piece = self.tracker.board.get(from_sq, "")
        return piece in ("K", "k")

    def _recover_gantry(self) -> None:
        """Emergency recovery: turn off magnet and attempt to home gantry."""
        if not self.serial:
            return
        print(f"{C_YELLOW}Attempting recovery: magnet off + home to a1...{C_RESET}")
        self.serial.flush_input()
        time.sleep(1.0)
        self.serial.flush_input()
        self.serial.send_and_wait_ack("MAG 0", timeout=5.0)
        self.serial.send_and_wait_ack("SQ a1", timeout=60.0)

    def process_reed_frame(self, frame_line: str) -> None:
        """Processes an incoming reed-switch frame, runs Stockfish, and executes response."""
        print(f"\n{C_BOLD}------------------------------------------------------{C_RESET}")
        print(f"Received ATmega Sensor Frame: {C_CYAN}{frame_line.strip()}{C_RESET}")

        result: DeduceResult = self.tracker.ingest_reed_frame(frame_line)
        if not result.move_detected:
            print(f"Status: {result.description}")
            return

        print(f"{C_GREEN}✓ Human Move Detected:{C_RESET} {C_BOLD}{result.san}{C_RESET} ({result.uci})")
        print(f"  Description: {result.description}")
        print(f"  Current FEN: {result.fen_after}")

        game_over = self.tracker.game_result()
        if game_over:
            print(f"\n{C_BOLD}{game_over}{C_RESET}")
            return

        print(f"\nConsulting Stockfish Engine (Depth {self.engine.depth})...")
        best_move, eval_str = self.engine.get_evaluation_and_best_move(result.fen_after)
        print(f"  Best Response : {C_BOLD}{C_GREEN}{best_move}{C_RESET}")
        print(f"  Evaluation    : {eval_str}")

        is_castling = self._is_castling_move(best_move)

        if is_castling:
            rook_from, rook_to = CASTLING_ROOK_MOVES[best_move]
            king_from, king_to = best_move[:2], best_move[2:4]
            king_piece = self.tracker.board.get(king_from, "K")
            rook_piece = self.tracker.board.get(rook_from, "R")
            print(f"\nCastling detected! Rook {rook_from}->{rook_to}, then king {king_from}->{king_to}")

            # Castling legality guarantees the path between king and rook is
            # empty, so both pieces can use single direct MOVE commands.
            # Rook moves first (entirely clear path), then king slides through
            # the rook's new square in one continuous gantry motion.
            plan = MovePlan(original_move=best_move)
            plan.path = [king_from, king_to]
            plan.actions = [
                MoveAction(ActionType.MOVE_MAIN, rook_from, rook_to,
                           rook_piece, f"Castling: rook {rook_from}->{rook_to}"),
                MoveAction(ActionType.MOVE_MAIN, king_from, king_to,
                           king_piece, f"Castling: king {king_from}->{king_to}"),
                MoveAction(ActionType.HOME_GANTRY, king_to, self.planner.home_square,
                           "Gantry", f"Return gantry head to origin ({self.planner.home_square})"),
            ]
        else:
            print(f"\nPlanning physical path and obstacle parking for '{best_move}'...")
            plan = self.planner.plan_move(best_move, self.tracker.board, home_at_end=True)

        print(f"  Planned Path : {' -> '.join(plan.path)}")
        if plan.parking_assignments:
            for obs, park in plan.parking_assignments.items():
                print(f"  {C_YELLOW}Obstacle Park: {obs} temporarily parks at {park}{C_RESET}")
        else:
            print("  Route is completely clear.")

        success = self.execute_physical_plan(plan)

        if success:
            src = best_move[0:2]
            dst = best_move[2:4]
            self.tracker.apply_move(src, dst)
            print(f"New FEN: {self.tracker.get_fen()}")

            game_over = self.tracker.game_result()
            if game_over:
                print(f"\n{C_BOLD}{game_over}{C_RESET}")
            else:
                print(f"{C_CYAN}Waiting for next user move (press board button INT0)...{C_RESET}")

    def handle_cli_command(self, cmd: str) -> bool:
        """Handles a CLI command. Returns False to exit."""
        parts = cmd.split()
        verb = parts[0].lower()

        if verb in ("exit", "quit", "q"):
            return False

        elif verb == "help":
            self.print_help()

        elif verb == "reset":
            print(f"{C_YELLOW}Resetting game state...{C_RESET}")
            if self.serial:
                self.serial.flush_input()
                self.serial.send_and_wait_ack("MAG 0", timeout=5.0)
                print("  Move gantry to a1 by hand, then press Enter.")
                input()
                self.serial.send_and_wait_ack("ZERO", timeout=5.0)
            self.tracker.reset()
            self.engine.close()
            self.engine = StockfishEngine(depth=self.engine.depth)
            print(f"{C_GREEN}✓ Game reset to starting position.{C_RESET}")
            if self.serial:
                self.serial.write("SCAN\r\n")

        elif verb == "fen":
            print(f"FEN: {self.tracker.get_fen()}")

        elif verb == "scan":
            if self.serial:
                print("Requesting board scan...")
                self.serial.write("SCAN\r\n")

        elif verb == "board":
            self._print_board()

        elif verb in ("speed", "delay"):
            if len(parts) >= 2 and self.serial:
                spd = parts[1]
                print(f"Setting motor step delay to {spd} us...")
                if self.serial.send_and_wait_ack(f"SPEED {spd}", timeout=4.0):
                    self.speed = int(spd)
                    print(f"{C_GREEN}✓ Speed updated to {spd} us{C_RESET}")
            else:
                print(f"Current speed: {self.speed} us. Usage: speed <us>")

        elif verb in ("m", "microstep"):
            if len(parts) >= 2 and self.serial:
                mode = parts[1]
                print(f"Setting microstepping to 1/{mode}...")
                if self.serial.send_and_wait_ack(f"M {mode}", timeout=4.0):
                    self.microstep = int(mode)
                    print(f"{C_GREEN}✓ Microstepping set to 1/{mode}{C_RESET}")
            else:
                print(f"Current: 1/{self.microstep}. Usage: m <1|2|4|8|16|32>")

        elif verb == "mag":
            if len(parts) >= 2 and self.serial:
                if parts[1].lower() in ("on", "1"):
                    self.serial.send_and_wait_ack("MAG 1", timeout=4.0)
                    print(f"{C_GREEN}Magnet ON{C_RESET}")
                elif parts[1].lower() in ("off", "0"):
                    self.serial.send_and_wait_ack("MAG 0", timeout=4.0)
                    print(f"{C_GREEN}Magnet OFF{C_RESET}")
            else:
                print("Usage: mag <on|off>")

        elif verb == "sq":
            if len(parts) >= 2 and self.serial:
                target = parts[1].lower()
                print(f"Moving gantry to {target.upper()}...")
                if self.serial.send_and_wait_ack(f"SQ {target}", timeout=35.0):
                    print(f"{C_GREEN}✓ Gantry at {target.upper()}{C_RESET}")
            else:
                print("Usage: sq <square> (e.g. sq e4)")

        elif verb == "zero":
            if self.serial:
                self.serial.send_and_wait_ack("ZERO", timeout=4.0)
                print(f"{C_GREEN}✓ Position zeroed at a1{C_RESET}")

        elif verb == "depth":
            if len(parts) >= 2:
                d = int(parts[1])
                self.engine.depth = d
                print(f"{C_GREEN}✓ Stockfish depth set to {d}{C_RESET}")
            else:
                print(f"Current depth: {self.engine.depth}. Usage: depth <n>")

        else:
            print(f"{C_RED}Unknown command: '{verb}'. Type 'help' for commands.{C_RESET}")

        return True

    def print_help(self) -> None:
        print(f"""
{C_BOLD}=== Orchestrator Commands ==={C_RESET}
  {C_CYAN}reset{C_RESET}               : Reset game to starting position (magnet off, re-zero, new game)
  {C_CYAN}fen{C_RESET}                 : Show current FEN position
  {C_CYAN}board{C_RESET}               : Show current board state
  {C_CYAN}scan{C_RESET}                : Request board scan from ATmega

  {C_CYAN}speed <us>{C_RESET}          : Set motor step delay (e.g. {C_GREEN}speed 200{C_RESET})
  {C_CYAN}m <1|2|4|8|16|32>{C_RESET}   : Set microstepping (e.g. {C_GREEN}m 8{C_RESET})
  {C_CYAN}depth <n>{C_RESET}           : Set Stockfish search depth (e.g. {C_GREEN}depth 14{C_RESET})

  {C_CYAN}sq <square>{C_RESET}         : Move gantry to square without magnet (e.g. {C_GREEN}sq e4{C_RESET})
  {C_CYAN}mag <on|off>{C_RESET}        : Manually toggle electromagnet
  {C_CYAN}zero{C_RESET}                : Zero current position as a1

  {C_CYAN}help{C_RESET}                : Show this menu
  {C_CYAN}exit{C_RESET}                : Quit orchestrator
""")

    def _print_board(self) -> None:
        print(f"\n{C_BOLD}     a   b   c   d   e   f   g   h{C_RESET}")
        print("   +---+---+---+---+---+---+---+---+")
        for rank in range(8, 0, -1):
            row = f" {rank} |"
            for file_idx in range(8):
                sq = f"{chr(ord('a') + file_idx)}{rank}"
                piece = self.tracker.board.get(sq)
                if piece:
                    row += f" {C_BOLD}{piece}{C_RESET} |"
                else:
                    row += f" . |"
            row += f" {rank}"
            print(row)
            print("   +---+---+---+---+---+---+---+---+")
        print(f"{C_BOLD}     a   b   c   d   e   f   g   h{C_RESET}")
        print(f"  Turn: {self.tracker.active_color} | FEN: {self.tracker.get_fen()}\n")

    def run_hardware_listener(self) -> None:
        """Main loop: listens for ATmega board frames and CLI input."""
        if not self.serial:
            return

        print(f"\n{C_BOLD}Orchestrator ready. Listening for board scans and CLI input.{C_RESET}")
        print(f"Type {C_CYAN}'help'{C_RESET} for commands, or press the INT0 button to scan.\n")

        try:
            while True:
                rlist, _, _ = select.select(
                    [sys.stdin, self.serial.fd], [], [], 0.1
                )

                if self.serial.fd in rlist:
                    line = self.serial.read_line(timeout=0.1)
                    if line:
                        if line.startswith("BOARD:"):
                            self.process_reed_frame(line)
                        elif "STATUS" in line:
                            print(f"  {C_CYAN}[MCU] {line}{C_RESET}")

                if sys.stdin in rlist:
                    user_line = sys.stdin.readline()
                    if not user_line:
                        break
                    cmd = user_line.strip()
                    if not cmd:
                        continue
                    if not self.handle_cli_command(cmd):
                        break

        except KeyboardInterrupt:
            print("\nReturning to menu...")

    def run_simulation(self) -> None:
        """Interactive terminal simulation without hardware."""
        print(f"\n{C_BOLD}======================================================{C_RESET}")
        print(f"  Automated Chessboard Orchestrator — Simulation Mode")
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

                if not res.move_detected:
                    print(f"  {C_RED}{res.description}{C_RESET}")
                    continue

                print(f"  Human move: {res.san} ({res.uci})")
                print(f"  FEN: {res.fen_after}")

                game_over = self.tracker.game_result()
                if game_over:
                    print(f"\n{C_BOLD}{game_over}{C_RESET}")
                    break

                print(f"\nStockfish calculating response...")
                best_move, eval_str = self.engine.get_evaluation_and_best_move(res.fen_after)
                print(f"  Stockfish Move: {C_BOLD}{C_GREEN}{best_move}{C_RESET} (Eval: {eval_str})")

                is_castling = self._is_castling_move(best_move)
                if is_castling:
                    rook_from, rook_to = CASTLING_ROOK_MOVES[best_move]
                    king_from, king_to = best_move[:2], best_move[2:4]
                    king_piece = self.tracker.board.get(king_from, "K")
                    rook_piece = self.tracker.board.get(rook_from, "R")
                    plan = MovePlan(original_move=best_move)
                    plan.path = [king_from, king_to]
                    plan.actions = [
                        MoveAction(ActionType.MOVE_MAIN, rook_from, rook_to,
                                   rook_piece, f"Castling: rook {rook_from}->{rook_to}"),
                        MoveAction(ActionType.MOVE_MAIN, king_from, king_to,
                                   king_piece, f"Castling: king {king_from}->{king_to}"),
                        MoveAction(ActionType.HOME_GANTRY, king_to, self.planner.home_square,
                                   "Gantry", f"Return gantry head to origin ({self.planner.home_square})"),
                    ]
                else:
                    plan = self.planner.plan_move(best_move, self.tracker.board, home_at_end=True)

                print(f"\n{plan.summary()}")
                self.execute_physical_plan(plan)

                self.tracker.apply_move(best_move[0:2], best_move[2:4])

                game_over = self.tracker.game_result()
                if game_over:
                    print(f"\n{C_BOLD}{game_over}{C_RESET}")
                    break

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
    parser.add_argument("--microstep", type=int, default=8, choices=[1, 2, 4, 8, 16, 32], help="Microstepping mode (default: 8 for 1/8)")
    parser.add_argument("--speed", type=int, default=200, help="Step pulse delay in microseconds (default: 200 us)")
    parser.add_argument("--simulate", action="store_true", help="Run interactive terminal simulation mode")

    args = parser.parse_args()

    orchestrator = ChessOrchestrator(
        port=args.port,
        baudrate=args.baud,
        depth=args.depth,
        microstep=args.microstep,
        speed=args.speed
    )

    try:
        if args.simulate:
            orchestrator.run_simulation()
        else:
            if orchestrator.initialize_hardware():
                orchestrator.run_hardware_listener()
            else:
                print(f"\n{C_YELLOW}Tip: You can test the entire loop right now in simulation mode:{C_RESET}")
                print("    python3 chess_orchestrator.py --simulate\n")
    finally:
        if orchestrator.serial:
            orchestrator.serial.close()
        orchestrator.engine.close()


if __name__ == "__main__":
    main()
