/*
 * Firmware A - hardware port interface.
 *
 * rot.c and stm32_rom.c only touch hardware through these functions, so the
 * same policy code runs on the Dabao (port_dabao.c) and on the host against
 * an emulated H743 ROM bootloader (test/host_test.c).
 */
#ifndef ROT_PORT_H
#define ROT_PORT_H

#include <stdbool.h>
#include <stdint.h>

/* H743 NRST. true = asserted (H743 held in reset). */
void port_nrst_hold(bool hold);

/* H743 BOOT0. true = boot ST system memory (ROM bootloader) on next reset. */
void port_boot0(bool high);

/* Baochip <-> H743 USART link (115200 8E1, H743 USART1 PA9/PA10). */
void port_link_write(const uint8_t *data, uint32_t len);
/* Returns true and stores a byte, or false if nothing arrived in timeout_ms. */
bool port_link_read(uint8_t *byte, uint32_t timeout_ms);
/* Drop any stale received bytes. */
void port_link_flush(void);
/* Disconnect the link from the H743 once it runs its own firmware. */
void port_link_detach(void);

/* Operator recovery strap: true = forced re-provisioning requested. */
bool port_provision_strap(void);

void port_delay_ms(uint32_t ms);

/*
 * Diagnostic log. Only called while the H743 is in reset (and therefore not
 * listening on the shared UART), never during a ROM-bootloader session.
 */
void port_log(const char *msg);
void port_log_hex(const char *label, uint32_t value);

#endif
