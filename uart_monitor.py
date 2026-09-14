#!/usr/bin/env python3
"""
Physical Chessboard Local Analysis Workbench Bridge for ATmega32 8x8 Reed Grid.

Features:
- Connects ATmega32 physical reed-switch chessboard over UART to Mac.
- Interactive software simulation mode (--simulate) listening on STDIN.
- Tracks piece identities, moves, captures, castling, and promotions.
- Supports partial initialization (e.g. testing only row 1 with 8 magnets).
- Generates live FEN and PGN files (game.pgn, live.fen).
- Serves local desktop analysis workbench on http://localhost:8080:
  - Lichess Chessground board UI with 60 FPS piece animations.
  - Stockfish (WASM) running locally in-browser Web Worker.
  - Real-time vertical centipawn evaluation bar.
  - Green SVG best-move arrow drawn natively on Chessground.
  - Web Audio synthesized wooden move/capture sound effects.
  - Real-time Server-Sent Events (SSE) for sub-millisecond move updates.
- 100% offline, zero npm/Node.js dependencies.
"""

from __future__ import annotations

import argparse
import datetime as dt
import errno
import fcntl
import glob
import http.server
import json
import mimetypes
import os
import select
import signal
import sys
import termios
import threading
import time


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

BOARD_RANKS = 8
BOARD_FILES = 8

STARTING_PIECES = {
    "a1": "R", "b1": "N", "c1": "B", "d1": "Q", "e1": "K", "f1": "B", "g1": "N", "h1": "R",
    "a2": "P", "b2": "P", "c2": "P", "d2": "P", "e2": "P", "f2": "P", "g2": "P", "h2": "P",
    "a7": "p", "b7": "p", "c7": "p", "d7": "p", "e7": "p", "f7": "p", "g7": "p", "h7": "p",
    "a8": "r", "b8": "n", "c8": "b", "d8": "q", "e8": "k", "f8": "b", "g8": "n", "h8": "r",
}

UNICODE_PIECES = {
    "K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘", "P": "♙",
    "k": "♚", "q": "♛", "r": "♜", "b": "♝", "n": "♞", "p": "♟",
}

PIECE_NAMES = {
    "P": "White Pawn", "N": "White Knight", "B": "White Bishop",
    "R": "White Rook", "Q": "White Queen",  "K": "White King",
    "p": "Black Pawn", "n": "Black Knight", "b": "Black Bishop",
    "r": "Black Rook", "q": "Black Queen",  "k": "Black King",
}

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_ui")

running = True
sse_clients: list[http.server.BaseHTTPRequestHandler] = []
sse_lock = threading.Lock()


def timestamp() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def log(level: str, message: str) -> None:
    print(f"[{timestamp()}] {level}: {message}", file=sys.stderr, flush=True)


def stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def list_ports() -> list[str]:
    return sorted(glob.glob("/dev/cu.*"))


def square_name(file_idx: int, rank_idx: int) -> str:
    return f"{chr(ord('a') + file_idx)}{rank_idx + 1}"


def parse_board(line: str) -> frozenset[str] | None:
    """Parse 'BOARD:r1/r2/.../r8' frame into a set of occupied square names."""
    if not line.startswith("BOARD:"):
        return None

    ranks = line.removeprefix("BOARD:").split("/")
    if len(ranks) != BOARD_RANKS or any(len(r) != BOARD_FILES for r in ranks):
        return None
    if any(ch not in "01" for r in ranks for ch in r):
        return None

    occupied = set()
    for rank_idx, rank_str in enumerate(ranks):
        for file_idx, ch in enumerate(rank_str):
            if ch == "1":
                occupied.add(square_name(file_idx, rank_idx))

    return frozenset(occupied)


