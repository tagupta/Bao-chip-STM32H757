/*
 * Host test for firmware A: rot.c + stm32_rom.c + Monocypher, run against a
 * byte-level emulation of the STM32H743 ROM bootloader (AN3155 over USART)
 * and the NRST/BOOT0 pins. Uses the real signed B bundle and HoloDi key.
 *
 *   test/run.sh
 */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "rot.h"
#include "rot_payload.h"
#include "rot_port.h"

/* ------------------------------------------------------------------ H743 */

#define FLASH_SIZE (256u * 1024u)

typedef enum {
    H_RESET,        /* NRST asserted */
    H_RUNNING,      /* released with BOOT0 low: executing sector 0 */
    H_UNSYNCED,     /* ROM bootloader, waiting for 0x7F */
    H_CMD,
    H_ADDR,
    H_READ_LEN,
    H_WRITE_LEN,
    H_WRITE_DATA,
    H_ERASE,
    H_WP_LEN,
    H_WP_DATA,
} h_state_t;

static struct {
    uint8_t flash[FLASH_SIZE];
    bool wrp0, rdp, dead;
    uint16_t pid;
    bool nrst_held, boot0;
    h_state_t st;
    uint8_t cmd, buf[300];
    uint32_t have, need, addr;
    uint8_t tx[1024];
    uint32_t tx_head, tx_tail;
    /* observations */
    unsigned releases, erases, writes;
    bool logged_in_session;
    bool strap;
} h;

static void h_send(uint8_t b)
{
    h.tx[h.tx_tail++ % sizeof h.tx] = b;
}

/* MCU reset; bytes already on the wire (e.g. the final ACK) still arrive. */
static void h_rom_reset(void)
{
    h.st = h.boot0 ? H_UNSYNCED : H_RUNNING;
}

static uint8_t xor_bytes(const uint8_t *p, uint32_t n)
{
    uint8_t x = 0;
    while (n--) {
        x ^= *p++;
    }
    return x;
}

static void h_expect(h_state_t st, uint32_t need)
{
    h.st = st;
    h.need = need;
    h.have = 0;
}

static void h_rx(uint8_t b)
{
    if (h.dead || h.st == H_RESET || h.st == H_RUNNING) {
        return;
    }
    if (h.st == H_UNSYNCED) {
        if (b == 0x7F) {
            h_send(0x79);
            h_expect(H_CMD, 2);
        }
        return;
    }
    h.buf[h.have++] = b;
    if (h.have < h.need) {
        return;
    }

    switch (h.st) {
    case H_CMD:
        h.cmd = h.buf[0];
        if ((uint8_t)(h.buf[0] ^ h.buf[1]) != 0xFF) {
            h_send(0x1F);
            h_expect(H_CMD, 2);
            return;
        }
        switch (h.cmd) {
        case 0x02:
            h_send(0x79); h_send(0x01);
            h_send((uint8_t)(h.pid >> 8)); h_send((uint8_t)h.pid);
            h_send(0x79);
            h_expect(H_CMD, 2);
            return;
        case 0x11: case 0x31:
            if (h.rdp) { h_send(0x1F); h_expect(H_CMD, 2); return; }
            h_send(0x79); h_expect(H_ADDR, 5); return;
        case 0x44:
            h_send(0x79); h_expect(H_ERASE, 5); return;
        case 0x63:
            h_send(0x79); h_expect(H_WP_LEN, 1); return;
        case 0x73:
            h_send(0x79); h.wrp0 = false; h_send(0x79); h_rom_reset(); return;
        default:
            h_send(0x1F); h_expect(H_CMD, 2); return;
        }
    case H_ADDR:
        if (xor_bytes(h.buf, 4) != h.buf[4]) { h_send(0x1F); h_expect(H_CMD, 2); return; }
        h.addr = ((uint32_t)h.buf[0] << 24) | ((uint32_t)h.buf[1] << 16) |
                 ((uint32_t)h.buf[2] << 8) | h.buf[3];
        if (h.addr < 0x08000000u || h.addr >= 0x08000000u + FLASH_SIZE) {
            h_send(0x1F); h_expect(H_CMD, 2); return;
        }
        h.addr -= 0x08000000u;
        h_send(0x79);
        if (h.cmd == 0x11) h_expect(H_READ_LEN, 2); else h_expect(H_WRITE_LEN, 1);
        return;
    case H_READ_LEN: {
        uint32_t n = (uint32_t)h.buf[0] + 1, i;
        if ((uint8_t)(h.buf[0] ^ h.buf[1]) != 0xFF) { h_send(0x1F); h_expect(H_CMD, 2); return; }
        h_send(0x79);
        for (i = 0; i < n; i++) h_send(h.flash[h.addr + i]);
        h_expect(H_CMD, 2);
        return;
    }
    case H_WRITE_LEN:
        /* keep N-1 in buf[0]; then N data bytes and a checksum */
        h.st = H_WRITE_DATA;
        h.need = (uint32_t)h.buf[0] + 3;
        return;
    case H_WRITE_DATA: {
        uint32_t n = (uint32_t)h.buf[0] + 1, i;
        if (xor_bytes(h.buf, n + 1) != h.buf[n + 1] || (n & 3u) ||
            (h.wrp0 && h.addr < H743_B_SECTOR_SIZE)) {
            h_send(0x1F); h_expect(H_CMD, 2); return;
        }
        for (i = 0; i < n; i++) h.flash[h.addr + i] &= h.buf[1 + i];
        h.writes++;
        h_send(0x79);
        h_expect(H_CMD, 2);
        return;
    }
    case H_ERASE: {
        uint16_t sector = (uint16_t)((h.buf[2] << 8) | h.buf[3]);
        if (xor_bytes(h.buf, 4) != h.buf[4] || h.buf[0] || h.buf[1] ||
            sector != 0 || h.wrp0) {
            h_send(0x1F); h_expect(H_CMD, 2); return;
        }
        memset(h.flash, 0xFF, H743_B_SECTOR_SIZE);
        h.erases++;
        h_send(0x79);
        h_expect(H_CMD, 2);
        return;
    }
    case H_WP_LEN:
        h.st = H_WP_DATA;
        h.need = (uint32_t)h.buf[0] + 3;
        return;
    case H_WP_DATA: {
        uint32_t n = (uint32_t)h.buf[0] + 1, i;
        if (xor_bytes(h.buf, n + 1) != h.buf[n + 1]) { h_send(0x1F); h_expect(H_CMD, 2); return; }
        for (i = 0; i < n; i++) if (h.buf[1 + i] == 0) h.wrp0 = true;
        h_send(0x79);
        h_rom_reset();
        return;
    }
    default:
        return;
    }
}

