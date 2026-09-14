# ATmega32 8x8 reed-grid chessboard scanner
# ATmega32 8x8 reed-grid chessboard scanner & live broadcast
# ATmega32 8x8 Reed-Grid Chessboard Scanner & Analysis Workbench

This firmware scans 64 reed switches through four 16-channel analog multiplexers (CD74HC4067) and transmits the complete board state over UART to a Mac. Closed reed switches (magnets present) are reported as `1`; open switches are reported as `0`.
This repository contains the embedded firmware and Mac host software for an 8x8 physical chessboard with 64 reed switches and 4x 16-channel multiplexers (CD74HC4067).

For a complete pin-by-pin schematic and breadboard wiring guide, see `HARDWARE_WIRING.md`.
The companion Python monitor assigns pieces on starting setup, tracks piece movements, captures, castling, promotions, writes standard FEN/PGN files, and serves an interactive web broadcast with an OBS Studio livestream overlay.
The Mac host software provides an interactive, local desktop analysis workbench featuring **Lichess Chessground**, **Chess.js**, **Stockfish (WASM)**, and real-time physical move tracking with audible move sounds.

For a complete pin-by-pin schematic and breadboard wiring guide, see `HARDWARE_WIRING.md` and `ATMEGA_CONNECTIONS.md`.
For hardware pinout and schematics, refer to:
- [`HARDWARE_WIRING.md`](file:///Users/badnails/Desktop/UART_ATMEGA/HARDWARE_WIRING.md)
- [`ATMEGA_CONNECTIONS.md`](file:///Users/badnails/Desktop/UART_ATMEGA/ATMEGA_CONNECTIONS.md)

---

## Multiplexer and trigger pinout
## 1. Multiplexer & Trigger Pinout

The 4 multiplexers share the 4 address select lines and produce 4 signal outputs:
The 4 multiplexers share 4 address select lines and produce 4 signal lines connected to Port A of the ATmega32:

| Signal | ATmega32 pin | Physical pin | Description |
| Signal | ATmega32 Pin | Physical Pin | Description |
|---|---|---:|---|
| S0 | PA0 | 40 | Common mux select bit 0 (LSB) |
| S1 | PA1 | 39 | Common mux select bit 1 |
| S2 | PA2 | 38 | Common mux select bit 2 |
| S3 | PA3 | 37 | Common mux select bit 3 (MSB) |
| SIG0 | PA4 | 36 | Mux 0: Files `a` (C0..C7) and `b` (C8..C15) |
| SIG1 | PA5 | 35 | Mux 1: Files `c` (C0..C7) and `d` (C8..C15) |
| SIG2 | PA6 | 34 | Mux 2: Files `e` (C0..C7) and `f` (C8..C15) |
| SIG3 | PA7 | 33 | Mux 3: Files `g` (C0..C7) and `h` (C8..C15) |
| Scan button | PD2 / INT0 | 16 | Push button to GND (triggers scan) |
| UART TX | PD1 / TXD | 15 | Serial output to Mac USB-UART RX |
| UART RX | PD0 / RXD | 14 | Serial input from Mac USB-UART TX |
| **S0** | PA0 | 40 | Common mux address bit 0 (LSB) |
| **S1** | PA1 | 39 | Common mux address bit 1 |
| **S2** | PA2 | 38 | Common mux address bit 2 |
| **S3** | PA3 | 37 | Common mux address bit 3 (MSB) |
| **SIG0** | PA4 | 36 | Mux 0: Files `a` (C0..C7) and `b` (C8..C15) |
| **SIG1** | PA5 | 35 | Mux 1: Files `c` (C0..C7) and `d` (C8..C15) |
| **SIG2** | PA6 | 34 | Mux 2: Files `e` (C0..C7) and `f` (C8..C15) |
| **SIG3** | PA7 | 33 | Mux 3: Files `g` (C0..C7) and `h` (C8..C15) |
| **Scan Button** | PD2 / INT0 | 16 | Push button to GND (triggers scan) |
| **UART TX** | PD1 / TXD | 15 | Serial output to Mac USB-UART RX (9600 baud) |
| **UART RX** | PD0 / RXD | 14 | Serial input from Mac USB-UART TX |

All 4 multiplexers have their `EN` (enable) pins grounded. The ATmega enables internal pull-ups on `PA4..PA7`:
- Open reed switch: Pin reads `1` (High) $\rightarrow$ `0` in UART message (empty).
- Closed reed switch: Pin reads `0` (GND) $\rightarrow$ `1` in UART message (occupied).
- Open reed switch (empty) $\rightarrow$ Pin reads `1` (High) $\rightarrow$ `0` in UART message.
- Closed reed switch (magnet present) $\rightarrow$ Pin reads `0` (GND) $\rightarrow$ `1` in UART message.

---

## UART board format
## 2. UART Board Format

One frame is sent automatically at power-up/reset, and subsequently after each `PD2` button press:
The ATmega transmits a 64-square occupancy frame upon boot/reset and on every push of the `PD2` button:

```text
BOARD:11111111/11111111/00000000/00000000/00000000/00000000/11111111/11111111
```

- Exactly 8 slash-separated rank groups: `rank 1` through `rank 8`.
- Inside each group: 8 characters (`0` or `1`) representing files `a` through `h`.
- Total line length: 79 bytes (72 characters + `\r\n`).
- Rate: 9600 baud, 8N1.
- 9600 baud, 8 data bits, no parity, 1 stop bit (8N1).

## Mac companion monitor (`uart_monitor.py`)
---

The companion Python script requires no external dependencies (pure Python standard library using `termios`, `fcntl`, and `select`).
## Mac Companion Broadcast Monitor (`uart_monitor.py`)
## 3. Local Desktop Analysis Workbench (`uart_monitor.py`)

### List serial ports:
The companion Python monitor requires zero external pip dependencies.
The companion Python monitor runs a local web server providing an interactive analysis workbench in your Mac browser.

```sh
python3 uart_monitor.py --list
```
### Features
1. **Piece Tracking**: Assigns standard starting pieces (White R, N, B, Q, K, B, N, R / Pawns; Black r, n, b, q, k, b, n, r / pawns).
2. **Move Inferences**:
   - Single-step and two-step moves (e.g. piece lifted, then placed).
   - Castling (`O-O` and `O-O-O` with automatic King and Rook moves).
   - Captures with captured pieces graveyard rack.
   - Pawn promotions (defaults to Queen).
3. **Live File Exports**:
   - `game.pgn`: Automatically updated on every move.
   - `live.fen`: Current board position in standard Forsyth-Edwards Notation.
4. **Built-in Web Broadcast Server** (`http://localhost:8080`):
   - Interactive live chessboard with smooth piece movement animations.
   - Move history table with SAN notation (e.g. `1. e4 e5 2. Nf3`).
   - Turn indicator badge and captured piece counters.
   - FEN & PGN copy buttons.
5. **OBS Studio Stream Overlay** (`http://localhost:8080/?overlay=1`):
   - Transparent background mode for Twitch / YouTube livestreams.
### Key Capabilities
1. **Lichess Chessground Board**:
   - Ultra-smooth 60 FPS piece movement animations matching your physical board actions.
   - Highlights source and destination squares of the last move.
2. **Real-time Stockfish (WASM)**:
   - Evaluates positions in a dedicated background browser worker.
   - **Best-Move Arrow**: Draws a green SVG vector arrow directly on Chessground showing the computer's recommended move.
   - **Live Vertical Eval Bar**: Smooth animated bar showing centipawns (`+1.4`) or forced mate (`M3`).
   - **Engine Stats Panel**: Displays search depth, nodes per second, and principal variation (PV line).
3. **Audible Move Feedback**:
   - Synthesizes realistic wooden piece-tap and capture sounds using the browser Web Audio API.
4. **Partial Initialization Support**:
   - Bench-test individual rows (e.g. bottom row magnets only). The workbench adapts without crashing the engine.
5. **Zero Dependencies & 100% Offline**:
   - All libraries (`Chessground`, `Chess.js`, `Stockfish WASM`) are vendored locally in `web_ui/`. No Node.js or `npm install` needed.

### Run self-test simulation:
### Commands
---

## 4. How to Run

### Test Simulation (No hardware needed)
### Interactive Software Simulation Mode (No hardware needed)
You can run the full system in simulation mode listening on **STDIN**, allowing you or another process to pipe or type board states and moves interactively while the browser workbench at `http://localhost:8080` updates live:

```sh
# 1. Run simulation self-test:
python3 uart_monitor.py --simulate
```

In this mode, you can:
- Type raw 64-switch frames: `BOARD:11111111/11110111/...`
- Type moves directly: `e2e4`, `e7e5`, `g1f3`
- Type `row1` to test the partial 8-piece setup (`a1..h1`)
- Type `reset` or `start` to reset to the standard starting position
- Or pipe moves from an automated testing script:
  ```sh
  python3 test_script.py | python3 uart_monitor.py --simulate
  ```

### Quick Self-Test
```sh
python3 uart_monitor.py --self-test
```

### Start monitoring serial port:
# 2. List available serial ports:
python3 uart_monitor.py --list

```sh
# 3. Start live monitor on your Mac:
python3 uart_monitor.py /dev/cu.usbserial-110

# 4. Custom web server port (default is 8080):
python3 uart_monitor.py /dev/cu.usbserial-110 --port 8085
```

### Display and move output:
---

When a frame is received, the monitor renders the 8x8 board to `stderr`:
## Live Stream & Software Integration Guide

```text
    +-----------------+
  8 |[#][#][#][#][#][#][#][#]|
  7 |[#][#][#][#] . [#][#][#]|
  6 | .  .  .  .  .  .  .  . |
  5 | .  .  .  .  .  .  .  . |
  4 | .  .  .  . [#] .  .  . |
  3 | .  .  .  .  .  .  .  . |
  2 |[#][#][#][#] . [#][#][#]|
  1 |[#][#][#][#][#][#][#][#]|
    +-----------------+
      a  b  c  d  e  f  g  h
### 1. OBS Studio (Twitch / YouTube Livestreams)
How to display the digital board on stream:
1. In OBS Studio, click **+** under **Sources** and select **Browser**.
2. Set the URL to:
### Run Live with Physical Board
1. List available serial ports:
   ```sh
   python3 uart_monitor.py --list
   ```
2. Start the monitor and workbench server:
   ```sh
   python3 uart_monitor.py /dev/cu.usbserial-110
   ```
3. Open your browser to:
   ```text
   http://localhost:8080/?overlay=1
   http://localhost:8080
   ```
3. Set Width to `600` and Height to `600`.
4. The board appears with a clean transparent background floating over your video feed. Moving a physical piece and tapping the scan button moves the piece on stream live!
4. Move pieces on your physical chessboard and tap the scan button!

[2026-09-13T03:53:09+06:00] BOARD: 32 pieces present: a1,a2,a7,a8,...
[2026-09-13T03:53:09+06:00] STATE: move inferred: e2e4
```
### 2. ChessConnect (Chrome Extension for Chess.com & Lichess)
- **ChessConnect** is a free browser extension that connects electronic boards to Chess.com and Lichess.
- It translates physical board movements directly into clicks on Chess.com / Lichess so you can play online games with your physical board.

And prints clean UCI move notation to `stdout`:
```text
e2e4
```
### 3. Lichess Live Broadcasts (`lichess.org/broadcast`)
- Lichess provides official tournament broadcast tools.
- A live study/broadcast can be created on Lichess, and a simple POST request pushes `game.pgn` to Lichess's open Broadcast API so anyone around the world can watch your physical game live with Stockfish evaluation.

This enables easy piping into chess engines like Stockfish or GNU Chess:
```sh
python3 uart_monitor.py /dev/cu.usbserial-110 | stockfish
```
### 4. Desktop Chess GUIs (Cutechess, Arena, BanksiaGUI, Scid vs PC)
- These desktop programs can automatically reload `game.pgn` or `live.fen` whenever it is modified on disk.
- Enables real-time computer analysis (Stockfish eval bar) alongside your physical game.

## Build and flash
---

## Build and Flash Firmware
## 5. Build and Flash Microcontroller

```sh
chmod +x RUN_CHESS.SH
./RUN_CHESS.SH
```