def can_piece_capture(piece: str, src: str, dst: str, board: dict[str, str]) -> bool:
    """Check if piece at src can legally capture an enemy piece at dst."""
    target = board.get(dst)
    if not target or piece.isupper() == target.isupper():
        return False

    sf, sr = ord(src[0]) - ord('a'), int(src[1]) - 1
    df, dr = ord(dst[0]) - ord('a'), int(dst[1]) - 1
    df_diff, dr_diff = df - sf, dr - sr
    p = piece.upper()

    if p == 'P':
        direction = 1 if piece.isupper() else -1
        return dr_diff == direction and abs(df_diff) == 1
    elif p == 'N':
        return (abs(df_diff), abs(dr_diff)) in [(1, 2), (2, 1)]
    elif p == 'K':
        return max(abs(df_diff), abs(dr_diff)) == 1
    elif p == 'B':
        if abs(df_diff) != abs(dr_diff):
            return False
        step_f = 1 if df_diff > 0 else -1
        step_r = 1 if dr_diff > 0 else -1
        for step in range(1, abs(df_diff)):
            if chr(ord('a') + sf + step * step_f) + str(sr + 1 + step * step_r) in board:
                return False
        return True
    elif p == 'R':
        if df_diff != 0 and dr_diff != 0:
            return False
        step_f = (df_diff > 0) - (df_diff < 0)
        step_r = (dr_diff > 0) - (dr_diff < 0)
        dist = max(abs(df_diff), abs(dr_diff))
        for step in range(1, dist):
            if chr(ord('a') + sf + step * step_f) + str(sr + 1 + step * step_r) in board:
                return False
        return True
    elif p == 'Q':
        if abs(df_diff) == abs(dr_diff):
            step_f = 1 if df_diff > 0 else -1
            step_r = 1 if dr_diff > 0 else -1
            for step in range(1, abs(df_diff)):
                if chr(ord('a') + sf + step * step_f) + str(sr + 1 + step * step_r) in board:
                    return False
            return True
        elif df_diff == 0 or dr_diff == 0:
            step_f = (df_diff > 0) - (df_diff < 0)
            step_r = (dr_diff > 0) - (dr_diff < 0)
            dist = max(abs(df_diff), abs(dr_diff))
            for step in range(1, dist):
                if chr(ord('a') + sf + step * step_f) + str(sr + 1 + step * step_r) in board:
                    return False
            return True
        return False
    return False


