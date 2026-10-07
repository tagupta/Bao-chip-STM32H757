# Cube Orange+ (your ADS-B carrier) + Baochip wiring

Your photo is **Cube Orange+** on the **ADS-B IN carrier** (antenna on the left, `CubePilot` / `ARDUPILOT` silkscreen). That is **not** the small “Mini Carrier” board used in the generic cartoon diagram.

## What connects where (mental model)

```text
┌─────────────────────────────────────┐     JST 6-wire      ┌──────────────────┐
│  Cube Orange+ on ADS-B carrier      │ ◄──────────────────►│ Breadboard       │
│  Port: TELEM 1 (left side, white)  │   3 signals + GND   │ + green Baochip  │
└─────────────────────────────────────┘                     └────────┬─────────┘
         │ USB on orange cube face                                      │ USB-C
         └──────────────────────────── Mac ──────────────────────────────┘
```

- **Breadboard** = only the **green Baochip** (like a Pico). The **Cube never goes on the breadboard**.
- **Cube UART** = **TELEM 1** JST connector on the **left** edge of the carrier (under the cube overhang), **not** the big **MAIN OUT / AUX OUT** pin rows on the **right**.

## TELEM 1 → Baochip (plug a cable, not header pins)

Use a **6-pin JST-GH TELEM1** cable (often shipped with the board, or cut one end and tin the wires for the breadboard).

| TELEM1 pin | Wire (typical) | Connect to Baochip |
|------------|----------------|--------------------|
| 1 | Red **+5 V** | **Nothing** (leave insulated) |
| 2 | **TX** (FC sends) | **PB13** (Baochip RX) |
| 3 | **RX** (FC receives) | **PB14** (Baochip TX) |
| 4 | CTS | Not used |
| 5 | RTS | Not used |
| 6 | **GND** | **GND** (and breadboard − rail) |

Add **10 kΩ** from **PB13** to **3.3 V**.

**Provisioning:** jumper **PB5** to **GND** on the Baochip (breadboard), not on the Cube.

## Mac USB (two ports)

| USB | Where on *your* hardware | Use |
|-----|---------------------------|-----|
| **Baochip USB-C** | Green board | Flash firmware **A**, logs |
| **Cube USB** | **Micro-USB on the orange cube face** (recessed in carrier) | Upload **C** after secure **B** runs |

The carrier silkscreen **“USB”** next to SPKT is **not** the main Mac cable for ArduPilot upload — use the **cube’s micro-USB**.

## Power

- **POWER 1** (or POWER 2): your power module → carrier (normal flight power).
- Baochip: **USB-C** from Mac (or valid board power).
- **Common GND**: TELEM1 pin 6 must share ground with Baochip GND.

## Not on the right-hand pin headers

The labeled blocks **SBUSo / RCIN / MAIN OUT / AUX OUT** are **PWM/servo outputs**. Do **not** use them for the Baochip ROM UART link. Use **TELEM 1** only.

## Still not on any external port (solder on Cube module)

| Baochip | FMU |
|---------|-----|
| **PB2** | **NRST** (reset) |
| **PB3** | **BOOT0** (R7 pad inside cube case) |

See [DEPLOY.md](DEPLOY.md) for NRST circuit options (`ROT_NRST_INVERTED`).

## Annotated photo

See **[cube-orange-plus-adsb-actual-wiring.jpg](cube-orange-plus-adsb-actual-wiring.jpg)** — labels drawn on **your** carrier layout.

Legacy breadboard-only art (Baochip detail): [cube-orange-baochip-breadboard.jpg](cube-orange-baochip-breadboard.jpg).
