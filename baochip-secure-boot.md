# Baochip secure boot on the Cube Orange+

> **⚠️ Updated Oct 2026** — This guide reflects the holodi-fc changes:
> the firmware moved out of blobcrypt into the standalone `holodi-fc` service,
> console commands changed from `blob …` to `holodi-fc …`,
> keys derive from one root secret (firmware slot 0 + pairing slot 1),
> frame version 3, ArduPilot patches 0005–0024, and Dabao pins moved to BIO soft-UARTs.
> See [What changed](#what-changed-holodi-fc-update) at the end for a summary.

This guide moves a Cube Orange+ onto a bootloader that only runs firmware signed by the Baochip (the chip on the Dabao board). The signing key is generated inside the Baochip and never leaves it. At the end, the Cube boots Baochip-signed firmware and refuses firmware that is signed with another key, unsigned, or tampered with.

This is the Baochip path only. It leaves out the earlier demo where the laptop generated `holodi-dev`, signed firmware with it, and proved that bootloader on its own.

One use of that laptop key remains, in Phase G. The Cube you start from already trusts `holodi-dev`, so the new bootloader has to travel inside a firmware image that the old bootloader will accept. That image is signed with the laptop key. After Phase G, the laptop key can no longer boot this Cube.

Every block below has two notes before the commands. **What this does** says what the commands change. **If it fails** says what to do instead of guessing.

## At a glance

| Phase | On USB | Changes hardware? |
|---|---|---|
| 0. Laptop setup | nothing | no |
| A. Build the Holodi firmware | nothing | no |
| B. Flash the Baochip | Dabao | yes, the Dabao (erases its key store) |
| B½. Dabao provisioning (bootwait) | Dabao | yes, one-time (disables boot1 console at power-up) |
| C. Create the root secret on the Baochip | Dabao | yes, the Dabao (creates root secret → 3 keys) |
| D. Build the chip-trusting Cube bootloader (2 keys) | nothing | no |
| E. Baochip signs the firmware | Dabao | no |
| F. Build the images the Cube must refuse | nothing | no |
| G. Move the Cube onto the chip key | Cube | **yes, the Cube bootloader. One way.** |
| H. Prove accept and reject | Cube | the Cube firmware only |

Phases A to F can be repeated as often as you like. Phase G is the only step that cannot be undone from the laptop alone.

## Starting point

- The Cube Orange+ is already running the secure bootloader that trusts `keys/holodi-dev_public_key.dat`. A Cube still on its stock bootloader also works: that bootloader checks no signatures, so Phase G works the same way.
- You can reach `Sureshot-Labs/fc-holodai` and `Sureshot-Labs/xous-core-internal`, branch **`holodi-fc`** (previously `holodi-lane`).
- Only one board is on USB at a time. The Baochip uses USB-C. The Cube uses micro-USB. The TELEM1 wires are not part of this procedure.
- `fc-holodai/keys/` and `fc-holodai/out/` are git-ignored. Key files never come with a clone. Copy them from the machine that set up the Cube, as Phase 0 describes.

Every `fc-holodai` command below runs inside `nix develop`, from `fc-holodai`.

Port names below are examples. Always use the name printed by `just ports`.

## Phase 0. First-time laptop setup

Do this once, before Phase A, on a machine that has never built this tree.

### Nix

> **What this does:** installs Nix, which pins every tool this procedure uses (ArduPilot's GCC 10, Python, `just`, MAVProxy), and turns on the `nix develop` command.
>
> **If it fails:** if `nix` is "command not found" right after installing, open a new terminal; the installer only changes new shells. If it is still missing, run `. /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh` in that terminal. If `/nix` is not mounted (`mount | grep ' /nix '` prints nothing), the install is broken: uninstall and run the installer again.

Install Nix with the installer from [nixos.org](https://nixos.org/download/), then open a new terminal and turn on flakes:

```bash
mkdir -p ~/.config/nix
printf "experimental-features = nix-command flakes\n" > ~/.config/nix/nix.conf
```

### Rosetta (Apple silicon only)

> **What this does:** lets the Mac run ArduPilot's GCC 10, which is an x86_64 program.
>
> **If it fails:** if it says Rosetta is already installed, carry on.

```bash
softwareupdate --install-rosetta
```

### Clone and enter the toolchain

> **What this does:** clones the flight-controller repo and opens the pinned shell. The first `nix develop` downloads the whole toolchain and can take several minutes. Later runs start in seconds.
>
> **If it fails:** `experimental Nix feature 'nix-command' is disabled` means the `nix.conf` line above is missing. Any other error: run `nix develop` again first, since a network drop during the download is the usual cause.

```bash
cd /Users/tanugupta/Work/bao_chip
git clone https://github.com/Sureshot-Labs/fc-holodai.git
cd fc-holodai
nix develop
```

`just patch` commits the ArduPilot patches with `git am`, so git needs an identity. If `git config user.name` or `git config user.email` is empty, set them before continuing.

### ArduPilot and the Holodi patches

> **What this does:** `just clone` fetches ArduPilot `Copter-4.7.1` into `ardupilot/`. `just patch` applies the Holodi patches on top of it as commits.
>
> **If it fails:** if the clone stops partway, delete `ardupilot/` and run `just clone` again, because the recipe skips any existing folder. If a patch does not apply, `just patch` prints how to inspect the conflict. To start over, run `just unpatch`, then `just patch`. Never run `just unpatch` after Phase D.

```bash
just clone
just patch
```

`just clone` is skipped when `ardupilot/` already exists. `just patch` is skipped for any patch whose commit is already on that clone. Both are safe to run again.

You can also skip typing them. `just secure-bl`, `just build-signed`, `just build-unsigned`, and `just holodi-sign` depend on `patch`, and `patch` depends on `clone`, so the first build in Phase D runs both. Run them here anyway: the clone is large, and a patch failure is easier to see on its own.

`just test` is the repo's full check (ArduPilot tests, Python, Rust, linters). It is not required to flash the boards. Run it if you want to confirm the tree before touching hardware.

### Laptop key files

> **What this does:** creates the two laptop keys. Phase G needs `holodi-dev`, and Phase F needs `secondary` for the "wrong key" image.
>
> **If it fails:** `keys/<name>_private_key.dat exists` means the key is already there. That is fine, so keep it.

`holodi-dev` must be the same key the Cube's current bootloader trusts. If this Cube was set up on another machine, copy that machine's `keys/holodi-dev_private_key.dat` and `keys/holodi-dev_public_key.dat` into `keys/` instead of generating new ones. A new `holodi-dev` produces a key the installed bootloader will reject, and Phase G will not boot. The only exception is a Cube on its stock bootloader, which checks no key, so a freshly generated `holodi-dev` works.

Create a key only if `keys/` does not already contain it. `just keygen` refuses to overwrite an existing key:

```bash
just keygen holodi-dev
just keygen secondary
```

## Phase A. Build the Holodi firmware

No board needs to be plugged in. This phase runs from `xous-core-internal`, not `fc-holodai`.

> **What this does:** builds the Baochip firmware — now the standalone `holodi-fc` service (previously blobcrypt). The result is three UF2 files for the Dabao.
>
> **If it fails:** if cargo says the RISC-V target or toolchain is missing, run `cargo xtask install-toolchain` in `xous-core-internal` and repeat the build. If the clone says the repository does not exist, your GitHub account does not have access to `Sureshot-Labs/xous-core-internal`; ask for access.

```bash
cd /Users/tanugupta/Work/bao_chip
git clone -b holodi-fc https://github.com/Sureshot-Labs/xous-core-internal.git
cd xous-core-internal
services/holodi-fc/build-image.sh
```

The build is reproducible: two builds of the same commit with the same toolchain give the same `sha256` hashes. Compare them before flashing an image someone else built.

Expect these three files:

```text
target/riscv32imac-unknown-xous-elf/release/loader.uf2
target/riscv32imac-unknown-xous-elf/release/xous.uf2
target/riscv32imac-unknown-xous-elf/release/apps.uf2
```

Do not generate a key yet. The first boot of this firmware erases the Baochip key store.

## Phase B. Flash the Baochip

1. Unplug the Cube.
2. Hold **PROG** on the Dabao and plug in its USB-C cable. Release PROG after about 2 seconds.
3. A drive named `BAOCHIP` appears. On the Mac, copy the files without Finder metadata, one at a time.

> **What this does:** writes the new firmware onto the Dabao. On first boot it erases the Baochip key store, which is why the key is created only after this phase.
>
> **If it fails:** if no `BAOCHIP` drive appears, unplug and repeat step 2, holding PROG a little longer. If a `cp` hangs for more than a minute, unplug the Dabao, wait 5 seconds, repeat step 2, and copy again from the file that hung. If copies keep hanging, run `sudo mdutil -i off /Volumes/BAOCHIP` so Spotlight stops indexing the drive, then copy again. If the drive disappears between files, wait until it returns before copying the next one.

```bash
cd /Users/tanugupta/Work/bao_chip/xous-core-internal/target/riscv32imac-unknown-xous-elf/release
for f in loader xous apps; do dd if=$f.uf2 of=/Volumes/BAOCHIP/$f.uf2 bs=512 && sync; done
diskutil eject /Volumes/BAOCHIP
```

4. Eject the drive, unplug the Dabao, wait 3 seconds, and plug it back in without holding PROG. If the Mac asks to set up a keyboard, quit that window. If the `BAOCHIP` drive appears again, press PROG once.
5. In the `fc-holodai` shell, run `just ports` and note the Dabao port. It looks like `/dev/cu.usbmodemFMZTMM1`. It can change after a replug.

## Phase B½. Dabao provisioning (bootwait)

Each Dabao needs a one-time provisioning step. With boot1's console still open (the port shows `~~Boot1 up!`):

> **What this does:** disables boot1's console at power-up. Without this, boot1 waits with a passwordless console on PB13 every time the Dabao powers up — a security risk once wired to the FC. The image's loader sets bootwait on at first boot if boot1's counter reads 0; `enable` then `disable` moves it past 0.
>
> **If it fails:** if `bootwait check` still says Enable, repeat `bootwait enable` then `bootwait disable` again. If the port is unresponsive, replug the Dabao while holding PROG.

```text
bootwait enable
bootwait disable
bootwait check        # should print: bootwait is Disable
```

Then boot the Xous shell: press PROG again, type `boot`, or power-cycle. The shell appears on the same USB port, and `holodi-fc status` answers `root_secret no` and `lanes idle: no root secret at boot`. Close the terminal before the recipes below use the port.

**Check:** after a power-cycle without PROG, the port shows the Xous shell, without `~~Boot1 up!` first.

## Phase C. Create the root secret, then export keys

The Dabao console answers for about 10 seconds after boot, then the USB port switches to data mode. If a command gets no answer, the easiest fix is to unplug the Dabao, plug it back in without PROG, and run the command within 10 seconds. The other way is to open `just term /dev/cu.usbmodemFMZTMM1`, wait one second, type `+++`, and quit the terminal with `Ctrl-T` then `q`. That hands the port back to the console until the next reboot. The `just holodi-*` commands cannot use the port while a terminal or MAVProxy holds it.

With only the Dabao plugged in:

> **What this does:** asks the Baochip to draw a root secret inside the chip (`holodi-fc keys gen`). From that one root secret, three keys are derived: **link**, **pairing** and **firmware**. The firmware and pairing public keys are exported to `keys/holodi-chip-firmware_public_key.dat` and `keys/holodi-chip-pairing_public_key.dat`. There is no key import any more — `holodi-fc keys gen` creates the single root secret on the chip.
>
> **If it fails:**
> - `no answer ... within 10 s`: the console is in data mode or a terminal holds the port. Close any terminal, replug the Dabao, and run the command again within 10 seconds.
> - `this Dabao image has no such holodi-fc subcommand`: the Dabao is not running the Phase A firmware. Repeat Phase B.
> - `keys/holodi-chip-firmware_public_key.dat exists`: a chip key was exported earlier. Do not delete it. Run `just holodi-keys` below and go by its answer.
> - `root_secret error:`: the Baochip already holds a root secret. Run `just holodi-keys` below to export its keys instead.
>
> Never use the `--force` option. It replaces the root secret, which changes all three keys and erases the pairing record. Every Cube bootloader built for the old keys then refuses everything the Baochip signs.

```bash
just holodi-keygen /dev/cu.usbmodemFMZTMM1
```

Expect two new files: `keys/holodi-chip-firmware_public_key.dat` and `keys/holodi-chip-pairing_public_key.dat`, each containing one `PUBLIC_KEYV1:...` line. There is no private-key file. The private keys stay on the chip. The public key files are not secret. Any laptop that signs or builds for this Cube needs a copy of them.

Unplug the Dabao, wait 3 seconds, plug it back in without PROG, then:

> **What this does:** reads the keys back after a reboot, which proves the Baochip stored the root secret rather than only holding it in memory.
>
> **If it fails:** if it says a file `holds another key`, the Baochip's key differs from the file. Stop here. If no Cube trusts the old files yet, move them aside and repeat this phase from `holodi-keygen`. If a Cube already trusts them, you need the Dabao that made those keys.

```bash
just holodi-keys /dev/cu.usbmodemFMZTMM1
```

Expect `unchanged, PUBLIC_KEYV1:...` for both files. Stop if either does not say `unchanged`.

## Phase D. Build the Cube bootloader that trusts the chip (two keys)

No board needs to be plugged in.

> **What this does:** builds the Cube bootloader with **two** Baochip public keys — the firmware key in slot 0 and the pairing key in slot 1. The bootloader is built from the patched ArduPilot tree, which includes patch 0022: stock ArduPilot's secure bootloader also tries its empty key slots, and with those it accepts signatures made without any key (about one time in four). **Any secure bootloader built before this patch should be rebuilt from the patched tree.** On the Cube that means `flashbootloader` from an image built with the current repo. The build must use ArduPilot's GCC 10.
>
> **If it fails:** if GCC is not `10-2020-q4-major`, you are outside `nix develop`. Enter it and rebuild. Never install a bootloader built with another GCC: ArduPilot issue [#32511](https://github.com/ArduPilot/ardupilot/issues/32511) is a Cube left with dead USB by a GCC 13 build, recoverable only with an SWD debug probe. If `fwcheck` prints `FAIL`, the wrong key file was used. Check `keys/holodi-chip-firmware_public_key.dat` and `keys/holodi-chip-pairing_public_key.dat` (Phase C) and run `just secure-bl` again.

```bash
arm-none-eabi-gcc --version
just secure-bl CubeOrangePlus holodi-chip
```

Expect GCC `10-2020-q4-major`, then `OK: trusts exactly holodi-chip-firmware_public_key.dat, holodi-chip-pairing_public_key.dat, in this slot order`. A single-key bootloader can't pair — you need both keys in the bootloader. Do not run `just reset-bl` or `just unpatch` after this. Both remove this bootloader from the tree, and the Phase G image would then carry the wrong one.

Optional extra check if this laptop also built the `holodi-dev` bootloader that runs on the Cube today (`out/CubeOrangePlus-secure-bl-holodi-dev.bin` exists). The two files should differ only in the key and the checksum at the end, which shows the new bootloader is the same code as the one already proven on this Cube:

```bash
cmp -l out/CubeOrangePlus-secure-bl-holodi-dev.bin out/CubeOrangePlus-secure-bl-holodi-chip.bin | wc -l
```

Expect about 36 (32 key bytes plus the 4-byte checksum). Hundreds of differences mean the two were built differently. Find out why before Phase G.

## Phase E. Have the Baochip sign the firmware

Plug in only the Dabao. If `just ports` shows a new name, use that name.

> **What this does:** builds ArduCopter with an empty signature slot, sends the Baochip only a 64-byte digest of it over USB (`holodi-fc fwsign begin`, then `holodi-fc fwsign finish` with the digest the laptop computes), checks the returned signature on the laptop, and writes `out/CubeOrangePlus-copter-signed-holodi-chip-firmware.apj`. The build takes a few minutes. The signed-build recipes now refuse an image whose embedded bootloader doesn't trust its signing key — without that check, a `flashbootloader` could leave the Cube recoverable only over SWD.
>
> **If it fails:** nothing on the Cube has changed, so it is safe to fix and rerun.
> - `...secure-bl-holodi-chip.bin is missing` or `does not trust`: run Phase D again.
> - `Holodi holds the firmware key X, the bootloader trusts Y: nothing signed`: this is a different Dabao, or the root secret changed. Stop and compare with Phase C.
> - `no answer`: replug the Dabao and run the command again. The 10-second window starts at boot, and the build runs first, so you may need `+++` (Phase C) right after the build ends.
> - `does not verify` or `the bootloader model refuses`: nothing was written. Run the command again once, and stop if it repeats.

```bash
just holodi-sign CubeOrangePlus /dev/cu.usbmodemFMZTMM1
```

The AP_Holodi image (for on-board pairing) is now:

```bash
just holodi-sign CubeOrangePlus /dev/cu.usbmodemFMZTMM1 --holodi
```

Expect the ending lines:

```text
out/CubeOrangePlus-copter-signed-holodi-chip-firmware.apj: signed by Holodi's key <base64>; bootloader model: OK
slot 0: <the same base64>  holodi-chip-firmware_public_key.dat
slot 1: <base64>  holodi-chip-pairing_public_key.dat
OK: trusts exactly holodi-chip-firmware_public_key.dat, holodi-chip-pairing_public_key.dat, in this slot order
```

The Cube has not changed yet.

## Phase F. Build the images the Cube must refuse

No board needs to be plugged in.

> **What this does:** builds three bad images: one signed with the `secondary` laptop key, one unsigned, and one copy of the Baochip-signed image with its version text changed. `verify` then runs a model of the bootloader's checks on the laptop against all four images.
>
> **If it fails:** if any line prints `FAIL`, do not continue. Rebuild the image named on that line with its command above. If the genuine image fails, repeat Phase E. If `build-signed ... secondary` says the key is missing, run `just keygen secondary`.

```bash
just build-signed CubeOrangePlus secondary --refused
just build-unsigned CubeOrangePlus
just tamper CubeOrangePlus holodi-chip-firmware
just verify CubeOrangePlus holodi-chip
python3 tools/fwcheck.py check out/CubeOrangePlus-secure-bl-holodi-chip.bin out/CubeOrangePlus-copter-to-sign.apj --expect BAD_FIRMWARE_SIGNATURE
```

Expect `verify` to print `OK: trusts exactly holodi-chip-firmware_public_key.dat, holodi-chip-pairing_public_key.dat, in this slot order`, then:

- `OK` for the Baochip image
- `VERIFICATION` for the secondary-key image
- `NO_APP_SIG` for the unsigned image
- `VERIFICATION` for the tampered image

The last command checks the image Phase E built before the Baochip signed it, and expects `BAD_FIRMWARE_SIGNATURE`.

Do not continue if any line says `FAIL`.

`just build-signed CubeOrangePlus secondary` also embeds the chip bootloader, but that image is never installed with `flashbootloader`. It is only uploaded later to show rejection.

## Phase G. Move the Cube onto the chip key

This is the handoff, and it cannot be undone from the laptop alone. `just secure-bl` in Phase D left the chip bootloader in the ArduPilot tree. The next build therefore contains:

```text
ArduCopter + embedded bootloader that trusts holodi-chip
```

and the whole image is signed with the existing laptop key, so today's Cube will boot it.

### Checks before touching the Cube

> **What this does:** confirms, on the laptop, that the bootloader the next build will embed trusts exactly the Baochip key.
>
> **If it fails:** if this prints `FAIL`, the tree holds another bootloader. Run Phase D again, then repeat this check. Do not build or upload until it prints `OK`.

```bash
python3 tools/fwcheck.py keys ardupilot/Tools/bootloaders/CubeOrangePlus_bl.bin --expect keys/holodi-chip-firmware_public_key.dat
```

Expect `OK: trusts exactly holodi-chip-firmware_public_key.dat, holodi-chip-pairing_public_key.dat, in this slot order`.

Optional: keep a copy of the current laptop-signed image, if one exists, before the build below overwrites it:

```bash
mkdir -p backup
cp -n out/CubeOrangePlus-copter-signed-holodi-dev.apj backup/CubeOrangePlus-copter-signed-holodi-dev-with-dev-bl.apj
```

### Build and upload the handoff image

Unplug the Dabao. Plug in only the Cube.

> **What this does:** builds ArduCopter signed with the laptop key, carrying the chip bootloader, and uploads it. The Cube's current bootloader checks the laptop signature and boots it. The bootloader itself is not changed yet.
>
> **If it fails:** nothing permanent has happened yet.
> - If the upload waits forever for the board, quit every MAVProxy (`pkill -f mavproxy.py`), replug the Cube, check the port with `just ports`, and upload again.
> - If the upload finishes but the Cube stays as `CubeOrange+-Secure-BL-v10`, its current bootloader does not trust this `holodi-dev` key. Stop. Find the key the Cube was set up with (Phase 0), rebuild, and upload again.

```bash
just build-signed CubeOrangePlus holodi-dev
just ports
just upload out/CubeOrangePlus-copter-signed-holodi-dev.apj /dev/cu.usbmodem11401
```

Use the port `just ports` printed. Expect the upload to finish and ArduCopter to boot. The Cube is still using the old bootloader at this moment.

If `out/CubeOrangePlus-secure-bl-holodi-dev.bin` exists on this laptop, you can confirm between the build and the upload that the old bootloader accepts the image: `python3 tools/fwcheck.py check out/CubeOrangePlus-secure-bl-holodi-dev.bin out/CubeOrangePlus-copter-signed-holodi-dev.apj --expect OK`. Do not run `just secure-bl CubeOrangePlus holodi-dev` to create that file, because it would replace the chip bootloader in the tree.

### Install the new bootloader

Wait about 10 seconds, then:

> **What this does:** connects MAVProxy to the running ArduCopter. `flashbootloader` makes ArduCopter erase flash sector 0 and write the chip bootloader it carries.
>
> **If it fails:**
> - If MAVProxy shows no `STABILIZE>` prompt or heartbeat, quit it, wait 10 more seconds, and connect again on the port from `just ports`.
> - `Bootloader not signed` means the embedded bootloader has no key. Nothing was written. Run Phase D again, then repeat this phase from the start.
> - If the Cube loses power or USB between `Erasing` and `Flash OK`, it has no bootloader. The only recovery is an SWD debug probe on the Cube's debug pins.

```bash
just mav /dev/cu.usbmodem11401
```

`just mav` runs `mavproxy.py --master=<port> --baudrate 115200 --state-basedir .mavproxy`.

At the `STABILIZE>` prompt, type:

```text
flashbootloader
```

Expect, in order:

```text
Got COMMAND_ACK: FLASH_BOOTLOADER: ACCEPTED
AP: Erasing
AP: Flashing bootloader.bin @08000000
AP: Flash OK
```

Leave the Cube plugged in until `Flash OK`. During the erase, the old bootloader is already gone. Unplug only after `Flash OK`, wait 3 seconds, and plug the Cube back in.

### Check the handoff

> **What this does:** shows the name the Cube gives on USB. Staying in the bootloader is the expected result here: the new bootloader trusts only the Baochip, and the firmware still on the Cube was signed by the laptop.
>
> **If it fails:**
> - If nothing prints, wait 10 seconds and run the command again. (`just usb` prints `no board on USB` on this Mac even when the Cube is connected, so use this command.)
> - If it shows `CubeOrange+` running ArduCopter instead, the old bootloader is still in place. Run the check from "Checks before touching the Cube", then repeat `flashbootloader`.
> - If the Cube never appears on USB at all, the bootloader does not start. That needs an SWD debug probe.

```bash
system_profiler SPUSBDataType | grep -iE "cube|bootloader"
```

Expect `CubeOrange+-Secure-BL-v10`, and expect it to stay there. From here, only the Baochip can sign firmware this Cube will run.

## Phase H. Prove accept and reject

Keep only the Cube plugged in. Quit MAVProxy first so it is not holding the port. `Ctrl-C` is not always enough. If an upload hangs, run `pkill -f mavproxy.py`.

> **What this does:** the script uploads five images one after another, waits 12 seconds after each, and prints the Cube's USB name. Only firmware uploads happen here. The bootloader is not touched, so this phase is safe to repeat.
>
> **If it fails:**
> - The script stops at the first upload that errors. Quit MAVProxy, replug the Cube, check the port with `just ports`, and run the script again from the start.
> - If the first image stays in `CubeOrange+-Secure-BL-v10`, the Baochip image does not match the new bootloader. Run `just verify CubeOrangePlus holodi-chip` (Phase F), sign again (Phase E), and upload only that image with `just upload out/CubeOrangePlus-copter-signed-holodi-chip.apj <port>`.
> - If any of the three bad images boots `CubeOrange+`, the secure boot is not working. Stop, and do not use this Cube until you know why.

```bash
just ports
./test_baochip_secure_boot.sh /dev/cu.usbmodem11401
```

Read the USB name after each one:

| Image | Expected USB name |
|---|---|
| Baochip-signed firmware | `CubeOrange+` |
| Firmware signed with `secondary` | `CubeOrange+-Secure-BL-v10` |
| Unsigned firmware | `CubeOrange+-Secure-BL-v10` |
| Baochip-signed firmware with one string changed | `CubeOrange+-Secure-BL-v10` |
| Baochip-signed firmware again | `CubeOrange+` |

The Cube should finish running ArduCopter `V4.7.1`. To see that yourself, run `just mav <port>`. A heartbeat and the `STABILIZE>` prompt mean it is flying firmware.

> **⚠️ Frame version 3 compatibility:** commands are now bound to the FC's boot, so a recorded command can't be replayed after a reboot. The emulator, the ArduPilot patches and the Dabao all moved together. **Old images won't talk to new ones** — rebuild everything from the current tree.

## Running the demo again later

The images are already built, so the Dabao does not need to be plugged in. After a laptop restart:

```bash
cd /Users/tanugupta/Work/bao_chip/fc-holodai
nix develop
just ports
./test_baochip_secure_boot.sh /dev/cu.usbmodem11401
```

Plug in only the Cube, and pass the port `just ports` printed. Do not run `flashbootloader` during a demo; the bootloader is already in place.

To show new firmware, plug in only the Dabao and repeat Phase E. Phase F is needed only if you want fresh bad images.

## Afterward

The Baochip is now the only signer for this Cube. Treat it as the Cube's key.

- Do not reflash the Dabao. A new developer image can erase its key store, and this Cube would then have no signer.
- Never replace the chip key (`--force`).
- Do not run `just reset-bl`, `just unpatch`, or `just secure-bl CubeOrangePlus holodi-dev` unless you are going back (below). Each one changes the bootloader that the next Cube build carries.
- Keep `keys/holodi-chip_public_key.dat`. It is not secret, but every bootloader for this Cube is built from it.

## Going back to the laptop key

This has to be done while the Baochip still holds its root secret. If the root secret is lost first, the only way back is an SWD debug probe. These steps come from the "Going back" section of `fc-holodai/docs/bench/holodi-signed-firmware.md` and have not been run on this bench yet.

> **What this does:** the Baochip signs one last image that carries a bootloader trusting the emulator's keys `holodi-emu`. The Cube's chip bootloader accepts that image, and `flashbootloader` then installs the emulator-key bootloader.
>
> **If it fails:** stop before `flashbootloader` if any check fails. Until that point, the Cube still trusts the Baochip and runs Baochip-signed firmware as before.

With only the Dabao plugged in:

```bash
just link-keys
just secure-bl CubeOrangePlus holodi-emu
just build-to-sign CubeOrangePlus
python3 tools/holodi_sign.py sign out/CubeOrangePlus-copter-to-sign.apj --port /dev/cu.usbmodemFMZTMM1 \
    --expect keys/holodi-chip-firmware_public_key.dat \
    --out out/CubeOrangePlus-copter-back-to-holodi-emu.apj
```

Then, with only the Cube plugged in:

```bash
just upload out/CubeOrangePlus-copter-back-to-holodi-emu.apj /dev/cu.usbmodem11401
just mav /dev/cu.usbmodem11401
```

Type `flashbootloader` at the `STABILIZE>` prompt and wait for `Flash OK`, as in Phase G. Unplug and replug the Cube. It stays in its bootloader, because the image on it is Baochip-signed and the bootloader now trusts `holodi-emu`. Finish with an emulator-signed image:

```bash
just build-signed CubeOrangePlus holodi-emu-firmware
just upload out/CubeOrangePlus-copter-signed-holodi-emu-firmware.apj /dev/cu.usbmodem11401
```

The Cube should boot ArduCopter again.

---

## What changed (holodi-fc update)

This section summarizes the breaking changes from the holodi-fc update. Keep it as a reference when comparing old and new setups.

### 1. Holodi firmware is its own service

The firmware moved out of `blobcrypt` (PR #10 on fc-holodai is closed) into the `holodi-fc` service on the `holodi-fc` branch of `xous-core-internal`. Console commands changed from `blob …` to `holodi-fc …`:

| Old command | New command |
|---|---|
| `blob fwkey pub` | `holodi-fc keys` |
| `blob fwkey gen` | `holodi-fc keys gen` |
| `blob fwsign begin` / `finish` | `holodi-fc fwsign begin` / `finish` |
| `blob hb` | `holodi-fc status` |
| *(n/a)* | `holodi-fc pair` |
| *(n/a)* | `holodi-fc watch [seconds\|off]` |

There is no key import any more: `holodi-fc keys gen` creates one root secret on the chip, and all keys derive from it.

### 2. Two keys in the bootloader

The root secret gives three keys: **link**, **pairing** and **firmware**. The secure bootloader now holds two of them:
- Slot 0: firmware key
- Slot 1: pairing key

Export both with `just holodi-keys <port> holodi-chip`, build the bootloader with `just secure-bl <board> holodi-chip`. A single-key bootloader can't pair.

### 3. Frame version 3

Commands are now bound to the FC's boot, so a recorded command can't be replayed after a reboot. The emulator, the ArduPilot patches and the Dabao all moved together. **Old images won't talk to new ones** — rebuild everything from the current tree.

### 4. ArduPilot patches 0005–0024

These are review fixes. The critical one: stock ArduPilot's secure bootloader also tries its empty key slots, and with those it accepts signatures made without any key, about one time in four. **Patch 0022** fixes it, so any secure bootloader built before today should be rebuilt from the patched tree. On the Cube that means `flashbootloader` from an image built with the current repo.

### 5. Cube safety

The signed-build recipes now refuse an image whose embedded bootloader doesn't trust its signing key. Without that check, a `flashbootloader` could leave the Cube recoverable only over SWD. The AP_Holodi image signed through Holodi is now: `just build-to-sign CubeOrangePlus --holodi`, then `just holodi-sign CubeOrangePlus <port> --holodi`.

### 6. Dabao pins

Both links now use the BIO soft-UARTs. Nothing connects to PB13/PB14 (boot1's console). Each Dabao needs one-time provisioning (`bootwait enable`, `bootwait disable`, `bootwait check`).

| Link | Pin |
|---|---|
| FC → Holodi (in) | PB3 (header pin 32) |
| Holodi → FC (out) | PB2 (pin 31) |
| Radio → Holodi (in) | PB5 (pin 35) |
| Holodi → radio (out) | PB4 (pin 34) |
| boot1 console (do not connect) | PB13, PB14 |

### What's unchanged

- The two-step signing (the laptop hashes, Holodi signs)
- ArduPilot's own signature format
- The overall Cube procedure (Phases A through H)
