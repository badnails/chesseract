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
 * UNIFIED AUTOMATED CHESSBOARD FIRMWARE (ATmega32)
 * ==============================================================================
 * Integrates:
 *   1. 8x8 Reed Switch Matrix via 4x CD74HC4067 Multiplexers (PORTA)
 *   2. INT0 Pushbutton scan trigger (PD2)
 *   3. Dual NEMA17 Steppers on DRV8825 (PORTC)
 *   4. CW020 Electromagnet Relay (PD3)
 *   5. Bidirectional UART communication (PD0/PD1 @ 9600 Baud)
 *
 * Pin Mapping:
 *   --- Multiplexer Board Sensing (PORTA) ---
 *   Pin 40 (PA0) : Mux Address Select S0 (LSB)
 *   Pin 39 (PA1) : Mux Address Select S1
 *   Pin 38 (PA2) : Mux Address Select S2
 *   Pin 37 (PA3) : Mux Address Select S3 (MSB)
 *   Pin 36 (PA4) : SIG0 (Mux 0: Files a & b)
 *   Pin 35 (PA5) : SIG1 (Mux 1: Files c & d)
 *   Pin 34 (PA6) : SIG2 (Mux 2: Files e & f)
 *   Pin 33 (PA7) : SIG3 (Mux 3: Files g & h)
 *
 *   --- Dual DRV8825 Stepper Gantry (PORTC) ---
 *   Pin 22 (PC0) : Motor X STEP (Rank axis, 1..8)
 *   Pin 23 (PC1) : Motor X DIR
 *   Pin 24 (PC2) : Motor Y STEP (File axis, a..h)
 *   Pin 25 (PC3) : Motor Y DIR
 *   Pin 26 (PC4) : Motor ENABLE (Active LOW)
 *   Pin 27 (PC5) : MODE0 (Microstep select 0)
 *   Pin 28 (PC6) : MODE1 (Microstep select 1)
 *   Pin 29 (PC7) : MODE2 (Microstep select 2)
 *
 *   --- Control & Serial (PORTD) ---
 *   Pin 14 (PD0) : UART RXD (from Host USB-UART TX / HC-05 TXD)
 *   Pin 15 (PD1) : UART TXD (to Host USB-UART RX / HC-05 RXD)
 *   Pin 16 (PD2) : INT0 Pushbutton to GND (Falling Edge Scan Trigger)
 *   Pin 17 (PD3) : CW020 Relay Module IN (Active LOW trigger for electromagnet)
 * ==============================================================================
 */

/* Multiplexer Defines */
#define MUX_SELECT_MASK 0x0FU
#define MUX_SIG_MASK    0xF0U
#define MUX_SIG0_PIN    PA4
#define MUX_SIG1_PIN    PA5
#define MUX_SIG2_PIN    PA6
#define MUX_SIG3_PIN    PA7
#define BOARD_RANKS     8U
#define BOARD_FILES     8U
#define MUX_CHANNELS    16U

/* Gantry Motor Defines (PORTC) */
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

/* Electromagnet Relay Defines (PD3) */
#define PIN_RELAY      PD3
#define RELAY_PORT     PORTD
#define RELAY_DDR      DDRD
#define RELAY_ACTIVE_LOW 1

/* Kinematics (40mm Square Grid) */
#define SQUARE_SIZE_MM        40.0f
#define BASE_STEPS_PER_MM_X   5.0f   /* 20T GT2 pulley -> 5.0 full steps/mm */
#define BASE_STEPS_PER_MM_Y   2.5f   /* 40T GT2 pulley -> 2.5 full steps/mm */

/* UART Defines (9600 Baud @ 1 MHz with U2X=1) */
#define UART_BAUD           9600UL
#define UART_UBRR           ((F_CPU + (UART_BAUD * 4UL)) / (UART_BAUD * 8UL) - 1UL)
#define RX_BUFFER_SIZE      64

/* State Variables */
static uint8_t board_state[BOARD_RANKS];
static volatile uint8_t scan_requested = 0;

static int32_t current_step_x = 0;
static int32_t current_step_y = 0;
static uint8_t current_microstep = 8;        /* Default 1/8 microstepping */
static float current_steps_per_mm_x = 40.0f; /* 5.0 * 8 = 40 steps/mm */
static float current_steps_per_mm_y = 20.0f; /* 2.5 * 8 = 20 steps/mm */
static uint16_t cruise_delay_us = 400;
static uint16_t start_delay_us = 900;

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
 * Reed Switch Multiplexer Scanner (PORTA)
 * ============================================================================== */
static void mux_init(void)
{
    /* PA0..PA3 as outputs (S0..S3), PA4..PA7 as inputs (SIG0..SIG3) */
    DDRA = (DDRA & (uint8_t)~MUX_SIG_MASK) | MUX_SELECT_MASK;
    /* Internal pull-ups on SIG lines (PA4..PA7). Initialize S0..S3 to 0 */
    PORTA = (PORTA & (uint8_t)~MUX_SELECT_MASK) | MUX_SIG_MASK;
}

