# Architecture PDF vs. xous-core `README-baochip.md`: final decisions

Authority order, decided once:

1. [`README-baochip.md`](https://github.com/betrusted-io/xous-core/blob/dev/README-baochip.md)
   (xous-core `dev`, checked against the `bao1x-boot/boot1` sources at commit `c025441`)
   is the final word on anything the Baochip itself does: boot0, boot1, keys,
   signing, lockdown, pins boot1 owns.
2. *Baochip–STM32H743 Secure Boot Architecture.pdf* is the final word on the
   H743 side: the A → B → C trust chain, reset-gated release, and the milestones M1–M5.

The two documents don't contradict each other on the trust chain. The README
answers the questions the PDF left open (its sections 2, 10 and 11) and adds
constraints the PDF didn't know about. Everything below is decided. Reopen it
only if the README itself changes.

## How the PDF maps onto the README

| PDF term | README / implementation | Status |
|---|---|---|
| "Baochip boot0 → boot1 → Baochip firmware" (section 2) | boot0 checks boot1 against the IFR keys. boot1 checks our firmware A against the **key manifest in boot1's own header** (`BOOT1_TO_LOADER_OR_BAREMETAL`). A is a *Baremetal* image (function code 6) at `0x60060000`. | Aligned |
| "Baochip maintains trusted public key, verifies B" (section 11) | The HoloDi public key is compiled into A. A verifies B. No Baochip key slot is involved. | Aligned |
| "Relationship between HoloDi keys and Baochip keys must be confirmed" (section 11) | Resolved: they are **two different keys**. The Baochip chain authenticates A with Ed25519 (boot1 manifest). A authenticates B, and B authenticates C, with the HoloDi Monocypher EdDSA key, which is the only scheme ArduPilot's bootloader can verify. | **Decided** |
| "Baochip starts, holds H743 in RESET" (sections 5, 6, 13) | boot1 runs *before* A and doesn't touch our pins. During that window only the external NRST pull (or the MOSFET) holds the H743 in reset. | **Hardware rule** |
| "Untrusted bootloader cannot execute", "Baochip outside the trust boundary" (Properties 2, 5) | These hold only if **A itself can't be replaced**. With developer-key signing, anyone with USB access who can press PROG can replace A. | **Bench only until production signing** |
| Pairing (section 12) | Any HoloDi secret held on the Baochip (pairing keys, session keys) must be derived from the README's `collateral` keys. Those exist only with a third-party boot1. | **Decided** |

## Decision 1: bench signing (now)

- A stays signed with the public developer key (`dabao-sdk/tools/sign_and_uf2.py`).
  The README says this is the default: "By default, the bao1x will accept and run
  developer images".
- Consequence: on first boot of A, boot1 sets the one-way `DEVELOPER_MODE` counter.
  It also erases the Baochip secrets, permanently. A doesn't use any Baochip secret,
  so this is harmless for secure boot. That Dabao is permanently a developer device.
- **Never run `lockdown` on a bench Dabao.** It revokes key slot 3 (the developer key)
  through one-way counters. After that, a developer-signed A never boots again, and
  this can't be undone.
- Bench boards don't meet PDF Properties 2 and 5 against someone holding the board.
  That is expected and accepted for M1–M5.

## Decision 2: production signing (path chosen)

**Chosen path: a third-party boot1** (README section "Conditions for Getting Signed
Third-Party `boot1`").

1. Build boot1 (`bao1x-boot`, `thirdparty` feature) with a key manifest that holds
   only HoloDi Ed25519 keys. Slots 0–2 hold production keys. Slot 3 is treated
   as a developer slot no matter how it's tagged. No Baochip key may appear in it.
2. Pass Baochip's acceptance tests: collateral initialised, `OEM_MODE` ≠ 0,
   `PK_RECEIPT` untouched, and the swap-and-revert test.
3. Baochip signs that boot1. HoloDi countersigns it with
   `fido-signer --countersign` (a key from slots 0–2).
4. HoloDi signs every release of A itself with a slot 0–2 key through `fido-signer`.
   No Baochip involvement after step 3.
5. Run `lockdown` (two-phase, type `YES`) at boot1 during factory provisioning.

Why this path and not the alternatives:

- **Baochip signs every A.** Rejected. B is embedded inside A, so every B release
  would need a Baochip signature.
- **Custom boot0 with HoloDi reference keys.** Rejected for now. The README puts the
  minimum order around 50,000 chips plus an engineering fee. Revisit only at that volume.
- **Developer key in production.** Rejected. Physical USB access plus PROG replaces A.

Residual risk you accept with this path: someone with physical access can use the
signed boot-updater to swap the stock Baochip boot1 back in. After `lockdown`, that
boot1 runs only images signed by Baochip's own keys (slots 0–2), never developer
images. The H743 also stays in reset unless an image actively drives PB2. Sector-0
WRP on the H743 (PDF section 10) is the second layer.

## Decision 3: anti-rollback on A

boot1 enforces a per-function-code anti-rollback counter. It rejects an image
whose `anti_rollback` field is below the counter and ratchets the counter up when
the field is higher. `sign_and_uf2.py` hard-codes `anti_rollback = 1`.

- Bench: leave it at 1.
- Production: every A release increments `anti_rollback`. A B update is always an
  A update, so an old A can't be reinstalled to bring back an old B or an old
  HoloDi key. The header that `fido-signer` signs must carry this value. That is
  part of the production signing work, and the current SDK tool can't do it.

## Decision 4: pins boot1 owns

The README reserves `PB13`/`PB14` as boot1's serial console at **1,000,000 baud 8N1**.
Firmware A uses the same UART2 pins for the H743 ROM-bootloader link at 115200 8E1.
**Keep this wiring.** Firmware A, the tests, and the bench are already built around it.
These rules come with it:

- The H743 must be held in reset by hardware from power-on (`ROT_NRST_INVERTED=1`
  MOSFET recommended). Otherwise boot1's 1 Mbaud banner goes into a running H743's USART1.
- When boot1 stays in its console (PROG held, bootwait enabled, or A fails its
  signature check) and USB isn't connected, it accepts commands on `PB13`. The H743's TX
  is high-impedance while it's in reset, so fit a 10 kΩ pull-up on `PB13` to stop
  noise from feeding the console.
- A log adapter on `PB14` sees boot1 output at 1,000,000 8N1, then A's output at
  115200 8E1.
- `PC13` (dabao) and `PF5` (baosec) are pulsed by boot1 before USB enumeration.
  None of our signals use them. Keep it that way.

## Decision 5: loading A and boot1 updates

- Loading A: either of the README's methods works. You can drag `holodi_rot_A.uf2`
  onto the `BAOCHIP` mass-storage drive and then run `bootwait disable` on the boot1
  console. Or you can run `serial_flash.py --persistent`, which does the same thing
  over USB serial. `bootwait` must end up **disabled**. Otherwise a Dabao waits in
  boot1 at power-on and A never runs, so the H743 stays in reset forever.
- To return to boot1 after that, hold PROG while applying power or pressing RESET.
- Updating boot1 (`boot-updater.uf2`, Xous ≥ v0.10.2) **overwrites A**. While no
  valid A exists, the H743 stays in reset (fail-closed). Reflash A straight after
  the update, or the updater runs again. Don't unplug during an update.
- Check state at any time with `audit` on the boot1 console. It reports the git
  revision, developer mode (`== IN DEVELOPER MODE ==`), and key status.

## Corrections to the PDF wording (no design change)

- Section 2: "Baochip firmware" is firmware A, a Baremetal image loaded by boot1.
- Sections 5, 6 and 13: during "Baochip starts", the reset hold is done by the board
  circuit until A runs. Firmware A takes it over before doing anything else.
- Section 15, Properties 2 and 5: these hold in production only after Decision 2 and `lockdown`.
- Section 16, M1: the UART link shares pins with boot1's console. See Decision 4.
