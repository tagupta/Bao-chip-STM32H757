# Deploying the Baochip → H743 secure boot chain

This implements *Baochip–STM32H743 Secure Boot Architecture.pdf*:

```text
Baochip (Dabao)                H743 (Kakute H7)
  firmware A  --authenticates-->  bootloader B  --authenticates-->  flight firmware C
  firmware/baochip_A            sector 0, 0x08000000            0x08020000 (ArduCopter)
```

| Image | What it is | Signed with | Verified by |
|---|---|---|---|
| A | `firmware/baochip_A` (C, Dabao SDK). Holds NRST, drives BOOT0, talks to the H743 ROM bootloader over UART, authenticates or installs B. Contains the HoloDi public key and the signed B bundle. | Baochip boot key (Ed25519ph, via `dabao-sdk/tools/sign_and_uf2.py`) | Baochip boot1 |
| B | ArduPilot secure bootloader for KakuteH7 with only the HoloDi public key compiled in. | HoloDi private key (`holodi/keys/holodi_private_key.dat`) | Firmware A |
| C | ArduCopter built with `--signed-fw`. | HoloDi private key | B |

### Which key is "the Baochip key"

HoloDi and Baochip are the same organisation, but they use two different
signature schemes, so two keys are involved:

- The **HoloDi key** (Monocypher EdDSA, BLAKE2b) signs **B and C**. The ArduPilot
  bootloader can only verify this scheme, so it is the only one usable for C.
  Firmware A carries the matching public key and uses it to verify B.
- The **Baochip boot key** (Ed25519ph, SHA-512) signs **A**. boot1 checks it
  against the key manifest in boot1's own header. On a Dabao bench board this
  is the public developer key built into `sign_and_uf2.py`. That puts the board
  permanently in developer mode, and anyone with USB access who can press PROG
  can replace A. Production uses a Baochip-signed third-party boot1 that
  carries HoloDi keys, followed by `lockdown`. See
  [BAOCHIP_README_ALIGNMENT.md](BAOCHIP_README_ALIGNMENT.md), Decision 2.
  **Never run `lockdown` on a bench Dabao.** It permanently stops a
  developer-signed A from booting.

## Tools you need

You don't need an IDE. Everything is shell scripts. Use any editor
(Cursor, VS Code).

