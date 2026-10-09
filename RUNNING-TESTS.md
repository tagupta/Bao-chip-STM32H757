# Running the Baochip secure-boot and command-authentication tests

**Why this document exists:** so that anyone cloning this GitHub repository for the first time can reproduce the exact bench we used — Cube Orange+ running only Baochip-signed firmware, and obeying only commands that Holodi has signed — using the same tool versions, repository commits, and automated test scripts.

---

## 0. What this test actually proves

Two independent properties of the bench, in order:

1. **Secure boot** — the Cube's bootloader runs only firmware signed by the Baochip (the chip on the Dabao). Wrong-key, unsigned, and tampered images are refused.
2. **Command authentication** — once the Cube is running Holodi-enabled firmware, it accepts flight commands only when Holodi (the Baochip) has signed them. Commands sent directly to the Cube's own USB are ignored when `HOLODI_REQUIRE=1`.

The procedures those tests follow are documented in [`baochip-secure-boot.md`](./baochip-secure-boot.md) and [`baochip-command-auth.md`](./baochip-command-auth.md). This file tells you **how to run the tests** that automate the final proofs of each one.

---

## 1. Prerequisites: three repositories

This GitHub repo is **documentation + simulation only**. The bench flow depends on two private repositories hosted under the `Sureshot-Labs` GitHub org, plus a USB-connected Dabao and Cube Orange+.

| Repository                 | Remote                             | Branch        | Pinned commit                                  |
| -------------------------- | ---------------------------------- | ------------- | ---------------------------------------------- |
| **This repo** (`bao_chip`) | `tagupta/Bao-chip-STM32H757`       | `master`      | `17d8542`                                      |
| **fc-holodai**             | `Sureshot-Labs/fc-holodai`         | `main`        | **`efe1d7f18787264551d7b77f3886b0d0e3beb078`** |
| **xous-core-internal**     | `Sureshot-Labs/xous-core-internal` | `holodi-lane` | **`61e3e80ba0b9e1bcc918f7769ef8afb2a733fef2`** |

- **Access:** both `Sureshot-Labs` repos are private. If `git clone` returns "repository not found", your GitHub account does not have access yet — ask for it before continuing.
- **Why pinned commits and not just branches:** `fc-holodai/main` moves forward with work that has not yet been re-tested on hardware. The commit above is the one this bench was validated against. Bump the pin only after you re-run the tests in Section 5 on a newer commit.

### Hardware

- Cube Orange+ (micro-USB to laptop).
- Dabao board carrying the Baochip (USB-C to laptop).
- Cube `TELEM1` wired to Dabao `UART2` as described in [`baochip-command-auth.md`](./baochip-command-auth.md), §"Check the wires".
- A Mac. The `fc-holodai` Nix shell is tested on `aarch64-darwin` and `x86_64-linux`; the step-by-step commands in the guides are written for macOS.

### What is **not** in git and must be brought separately

- `fc-holodai/keys/` — laptop keys (`holodi-dev`, `secondary`) and the Baochip public key export (`holodi-chip_public_key.dat`). If the Cube was provisioned on another machine, **copy that machine's `keys/` directory in**; regenerating `holodi-dev` on a Cube that already trusts the old one will brick Phase G.
- `fc-holodai/out/` — signed `.apj` images used by the test scripts.

---

## 2. Suggested directory layout

Clone all three repositories as siblings:

```text
~/work/bao-bench/
├── bao_chip/              # this repo (docs + Renode sim)
├── fc-holodai/            # Nix shell, ArduPilot patches, `just` recipes, test scripts
└── xous-core-internal/    # Dabao firmware (only needed to (re)flash the Baochip)
```

```bash
mkdir -p ~/work/bao-bench && cd ~/work/bao-bench

git clone https://github.com/tagupta/Bao-chip-STM32H757.git bao_chip

git clone https://github.com/Sureshot-Labs/fc-holodai.git
git -C fc-holodai checkout efe1d7f18787264551d7b77f3886b0d0e3beb078

git clone -b holodi-lane https://github.com/Sureshot-Labs/xous-core-internal.git
git -C xous-core-internal checkout 61e3e80ba0b9e1bcc918f7769ef8afb2a733fef2
```

Everything below runs from `~/work/bao-bench/fc-holodai` inside **`nix develop`**, unless noted otherwise.

---

## 3. Two paths through this document

Pick the one that matches your bench state:

- **Path A — Full bring-up** (empty hardware, nothing provisioned). Follow Sections 4 and 5. Expect several hours.
- **Path B — Re-run tests only** (bench already provisioned, you just want to execute the automated checks). Jump to Section 5.

If you are not sure, run `just ports` in `fc-holodai`, plug in the Cube, and run `system_profiler SPUSBDataType | grep -iE "cube|bootloader"`:

- `CubeOrange+-Secure-BL-v10` while trying to boot a known-good signed image → bootloader is already the Baochip-trust bootloader → **Path B**.
- `CubeOrange+` running stock ArduCopter, or a Cube that was never touched → **Path A**.

