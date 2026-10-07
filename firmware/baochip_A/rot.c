/*
 * Firmware A - Baochip root-of-trust policy (architecture sections 5, 6,
 * 10, 13 and milestones M1-M5).
 *
 *   power on -> hold H743 in reset
 *            -> verify Baochip's own signed copy of B   (bad: keep reset)
 *            -> enter H743 ROM bootloader, read sector 0
 *                 authentic B           -> release reset (normal boot)
 *                 blank, or strap set   -> erase, write, read back, verify
 *                 anything else         -> keep reset (B_malicious, M5)
 *
 * The only path that releases NRST with BOOT0 low is release_h743(), and it
 * is only reached after B in H743 flash has been authenticated.
 */
#include <stddef.h>

#include "monocypher.h"
#include "rot.h"
#include "rot_port.h"
#include "stm32_rom.h"

typedef enum {
    B_VALID,
    B_BLANK,
    B_INVALID,
    B_READ_ERROR,
} b_state_t;

static uint8_t xfer[STM32_ROM_MAX_XFER];

const char *rot_result_name(rot_result_t r)
{
    switch (r) {
    case ROT_RELEASED_VERIFIED:    return "RELEASED_VERIFIED";
    case ROT_RELEASED_PROVISIONED: return "RELEASED_PROVISIONED";
    case ROT_HOLD_BAD_BUNDLE:      return "HOLD_BAD_BUNDLE";
    case ROT_HOLD_LINK:            return "HOLD_LINK";
    case ROT_HOLD_WRONG_TARGET:    return "HOLD_WRONG_TARGET";
    case ROT_HOLD_READ_PROTECTED:  return "HOLD_READ_PROTECTED";
    case ROT_HOLD_UNTRUSTED_B:     return "HOLD_UNTRUSTED_B";
    case ROT_HOLD_PROGRAM_FAILED:  return "HOLD_PROGRAM_FAILED";
    }
    return "?";
}

static uint32_t le32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static bool parse_bundle(const rot_config_t *cfg, const uint8_t **sig,
                         const uint8_t **image, uint32_t *image_len)
{
    static const char magic[] = B_BUNDLE_MAGIC;
    uint32_t i, len;

    if (cfg->bundle_len < B_BUNDLE_HEADER_LEN) {
        return false;
    }
    for (i = 0; i < 8; i++) {
        if (cfg->bundle[i] != (uint8_t)magic[i]) {
            return false;
        }
    }
    len = le32(cfg->bundle + 8);
    if (len == 0 || len > H743_B_SECTOR_SIZE ||
        cfg->bundle_len != B_BUNDLE_HEADER_LEN + len) {
        return false;
    }
    *sig = cfg->bundle + 12;
    *image = cfg->bundle + B_BUNDLE_HEADER_LEN;
    *image_len = len;
    return true;
}

/* Keep NRST asserted and BOOT0 low: the state every failure ends in. */
static void hold_h743(void)
{
    port_nrst_hold(true);
    port_boot0(false);
}

static void release_h743(void)
{
    port_nrst_hold(true);
    port_boot0(false);
    port_delay_ms(10);
    port_link_detach();
    port_nrst_hold(false);
}

/* No port_log() between open_session() and hold_h743(): the UART is shared. */
static bool open_session(rot_result_t *why)
{
    uint16_t pid = 0;

    stm32_rom_enter();
    if (stm32_rom_sync() != ROM_OK || stm32_rom_get_id(&pid) != ROM_OK) {
        *why = ROT_HOLD_LINK;
        return false;
    }
    if (pid != STM32H74X_PID) {
        *why = ROT_HOLD_WRONG_TARGET;
        return false;
    }
    return true;
}

/*
 * Authenticate whatever is in H743 sector 0 against B's signature, streaming
 * it through Monocypher so no 128 KiB buffer is needed.
 */
