#ifndef F_CPU
#define F_CPU 1000000UL
#endif

#include <avr/io.h>
#include <avr/interrupt.h>
#include <avr/pgmspace.h>
#include <util/delay.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>

/*
 * ==============================================================================
 * PIN MAPPING - ATmega32 (DIP-40) PORTC for Dual DRV8825 Stepper Drivers
 * ==============================================================================
 * Pin 22 (PC0) : Motor X STEP
 * Pin 23 (PC1) : Motor X DIR
 * Pin 24 (PC2) : Motor Y STEP
 * Pin 25 (PC3) : Motor Y DIR
 * Pin 26 (PC4) : ENABLE (Active LOW, shared between both DRV8825 modules)
 * Pin 27 (PC5) : MODE0  (Microstep select bit 0, shared)
 * Pin 28 (PC6) : MODE1  (Microstep select bit 1, shared)
 * Pin 29 (PC7) : MODE2  (Microstep select bit 2, shared)
 *
 * Pin 14 (PD0) : UART RXD (connected to USB-UART TX)
 * Pin 15 (PD1) : UART TXD (connected to USB-UART RX)
 * ==============================================================================
 */

#define PIN_STEP_X      PC0
#define PIN_DIR_X       PC1
#define PIN_STEP_Y      PC2
#define PIN_DIR_Y       PC3
#define PIN_ENABLE     PC4
#define PIN_MODE0      PC5
#define PIN_MODE1      PC6
#define PIN_MODE2      PC7

#define MOTOR_PORT     PORTC
#define MOTOR_DDR      DDRC

/*
 * Mechanical Kinematics:
 * X Axis: GT2 Belt (2mm pitch), 20-tooth pulley = 40.0 mm / revolution.
 *         200 steps/rev -> Base resolution = 200 / 40 = 5.0 steps/mm (Full Step).
 *         Movable rod travel: 45 cm (450.0 mm).
 * Y Axis: GT2 Belt (2mm pitch), 30-tooth pulley = 60.0 mm / revolution.
 *         200 steps/rev -> Base resolution = 200 / 60 = 3.3333 steps/mm (Full Step).
 *         Movable rod travel: 42 cm (420.0 mm).
 */
#define BASE_STEPS_PER_MM_X   5.0f
#define BASE_STEPS_PER_MM_Y   (200.0f / 60.0f)
#define X_MAX_TRAVEL_MM       450.0f
#define Y_MAX_TRAVEL_MM       420.0f

/* UART Configuration (9600 Baud @ 1 MHz with U2X=1) */
#define UART_BAUD           9600UL
#define UART_UBRR           ((F_CPU + (UART_BAUD * 4UL)) / (UART_BAUD * 8UL) - 1UL)

#define RX_BUFFER_SIZE      64

/* Motion State Variables */
static int32_t current_step_x = 0;
static int32_t current_step_y = 0;
static uint8_t current_microstep = 16;  /* Default to 1/16 microstepping */
static float current_steps_per_mm_x = 80.0f;                   /* 5 * 16 = 80 steps/mm */
static float current_steps_per_mm_y = (16.0f * 200.0f / 60.0f); /* 53.333 steps/mm */
static uint16_t step_delay_us = 400;       /* Default delay us */

/* ==============================================================================
 * UART Functions
 * ============================================================================== */

static void uart_init(void)
{
    UCSRA = (1U << U2X); /* Double speed mode */
    UCSRB = (1U << RXEN) | (1U << TXEN);
    UCSRC = (1U << URSEL) | (1U << UCSZ1) | (1U << UCSZ0); /* 8-bit, 1 stop bit, no parity */
    UBRRH = (uint8_t)(UART_UBRR >> 8);
    UBRRL = (uint8_t)UART_UBRR;
}

static void uart_putc(char c)
{
    while ((UCSRA & (1U << UDRE)) == 0U) {
        /* Wait for empty transmit buffer */
    }
    UDR = (uint8_t)c;
}

static void uart_puts(const char *s)
{
    while (*s != '\0') {
        uart_putc(*s++);
    }
}

static void uart_puts_P(const char *progmem_s)
{
    char c;
    while ((c = pgm_read_byte(progmem_s++)) != '\0') {
        uart_putc(c);
    }
}

static bool uart_available(void)
{
    return (UCSRA & (1U << RXC)) != 0U;
}

static char uart_getc(void)
{
    while (!uart_available()) {
        /* Wait */
    }
    return (char)UDR;
}

