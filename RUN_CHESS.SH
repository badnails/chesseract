#!/bin/bash
set -euo pipefail

MCU="atmega32"
PROGRAMMER="usbasp"
F_CPU="1000000UL"

avr-gcc \
    -mmcu="$MCU" \
    -DF_CPU="$F_CPU" \
    -std=c11 -Os -Wall -Wextra \
    -o chess.elf chess.c

avr-objcopy -O ihex -R .eeprom chess.elf chess.hex
avr-size --mcu="$MCU" --format=avr chess.elf

echo "Flashing chess.hex to $MCU with $PROGRAMMER..."
avrdude -c "$PROGRAMMER" -p m32 -U flash:w:chess.hex:i
