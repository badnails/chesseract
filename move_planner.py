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


BoardDict = Dict[str, str]


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
        board: BoardDict
    ) -> List[str]:
        """
        Dijkstra search: empty square cost = 1, occupied = OBSTACLE_PENALTY.
        """
        from_sq = from_sq.lower()
        to_sq = to_sq.lower()

        pq: List[Tuple[int, str, List[str]]] = [(0, from_sq, [from_sq])]
        visited: Dict[str, int] = {from_sq: 0}

        while pq:
            cost, curr, path = heapq.heappop(pq)

            if curr == to_sq:
                return path

            if cost > visited.get(curr, float('inf')):
                continue

            for neighbor in self.get_orthogonal_neighbors(curr):
                if neighbor == to_sq:
                    step_cost = 1
                elif neighbor in board:
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
        board: BoardDict,
        reserved_squares: Set[str]
    ) -> str:
        """BFS for nearest unoccupied, unreserved square."""
        queue = [blocking_sq]
        visited = {blocking_sq}

        while queue:
            curr = queue.pop(0)

            if curr != blocking_sq and curr not in board and curr not in reserved_squares:
                return curr

            for neighbor in self.get_orthogonal_neighbors(curr):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)

        raise RuntimeError(f"No available parking spot on the board for piece at {blocking_sq}!")

    def plan_move(
        self,
        move_str: str,
        board: BoardDict,
        home_at_end: bool = True
    ) -> MovePlan:
        """
        Plans a complete physical move sequence for a move string (e.g. 'e2e4').
        board is a dict mapping occupied square names to piece characters.
        """
        cleaned_move = move_str.strip().lower()
        if len(cleaned_move) < 4:
            raise ValueError(f"Invalid move format: '{move_str}' (expected e.g. 'e2e4')")

        from_sq = cleaned_move[0:2]
        to_sq = cleaned_move[2:4]

        if from_sq not in board:
            raise ValueError(f"Cannot move: Source square '{from_sq}' is empty!")

        moving_piece = board[from_sq]
        wb = dict(board)

        plan = MovePlan(original_move=cleaned_move)

        if to_sq in wb:
            del wb[to_sq]

        path = self.find_least_obstructed_path(from_sq, to_sq, wb)
        plan.path = path

        intermediate_sqs = path[1:-1]
        obstructing_sqs = [sq for sq in intermediate_sqs if sq in wb]
        plan.obstructing_squares = obstructing_sqs

        reserved = set(path) | {from_sq, to_sq}
        evacuations: List[Tuple[str, str, str]] = []

        for obs_sq in obstructing_sqs:
            obs_piece = wb[obs_sq]
            park_sq = self.find_parking_spot(obs_sq, wb, reserved)

            plan.parking_assignments[obs_sq] = park_sq
            reserved.add(park_sq)

            del wb[obs_sq]
            wb[park_sq] = obs_piece

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

        plan.actions.append(
            MoveAction(
                action_type=ActionType.MOVE_MAIN,
                from_sq=from_sq,
                to_sq=to_sq,
                piece=moving_piece,
                reason=f"Execute chess move {cleaned_move}"
            )
        )
        del wb[from_sq]
        wb[to_sq] = moving_piece

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
            del wb[park_sq]
            wb[orig_sq] = obs_piece

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
    board: BoardDict = {}
    back_white = ["R", "N", "B", "Q", "K", "B", "N", "R"]
    back_black = ["r", "n", "b", "q", "k", "b", "n", "r"]
    for f in range(8):
        board[coord_to_square(f, 0)] = back_white[f]
        board[coord_to_square(f, 1)] = "P"
        board[coord_to_square(f, 6)] = "p"
        board[coord_to_square(f, 7)] = back_black[f]
    p = planner.plan_move("g1f3", board)
    print(p.summary())

