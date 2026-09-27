#!/usr/bin/env python3
"""
Game Tracker Subsystem for Automated Chessboard.
Tracks board state via python-chess, deduces physical moves from
reed-switch sensor deltas, validates legality, and provides FEN.

Has ZERO dependency on Stockfish or gantry mechanics.
"""

from __future__ import annotations

import chess
from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional, Set

from serial_connection import parse_board_frame


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
    uci: Optional[str] = None
    san: Optional[str] = None
    from_sq: Optional[str] = None
    to_sq: Optional[str] = None
    piece: Optional[str] = None
    captured: Optional[str] = None
    fen_after: str = ""
    description: str = ""


def parse_reed_frame(line: str) -> Optional[FrozenSet[str]]:
    """Parses 'BOARD:r1/r2/.../r8' frame. Delegates to shared parse_board_frame."""
    return parse_board_frame(line)


class GameTracker:
    """
    Tracks official board state via python-chess.
    Deduces moves from physical reed switch sensor deltas and validates legality.
    """

    def __init__(self):
        self._board = chess.Board()
        self.previous_sensors: Optional[FrozenSet[str]] = None
        self.pending_lift: Optional[str] = None

    def reset(self) -> None:
        self._board = chess.Board()
        self.previous_sensors = None
        self.pending_lift = None

    @property
    def board(self) -> Dict[str, str]:
        """Dict view of the board for move planner compatibility (sq -> piece char)."""
        result: Dict[str, str] = {}
        for sq_idx in chess.SQUARES:
            piece = self._board.piece_at(sq_idx)
            if piece:
                result[chess.square_name(sq_idx)] = piece.symbol()
        return result

    @property
    def active_color(self) -> str:
        return "white" if self._board.turn == chess.WHITE else "black"

    def get_fen(self) -> str:
        return self._board.fen()

    def is_game_over(self) -> bool:
        return self._board.is_game_over()

    def game_result(self) -> Optional[str]:
        """Returns result string if game is over, else None."""
        if self._board.is_checkmate():
            winner = "Black" if self._board.turn == chess.WHITE else "White"
            return f"Checkmate! {winner} wins."
        if self._board.is_stalemate():
            return "Draw by stalemate."
        if self._board.is_insufficient_material():
            return "Draw by insufficient material."
        if self._board.can_claim_fifty_moves():
            return "Draw claimable (50-move rule)."
        if self._board.is_fivefold_repetition():
            return "Draw by fivefold repetition."
        return None

    def _find_legal_move(self, src: str, dst: str) -> Optional[chess.Move]:
        """Finds a legal move from src to dst, handling promotion automatically."""
        from_sq = chess.parse_square(src)
        to_sq = chess.parse_square(dst)

        # Try promotion to queen first (most common)
        promo_move = chess.Move(from_sq, to_sq, promotion=chess.QUEEN)
        if promo_move in self._board.legal_moves:
            return promo_move

        normal_move = chess.Move(from_sq, to_sq)
        if normal_move in self._board.legal_moves:
            return normal_move

        return None

    def apply_move(self, src: str, dst: str) -> DeduceResult:
        """Validates and applies a move. Returns DeduceResult."""
        src = src.lower()
        dst = dst.lower()

        piece_at_src = self._board.piece_at(chess.parse_square(src))
        if piece_at_src is None:
            return DeduceResult(
                move_detected=False,
                fen_after=self.get_fen(),
                description=f"Rejected: no piece on {src}"
            )

        piece_color_white = piece_at_src.color == chess.WHITE
        is_white_turn = self._board.turn == chess.WHITE
        if piece_color_white != is_white_turn:
            expected = "white" if is_white_turn else "black"
            p_name = PIECE_NAMES.get(piece_at_src.symbol(), str(piece_at_src))
            return DeduceResult(
                move_detected=False,
                fen_after=self.get_fen(),
                description=f"Rejected: it is {expected}'s turn, but {src} has {p_name}"
            )

        move = self._find_legal_move(src, dst)
        if move is None:
            p_name = PIECE_NAMES.get(piece_at_src.symbol(), str(piece_at_src))
            return DeduceResult(
                move_detected=False,
                fen_after=self.get_fen(),
                description=f"Illegal move: {p_name} {src} -> {dst}"
            )

        san = self._board.san(move)
        piece_symbol = piece_at_src.symbol()

        captured_piece = self._board.piece_at(move.to_square)
        if captured_piece is None and self._board.is_en_passant(move):
            captured_piece = chess.Piece(chess.PAWN, not self._board.turn)
        captured_symbol = captured_piece.symbol() if captured_piece else None

        self._board.push(move)
        self.pending_lift = None
        self.previous_sensors = frozenset(self.board.keys())

        p_name = PIECE_NAMES.get(piece_symbol, piece_symbol)
        desc = f"{p_name}: {src} -> {dst}"
        if captured_symbol:
            cap_name = PIECE_NAMES.get(captured_symbol, captured_symbol)
            desc += f" (captured {cap_name})"
        if self._board.is_check():
            desc += " +"

        return DeduceResult(
            move_detected=True,
            uci=move.uci(),
            san=san,
            from_sq=src,
            to_sq=dst,
            piece=piece_symbol,
            captured=captured_symbol,
            fen_after=self.get_fen(),
            description=desc
        )

    def ingest_reed_frame(self, frame_line: str) -> DeduceResult:
        """Ingests a 'BOARD:...' serial line and determines what move occurred."""
        current_sensors = parse_reed_frame(frame_line)
        if current_sensors is None:
            return DeduceResult(
                move_detected=False,
                fen_after=self.get_fen(),
                description=f"Invalid reed frame format: {frame_line[:30]}"
            )

        # First scan — establish baseline
        if self.previous_sensors is None:
            self.previous_sensors = current_sensors
            expected = frozenset(self.board.keys())
            if current_sensors == expected:
                desc = f"Board initialized ({len(current_sensors)} pieces)"
            else:
                desc = f"Board baseline set ({len(current_sensors)} sensors active)"
            return DeduceResult(
                move_detected=False,
                fen_after=self.get_fen(),
                description=desc
            )

        removed = self.previous_sensors - current_sensors
        added = current_sensors - self.previous_sensors
        self.previous_sensors = current_sensors

        # No change
        if not removed and not added:
            return DeduceResult(
                move_detected=False,
                fen_after=self.get_fen(),
                description="No physical piece changes detected"
            )

        # Castling detection (2 removed, 2 added)
        castling_patterns = [
            ({"e1", "h1"}, {"g1", "f1"}, "e1", "g1"),
            ({"e1", "a1"}, {"c1", "d1"}, "e1", "c1"),
            ({"e8", "h8"}, {"g8", "f8"}, "e8", "g8"),
            ({"e8", "a8"}, {"c8", "d8"}, "e8", "c8"),
        ]
        for rm_pattern, add_pattern, king_src, king_dst in castling_patterns:
            if removed == rm_pattern and added == add_pattern:
                return self.apply_move(king_src, king_dst)

        # Standard move (1 removed, 1 added)
        if len(removed) == 1 and len(added) == 1:
            src = next(iter(removed))
            dst = next(iter(added))
            return self.apply_move(src, dst)

        # Capture: attacker lifted, victim lifted, attacker placed on victim's square
        # Shows as 2 removed, 1 added
        if len(removed) == 2 and len(added) == 1:
            dst = next(iter(added))
            # The piece that was NOT on dst is the attacker
            for sq in removed:
                if sq != dst:
                    return self.apply_move(sq, dst)

        # En passant: 2 removed (attacker + captured pawn), 1 added (attacker's destination)
        # Already handled by the 2-removed/1-added case above

        # Piece lifted, waiting for placement (1 removed, 0 added)
        if len(removed) == 1 and not added:
            lifted = next(iter(removed))
            self.pending_lift = lifted
            piece = self._board.piece_at(chess.parse_square(lifted))
            p_name = PIECE_NAMES.get(piece.symbol(), "Piece") if piece else "Piece"
            return DeduceResult(
                move_detected=False,
                fen_after=self.get_fen(),
                description=f"{p_name} lifted from {lifted} (waiting for placement)"
            )

        # Piece placed after earlier lift (0 removed, 1 added)
        if not removed and len(added) == 1 and self.pending_lift:
            src = self.pending_lift
            dst = next(iter(added))
            return self.apply_move(src, dst)

        return DeduceResult(
            move_detected=False,
            fen_after=self.get_fen(),
            description=f"Ambiguous sensor delta: removed={sorted(removed)}, added={sorted(added)}"
        )


def main():
    print("==================================================")
    print("Game Tracker Subsystem (python-chess)")
    print("==================================================")

    tracker = GameTracker()
    print(f"Initial FEN: {tracker.get_fen()}")

    frame_start = "BOARD:11111111/11111111/00000000/00000000/00000000/00000000/11111111/11111111"
    tracker.ingest_reed_frame(frame_start)

    frame_e2e4 = "BOARD:11111111/11110111/00000000/00001000/00000000/00000000/11111111/11111111"
    result = tracker.ingest_reed_frame(frame_e2e4)

    print(f"\nHuman Move (e2->e4): detected={result.move_detected} SAN={result.san} UCI={result.uci}")
    print(f"FEN: {result.fen_after}")

    result2 = tracker.apply_move("e7", "e5")
    print(f"Black Move (e7->e5): detected={result2.move_detected} SAN={result2.san}")
    print(f"FEN: {result2.fen_after}")

    # Test illegal move
    result3 = tracker.apply_move("a2", "a5")
    print(f"\nIllegal (a2->a5): detected={result3.move_detected} desc={result3.description}")

    # Test game over detection
    print(f"\nGame over: {tracker.is_game_over()}")
    print("✓ Game Tracker verified!")


if __name__ == "__main__":
    main()
