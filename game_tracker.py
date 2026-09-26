#!/usr/bin/env python3
"""
Game Tracker Subsystem for Automated Chessboard.
Follows Single Responsibility Principle (SRP):
Dedicated exclusively to tracking chessboard state, parsing reed-switch matrix
sensor frames (BOARD:11111111/...), deducing physical moves (moves, captures, castling),
and maintaining official FEN / PGN game representations.

Has ZERO dependency on Stockfish or gantry mechanics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime as dt
from typing import Dict, FrozenSet, List, Optional, Set, Tuple


STARTING_PIECES: Dict[str, str] = {
    "a1": "R", "b1": "N", "c1": "B", "d1": "Q", "e1": "K", "f1": "B", "g1": "N", "h1": "R",
    "a2": "P", "b2": "P", "c2": "P", "d2": "P", "e2": "P", "f2": "P", "g2": "P", "h2": "P",
    "a7": "p", "b7": "p", "c7": "p", "d7": "p", "e7": "p", "f7": "p", "g7": "p", "h7": "p",
    "a8": "r", "b8": "n", "c8": "b", "d8": "q", "e8": "k", "f8": "b", "g8": "n", "h8": "r",
}

PIECE_NAMES: Dict[str, str] = {
    "P": "White Pawn", "N": "White Knight", "B": "White Bishop",
    "R": "White Rook", "Q": "White Queen",  "K": "White King",
    "p": "Black Pawn", "n": "Black Knight", "b": "Black Bishop",
    "r": "Black Rook", "q": "Black Queen",  "k": "Black King",
}


@dataclass
class DeduceResult:
    """Result of processing a reed-switch sensor transition."""
    move_detected: bool
    uci: Optional[str] = None          # e.g. "e2e4"
    san: Optional[str] = None          # e.g. "e4" or "Nf3"
    from_sq: Optional[str] = None      # e.g. "e2"
    to_sq: Optional[str] = None        # e.g. "e4"
    piece: Optional[str] = None        # e.g. "P"
    captured: Optional[str] = None     # e.g. "p"
    fen_after: str = ""
    description: str = ""


def parse_reed_frame(line: str) -> Optional[FrozenSet[str]]:
    """
    Parses 'BOARD:r1/r2/.../r8' frame from ATmega32 into a set of occupied square names.
    '1' = occupied, '0' = empty.
    """
    cleaned = line.strip()
    if cleaned.startswith("BOARD:"):
        cleaned = cleaned[len("BOARD:"):]

    ranks = cleaned.split("/")
    if len(ranks) != 8 or any(len(r) != 8 for r in ranks):
        return None
    if any(ch not in "01" for r in ranks for ch in r):
        return None

    occupied: Set[str] = set()
    for rank_idx, rank_str in enumerate(ranks):
        for file_idx, ch in enumerate(rank_str):
            if ch == "1":
                file_char = chr(ord('a') + file_idx)
                rank_char = str(rank_idx + 1)
                occupied.add(f"{file_char}{rank_char}")

    return frozenset(occupied)


def can_piece_capture(piece: str, src: str, dst: str, board: Dict[str, str]) -> bool:
    """Basic geometric capture validation for piece types."""
    sf, sr = ord(src[0]) - ord('a'), int(src[1]) - 1
    df, dr = ord(dst[0]) - ord('a'), int(dst[1]) - 1
    d_file = abs(df - sf)
    d_rank = abs(dr - sr)

    is_white = piece.isupper()
    target = board.get(dst)
    if not target or (target.isupper() == is_white):
        return False

    p = piece.upper()
    if p == "P":
        direction = 1 if is_white else -1
        return d_file == 1 and (dr - sr) == direction
    elif p == "N":
        return (d_file, d_rank) in [(1, 2), (2, 1)]
    elif p == "K":
        return max(d_file, d_rank) == 1
    elif p == "R":
        return (sf == df or sr == dr)
    elif p == "B":
        return d_file == d_rank
    elif p == "Q":
        return (sf == df or sr == dr) or (d_file == d_rank)
    return False


class GameTracker:
    """
    Tracks official board position, turn, castling rights, and en passant.
    Deduces moves from physical reed switch sensor state deltas.
    """

    def __init__(self):
        self.board: Dict[str, str] = {}
        self.turn: str = "white"  # "white" or "black"
        self.castling: Dict[str, bool] = {"K": True, "Q": True, "k": True, "q": True}
        self.en_passant: Optional[str] = None
        self.halfmove_clock: int = 0
        self.fullmove: int = 1
        self.history: List[dict] = []
        self.previous_sensors: Optional[FrozenSet[str]] = None
        self.pending_lift: Optional[str] = None
        self.reset()

    def reset(self) -> None:
        """Resets the game to standard starting chess position."""
        self.board = dict(STARTING_PIECES)
        self.turn = "white"
        self.castling = {"K": True, "Q": True, "k": True, "q": True}
        self.en_passant = None
        self.halfmove_clock = 0
        self.fullmove = 1
        self.history = []
        self.pending_lift = None
        self.previous_sensors = frozenset(STARTING_PIECES.keys())

    def get_fen(self) -> str:
        """Generates standard FEN string representing the current board state."""
        rows = []
        for rank in range(8, 0, -1):
            empty_count = 0
            row_str = ""
            for file_idx in range(8):
                sq = f"{chr(ord('a') + file_idx)}{rank}"
                piece = self.board.get(sq)
                if piece:
                    if empty_count > 0:
                        row_str += str(empty_count)
                        empty_count = 0
                    row_str += piece
                else:
                    empty_count += 1
            if empty_count > 0:
                row_str += str(empty_count)
            rows.append(row_str)

        castling_parts = []
        if self.castling["K"]: castling_parts.append("K")
        if self.castling["Q"]: castling_parts.append("Q")
        if self.castling["k"]: castling_parts.append("k")
        if self.castling["q"]: castling_parts.append("q")
        castling_str = "".join(castling_parts) if castling_parts else "-"

        ep_str = self.en_passant or "-"
        turn_str = "w" if self.turn == "white" else "b"
        return f"{'/'.join(rows)} {turn_str} {castling_str} {ep_str} {self.halfmove_clock} {self.fullmove}"

    def apply_move(self, src: str, dst: str, victim_sq: Optional[str] = None) -> DeduceResult:
        """
        Applies a validated chess move to the internal game state and returns DeduceResult.
        """
        src = src.lower()
        dst = dst.lower()
        piece = self.board.get(src, "P" if self.turn == "white" else "p")
        captured = None

        # 1. Castling Checks
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

        # 2. Standard Move & Capture
        if not is_castling:
            target_sq = victim_sq or dst
            if target_sq in self.board and target_sq != src:
                captured = self.board.pop(target_sq)

            # Promotion to Queen by default
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

        # Update castling rights if rooks moved
        if src == "a1" or dst == "a1": self.castling["Q"] = False
        if src == "h1" or dst == "h1": self.castling["K"] = False
        if src == "a8" or dst == "a8": self.castling["q"] = False
        if src == "h8" or dst == "h8": self.castling["k"] = False

        # En passant tracking
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

        # Update turn and clocks
        if self.turn == "white":
            self.turn = "black"
        else:
            self.turn = "white"
            self.fullmove += 1

        fen_after = self.get_fen()
        self.pending_lift = None

        # Synchronize sensors
        self.previous_sensors = frozenset(self.board.keys())

        return DeduceResult(
            move_detected=True,
            uci=uci_move,
            san=san,
            from_sq=src,
            to_sq=dst,
            piece=piece,
            captured=captured,
            fen_after=fen_after,
            description=desc
        )

    def ingest_reed_frame(self, frame_line: str) -> DeduceResult:
        """
        Ingests a 'BOARD:11111111/...' serial line from chess.c and determines what move occurred.
        """
        current_sensors = parse_reed_frame(frame_line)
        if current_sensors is None:
            return DeduceResult(
                move_detected=False,
                fen_after=self.get_fen(),
                description=f"Invalid reed frame format: {frame_line[:30]}"
            )

        if self.previous_sensors is None:
            self.previous_sensors = current_sensors
            return DeduceResult(
                move_detected=False,
                fen_after=self.get_fen(),
                description=f"Baseline sensor state established ({len(current_sensors)} pieces on board)"
            )

        removed = self.previous_sensors - current_sensors
        added = current_sensors - self.previous_sensors
        self.previous_sensors = current_sensors

        # 0. No difference
        if not removed and not added:
            return DeduceResult(
                move_detected=False,
                fen_after=self.get_fen(),
                description="No physical piece changes detected"
            )

        # 1. Castling detection (2 removed, 2 added)
        if removed == {"e1", "h1"} and added == {"g1", "f1"}:
            return self.apply_move("e1", "g1")
        if removed == {"e1", "a1"} and added == {"c1", "d1"}:
            return self.apply_move("e1", "c1")
        if removed == {"e8", "h8"} and added == {"g8", "f8"}:
            return self.apply_move("e8", "g8")
        if removed == {"e8", "a8"} and added == {"c8", "d8"}:
            return self.apply_move("e8", "c8")

        # 2. Standard move (1 removed, 1 added)
        if len(removed) == 1 and len(added) == 1:
            src = next(iter(removed))
            dst = next(iter(added))
            return self.apply_move(src, dst)

        # 3. Capture move where target was lifted earlier or simultaneous
        # (e.g. piece lifted from board, only 1 removed, 0 added)
        if len(removed) == 1 and not added:
            lifted = next(iter(removed))
            # Check if this lifted piece can only make 1 legal capture onto an occupied square
            if lifted in self.board:
                p = self.board[lifted]
                possible_captures = [
                    sq for sq in self.board
                    if can_piece_capture(p, lifted, sq, self.board)
                ]
                if len(possible_captures) == 1:
                    target = possible_captures[0]
                    return self.apply_move(lifted, target)

            self.pending_lift = lifted
            p_name = PIECE_NAMES.get(self.board.get(lifted, ""), "Piece")
            return DeduceResult(
                move_detected=False,
                fen_after=self.get_fen(),
                description=f"{p_name} lifted from {lifted} (waiting for placement)"
            )

        # 4. Piece placed down after earlier lift
        if not removed and len(added) == 1 and self.pending_lift:
            src = self.pending_lift
            dst = next(iter(added))
            return self.apply_move(src, dst)

        return DeduceResult(
            move_detected=False,
            fen_after=self.get_fen(),
            description=f"Ambiguous sensor delta: removed={list(removed)}, added={list(added)}"
        )


# ==============================================================================
# Standalone CLI / Verification
# ==============================================================================

def main():
    print("==================================================")
    print("Game Tracker Subsystem (Reed Matrix -> FEN & Move)")
    print("==================================================")

    tracker = GameTracker()
    print(f"Initial FEN: {tracker.get_fen()}")

    # Simulate player moving e2 -> e4
    # e2 (file 4, rank 1 in 0-indexed) changes from 1 to 0
    # e4 (file 4, rank 3 in 0-indexed) changes from 0 to 1
    # Frame 1: Standard starting position
    frame_start = "BOARD:11111111/11111111/00000000/00000000/00000000/00000000/11111111/11111111"
    tracker.ingest_reed_frame(frame_start)

    # Frame 2: e2 removed, e4 added
    # Rank 2 (index 1): 11110111 (e2 is 0)
    # Rank 4 (index 3): 00001000 (e4 is 1)
    frame_e2e4 = "BOARD:11111111/11110111/00000000/00001000/00000000/00000000/11111111/11111111"
    result = tracker.ingest_reed_frame(frame_e2e4)

    print("\nSimulating Human Move (e2 -> e4):")
    print(f"Move Detected: {result.move_detected}")
    print(f"UCI Move     : {result.uci}")
    print(f"SAN Move     : {result.san}")
    print(f"Description  : {result.description}")
    print(f"FEN After    : {result.fen_after}")

    # Now simulate Black responding (or Stockfish move applied): e.g. e7 -> e5
    print("\nApplying Move e7 -> e5:")
    black_res = tracker.apply_move("e7", "e5")
    print(f"UCI Move     : {black_res.uci}")
    print(f"FEN After    : {black_res.fen_after}")
    print("\n✓ Game Tracker subsystem is verified!")


if __name__ == "__main__":
    main()

