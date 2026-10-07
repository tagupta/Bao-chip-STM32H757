#include "stm32_rom.h"
#include "rot_port.h"

#define CMD_GET_ID          0x02
#define CMD_READ_MEMORY     0x11
#define CMD_WRITE_MEMORY    0x31
#define CMD_EXTENDED_ERASE  0x44
#define CMD_WRITE_PROTECT   0x63
#define CMD_WRITE_UNPROTECT 0x73

#define TIMEOUT_MS          1000u
/* H743 sector erase is specified up to ~4 s; leave a wide margin. */
#define ERASE_TIMEOUT_MS    15000u
#define BOOT_SETTLE_MS      150u
#define SYNC_ATTEMPTS       10u

static rom_status_t wait_ack(uint32_t timeout_ms)
{
    uint8_t b;

    if (!port_link_read(&b, timeout_ms)) {
        return ROM_TIMEOUT;
    }
    if (b == STM32_ACK) {
        return ROM_OK;
    }
    if (b == STM32_NACK) {
        return ROM_NACK;
    }
    return ROM_PROTOCOL;
}

static rom_status_t send_cmd(uint8_t cmd)
{
    uint8_t frame[2] = { cmd, (uint8_t)(cmd ^ 0xFFu) };

    port_link_write(frame, 2);
    return wait_ack(TIMEOUT_MS);
}

static rom_status_t send_addr(uint32_t addr)
{
    uint8_t frame[5];

    frame[0] = (uint8_t)(addr >> 24);
    frame[1] = (uint8_t)(addr >> 16);
    frame[2] = (uint8_t)(addr >> 8);
    frame[3] = (uint8_t)addr;
    frame[4] = frame[0] ^ frame[1] ^ frame[2] ^ frame[3];
    port_link_write(frame, 5);
    return wait_ack(TIMEOUT_MS);
}

void stm32_rom_enter(void)
{
    port_nrst_hold(true);
    port_boot0(true);
    port_delay_ms(10);
    port_link_flush();
    port_nrst_hold(false);
    port_delay_ms(BOOT_SETTLE_MS);
}

rom_status_t stm32_rom_sync(void)
{
    static const uint8_t sync = 0x7F;
    uint32_t attempt;
    rom_status_t st = ROM_TIMEOUT;

    for (attempt = 0; attempt < SYNC_ATTEMPTS; attempt++) {
        port_link_write(&sync, 1);
        st = wait_ack(200);
        if (st == ROM_OK) {
            return ROM_OK;
        }
        /* A NACK after a lost ACK means auto-baud already locked. */
        if (st == ROM_NACK && attempt > 0) {
            return ROM_OK;
        }
    }
    return st;
}

rom_status_t stm32_rom_get_id(uint16_t *pid)
{
    uint8_t n, hi, lo;
    rom_status_t st = send_cmd(CMD_GET_ID);

    if (st != ROM_OK) {
        return st;
    }
    if (!port_link_read(&n, TIMEOUT_MS) || n != 1 ||
        !port_link_read(&hi, TIMEOUT_MS) || !port_link_read(&lo, TIMEOUT_MS)) {
        return ROM_PROTOCOL;
    }
    *pid = (uint16_t)((hi << 8) | lo);
    return wait_ack(TIMEOUT_MS);
}

rom_status_t stm32_rom_read(uint32_t addr, uint8_t *buf, uint32_t len)
{
    uint8_t frame[2];
    uint32_t i;
    rom_status_t st;

    if (len == 0 || len > STM32_ROM_MAX_XFER) {
        return ROM_PROTOCOL;
    }
    if ((st = send_cmd(CMD_READ_MEMORY)) != ROM_OK) {
        return st;
    }
    if ((st = send_addr(addr)) != ROM_OK) {
        return st;
    }
    frame[0] = (uint8_t)(len - 1);
    frame[1] = (uint8_t)(frame[0] ^ 0xFFu);
    port_link_write(frame, 2);
    if ((st = wait_ack(TIMEOUT_MS)) != ROM_OK) {
        return st;
    }
    for (i = 0; i < len; i++) {
        if (!port_link_read(&buf[i], TIMEOUT_MS)) {
            return ROM_TIMEOUT;
        }
    }
    return ROM_OK;
}

rom_status_t stm32_rom_write(uint32_t addr, const uint8_t *buf, uint32_t len)
{
    uint8_t n, csum;
    uint32_t i;
    rom_status_t st;

    if (len == 0 || len > STM32_ROM_MAX_XFER || (len & 3u) != 0) {
        return ROM_PROTOCOL;
    }
    if ((st = send_cmd(CMD_WRITE_MEMORY)) != ROM_OK) {
        return st;
    }
    if ((st = send_addr(addr)) != ROM_OK) {
        return st;
    }
    n = (uint8_t)(len - 1);
    csum = n;
    for (i = 0; i < len; i++) {
        csum ^= buf[i];
    }
    port_link_write(&n, 1);
    port_link_write(buf, len);
    port_link_write(&csum, 1);
    return wait_ack(TIMEOUT_MS);
}

rom_status_t stm32_rom_erase_sector(uint16_t sector)
{
    uint8_t frame[5];
    rom_status_t st;

    if ((st = send_cmd(CMD_EXTENDED_ERASE)) != ROM_OK) {
        return st;
    }
    frame[0] = 0x00;                    /* N-1 = 0: one sector */
    frame[1] = 0x00;
    frame[2] = (uint8_t)(sector >> 8);
    frame[3] = (uint8_t)sector;
    frame[4] = frame[0] ^ frame[1] ^ frame[2] ^ frame[3];
    port_link_write(frame, 5);
    return wait_ack(ERASE_TIMEOUT_MS);
}

rom_status_t stm32_rom_write_protect(const uint8_t *sectors, uint8_t count)
{
    uint8_t n, csum;
    uint8_t i;
    rom_status_t st;

    if (count == 0) {
        return ROM_PROTOCOL;
    }
    if ((st = send_cmd(CMD_WRITE_PROTECT)) != ROM_OK) {
        return st;
    }
    n = (uint8_t)(count - 1);
    csum = n;
    for (i = 0; i < count; i++) {
        csum ^= sectors[i];
    }
    port_link_write(&n, 1);
    port_link_write(sectors, count);
    port_link_write(&csum, 1);
    return wait_ack(TIMEOUT_MS);
}

rom_status_t stm32_rom_write_unprotect(void)
{
    rom_status_t st = send_cmd(CMD_WRITE_UNPROTECT);

    if (st != ROM_OK) {
        return st;
    }
    return wait_ack(TIMEOUT_MS);
}