| Purpose | Tool | Needed? |
|---|---|---|
| Build A | RISC-V GCC in `dabao-sdk/xpack-riscv-none-elf-gcc-15.2.0-1` (already present) | yes |
| Flash A | USB cable to the Dabao, `dabao-sdk/tools/serial_flash.py` | yes |
| Build B and C | Docker (`sim/build_secure.sh` pins ArduPilot's ARM GCC 10) | only to rebuild |
| Upload C | B's USB port with `ardupilot/Tools/scripts/uploader.py`, or Mission Planner / QGroundControl | yes |
| Inspect or recover the H743 | ST-LINK V2/V3 on the Kakute's SWD pads plus **STM32CubeProgrammer** (`STM32_Programmer_CLI`) | strongly recommended for the bench |
| Watch A's log | Any USB-serial adapter + `picocom`/`screen` at 115200 **8E1** (boot1 prints first on the same pin at 1,000,000 8N1) | recommended |

You don't need STM32CubeIDE, because B is ArduPilot's bootloader built with
ArduPilot's `waf`, not a CubeIDE project. You'd only want it if you later
write your own B from scratch. "ArduPilot" here is the flight-stack source
tree, not an IDE.

## Wiring (Dabao to Kakute H7)

| Dabao pin | Signal | Kakute H7 | Notes |
|---|---|---|---|
| 15 PB14 | UART2 TX | RX1 pad (PA10, USART1_RX) | ROM bootloader link, 115200 8E1 |
| 16 PB13 | UART2 RX | TX1 pad (PA9, USART1_TX) | add 10 kΩ pull-up to 3.3 V (boot1 console input, see below) |
| 31 PB2 | NRST control | NRST | see below |
| 32 PB3 | BOOT0 | BOOT0 (boot-button pad) | board pull-down keeps it low when Baochip is off |
| PB5 | provision strap | jumper to GND | install B (first time, B update, recovery) |
| 29 PB1 | status LED | — | solid = released; N blinks = refusal code |
| GND | GND | GND | common ground, 3.3 V logic on both sides |

The H743 must be in reset from the moment power is applied, before firmware A
even runs. Pick one of these:

- **Recommended:** NRST is pulled to GND by an N-MOSFET (for example 2N7002), with
  the gate pulled up to 3.3 V with 10 kΩ and driven by PB2. Build A with
  `ROT_NRST_INVERTED=1`. Reset is held whenever the Baochip is off or still
  booting.
- **Direct:** a 10 kΩ pull-down on NRST, and PB2 connected to NRST through a
  1 kΩ series resistor. Build A with `ROT_NRST_INVERTED=0` (the default).

This is mandatory, not optional. Baochip's boot1 runs before A and doesn't
touch PB2. boot1 also uses PB13/PB14 as its own serial console at
1,000,000 8N1. If boot1 stays at its prompt (PROG held, bootwait enabled, or A
rejected) and USB isn't connected, it reads commands from PB13. The pull-up
stops a floating H743 TX line from feeding that console.

Check the NRST and BOOT0 pad locations against the Kakute H7 schematic before
soldering. The USB-serial adapter's RX can sit on PB14 in parallel to read
A's log. A only prints while the H743 is held in reset.

Status LED blink codes match `rot_result_t` in `firmware/baochip_A/rot.h`:
2 = Baochip's own B bundle is bad, 3 = no link to the ROM bootloader,
4 = wrong chip, 5 = RDP blocks reading, 6 = untrusted B in flash,
7 = provisioning failed.

## Commands

Run everything from the repo root.

### 0. One-time host setup

```bash
.venv/bin/python -m pip install pure25519 pyserial pymavlink pyfatfs==1.1.0 intelhex "setuptools<81"
```

The HoloDi signer is now pure Python (`holodi/holodi_eddsa.py`), so
`pymonocypher` is no longer needed.

### 1. Build and sign B and C (HoloDi key)

```bash
firmware/h743_B/build.sh              # Docker build, then sign
firmware/h743_B/build.sh --no-build   # sign the existing holodi/build outputs
```

This produces `holodi/build/B.bin`, `holodi/build/B.bundle` (B plus its HoloDi
signature) and `holodi/build/C.apj` (signed ArduCopter). It refuses to sign a B
that doesn't contain the HoloDi key or that still trusts ArduPilot's keys.

To sign by hand:

```bash
.venv/bin/python holodi/boot_bundle.py sign holodi/build/B.bin holodi/build/B.bundle
.venv/bin/python holodi/boot_bundle.py verify holodi/build/B.bundle          # OK
.venv/bin/python holodi/sign_apj.py sign holodi/build/arducopter.apj holodi/build/C.apj
.venv/bin/python holodi/sign_apj.py verify holodi/build/C.apj                # OK
```

### 2. Build and sign A (Baochip key)

```bash
ROT_NRST_INVERTED=1 firmware/baochip_A/build.sh    # 0 if wired directly
```

This produces `firmware/baochip_A/build/holodi_rot_A.uf2`. The script refuses
to embed a B bundle that fails HoloDi verification.

### 3. Test before touching hardware

```bash
firmware/baochip_A/test/run.sh          # A's real code vs an emulated H743 ROM bootloader
.venv/bin/python holodi/req_full_chain.py   # Renode: Baochip stand-in -> B -> C, 6 cases (~6 min)
```

### 4. Flash A onto the Dabao (persistent)

```bash
cd dabao-sdk
python3 tools/serial_flash.py --persistent /dev/tty.usbmodem<DABAO> ../firmware/baochip_A/build/holodi_rot_A.uf2
cd ..
```

`--persistent` sends `bootwait disable`, so A runs on every power-up, which is
the whole point. With bootwait enabled (the Dabao default), the board waits at
the boot1 prompt, A never runs, and the H743 stays in reset.

You can also use the README's own method. Copy `holodi_rot_A.uf2` onto the
`BAOCHIP` drive, `sync`, then type `bootwait disable` on the boot1 USB console
and press PROG.

To get back to the boot1 prompt for reflashing, hold PROG while applying power
or pressing RESET. `audit` at that prompt shows the boot1 revision and
`== IN DEVELOPER MODE ==` on bench boards.

Updating boot1 with `boot-updater.uf2` overwrites A. Reflash A right after the
update. Until then the H743 stays in reset.

### 5. First power-on: provision B (milestone M3)

A Kakute H7 from the factory already has code at 0x08000000 (usually
Betaflight, sometimes an ArduPilot bootloader). It isn't HoloDi-signed, so A
correctly refuses it (6 blinks). For the first install, do one of the
following:

- Fit the **PB5 to GND jumper**, power up, wait for a solid LED, then remove the
  jumper. Or
- Mass-erase the H743 once: `STM32_Programmer_CLI -c port=SWD -e all`. A then
  sees a blank sector and provisions B by itself.

Watch the log while this happens:

```bash
picocom -b 115200 -y e /dev/tty.usbserial<ADAPTER>
```

You should see `B bundle authentic`, then `B provisioned and verified: releasing H743`.
A also sets write protection (WRP) on sector 0 after installing B.

### 6. Install C through B (document section 8)

Connect the Kakute's USB. B enumerates as an ArduPilot bootloader.

```bash
.venv/bin/python ardupilot/Tools/scripts/uploader.py --port /dev/tty.usbmodem<KAKUTE> holodi/build/C.apj
```

B writes C and then checks C's signature on every boot. ArduCopter on MAVLink
means the whole chain is up.

### 7. Normal power-on (document section 6)

Remove the strap and power-cycle. A re-reads sector 0 through the ROM
bootloader, authenticates it, and releases reset without rewriting anything.
That takes roughly 13 s at 115200 baud for the full 128 KiB sector check.
`ROT_CHECK_FULL_SECTOR=0` reads only B's bytes (about 3 s), but then it no
longer proves the unused part of the sector is blank.

### 8. Bench attacks (milestones M4 and M5)

| Test | How | Expected |
|---|---|---|
| Tampered C | upload `holodi/build/C.tampered.apj` (C with its version banner edited after signing; regenerate with `req_full_chain.py --sign`) using `uploader.py` | B stays in bootloader mode, no MAVLink |
| Unsigned C | upload the stock ArduCopter `.apj` from firmware.ardupilot.org | same |
| B_malicious | over SWD with STM32CubeProgrammer: clear sector-0 write protection in the Option Bytes view (`STM32_Programmer_CLI -c port=SWD -ob displ` lists the WRP fields), then `STM32_Programmer_CLI -c port=SWD -w ardupilot/Tools/bootloaders/KakuteH7_bl.bin 0x08000000 -v`, then power-cycle | A: `REFUSED: ... unauthenticated B`, 6 blinks, H743 stays in reset |
| Recovery | fit the PB5 strap and power-cycle | A reinstalls the genuine B |

### Updating B

Rebuild and re-sign B (step 1), rebuild A (step 2), reflash A (step 4), then
power up once with the PB5 strap fitted. The new A doesn't recognise the old
B, so without the strap it refuses it, exactly as it would refuse B_malicious.

## Rules for H743 protection

- Keep **RDP at level 0**. A authenticates B by reading it through the ROM
  bootloader, and RDP level 1 blocks that read (A then holds reset with 5
  blinks). **Never set RDP level 2.** It's permanent and disables the ROM
  bootloader, so A could never verify or reinstall B again.
- WRP on sector 0 stops C or B from overwriting B. At RDP 0, anyone with SWD
  access can still lift WRP. That's acceptable because A re-authenticates B on
  every power-on (M5), but there's a window: a B modified at runtime would run
  after an H743-internal reset (watchdog, reboot) until the next power cycle.
- Don't use ArduPilot's secure-command key update (it rewrites keys inside B).
  Rotate keys by issuing a new signed B through A instead.