/* ==============================================================================
 * Microstepping Control (DRV8825 MODE0..MODE2)
 *
 * DRV8825 Truth Table:
 * MODE0 (PC5) | MODE1 (PC6) | MODE2 (PC7) | Resolution
 *   LOW       |   LOW       |   LOW       | Full step (1/1)
 *   HIGH      |   LOW       |   LOW       | 1/2 step
 *   LOW       |   HIGH      |   LOW       | 1/4 step
 *   HIGH      |   HIGH      |   LOW       | 1/8 step
 *   LOW       |   LOW       |   HIGH      | 1/16 step
 *   HIGH      |   HIGH      |   HIGH      | 1/32 step
 * ============================================================================== */

static bool set_microstepping(uint8_t mode)
{
    uint8_t m0 = 0, m1 = 0, m2 = 0;

    switch (mode) {
        case 1:
            m0 = 0; m1 = 0; m2 = 0;
            break;
        case 2:
            m0 = 1; m1 = 0; m2 = 0;
            break;
        case 4:
            m0 = 0; m1 = 1; m2 = 0;
            break;
        case 8:
            m0 = 1; m1 = 1; m2 = 0;
            break;
        case 16:
            m0 = 0; m1 = 0; m2 = 1;
            break;
        case 32:
            m0 = 1; m1 = 1; m2 = 1;
            break;
        default:
            return false;
    }

    /* Apply to PORTC pins */
    if (m0) MOTOR_PORT |= (1U << PIN_MODE0); else MOTOR_PORT &= ~(1U << PIN_MODE0);
    if (m1) MOTOR_PORT |= (1U << PIN_MODE1); else MOTOR_PORT &= ~(1U << PIN_MODE1);
    if (m2) MOTOR_PORT |= (1U << PIN_MODE2); else MOTOR_PORT &= ~(1U << PIN_MODE2);

    /* Recalculate step positions to preserve physical location */
    if (current_microstep > 0) {
        current_step_x = (int32_t)((float)current_step_x * ((float)mode / (float)current_microstep));
        current_step_y = (int32_t)((float)current_step_y * ((float)mode / (float)current_microstep));
    }

    current_microstep = mode;
    current_steps_per_mm_x = BASE_STEPS_PER_MM_X * (float)mode;
    current_steps_per_mm_y = BASE_STEPS_PER_MM_Y * (float)mode;
    return true;
}

static void motors_enable(bool enable)
{
    if (enable) {
        /* Active LOW on DRV8825: 0 enables outputs */
        MOTOR_PORT &= ~(1U << PIN_ENABLE);
    } else {
        /* 1 disables driver outputs (relaxes motors) */
        MOTOR_PORT |= (1U << PIN_ENABLE);
    }
}

/* Variable delay supporting microseconds at 1 MHz */
static void delay_microseconds(uint16_t us)
{
    while (us--) {
        _delay_us(1);
    }
}

/* ==============================================================================
 * Coordinated Multi-Axis Step Engine (Bresenham Linear DDA)
 * ============================================================================== */

static void move_steps(int32_t delta_x, int32_t delta_y)
{
    if (delta_x == 0 && delta_y == 0) {
        return;
    }

    /* Ensure motors are enabled */
    motors_enable(true);

    /* Set Directions */
    if (delta_x >= 0) {
        MOTOR_PORT &= ~(1U << PIN_DIR_X); /* Forward */
    } else {
        MOTOR_PORT |= (1U << PIN_DIR_X);  /* Reverse */
        delta_x = -delta_x;
    }

    if (delta_y >= 0) {
        MOTOR_PORT &= ~(1U << PIN_DIR_Y); /* Forward */
    } else {
        MOTOR_PORT |= (1U << PIN_DIR_Y);  /* Reverse */
        delta_y = -delta_y;
    }

    /* Allow direction signal setup time (DRV8825 requires min 650ns) */
    _delay_us(2);

    /* Coordinated Bresenham DDA */
    int32_t max_steps = (delta_x > delta_y) ? delta_x : delta_y;
    int32_t err_x = max_steps / 2;
    int32_t err_y = max_steps / 2;

    for (int32_t i = 0; i < max_steps; ++i) {
        uint8_t step_mask = 0;

        err_x -= delta_x;
        if (err_x < 0) {
            err_x += max_steps;
            step_mask |= (1U << PIN_STEP_X);
        }

        err_y -= delta_y;
        if (err_y < 0) {
            err_y += max_steps;
            step_mask |= (1U << PIN_STEP_Y);
        }

        if (step_mask != 0) {
            /* High pulse (DRV8825 requires min 1.9us) */
            MOTOR_PORT |= step_mask;
            _delay_us(4);
            MOTOR_PORT &= ~step_mask;
        }

        /* Inter-step delay determines speed */
        delay_microseconds(step_delay_us);
    }
}

