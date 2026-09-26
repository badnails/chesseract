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
 * AUTOMATED CHESSBOARD GANTRY FIRMWARE (ATmega32)
 * ==============================================================================
 * Controls dual NEMA17 steppers (DRV8825) and a CW020 relay electromagnet
 * under an 8x8 chessboard with 4 cm (40 mm) square spacing.
 *
 * Pin Mapping:
 *   Pin 22 (PC0) : Motor X STEP (Rank axis)
 *   Pin 23 (PC1) : Motor X DIR
 *   Pin 24 (PC2) : Motor Y STEP (File axis)
 *   Pin 25 (PC3) : Motor Y DIR
 *   Pin 26 (PC4) : Motor ENABLE (Active LOW; auto-managed for power save)
 *   Pin 27 (PC5) : MODE0 (Microstepping select bit 0)
 *   Pin 28 (PC6) : MODE1 (Microstepping select bit 1)
 *   Pin 29 (PC7) : MODE2 (Microstepping select bit 2)
 *
 *   Pin 16 (PD2) : CW020 Relay Module IN (Active LOW trigger for electromagnet)
 *   Pin 14 (PD0) : UART RXD (HC-05 TXD)
 *   Pin 15 (PD1) : UART TXD (HC-05 RXD via voltage divider)
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

#define PIN_RELAY      PD2
#define RELAY_PORT     PORTD
#define RELAY_DDR      DDRD

/* CW020 relay is Active LOW (IN=0 turns relay ON, IN=1 turns relay OFF) */
#define RELAY_ACTIVE_LOW 1

/*
 * Chessboard Kinematics:
 * - Square size: 40.0 mm (4.0 cm)
 * - X Axis (Rank 1..8): GT2 20T pulley (40mm/rev) -> 5.0 steps/mm base (200 steps/rev)
 * - Y Axis (File a..h): GT2 40T pulley (80mm/rev) -> 2.5 steps/mm base (200 steps/rev)
 * - Origin (0, 0): Center of square 'a1'
 */
#define SQUARE_SIZE_MM        40.0f
#define BASE_STEPS_PER_MM_X   5.0f
#define BASE_STEPS_PER_MM_Y   2.5f

#define BOARD_MIN_MM          0.0f
#define BOARD_MAX_MM          (7.0f * SQUARE_SIZE_MM) /* 280.0 mm from a1 to h8 */

/* UART Configuration (9600 Baud @ 1 MHz with U2X=1) */
#define UART_BAUD           9600UL
#define UART_UBRR           ((F_CPU + (UART_BAUD * 4UL)) / (UART_BAUD * 8UL) - 1UL)

#define RX_BUFFER_SIZE      64

/* Motion State Variables */
static int32_t current_step_x = 0;
static int32_t current_step_y = 0;
static uint8_t current_microstep = 8;         /* Default 1/8 microstepping */
static float current_steps_per_mm_x = 40.0f;  /* 5.0 * 8 = 40 steps/mm */
static float current_steps_per_mm_y = 20.0f;  /* 2.5 * 8 = 20 steps/mm */
static uint16_t cruise_delay_us = 400;        /* Cruising delay between steps */
static uint16_t start_delay_us = 900;         /* Gentle start delay for acceleration */

/* ==============================================================================
 * UART Functions
 * ============================================================================== */

static void uart_init(void)
{
    UCSRA = (1U << U2X);
    UCSRB = (1U << RXEN) | (1U << TXEN);
    UCSRC = (1U << URSEL) | (1U << UCSZ1) | (1U << UCSZ0);
    UBRRH = (uint8_t)(UART_UBRR >> 8);
    UBRRL = (uint8_t)UART_UBRR;
}

static void uart_putc(char c)
{
    while ((UCSRA & (1U << UDRE)) == 0U) {}
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
    while (!uart_available()) {}
    return (char)UDR;
}

/* ==============================================================================
 * Electromagnet Relay Control (CW020)
 * ============================================================================== */

static void magnet_set(bool on)
{
#if RELAY_ACTIVE_LOW
    if (on) {
        RELAY_PORT &= ~(1U << PIN_RELAY); /* LOW turns relay ON */
    } else {
        RELAY_PORT |= (1U << PIN_RELAY);  /* HIGH turns relay OFF */
    }
#else
    if (on) {
        RELAY_PORT |= (1U << PIN_RELAY);
    } else {
        RELAY_PORT &= ~(1U << PIN_RELAY);
    }
#endif
}

