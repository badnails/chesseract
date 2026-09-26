#!/bin/bash
set -euo pipefail

MCU="atmega32"
PROGRAMMER="usbasp"
F_CPU="1000000UL"

echo "=== Compiling simple_test.c for $MCU ==="
avr-gcc \
    -mmcu="$MCU" \
    -DF_CPU="$F_CPU" \
    -std=c11 -Os -Wall -Wextra \
    -o simple_test.elf simple_test.c

avr-objcopy -O ihex -R .eeprom simple_test.elf simple_test.hex
avr-size --mcu="$MCU" --format=avr simple_test.elf

if [[ "${1:-}" == "--flash" || "${1:-}" == "-f" ]]; then
    echo "=== Flashing simple_test.hex to $MCU with $PROGRAMMER ==="
    avrdude -c "$PROGRAMMER" -p m32 -U flash:w:simple_test.hex:i
    echo "=== Flash complete! Motors should be turning! ==="
else
    echo ""
    echo "Compilation successful! To flash, run:"
    echo "  ./flash_simple.sh --flash"
fi