static void move_to_mm(float target_x_mm, float target_y_mm)
{
    /* X and Y axis soft travel limits */
    if (target_x_mm < 0.0f) target_x_mm = 0.0f;
    if (target_x_mm > X_MAX_TRAVEL_MM) target_x_mm = X_MAX_TRAVEL_MM;
    if (target_y_mm < 0.0f) target_y_mm = 0.0f;
    if (target_y_mm > Y_MAX_TRAVEL_MM) target_y_mm = Y_MAX_TRAVEL_MM;

    int32_t target_step_x = (int32_t)(target_x_mm * current_steps_per_mm_x);
    int32_t target_step_y = (int32_t)(target_y_mm * current_steps_per_mm_y);

    int32_t dx = target_step_x - current_step_x;
    int32_t dy = target_step_y - current_step_y;

    move_steps(dx, dy);

    current_step_x = target_step_x;
    current_step_y = target_step_y;
}

static void move_relative_mm(float rel_x_mm, float rel_y_mm)
{
    float current_x_mm = (float)current_step_x / current_steps_per_mm_x;
    float current_y_mm = (float)current_step_y / current_steps_per_mm_y;

    float target_x_mm = current_x_mm + rel_x_mm;
    float target_y_mm = current_y_mm + rel_y_mm;

    /* Clamp X within 0..450mm rod limit */
    if (target_x_mm < 0.0f) target_x_mm = 0.0f;
    if (target_x_mm > X_MAX_TRAVEL_MM) target_x_mm = X_MAX_TRAVEL_MM;
    rel_x_mm = target_x_mm - current_x_mm;

    /* Clamp Y within 0..420mm rod limit */
    if (target_y_mm < 0.0f) target_y_mm = 0.0f;
    if (target_y_mm > Y_MAX_TRAVEL_MM) target_y_mm = Y_MAX_TRAVEL_MM;
    rel_y_mm = target_y_mm - current_y_mm;

    int32_t dx = (int32_t)(rel_x_mm * current_steps_per_mm_x);
    int32_t dy = (int32_t)(rel_y_mm * current_steps_per_mm_y);

    move_steps(dx, dy);

    current_step_x += dx;
    current_step_y += dy;
}

/* ==============================================================================
 * Command Interpreter
 * ============================================================================== */

static void print_status(void)
{
    char buf[90];
    float x_mm = (float)current_step_x / current_steps_per_mm_x;
    float y_mm = (float)current_step_y / current_steps_per_mm_y;

    /* Break float into integer and fractional part for reliable printing */
    int32_t x_int = (int32_t)x_mm;
    int32_t x_frac = abs((int32_t)((x_mm - (float)x_int) * 100.0f));
    int32_t y_int = (int32_t)y_mm;
    int32_t y_frac = abs((int32_t)((y_mm - (float)y_int) * 100.0f));

    long spm_x = (long)(current_steps_per_mm_x + 0.5f);
    long spm_y = (long)(current_steps_per_mm_y + 0.5f);

    snprintf(buf, sizeof(buf), "STATUS X:%ld.%02ld Y:%ld.%02ld mm | M:1/%u (X:%ld Y:%ld stp/mm) | DLY:%uus\r\n",
             (long)x_int, (long)x_frac, (long)y_int, (long)y_frac, current_microstep, spm_x, spm_y, step_delay_us);
    uart_puts(buf);
}