static bool magnet_is_on(void)
{
#if RELAY_ACTIVE_LOW
    return (RELAY_PORT & (1U << PIN_RELAY)) == 0;
#else
    return (RELAY_PORT & (1U << PIN_RELAY)) != 0;
#endif
}

/* ==============================================================================
 * Driver Power & Microstepping Control
 * ============================================================================== */

static void motors_enable(bool enable)
{
    if (enable) {
        MOTOR_PORT &= ~(1U << PIN_ENABLE); /* Active LOW enables outputs */
        _delay_ms(5);                      /* Driver charge pump wakeup */
    } else {
        MOTOR_PORT |= (1U << PIN_ENABLE);  /* HIGH disables outputs (relaxes motor) */
    }
}

static bool set_microstepping(uint8_t mode)
{
    uint8_t m0 = 0, m1 = 0, m2 = 0;

    switch (mode) {
        case 1:  m0 = 0; m1 = 0; m2 = 0; break;
        case 2:  m0 = 1; m1 = 0; m2 = 0; break;
        case 4:  m0 = 0; m1 = 1; m2 = 0; break;
        case 8:  m0 = 1; m1 = 1; m2 = 0; break;
        case 16: m0 = 0; m1 = 0; m2 = 1; break;
        case 32: m0 = 1; m1 = 1; m2 = 1; break;
        default: return false;
    }

    if (m0) MOTOR_PORT |= (1U << PIN_MODE0); else MOTOR_PORT &= ~(1U << PIN_MODE0);
    if (m1) MOTOR_PORT |= (1U << PIN_MODE1); else MOTOR_PORT &= ~(1U << PIN_MODE1);
    if (m2) MOTOR_PORT |= (1U << PIN_MODE2); else MOTOR_PORT &= ~(1U << PIN_MODE2);

    if (current_microstep > 0) {
        current_step_x = (int32_t)((float)current_step_x * ((float)mode / (float)current_microstep));
        current_step_y = (int32_t)((float)current_step_y * ((float)mode / (float)current_microstep));
    }

    current_microstep = mode;
    current_steps_per_mm_x = BASE_STEPS_PER_MM_X * (float)mode;
    current_steps_per_mm_y = BASE_STEPS_PER_MM_Y * (float)mode;
    return true;
}

static void delay_microseconds(uint16_t us)
{
    while (us--) {
        _delay_us(1);
    }
}

/* ==============================================================================
 * Smooth Single-Axis Stepping with Trapezoidal Acceleration
 * ============================================================================== */

static void step_single_axis(uint8_t step_pin, uint8_t dir_pin, int32_t steps)
{
    if (steps == 0) return;

    /* Wake up motors */
    motors_enable(true);

    /* Set Direction */
    if (steps > 0) {
        MOTOR_PORT &= ~(1U << dir_pin);
    } else {
        MOTOR_PORT |= (1U << dir_pin);
        steps = -steps;
    }
    _delay_us(5);

    /* Ramp parameters */
    int32_t accel_steps = steps / 4;
    if (accel_steps > 80) accel_steps = 80; /* Max 80 steps ramp up/down */

    uint16_t delay = start_delay_us;
    uint16_t delay_diff = (start_delay_us > cruise_delay_us) ? (start_delay_us - cruise_delay_us) : 0;

    for (int32_t i = 0; i < steps; ++i) {
        /* Pulse HIGH */
        MOTOR_PORT |= (1U << step_pin);
        _delay_us(5);
        MOTOR_PORT &= ~(1U << step_pin);

        /* Acceleration curve */
        if (accel_steps > 0) {
            if (i < accel_steps) {
                /* Ramp up (speed up) */
                delay = start_delay_us - (uint16_t)(((uint32_t)delay_diff * i) / accel_steps);
            } else if (i >= (steps - accel_steps)) {
                /* Ramp down (slow down) */
                int32_t remaining = steps - 1 - i;
                delay = cruise_delay_us + (uint16_t)(((uint32_t)delay_diff * (accel_steps - remaining)) / accel_steps);
            } else {
                /* Cruising */
                delay = cruise_delay_us;
            }
        } else {
            delay = cruise_delay_us;
        }

        delay_microseconds(delay);
    }
}