static void mux_select(uint8_t channel)
{
    PORTA = (PORTA & (uint8_t)~MUX_SELECT_MASK) | (channel & MUX_SELECT_MASK);
    _delay_us(10); /* Settle analog multiplexer capacitance */
}

static void scan_board(void)
{
    for (uint8_t channel = 0U; channel < MUX_CHANNELS; channel++) {
        uint8_t rank;
        uint8_t file_base;

        if (channel < 8U) {
            rank = channel;
            file_base = 0U; /* Files a, c, e, g */
        } else {
            rank = (uint8_t)(channel - 8U);
            file_base = 1U; /* Files b, d, f, h */
        }

        mux_select(channel);
        uint8_t sig_sample = PINA;

        for (uint8_t mux_idx = 0U; mux_idx < 4U; mux_idx++) {
            uint8_t sig_pin = (uint8_t)(MUX_SIG0_PIN + mux_idx);
            uint8_t file = (uint8_t)(file_base + (mux_idx * 2U));

            /* Switch closed to GND reads 0 -> Piece present */
            if ((sig_sample & (uint8_t)(1U << sig_pin)) == 0U) {
                board_state[rank] |= (uint8_t)(1U << file);
            } else {
                board_state[rank] &= (uint8_t)~(1U << file);
            }
        }
    }
}

static void send_board(void)
{
    uart_puts("BOARD:");
    for (uint8_t rank = 0U; rank < BOARD_RANKS; rank++) {
        for (uint8_t file = 0U; file < BOARD_FILES; file++) {
            uart_putc((board_state[rank] & (1U << file)) != 0U ? '1' : '0');
        }
        if (rank != (BOARD_RANKS - 1U)) {
            uart_putc('/');
        }
    }
    uart_puts("\r\n");
}

/* ==============================================================================
 * INT0 Pushbutton Scanner Trigger (PD2)
 * ============================================================================== */
static void scan_button_init(void)
{
    /* PD2 as input with pull-up enabled */
    DDRD &= (uint8_t)~(1U << PD2);
    PORTD |= (1U << PD2);

    /* Falling edge on INT0 triggers interrupt */
    MCUCR = (MCUCR & (uint8_t)~(1U << ISC00)) | (1U << ISC01);
    GIFR = (1U << INTF0);
    GICR |= (1U << INT0);
}

ISR(INT0_vect)
{
    scan_requested = 1U;
}

/* ==============================================================================
 * Electromagnet Relay Control (PD3)
 * ============================================================================== */
static void magnet_init(void)
{
    RELAY_DDR |= (1U << PIN_RELAY);
#if RELAY_ACTIVE_LOW
    RELAY_PORT |= (1U << PIN_RELAY); /* HIGH = OFF initially */
#else
    RELAY_PORT &= ~(1U << PIN_RELAY);
#endif
}

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
 * Stepper Driver Control (PORTC)
 * ============================================================================== */
static void motors_enable(bool enable)
{
    if (enable) {
        MOTOR_PORT &= ~(1U << PIN_ENABLE); /* Active LOW enables outputs */
        _delay_ms(5);
    } else {
        MOTOR_PORT |= (1U << PIN_ENABLE);  /* HIGH relaxes motors */
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

static void step_single_axis(uint8_t step_pin, uint8_t dir_pin, int32_t steps)
{
    if (steps == 0) return;

    motors_enable(true);

    if (steps > 0) {
        MOTOR_PORT &= ~(1U << dir_pin);
    } else {
        MOTOR_PORT |= (1U << dir_pin);
        steps = -steps;
    }
    _delay_us(5);

    int32_t accel_steps = steps / 4;
    if (accel_steps > 80) accel_steps = 80;

    uint16_t delay = start_delay_us;
    uint16_t delay_diff = (start_delay_us > cruise_delay_us) ? (start_delay_us - cruise_delay_us) : 0;

    for (int32_t i = 0; i < steps; ++i) {
        MOTOR_PORT |= (1U << step_pin);
        _delay_us(5);
        MOTOR_PORT &= ~(1U << step_pin);

        if (accel_steps > 0) {
            if (i < accel_steps) {
                delay = start_delay_us - (uint16_t)(((uint32_t)delay_diff * i) / accel_steps);
            } else if (i >= (steps - accel_steps)) {
                int32_t remaining = steps - 1 - i;
                delay = cruise_delay_us + (uint16_t)(((uint32_t)delay_diff * (accel_steps - remaining)) / accel_steps);
            } else {
                delay = cruise_delay_us;
            }
        } else {
            delay = cruise_delay_us;
        }

        delay_microseconds(delay);
    }
}

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
        _delay_ms(30);
    }

    /* Move Y (File) second */
    if (dy != 0) {
        step_single_axis(PIN_STEP_Y, PIN_DIR_Y, dy);
        current_step_y = target_step_y;
        _delay_ms(30);
    }

    _delay_ms(100);
    motors_enable(false);
}