class ChessGame:
    """Tracks physical chess piece state, move histories, FEN, and PGN."""

    def __init__(self, pgn_path: str = "game.pgn", fen_path: str = "live.fen") -> None:
        self.pgn_path = pgn_path
        self.fen_path = fen_path
        self.board: dict[str, str] = {}
        self.turn: str = "white"
        self.fullmove: int = 1
        self.halfmove_clock: int = 0
        self.castling: dict[str, bool] = {"K": True, "Q": True, "k": True, "q": True}
        self.en_passant: str | None = None
        self.captured_by_white: list[str] = []
        self.captured_by_black: list[str] = []
        self.history: list[dict] = []
        self.last_move: dict | None = None
        self.pending_lift: str | None = None
        self.pending_victim: str | None = None
        self.previous_sensors: frozenset[str] | None = None
        self.reset()

    def reset(self) -> None:
        self.board = dict(STARTING_PIECES)
        self.turn = "white"
        self.fullmove = 1
        self.halfmove_clock = 0
        self.castling = {"K": True, "Q": True, "k": True, "q": True}
        self.en_passant = None
        self.captured_by_white.clear()
        self.captured_by_black.clear()
        self.history.clear()
        self.last_move = None
        self.pending_lift = None
        self.pending_victim = None
        self.previous_sensors = None
        self.save_files()

    def generate_fen(self) -> str:
        rows = []
        for r in range(8, 0, -1):
            empty = 0
            row_str = ""
            for f in range(8):
                sq = chr(ord('a') + f) + str(r)
                if sq in self.board:
                    if empty > 0:
                        row_str += str(empty)
                        empty = 0
                    row_str += self.board[sq]
                else:
                    empty += 1
            if empty > 0:
                row_str += str(empty)
            rows.append(row_str)

        castling_str = "".join([k for k in ["K", "Q", "k", "q"] if self.castling.get(k)]) or "-"
        ep_str = self.en_passant or "-"
        turn_str = "w" if self.turn == "white" else "b"
        return f"{'/'.join(rows)} {turn_str} {castling_str} {ep_str} {self.halfmove_clock} {self.fullmove}"

    def generate_pgn(self) -> str:
        lines = [
            '[Event "Physical Chessboard Local Analysis"]',
            f'[Date "{dt.date.today().strftime("%Y.%m.%d")}"]',
            '[Round "1"]',
            '[White "Player"]',
            '[Black "Stockfish Analysis"]',
            '[Result "*"]',
            "",
        ]
        move_entries = []
        for h in self.history:
            num = h["fullmove"]
            if h["turn"] == "white":
                move_entries.append(f"{num}. {h['san']}")
            else:
                if not move_entries or not move_entries[-1].startswith(f"{num}."):
                    move_entries.append(f"{num}... {h['san']}")
                else:
                    move_entries[-1] += f" {h['san']}"
        lines.append(" ".join(move_entries))
        lines.append("*")
        return "\n".join(lines)

    def save_files(self) -> None:
        try:
            with open(self.fen_path, "w", encoding="utf-8") as f:
                f.write(self.generate_fen() + "\n")
            with open(self.pgn_path, "w", encoding="utf-8") as f:
                f.write(self.generate_pgn() + "\n")
        except OSError as err:
            log("WARNING", f"Cannot write PGN/FEN files: {err}")

    def execute_move(self, src: str, dst: str, victim_sq: str | None = None) -> tuple[str, str]:
        piece = self.board.get(src, "P" if self.turn == "white" else "p")
        captured = None

        # Check castling
        san = ""
        is_castling = False
        if piece == "K" and src == "e1":
            if dst == "g1":
                san = "O-O"
                self.board["f1"] = self.board.pop("h1", "R")
                is_castling = True
            elif dst == "c1":
                san = "O-O-O"
                self.board["d1"] = self.board.pop("a1", "R")
                is_castling = True
            self.castling["K"] = False
            self.castling["Q"] = False
        elif piece == "k" and src == "e8":
            if dst == "g8":
                san = "O-O"
                self.board["f8"] = self.board.pop("h8", "r")
                is_castling = True
            elif dst == "c8":
                san = "O-O-O"
                self.board["d8"] = self.board.pop("a8", "r")
                is_castling = True
            self.castling["k"] = False
            self.castling["q"] = False

        if not is_castling:
            target_sq = victim_sq or dst
            if target_sq in self.board and target_sq != src:
                captured = self.board.pop(target_sq)
                if captured.islower():
                    self.captured_by_white.append(captured)
                else:
                    self.captured_by_black.append(captured)

            promoted = False
            if piece == "P" and dst[1] == "8":
                piece = "Q"
                promoted = True
            elif piece == "p" and dst[1] == "1":
                piece = "q"
                promoted = True

            if piece.upper() == "P":
                if captured or (src[0] != dst[0]):
                    san = f"{src[0]}x{dst}"
                else:
                    san = dst
                if promoted:
                    san += "=Q"
            else:
                p_char = piece.upper()
                x_char = "x" if captured else ""
                san = f"{p_char}{x_char}{dst}"

        if src in self.board:
            del self.board[src]
        self.board[dst] = piece

        if src == "a1" or dst == "a1": self.castling["Q"] = False
        if src == "h1" or dst == "h1": self.castling["K"] = False
        if src == "a8" or dst == "a8": self.castling["q"] = False
        if src == "h8" or dst == "h8": self.castling["k"] = False

        if piece.upper() == "P" and abs(int(dst[1]) - int(src[1])) == 2:
            mid_rank = (int(dst[1]) + int(src[1])) // 2
            self.en_passant = f"{src[0]}{mid_rank}"
        else:
            self.en_passant = None

        p_name = PIECE_NAMES.get(piece, piece)
        desc = f"{p_name}: {src} -> {dst}"
        if captured:
            cap_name = PIECE_NAMES.get(captured, captured)
            desc += f" (captured {cap_name})"

        uci_move = f"{src}{dst}"
        move_info = {
            "fullmove": self.fullmove,
            "turn": self.turn,
            "san": san,
            "uci": uci_move,
            "from": src,
            "to": dst,
            "piece": piece,
            "captured": captured,
            "desc": desc,
            "fen": self.generate_fen(),
        }
        self.history.append(move_info)
        self.last_move = move_info

        if self.turn == "white":
            self.turn = "black"
        else:
            self.turn = "white"
            self.fullmove += 1

        self.pending_lift = None
        self.pending_victim = None
        self.save_files()
        return uci_move, f"{san} ({desc})"

    def update_sensor_state(self, current: frozenset[str]) -> tuple[str | None, str]:
        # Reset detection if all 32 starting pieces are in place
        if current == frozenset(STARTING_PIECES.keys()):
            self.reset()
            self.previous_sensors = current
            return None, "board reset to standard starting position"

        if self.previous_sensors is None or (not self.board and current):
            self.previous_sensors = current
            # Partial board support: populate only squares where magnets are present
            if current != frozenset(STARTING_PIECES.keys()):
                self.board.clear()
                for sq in current:
                    if sq in STARTING_PIECES:
                        self.board[sq] = STARTING_PIECES[sq]
                    else:
                        rank = int(sq[1])
                        self.board[sq] = "P" if rank <= 4 else "p"
                self.save_files()
                return None, f"partial board initialized with {len(current)} pieces"
            return None, f"initial baseline recorded ({len(current)} pieces on board)"

        removed = self.previous_sensors - current
        added = current - self.previous_sensors
        self.previous_sensors = current

        # 1. Castling (both moved in one scan)
        if removed == {"e1", "h1"} and added == {"g1", "f1"}:
            return self.execute_move("e1", "g1")
        if removed == {"e1", "a1"} and added == {"c1", "d1"}:
            return self.execute_move("e1", "c1")
        if removed == {"e8", "h8"} and added == {"g8", "f8"}:
            return self.execute_move("e8", "g8")
        if removed == {"e8", "a8"} and added == {"c8", "d8"}:
            return self.execute_move("e8", "c8")

        # 2. Standard single-step move
        if len(removed) == 1 and len(added) == 1:
            src = next(iter(removed))
            dst = next(iter(added))
            victim = self.pending_victim
            return self.execute_move(src, dst, victim_sq=victim)

        # 3. Piece lifted from board
        if len(removed) == 1 and not added:
            lifted = next(iter(removed))
            if lifted in self.board:
                p = self.board[lifted]
                possible_captures = [
                    sq for sq in self.board
                    if can_piece_capture(p, lifted, sq, self.board)
                ]
                if len(possible_captures) == 1:
                    target = possible_captures[0]
                    return self.execute_move(lifted, target)

            self.pending_lift = lifted
            p_name = PIECE_NAMES.get(self.board.get(lifted, ""), "Piece")
            return None, f"{p_name} lifted from {lifted}; waiting for placement"

        # 4. Piece placed on board
        if not removed and len(added) == 1:
            placed = next(iter(added))
            if self.pending_lift is not None:
                src = self.pending_lift
                victim = self.pending_victim
                return self.execute_move(src, placed, victim_sq=victim)
            return None, f"piece placed at {placed} without registered lift"

        # 5. Two pieces lifted (e.g. capture in progress)
        if len(removed) == 2 and not added:
            rem_list = sorted(removed)
            self.pending_victim = rem_list[0]
            self.pending_lift = rem_list[1]
            return None, f"two pieces lifted: {rem_list[0]} and {rem_list[1]}"

        if not removed and not added:
            return None, "board state unchanged"

        rem_str = ",".join(sorted(removed)) or "none"
        add_str = ",".join(sorted(added)) or "none"
        self.pending_lift = None
        self.pending_victim = None
        return None, f"multiple squares changed: removed=[{rem_str}], added=[{add_str}]"

    def render_terminal(self) -> str:
        """Render board with actual Unicode chess piece glyphs."""
        lines = ["    +---------------------------------+"]
        for rank_idx in range(BOARD_RANKS - 1, -1, -1):
            rank_label = str(rank_idx + 1)
            row_items = []
            for file_idx in range(BOARD_FILES):
                sq = square_name(file_idx, rank_idx)
                piece = self.board.get(sq)
                glyph = UNICODE_PIECES.get(piece, "·") if piece else "·"
                row_items.append(f" {glyph} ")
            lines.append(f"  {rank_label} |{' '.join(row_items)}|")
        lines.append("    +---------------------------------+")
        lines.append("       a   b   c   d   e   f   g   h")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "fen": self.generate_fen(),
            "turn": self.turn,
            "fullmove": self.fullmove,
            "board": self.board,
            "captured_white": [UNICODE_PIECES.get(p, p) for p in self.captured_by_white],
            "captured_black": [UNICODE_PIECES.get(p, p) for p in self.captured_by_black],
            "last_move": self.last_move,
            "history": self.history,
            "pending_lift": self.pending_lift,
        }


