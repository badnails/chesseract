#ifndef F_CPU
#define F_CPU 1000000UL
#endif

#include <avr/io.h>
#include <util/delay.h>

/*
 * ==============================================================================
 * ULTRA-SIMPLE STEPPER TEST FOR ATmega32
 * ==============================================================================
 * This code does NOT use UART, interrupts, or complex libraries.
 * It directly pulses PC0 (Step X) and PC2 (Step Y) in an infinite loop.
 *
 * Pin Connections on ATmega32 (DIP-40):
 *   Pin 22 (PC0) : Motor X STEP
 *   Pin 23 (PC1) : Motor X DIR
 *   Pin 24 (PC2) : Motor Y STEP
 *   Pin 25 (PC3) : Motor Y DIR
 *   Pin 26 (PC4) : ENABLE (Held LOW = Motors ON)
 *   Pin 27 (PC5) : MODE0  (Held LOW = Full Step)
 *   Pin 28 (PC6) : MODE1  (Held LOW = Full Step)
 *   Pin 29 (PC7) : MODE2  (Held LOW = Full Step)
 * ==============================================================================
 */

int main(void)
{
    /* 1. Disable JTAG interface to unlock PC2, PC3, PC4, PC5 as normal GPIO */
    MCUCSR |= (1U << JTD);
    MCUCSR |= (1U << JTD);

    /* 2. Configure all 8 pins of PORTC (pins 22 to 29) as digital outputs */
    DDRC = 0xFF;

    /* 3. Set Driver Controls:
     *    - ENABLE (PC4) = 0 (Active LOW -> Turn on motor current)
     *    - DIR_X (PC1)  = 0 (Spin forward)
     *    - DIR_Y (PC3)  = 0 (Spin forward)
     *    - MODE0..2 (PC5, PC6, PC7) = 0 (Full Step mode: 200 steps/rev, max torque)
     */
    PORTC = 0x00;

    /* Brief settling pause */
    _delay_ms(200);

    /* 4. Step continuously in one direction forever */
    while (1) {
        /* Drive STEP pins HIGH (PC0 and PC2) */
        PORTC |= (1U << PC0) | (1U << PC2);
        _delay_us(20);

        /* Drive STEP pins LOW */
        PORTC &= ~((1U << PC0) | (1U << PC2));

        /* Wait between steps:
         * 4ms per step = 250 steps/second.
         * At full step, 200 steps = 1 full revolution (takes ~0.8 seconds/rev).
         */
        _delay_ms(4);
    }

    return 0;
}
