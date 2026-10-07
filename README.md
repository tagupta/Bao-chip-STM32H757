# Baochip / H743 Secure Boot Work Summary

This repo contains the recent work for a Baochip-controlled secure boot chain for
an STM32H743 flight controller running ArduPilot.

The core idea is:

```text
Baochip firmware A -> verifies H743 bootloader B -> verifies flight firmware C
```

Baochip is treated as the root of trust. It holds the H743 in reset, verifies
the H743 bootloader, installs or accepts only an authentic bootloader, and then
releases the H743. The trusted H743 bootloader then verifies the ArduCopter
firmware before running it.

## What We Have Done Recently

### 1. Defined the Secure Boot Flow

We documented and implemented the chain:

1. Generate a HoloDi private/public key pair.
2. Keep the private key on HoloDi.
3. Export the HoloDi public key.
4. Embed the public key into the H743 secure bootloader.
5. Build the H743 firmware on the laptop.
6. Send firmware to HoloDi for hashing/signing.
7. Bring the signed firmware back to the laptop.
8. Flash the signed firmware and signature to the flight controller.
9. The flight controller accepts only valid signatures and rejects invalid ones.

This is described in more detail in `SECURE_BOOT_EXPLAINED.md` and
`final_doc.txt`.

### 2. Built the H743 Bootloader and Firmware Signing Path

The H743 side now has a signed bootloader/firmware flow:

- `firmware/h743_B/build.sh` builds and signs the secure H743 artifacts.
- `holodi/boot_bundle.py` creates and verifies signed bootloader bundles.
- `holodi/sign_apj.py` signs ArduPilot `.apj` firmware images.
- `holodi/keys/holodi_public_key.dat` is the public key used by Baochip and the
  H743 bootloader to verify signatures.

The expected outputs are generated under `holodi/build/`:

- `B.bin`: raw H743 bootloader image.
- `B.bundle`: signed bootloader bundle verified by Baochip firmware A.
- `C.apj`: signed ArduCopter firmware verified by bootloader B.

### 3. Added Baochip Firmware A Root-of-Trust Logic

The Baochip firmware work lives in `firmware/baochip_A/`.

Firmware A is responsible for:

- holding the H743 in reset;
- talking to the H743 ROM bootloader;
- verifying the signed bootloader bundle;
- provisioning bootloader B into H743 flash when needed;
- checking that bootloader B remains authentic on later boots;
- keeping the H743 in reset if verification fails.

Important files:

- `firmware/baochip_A/rot.c`
- `firmware/baochip_A/rot.h`
- `firmware/baochip_A/stm32_rom.c`
- `firmware/baochip_A/build.sh`
- `firmware/baochip_A/test/run.sh`

### 4. Created a Simulation of the Full Chain

We added a Renode-based simulation for the complete trust chain.

The simulation models:

- Baochip verifying bootloader B;
- H743 running the trusted bootloader;
- bootloader B verifying ArduCopter firmware C;
- valid firmware reaching MAVLink heartbeat;
- invalid bootloader or firmware being rejected.

Important files:

- `holodi/baochip_verifier.py`: Python stand-in for Baochip firmware A.
- `holodi/req_full_chain.py`: automated full-chain test runner.
- `sim/run_secure_boot.sh`: interactive secure boot demo.
- `ardupilot/Tools/renode/peripherals/common/AP_BaochipController.cs`: Renode
  controller that exposes the Baochip/H743 link.

The automated test covers cases such as:

- blank H743 bootloader sector being provisioned;
- normal boot with authentic B already installed;
- tampered bootloader bundle being rejected;
- malicious bootloader in H743 flash being rejected;
- tampered application firmware being rejected by bootloader B;
- unsigned official firmware being rejected.

### 5. Wired Baochip to Cube Orange+ and Verified the UART Link

A major part of the recent work was physically connecting the Baochip (the green
Dabao board on a breadboard) to a **Cube Orange+** flight controller sitting on
its ADS-B carrier board, and proving that the two chips can talk.

#### The physical connection

```text
┌─────────────────────────────────────┐     JST 6-wire      ┌──────────────────┐
│  Cube Orange+ on ADS-B carrier      │ ◄──────────────────►│ Breadboard       │
│  Port: TELEM 1 (left side, white)  │   3 signals + GND   │ + green Baochip  │
└─────────────────────────────────────┘                     └────────┬─────────┘
         │ USB on orange cube face                                      │ USB-C
         └──────────────────────────── Mac ──────────────────────────────┘
```

The Baochip communicates with the Cube's TELEM 1 port via UART:

