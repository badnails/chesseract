#!/usr/bin/env python3
"""
Move Visualizer Subsystem for Automated Chessboard Gantry.
Follows Single Responsibility Principle (SRP):
Dedicated exclusively to visualizing and stepping through move plans, showing:
- Which moves are being made.
- Who is giving place to who (obstacle evacuation and parking spots).
- Gantry position tracking and home return to a1.
- Both Terminal (ANSI colored) and Web (Interactive browser GUI) modes.
"""

from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, HTTPServer
import json
import os
import sys
import time
from typing import Dict, List, Optional
import urllib.parse
import webbrowser

from move_planner import (
    BoardState,
    MoveAction,
    MovePlan,
    MovePlanner,
    ActionType,
    square_to_coord,
    coord_to_square,
)

# Unicode chess symbols
PIECE_SYMBOLS = {
    "K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘", "P": "♙",
    "k": "♚", "q": "♛", "r": "♜", "b": "♝", "n": "♞", "p": "♟",
    "Piece": "●", "Blocker": "▲", "P_friendly": "♙", "P_blocker": "▲",
    "X": "▲", "q_black": "♛", "?": "●"
}

# ANSI terminal colors
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_CYAN = "\033[36m"
C_RED = "\033[31m"
C_MAGENTA = "\033[35m"
C_DIM = "\033[2m"
BG_DARK = "\033[48;5;236m"
BG_LIGHT = "\033[48;5;240m"


class TerminalVisualizer:
    """Renders ANSI-colored step-by-step visual frames directly in terminal."""

    @staticmethod
    def render_board(
        board: BoardState,
        gantry_sq: str = "a1",
        highlight_from: Optional[str] = None,
        highlight_to: Optional[str] = None,
        highlight_type: Optional[ActionType] = None
    ) -> str:
        lines = []
        lines.append(f"{C_BOLD}   a   b   c   d   e   f   g   h{C_RESET}")
        lines.append(" +---+---+---+---+---+---+---+---+")

        for rank_idx in range(7, -1, -1):
            rank_num = rank_idx + 1
            row_str = f"{rank_num}|"

            for file_idx in range(8):
                sq = coord_to_square(file_idx, rank_idx)
                piece = board.get_piece(sq)
                symbol = PIECE_SYMBOLS.get(piece, piece[0] if piece else " ")

                is_light = (file_idx + rank_idx) % 2 == 1
                bg = BG_LIGHT if is_light else BG_DARK

                # Highlights
                cell_color = C_RESET
                indicator = " "
                if sq == gantry_sq:
                    indicator = "⚓"  # Gantry anchor

                if sq == highlight_to:
                    if highlight_type == ActionType.EVACUATE:
                        cell_color = f"{C_YELLOW}{C_BOLD}"
                    elif highlight_type == ActionType.RESTORE:
                        cell_color = f"{C_CYAN}{C_BOLD}"
                    else:
                        cell_color = f"{C_GREEN}{C_BOLD}"
                elif sq == highlight_from:
                    cell_color = f"{C_RED}{C_DIM}"

                if symbol == " ":
                    cell_content = f"{indicator} " if indicator != " " else "  "
                else:
                    cell_content = f"{symbol}{indicator if indicator != ' ' else ' '}"

                row_str += f"{bg}{cell_color} {cell_content}{C_RESET}{bg}|{C_RESET}"

            row_str += f" {rank_num}"
            lines.append(row_str)
            lines.append(" +---+---+---+---+---+---+---+---+")

        lines.append(f"{C_BOLD}   a   b   c   d   e   f   g   h{C_RESET}")
        return "\n".join(lines)

    @classmethod
    def play_plan_interactive(cls, plan: MovePlan, initial_board: BoardState):
        """Step-by-step playback in terminal with user keystroke."""
        curr_board = initial_board.clone()
        gantry_sq = "a1"

        print(f"\n{C_BOLD}======================================================{C_RESET}")
        print(f"{C_BOLD} PHYSICAL MOVE PLAN: {C_GREEN}{plan.original_move}{C_RESET}")
        print(f" Planned Path: {' -> '.join(plan.path)}")
        if plan.parking_assignments:
            print(f"{C_YELLOW} Displaced (Giving Place): {C_RESET}")
            for obs, park in plan.parking_assignments.items():
                p = initial_board.get_piece(obs) or "Piece"
                print(f"   • {p} on {obs} -> temporary parking spot {park}")
        else:
            print(" Path is clear: No pieces need to give place.")
        print(f" Total Physical Steps: {len(plan.actions)}")
        print(f"{C_BOLD}======================================================{C_RESET}\n")

        print(f"{C_BOLD}Initial Position (Gantry at {gantry_sq}):{C_RESET}")
        print(cls.render_board(curr_board, gantry_sq=gantry_sq))
        print(f"\n{C_CYAN}Press Enter to start physical execution...{C_RESET}")
        input()

        for idx, action in enumerate(plan.actions, start=1):
            # Header
            if action.action_type == ActionType.EVACUATE:
                color = C_YELLOW
                tag = "PARKING / EVACUATING"
            elif action.action_type == ActionType.MOVE_MAIN:
                color = C_GREEN
                tag = "MAIN MOVE"
            elif action.action_type == ActionType.RESTORE:
                color = C_CYAN
                tag = "RESTORING"
            elif action.action_type == ActionType.HOME_GANTRY:
                color = C_MAGENTA
                tag = "HOMING GANTRY"
            else:
                color = C_RED
                tag = "CAPTURE REMOVAL"

            print("\033[2J\033[H")  # Clear terminal screen
            print(f"{C_BOLD}Step {idx}/{len(plan.actions)}: {color}[{tag}]{C_RESET}")
            print(f" Action   : {action.piece} from {action.from_sq} -> {action.to_sq}")
            print(f" Reason   : {action.reason}")
            print(f" Firmware : {C_BOLD}UART TX => '{action.to_gantry_command()}'{C_RESET}")
            print("------------------------------------------------------")

            # Update board state
            if action.action_type == ActionType.HOME_GANTRY:
                gantry_sq = action.to_sq
            else:
                p = curr_board.get_piece(action.from_sq) or action.piece
                curr_board.clear_square(action.from_sq)
                curr_board.set_piece(action.to_sq, p)
                gantry_sq = action.to_sq

            print(cls.render_board(
                curr_board,
                gantry_sq=gantry_sq,
                highlight_from=action.from_sq,
                highlight_to=action.to_sq,
                highlight_type=action.action_type
            ))
            print(f"\n{C_DIM}Legend: ⚓ Gantry Head | Yellow: Parked | Green: Main | Cyan: Restored{C_RESET}")

            if idx < len(plan.actions):
                input(f"\n{C_CYAN}Press Enter for step {idx + 1}...{C_RESET}")
            else:
                print(f"\n{C_GREEN}{C_BOLD}✓ Execution Complete! Gantry returned to {gantry_sq}.{C_RESET}\n")