static b_state_t classify_flash(const rot_config_t *cfg, const uint8_t *sig,
                                uint32_t image_len, rom_status_t *err)
{
    crypto_check_ctx ctx;
    crypto_check_ctx_abstract *actx = (crypto_check_ctx_abstract *)&ctx;
    uint32_t end, off, i, chunk, signed_part;
    bool blank = true, tail_erased = true;
    rom_status_t st;

    end = cfg->check_full_sector
        ? H743_B_SECTOR_SIZE
        : (image_len + STM32_ROM_MAX_XFER - 1) & ~(STM32_ROM_MAX_XFER - 1);

    crypto_check_init(actx, sig, cfg->pubkey);
    for (off = 0; off < end; off += chunk) {
        chunk = end - off;
        if (chunk > STM32_ROM_MAX_XFER) {
            chunk = STM32_ROM_MAX_XFER;
        }
        st = stm32_rom_read(H743_FLASH_BASE + off, xfer, chunk);
        if (st != ROM_OK) {
            *err = st;
            return B_READ_ERROR;
        }
        signed_part = 0;
        if (off < image_len) {
            signed_part = image_len - off;
            if (signed_part > chunk) {
                signed_part = chunk;
            }
            crypto_check_update(actx, xfer, signed_part);
        }
        for (i = 0; i < chunk; i++) {
            if (xfer[i] != 0xFF) {
                blank = false;
                if (i >= signed_part) {
                    tail_erased = false;
                }
            }
        }
    }
    if (blank) {
        return B_BLANK;
    }
    if (crypto_check_final(actx) != 0 || !tail_erased) {
        return B_INVALID;
    }
    return B_VALID;
}

static bool program_b(const uint8_t *image, uint32_t image_len)
{
    uint32_t off, i, chunk, padded;
    rot_result_t why;
    rom_status_t st;

    st = stm32_rom_erase_sector(H743_B_SECTOR);
    if (st == ROM_NACK) {
        /* Most likely WRP from a previous provisioning: lift it and retry. */
        if (stm32_rom_write_unprotect() != ROM_OK || !open_session(&why)) {
            return false;
        }
        st = stm32_rom_erase_sector(H743_B_SECTOR);
    }
    if (st != ROM_OK) {
        return false;
    }

    /* H7 programs 256-bit flash words: pad the last chunk with 0xFF. */
    for (off = 0; off < image_len; off += chunk) {
        chunk = image_len - off;
        if (chunk > STM32_ROM_MAX_XFER) {
            chunk = STM32_ROM_MAX_XFER;
        }
        padded = (chunk + 31u) & ~31u;
        for (i = 0; i < padded; i++) {
            xfer[i] = (i < chunk) ? image[off + i] : 0xFF;
        }
        if (stm32_rom_write(H743_FLASH_BASE + off, xfer, padded) != ROM_OK) {
            return false;
        }
    }
    return true;
}

rot_result_t rot_run(const rot_config_t *cfg)
{
    const uint8_t *sig = NULL, *image = NULL;
    uint32_t image_len = 0;
    rom_status_t err = ROM_OK;
    rot_result_t r;
    b_state_t state;
    bool strap;

    hold_h743();
    port_log("H743 held in reset");

    if (!parse_bundle(cfg, &sig, &image, &image_len) ||
        crypto_check(sig, cfg->pubkey, image, image_len) != 0) {
        port_log("REFUSED: Baochip's copy of B is not authentic");
        return ROT_HOLD_BAD_BUNDLE;
    }
    port_log_hex("B bundle authentic, bytes", image_len);

    strap = port_provision_strap();
    if (strap) {
        port_log("provision strap set: B will be reinstalled");
    }

    if (!open_session(&r)) {
        hold_h743();
        port_log(r == ROT_HOLD_WRONG_TARGET
                 ? "REFUSED: target on the link is not an STM32H74x"
                 : "REFUSED: no H743 ROM bootloader on the link");
        return r;
    }

    state = classify_flash(cfg, sig, image_len, &err);
    if (state == B_READ_ERROR) {
        hold_h743();
        if (err == ROM_NACK) {
            port_log("REFUSED: H743 flash not readable (RDP set?)");
            return ROT_HOLD_READ_PROTECTED;
        }
        port_log("REFUSED: link error while reading B");
        return ROT_HOLD_LINK;
    }

    if (state == B_VALID && !strap) {
        hold_h743();
        port_log("installed B authentic: releasing H743");
        release_h743();
        return ROT_RELEASED_VERIFIED;
    }

    if (state == B_INVALID && !strap) {
        hold_h743();
        port_log("REFUSED: H743 sector 0 holds an unauthenticated B");
        return ROT_HOLD_UNTRUSTED_B;
    }

    if (!program_b(image, image_len) ||
        classify_flash(cfg, sig, image_len, &err) != B_VALID) {
        hold_h743();
        port_log("REFUSED: provisioning B failed read-back verification");
        return ROT_HOLD_PROGRAM_FAILED;
    }

    if (cfg->write_protect_b) {
        static const uint8_t sectors[1] = { H743_B_SECTOR };
        if (stm32_rom_write_protect(sectors, 1) != ROM_OK) {
            hold_h743();
            port_log("warning: could not write-protect sector 0");
        }
    }

    hold_h743();
    port_log("B provisioned and verified: releasing H743");
    release_h743();
    return ROT_RELEASED_PROVISIONED;
}
