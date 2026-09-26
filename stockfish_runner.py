#!/usr/bin/env python3
"""
Stockfish Engine Subsystem for Automated Chessboard.
Follows Single Responsibility Principle (SRP):
Dedicated exclusively to interfacing with the Stockfish chess engine via the
standard UCI (Universal Chess Interface) protocol over subprocess pipes.

Given a board position (FEN or starting moves), queries Stockfish and returns
the engine's recommended best move (e.g. 'e7e5', 'g8f6').
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from typing import Optional, Tuple


class StockfishEngine:
    """
    Manages a persistent Stockfish process via UCI pipe communication.
    Works natively on macOS (Apple Silicon / Intel) and Linux.
    """

    DEFAULT_BINARY_PATHS = [
        "/opt/homebrew/bin/stockfish",       # Standard Homebrew on Apple Silicon (M1/M2/M3/M4)
        "/usr/local/bin/stockfish",          # Standard Homebrew on Intel Mac / Linux
        "stockfish",                         # System PATH
    ]

    def __init__(
        self,
        binary_path: Optional[str] = None,
        depth: int = 14,
        skill_level: int = 20,  # 0 (easiest) to 20 (grandmaster)
        threads: int = 2,
        hash_mb: int = 32
    ):
        self.depth = depth
        self.skill_level = skill_level
        self.threads = threads
        self.hash_mb = hash_mb
        self.process: Optional[subprocess.Popen] = None

        self.binary_path = self._locate_binary(binary_path)
        if self.binary_path:
            self._start_process()

    def _locate_binary(self, custom_path: Optional[str]) -> Optional[str]:
        """Locates the Stockfish executable binary on the Mac filesystem."""
        if custom_path:
            if os.path.isfile(custom_path) and os.access(custom_path, os.X_OK):
                return custom_path
            resolved = shutil.which(custom_path)
            if resolved:
                return resolved

        for candidate in self.DEFAULT_BINARY_PATHS:
            if os.path.isabs(candidate) and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
            resolved = shutil.which(candidate)
            if resolved:
                return resolved

        return None

    def is_installed(self) -> bool:
        """Returns True if the native Stockfish binary is detected and runnable."""
        return self.binary_path is not None and self.process is not None

    def _start_process(self) -> None:
        """Launches the Stockfish process and initializes UCI parameters."""
        if not self.binary_path:
            return

        try:
            self.process = subprocess.Popen(
                [self.binary_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )

            # Initialize UCI
            self._send_command("uci")
            self._wait_for("uciok")

            # Configure Engine Options
            self._send_command(f"setoption name Threads value {self.threads}")
            self._send_command(f"setoption name Hash value {self.hash_mb}")
            self._send_command(f"setoption name Skill Level value {self.skill_level}")
            self._send_command("isready")
            self._wait_for("readyok")

        except Exception as e:
            self.process = None
            raise RuntimeError(f"Failed to start Stockfish process at '{self.binary_path}': {e}")

    def _send_command(self, cmd: str) -> None:
        """Sends a raw UCI command line to Stockfish stdin."""
        if not self.process or not self.process.stdin:
            raise RuntimeError("Stockfish process is not running.")
        try:
            self.process.stdin.write(cmd.strip() + chr(10))
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
    def _wait_for(self, target: str, timeout: float = 5.0) -> list[str]:
        """Reads lines from stdout until target prefix or line is matched."""
        if not self.process or not self.process.stdout:
            raise RuntimeError("Stockfish process is not running.")

        lines = []
        start_time = time.time()
        while time.time() - start_time < timeout:
            line = self.process.stdout.readline()
            if not line:
                break
            line_str = line.strip()
            lines.append(line_str)
            if line_str == target or line_str.startswith(target):
                return lines

        raise TimeoutError(f"Stockfish timed out waiting for: '{target}'")

    def get_best_move(
        self,
        fen: str,
        depth: Optional[int] = None,
        movetime_ms: Optional[int] = None
    ) -> str:
        """
        Calculates and returns the best move (e.g. 'e7e5') for the given FEN position.
        """
        if not self.is_installed():
            raise RuntimeError(
                "Stockfish binary is not installed on this Mac!\n"
                "Please run: 'brew install stockfish' in your terminal."
            )

        search_depth = depth if depth is not None else self.depth

        # Tell Stockfish the position
        self._send_command(f"position fen {fen.strip()}")

        # Start search
        if movetime_ms:
            self._send_command(f"go movetime {movetime_ms}")
        else:
            self._send_command(f"go depth {search_depth}")

        # Read output until bestmove is found
        lines = self._wait_for("bestmove", timeout=15.0)
        for line in reversed(lines):
            if line.startswith("bestmove"):
                parts = line.split()
                if len(parts) >= 2:
                    return parts[1]

        raise RuntimeError("No best move returned by Stockfish.")

    def get_evaluation_and_best_move(
        self,
        fen: str,
        depth: Optional[int] = None
    ) -> Tuple[str, str]:
        """
        Returns (best_move, evaluation_str), e.g. ('e7e5', '+0.45' or 'M2').
        """
        if not self.is_installed():
            raise RuntimeError("Stockfish is not installed.")

        search_depth = depth if depth is not None else self.depth
        self._send_command(f"position fen {fen.strip()}")
        self._send_command(f"go depth {search_depth}")

        lines = self._wait_for("bestmove", timeout=15.0)
        best_move = ""
        eval_str = "0.0"

        for line in lines:
            if "score cp" in line:
                idx = line.find("score cp")
                parts = line[idx:].split()
                if len(parts) >= 3:
                    cp = int(parts[2])
                    eval_str = f"{cp / 100.0:+.2f}"
            elif "score mate" in line:
                idx = line.find("score mate")
                parts = line[idx:].split()
                if len(parts) >= 3:
                    eval_str = f"M{parts[2]}"

            if line.startswith("bestmove"):
                parts = line.split()
                if len(parts) >= 2:
                    best_move = parts[1]

        return best_move, eval_str

    def close(self) -> None:
        """Gracefully terminates the Stockfish process."""
        if self.process:
            try:
                self._send_command("quit")
                self.process.terminate()
                self.process.wait(timeout=1.0)
            except Exception:
                pass
            self.process = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# ==============================================================================
# Standalone CLI / Verification
# ==============================================================================

def main():
    print("==================================================")
    print("Stockfish Engine Subsystem for Mac (ATmega Bridge)")
    print("==================================================")

    engine = StockfishEngine()

    if not engine.is_installed():
        print("\n❌ Native Stockfish binary was NOT found on this Mac.")
        print("To install it natively in 10 seconds, run:")
        print("    brew install stockfish\n")
        print("Checking default paths checked:")
        for p in StockfishEngine.DEFAULT_BINARY_PATHS:
            print(f"  - {p} (exists: {os.path.exists(p)})")
        sys.exit(1)

    print(f"\n✓ Found Native Stockfish at: {engine.binary_path}")

    # Test Position 1: After 1. e4 (Black to move)
    fen_after_e4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
    print(f"\nEvaluating position after White's 1. e4:")
    print(f"FEN: {fen_after_e4}")

    t0 = time.time()
    best_move, eval_score = engine.get_evaluation_and_best_move(fen_after_e4, depth=14)
    elapsed = time.time() - t0

    print(f"Best Move : {best_move}")
    print(f"Eval      : {eval_score}")
    print(f"Search Time: {elapsed:.3f} seconds")
    print("\n✓ Stockfish Engine subsystem is ready!")

    engine.close()


if __name__ == "__main__":
    main()

