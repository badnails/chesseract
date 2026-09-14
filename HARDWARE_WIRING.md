# Complete hardware wiring: ATmega32 + 8x8 reed chessboard + 4x 16-channel muxes

This guide matches the firmware in `chess.c` and monitor in `uart_monitor.py`. It assumes:

- ATmega32 in the 40-pin DIP package
- four 16-channel CD74HC4067 multiplexer modules labeled `C0` through `C15`, `SIG` (or `COM`), `S0` through `S3`, `EN`, `VCC`, and `GND`
- 64 normally-open reed switches (one for each square of an 8x8 chessboard)
- one normally-open push button for the scan trigger
- a logic-level USB-to-UART adapter (3.3V or 5V matching MCU VCC)
- a USBasp programmer
- a regulated 5V (or compatible) power supply

## Overall connection diagram

```text
                           ATmega32 (DIP-40)
                        +----------------------+
       mux S0 (common) < PA0, pin 40          |
       mux S1 (common) < PA1, pin 39          |
       mux S2 (common) < PA2, pin 38          |
       mux S3 (common) < PA3, pin 37          |
         mux 0 SIG0 ---> PA4, pin 36          |
         mux 1 SIG1 ---> PA5, pin 35          |
         mux 2 SIG2 ---> PA6, pin 34          |
         mux 3 SIG3 ---> PA7, pin 33          |
                        |                      |
      scan button to GND > PD2/INT0, pin 16    |
                        |                      |
      USB-UART RX <----- PD1/TXD, pin 15       |
      USB-UART TX -----> PD0/RXD, pin 14       |
                        |                      |
                        | VCC pin 10 ---- +5V  |
                        | AVCC pin 30 --- +5V  |
                        | GND pins 11,31- GND  |
                        +----------------------+

    +-----------------+   +-----------------+   +-----------------+   +-----------------+
    | MUX 0 (Files a,b)|   | MUX 1 (Files c,d)|   | MUX 2 (Files e,f)|   | MUX 3 (Files g,h)|
    +-----------------+   +-----------------+   +-----------------+   +-----------------+
S0 <--- PA0 (pin 40) <---------- PA0 <----------------- PA0 <----------------- PA0
S1 <--- PA1 (pin 39) <---------- PA1 <----------------- PA1 <----------------- PA1
S2 <--- PA2 (pin 38) <---------- PA2 <----------------- PA2 <----------------- PA2
S3 <--- PA3 (pin 37) <---------- PA3 <----------------- PA3 <----------------- PA3
SIG ---> PA4 (pin 36) | SIG ---> PA5 (pin 35) | SIG ---> PA6 (pin 34) | SIG ---> PA7 (pin 33)
EN  ---> GND          | EN  ---> GND          | EN  ---> GND          | EN  ---> GND
VCC ---> +5V          | VCC ---> +5V          | VCC ---> +5V          | VCC ---> +5V
GND ---> GND          | GND ---> GND          | GND ---> GND          | GND ---> GND
C0..C7:  a1..a8       | C0..C7:  c1..c8       | C0..C7:  e1..e8       | C0..C7:  g1..g8
C8..C15: b1..b8       | C8..C15: d1..d8       | C8..C15: f1..f8       | C8..C15: h1..h8
```

Every device (MCU, all 4 muxes, USB-UART adapter, programmer) must share a common ground.

## 1. ATmega32 power and support connections

Disconnect power before changing wiring.

| ATmega32 pin | Physical pin | Connect to |
|---|---:|---|
| VCC | 10 | regulated 5V positive |
| GND | 11 | ground |
| AVCC | 30 | regulated 5V positive (required for Port A) |
| GND | 31 | ground |
| AREF | 32 | 100 nF capacitor to ground |
| RESET | 9 | 10 kOhm pull-up resistor to 5V |

**Important**: AVCC must be powered even though the ADC is not being used; Port A will not operate reliably without it.

Place these decoupling capacitors close to the pins:
- 100 nF ceramic capacitor between VCC (pin 10) and GND (pin 11)
- 100 nF ceramic capacitor between AVCC (pin 30) and GND (pin 31)
- 100 nF ceramic capacitor across each multiplexer module's VCC and GND pins
- 10 µF electrolytic capacitor across the main power rails

The build assumes the MCU runs at 1 MHz (factory default internal RC oscillator).

## 2. Multiplexer connections

All four CD74HC4067 multiplexer modules share the 4 address select lines:

| Mux signal | Connect to ATmega32 | Physical pin | Notes |
|---|---|---:|---|
| S0 (all 4 muxes) | PA0 | 40 | Least-significant address bit |
| S1 (all 4 muxes) | PA1 | 39 | Address bit 1 |
| S2 (all 4 muxes) | PA2 | 38 | Address bit 2 |
| S3 (all 4 muxes) | PA3 | 37 | Most-significant address bit |
| EN (all 4 muxes) | GND | - | Active-low enable (ground to enable) |
| VCC (all 4 muxes)| +5V | - | Shared MCU power supply |
| GND (all 4 muxes)| GND | - | Common ground |

