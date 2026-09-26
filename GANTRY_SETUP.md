# Dual NEMA 17 & DRV8825 XY Gantry Setup Guide (ATmega32 + UART)

This guide details the complete hardware wiring, driver configuration, firmware flashing, and laptop operation for testing the two NEMA 17 stepper motors on your **Andrew Sleigh v2 Cartesian XY Pen Plotter**.

---

## 1. Hardware Pin Connections

All motor and driver control signals are routed to **PORTC (pins 22–29)** of the ATmega32 DIP-40 chip.

### ATmega32 DIP-40 Pinout Reference

```text
                      ATmega32 (DIP-40)
                   +---------\_/---------+
         (nc) --- |  1 (PB0)   (PA0) 40 | --- (nc)
         (nc) --- |  2 (PB1)   (PA1) 39 | --- (nc)
         (nc) --- |  3 (PB2)   (PA2) 38 | --- (nc)
         (nc) --- |  4 (PB3)   (PA3) 37 | --- (nc)
         (nc) --- |  5 (PB4)   (PA4) 36 | --- (nc)
  USBasp MOSI --- |  6 (PB5)   (PA5) 35 | --- (nc)
  USBasp MISO --- |  7 (PB6)   (PA6) 34 | --- (nc)
   USBasp SCK --- |  8 (PB7)   (PA7) 33 | --- (nc)
 RESET (10k)  --- |  9 (RST)     (AREF) 32 | --- 100 nF cap to GND
         +5V  --- | 10 (VCC)     (AVCC) 31 | --- GND
         GND  --- | 11 (GND)      (GND) 30 | --- +5V
         (nc) --- | 12 (XTAL2)   (PC7)  29 | ---> MODE2 (Shared M2)
         (nc) --- | 13 (XTAL1)   (PC6)  28 | ---> MODE1 (Shared M1)
 USB-UART TX  --->| 14 (PD0)     (PC5)  27 | ---> MODE0 (Shared M0)
 USB-UART RX  <---| 15 (PD1)     (PC4)  26 | ---> ENABLE (Shared EN)
         (nc) --- | 16 (PD2)     (PC3)  25 | ---> DIR_Y (Motor Y Direction)
         (nc) --- | 17 (PD3)     (PC2)  24 | ---> STEP_Y (Motor Y Step Pulse)
         (nc) --- | 18 (PD4)     (PC1)  23 | ---> DIR_X (Motor X Direction)
         (nc) --- | 19 (PD5)     (PC0)  22 | ---> STEP_X (Motor X Step Pulse)
         (nc) --- | 20 (PD6)     (PD7)  21 | --- (nc)
                   +---------------------+
```

---

## 2. DRV8825 Module Wiring

Both DRV8825 carrier boards (Motor X and Motor Y) share `ENABLE`, `MODE0`, `MODE1`, `MODE2`, and common ground.

### Motor X Driver (Carriage)
| DRV8825 Pin | Connect To | Details |
|:---|:---|:---|
| **EN** | ATmega32 `PC4` (pin 26) | Shared enable (Active LOW: 0 = on, 1 = relaxed) |
| **M0** (MODE0) | ATmega32 `PC5` (pin 27) | Microstep select bit 0 |
| **M1** (MODE1) | ATmega32 `PC6` (pin 28) | Microstep select bit 1 |
| **M2** (MODE2) | ATmega32 `PC7` (pin 29) | Microstep select bit 2 |
| **RST** | Connect to **SLP** pin | Tie RST and SLP together on the driver board |
| **SLP** | Connect to **RST** pin | Keeps driver awake |
| **STEP** | ATmega32 `PC0` (pin 22) | Step pulse input |
| **DIR** | ATmega32 `PC1` (pin 23) | Direction input |
| **GND** (Logic) | GND Rail | Common ground with ATmega32 & Power supply |
| **FAULT** | Unconnected | Fault output |
| **A1, A2** | NEMA17 Motor Phase A | Typically Black / Green wires |
| **B1, B2** | NEMA17 Motor Phase B | Typically Red / Blue wires |
| **GND** (Motor) | Motor Supply Negative | Common GND |
| **VMOT** | Motor Supply Positive (+12V to +24V) | **Must place $100\,\mu\text{F}$ capacitor across VMOT and GND** |