active_game: ChessGame | None = None


def broadcast_sse(data: dict) -> None:
    """Broadcast state to all connected browser SSE listeners in real time."""
    payload = f"data: {json.dumps(data)}\n\n".encode("utf-8")
    with sse_lock:
        removals = []
        for client in sse_clients:
            try:
                client.wfile.write(payload)
                client.wfile.flush()
            except Exception:
                removals.append(client)
        for c in removals:
            if c in sse_clients:
                sse_clients.remove(c)


class WorkbenchHTTPHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        global active_game
        # 1. Real-time Server-Sent Events stream
        if self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
            self.end_headers()

            # Immediate initial sync on connect
            init_payload = active_game.to_dict() if active_game else {}
            try:
                self.wfile.write(f"data: {json.dumps(init_payload)}\n\n".encode("utf-8"))
                self.wfile.flush()
            except Exception:
                return

            with sse_lock:
                sse_clients.append(self)

            try:
                while running:
                    time.sleep(1)
            except Exception:
                pass
            finally:
                with sse_lock:
                    if self in sse_clients:
                        sse_clients.remove(self)
            return

        # 2. JSON State endpoints
        if self.path.startswith("/api/state"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            state = active_game.to_dict() if active_game else {}
            self.wfile.write(json.dumps(state).encode("utf-8"))
            return
        elif self.path.startswith("/api/pgn"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            pgn = active_game.generate_pgn() if active_game else ""
            self.wfile.write(pgn.encode("utf-8"))
            return
        elif self.path.startswith("/api/fen"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            fen = active_game.generate_fen() if active_game else ""
            self.wfile.write(fen.encode("utf-8"))
            return

        # 3. Static local file serving from web_ui directory
        req_path = self.path.split("?")[0].lstrip("/")
        if req_path in ("", "index.html"):
            target_file = os.path.join(WEB_DIR, "index.html")
        else:
            clean_path = os.path.normpath(req_path)
            target_file = os.path.join(WEB_DIR, clean_path)

        if not os.path.isfile(target_file) or not target_file.startswith(WEB_DIR):
            self.send_response(404)
            self.end_headers()
            return

        mime_type, _ = mimetypes.guess_type(target_file)
        if target_file.endswith(".js"):
            mime_type = "application/javascript"
        elif target_file.endswith(".css"):
            mime_type = "text/css"
        elif target_file.endswith(".wasm"):
            mime_type = "application/wasm"
        elif not mime_type:
            mime_type = "application/octet-stream"

        try:
            with open(target_file, "rb") as f:
                content = f.read()

            self.send_response(200)
            self.send_header("Content-Type", f"{mime_type}; charset=utf-8" if "text" in mime_type else mime_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        except OSError:
            self.send_response(500)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


def start_workbench_server(port: int) -> http.server.ThreadingHTTPServer:
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    server = http.server.ThreadingHTTPServer(("", port), WorkbenchHTTPHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def configure_uart(fd: int, baud: int) -> None:
    settings = termios.tcgetattr(fd)
    settings[0] = termios.IGNPAR
    settings[1] = 0
    settings[2] &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB)
    settings[2] |= termios.CS8 | termios.CLOCAL | termios.CREAD
    if hasattr(termios, "CRTSCTS"):
        settings[2] &= ~termios.CRTSCTS
    settings[3] = 0
    settings[4] = BAUD_RATES[baud]
    settings[5] = BAUD_RATES[baud]
    settings[6][termios.VMIN] = 0
    settings[6][termios.VTIME] = 0

    termios.tcsetattr(fd, termios.TCSANOW, settings)
    termios.tcflush(fd, termios.TCIFLUSH)


def open_uart(device: str, baud: int) -> int:
    fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        if hasattr(termios, "TIOCEXCL"):
            fcntl.ioctl(fd, termios.TIOCEXCL)
        configure_uart(fd, baud)
    except Exception:
        os.close(fd)
        raise
    return fd


def process_uart_line(line: str, game: ChessGame) -> None:
    if not line.startswith("BOARD:"):
        if line:
            log("DEVICE", line)
        return

    board_sensors = parse_board(line)
    if board_sensors is None:
        log("WARNING", f"invalid board frame ignored: {line!r}")
        return

    uci_move, detail = game.update_sensor_state(board_sensors)

    # Render board visually in terminal
    terminal_board = game.render_terminal()
    print(f"\n{terminal_board}\n", file=sys.stderr, flush=True)

    log("STATE", detail)
    log("FEN", game.generate_fen())

    # Real-time push to browser workbench via SSE
    broadcast_sse(game.to_dict())

    if uci_move is not None:
        print(uci_move, flush=True)


def emit_complete_lines(buffer: bytearray, game: ChessGame) -> None:
    while True:
        newline = buffer.find(b"\n")
        if newline < 0:
            return

        raw_line = bytes(buffer[:newline]).rstrip(b"\r")
        del buffer[: newline + 1]
        process_uart_line(raw_line.decode("utf-8", errors="replace"), game)


def run_simulation_mode(port: int, delay: float = 0.0, exit_on_eof: bool = False) -> int:
    """Interactive software simulator: listens on STDIN for BOARD frames or UCI moves."""
    global active_game
    active_game = ChessGame()

    server = start_workbench_server(port)
    log("WORKBENCH", f"Local Chess Analysis Workbench active at http://localhost:{port}")
    is_interactive = sys.stdin.isatty()

    if is_interactive:
        log("SIMULATOR", "Interactive simulation mode running. Listening for input on STDIN.")
        log("SIMULATOR", "Commands: e2e4, e7e5, BOARD:... frames, 'row1', 'reset', 'quit', 'help'")
    else:
        log("SIMULATOR", "Piped simulation mode active. Reading frames/moves from STDIN...")

    # Send initial starting board frame
    start_frame = "BOARD:11111111/11111111/00000000/00000000/00000000/00000000/11111111/11111111"
    process_uart_line(start_frame, active_game)
    if is_interactive:
        print("\n[Simulator Ready] Enter command or frame > ", end="", flush=True)

    while running:
        try:
            readable, _, _ = select.select([sys.stdin], [], [], 0.5)
            if readable:
                line = sys.stdin.readline()
                if not line:  # EOF reached (e.g. piped input finished)
                    if exit_on_eof:
                        log("SIMULATOR", "STDIN closed (EOF). Exiting simulation mode.")
                        break
                    else:
                        log("SIMULATOR", f"All piped input processed. Web server staying alive at http://localhost:{port}")
                        log("SIMULATOR", f"Open http://localhost:{port} in your browser. Press Ctrl+C in terminal to stop.")
                        while running:
                            time.sleep(0.5)
                        break

                line = line.strip()
                if not line:
                    if is_interactive:
                        print("[Simulator] > ", end="", flush=True)
                    continue

                if line in ("quit", "exit", "q"):
                    break
                elif line in ("help", "?"):
                    print("\n--- Simulator Help ---")
                    print("  BOARD:r1/r2/.../r8  - Raw 64-reed switch binary frame from ATmega")
                    print("  e2e4, e7e5, etc.    - Play move coordinates directly")
                    print("  start, reset        - Reset to standard 32-piece starting setup")
                    print("  row1                - Test partial setup with only row 1 (a1..h1)")
                    print("  fen                 - Print current FEN string")
                    print("  quit                - Exit simulator\n")
                elif line in ("start", "reset"):
                    process_uart_line(start_frame, active_game)
                elif line == "row1":
                    row1_frame = "BOARD:11111111/00000000/00000000/00000000/00000000/00000000/00000000/00000000"
                    active_game.reset()
                    process_uart_line(row1_frame, active_game)
                elif line == "fen":
                    print(f"Current FEN: {active_game.generate_fen()}")
                elif line.startswith("BOARD:"):
                    process_uart_line(line, active_game)
                else:
                    uci = line.lower().replace(" ", "").replace("-", "")
                    if len(uci) >= 4:
                        src, dst = uci[:2], uci[2:4]
                        if src in active_game.board:
                            move, desc = active_game.execute_move(src, dst)
                            print(f"\n{active_game.render_terminal()}\n", file=sys.stderr)
                            log("STATE", desc)
                            log("FEN", active_game.generate_fen())
                            broadcast_sse(active_game.to_dict())
                        else:
                            log("WARNING", f"No piece at source square {src}")
                    else:
                        log("WARNING", f"Unrecognized input: {line!r}. Type 'help' for options.")

                if delay > 0:
                    time.sleep(delay)

                if is_interactive:
                    print("[Simulator] > ", end="", flush=True)
        except (KeyboardInterrupt, EOFError):
            break

    server.shutdown()
    log("STOPPED", "Simulator closed.")
    return 0


def monitor(device: str, baud: int, heartbeat: float, reconnect_delay: float, port: int) -> int:
    global active_game
    active_game = ChessGame()

    server = start_workbench_server(port)
    log("WORKBENCH", f"Local Chess Analysis Workbench active at http://localhost:{port}")

    fd: int | None = None
    receive_buffer = bytearray()
    connected_at = 0.0
    last_heartbeat = 0.0
    total_bytes = 0
    total_frames = 0
    last_data_at: float | None = None
    waiting_logged = False

    while running:
        if fd is None:
            try:
                fd = open_uart(device, baud)
            except OSError as error:
                if not waiting_logged:
                    reason = error.strerror or str(error)
                    log("WAIT", f"cannot open {device}: {reason}; retrying")
                    waiting_logged = True
                time.sleep(reconnect_delay)
                continue

            connected_at = time.monotonic()
            last_heartbeat = connected_at
            total_bytes = 0
            total_frames = 0
            last_data_at = None
            receive_buffer.clear()
            waiting_logged = False
            log("CONNECTED", f"opened {device} at {baud} baud, 8N1")

        now = time.monotonic()
        timeout = max(0.0, min(1.0, heartbeat - (now - last_heartbeat)))

        try:
            readable, _, _ = select.select([fd], [], [], timeout)
            if readable:
                chunk = os.read(fd, 4096)
                if not chunk:
                    raise OSError(errno.ENODEV, "serial device returned end-of-file")

                if last_data_at is None:
                    log("LINK", "first UART data received; physical board connected")
                last_data_at = time.monotonic()
                total_bytes += len(chunk)
                before = receive_buffer.count(b"\n")
                receive_buffer.extend(chunk)
                after = receive_buffer.count(b"\n")
                total_frames += after - before
                emit_complete_lines(receive_buffer, active_game)

            now = time.monotonic()
            if now - last_heartbeat >= heartbeat:
                uptime = int(now - connected_at)
                activity = "no data yet" if last_data_at is None else f"last data {int(now - last_data_at)}s ago"
                log("HEARTBEAT", f"port open for {uptime}s; {activity}; bytes={total_bytes}")
                last_heartbeat = now

        except OSError as error:
            reason = error.strerror or str(error)
            log("DISCONNECTED", f"{device}: {reason}")
            os.close(fd)
            fd = None
            time.sleep(reconnect_delay)

    if fd is not None:
        os.close(fd)
    server.shutdown()
    log("STOPPED", "workbench closed")
    return 0


def run_self_test() -> int:
    """Simulate UART frames to verify piece tracking, moves, partial setups, and workbench serving."""
    global active_game
    print("=== Running Local Workbench Self-Test ===", file=sys.stderr)
    game = ChessGame()
    active_game = game

    server = start_workbench_server(8089)
    print("Workbench server listening on http://localhost:8089", file=sys.stderr)

    # Frame 1: Standard starting setup
    frame1 = "BOARD:11111111/11111111/00000000/00000000/00000000/00000000/11111111/11111111"
    process_uart_line(frame1, game)
    assert len(game.board) == 32

    # Frame 2: 1. e4 (White Pawn e2 -> e4)
    frame2 = "BOARD:11111111/11110111/00000000/00001000/00000000/00000000/11111111/11111111"
    process_uart_line(frame2, game)
    assert game.board["e4"] == "P"

    # Frame 3: 1... e5 (lift e7, place e5)
    frame3a = "BOARD:11111111/11110111/00000000/00001000/00000000/00000000/11110111/11111111"
    process_uart_line(frame3a, game)
    assert game.pending_lift == "e7"

    frame3b = "BOARD:11111111/11110111/00000000/00001000/00001000/00000000/11110111/11111111"
    process_uart_line(frame3b, game)
    assert game.board["e5"] == "p"

    # Frame 4: 2. Nf3
    frame4 = "BOARD:11111101/11110111/00000100/00001000/00001000/00000000/11110111/11111111"
    process_uart_line(frame4, game)
    assert game.board["f3"] == "N"

    # Frame 5: Partial board reset (only bottom row a1..h1)
    partial_frame = "BOARD:11111111/00000000/00000000/00000000/00000000/00000000/00000000/00000000"
    game.reset()
    process_uart_line(partial_frame, game)
    assert len(game.board) == 8
    assert game.board["e1"] == "K"
    assert "e2" not in game.board

    server.shutdown()
    print("=== Self-Test Passed Successfully ===", file=sys.stderr)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ATmega physical chessboard local analysis workbench with Chessground & Stockfish WASM."
    )
    parser.add_argument("device", nargs="?", help="serial device, e.g. /dev/cu.usbserial-110, or '-' for stdin")
    parser.add_argument("--baud", type=int, default=9600, choices=sorted(BAUD_RATES))
    parser.add_argument("--port", type=int, default=8080, help="workbench port (default: 8080)")
    parser.add_argument("--heartbeat", type=float, default=10.0, help="heartbeat interval (seconds)")
    parser.add_argument("--reconnect-delay", type=float, default=2.0, help="reconnect delay (seconds)")
    parser.add_argument("--simulate", action="store_true", help="run software simulator listening on stdin")
    parser.add_argument("--delay", type=float, default=0.0, help="seconds to pause between piped moves (default: 0)")
    parser.add_argument("--exit-on-eof", action="store_true", help="exit immediately when stdin closes instead of keeping server alive")
    parser.add_argument("--list", action="store_true", help="list macOS serial devices and exit")
    parser.add_argument("--self-test", action="store_true", help="run simulation test and exit")
    args = parser.parse_args()

    if args.self_test or args.simulate or args.device == "-":
        return args

    if not args.list and not args.device:
        parser.error("provide a serial device, use --simulate, or use --list")
    return args


def main() -> int:
    args = parse_args()
    if args.self_test:
        return run_self_test()

    if args.simulate or args.device == "-":
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        return run_simulation_mode(args.port, delay=args.delay, exit_on_eof=args.exit_on_eof)

    if args.list:
        ports = list_ports()
        if not ports:
            log("INFO", "no /dev/cu.* devices found")
            return 1
        for port in ports:
            print(port)
        return 0

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    return monitor(args.device, args.baud, args.heartbeat, args.reconnect_delay, args.port)


if __name__ == "__main__":
    raise SystemExit(main())

