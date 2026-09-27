# Automated Chessboard - Bug Analysis & Findings

## Critical Bugs

### ~~BUG-1: Gantry Position Offset — a1 Is Not at (0,0)~~ **NOT A BUG**

**Files:** `chessboard_firmware.c:386-398`

**Reassessed:** The gantry is manually positioned at a1 before startup, then `ZERO` sets that position as (0,0). So `parse_square()` mapping a1 → (0,0) is correct — the coordinate origin matches the physical a1 position.

**Note:** This means the gantry cannot reach negative coordinates (border cells below rank 1 / left of file a). When graveyard/parking on border cells is implemented, the zero point will need to move to the board corner and offsets will need to be added.

---

### BUG-2: Premature Magnet Release / Unreliable Electromagnet Timing

**Files:** `chessboard_firmware.c:400-422`

The `execute_chess_move()` function has these timing issues:

1. **Magnet engage delay too short (250 ms):** The electromagnet relay has mechanical actuation time, plus the magnetic field needs time to build enough force to grip through the board. 250 ms may not be enough for a reliable grip, especially with thicker board material.

2. **Magnet release delay too short (200 ms):** After releasing, the firmware immediately sends `OK MOVE COMPLETE`. The Python side then fires the next UART command. If the relay hasn't fully released, the piece may get dragged.

3. **`move_orthogonal_mm()` disables motors after every move** (line 383: `motors_enable(false)`). This adds 100+ ms of overhead. For multi-step sequences, the motor driver keeps cycling enable/disable, which can cause position drift due to missed steps on re-enable.

4. **`SQ` command turns magnet OFF** (line 483: `magnet_set(false)`), which is correct for empty traversal but means HOME_GANTRY always sends a redundant magnet-off.

---

### BUG-3: Slow Gantry Movement

**Files:** `chessboard_firmware.c:311-384`

Multiple causes:

1. **`delay_microseconds()` loop overhead (line 311-316):** Each iteration calls `_delay_us(1)` in a while loop. At 1 MHz clock, each loop iteration has ~4-5 cycles overhead beyond the 1 µs delay. A 200 µs cruise delay actually takes ~300+ µs. This makes the motor run ~50% slower than configured.

2. **Motor enable/disable per axis move:** `move_orthogonal_mm()` does X-axis, waits 30 ms, does Y-axis, waits 30 ms, then waits 100 ms and disables motors. Per UART command, that's 160 ms of dead time minimum.

3. **Sequential axis movement:** X moves first, then Y. No diagonal motion. A move from a1 to h8 takes (7 squares × X) + (7 squares × Y) instead of simultaneous motion. This is a design limitation of single-axis stepping but the delays between axes are unnecessarily large.

4. **Acceleration ramp is limited to 80 steps** (line 333). At 1/8 microstepping with 40 steps/mm, that's only 2 mm of acceleration. For a 280 mm traverse (7 squares), the motor spends almost all its time at cruise speed, which is fine — but the hard cap means very short moves (1-2 squares) get minimal acceleration and sound rough.

---

### BUG-4: Gantry Doesn't Return to a1

**Files:** `chess_orchestrator.py:265-297`

If any step in `execute_physical_plan()` fails or times out (line 291-294), the function returns `False` immediately. The HOME_GANTRY action at the end of the plan never executes. The gantry stays wherever it stopped.

Additionally, the `send_and_wait_ack` timeout of 60 seconds (line 291) is generous, but if Bluetooth drops a character and "OK" never arrives, the entire sequence halts with no recovery.

---

### BUG-5: Stockfish Position/Search Race

**Files:** `stockfish_runner.py:101-116`

`get_evaluation_and_best_move()` sends `position fen ...` then immediately sends `go depth ...` without waiting for Stockfish to confirm it processed the position. UCI protocol recommends sending `isready` and waiting for `readyok` between position and search commands. In practice, Stockfish usually handles this fine due to pipe buffering, but under heavy load or complex positions this could cause Stockfish to search from the wrong position.

---

## Moderate Issues

### ISSUE-6: Unnecessary Timeouts and Delays

**Files:** `stockfish_runner.py:95, chess_orchestrator.py:131`

- `_wait_for` in Stockfish has a 5-second default timeout. UCI init is fast, but `isready` after setting options can take time if Stockfish is allocating hash tables.
- `send_and_wait_ack` defaults to 25 seconds. Most commands complete in <2 seconds. The timeout itself isn't harmful but masks real issues when things go wrong — you wait 25 seconds before learning a command failed.

### ISSUE-7: FEN Separator

**Files:** `game_tracker.py:178`

The FEN uses `/` as rank separator, which is standard. However, `get_fen()` joins rows with `'/'.join(rows)`, producing `rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR`. This is correct. No bug here — Stockfish accepts standard FEN.

### ISSUE-8: Halfmove Clock Never Increments

**Files:** `game_tracker.py:114, 180-290`

`halfmove_clock` is initialized to 0 and never updated in `apply_move()`. It should reset to 0 on pawn moves and captures, and increment otherwise. This affects FEN correctness but Stockfish doesn't rely on it for move selection (only for draw claims).

### ISSUE-9: En Passant Capture Not Handled

**Files:** `game_tracker.py:216-219`

When a pawn captures en passant, the captured pawn is on a different square than the destination. The code uses `target_sq = victim_sq or dst`, so if `victim_sq` isn't passed, it looks for a capture on the destination square — which is empty in en passant. The en passant pawn won't be removed from the board dict.

The `ingest_reed_frame()` sensor-based detection wouldn't catch this either, since en passant shows as 2 removed (attacker + captured pawn) and 1 added (attacker on new square), which doesn't match any of the detection patterns.

---

## Performance Notes

### Motor Speed at 1 MHz CPU

At `F_CPU = 1000000UL` (1 MHz), the ATmega32 executes ~1 million instructions per second. The `delay_microseconds()` function using a while loop with `_delay_us(1)` has significant loop overhead:

```
Each iteration: _delay_us(1) + loop decrement + compare + branch ≈ 5-7 cycles ≈ 5-7 µs
Configured 200 µs delay → actual ~1000-1400 µs
```

This means the motor runs **5-7× slower than the configured speed**. At 1/8 microstepping (40 steps/mm) and actual ~1000 µs per step, moving one square (40 mm = 1600 steps) takes ~1.6 seconds per axis. A diagonal move across the board could take 20+ seconds.

**Fix options:**
1. Use `_delay_us(200)` directly instead of the loop (requires compile-time constant)
2. Use a timer-based approach with OCR compare match
3. Increase F_CPU if the crystal supports it