static bool parse_square(const char *sq, float *x_mm, float *y_mm)
{
    char file = sq[0];
    char rank = sq[1];

    if (file >= 'A' && file <= 'H') file += ('a' - 'A');
    if (file < 'a' || file > 'h') return false;
    if (rank < '1' || rank > '8') return false;

    *x_mm = (float)(rank - '1') * SQUARE_SIZE_MM;
    *y_mm = (float)(file - 'a') * SQUARE_SIZE_MM;
    return true;
}

static bool execute_chess_move(const char *from_sq, const char *to_sq)
{
    float x_from, y_from, x_to, y_to;
    if (!parse_square(from_sq, &x_from, &y_from)) return false;
    if (!parse_square(to_sq, &x_to, &y_to)) return false;

    /* 1. Travel empty to pickup square */
    magnet_set(false);
    move_orthogonal_mm(x_from, y_from);

    /* 2. Engage Electromagnet */
    magnet_set(true);
    _delay_ms(250);

    /* 3. Transfer piece to destination square */
    move_orthogonal_mm(x_to, y_to);

    /* 4. Release piece */
    magnet_set(false);
    _delay_ms(200);

    return true;
}

/* ==============================================================================
 * Command Interpreter
 * ============================================================================== */
static void print_status(void)
{
    char buf[100];
    float x_mm = (float)current_step_x / current_steps_per_mm_x;
    float y_mm = (float)current_step_y / current_steps_per_mm_y;

    int rank = (int)((x_mm + 20.0f) / SQUARE_SIZE_MM) + 1;
    int file_idx = (int)((y_mm + 20.0f) / SQUARE_SIZE_MM);
    if (rank < 1) rank = 1; else if (rank > 8) rank = 8;
    if (file_idx < 0) file_idx = 0; else if (file_idx > 7) file_idx = 7;
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
    /* Strip leading whitespace, nulls, and garbage power-cycle bytes */
    while (*cmd && ((uint8_t)*cmd <= ' ' || (uint8_t)*cmd > 0x7E)) cmd++;
    if (*cmd == '\0') return;

    if (strncmp(cmd, "PING", 4) == 0) {
        uart_puts_P(PSTR("PONG\r\n"));
    }
    else if (strncmp(cmd, "SCAN", 4) == 0) {
        scan_board();
        send_board();
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
            uart_puts_P(PSTR("ERR: Format MOVE <from><to>\r\n"));
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
        uart_puts_P(PSTR("ERR: Unknown command\r\n"));
    }
}

/* ==============================================================================
 * Main Application
 * ============================================================================== */
int main(void)
{
    /* 1. Disable JTAG on PORTC */
    MCUCSR |= (1U << JTD);
    MCUCSR |= (1U << JTD);

    /* 2. Configure DRV8825 Motor Control Pins (PORTC) */
    MOTOR_PORT &= ~((1U << PIN_STEP_X) | (1U << PIN_STEP_Y));
    MOTOR_DDR  |= (1U << PIN_STEP_X) | (1U << PIN_DIR_X) |
                  (1U << PIN_STEP_Y) | (1U << PIN_DIR_Y) |
                  (1U << PIN_ENABLE) |
                  (1U << PIN_MODE0)  | (1U << PIN_MODE1) | (1U << PIN_MODE2);

    motors_enable(false);
    set_microstepping(8); /* Default 1/8 microstepping */

    /* 3. Configure CW020 Electromagnet Relay (PD3) */
    magnet_init();

    /* 4. Configure Multiplexers (PORTA) */
    mux_init();

    /* 5. Configure Scan Pushbutton (PD2 / INT0) */
    scan_button_init();

    /* 6. Configure UART (9600 Baud) */
    uart_init();

    /* 7. Enable Global Interrupts */
    sei();

    _delay_ms(50);
    uart_puts_P(PSTR("\r\n========================================\r\n"));
    uart_puts_P(PSTR("ATmega32 Unified Automated Chessboard\r\n"));
    uart_puts_P(PSTR("Sensors: PORTA (Muxes) | Motors: PORTC\r\n"));
    uart_puts_P(PSTR("Magnet: PD3 (Relay) | Scan Btn: PD2 (INT0)\r\n"));
    uart_puts_P(PSTR("========================================\r\n"));

    /* Initial power-up scan to establish Mac baseline */
    scan_board();
    send_board();
    print_status();

    char rx_buf[RX_BUFFER_SIZE];
    uint8_t rx_idx = 0;

    for (;;) {
        /* Handle INT0 Pushbutton Scan Event */
        if (scan_requested != 0U) {
            _delay_ms(25); /* Mechanical switch debounce */
            scan_board();
            send_board();

            cli();
            scan_requested = 0U;
            GIFR = (1U << INTF0);
            sei();
        }

        /* Handle Incoming UART Commands */
        if (uart_available()) {
            char c = uart_getc();

            if (c == '\r' || c == '\n') {
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