static void parse_and_execute(char *cmd)
{
    /* Trim whitespace */
    while (*cmd == ' ' || *cmd == '\t') cmd++;
    if (*cmd == '\0') return;

    if (strncmp(cmd, "PING", 4) == 0) {
        uart_puts_P(PSTR("PONG\r\n"));
    }
    else if (strncmp(cmd, "HELP", 4) == 0) {
        uart_puts_P(PSTR("--- ATmega32 DRV8825 Test Driver Commands ---\r\n"));
        uart_puts_P(PSTR("  M <1|2|4|8|16|32>    : Set microstep mode\r\n"));
        uart_puts_P(PSTR("  G0 X<mm> Y<mm>       : Move to absolute position (mm)\r\n"));
        uart_puts_P(PSTR("  R X<mm> Y<mm>        : Move relative distance (mm)\r\n"));
        uart_puts_P(PSTR("  JOG <X|Y> <mm>       : Jog single axis (e.g. JOG X 10)\r\n"));
        uart_puts_P(PSTR("  SPEED <us>           : Set step delay us (e.g. SPEED 800)\r\n"));
        uart_puts_P(PSTR("  EN <1|0>             : Enable (1) or Disable (0) motors\r\n"));
        uart_puts_P(PSTR("  ZERO                 : Zero current coordinate (0, 0)\r\n"));
        uart_puts_P(PSTR("  POS                  : Query current position & config\r\n"));
        uart_puts_P(PSTR("  TEST SQUARE <mm>     : Run square test pattern\r\n"));
        uart_puts_P(PSTR("  TEST DIAG <mm>       : Run diagonal back-and-forth test\r\n"));
        uart_puts_P(PSTR("OK\r\n"));
    }
    else if (strncmp(cmd, "POS", 3) == 0) {
        print_status();
        uart_puts_P(PSTR("OK\r\n"));
    }
    else if (strncmp(cmd, "ZERO", 4) == 0) {
        current_step_x = 0;
        current_step_y = 0;
        uart_puts_P(PSTR("OK ZEROED\r\n"));
    }
    else if (strncmp(cmd, "EN", 2) == 0) {
        int state = atoi(cmd + 2);
        motors_enable(state != 0);
        uart_puts_P(state ? PSTR("OK MOTORS ENABLED\r\n") : PSTR("OK MOTORS DISABLED\r\n"));
    }
    else if (strncmp(cmd, "SPEED", 5) == 0) {
        int us = atoi(cmd + 5);
        if (us >= 100 && us <= 20000) {
            step_delay_us = (uint16_t)us;
            uart_puts_P(PSTR("OK SPEED UPDATED\r\n"));
        } else {
            uart_puts_P(PSTR("ERR: Speed must be 100..20000 us\r\n"));
        }
    }
    else if (cmd[0] == 'M' && (cmd[1] == ' ' || cmd[1] == '\t')) {
        int mode = atoi(cmd + 1);
        if (set_microstepping((uint8_t)mode)) {
            char buf[60];
            long spm_x = (long)(current_steps_per_mm_x + 0.5f);
            long spm_y = (long)(current_steps_per_mm_y + 0.5f);
            snprintf(buf, sizeof(buf), "OK MICROSTEP 1/%d (X:%ld Y:%ld stp/mm)\r\n", mode, spm_x, spm_y);
            uart_puts(buf);
        } else {
            uart_puts_P(PSTR("ERR: Valid microsteps are 1, 2, 4, 8, 16, 32\r\n"));
        }
    }
    else if (strncmp(cmd, "G0", 2) == 0 || strncmp(cmd, "G1", 2) == 0 ||
             strncmp(cmd, "g0", 2) == 0 || strncmp(cmd, "g1", 2) == 0) {
        char *px = strchr(cmd, 'X');
        if (!px) px = strchr(cmd, 'x');
        char *py = strchr(cmd, 'Y');
        if (!py) py = strchr(cmd, 'y');

        float tx = px ? atof(px + 1) : ((float)current_step_x / current_steps_per_mm_x);
        float ty = py ? atof(py + 1) : ((float)current_step_y / current_steps_per_mm_y);

        move_to_mm(tx, ty);
        print_status();
        uart_puts_P(PSTR("OK\r\n"));
    }
    else if ((cmd[0] == 'R' || cmd[0] == 'r') && (cmd[1] == ' ' || cmd[1] == '\t')) {
        char *px = strchr(cmd, 'X');
        if (!px) px = strchr(cmd, 'x');
        char *py = strchr(cmd, 'Y');
        if (!py) py = strchr(cmd, 'y');

        float rx = px ? atof(px + 1) : 0.0f;
        float ry = py ? atof(py + 1) : 0.0f;

        move_relative_mm(rx, ry);
        print_status();
        uart_puts_P(PSTR("OK\r\n"));
    }
    else if (strncmp(cmd, "JOG", 3) == 0 || strncmp(cmd, "jog", 3) == 0) {
        char *p = cmd + 3;
        while (*p == ' ') p++;
        char axis = *p;
        float dist = atof(p + 1);

        if (axis == 'X' || axis == 'x') {
            move_relative_mm(dist, 0.0f);
            print_status();
            uart_puts_P(PSTR("OK\r\n"));
        } else if (axis == 'Y' || axis == 'y') {
            move_relative_mm(0.0f, dist);
            print_status();
            uart_puts_P(PSTR("OK\r\n"));
        } else {
            uart_puts_P(PSTR("ERR: Specify axis X or Y (e.g. JOG X 10)\r\n"));
        }
    }
    else if (strncmp(cmd, "TEST SQUARE", 11) == 0 || strncmp(cmd, "test square", 11) == 0) {
        float size = atof(cmd + 11);
        if (size <= 0.0f) size = 30.0f; /* Default 30mm */

        uart_puts_P(PSTR("RUNNING SQUARE TEST...\r\n"));
        move_relative_mm(size, 0.0f);
        _delay_ms(100);
        move_relative_mm(0.0f, size);
        _delay_ms(100);
        move_relative_mm(-size, 0.0f);
        _delay_ms(100);
        move_relative_mm(0.0f, -size);

        print_status();
        uart_puts_P(PSTR("OK SQUARE TEST COMPLETE\r\n"));
    }
    else if (strncmp(cmd, "TEST DIAG", 9) == 0 || strncmp(cmd, "test diag", 9) == 0) {
        float size = atof(cmd + 9);
        if (size <= 0.0f) size = 40.0f;

        uart_puts_P(PSTR("RUNNING DIAGONAL TEST...\r\n"));
        move_relative_mm(size, size);
        _delay_ms(150);
        move_relative_mm(-size, -size);

        print_status();
        uart_puts_P(PSTR("OK DIAGONAL TEST COMPLETE\r\n"));
    }
    else {
        uart_puts_P(PSTR("ERR: Unknown command. Type HELP for command list.\r\n"));
    }
}