Each multiplexer has its own separate output SIG line connected to Port A:

| Multiplexer | Signal line | ATmega32 signal | Physical pin | Board files covered |
|---|---|---|---:|---|
| Mux 0 (leftmost) | SIG | PA4 | 36 | Files `a` (C0..C7) and `b` (C8..C15) |
| Mux 1 (second)   | SIG | PA5 | 35 | Files `c` (C0..C7) and `d` (C8..C15) |
| Mux 2 (third)    | SIG | PA6 | 34 | Files `e` (C0..C7) and `f` (C8..C15) |
| Mux 3 (fourth)   | SIG | PA7 | 33 | Files `g` (C0..C7) and `h` (C8..C15) |

The firmware configures `PA0..PA3` as outputs and `PA4..PA7` as inputs with internal pull-ups enabled.

```text
reed switch open (empty)      -> SIG reads HIGH -> reported as '0'
reed switch closed (occupied) -> SIG reads LOW  -> reported as '1'
```

## 3. Reed-switch 8x8 chessboard wiring

Connect one terminal of each reed switch to its designated multiplexer channel `C0..C15` and the other terminal to common GND. Reed switches are non-polarized.

### Full 64-square channel allocation table

| Channel | S3..S0 | Mux 0 (PA4) | Mux 1 (PA5) | Mux 2 (PA6) | Mux 3 (PA7) | Other lead |
|---|---|---|---|---|---|---|
| **C0**  | 0000 | **a1** | **c1** | **e1** | **g1** | GND |
| **C1**  | 0001 | **a2** | **c2** | **e2** | **g2** | GND |
| **C2**  | 0010 | **a3** | **c3** | **e3** | **g3** | GND |
| **C3**  | 0011 | **a4** | **c4** | **e4** | **g4** | GND |
| **C4**  | 0100 | **a5** | **c5** | **e5** | **g5** | GND |
| **C5**  | 0101 | **a6** | **c6** | **e6** | **g6** | GND |
| **C6**  | 0110 | **a7** | **c7** | **e7** | **g7** | GND |
| **C7**  | 0111 | **a8** | **c8** | **e8** | **g8** | GND |
| **C8**  | 1000 | **b1** | **d1** | **f1** | **h1** | GND |
| **C9**  | 1001 | **b2** | **d2** | **f2** | **h2** | GND |
| **C10** | 1010 | **b3** | **d3** | **f3** | **h3** | GND |
| **C11** | 1011 | **b4** | **d4** | **f4** | **h4** | GND |
| **C12** | 1100 | **b5** | **d5** | **f5** | **h5** | GND |
| **C13** | 1101 | **b6** | **d6** | **f6** | **h6** | GND |
| **C14** | 1110 | **b7** | **d7** | **f7** | **h7** | GND |
| **C15** | 1111 | **b8** | **d8** | **f8** | **h8** | GND |

## 4. Interrupt scan trigger button

```text
ATmega32 PD2 / INT0 (physical pin 16) ---- push button ---- GND
```

The firmware enables PD2's internal pull-up. Pressing the button pulls PD2 low, generating an `INT0` falling-edge interrupt. The ISR flags the request, and `main()` handles 25 ms debouncing, scans all 64 squares in 16 multiplexer steps, and transmits the UART frame.

## 5. USB-to-UART adapter

Use a 5V/3.3V TTL USB-to-UART adapter (e.g. CP2102, FT232, CH340).

| ATmega32 pin | Physical pin | USB-UART adapter pin |
|---|---:|---|
| PD1 / TXD | 15 | RX / RXD |
| PD0 / RXD | 14 | TX / TXD (optional, for future host commands) |
| GND | 11 or 31 | GND (common ground) |

Settings: 9600 baud, 8 data bits, no parity, 1 stop bit (8N1).

## 6. USBasp programmer

| USBasp signal | ATmega32 signal | Physical pin |
|---|---|---:|
| MOSI | PB5 / MOSI | 6 |
| MISO | PB6 / MISO | 7 |
| SCK | PB7 / SCK | 8 |
| RESET | RESET | 9 |
| VCC | VCC (only if powering target from USBasp) | 10 |
| GND | common ground | 11 |

## 7. Power-up and verification checklist

1. Check for power-to-ground shorts with a multimeter before plugging in USB.
2. Confirm AVCC (pin 30) is connected to 5V.
3. Ground the EN pins on all 4 multiplexers.
4. Run `./RUN_CHESS.SH` to compile and flash `chess.hex`.
5. Run `python3 uart_monitor.py --list` to locate your Mac's serial port.
6. Run `python3 uart_monitor.py /dev/cu.usbserial-110`.
7. Ground PD2: you should see the 8x8 board rendered in your terminal.
