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
    C_RESET, C_BOLD, C_GREEN, C_YELLOW, C_CYAN, C_RED, C_MAGENTA,
)
from move_planner import MovePlanner, MovePlan, MoveAction, ActionType, coord_to_square


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
  {C_GREEN}4{C_RESET}  Demo Mode         — Free movement with pathfinding
  {C_GREEN}q{C_RESET}  Quit
""")


def run_full_game(serial: SerialConnection, depth: int, microstep: int, speed: int, server=None):
    from chess_orchestrator import ChessOrchestrator
    orch = ChessOrchestrator(depth=depth, microstep=microstep, speed=speed)
    orch.serial = serial
    orch.web_server = server
    orch.tracker.reset()

    if server:
        server.set_difficulty_callback(orch.handle_difficulty_change)

    print("Requesting board scan...")
    serial.write("SCAN\r\n")
    try:
        orch.run_hardware_listener()
    finally:
        if server:
            server.set_difficulty_callback(None)
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


DEMO_CASTLING_MOVES = {
    "e1g1": ("h1", "f1"),
    "e1c1": ("a1", "d1"),
    "e8g8": ("h8", "f8"),
    "e8c8": ("a8", "d8"),
}


def _init_demo_board():
    """16 white pieces in standard starting positions."""
    board = {}
    back_rank = ["R", "N", "B", "Q", "K", "B", "N", "R"]
    for f in range(8):
        board[coord_to_square(f, 0)] = back_rank[f]
        board[coord_to_square(f, 1)] = "P"
    return board


def _print_demo_board(board):
    print(f"\n{C_BOLD}     a   b   c   d   e   f   g   h{C_RESET}")
    print("   +---+---+---+---+---+---+---+---+")
    for rank in range(8, 0, -1):
        row = f" {rank} |"
        for fi in range(8):
            sq = f"{chr(ord('a') + fi)}{rank}"
            piece = board.get(sq)
            if piece:
                row += f" {C_BOLD}{piece}{C_RESET} |"
            else:
                row += f" . |"
        row += f" {rank}"
        print(row)
        print("   +---+---+---+---+---+---+---+---+")
    print(f"{C_BOLD}     a   b   c   d   e   f   g   h{C_RESET}")
    print(f"  Pieces on board: {C_GREEN}{len(board)}{C_RESET}\n")


def _execute_demo_plan(serial, plan, board=None, server=None):
    """Sends each planned step to hardware. Returns True on success.
    When board and server are provided, pushes intermediate board states
    to the web UI after each hop for real-time animation."""
    total = len(plan.actions)
    print(f"\n{C_BOLD}Executing ({total} steps):{C_RESET}")

    viz = dict(board) if board else None

    tags = {
        ActionType.EVACUATE: f"{C_YELLOW}EVACUATE{C_RESET}",
        ActionType.MOVE_MAIN: f"{C_GREEN}MOVE{C_RESET}",
        ActionType.RESTORE: f"{C_CYAN}RESTORE{C_RESET}",
        ActionType.HOME_GANTRY: f"{C_MAGENTA}HOME{C_RESET}",
    }

    for idx, action in enumerate(plan.actions, start=1):
        cmd = action.to_gantry_command()
        tag = tags.get(action.action_type, action.action_type.value)
        print(f"  {idx}/{total}: [{tag}] {action.piece} {action.from_sq}->{action.to_sq}  ({cmd})")

        success = serial.send_and_wait_ack(
            cmd, timeout=60.0,
            on_progress=lambda line: print(f"         {C_CYAN}{line}{C_RESET}")
        )
        if not success:
            print(f"  {C_RED}Step {idx} failed! Recovering...{C_RESET}")
            serial.flush_input()
            serial.send_and_wait_ack("MAG 0", timeout=5.0)
            serial.send_and_wait_ack("SQ a1", timeout=60.0)
            return False

        if viz is not None and server:
            last_move = None
            if action.action_type != ActionType.HOME_GANTRY:
                if action.from_sq in viz:
                    viz[action.to_sq] = viz.pop(action.from_sq)
                last_move = {"from": action.from_sq, "to": action.to_sq}
            server.push_board_state(viz, last_move=last_move, gantry_action={
                "step": f"{idx}/{total}",
                "type": action.action_type.value,
                "detail": action.reason,
                "piece": action.piece,
            })

    print(f"{C_GREEN}✓ Done. Gantry at a1.{C_RESET}")
    return True


def run_demo_mode(serial, server=None):
    print(f"\n{C_BOLD}=== Demo Mode ==={C_RESET}")
    print(f"Board starts with 16 white pieces. Type moves like {C_GREEN}e2e4{C_RESET}, {C_GREEN}g1f3{C_RESET}.")
    print(f"Castling: {C_GREEN}e1g1{C_RESET} (kingside) or {C_GREEN}e1c1{C_RESET} (queenside).")
    print(f"Commands: {C_CYAN}board{C_RESET}, {C_CYAN}reset{C_RESET}, {C_CYAN}back{C_RESET}\n")

    planner = MovePlanner(home_square="a1")
    board = _init_demo_board()
    if server:
        server.push_board_state(board)

    _print_demo_board(board)

    try:
        while True:
            user_input = input(f"{C_BOLD}Demo > {C_RESET}").strip().lower()
            if not user_input:
                continue

            if user_input in ("back", "menu"):
                return
            if user_input in ("exit", "quit"):
                sys.exit(0)
            if user_input == "board":
                _print_demo_board(board)
                continue
            if user_input == "reset":
                board = _init_demo_board()
                print(f"{C_GREEN}✓ Board reset to starting position.{C_RESET}")
                if server:
                    server.push_board_state(board)
                _print_demo_board(board)
                continue
            if user_input == "help":
                print(f"  Type a move ({C_GREEN}e2e4{C_RESET}) or: board, reset, back")
                continue

            if len(user_input) != 4:
                print(f"{C_RED}Format: 4 characters (e.g. e2e4){C_RESET}")
                continue

            from_sq = user_input[0:2]
            to_sq = user_input[2:4]

            for sq in (from_sq, to_sq):
                if len(sq) != 2 or sq[0] not in "abcdefgh" or sq[1] not in "12345678":
                    print(f"{C_RED}Invalid square: {sq}{C_RESET}")
                    break
            else:
                if from_sq not in board:
                    print(f"{C_RED}No piece on {from_sq}.{C_RESET}")
                    continue

                is_castling = (
                    user_input in DEMO_CASTLING_MOVES
                    and board.get(from_sq) in ("K", "k")
                )

                if to_sq in board and not is_castling:
                    print(f"{C_RED}Destination {to_sq} is occupied ({board[to_sq]}). Move it first.{C_RESET}")
                    continue

                if is_castling:
                    rook_from, rook_to = DEMO_CASTLING_MOVES[user_input]
                    king_piece = board[from_sq]
                    rook_piece = board.get(rook_from, "R")

                    if rook_from not in board:
                        print(f"{C_RED}No rook on {rook_from} for castling.{C_RESET}")
                        continue

                    print(f"\n{C_BOLD}Castling: rook {rook_from}->{rook_to}, king {from_sq}->{to_sq}{C_RESET}")

                    plan = MovePlan(original_move=user_input)
                    plan.path = [from_sq, to_sq]
                    plan.actions = [
                        MoveAction(ActionType.MOVE_MAIN, rook_from, rook_to,
                                   rook_piece, f"Castling: rook {rook_from}->{rook_to}"),
                        MoveAction(ActionType.MOVE_MAIN, from_sq, to_sq,
                                   king_piece, f"Castling: king {from_sq}->{to_sq}"),
                        MoveAction(ActionType.HOME_GANTRY, to_sq, planner.home_square,
                                   "Gantry", "Return to a1"),
                    ]

                    if _execute_demo_plan(serial, plan, board=board, server=server):
                        board[to_sq] = board.pop(from_sq)
                        board[rook_to] = board.pop(rook_from)
                        if server:
                            server.push_board_state(board)
                        _print_demo_board(board)
                else:
                    print(f"\n{C_BOLD}Planning {from_sq} -> {to_sq}...{C_RESET}")
                    plan = planner.plan_move(user_input, board, home_at_end=True)

                    print(f"  Path: {' -> '.join(plan.path)}")
                    if plan.obstructing_squares:
                        for obs, park in plan.parking_assignments.items():
                            print(f"  {C_YELLOW}Evacuate {board.get(obs, '?')} on {obs} -> park at {park}{C_RESET}")
                    else:
                        print(f"  {C_GREEN}Path is clear.{C_RESET}")

                    if _execute_demo_plan(serial, plan, board=board, server=server):
                        board[to_sq] = board.pop(from_sq)
                        if server:
                            server.push_board_state(board)
                        _print_demo_board(board)

    except KeyboardInterrupt:
        print("\nReturning to menu...")


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

    from demo_server import DemoServer
    server = None
    for port in (8080, 8081, 8082):
        srv = DemoServer(port=port)
        if srv.start():
            server = srv
            break
    if server:
        print(f"{C_GREEN}Live board view:{C_RESET} {C_BOLD}http://localhost:{server.port}{C_RESET}")
    else:
        print(f"{C_YELLOW}Web server unavailable (ports 8080-8082 in use).{C_RESET}")

    try:
        while True:
            print_main_menu()
            choice = input(f"{C_BOLD}> {C_RESET}").strip().lower()

            if choice in ("q", "quit", "exit"):
                break
            elif choice == "1":
                run_full_game(serial, args.depth, args.microstep, args.speed, server=server)
            elif choice == "2":
                run_board_diagnostic(serial)
            elif choice == "3":
                run_gantry_diagnostic(serial, args.microstep, args.speed)
            elif choice == "4":
                run_demo_mode(serial, server=server)
            else:
                print(f"{C_RED}Invalid choice.{C_RESET}")

    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        if server:
            server.stop()
        serial.close()
        print("Done.")


if __name__ == "__main__":
    main()