/*
 * Strict Orthogonal Movement:
 * Always moves Rank (X) first, then File (Y) second. Never diagonally.
 */
static void move_orthogonal_mm(float target_x_mm, float target_y_mm)
{
    int32_t target_step_x = (int32_t)(target_x_mm * current_steps_per_mm_x);
    int32_t target_step_y = (int32_t)(target_y_mm * current_steps_per_mm_y);

    int32_t dx = target_step_x - current_step_x;
    int32_t dy = target_step_y - current_step_y;

    /* Move X (Rank) first */
    if (dx != 0) {
        step_single_axis(PIN_STEP_X, PIN_DIR_X, dx);
        current_step_x = target_step_x;
        _delay_ms(30); /* Brief inter-axis settle */
    }

    /* Move Y (File) second */
    if (dy != 0) {
        step_single_axis(PIN_STEP_Y, PIN_DIR_Y, dy);
        current_step_y = target_step_y;
        _delay_ms(30);
    }

    /* Auto power-save: relax motors after 100ms settling */
    _delay_ms(100);
    motors_enable(false);
}

/* ==============================================================================
 * Chess Algebraic Coordinate Conversion
 *   Square 'a1' -> (X = 0 mm, Y = 0 mm)
 *   Square 'h8' -> (X = 280 mm, Y = 280 mm)
 * ============================================================================== */

static bool parse_square(const char *sq, float *x_mm, float *y_mm)
{
    char file = sq[0];
    char rank = sq[1];

    if (file >= 'A' && file <= 'H') file += ('a' - 'A');
    if (file < 'a' || file > 'h') return false;
    if (rank < '1' || rank > '8') return false;

    /* X axis is Rank (1..8) */
    *x_mm = (float)(rank - '1') * SQUARE_SIZE_MM;

    /* Y axis is File (a..h) */
    *y_mm = (float)(file - 'a') * SQUARE_SIZE_MM;

    return true;
}

static bool execute_chess_move(const char *from_sq, const char *to_sq)
{
    float x_from, y_from, x_to, y_to;

    if (!parse_square(from_sq, &x_from, &y_from)) return false;
    if (!parse_square(to_sq, &x_to, &y_to)) return false;

    /* 1. Ensure magnet is OFF and travel to pickup square */
    magnet_set(false);
    move_orthogonal_mm(x_from, y_from);

    /* 2. Engage Electromagnet to grab the piece */
    magnet_set(true);
    _delay_ms(250); /* Allow magnetic field to firmly latch */

    /* 3. Transfer piece to destination square orthogonally */
    move_orthogonal_mm(x_to, y_to);

    /* 4. Release piece */
    magnet_set(false);
    _delay_ms(200); /* Allow magnetic field to fully collapse */

    return true;
}

/* ==============================================================================
 * Status and Command Parser
 * ============================================================================== */

static void print_status(void)
{
    char buf[100];
    float x_mm = (float)current_step_x / current_steps_per_mm_x;
    float y_mm = (float)current_step_y / current_steps_per_mm_y;

    /* Approximate square */
    int rank = (int)((x_mm + 20.0f) / SQUARE_SIZE_MM) + 1;
    int file_idx = (int)((y_mm + 20.0f) / SQUARE_SIZE_MM);
    if (rank < 1) {
        rank = 1;
    } else if (rank > 8) {
        rank = 8;
    }
    if (file_idx < 0) {
        file_idx = 0;
    } else if (file_idx > 7) {
        file_idx = 7;
    }
    char file_char = 'a' + file_idx;

    long spm_x = (long)(current_steps_per_mm_x + 0.5f);
    long spm_y = (long)(current_steps_per_mm_y + 0.5f);

    snprintf(buf, sizeof(buf), "STATUS SQ:%c%d | X:%ld Y:%ld mm | MAG:%s | M:1/%u (X:%ld Y:%ld) | DLY:%uus\r\n",
             file_char, rank, (long)(x_mm + 0.5f), (long)(y_mm + 0.5f),
             magnet_is_on() ? "ON" : "OFF", current_microstep, spm_x, spm_y, cruise_delay_us);
    uart_puts(buf);
}