/* ------------------------------------------------------------------ port */

void port_nrst_hold(bool hold)
{
    if (hold) {
        h.nrst_held = true;
        h.st = H_RESET;
        return;
    }
    if (h.nrst_held) {
        h.nrst_held = false;
        h_rom_reset();
        if (h.st == H_RUNNING) {
            h.releases++;
        }
    }
}

void port_boot0(bool high) { h.boot0 = high; }

void port_link_write(const uint8_t *data, uint32_t len)
{
    while (len--) {
        h_rx(*data++);
    }
}

bool port_link_read(uint8_t *byte, uint32_t timeout_ms)
{
    (void)timeout_ms;
    if (h.tx_head == h.tx_tail) {
        return false;
    }
    *byte = h.tx[h.tx_head++ % sizeof h.tx];
    return true;
}

void port_link_flush(void) { h.tx_head = h.tx_tail; }
void port_link_detach(void) {}
bool port_provision_strap(void) { return h.strap; }
void port_delay_ms(uint32_t ms) { (void)ms; }

static bool in_rom_session(void)
{
    return !h.nrst_held && h.st != H_RUNNING;
}

void port_log(const char *msg)
{
    if (in_rom_session()) {
        h.logged_in_session = true;
    }
    printf("      [A] %s\n", msg);
}

void port_log_hex(const char *label, uint32_t value)
{
    if (in_rom_session()) {
        h.logged_in_session = true;
    }
    printf("      [A] %s 0x%08x\n", label, value);
}

/* ------------------------------------------------------------------ tests */

static const uint8_t *b_image(void) { return B_BUNDLE + B_BUNDLE_HEADER_LEN; }
static uint32_t b_len(void) { return B_BUNDLE_LEN - B_BUNDLE_HEADER_LEN; }

static void power_on(bool keep_flash)
{
    uint8_t flash[FLASH_SIZE];
    bool wrp = h.wrp0;

    if (keep_flash) memcpy(flash, h.flash, sizeof flash);
    memset(&h, 0, sizeof h);
    if (keep_flash) { memcpy(h.flash, flash, sizeof flash); h.wrp0 = wrp; }
    else memset(h.flash, 0xFF, sizeof h.flash);
    h.pid = 0x0450;
    h.nrst_held = true;
    h.st = H_RESET;
}

static rot_result_t boot(const uint8_t *bundle, uint32_t bundle_len)
{
    rot_config_t cfg = { HOLODI_PUBKEY, bundle, bundle_len, true, true };
    return rot_run(&cfg);
}

static bool flash_holds_b(void)
{
    uint32_t i;
    if (memcmp(h.flash, b_image(), b_len()) != 0) return false;
    for (i = b_len(); i < H743_B_SECTOR_SIZE; i++) if (h.flash[i] != 0xFF) return false;
    return true;
}

static int failures;

