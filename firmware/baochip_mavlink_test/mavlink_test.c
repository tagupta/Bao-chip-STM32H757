/*
 * Bench relay test: Dabao (Baochip-1x) commands a Cube Orange+ flight mode
 * over MAVLink on the Cube's TELEM1 port.
 *
 * Dabao header         Cube Orange+ TELEM1 (SERIAL1, 57600 8N1, MAVLink2)
 *   PB14 UART2_TX  ->  pin 3 RX
 *   PB13 UART2_RX  <-  pin 2 TX   (10 kOhm pull-up to 3.3 V)
 *   GND            --  pin 6 GND
 *   PB1  GPIO      ->  LED (optional)
 *
 * Behaviour:
 *   - Sends its own HEARTBEAT at 1 Hz as an onboard computer.
 *   - Waits for the autopilot's HEARTBEAT, then sends MAV_CMD_DO_SET_MODE
 *     for MAVLINK_TEST_MODE every 5 s until the Cube ACKs it as accepted.
 *
 * LED: short flash = HEARTBEAT received from the Cube, solid = mode change
 * accepted, three fast flashes = mode change rejected (LOITER needs a GPS
 * position, so expect that on the bench).
 *
 * UART2 carries MAVLink only, so nothing here may call mini_printf().
 */
#include <string.h>

#include "bao.h"

#ifndef MAVLINK_TEST_MODE
#define MAVLINK_TEST_MODE 5u /* ArduCopter: 0 STABILIZE, 2 ALT_HOLD, 5 LOITER */
#endif

#define LINK_UART 2u
#define LINK_BAUD 57600u
#define LED_PORT  GPIO_PORT_B
#define LED_PIN   1u

#define OUR_SYSID 42u
#define OUR_COMPID 191u /* MAV_COMP_ID_ONBOARD_COMPUTER */

#define MSG_HEARTBEAT     0u
#define MSG_COMMAND_LONG  76u
#define MSG_COMMAND_ACK   77u
#define CMD_DO_SET_MODE   176u
#define MODE_FLAG_CUSTOM  1u
#define MAV_RESULT_ACCEPTED 0u
#define MAV_AUTOPILOT_INVALID 8u
#define MAV_TYPE_ONBOARD_CONTROLLER 18u

#define HEARTBEAT_PERIOD_MS 1000u
#define COMMAND_PERIOD_MS   5000u
#define LED_FLASH_MS        60u

typedef enum { P_IDLE, P_LEN, P_HEADER, P_PAYLOAD, P_CRC1, P_CRC2, P_SIG } parse_state_t;

typedef struct {
    parse_state_t state;
    bool v2;
    uint8_t len;
    uint8_t hdr[9];
    uint8_t hdr_len;
    uint8_t hdr_got;
    uint8_t payload[255];
    uint8_t got;
    uint16_t crc;
    uint8_t sig_left;
    uint8_t sysid;
    uint8_t compid;
    uint32_t msgid;
} parser_t;

static uint8_t tx_seq;

static void crc_accumulate(uint16_t *crc, uint8_t byte)
{
    uint8_t tmp = byte ^ (uint8_t)(*crc & 0xFFu);

    tmp ^= (uint8_t)(tmp << 4);
    *crc = (uint16_t)((*crc >> 8) ^ ((uint16_t)tmp << 8) ^ ((uint16_t)tmp << 3) ^ (tmp >> 4));
}

static bool crc_extra(uint32_t msgid, uint8_t *extra, uint8_t *full_len)
{
    switch (msgid) {
    case MSG_HEARTBEAT:   *extra = 50u;  *full_len = 9u;  return true;
    case MSG_COMMAND_ACK: *extra = 143u; *full_len = 10u; return true;
    default: return false;
    }
}

static void put_u16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void put_u32(uint8_t *p, uint32_t v) { put_u16(p, (uint16_t)v); put_u16(p + 2, (uint16_t)(v >> 16)); }

static void put_float(uint8_t *p, float f)
{
    uint32_t v;

    memcpy(&v, &f, sizeof v);
    put_u32(p, v);
}

/* MAVLink 1 framing; ArduPilot accepts it on a MAVLink2 port. */
static void send_message(uint8_t msgid, uint8_t extra, const uint8_t *payload, uint8_t len)
{
    uint8_t frame[6 + 255 + 2];
    uint16_t crc = 0xFFFFu;
    uint32_t i;

    frame[0] = 0xFEu;
    frame[1] = len;
    frame[2] = tx_seq++;
    frame[3] = OUR_SYSID;
    frame[4] = OUR_COMPID;
    frame[5] = msgid;
    memcpy(&frame[6], payload, len);
    for (i = 1; i < 6u + len; i++) {
        crc_accumulate(&crc, frame[i]);
    }
    crc_accumulate(&crc, extra);
    put_u16(&frame[6 + len], crc);
    uart_write(LINK_UART, frame, 8u + len);
}

static void send_heartbeat(void)
{
    uint8_t p[9];

    put_u32(&p[0], 0);
    p[4] = MAV_TYPE_ONBOARD_CONTROLLER;
    p[5] = MAV_AUTOPILOT_INVALID;
    p[6] = 0;
    p[7] = 4u; /* MAV_STATE_ACTIVE */
    p[8] = 3u;
    send_message(MSG_HEARTBEAT, 50u, p, sizeof p);
}

