#ifndef F_CPU
#define F_CPU 1000000UL
#endif

#include <avr/interrupt.h>
#include <avr/io.h>
#include <util/delay.h>
#include <stdint.h>

#define UART_BAUD 9600UL
#define UART_UBRR ((F_CPU + (UART_BAUD * 4UL)) / (UART_BAUD * 8UL) - 1UL)

/*
 * Multiplexer Pin Mapping on ATmega32 PORTA:
 *   PA0..PA3 : S0..S3 common multiplexer channel select outputs.
 *   PA4      : SIG0 input (Mux 0: Files 'a' on C0..C7 and 'b' on C8..C15)
 *   PA5      : SIG1 input (Mux 1: Files 'c' on C0..C7 and 'd' on C8..C15)
 *   PA6      : SIG2 input (Mux 2: Files 'e' on C0..C7 and 'f' on C8..C15)
 *   PA7      : SIG3 input (Mux 3: Files 'g' on C0..C7 and 'h' on C8..C15)
 */
#define MUX_SELECT_MASK 0x0FU
#define MUX_SIG_MASK    0xF0U

#define MUX_SIG0_PIN    PA4
#define MUX_SIG1_PIN    PA5
#define MUX_SIG2_PIN    PA6
#define MUX_SIG3_PIN    PA7

#define BOARD_RANKS     8U
#define BOARD_FILES     8U
#define MUX_CHANNELS    16U

/*
 * board[rank] holds the state for ranks 1..8 (index 0..7).
 * Bit 0 corresponds to file 'a', bit 1 to 'b', ..., bit 7 to 'h'.
 * A set bit (1) means reed switch closed / magnet present.
 */
static uint8_t board[BOARD_RANKS];
static volatile uint8_t scan_requested;

static void uart_init(void)
{
    /* U2X=1 gives an accurate 9600-baud clock when F_CPU is 1 MHz (UBRR = 12). */
    UCSRA = (1U << U2X);
    UCSRB = (1U << TXEN);
    UCSRC = (1U << URSEL) | (1U << UCSZ1) | (1U << UCSZ0);
    UBRRH = (uint8_t)(UART_UBRR >> 8);
    UBRRL = (uint8_t)UART_UBRR;
}

static void uart_putc(char value)
{
    while ((UCSRA & (1U << UDRE)) == 0U) {
        /* Wait until the transmit buffer is available. */
    }
    UDR = (uint8_t)value;
}

static void uart_puts(const char *text)
{
    while (*text != '\0') {
        uart_putc(*text++);
    }
}

static void mux_init(void)
{
    /* Configure PA0..PA3 as outputs (S0..S3) and PA4..PA7 as inputs (SIG0..SIG3). */
    DDRA = (DDRA & (uint8_t)~MUX_SIG_MASK) | MUX_SELECT_MASK;

    /*
     * Enable internal pull-ups on PA4..PA7 (SIG lines).
     * Initialize S0..S3 to channel 0.
     * When a reed switch closes to GND, the corresponding SIG pin reads 0.
     */
    PORTA = (PORTA & (uint8_t)~MUX_SELECT_MASK) | MUX_SIG_MASK;
}

static void mux_select(uint8_t channel)
{
    /* Preserve PA4..PA7 pull-up settings and update S0..S3. */
    PORTA = (PORTA & (uint8_t)~MUX_SELECT_MASK) | (channel & MUX_SELECT_MASK);
    /* Allow analog mux switch and line capacitance to settle. */
    _delay_us(10);
}

/*
 * Scans all 64 reed switches using 16 multiplexer channel select steps.
 *
 * Channel mapping:
 *   Channels 0..7 (C0..C7):
 *     Rank = channel (ranks 1..8)
 *     Mux 0 (PA4) -> file 'a' (bit 0)
 *     Mux 1 (PA5) -> file 'c' (bit 2)
 *     Mux 2 (PA6) -> file 'e' (bit 4)
 *     Mux 3 (PA7) -> file 'g' (bit 6)
 *   Channels 8..15 (C8..C15):
 *     Rank = channel - 8 (ranks 1..8)
 *     Mux 0 (PA4) -> file 'b' (bit 1)
 *     Mux 1 (PA5) -> file 'd' (bit 3)
 *     Mux 2 (PA6) -> file 'f' (bit 5)
 *     Mux 3 (PA7) -> file 'h' (bit 7)
 */
static void scan_board(void)
{
    uint8_t channel;

    for (channel = 0U; channel < MUX_CHANNELS; channel++) {
        uint8_t rank;
        uint8_t file_base;
        uint8_t sig_sample;
        uint8_t mux_idx;

        if (channel < 8U) {
            rank = channel;
            file_base = 0U; /* Files a, c, e, g */
        } else {
            rank = (uint8_t)(channel - 8U);
            file_base = 1U; /* Files b, d, f, h */
        }

        mux_select(channel);
        sig_sample = PINA;

        for (mux_idx = 0U; mux_idx < 4U; mux_idx++) {
            uint8_t sig_pin = (uint8_t)(MUX_SIG0_PIN + mux_idx);
            uint8_t file = (uint8_t)(file_base + (mux_idx * 2U));

            /* Input is low (0) when reed switch is closed to GND (magnet present). */
            if ((sig_sample & (uint8_t)(1U << sig_pin)) == 0U) {
                board[rank] |= (uint8_t)(1U << file);
            } else {
                board[rank] &= (uint8_t)~(1U << file);
            }
        }
    }
}

/*
 * Sends machine-friendly frame over UART:
 *   BOARD:r1/r2/r3/r4/r5/r6/r7/r8\r\n
 * Where each rank rx is 8 characters ('1' for occupied, '0' for empty)
 * ordered from file 'a' to file 'h'.
 */
static void send_board(void)
{
    uint8_t rank;
    uint8_t file;

    uart_puts("BOARD:");
    for (rank = 0U; rank < BOARD_RANKS; rank++) {
        for (file = 0U; file < BOARD_FILES; file++) {
            uart_putc((board[rank] & (1U << file)) != 0U ? '1' : '0');
        }
        if (rank != (BOARD_RANKS - 1U)) {
            uart_putc('/');
        }
    }
    uart_puts("\r\n");
}

static void scan_button_init(void)
{
    /* PD2/INT0 is an input with its internal pull-up enabled. */
    DDRD &= (uint8_t)~(1U << PD2);
    PORTD |= (1U << PD2);

    /* A push-button short to ground produces a falling-edge interrupt. */
    MCUCR = (MCUCR & (uint8_t)~(1U << ISC00)) | (1U << ISC01);
    GIFR = (1U << INTF0);
    GICR |= (1U << INT0);
}

ISR(INT0_vect)
{
    scan_requested = 1U;
}

int main(void)
{
    mux_init();
    uart_init();
    scan_button_init();
    sei();

    uart_puts("8x8 reed chessboard ready; ground PD2 to scan.\r\n");

    /* Initial power-up scan to establish Mac baseline immediately. */
    scan_board();
    send_board();

    for (;;) {
        if (scan_requested != 0U) {
            /* Mechanical switch debounce delay. */
            _delay_ms(25);

            scan_board();
            send_board();

            /* Discard bounce accumulated while scanning/transmitting. */
            cli();
            scan_requested = 0U;
            GIFR = (1U << INTF0);
            sei();
        }
    }
}
