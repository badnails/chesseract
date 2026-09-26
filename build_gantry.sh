#!/bin/bash
set -euo pipefail

MCU="atmega32"
PROGRAMMER="usbasp"
F_CPU="1000000UL"

echo "=== Compiling gantry.c for $MCU (F_CPU=$F_CPU) ==="
avr-gcc \
    -mmcu="$MCU" \
    -DF_CPU="$F_CPU" \
    -std=c11 -Os -Wall -Wextra \
    -o gantry.elf gantry.c

avr-objcopy -O ihex -R .eeprom gantry.elf gantry.hex
echo "=== Firmware Size ==="
avr-size --mcu="$MCU" --format=avr gantry.elf

if [[ "${1:-}" == "--flash" || "${1:-}" == "-f" ]]; then
    echo "=== Flashing gantry.hex to $MCU with $PROGRAMMER ==="
    avrdude -c "$PROGRAMMER" -p m32 -U flash:w:gantry.hex:i
    echo "=== Flash complete! ==="
else
    echo ""
    echo "Compilation successful! To flash to your ATmega32, run:"
    echo "  ./build_gantry.sh --flash"
fi