---

## 4. Path A — Full bring-up (one time per bench)

Each phase below links to the authoritative procedure. The names (Phase 0, A, B, …) match [`baochip-secure-boot.md`](./baochip-secure-boot.md) and the "Part" numbers match [`baochip-command-auth.md`](./baochip-command-auth.md), so you can switch between this file and those without renumbering.

### Phase 0 — Laptop toolchain

Source: [`baochip-secure-boot.md`](./baochip-secure-boot.md), _Phase 0_.

1. Install **Nix** with flakes enabled; on Apple Silicon also install **Rosetta** (ArduPilot's GCC 10 is x86_64).
2. Enter the pinned shell:
   ```bash
   cd ~/work/bao-bench/fc-holodai
   nix develop
   ```
3. `just clone` (fetches ArduPilot `Copter-4.7.1`), then `just patch` (applies Holodi patches).
4. Create the two laptop keys **only if `keys/` doesn't already contain them**:
   ```bash
   just keygen holodi-dev
   just keygen secondary
   ```
   If this Cube was provisioned on another laptop, **copy that laptop's `keys/holodi-dev_*` into `keys/`** instead of generating new ones. A fresh `holodi-dev` here will cause Phase G to brick the Cube.

### Phase A–C — Baochip firmware and chip signing key

Source: [`baochip-secure-boot.md`](./baochip-secure-boot.md), _Phases A, B, C_.

1. Build the Dabao firmware from `xous-core-internal` (pinned to `61e3e80`):
   ```bash
   cd ~/work/bao-bench/xous-core-internal
   cargo xtask dabao dabao-console --no-timestamp --kernel-feature debug-proc
   ```
   If cargo complains about `is_multiple_of` or `copy_from_slice`, apply the two one-line fixes documented in Phase A of the secure-boot guide.
2. Flash the three UF2 files (`loader.uf2`, `xous.uf2`, `apps.uf2`) onto the Dabao in PROG mode.
3. From `fc-holodai`:
   ```bash
   just holodi-keygen <dabao-port>
   just holodi-pubkey <dabao-port>   # after replug; expect "unchanged, PUBLIC_KEYV1:..."
   ```
   This creates the Baochip chip key and exports its public half to `keys/holodi-chip_public_key.dat`. **Never run with `--force`** — it replaces the chip key and bricks every Cube that trusted the old one.

### Phase D–G — Secure bootloader onto the Cube

Source: [`baochip-secure-boot.md`](./baochip-secure-boot.md), _Phases D, E, F, G_.

1. `just secure-bl CubeOrangePlus holodi-chip` — build the Cube bootloader that trusts only the Baochip chip key.
2. `python3 tools/fwcheck.py keys out/CubeOrangePlus-secure-bl-holodi-chip.bin --expect keys/holodi-chip_public_key.dat` — confirm it trusts the right key.
3. Build signed and "should be refused" images (Phases E and F) so the test script in Section 5 has files to upload.
4. **Phase G is the one-way step.** It installs the new bootloader onto the Cube. Follow the guide exactly: a wrong bootloader here needs an SWD probe to recover.

### Part 1–7 of command auth

Source: [`baochip-command-auth.md`](./baochip-command-auth.md), _Parts 1 through 7_.

After Phase H of secure boot passes:

1. **Part 1** — set Holodi's command seed and lanes on the Dabao console (`blob seed`, `blob mode duplex`, `blob lane sign`, `blob lane verify`, `blob uart2baud 921600`).
2. **Parts 2–3** — build, sign, and upload the Holodi-enabled Cube firmware.
3. **Part 4** — pair the Cube with this Dabao.
4. **Part 7** — set `HOLODI_REQUIRE 1` so the Cube ignores direct-USB commands.

When Part 7 is done, the bench is in the exact state the Section 5 tests assume.

---

## 5. Path B — Running the automated tests

Both test scripts live in **`fc-holodai`** (untracked at commit `efe1d7f` — see Section 7). Open a shell:

```bash
cd ~/work/bao-bench/fc-holodai
nix develop
just ports            # identify Dabao and Cube USB device paths
```

### Phase T1 — Secure-boot test (`test_baochip_secure_boot.sh`)

**Plugged in:** Cube only. Dabao unplugged. Quit any MAVProxy still holding the Cube's port (`pkill -f mavproxy.py` if needed).

```bash
./test_baochip_secure_boot.sh /dev/cu.usbmodem<CUBE>
```

The script uploads five images from `out/` and, after each one, reads the Cube's USB device name to decide pass/fail:

| #   | Image                                          | Expected USB name           | What it proves                            |
| --- | ---------------------------------------------- | --------------------------- | ----------------------------------------- |
| 1   | `CubeOrangePlus-copter-signed-holodi-chip.apj` | `CubeOrange+`               | Legitimate Baochip-signed firmware boots. |
| 2   | Firmware signed with `secondary`               | `CubeOrange+-Secure-BL-v10` | Wrong-key image is refused.               |
| 3   | Unsigned firmware                              | `CubeOrange+-Secure-BL-v10` | Unsigned image is refused.                |
| 4   | Baochip-signed firmware with one byte tampered | `CubeOrange+-Secure-BL-v10` | Tampering is detected.                    |
| 5   | Baochip-signed firmware again                  | `CubeOrange+`               | Bench recovers to a known-good state.     |

**Expected final line:** `5 passed, 0 failed`.

### Phase T2 — Command-authentication test (`test_baochip_command_auth.sh`)

**Plugged in:** both Dabao and Cube. TELEM1 wired. Cube on `SERIAL1_PROTOCOL=51`, paired, with Holodi lanes configured (Section 4, Parts 1–4). The script asks you to **replug the Dabao** at the points where it needs to read `blob status` on the console side.

```bash
./test_baochip_command_auth.sh /dev/cu.usbmodem<DABAO> /dev/cu.usbmodem<CUBE>
```

The script walks five checks and reports pass/fail per check using counters from both ends (Cube `@SYS/holodi.txt` and Dabao `blob status`):

| #   | Check                                                                  | Pass condition                                                                                                          |
| --- | ---------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| 1   | Commands through Holodi are signed and obeyed                          | Mode change `ACCEPTED`; Cube `frames_in` goes up; no `rejected` counter moves.                                          |
| 2   | Direct Cube USB commands are ignored (needs `HOLODI_REQUIRE=1`)        | Mode does **not** change; `require 1 rx_locked_channels ≥ 1`. Skipped if `HOLODI_REQUIRE=0`.                            |
| 3   | Cube telemetry is tagged and Holodi verifies it                        | Cube `frames_out` goes up; Holodi `from_fc_frames > 0`.                                                                 |
| 4   | Both ends name the same pairing                                        | Cube `uid` == Cube `paired` == Holodi `paired`.                                                                         |
| 5   | _(optional, `--forged-telemetry`)_ Untagged telemetry is not forwarded | Needs `HOLODI_REQUIRE=0`. Script flips `SERIAL1_PROTOCOL` to `2`, confirms Holodi forwards nothing, then flips it back. |

Negative-telemetry run (optional):

```bash
./test_baochip_command_auth.sh /dev/cu.usbmodem<DABAO> /dev/cu.usbmodem<CUBE> --forged-telemetry
```

Only run this with `HOLODI_REQUIRE=0`. With the lock on, the script cannot put `SERIAL1_PROTOCOL` back from the Cube's own USB and could leave the board hard to recover.

**Expected final line:** `4 passed, 0 failed, 0 skipped` (or `5 passed` with `--forged-telemetry`).

---

## 6. Troubleshooting quick index

| Symptom                                                 | Where to look                                                                                                                 |
| ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `nix: command not found` right after installing Nix     | [`baochip-secure-boot.md`](./baochip-secure-boot.md), _Phase 0 → Nix_                                                         |
| `just ports` doesn't show one of the boards             | Replug that board; a name can change after each replug.                                                                       |
| Secure-boot test 1 stays in `CubeOrange+-Secure-BL-v10` | Re-run `just verify CubeOrangePlus holodi-chip` (Phase F), re-sign (Phase E), re-upload only image 1.                         |
| Command-auth test 1 shows `rejected bad-signature`      | Cube holds a different Holodi key than the Dabao currently signing. Redo pairing (Part 4).                                    |
| Command-auth test 2 says "SKIPPED (HOLODI_REQUIRE 0)"   | By design. Set `HOLODI_REQUIRE 1` through Holodi first (Part 7), then re-run.                                                 |
| `no answer within 10 s` on `just holodi-*`              | Dabao USB is in data mode. Replug the Dabao and re-run within 10 seconds, or free the port with `+++` then quit the terminal. |
| Script asks for a Dabao replug and nothing happens      | Any MAVProxy or terminal holding the Dabao port blocks it. Close it, replug, and continue.                                    |

More detailed troubleshooting lives inline in [`baochip-secure-boot.md`](./baochip-secure-boot.md) and [`baochip-command-auth.md`](./baochip-command-auth.md) under each phase/part — every block there has a **"If it fails"** note.

---

## 7. Housekeeping notes for the GitHub push

- The automated scripts (`test_baochip_secure_boot.sh`, `test_baochip_command_auth.sh`, `test_baochip_command_auth.py`) currently live **untracked** inside the local `fc-holodai` clone. Before pointing new users at this file, commit them to `Sureshot-Labs/fc-holodai` (at or after `efe1d7f`) so Section 5 works after a clean clone.
- `fc-holodai/main` on GitHub is a few commits ahead of `efe1d7f`. Do **not** bump the pin in Section 1 without re-running Phases T1 and T2 on the newer commit first.
- `keys/` and `out/` are intentionally git-ignored in `fc-holodai`. They are per-bench artifacts and must never be pushed.
