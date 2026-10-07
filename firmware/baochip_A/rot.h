/*
 * Firmware A - Baochip root-of-trust policy for the H743.
 *
 * Baochip decides whether the H743 bootloader (B) is trusted; the trusted
 * B decides whether the flight firmware (C) is trusted.
 */
#ifndef ROT_H
#define ROT_H

#include <stdbool.h>
#include <stdint.h>

/* H743 flash bank 1, sector 0: where B lives and what the CPU boots from. */
#define H743_FLASH_BASE     0x08000000u
#define H743_B_SECTOR       0u
#define H743_B_SECTOR_SIZE  (128u * 1024u)

/* Bundle framing produced by holodi/boot_bundle.py. */
#define B_BUNDLE_MAGIC      "BAOBOOT1"
#define B_BUNDLE_HEADER_LEN (8u + 4u + 64u)

typedef enum {
    /* H743 released from reset. */
    ROT_RELEASED_VERIFIED = 0,  /* B already in flash and authentic */
    ROT_RELEASED_PROVISIONED,   /* B installed, read back and authenticated */
    /* H743 kept in reset. */
    ROT_HOLD_BAD_BUNDLE,        /* Baochip's own copy of B is not authentic */
    ROT_HOLD_LINK,              /* no answer from the H743 ROM bootloader */
    ROT_HOLD_WRONG_TARGET,      /* the part on the link is not an STM32H74x */
    ROT_HOLD_READ_PROTECTED,    /* RDP prevents Baochip from reading B */
    ROT_HOLD_UNTRUSTED_B,       /* flash holds a B that fails authentication */
    ROT_HOLD_PROGRAM_FAILED,    /* erase/write/read-back of B failed */
} rot_result_t;

typedef struct {
    const uint8_t *pubkey;      /* 32-byte HoloDi public key */
    const uint8_t *bundle;      /* signed B bundle */
    uint32_t bundle_len;
    bool write_protect_b;       /* set WRP on sector 0 after provisioning */
    bool check_full_sector;     /* also require the rest of sector 0 erased */
} rot_config_t;

rot_result_t rot_run(const rot_config_t *cfg);

static inline bool rot_released(rot_result_t r)
{
    return r == ROT_RELEASED_VERIFIED || r == ROT_RELEASED_PROVISIONED;
}

const char *rot_result_name(rot_result_t r);

#endif