### Motor Y Driver (Arm)
Same as above, except:
* **STEP** connects to ATmega32 `PC2` (pin 24).
* **DIR** connects to ATmega32 `PC3` (pin 25).
* `EN`, `M0`, `M1`, `M2` share the exact same pins with Motor X (`PC4`, `PC5`, `PC6`, `PC7`).

---

## 3. Current Limit Verification ($V_{ref}$)

For DRV8825 boards with standard $0.10\,\Omega$ ($R100$) current sense resistors:
$$I_{trip} = \frac{V_{ref}}{2 \times R_{sense}} = \frac{0.75\text{ V}}{0.20\,\Omega} = 1.50\text{ A}$$

* **Motor rating**: 1.7A per phase.
* **Operating current**: 1.50A is optimal—it delivers plenty of torque for the gantry belts without over-stressing the motor coils or driver FETs.
* **Heatsink**: Ensure small aluminum heatsinks are attached to the DRV8825 chips.

---

## 4. Kinematics & Microstepping Reference

* **X Axis**: GT2 belt ($2\text{ mm}$ pitch) with **20-tooth pulley**:
  $$\text{Circumference} = 20 \times 2\text{ mm} = 40.0\text{ mm / rev}\quad \longrightarrow\quad \text{Base: } 5.0\text{ full steps / mm}$$
* **Y Axis**: GT2 belt ($2\text{ mm}$ pitch) with **30-tooth pulley**:
  $$\text{Circumference} = 30 \times 2\text{ mm} = 60.0\text{ mm / rev}\quad \longrightarrow\quad \text{Base: } 3.3333\text{ full steps / mm}$$
* **Y Axis Movable Travel Limit**: **$42\text{ cm} = 420.0\text{ mm}$** (enforced by software limits).

| Mode | Microstep | X Steps / mm (20T) | Y Steps / mm (30T) |
|:---:|:---:|:---:|:---:|
| `m 1` | Full Step (1/1) | 5.0 | 3.33 |
| `m 2` | Half Step (1/2) | 10.0 | 6.67 |
| `m 4` | 1/4 Step | 20.0 | 13.33 |
| `m 8` | 1/8 Step | 40.0 | 26.67 |
| `m 16` | **1/16 Step (Default)** | 80.0 | 53.33 |
| `m 32` | 1/32 Step | 160.0 | 106.67 |

---

## 5. Compiling and Flashing Firmware

1. Connect your **USBasp** programmer to ATmega32 pins:
   * `PB5` (pin 6) $\rightarrow$ MOSI
   * `PB6` (pin 7) $\rightarrow$ MISO
   * `PB7` (pin 8) $\rightarrow$ SCK
   * `RESET` (pin 9) $\rightarrow$ RESET
   * `VCC` & `GND`
2. Run the build and flash script:
   ```bash
   ./build_gantry.sh --flash
   ```

---

## 6. Operating from Laptop (Python Controller)

The Python CLI (`gantry_cli.py`) runs natively on macOS with zero external dependencies.

### Launching the Controller
```bash
# Auto-detects USB serial device and opens command prompt
./gantry_cli.py

# Or launch directly into live keyboard jog mode
./gantry_cli.py --jog
```

### Live Keyboard Jogging Mode
Press `jog` or launch with `--jog`:
* **`W` / `Up Arrow`**: Jog +Y
* **`S` / `Down Arrow`**: Jog -Y
* **`A` / `Left Arrow`**: Jog -X
* **`D` / `Right Arrow`**: Jog +X
* **`1` .. `5`**: Change step distance (`0.1mm`, `1mm`, `5mm`, `10mm`, `50mm`)
* **`Z`**: Zero current position as origin $(0, 0)$
* **`E`**: Re-enable motor holding current
* **`P`**: Print current $(X, Y)$ position and step mode
* **`Q` / `ESC`**: Exit back to command prompt

### Command Prompt Mode
```text
gantry> m 16                # Switch to 1/16 microstepping (80 steps/mm)
gantry> goto 50 25          # Move to coordinate X=50mm, Y=25mm
gantry> move 10 -5          # Relative move +10mm X, -5mm Y
gantry> jog x 20            # Jog X axis by 20mm
gantry> speed 800           # Change step pulse delay in microseconds
gantry> test square 40      # Run 40mm square diagnostic test
gantry> test diag 50        # Run 50mm diagonal test
gantry> zero                # Reset coordinate origin to (0, 0)
gantry> disable             # Relax motors (stops heating while idle)
gantry> enable              # Re-engage motor holding torque
```