| TELEM1 pin | Wire            | Baochip pin           |
|------------|-----------------|-----------------------|
| 1          | +5 V            | **Not connected**     |
| 2          | TX (Cube sends) | **PB13** (Baochip RX) |
| 3          | RX (Cube receives) | **PB14** (Baochip TX) |
| 6          | GND             | **GND** (common)      |

A **10 kΩ pull-up to 3.3 V** is added on PB13 to prevent noise from feeding the
Baochip boot1 console when the Cube is in reset.

Two additional signals require **soldering inside the Cube module** (not
available on external headers):

| Baochip | Cube FMU          | Purpose                       |
|---------|--------------------|-------------------------------|
| PB2     | NRST               | Baochip holds/releases reset  |
| PB3     | BOOT0 (R7 pad)     | Baochip controls boot mode    |

A **PB5 → GND** jumper on the breadboard is used during provisioning only.

Two USB cables go to the Mac: the Baochip's **USB-C** (for flashing firmware A
and reading logs) and the **Cube's micro-USB** on the orange face (for uploading
flight firmware C).

See the annotated wiring photos in `docs/`:

- `docs/cube-orange-plus-adsb-actual-wiring.jpg` (Cube carrier layout)
- `docs/cube-orange-baochip-breadboard.jpg` (breadboard detail)

Full wiring reference: `docs/CUBE_ORANGE_PLUS_WIRING.md`.

#### What we ran: the MAVLink relay test

We wrote a test to prove that Baochip firmware can actually command the Cube
over this UART link. The test lives in `firmware/baochip_mavlink_test/`.

**Test firmware** (`firmware/baochip_mavlink_test/mavlink_test.c`): a bare-metal
C program for the Baochip that:

1. opens UART2 at 57600 baud (the same pins going to Cube TELEM1);
2. sends a MAVLink heartbeat at 1 Hz as an onboard computer (system ID 42);
3. waits for the Cube's own heartbeat;
4. sends `MAV_CMD_DO_SET_MODE` to switch the Cube to LOITER mode every 5 seconds;
5. blinks an LED on PB1 to show status (short flash = Cube heartbeat received,
   solid = mode change accepted, triple flash = mode change rejected).

**Monitor script** (`firmware/baochip_mavlink_test/cube_monitor.py`): a Python
script that connects to the Cube's USB and:

1. confirms the Cube is running ArduCopter (detected **ArduCopter 4.6.1**);
2. reads `SERIAL1_PROTOCOL` and `SERIAL1_BAUD` to verify TELEM1 is set for
   MAVLink2 at 57600 (can auto-fix with `--fix`);
3. watches for the Baochip's heartbeat (system ID 42) arriving through TELEM1;
4. reports flight-mode changes and Cube status messages.

#### What we did step by step

1. **Checked USB enumeration** — ran `system_profiler SPUSBDataType` and
   `ls /dev/cu.*` repeatedly to confirm when the Cube and Baochip appeared as
   USB serial devices on the Mac.

2. **Confirmed Cube firmware** — ran the monitor script against the Cube's USB:

   ```bash
   .venv/bin/python firmware/baochip_mavlink_test/cube_monitor.py --port /dev/cu.usbmodem11401
   ```

   Output confirmed **ArduCopter 4.6.1**, `SERIAL1_PROTOCOL = 2` (MAVLink2),
   `SERIAL1_BAUD = 57` (57600). The Cube was in STABILIZE mode, sending EKF3
   and PreArm status messages (expected on a bench without GPS).

3. **Built and flashed the MAVLink relay test onto the Baochip** — used the
   RISC-V toolchain in the Dabao SDK to build the test firmware, then flashed
   it with `serial_flash.py --persistent` so it runs on every power cycle:

   ```bash
   firmware/baochip_mavlink_test/build.sh
   .venv/bin/python dabao-sdk/tools/serial_flash.py --persistent \
       /dev/cu.usbmodemFMZTMM1 firmware/baochip_mavlink_test/build/mavlink_test.uf2
   ```

   The flash wrote 22 UF2 blocks to the Baochip and disabled boot-wait.

