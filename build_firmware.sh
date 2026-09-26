#!/bin/bash
set -euo pipefail

MCU="atmega32"
PROGRAMMER="usbasp"
F_CPU="1000000UL"
SOURCE="chessboard_firmware.c"
TARGET="chessboard_firmware"

echo "=== Compiling $SOURCE for $MCU (F_CPU=$F_CPU) ==="
/opt/homebrew/bin/avr-gcc \
    -mmcu="$MCU" \
    -DF_CPU="$F_CPU" \
    -std=c11 -Os -Wall -Wextra \
    -o "${TARGET}.elf" "$SOURCE"

/opt/homebrew/bin/avr-objcopy -O ihex -R .eeprom "${TARGET}.elf" "${TARGET}.hex"

echo "=== Firmware Memory Footprint ==="
/opt/homebrew/bin/avr-size --mcu="$MCU" --format=avr "${TARGET}.elf"

if [[ "${1:-}" == "--flash" || "${1:-}" == "-f" ]]; then
    echo "=== Flashing ${TARGET}.hex to $MCU via $PROGRAMMER ==="
    /opt/homebrew/bin/avrdude -c "$PROGRAMMER" -p m32 -U "flash:w:${TARGET}.hex:i"
    echo "=== Flash Complete! ==="
else
    echo ""
    echo "Compilation successful: ${TARGET}.hex ready."
    echo "To compile and flash to your ATmega32 with USBasp, run:"
    echo "  ./build_firmware.sh --flash"
fi