static void send_set_mode(uint8_t target_sys, uint8_t target_comp, uint32_t mode)
{
    uint8_t p[33];

    memset(p, 0, sizeof p);
    put_float(&p[0], (float)MODE_FLAG_CUSTOM);
    put_float(&p[4], (float)mode);
    put_u16(&p[28], CMD_DO_SET_MODE);
    p[30] = target_sys;
    p[31] = target_comp;
    p[32] = 0;
    send_message(MSG_COMMAND_LONG, 152u, p, sizeof p);
}

/* Returns true when a complete frame with a valid CRC for a known message is ready. */
static bool parse_byte(parser_t *ps, uint8_t c)
{
    uint8_t extra, full_len;

    switch (ps->state) {
    case P_IDLE:
        if (c == 0xFDu || c == 0xFEu) {
            ps->v2 = (c == 0xFDu);
            ps->crc = 0xFFFFu;
            ps->state = P_LEN;
        }
        return false;
    case P_LEN:
        ps->len = c;
        crc_accumulate(&ps->crc, c);
        ps->hdr_len = ps->v2 ? 8u : 4u;
        ps->hdr_got = 0;
        ps->state = P_HEADER;
        return false;
    case P_HEADER:
        ps->hdr[ps->hdr_got++] = c;
        crc_accumulate(&ps->crc, c);
        if (ps->hdr_got == ps->hdr_len) {
            if (ps->v2) {
                ps->sig_left = (ps->hdr[0] & 0x01u) ? 13u : 0u;
                ps->sysid = ps->hdr[3];
                ps->compid = ps->hdr[4];
                ps->msgid = ps->hdr[5] | ((uint32_t)ps->hdr[6] << 8) | ((uint32_t)ps->hdr[7] << 16);
            } else {
                ps->sig_left = 0;
                ps->sysid = ps->hdr[1];
                ps->compid = ps->hdr[2];
                ps->msgid = ps->hdr[3];
            }
            ps->got = 0;
            ps->state = ps->len ? P_PAYLOAD : P_CRC1;
        }
        return false;
    case P_PAYLOAD:
        ps->payload[ps->got++] = c;
        crc_accumulate(&ps->crc, c);
        if (ps->got == ps->len) {
            ps->state = P_CRC1;
        }
        return false;
    case P_CRC1:
        ps->hdr[0] = c;
        ps->state = P_CRC2;
        return false;
    case P_CRC2: {
        uint16_t rx_crc = (uint16_t)(ps->hdr[0] | ((uint16_t)c << 8));
        bool ok = false;

        if (crc_extra(ps->msgid, &extra, &full_len)) {
            crc_accumulate(&ps->crc, extra);
            ok = (ps->crc == rx_crc);
            if (ok && ps->len < full_len) {
                /* MAVLink 2 strips trailing zero bytes. */
                memset(&ps->payload[ps->len], 0, (uint32_t)(full_len - ps->len));
            }
        }
        ps->state = ps->sig_left ? P_SIG : P_IDLE;
        return ok;
    }
    case P_SIG:
        if (--ps->sig_left == 0) {
            ps->state = P_IDLE;
        }
        return false;
    }
    ps->state = P_IDLE;
    return false;
}

static void led(bool on)
{
    gpio_put(LED_PORT, LED_PIN, on);
}

int main(void)
{
    parser_t ps;
    uint8_t target_sys = 0, target_comp = 0;
    bool accepted = false;
    uint32_t reject_flashes = 0;
    uint64_t now, last_hb = 0, last_cmd = 0, led_off_at = 0;

    gpio_init(LED_PORT, LED_PIN);
    gpio_put(LED_PORT, LED_PIN, false);
    gpio_set_dir(LED_PORT, LED_PIN, true);

    bao_init();
    uart_init(LINK_UART, LINK_BAUD);
    memset(&ps, 0, sizeof ps);

    while (1) {
        now = millis();

        while (uart_is_readable(LINK_UART)) {
            if (!parse_byte(&ps, (uint8_t)uart_getc(LINK_UART))) {
                continue;
            }
            if (ps.msgid == MSG_HEARTBEAT && ps.payload[5] != MAV_AUTOPILOT_INVALID) {
                target_sys = ps.sysid;
                target_comp = ps.compid;
                if (!accepted && reject_flashes == 0) {
                    led(true);
                    led_off_at = now + LED_FLASH_MS;
                }
            } else if (ps.msgid == MSG_COMMAND_ACK && ps.payload[0] == (CMD_DO_SET_MODE & 0xFFu) &&
                       ps.payload[1] == (CMD_DO_SET_MODE >> 8)) {
                if (ps.payload[2] == MAV_RESULT_ACCEPTED) {
                    accepted = true;
                    led(true);
                } else {
                    reject_flashes = 6;
                    led_off_at = now;
                }
            }
        }

        if (now - last_hb >= HEARTBEAT_PERIOD_MS) {
            last_hb = now;
            send_heartbeat();
        }

        if (!accepted && target_sys != 0 && now - last_cmd >= COMMAND_PERIOD_MS) {
            last_cmd = now;
            send_set_mode(target_sys, target_comp, MAVLINK_TEST_MODE);
        }

        if (!accepted && led_off_at != 0 && now >= led_off_at) {
            if (reject_flashes != 0) {
                reject_flashes--;
                led(reject_flashes & 1u);
                led_off_at = now + 120u;
            } else {
                led(false);
                led_off_at = 0;
            }
        }
    }
}
