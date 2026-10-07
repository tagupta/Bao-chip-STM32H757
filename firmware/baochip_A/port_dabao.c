/*
 * Firmware A on the Dabao (Baochip-1x): pin mapping, UART link, main().
 *
 * Dabao header         H743 (Kakute H7)
 *   PB14 UART2_TX  ->  PA10 USART1_RX  (RX1 pad)
 *   PB13 UART2_RX  <-  PA9  USART1_TX  (TX1 pad)
 *   PB2  GPIO      ->  NRST            (see ROT_NRST_INVERTED)
 *   PB3  GPIO      ->  BOOT0
 *   PB5  GPIO      <-  provision strap (jumper to GND = reinstall B)
 *   PB1  GPIO      ->  status LED
 *   GND            --  GND
 *
 * UART2 is also the Dabao console. Log lines are only emitted while the
 * H743 is held in reset, so a USB-serial adapter listening on PB14 (115200
 * 8E1) sees them without disturbing the ROM-bootloader session.
 */
#include "bao.h"
#include "hardware/regs/addressmap.h"
#include "hardware/regs/udma.h"

#include "rot.h"
#include "rot_payload.h"
#include "rot_port.h"

#ifndef ROT_NRST_INVERTED
/* 0: PB2 drives NRST directly (low = reset). 1: PB2 drives an N-MOSFET gate. */
#define ROT_NRST_INVERTED 0
#endif
#ifndef ROT_WRITE_PROTECT_B
#define ROT_WRITE_PROTECT_B 1
#endif
#ifndef ROT_CHECK_FULL_SECTOR
#define ROT_CHECK_FULL_SECTOR 1
#endif

#define LINK_UART     2u
#define LINK_BAUD     115200u
#define UART_PARITY_EN (1u << 0)

#define NRST_PORT  GPIO_PORT_B
#define NRST_PIN   2u
#define BOOT0_PORT GPIO_PORT_B
#define BOOT0_PIN  3u
#define STRAP_PORT GPIO_PORT_B
#define STRAP_PIN  5u
#define LED_PORT   GPIO_PORT_B
#define LED_PIN    1u

static bool link_detached;

void port_nrst_hold(bool hold)
{
    gpio_put(NRST_PORT, NRST_PIN, ROT_NRST_INVERTED ? hold : !hold);
}

void port_boot0(bool high)
{
    gpio_put(BOOT0_PORT, BOOT0_PIN, high);
}

void port_link_write(const uint8_t *data, uint32_t len)
{
    uart_write(LINK_UART, data, len);
}

bool port_link_read(uint8_t *byte, uint32_t timeout_ms)
{
    uint64_t start = millis();

    while (!uart_is_readable(LINK_UART)) {
        if (millis() - start >= timeout_ms) {
            return false;
        }
    }
    *byte = (uint8_t)(REG32(UDMA_UART2_BASE + UDMA_UART_DATA_OFFSET) & 0xFFu);
    return true;
}

void port_link_flush(void)
{
    uint32_t guard;

    for (guard = 0; guard < 4096u && uart_is_readable(LINK_UART); guard++) {
        (void)REG32(UDMA_UART2_BASE + UDMA_UART_DATA_OFFSET);
    }
}

void port_link_detach(void)
{
    /* Stop driving the H743's USART1 RX once it runs B and then C. */
    gpio_set_function(GPIO_PORT_B, 14u, GPIO_FUNC_GPIO);
    gpio_set_dir(GPIO_PORT_B, 14u, false);
    link_detached = true;
}

bool port_provision_strap(void)
{
    return !gpio_get(STRAP_PORT, STRAP_PIN);
}

void port_delay_ms(uint32_t ms)
{
    delay_ms(ms);
}

void port_log(const char *msg)
{
    if (!link_detached) {
        mini_printf("[A] %s\r\n", msg);
    }
}

void port_log_hex(const char *label, uint32_t value)
{
    if (!link_detached) {
        mini_printf("[A] %s 0x%08x\r\n", label, value);
    }
}

static void output_pin(uint port, uint pin, bool value)
{
    gpio_init(port, pin);
    gpio_put(port, pin, value);
    gpio_set_dir(port, pin, true);
}

static void link_uart_init(void)
{
    volatile uint32_t *setup =
        (volatile uint32_t *)(uintptr_t)(UDMA_UART2_BASE + UDMA_UART_SETUP_OFFSET);

    /* The STM32 ROM bootloader needs even parity; the UDMA UART's is even. */
    uart_init(LINK_UART, LINK_BAUD);
    *setup = *setup | UART_PARITY_EN;
    memory_fence();
}

int main(void)
{
    rot_config_t cfg;
    rot_result_t result;
    uint32_t i;

    /* Assert NRST before anything else runs. */
    output_pin(NRST_PORT, NRST_PIN, ROT_NRST_INVERTED ? true : false);
    output_pin(BOOT0_PORT, BOOT0_PIN, false);
    output_pin(LED_PORT, LED_PIN, false);
    gpio_init(STRAP_PORT, STRAP_PIN);
    gpio_pull_up(STRAP_PORT, STRAP_PIN);

    bao_init();
    link_uart_init();
    delay_ms(5);

    port_log("HoloDi firmware A: Baochip root of trust for H743");

    cfg.pubkey = HOLODI_PUBKEY;
    cfg.bundle = B_BUNDLE;
    cfg.bundle_len = B_BUNDLE_LEN;
    cfg.write_protect_b = ROT_WRITE_PROTECT_B;
    cfg.check_full_sector = ROT_CHECK_FULL_SECTOR;

    result = rot_run(&cfg);
    port_log(rot_result_name(result));

    if (rot_released(result)) {
        gpio_put(LED_PORT, LED_PIN, true);
        while (1) {
            __asm__ volatile ("wfi");
        }
    }

    /* Fail closed: NRST stays asserted; blink the result code forever. */
    while (1) {
        port_nrst_hold(true);
        for (i = 0; i < (uint32_t)result; i++) {
            gpio_put(LED_PORT, LED_PIN, true);
            delay_ms(150);
            gpio_put(LED_PORT, LED_PIN, false);
            delay_ms(250);
        }
        delay_ms(1500);
    }
}