static void check(const char *name, bool ok)
{
    printf("   %s: %s\n", ok ? "PASS" : "FAIL", name);
    if (!ok) failures++;
}

static void expect(const char *title, rot_result_t got, rot_result_t want)
{
    printf("== %s\n", title);
    printf("   result=%s\n", rot_result_name(got));
    check("result", got == want);
    check(rot_released(want) ? "H743 released exactly once" : "H743 never released",
          h.releases == (rot_released(want) ? 1u : 0u));
    check(rot_released(want) ? "H743 left running from flash (BOOT0 low)"
                             : "H743 left in reset",
          rot_released(want) ? (h.st == H_RUNNING && !h.boot0) : (h.st == H_RESET));
    check("no log output during ROM session", !h.logged_in_session);
}

static uint8_t *load(const char *path, uint32_t *len)
{
    FILE *f = fopen(path, "rb");
    uint8_t *p;
    long n;
    if (!f) { perror(path); exit(2); }
    fseek(f, 0, SEEK_END); n = ftell(f); fseek(f, 0, SEEK_SET);
    p = malloc((size_t)n);
    if (fread(p, 1, (size_t)n, f) != (size_t)n) exit(2);
    fclose(f);
    *len = (uint32_t)n;
    return p;
}

int main(int argc, char **argv)
{
    uint8_t *bad_bundle;
    uint8_t *stock;
    uint32_t stock_len;
    rot_result_t r;

    if (argc != 2) {
        fprintf(stderr, "usage: %s <unsigned stock bootloader.bin>\n", argv[0]);
        return 2;
    }
    stock = load(argv[1], &stock_len);

    power_on(false);
    r = boot(B_BUNDLE, B_BUNDLE_LEN);
    expect("M3 first power-on, blank H743: provision B", r, ROT_RELEASED_PROVISIONED);
    check("sector 0 holds exactly B", flash_holds_b());
    check("sector 0 write-protected", h.wrp0);

    power_on(true);
    r = boot(B_BUNDLE, B_BUNDLE_LEN);
    expect("normal power-on: authenticate installed B, no rewrite", r, ROT_RELEASED_VERIFIED);
    check("no erase or write on normal boot", h.erases == 0 && h.writes == 0);

    power_on(true);
    h.flash[128] ^= 0x01;
    h.wrp0 = false;
    r = boot(B_BUNDLE, B_BUNDLE_LEN);
    expect("M5 B_malicious (1 bit) in H743 flash", r, ROT_HOLD_UNTRUSTED_B);
    check("flash left untouched", h.erases == 0 && h.writes == 0);

    power_on(false);
    memcpy(h.flash, stock, stock_len);
    r = boot(B_BUNDLE, B_BUNDLE_LEN);
    expect("M5 unsigned stock ArduPilot bootloader in flash", r, ROT_HOLD_UNTRUSTED_B);

    power_on(false);
    memcpy(h.flash, b_image(), b_len());
    h.flash[H743_B_SECTOR_SIZE - 4] = 0x00;
    r = boot(B_BUNDLE, B_BUNDLE_LEN);
    expect("authentic B plus junk in rest of sector 0", r, ROT_HOLD_UNTRUSTED_B);

    power_on(false);
    memcpy(h.flash, stock, stock_len);
    h.wrp0 = true;
    h.strap = true;
    r = boot(B_BUNDLE, B_BUNDLE_LEN);
    expect("recovery strap: reinstall over write-protected bad B", r, ROT_RELEASED_PROVISIONED);
    check("sector 0 holds exactly B", flash_holds_b());

    bad_bundle = malloc(B_BUNDLE_LEN);
    memcpy(bad_bundle, B_BUNDLE, B_BUNDLE_LEN);
    bad_bundle[B_BUNDLE_HEADER_LEN + 128] ^= 0x01;
    power_on(false);
    r = boot(bad_bundle, B_BUNDLE_LEN);
    expect("Baochip's own copy of B tampered", r, ROT_HOLD_BAD_BUNDLE);
    check("H743 never left reset (no ROM session)", h.erases == 0 && h.st == H_RESET);

    power_on(false);
    h.rdp = true;
    r = boot(B_BUNDLE, B_BUNDLE_LEN);
    expect("H743 read-out protection set", r, ROT_HOLD_READ_PROTECTED);

    power_on(false);
    h.pid = 0x0451;
    r = boot(B_BUNDLE, B_BUNDLE_LEN);
    expect("wrong target on link", r, ROT_HOLD_WRONG_TARGET);

    power_on(false);
    h.dead = true;
    r = boot(B_BUNDLE, B_BUNDLE_LEN);
    expect("M1 link dead / BOOT0 not wired", r, ROT_HOLD_LINK);

    printf("\nfirmware A host test: %s\n", failures ? "FAIL" : "PASS");
    return failures ? 1 : 0;
}
