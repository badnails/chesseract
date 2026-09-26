#!/usr/bin/env python3
"""
Move Planner Subsystem for Automated Chessboard Gantry.
Follows Single Responsibility Principle (SRP):
Given the current board state and a target chess move (e.g. from Stockfish),
it plans the optimal physical movement sequence, including:
1. Finding the path with the least number of obstructing pieces (Dijkstra).
2. Evacuating obstructing pieces to nearby free 'parking' squares (BFS).
3. Executing the main piece move.
4. Restoring evacuated pieces back to their original squares (LIFO stack).
5. Handling gantry homing (always starting/returning to a1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import heapq
from typing import Dict, List, Optional, Set, Tuple


class ActionType(str, Enum):
    CAPTURE_REMOVE = "CAPTURE_REMOVE"
    EVACUATE = "EVACUATE"
    MOVE_MAIN = "MOVE_MAIN"
    RESTORE = "RESTORE"
    HOME_GANTRY = "HOME_GANTRY"


@dataclass
class MoveAction:
    """Represents a single atomic physical move executed by the gantry."""
    action_type: ActionType
    from_sq: str
    to_sq: str
    piece: str = "?"
    reason: str = ""

    def to_gantry_command(self) -> str:
        """Translates this action into ATmega32 chess_gantry UART commands."""
        if self.action_type == ActionType.HOME_GANTRY:
            return f"SQ {self.to_sq}"
        return f"MOVE {self.from_sq}{self.to_sq}"

    def __repr__(self) -> str:
        return (
            f"[{self.action_type.value}] {self.piece} from {self.from_sq} -> {self.to_sq} "
            f"({self.reason})"
        )


@dataclass
class MovePlan:
    """Complete planned sequence of physical actions to execute a chess move."""
    original_move: str
    actions: List[MoveAction] = field(default_factory=list)
    path: List[str] = field(default_factory=list)
    obstructing_squares: List[str] = field(default_factory=list)
    parking_assignments: Dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"=== Move Plan for '{self.original_move}' ===",
            f"Planned Path : {' -> '.join(self.path)}",
            f"Obstructions : {', '.join(self.obstructing_squares) if self.obstructing_squares else 'None'}",
        ]
        if self.parking_assignments:
            parking_str = ", ".join(f"{orig}->{park}" for orig, park in self.parking_assignments.items())
            lines.append(f"Parking Spots: {parking_str}")

        lines.append(f"Total Steps  : {len(self.actions)}")
        lines.append("Action Execution Sequence:")
        for idx, act in enumerate(self.actions, start=1):
            lines.append(f"  {idx}. {act} => UART: '{act.to_gantry_command()}'")
        return "\n".join(lines)


def square_to_coord(sq: str) -> Tuple[int, int]:
    """Convert square notation (e.g. 'e4') to (file_idx, rank_idx) 0-indexed."""
    sq = sq.strip().lower()
    if len(sq) != 2 or sq[0] < 'a' or sq[0] > 'h' or sq[1] < '1' or sq[1] > '8':
        raise ValueError(f"Invalid chess square: '{sq}'")
    return ord(sq[0]) - ord('a'), ord(sq[1]) - ord('1')


def coord_to_square(file_idx: int, rank_idx: int) -> str:
    """Convert (file_idx, rank_idx) 0-indexed to square notation (e.g. 'e4')."""
    if not (0 <= file_idx <= 7 and 0 <= rank_idx <= 7):
        raise ValueError(f"Invalid coordinate: ({file_idx}, {rank_idx})")
    return f"{chr(ord('a') + file_idx)}{rank_idx + 1}"


def manhattan_distance(sq1: str, sq2: str) -> int:
    """Manhattan distance between two squares."""
    x1, y1 = square_to_coord(sq1)
    x2, y2 = square_to_coord(sq2)
    return abs(x1 - x2) + abs(y1 - y2)


class BoardState:
    """
    Tracks piece presence across the 8x8 chessboard.
    Can be loaded from reed switch matrix string, FEN, or direct dict.
    """
    def __init__(self, squares: Optional[Dict[str, Optional[str]]] = None):
        self.squares: Dict[str, Optional[str]] = {}
        for f in range(8):
            for r in range(8):
                sq = coord_to_square(f, r)
                self.squares[sq] = None

        if squares:
            for sq, piece in squares.items():
                self.set_piece(sq, piece)

    def is_occupied(self, sq: str) -> bool:
        return self.squares.get(sq.lower()) is not None

    def get_piece(self, sq: str) -> Optional[str]:
        return self.squares.get(sq.lower())

    def set_piece(self, sq: str, piece: Optional[str]) -> None:
        self.squares[sq.lower()] = piece

    def clear_square(self, sq: str) -> None:
        self.squares[sq.lower()] = None

    def clone(self) -> BoardState:
        return BoardState(dict(self.squares))

    @classmethod
    def from_reed_matrix_string(cls, matrix_str: str) -> BoardState:
        """
        Parses serial output from chess.c:
        'BOARD:11111111/11111111/00000000/00000000/00000000/00000000/11111111/11111111'
        where '1' means occupied and '0' means empty.
        """
        board = cls()
        cleaned = matrix_str.strip()
        if cleaned.startswith("BOARD:"):
            cleaned = cleaned[len("BOARD:"):]

        ranks = cleaned.split("/")
        if len(ranks) != 8:
            raise ValueError(f"Malformed reed matrix string (must contain 8 ranks): '{matrix_str}'")

        for r_idx, rank_data in enumerate(ranks):
            rank_data = rank_data.strip()
            if len(rank_data) != 8:
                raise ValueError(f"Rank {r_idx + 1} has invalid length ({len(rank_data)}): '{rank_data}'")
            for f_idx, char in enumerate(rank_data):
                sq = coord_to_square(f_idx, r_idx)
                if char == '1':
                    board.set_piece(sq, "P")  # Generic piece token if identity unknown
                else:
                    board.clear_square(sq)
        return board

    @classmethod
    def standard_starting_position(cls) -> BoardState:
        """Initializes a standard chess starting board state."""
        board = cls()
        # White pieces (Ranks 1 and 2)
        back_white = ["R", "N", "B", "Q", "K", "B", "N", "R"]
        for f, p in enumerate(back_white):
            board.set_piece(coord_to_square(f, 0), p)
            board.set_piece(coord_to_square(f, 1), "P")

        # Black pieces (Ranks 7 and 8)
        back_black = ["r", "n", "b", "q", "k", "b", "n", "r"]
        for f, p in enumerate(back_black):
            board.set_piece(coord_to_square(f, 7), p)
            board.set_piece(coord_to_square(f, 6), "p")

        return board


class MovePlanner:
    """
    Plans collision-free piece transport across the 8x8 chessboard.
    Uses:
      - Dijkstra on 8x8 orthogonal grid with weighted obstacle penalties
        to find the route traversing the minimum number of obstructing pieces.
      - BFS flood fill to discover nearest safe parking squares.
      - LIFO restoration stack for temporarily displaced pieces.
    """

    OBSTACLE_PENALTY = 1000  # High cost for traversing an occupied square

    def __init__(self, home_square: str = "a1"):
        self.home_square = home_square.lower()

    def get_orthogonal_neighbors(self, sq: str) -> List[str]:
        """Returns valid N, S, E, W neighboring squares."""
        f, r = square_to_coord(sq)
        neighbors = []
        for df, dr in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nf, nr = f + df, r + dr
            if 0 <= nf <= 7 and 0 <= nr <= 7:
                neighbors.append(coord_to_square(nf, nr))
        return neighbors

    def find_least_obstructed_path(
        self,
        from_sq: str,
        to_sq: str,
        board: BoardState
    ) -> List[str]:
        """
        Finds the shortest orthogonal path from `from_sq` to `to_sq` that
        traverses the least number of occupied squares.
        Uses Dijkstra search where empty square cost = 1 and occupied square cost = OBSTACLE_PENALTY.
        """
        from_sq = from_sq.lower()
        to_sq = to_sq.lower()

        # Priority queue stores tuples: (accumulated_cost, current_sq, path)
        pq: List[Tuple[int, str, List[str]]] = [(0, from_sq, [from_sq])]
        visited: Dict[str, int] = {from_sq: 0}

        while pq:
            cost, curr, path = heapq.heappop(pq)

            if curr == to_sq:
                return path

            if cost > visited.get(curr, float('inf')):
                continue

            for neighbor in self.get_orthogonal_neighbors(curr):
                # Edge weight:
                # If neighbor is destination, ignore target piece occupancy (handled via capture/arrival)
                if neighbor == to_sq:
                    step_cost = 1
                elif board.is_occupied(neighbor):
                    step_cost = self.OBSTACLE_PENALTY
                else:
                    step_cost = 1

                new_cost = cost + step_cost
                if new_cost < visited.get(neighbor, float('inf')):
                    visited[neighbor] = new_cost
                    heapq.heappush(pq, (new_cost, neighbor, path + [neighbor]))

        raise RuntimeError(f"Could not find any path from {from_sq} to {to_sq}")

    def find_parking_spot(
        self,
        blocking_sq: str,
        board: BoardState,
        reserved_squares: Set[str]
    ) -> str:
        """
        Finds the nearest unoccupied square to `blocking_sq` that is NOT in `reserved_squares`.
        Uses Breadth-First Search (BFS).
        """
        queue = [blocking_sq]
        visited = {blocking_sq}

        while queue:
            curr = queue.pop(0)

            # Check if this square is suitable for parking
            if curr != blocking_sq and not board.is_occupied(curr) and curr not in reserved_squares:
                return curr

            for neighbor in self.get_orthogonal_neighbors(curr):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)

        raise RuntimeError(f"No available parking spot on the board for piece at {blocking_sq}!")

    def plan_move(
        self,
        move_str: str,
        board: BoardState,
        graveyard_sq: str = "h1",
        home_at_end: bool = True
    ) -> MovePlan:
        """
        Plans a complete physical move sequence for a standard move string (e.g. 'e2e4' or 'g1f3').

        Workflow:
        1. Validate source and destination.
        2. If destination is occupied by opponent, plan capture removal to graveyard.
        3. Compute the least-obstructed orthogonal path from from_sq to to_sq.
        4. Identify all intermediate squares that have blocking pieces.
        5. For each blocking piece:
           - Allocate a safe temporary parking spot (outside the planned path).
           - Add EVACUATE action.
        6. Add MOVE_MAIN action for the active piece.
        7. For each evacuated piece (in reverse order / LIFO):
           - Add RESTORE action returning it to its original square.
        8. If `home_at_end`, add HOME_GANTRY action returning to `a1`.
        """
        cleaned_move = move_str.strip().lower()
        if len(cleaned_move) < 4:
            raise ValueError(f"Invalid move format: '{move_str}' (expected e.g. 'e2e4')")

        from_sq = cleaned_move[0:2]
        to_sq = cleaned_move[2:4]

        if not board.is_occupied(from_sq):
            raise ValueError(f"Cannot move: Source square '{from_sq}' is empty!")

        moving_piece = board.get_piece(from_sq) or "Piece"
        working_board = board.clone()

        plan = MovePlan(original_move=cleaned_move)

        # 1. Handle Capture if destination is occupied
        if working_board.is_occupied(to_sq):
            captured_piece = working_board.get_piece(to_sq) or "OpponentPiece"
            plan.actions.append(
                MoveAction(
                    action_type=ActionType.CAPTURE_REMOVE,
                    from_sq=to_sq,
                    to_sq=graveyard_sq,
                    piece=captured_piece,
                    reason=f"Capture removal of {captured_piece} on {to_sq}"
                )
            )
            working_board.clear_square(to_sq)

        # 2. Find least-obstructed path
        path = self.find_least_obstructed_path(from_sq, to_sq, working_board)
        plan.path = path

        # 3. Identify obstructing pieces (intermediate squares)
        intermediate_sqs = path[1:-1]
        obstructing_sqs = [sq for sq in intermediate_sqs if working_board.is_occupied(sq)]
        plan.obstructing_squares = obstructing_sqs

        # 4. Plan Evacuation of each obstructing piece
        # Reserved squares: squares that cannot be used as parking spots
        reserved = set(path) | {from_sq, to_sq}
        evacuations: List[Tuple[str, str, str]] = []  # (original_sq, park_sq, piece)

        for obs_sq in obstructing_sqs:
            obs_piece = working_board.get_piece(obs_sq) or "Blocker"
            park_sq = self.find_parking_spot(obs_sq, working_board, reserved)

            plan.parking_assignments[obs_sq] = park_sq
            reserved.add(park_sq)

            # Update working board for subsequent search
            working_board.clear_square(obs_sq)
            working_board.set_piece(park_sq, obs_piece)

            evacuations.append((obs_sq, park_sq, obs_piece))

            plan.actions.append(
                MoveAction(
                    action_type=ActionType.EVACUATE,
                    from_sq=obs_sq,
                    to_sq=park_sq,
                    piece=obs_piece,
                    reason=f"Clear path for {from_sq}->{to_sq}"
                )
            )

        # 5. Plan Main Move
        plan.actions.append(
            MoveAction(
                action_type=ActionType.MOVE_MAIN,
                from_sq=from_sq,
                to_sq=to_sq,
                piece=moving_piece,
                reason=f"Execute chess move {cleaned_move}"
            )
        )
        working_board.clear_square(from_sq)
        working_board.set_piece(to_sq, moving_piece)

        # 6. Plan Restoration of Evacuated Pieces in LIFO order
        for orig_sq, park_sq, obs_piece in reversed(evacuations):
            plan.actions.append(
                MoveAction(
                    action_type=ActionType.RESTORE,
                    from_sq=park_sq,
                    to_sq=orig_sq,
                    piece=obs_piece,
                    reason=f"Return {obs_piece} to original square {orig_sq}"
                )
            )
            working_board.clear_square(park_sq)
            working_board.set_piece(orig_sq, obs_piece)

        # 7. Return Gantry to Home (a1)
        if home_at_end:
            plan.actions.append(
                MoveAction(
                    action_type=ActionType.HOME_GANTRY,
                    from_sq=to_sq,
                    to_sq=self.home_square,
                    piece="Gantry",
                    reason=f"Return gantry head to origin ({self.home_square})"
                )
            )

        return plan


if __name__ == "__main__":
    planner = MovePlanner(home_square="a1")
    b = BoardState.standard_starting_position()
    p = planner.plan_move("g1f3", b)
    print(p.summary())

