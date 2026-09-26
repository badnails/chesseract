#!/bin/bash
set -euo pipefail

MCU="atmega32"
PROGRAMMER="usbasp"
F_CPU="1000000UL"

echo "=== Compiling chess_gantry.c for $MCU (F_CPU=$F_CPU) ==="
avr-gcc \
    -mmcu="$MCU" \
    -DF_CPU="$F_CPU" \
    -std=c11 -Os -Wall -Wextra \
    -o chess_gantry.elf chess_gantry.c

avr-objcopy -O ihex -R .eeprom chess_gantry.elf chess_gantry.hex
echo "=== Firmware Size ==="
avr-size --mcu="$MCU" --format=avr chess_gantry.elf

if [[ "${1:-}" == "--flash" || "${1:-}" == "-f" ]]; then
    echo "=== Flashing chess_gantry.hex to $MCU with $PROGRAMMER ==="
    avrdude -c "$PROGRAMMER" -p m32 -U flash:w:chess_gantry.hex:i
    echo "=== Flash complete! ==="
else
    echo ""
    echo "Compilation successful! To flash to your ATmega32, run:"
    echo "  ./build_chess.sh --flash"
fi