/* ==============================================================================
 * Main Routine
 * ============================================================================== */

int main(void)
{
    /*
     * CRITICAL for ATmega32: Disable JTAG interface in software.
     * On factory ATmega32, JTAG is enabled by default on PC2 (TCK), PC3 (TMS),
     * PC4 (TDO), and PC5 (TDI). Disabling JTD bit twice within 4 cycles frees
     * them for normal GPIO operation (STEP_Y, DIR_Y, ENABLE, MODE0).
     */
    MCUCSR |= (1U << JTD);
    MCUCSR |= (1U << JTD);

    /* Initialize outputs: drive STEP low before enabling outputs */
    MOTOR_PORT &= ~((1U << PIN_STEP_X) | (1U << PIN_STEP_Y));

    /* Set all Motor control pins on PORTC as Outputs */
    MOTOR_DDR |= (1U << PIN_STEP_X) | (1U << PIN_DIR_X) |
                 (1U << PIN_STEP_Y) | (1U << PIN_DIR_Y) |
                 (1U << PIN_ENABLE) |
                 (1U << PIN_MODE0)  | (1U << PIN_MODE1) | (1U << PIN_MODE2);

    /* Initialize drivers to enabled and default 1/16 microstep */
    motors_enable(true);
    set_microstepping(16);

    /* Initialize UART */
    uart_init();

    _delay_ms(50);
    uart_puts_P(PSTR("\r\n========================================\r\n"));
    uart_puts_P(PSTR("ATmega32 Dual DRV8825 Gantry Driver v1.0\r\n"));
    uart_puts_P(PSTR("Cartesian XY (20T GT2, NEMA17)\r\n"));
    uart_puts_P(PSTR("Type HELP for command list.\r\n"));
    uart_puts_P(PSTR("========================================\r\n"));
    print_status();

    char rx_buf[RX_BUFFER_SIZE];
    uint8_t rx_idx = 0;

    while (1) {
        if (uart_available()) {
            char c = uart_getc();

            /* Echo character back */
            uart_putc(c);

            if (c == '\r' || c == '\n') {
                uart_puts_P(PSTR("\r\n"));
                if (rx_idx > 0) {
                    rx_buf[rx_idx] = '\0';
                    parse_and_execute(rx_buf);
                    rx_idx = 0;
                }
            } else if (c == '\b' || c == 0x7F) {
                /* Backspace handling */
                if (rx_idx > 0) {
                    rx_idx--;
                }
            } else if (rx_idx < (RX_BUFFER_SIZE - 1)) {
                rx_buf[rx_idx++] = c;
            }
        }
    }

    return 0;
}