static void parse_and_execute(char *cmd)
{
    while (*cmd == ' ' || *cmd == '\t') cmd++;
    if (*cmd == '\0') return;

    if (strncmp(cmd, "PING", 4) == 0) {
        uart_puts_P(PSTR("PONG\r\n"));
    }
    else if (strncmp(cmd, "HELP", 4) == 0) {
        uart_puts_P(PSTR("--- Automated Chessboard Gantry Commands ---\r\n"));
        uart_puts_P(PSTR("  MOVE <from><to>     : Execute chess move (e.g. MOVE a1b1 or e2e4)\r\n"));
        uart_puts_P(PSTR("  SQ <square>         : Move gantry to square without magnet (e.g. SQ e4)\r\n"));
        uart_puts_P(PSTR("  MAG <1|0>           : Turn electromagnet ON (1) or OFF (0)\r\n"));
        uart_puts_P(PSTR("  ZERO                : Set current position as square a1 (0, 0)\r\n"));
        uart_puts_P(PSTR("  POS                 : Query current square, position, and status\r\n"));
        uart_puts_P(PSTR("  G0 X<mm> Y<mm>      : Move orthogonal to coordinates in mm\r\n"));
        uart_puts_P(PSTR("  M <1|2|4|8|16|32>   : Set microstepping mode (e.g. M 8)\r\n"));
        uart_puts_P(PSTR("  SPEED <us>          : Set cruising step delay us (e.g. SPEED 400)\r\n"));
        uart_puts_P(PSTR("  EN <1|0>            : Manual motor enable/disable\r\n"));
        uart_puts_P(PSTR("OK\r\n"));
    }
    else if (strncmp(cmd, "POS", 3) == 0) {
        print_status();
        uart_puts_P(PSTR("OK\r\n"));
    }
    else if (strncmp(cmd, "ZERO", 4) == 0) {
        current_step_x = 0;
        current_step_y = 0;
        uart_puts_P(PSTR("OK ZEROED (Square a1)\r\n"));
    }
    else if (strncmp(cmd, "MAG", 3) == 0 || strncmp(cmd, "mag", 3) == 0) {
        char *p = cmd + 3;
        while (*p == ' ') p++;
        int state = atoi(p);
        magnet_set(state != 0);
        uart_puts_P(state ? PSTR("OK MAGNET ON\r\n") : PSTR("OK MAGNET OFF\r\n"));
    }
    else if (strncmp(cmd, "SQ", 2) == 0 || strncmp(cmd, "sq", 2) == 0) {
        char *p = cmd + 2;
        while (*p == ' ') p++;
        float tx, ty;
        if (parse_square(p, &tx, &ty)) {
            magnet_set(false);
            move_orthogonal_mm(tx, ty);
            print_status();
            uart_puts_P(PSTR("OK\r\n"));
        } else {
            uart_puts_P(PSTR("ERR: Invalid square (use a1..h8)\r\n"));
        }
    }
    else if (strncmp(cmd, "MOVE", 4) == 0 || strncmp(cmd, "move", 4) == 0) {
        char *p = cmd + 4;
        while (*p == ' ') p++;

        /* Supports both "MOVE a1b1" and "MOVE a1 b1" */
        char from_sq[3] = {0}, to_sq[3] = {0};
        if (strlen(p) >= 4) {
            from_sq[0] = p[0]; from_sq[1] = p[1];
            char *p2 = p + 2;
            while (*p2 == ' ') p2++;
            to_sq[0] = p2[0]; to_sq[1] = p2[1];

            if (execute_chess_move(from_sq, to_sq)) {
                print_status();
                uart_puts_P(PSTR("OK MOVE COMPLETE\r\n"));
            } else {
                uart_puts_P(PSTR("ERR: Invalid move squares\r\n"));
            }
        } else {
            uart_puts_P(PSTR("ERR: Format MOVE <from><to> (e.g. MOVE a1b1)\r\n"));
        }
    }
    /* Direct 4-char algebraic moves like "a1b1" or "e2e4" */
    else if (strlen(cmd) == 4 &&
             ((cmd[0] >= 'a' && cmd[0] <= 'h') || (cmd[0] >= 'A' && cmd[0] <= 'H')) &&
             (cmd[1] >= '1' && cmd[1] <= '8') &&
             ((cmd[2] >= 'a' && cmd[2] <= 'h') || (cmd[2] >= 'A' && cmd[2] <= 'H')) &&
             (cmd[3] >= '1' && cmd[3] <= '8')) {
        char from_sq[3] = {cmd[0], cmd[1], '\0'};
        char to_sq[3]   = {cmd[2], cmd[3], '\0'};
        if (execute_chess_move(from_sq, to_sq)) {
            print_status();
            uart_puts_P(PSTR("OK MOVE COMPLETE\r\n"));
        } else {
            uart_puts_P(PSTR("ERR: Move failed\r\n"));
        }
    }
    else if (strncmp(cmd, "G0", 2) == 0 || strncmp(cmd, "g0", 2) == 0) {
        char *px = strchr(cmd, 'X'); if (!px) px = strchr(cmd, 'x');
        char *py = strchr(cmd, 'Y'); if (!py) py = strchr(cmd, 'y');

        float tx = px ? atof(px + 1) : ((float)current_step_x / current_steps_per_mm_x);
        float ty = py ? atof(py + 1) : ((float)current_step_y / current_steps_per_mm_y);

        move_orthogonal_mm(tx, ty);
        print_status();
        uart_puts_P(PSTR("OK\r\n"));
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
    else if (strncmp(cmd, "SPEED", 5) == 0) {
        int us = atoi(cmd + 5);
        if (us >= 100 && us <= 5000) {
            cruise_delay_us = (uint16_t)us;
            start_delay_us = cruise_delay_us * 2;
            if (start_delay_us > 1500) start_delay_us = 1500;
            uart_puts_P(PSTR("OK SPEED UPDATED\r\n"));
        } else {
            uart_puts_P(PSTR("ERR: Speed must be 100..5000 us\r\n"));
        }
    }
    else if (strncmp(cmd, "EN", 2) == 0) {
        int state = atoi(cmd + 2);
        motors_enable(state != 0);
        uart_puts_P(state ? PSTR("OK MOTORS ENABLED\r\n") : PSTR("OK MOTORS DISABLED\r\n"));
    }
    else {
        uart_puts_P(PSTR("ERR: Unknown command. Type HELP for list.\r\n"));
    }
}

/* ==============================================================================
 * Main Routine
 * ============================================================================== */

int main(void)
{
    /* 1. Unlock JTAG pins on PORTC */
    MCUCSR |= (1U << JTD);
    MCUCSR |= (1U << JTD);

    /* 2. Configure Motor Control Pins (PORTC) as Outputs */
    MOTOR_PORT &= ~((1U << PIN_STEP_X) | (1U << PIN_STEP_Y));
    MOTOR_DDR  |= (1U << PIN_STEP_X) | (1U << PIN_DIR_X) |
                  (1U << PIN_STEP_Y) | (1U << PIN_DIR_Y) |
                  (1U << PIN_ENABLE) |
                  (1U << PIN_MODE0)  | (1U << PIN_MODE1) | (1U << PIN_MODE2);

    /* Start with motors DISABLED for silent/cool idle */
    motors_enable(false);
    set_microstepping(8); /* Default 1/8 microstep */

    /* 3. Configure CW020 Relay Module on PD2 (Physical Pin 16) */
    magnet_set(false);                    /* Ensure magnet is OFF */
    RELAY_DDR |= (1U << PIN_RELAY);       /* Set PD2 as Output */

    /* 4. Initialize UART */
    uart_init();

    _delay_ms(50);
    uart_puts_P(PSTR("\r\n========================================\r\n"));
    uart_puts_P(PSTR("ATmega32 Automated Chessboard Gantry\r\n"));
    uart_puts_P(PSTR("8x8 Grid (4cm squares) | CW020 Relay\r\n"));
    uart_puts_P(PSTR("Type HELP for commands.\r\n"));
    uart_puts_P(PSTR("========================================\r\n"));
    print_status();

    char rx_buf[RX_BUFFER_SIZE];
    uint8_t rx_idx = 0;

    while (1) {
        if (uart_available()) {
            char c = uart_getc();
            uart_putc(c); /* Echo back */

            if (c == '\r' || c == '\n') {
                uart_puts_P(PSTR("\r\n"));
                if (rx_idx > 0) {
                    rx_buf[rx_idx] = '\0';
                    parse_and_execute(rx_buf);
                    rx_idx = 0;
                }
            } else if (c == '\b' || c == 0x7F) {
                if (rx_idx > 0) rx_idx--;
            } else if (rx_idx < (RX_BUFFER_SIZE - 1)) {
                rx_buf[rx_idx++] = c;
            }
        }
    }

    return 0;
}
