/*
 * Client for the STM32 factory ROM (system-memory) bootloader, USART
 * protocol (ST AN3155). This is the "Baochip -> UART -> H743 ROM
 * bootloader" path from the architecture document (milestone M1); it is
 * what lets Baochip read, erase and program H743 flash without any code of
 * its own running on the H743.
 */
#ifndef STM32_ROM_H
#define STM32_ROM_H

#include <stdbool.h>
#include <stdint.h>

#define STM32_ACK   0x79
#define STM32_NACK  0x1F

#define STM32H74X_PID 0x0450

#define STM32_ROM_MAX_XFER 256

typedef enum {
    ROM_OK = 0,
    ROM_TIMEOUT,
    ROM_NACK,
    ROM_PROTOCOL,
} rom_status_t;

/* Hold reset, set BOOT0, release: the H743 starts in its ROM bootloader. */
void stm32_rom_enter(void);

/* 0x7F auto-baud synchronisation. */
rom_status_t stm32_rom_sync(void);

/* GET_ID (0x02). */
rom_status_t stm32_rom_get_id(uint16_t *pid);

/* READ_MEMORY (0x11), len 1..256. Fails with ROM_NACK if RDP is active. */
rom_status_t stm32_rom_read(uint32_t addr, uint8_t *buf, uint32_t len);

/* WRITE_MEMORY (0x31), len 1..256, multiple of 4. */
rom_status_t stm32_rom_write(uint32_t addr, const uint8_t *buf, uint32_t len);

/* EXTENDED_ERASE (0x44) of one flash sector (H7: 128 KiB sectors). */
rom_status_t stm32_rom_erase_sector(uint16_t sector);

/* WRITE_PROTECT (0x63) of the given sectors. The ROM resets the H743 after. */
rom_status_t stm32_rom_write_protect(const uint8_t *sectors, uint8_t count);

/* WRITE_UNPROTECT (0x73), all sectors. The ROM resets the H743 after. */
rom_status_t stm32_rom_write_unprotect(void);

#endif
