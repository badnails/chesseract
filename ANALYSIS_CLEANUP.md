# Automated Chessboard - Code Debt & Cleanup Findings

## Duplicated Code

### DUP-1: SerialConnection Class (3 identical copies)

The exact same `SerialConnection` class (~80 lines) exists in:
- `chess_orchestrator.py:52-145`
- `chess_gantry_diagnostic.py:62-135`
- `board_diagnostic.py:62-131`

**Fix:** Extract to a shared `serial_connection.py` module. Import everywhere.

### DUP-2: auto_detect_bluetooth_port() (3 identical copies)

Same function in:
- `chess_orchestrator.py:148-162`
- `chess_gantry_diagnostic.py:48-59`
- `board_diagnostic.py:133-144`

**Fix:** Move into the shared serial module.

### DUP-3: Bluetooth Handshake Loop (3 identical copies)

The 8-attempt PING/PONG handshake loop is copy-pasted in:
- `chess_orchestrator.py:219-239`
- `chess_gantry_diagnostic.py:169-188`
- `board_diagnostic.py:215-234`

**Fix:** Make it a method on SerialConnection (e.g. `connect_with_handshake()`).

### DUP-4: ANSI Color Constants (3 identical copies)

`C_RESET`, `C_BOLD`, `C_GREEN`, etc. duplicated across all three Python files.

**Fix:** Move to the shared serial module or a tiny `colors.py`.

### DUP-5: BAUD_RATES Dict (3 identical copies)

Same dict mapping baud rates to termios constants in all three files.

**Fix:** Part of the shared serial module.

### DUP-6: Board Frame Parsing (3 implementations)

Board frame parsing exists in three variants:
- `game_tracker.py:48-71` — `parse_reed_frame()` returns `FrozenSet[str]`
- `board_diagnostic.py:147-165` — `parse_board_frame()` returns `Set[str]`
- `chess_gantry_diagnostic.py:265-279` — method `parse_board_frame()` returns `Set[str]`

All do the same thing with slightly different signatures.

**Fix:** Use the `game_tracker.parse_reed_frame()` version everywhere.

---

## Legacy / Dead Files

These files are earlier iterations superseded by `chessboard_firmware.c` and the orchestrator system:

| File | Lines | Purpose | Superseded By |
|------|-------|---------|---------------|
| `chess.c` | 207 | Reed switch scanning only | `chessboard_firmware.c` |
| `gantry.c` | 535 | Stepper motor control only | `chessboard_firmware.c` |
| `chess_gantry.c` | 569 | Combined scan + gantry | `chessboard_firmware.c` |
| `chess_cli.py` | 346 | Terminal UI for chess.c | `chess_orchestrator.py` |
| `gantry_cli.py` | 510 | Terminal UI for gantry.c | `chess_gantry_diagnostic.py` |
| `move_visualizer.py` | 813 | Visual move planning demo | `move_planner.py` |

**Total dead code: ~2,980 lines**

**Action:** Move to `archive/` folder.

---

## Unnecessary Complexity

### CMPLX-1: StockfishEngine Has Too Many Options

`stockfish_runner.py` constructor takes `skill_level`, `threads`, `hash_mb` — none of which the orchestrator ever changes from defaults. The `movetime_ms` parameter in `get_best_move()` is never used. `get_best_move()` and `get_evaluation_and_best_move()` are nearly identical — the only difference is the latter also parses the eval score.

**Fix:** Remove `get_best_move()` (unused), remove `movetime_ms` parameter, hardcode `threads=2` and `hash_mb=32` as constants.

### CMPLX-2: Orchestrator Has Redundant Modes

`chess_orchestrator.py` has `--dry-run` AND `--simulate`. Dry-run skips serial writes but still needs hardware init. Simulate skips everything. The diagnostic tool (`chess_gantry_diagnostic.py`) already provides a better interactive experience than `--simulate`.

**Fix:** Keep `--simulate` for offline testing without hardware. Remove `--dry-run` since the diagnostic tool covers that use case.

### CMPLX-3: MovePlanner Graveyard Parameter

`plan_move()` takes `graveyard_sq="h1"` but the graveyard concept (border cells for captured pieces) is postponed. Currently, captured pieces get "moved to h1" which overwrites whatever is on h1 (the white rook at game start!).

**Fix:** For now, since capture removal to border cells is deferred, handle captures by simply removing the captured piece from tracking. The human physically removes captured pieces.

### ~~CMPLX-4: BoardState Exists in Two Forms~~ — **DONE**: removed `BoardState` class entirely. `MovePlanner` now accepts `Dict[str, str]` directly. Orchestrator passes `tracker.board` with no conversion.

---

## Firmware Debt

### ~~FW-1: snprintf Bloat~~ — **DONE**: replaced `snprintf`/`stdio.h` with `uart_put_int()`/`uart_put_uint()` + PROGMEM strings. Saved ~3.7KB flash (8,593→4,859 bytes).

### ~~FW-2: Unused Float Math~~ — **DONE**: all kinematics converted to `uint16_t`/`int16_t` integer math. No floats remain in firmware.

### ~~FW-3: Command Parser Fragility~~ — **DONE**: removed redundant direct 4-char algebraic handler. All moves go through unified MOVE command.

### ~~FW-4: No Command Echo~~ — **DONE**: firmware now echoes `RX:<cmd>\r\n` before executing every command.

---

## Recommended Cleanup Priority

1. ~~**Fix a1 offset** (BUG-1)~~ — Not a bug; gantry zeroed at a1
2. **Fix delay_microseconds overhead** (BUG-3) — directly affects usability / slow movement
3. **Fix magnet timing** (BUG-2) — reliability of piece transfer
4. ~~**Extract shared serial code** (DUP-1 through DUP-6)~~ — **DONE**: `serial_connection.py` created; all 3 consumers import from it
5. ~~**Archive legacy files**~~ — **DONE**: 6 files (~2,980 lines) moved to `archive/`
6. ~~**Remove unused Stockfish options** (CMPLX-1)~~ — **DONE**: removed `get_best_move()`, `movetime_ms`, `skill_level`, `threads`, `hash_mb` params
7. ~~**Remove --dry-run** (CMPLX-2)~~ — **DONE**: removed redundant mode
8. ~~**Fix Stockfish race** (BUG-5)~~ — **DONE**: added `isready`/`readyok` between position and go
9. **Fix en passant** (ISSUE-9) — correctness
