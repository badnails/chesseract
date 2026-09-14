# ATmega32 Pin Connection Quick Reference

Concise wiring table for the 8x8 chessboard project using the **ATmega32 (40-pin DIP)**.

---

## 1. Quick Connection Table

| Physical Pin | ATmega32 Name | Connect To | Details / Notes |
|:---:|:---|:---|:---|
| **6** | `PB5` (MOSI) | USBasp MOSI | Flashing firmware |
| **7** | `PB6` (MISO) | USBasp MISO | Flashing firmware |
| **8** | `PB7` (SCK) | USBasp SCK | Flashing firmware |
| **9** | `RESET` | 10 kΩ pull-up to +5V & USBasp RESET | Holds chip out of reset |
| **10** | `VCC` | **+5V** power rail | Place 100 nF cap to GND (pin 11) |
| **11** | `GND` | **GND** rail | Common ground |
| **14** | `PD0` (RXD) | USB-UART **TX** | Serial input from host |
| **15** | `PD1` (TXD) | USB-UART **RX** | Serial output to Mac (9600 baud) |
| **16** | `PD2` (INT0) | **Scan Push Button** | Other side of button to GND |
| **30** | `AVCC` | **+5V** power rail | **Mandatory** for Port A (100 nF to GND) |
| **31** | `GND` | **GND** rail | Common ground |
| **32** | `AREF` | 100 nF capacitor to GND | Analog reference decoupling |
| **33** | `PA7` | **Mux 3 SIG** | Reed switches for files `g` & `h` |
| **34** | `PA6` | **Mux 2 SIG** | Reed switches for files `e` & `f` |
| **35** | `PA5` | **Mux 1 SIG** | Reed switches for files `c` & `d` |
| **36** | `PA4` | **Mux 0 SIG** | Reed switches for files `a` & `b` |
| **37** | `PA3` | **S3** (shared across all 4 muxes) | Multiplexer address bit 3 (MSB) |
| **38** | `PA2` | **S2** (shared across all 4 muxes) | Multiplexer address bit 2 |
| **39** | `PA1` | **S1** (shared across all 4 muxes) | Multiplexer address bit 1 |
| **40** | `PA0` | **S0** (shared across all 4 muxes) | Multiplexer address bit 0 (LSB) |

> **All other ATmega pins** (pins 1–5, 12, 13, 17–29) are **unconnected**.

---

## 2. DIP-40 Pinout Visual

```text
                     ATmega32 (DIP-40)
                  +---------\_/---------+
         (nc) --- |  1 (PB0)   (PA0) 40 | ---> S0 (Mux 0, 1, 2, 3)
         (nc) --- |  2 (PB1)   (PA1) 39 | ---> S1 (Mux 0, 1, 2, 3)
         (nc) --- |  3 (PB2)   (PA2) 38 | ---> S2 (Mux 0, 1, 2, 3)
         (nc) --- |  4 (PB3)   (PA3) 37 | ---> S3 (Mux 0, 1, 2, 3)
         (nc) --- |  5 (PB4)   (PA4) 36 | <--- SIG0 (Mux 0: files a, b)
 USBasp MOSI <--- |  6 (PB5)   (PA5) 35 | <--- SIG1 (Mux 1: files c, d)
 USBasp MISO ---> |  7 (PB6)   (PA6) 34 | <--- SIG2 (Mux 2: files e, f)
  USBasp SCK <--- |  8 (PB7)   (PA7) 33 | <--- SIG3 (Mux 3: files g, h)
RESET (10k to 5V) |  9 (RST)     (AREF) 32 | --- 100 nF cap to GND
         +5V ---> | 10 (VCC)     (AVCC) 31 | --- GND
         GND ---> | 11 (GND)      (GND) 30 | <--- +5V (REQUIRED!)
         (nc) --- | 12 (XTAL2)   (PC7)  29 | --- (nc)
         (nc) --- | 13 (XTAL1)   (PC6)  28 | --- (nc)
 USB-UART TX ---> | 14 (PD0)     (PC5)  27 | --- (nc)
 USB-UART RX <--- | 15 (PD1)     (PC4)  26 | --- (nc)
Scan Btn to GND - | 16 (PD2)     (PC3)  25 | --- (nc)
         (nc) --- | 17 (PD3)     (PC2)  24 | --- (nc)
         (nc) --- | 18 (PD4)     (PC1)  23 | --- (nc)
         (nc) --- | 19 (PD5)     (PC0)  22 | --- (nc)
         (nc) --- | 20 (PD6)     (PD7)  21 | --- (nc)
                  +---------------------+
```

---

## 3. Multiplexer Module Pins (All 4 Modules)

For each of the 4 CD74HC4067 multiplexers:

| Mux Pin | Connect To |
|:---|:---|
| **VCC** | +5V rail (with 100 nF cap to GND) |
| **GND** | GND rail |
| **EN** | **GND** (keeps multiplexer enabled) |
| **S0** | ATmega32 `PA0` (pin 40) |
| **S1** | ATmega32 `PA1` (pin 39) |
| **S2** | ATmega32 `PA2` (pin 38) |
| **S3** | ATmega32 `PA3` (pin 37) |
| **SIG** | Dedicated ATmega pin: `PA4` (Mux 0), `PA5` (Mux 1), `PA6` (Mux 2), `PA7` (Mux 3) |
| **C0..C15** | One end of each reed switch (other end to GND) |

---

## 4. Power & Decoupling Checklist

1. **VCC (pin 10)** $\rightarrow$ +5V with 100 nF ceramic cap to GND (pin 11).
2. **AVCC (pin 30)** $\rightarrow$ +5V with 100 nF ceramic cap to GND (pin 31). *(Do not leave floating; Port A will fail without it).*
3. **AREF (pin 32)** $\rightarrow$ 100 nF ceramic cap to GND.
4. **RESET (pin 9)** $\rightarrow$ 10 kΩ resistor to +5V.
5. **Common Ground** $\rightarrow$ ATmega GND, all 4 Mux GNDs, USB-UART GND, and USBasp GND must all be tied together.