# ==============================================================================
# Web GUI Server Implementation
# ==============================================================================

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Automated Chess Gantry - Physical Move Visualizer</title>
  <style>
    :root {
      --bg: #1e1e24;
      --card-bg: #282830;
      --accent: #61afef;
      --green: #98c379;
      --yellow: #e5c07b;
      --red: #e06c75;
      --cyan: #56b6c2;
      --light-sq: #c0c7cf;
      --dark-sq: #788896;
      --text: #e0e0e0;
      --muted: #8a8a98;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      display: flex;
      flex-direction: column;
      height: 100vh;
      overflow: hidden;
    }
    header {
      background: #18181c;
      padding: 12px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid #333;
    }
    h1 { font-size: 18px; font-weight: 600; color: #fff; }
    .badge {
      background: #333842;
      color: var(--accent);
      padding: 4px 10px;
      border-radius: 4px;
      font-size: 12px;
      font-weight: 500;
    }
    .main-layout {
      display: flex;
      flex: 1;
      padding: 20px;
      gap: 24px;
      overflow: hidden;
    }
    .board-pane {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      flex: 1;
    }
    .board {
      width: 480px;
      height: 480px;
      display: grid;
      grid-template-columns: repeat(8, 1fr);
      grid-template-rows: repeat(8, 1fr);
      border: 3px solid #444;
      border-radius: 4px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.5);
      position: relative;
    }
    .cell {
      display: flex;
      justify-content: center;
      align-items: center;
      font-size: 38px;
      cursor: default;
      user-select: none;
      position: relative;
      transition: background-color 0.25s, box-shadow 0.25s;
    }
    .cell.light { background-color: var(--light-sq); color: #222; }
    .cell.dark { background-color: var(--dark-sq); color: #111; }
    .cell .coord {
      position: absolute;
      font-size: 10px;
      font-weight: 700;
      color: rgba(0,0,0,0.45);
      bottom: 2px;
      right: 3px;
    }
    .cell.highlight-from {
      box-shadow: inset 0 0 0 4px var(--red);
    }
    .cell.highlight-to-evacuate {
      box-shadow: inset 0 0 0 5px var(--yellow);
      background-color: rgba(229, 192, 123, 0.4) !important;
    }
    .cell.highlight-to-main {
      box-shadow: inset 0 0 0 5px var(--green);
      background-color: rgba(152, 195, 121, 0.45) !important;
    }
    .cell.highlight-to-restore {
      box-shadow: inset 0 0 0 5px var(--cyan);
      background-color: rgba(86, 182, 194, 0.4) !important;
    }
    .gantry-marker {
      position: absolute;
      top: 3px;
      left: 3px;
      font-size: 13px;
      background: rgba(224, 108, 117, 0.9);
      color: #fff;
      padding: 1px 4px;
      border-radius: 3px;
      font-weight: bold;
    }
    .controls-pane {
      width: 440px;
      background: var(--card-bg);
      border-radius: 8px;
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 16px;
      overflow-y: auto;
    }
    .section-title {
      font-size: 13px;
      font-weight: 700;
      text-transform: uppercase;
      color: var(--muted);
      letter-spacing: 0.5px;
    }
    .input-row {
      display: flex;
      gap: 10px;
    }
    input[type="text"] {
      flex: 1;
      padding: 10px 14px;
      background: #1e1e24;
      border: 1px solid #444;
      border-radius: 6px;
      color: #fff;
      font-size: 15px;
      outline: none;
    }
    input[type="text"]:focus { border-color: var(--accent); }
    button {
      background: var(--accent);
      color: #1e1e24;
      border: none;
      padding: 10px 16px;
      border-radius: 6px;
      font-weight: 600;
      cursor: pointer;
      transition: opacity 0.2s;
    }
    button:hover { opacity: 0.9; }
    .btn-secondary {
      background: #3a3a46;
      color: #fff;
    }
    .preset-btns {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .preset-btn {
      background: #333842;
      color: var(--text);
      padding: 6px 10px;
      font-size: 12px;
      border-radius: 4px;
    }
    .step-card {
      background: #1f1f26;
      border-left: 4px solid var(--accent);
      padding: 14px;
      border-radius: 4px;
    }
    .step-card.EVACUATE { border-color: var(--yellow); }
    .step-card.MOVE_MAIN { border-color: var(--green); }
    .step-card.RESTORE { border-color: var(--cyan); }
    .step-card.HOME_GANTRY { border-color: #c678dd; }
    .step-num { font-size: 12px; color: var(--muted); font-weight: 600; }
    .step-title { font-size: 16px; font-weight: 700; margin: 4px 0 8px 0; }
    .step-desc { font-size: 13px; color: #ccc; line-height: 1.4; }
    .uart-badge {
      display: inline-block;
      margin-top: 8px;
      background: #111;
      padding: 4px 8px;
      border-radius: 4px;
      font-family: monospace;
      font-size: 12px;
      color: #98c379;
    }
    .nav-buttons {
      display: flex;
      gap: 10px;
    }
    .nav-buttons button { flex: 1; }
    .table-box {
      background: #1f1f26;
      border-radius: 4px;
      overflow: hidden;
      font-size: 13px;
    }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 8px 12px; text-align: left; }
    th { background: #18181c; color: var(--muted); font-size: 11px; }
    tr:not(:last-child) { border-bottom: 1px solid #2d2d38; }
  </style>
</head>
<body>
  <header>
    <h1>Automated Chessboard Gantry &mdash; Move Visualizer</h1>
    <div class="badge">Mac Local Testbed &bull; ATmega32 Firmware Planner</div>
  </header>

  <main class="main-layout">
    <section class="board-pane">
      <div id="board" class="board"></div>
    </section>

    <aside class="controls-pane">
      <div>
        <div class="section-title">Test Move Input</div>
        <div class="input-row" style="margin-top: 8px;">
          <input type="text" id="moveInput" placeholder="e.g. g1f3 or e2e4" value="g1f3" />
          <button id="planBtn">Plan Move</button>
        </div>
      </div>

      <div>
        <div class="section-title">Preset Scenarios</div>
        <div class="preset-btns" style="margin-top: 8px;">
          <button class="preset-btn" onclick="loadPreset('g1f3')">Knight Leap (g1f3)</button>
          <button class="preset-btn" onclick="loadPreset('b1c3')">Knight Leap (b1c3)</button>
          <button class="preset-btn" onclick="loadPreset('e2e4')">Pawn Push (e2e4)</button>
          <button class="preset-btn" onclick="loadPreset('blocked_pawn')">Boxed-in Pawn</button>
          <button class="preset-btn" onclick="loadPreset('d4d8_capture')">Capture Queen (d4d8)</button>
        </div>
      </div>

      <!-- Current Step Display -->
      <div>
        <div class="section-title">Current Step Action</div>
        <div id="stepCard" class="step-card" style="margin-top: 8px;">
          <div class="step-num" id="stepNumText">Step 0 of 0</div>
          <div class="step-title" id="stepTitleText">Waiting for plan...</div>
          <div class="step-desc" id="stepDescText">Enter a move or select a preset to view the physical steps.</div>
          <div id="uartBadge" class="uart-badge" style="display:none;"></div>
        </div>
      </div>

      <!-- Stepping Controls -->
      <div class="nav-buttons">
        <button id="prevBtn" class="btn-secondary" onclick="prevStep()">◀ Previous</button>
        <button id="playBtn" class="btn-secondary" onclick="toggleAutoPlay()">▶ Auto Play</button>
        <button id="nextBtn" onclick="nextStep()">Next Step ▶</button>
      </div>

      <!-- Who Gives Place To Who Table -->
      <div>
        <div class="section-title">Obstacle Evacuation (Who Gives Place To Who)</div>
        <div class="table-box" style="margin-top: 8px;">
          <table>
            <thead>
              <tr><th>Blocker Piece</th><th>Original Sq</th><th>Temp Parking Sq</th><th>Status</th></tr>
            </thead>
            <tbody id="parkingTableBody">
              <tr><td colspan="4" style="color:#777; text-align:center;">No active obstacles</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </aside>
  </main>

  <script>
    const SYMBOLS = {
      'K': '♔', 'Q': '♕', 'R': '♖', 'B': '♗', 'N': '♘', 'P': '♙',
      'k': '♚', 'q': '♛', 'r': '♜', 'b': '♝', 'n': '♞', 'p': '♟',
      'Piece': '●', 'Blocker': '▲', 'P_friendly': '♙', 'P_blocker': '▲',
      'X': '▲', 'q_black': '♛', '?': '●'
    };

    let currentPlan = null;
    let initialBoard = {};
    let stepBoards = [];
    let currentStepIdx = 0;
    let autoPlayTimer = null;

    function buildEmptyBoardUI() {
      const boardEl = document.getElementById('board');
      boardEl.innerHTML = '';
      for (let r = 7; r >= 0; r--) {
        for (let f = 0; f < 8; f++) {
          const sq = String.fromCharCode(97 + f) + (r + 1);
          const cell = document.createElement('div');
          const isLight = (f + r) % 2 === 1;
          cell.className = 'cell ' + (isLight ? 'light' : 'dark');
          cell.id = 'cell_' + sq;
          cell.innerHTML = '<span class="coord">' + sq + '</span><span class="piece" id="p_' + sq + '"></span>';
          boardEl.appendChild(cell);
        }
      }
    }

    function renderBoardState(boardState, gantrySq, highlightFrom, highlightTo, highlightType) {
      for (let r = 0; r < 8; r++) {
        for (let f = 0; f < 8; f++) {
          const sq = String.fromCharCode(97 + f) + (r + 1);
          const cell = document.getElementById('cell_' + sq);
          const pieceSpan = document.getElementById('p_' + sq);
          
          // Clear previous highlights
          cell.className = cell.className.split(' ').filter(c => !c.startsWith('highlight-')).join(' ');
          
          // Remove old gantry marker
          const oldMarker = cell.querySelector('.gantry-marker');
          if (oldMarker) oldMarker.remove();

          // Piece display
          const p = boardState[sq];
          pieceSpan.innerText = p ? (SYMBOLS[p] || p) : '';

          // Gantry marker
          if (sq === gantrySq) {
            const marker = document.createElement('div');
            marker.className = 'gantry-marker';
            marker.innerText = '⚓';
            cell.appendChild(marker);
          }

          // Highlights
          if (sq === highlightFrom) {
            cell.classList.add('highlight-from');
          }
          if (sq === highlightTo) {
            if (highlightType === 'EVACUATE') cell.classList.add('highlight-to-evacuate');
            else if (highlightType === 'RESTORE') cell.classList.add('highlight-to-restore');
            else cell.classList.add('highlight-to-main');
          }
        }
      }
    }

    async function requestPlan(moveStr, presetName = null) {
      const url = '/api/plan?move=' + encodeURIComponent(moveStr) + (presetName ? '&preset=' + presetName : '');
      const resp = await fetch(url);
      const data = await resp.json();
      
      currentPlan = data.plan;
      initialBoard = data.initial_board;
      stepBoards = data.step_boards;
      currentStepIdx = 0;
      
      updateParkingTable(data.plan.parking_assignments, initialBoard);
      updateStepUI();
    }

    function updateParkingTable(parking, board) {
      const tbody = document.getElementById('parkingTableBody');
      tbody.innerHTML = '';
      const entries = Object.entries(parking || {});
      if (entries.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="color:#777; text-align:center;">No obstacles (Direct clear route)</td></tr>';
        return;
      }
      entries.forEach(([orig, park]) => {
        const piece = board[orig] || 'Piece';
        const tr = document.createElement('tr');
        tr.innerHTML = `<td><strong>${SYMBOLS[piece] || piece}</strong> (${piece})</td>` +
                       `<td><code>${orig}</code></td>` +
                       `<td style="color:var(--yellow); font-weight:bold;"><code>${park}</code></td>` +
                       `<td id="status_${orig}">Giving place</td>`;
        tbody.appendChild(tr);
      });
    }

    function updateStepUI() {
      if (!currentPlan) return;
      const actions = currentPlan.actions;
      const total = actions.length;

      if (currentStepIdx === 0) {
        // Initial state before any move executed
        document.getElementById('stepNumText').innerText = `Initial State (0 of ${total})`;
        document.getElementById('stepTitleText').innerText = `Target Move: ${currentPlan.original_move}`;
        document.getElementById('stepDescText').innerText = `Gantry parked at a1. Path: ${currentPlan.path.join(' ➔ ')}`;
        document.getElementById('stepCard').className = 'step-card';
        document.getElementById('uartBadge').style.display = 'none';

        renderBoardState(initialBoard, 'a1', null, null, null);
        return;
      }

      const action = actions[currentStepIdx - 1];
      const stepBoard = stepBoards[currentStepIdx - 1];

      document.getElementById('stepNumText').innerText = `Step ${currentStepIdx} of ${total} [${action.action_type}]`;
      
      let title = "";
      if (action.action_type === 'EVACUATE') title = `Evacuate Blocker ${action.piece} (${action.from_sq} ➔ ${action.to_sq})`;
      else if (action.action_type === 'MOVE_MAIN') title = `Execute Chess Move ${action.from_sq} ➔ ${action.to_sq}`;
      else if (action.action_type === 'RESTORE') title = `Restore Blocker ${action.piece} (${action.from_sq} ➔ ${action.to_sq})`;
      else if (action.action_type === 'HOME_GANTRY') title = `Gantry Return to Origin (${action.to_sq})`;
      else title = `Capture Removal (${action.from_sq} ➔ ${action.to_sq})`;

      document.getElementById('stepTitleText').innerText = title;
      document.getElementById('stepDescText').innerText = action.reason;
      
      const card = document.getElementById('stepCard');
      card.className = 'step-card ' + action.action_type;

      const badge = document.getElementById('uartBadge');
      badge.style.display = 'inline-block';
      badge.innerText = "UART TX: " + (action.action_type === 'HOME_GANTRY' ? `SQ ${action.to_sq}` : `MOVE ${action.from_sq}${action.to_sq}`);

      renderBoardState(stepBoard, action.to_sq, action.from_sq, action.to_sq, action.action_type);
    }

    function nextStep() {
      if (!currentPlan) return;
      if (currentStepIdx < currentPlan.actions.length) {
        currentStepIdx++;
        updateStepUI();
      } else if (autoPlayTimer) {
        toggleAutoPlay();
      }
    }

    function prevStep() {
      if (!currentPlan) return;
      if (currentStepIdx > 0) {
        currentStepIdx--;
        updateStepUI();
      }
    }

    function toggleAutoPlay() {
      const btn = document.getElementById('playBtn');
      if (autoPlayTimer) {
        clearInterval(autoPlayTimer);
        autoPlayTimer = null;
        btn.innerText = "▶ Auto Play";
      } else {
        btn.innerText = "❚❚ Pause";
        autoPlayTimer = setInterval(() => {
          if (currentStepIdx >= currentPlan.actions.length) {
            currentStepIdx = 0;
          }
          nextStep();
        }, 1200);
      }
    }

    function loadPreset(preset) {
      if (autoPlayTimer) toggleAutoPlay();
      if (preset === 'g1f3') requestPlan('g1f3', 'standard');
      else if (preset === 'b1c3') requestPlan('b1c3', 'standard');
      else if (preset === 'e2e4') requestPlan('e2e4', 'standard');
      else if (preset === 'blocked_pawn') requestPlan('e2e4', 'boxed');
      else if (preset === 'd4d8_capture') requestPlan('d4d8', 'capture');
    }

    document.getElementById('planBtn').onclick = () => {
      const m = document.getElementById('moveInput').value.trim();
      if (m) requestPlan(m, 'standard');
    };

    buildEmptyBoardUI();
    loadPreset('g1f3');
  </script>
</body>
</html>
"""


class WebVisualizerServer(SimpleHTTPRequestHandler):
    """Simple embedded web server providing API endpoints and HTML5 visualizer."""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ["/", "/index.html"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
            return

        if parsed.path == "/api/plan":
            params = urllib.parse.parse_qs(parsed.query)
            move_str = params.get("move", ["g1f3"])[0]
            preset = params.get("preset", ["standard"])[0]

            # Construct requested board state
            if preset == "boxed":
                board = BoardState()
                board.set_piece("e2", "P")
                board.set_piece("d2", "X")
                board.set_piece("f2", "X")
                board.set_piece("e1", "X")
                board.set_piece("e3", "X")
            elif preset == "capture":
                board = BoardState()
                board.set_piece("d4", "R")
                board.set_piece("d6", "P_friendly")
                board.set_piece("d8", "q_black")
            else:
                board = BoardState.standard_starting_position()

            planner = MovePlanner(home_square="a1")
            try:
                plan = planner.plan_move(move_str, board)
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

            # Compute step boards for playback
            step_boards = []
            curr = board.clone()
            for act in plan.actions:
                if act.action_type != ActionType.HOME_GANTRY:
                    p = curr.get_piece(act.from_sq) or act.piece
                    curr.clear_square(act.from_sq)
                    curr.set_piece(act.to_sq, p)
                step_boards.append(dict(curr.squares))

            response_data = {
                "plan": {
                    "original_move": plan.original_move,
                    "path": plan.path,
                    "obstructing_squares": plan.obstructing_squares,
                    "parking_assignments": plan.parking_assignments,
                    "actions": [
                        {
                            "action_type": a.action_type.value,
                            "from_sq": a.from_sq,
                            "to_sq": a.to_sq,
                            "piece": a.piece,
                            "reason": a.reason,
                        }
                        for a in plan.actions
                    ],
                },
                "initial_board": dict(board.squares),
                "step_boards": step_boards,
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response_data).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()


def start_web_server(port: int = 8080):
    server = HTTPServer(("127.0.0.1", port), WebVisualizerServer)
    url = f"http://127.0.0.1:{port}"
    print(f"\n{C_BOLD}Starting Chess Gantry Web Visualizer at: {C_GREEN}{url}{C_RESET}")
    print(f"Opening browser... (Press Ctrl+C to stop)\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
        server.server_close()


# ==============================================================================
# CLI Entry Point
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Visualizer for Chess Gantry Move Plans and Obstacle Evacuations."
    )
    parser.add_argument("move", nargs="?", default="g1f3", help="Move to plan and visualize (e.g. g1f3, e2e4)")
    parser.add_argument("--web", action="store_true", help="Launch interactive browser visualizer on localhost:8080")
    parser.add_argument("--port", type=int, default=8080, help="Web server port (default 8080)")
    parser.add_argument("--boxed", action="store_true", help="Use boxed-in pawn scenario instead of standard board")

    args = parser.parse_args()

    if args.web:
        start_web_server(args.port)
        return

    # Terminal CLI mode
    if args.boxed:
        board = BoardState()
        board.set_piece("e2", "P")
        board.set_piece("d2", "X")
        board.set_piece("f2", "X")
        board.set_piece("e1", "X")
        board.set_piece("e3", "X")
    else:
        board = BoardState.standard_starting_position()

    planner = MovePlanner(home_square="a1")
    try:
        plan = planner.plan_move(args.move, board)
    except Exception as e:
        print(f"{C_RED}Error planning move: {e}{C_RESET}")
        sys.exit(1)

    TerminalVisualizer.play_plan_interactive(plan, board)


if __name__ == "__main__":
    main()