4. **Ran the monitor again and confirmed bidirectional communication**:

   ```bash
   .venv/bin/python firmware/baochip_mavlink_test/cube_monitor.py --port /dev/cu.usbmodem11401
   ```

   Key results:
   - **`[OK] Baochip heartbeat arrived through TELEM1: Baochip TX -> Cube RX works.`**
     — proves the Baochip's UART TX (PB14) is reaching the Cube's TELEM1 RX.
   - **`[Cube mode] LOITER`** — the Cube accepted the Baochip's
     `MAV_CMD_DO_SET_MODE` and switched from STABILIZE to LOITER, proving the
     Cube's TELEM1 TX is reaching the Baochip's UART RX (PB13) and that the
     full MAVLink command-acknowledge loop works.
   - The Cube continued to report EKF3 and PreArm messages, which is normal
     bench behaviour without a GPS fix.

#### What this proved

- The **UART wiring between Baochip and Cube Orange+ TELEM1 is correct** in
  both directions.
- Baochip firmware running on the Dabao can **parse MAVLink, send heartbeats,
  and issue flight-mode commands** to the Cube.
- The Cube treats the Baochip as a legitimate onboard companion computer.
- This UART link is the same one that the secure boot chain will use: firmware A
  talks to the H743 ROM bootloader over these exact pins to verify and install
  bootloader B.

### 6. Documented Hardware Deployment

Deployment and wiring notes were added under `docs/`.

Important docs:

- `docs/DEPLOY.md`: main hardware deployment guide.
- `docs/CUBE_ORANGE_PLUS_WIRING.md`: Cube Orange+ and Baochip wiring notes.
- `docs/BAOCHIP_README_ALIGNMENT.md`: decisions that align the architecture PDF
  with Baochip's boot flow and signing model.

The key hardware requirements are:

- Baochip must hold H743 reset from power-on.
- Baochip talks to the H743 ROM bootloader over UART.
- Baochip controls H743 `NRST` and `BOOT0`.
- H743 sector 0 contains bootloader B.
- Bootloader B must be write-protected after provisioning.

### 7. Clarified Key Ownership and Production Assumptions

We separated two different key roles:

- **HoloDi key**: signs H743 bootloader B and firmware C.
- **Baochip boot key**: signs Baochip firmware A for the Baochip boot chain.

On the bench, Baochip firmware A is developer-signed. That is acceptable for
testing, but it means someone with physical access and the ability to enter
developer mode could replace A.

For production, the selected path is:

- use a third-party Baochip boot1 signed for HoloDi;
- sign Baochip firmware A with HoloDi production keys;
- run Baochip lockdown during factory provisioning;
- prevent the public key or firmware A from being replaced after setup.

This answers the main security question from `final_doc.txt`: in dev mode, an
attacker replacing the embedded public key cannot be fully prevented. In
production, this must be prevented by factory provisioning, lockdown, and memory
protection so the trusted public key cannot be replaced.

## Common Commands

Run these from the repo root.

Install Python dependencies:

```bash
.venv/bin/python -m pip install pure25519 pyserial pymavlink pyfatfs==1.1.0 intelhex "setuptools<81"
```

Build and sign H743 bootloader B and firmware C:

```bash
firmware/h743_B/build.sh
```

Sign existing build outputs without rebuilding:

```bash
firmware/h743_B/build.sh --no-build
```

Build Baochip firmware A:

```bash
ROT_NRST_INVERTED=1 firmware/baochip_A/build.sh
```

Run Baochip firmware A host tests:

```bash
firmware/baochip_A/test/run.sh
```

Run the full Renode secure boot simulation:

```bash
.venv/bin/python holodi/req_full_chain.py
```

Run the interactive demo:

```bash
sim/run_secure_boot.sh
```

Build and flash the Baochip ↔ Cube MAVLink relay test:

```bash
firmware/baochip_mavlink_test/build.sh
.venv/bin/python dabao-sdk/tools/serial_flash.py --persistent /dev/cu.usbmodem<BAOCHIP> \
    firmware/baochip_mavlink_test/build/mavlink_test.uf2
```

Monitor the Cube from the Mac (confirms wiring, firmware, and Baochip heartbeat):

```bash
.venv/bin/python firmware/baochip_mavlink_test/cube_monitor.py --port /dev/cu.usbmodem<CUBE>
```

## Current Status

The repo now has:

- a documented secure boot architecture;
- HoloDi signing tools for H743 bootloader and firmware;
- Baochip firmware A root-of-trust logic;
- Renode simulation for the Baochip -> H743 bootloader -> ArduCopter chain;
- hardware deployment and Cube Orange+ wiring notes;
- tests for valid and invalid boot scenarios;
- a verified physical UART link between Baochip and Cube Orange+;
- a MAVLink relay test proving the Baochip can command the Cube over TELEM1.

The main remaining production work is to replace the bench developer-signing
setup with production Baochip signing, factory lockdown, and final hardware
provisioning.

