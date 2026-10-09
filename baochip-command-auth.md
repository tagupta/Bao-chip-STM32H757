# Baochip-signed commands on the Cube Orange+

After this guide, the Cube Orange+ obeys a command only if Holodi (the Baochip on the Dabao) signed it. You send commands from MAVProxy on the laptop, through the Dabao's USB cable. Holodi signs them and passes them to the Cube over the wires already between the two boards. The Cube checks each signature, and drops anything that doesn't verify.

- **No hardware changes.** No wire is added or moved, and nothing is flashed onto the Dabao.
- **The Cube gets one new firmware image**, signed by the Dabao the same way as in the secure-boot guide. Its bootloader isn't touched.
- **Start from the end of `baochip-secure-boot.md`.** The Cube must trust the chip's key, and Phase H of that guide must have passed.

---

## Before you start: four things to know

### 1. Where to run commands

Every `just ...` command runs in a terminal opened like this:

```bash
cd /Users/tanugupta/Work/bao_chip/fc-holodai
nix develop
```

Other commands, such as `openssl` and `system_profiler`, work in any terminal.

### 2. Plugging in the Dabao

- Plug in the USB-C cable and **press nothing**.
- Never hold **PROG** while plugging in. That's the flashing mode, which this guide doesn't use.
- If a `BAOCHIP` drive appears on the Mac anyway, press PROG once, briefly.
- "Replug" always means: unplug, wait 3 seconds, plug back in, press nothing.

### 3. Opening the Dabao console

The Dabao's USB port is its **console** (where you type `blob ...` commands) for 10 seconds after it boots. After that it becomes the **data link** that carries MAVLink to the laptop. To reach the console:

1. Run `just term <dabao>`. This opens a terminal window connected to the Dabao.
2. Press Enter. If you see `[console]`, you're in.
3. If you see nothing, stay in that same window. Take your hands off the keyboard for one second, type `+++`, and press Enter. The `+` characters may not show. You should then see `[console]`.
4. To leave the window, press `Ctrl-T`, then `q`.

Once you've used `+++`, the data link stays off until you replug the Dabao. Replugging also brings the console back for 10 seconds.

**Wait at least 15 seconds after plugging in the Dabao before starting MAVProxy on it.** Otherwise MAVProxy's bytes land in the console.

### 4. Port names

Plug in **one board at a time** and run `just ports` after each. Write the two names down:

| Board | Cable     | Looks like                | Called below |
| ----- | --------- | ------------------------- | ------------ |
| Dabao | USB-C     | `/dev/cu.usbmodemFMZTMM1` | `<dabao>`    |
| Cube  | micro-USB | `/dev/cu.usbmodem11401`   | `<cube>`     |

A name can change after a replug. If a command can't find a board, run `just ports` again.

### Check the wires (look only, don't change anything)

| Dabao pin | Goes to on the Cube (TELEM1) |
| --------- | ---------------------------- |
| PB13      | TX (pin 2)                   |
| PB14      | RX (pin 3)                   |
| GND       | GND (pin 6)                  |

If your wires go to other pins, stop and tell me which. The Dabao's settings can follow the wires; the wires don't need to move.

---

## Part 1. Set up Holodi's key and lanes

**Plugged in: the Dabao only.**

1. **Make a seed for Holodi's command key.** In any terminal:

   ```bash
   openssl rand -hex 32
   ```

   It prints 64 hex digits. Copy them, but don't save them in a file: anyone with this seed can sign commands as Holodi.

2. **Open the Dabao console**, as described in "Opening the Dabao console" above.

3. **Check that the firmware key is still there.** At `[console]`:

   ```text
   blob fwkey pubp
   ```

   You should see `fwkey public PUBLIC_KEYV1:...` and `fwkey hex ...`. The `PUBLIC_KEYV1:...` part should match `keys/holodi-chip_public_key.dat` (`cat keys/holodi-chip_public_key.dat` in `fc-holodai`).
   If it says `fwkey error: no firmware key`: stop. The Dabao has lost the key the Cube trusts. Go back to the secure-boot guide.

   (`blob status` doesn't show the firmware key yet. It only lists it once a seed is set, in step 4.)

4. **Give Holodi its command key and point it at your wires.** Type these one at a time:

   ```text
   blob seed <the 64 hex digits from step 1>
   blob mode duplex
   blob lane sign usb uart2
   blob lane verify uart2 usb
   blob uart2baud 921600
   ```

   `blob seed` prints a public key. **Write down its first 8 characters**; step 23 checks them.
   If a command says "install a key or a seed first": `blob seed` didn't take. Type it again.

   **Don't type** `blob preset holodi` (it uses pins that aren't wired here), `blob fwkey ...` or `blob unpair`.

5. **Leave the console:** `Ctrl-T`, then `q`.

6. **Replug the Dabao, and within 10 seconds check the firmware key:**

   ```bash
   just holodi-pubkey <dabao>
   ```

   You should see `unchanged, PUBLIC_KEYV1:...`.
   If it says `no answer`: you were too slow. Replug and run it straight away.
   If it says `holds another key`: stop. Go back to the secure-boot guide.

7. **Replug the Dabao, and within 10 seconds check the settings:**

   ```bash
   just term <dabao>
   ```

   ```text
   blob status
   ```

   You should see `sign Usb->Uart2, verify Uart2->Usb`, `uart2 baud: 921600`, `seed yes`, `fwkey ardupilot-blake2b` and `paired no`.
   `no key installed` is expected: that line is the unused AES key, not the firmware key. `to_fc_unpaired` above zero is also expected, because Holodi isn't paired yet.
   Leave with `Ctrl-T`, `q`.

---

## Part 2. Build the Cube firmware and have the Dabao sign it

**Plugged in: the Dabao only.** Close any terminal that's connected to it.

8. **Build the firmware.** This takes a few minutes, and the Dabao isn't used yet:

   ```bash
   just build-to-sign CubeOrangePlus 1
   ```

   The `1` switches Holodi support on.
   If it stops with a compile error: this is the first Holodi build for the Cube, so it may need a code fix. Save the output and send it to me. Don't keep retrying.

9. **Replug the Dabao, and within 10 seconds sign the firmware:**

   ```bash
   just holodi-sign CubeOrangePlus <dabao> holodi-chip 1
   ```

   You should see, at the end:

   ```text
   out/CubeOrangePlus-copter-holodi-signed-holodi-chip.apj: signed by Holodi's key <base64>; bootloader model: OK
   ...
   CubeOrangePlus-copter-holodi-signed-holodi-chip.apj: OK
   ```

   If it says `no answer`: the 10 seconds passed while it rechecked the build. Replug the Dabao and run the same command again; it'll be faster the second time.
   If it says `...secure-bl-holodi-chip.bin is missing` or `does not trust`: run Phase D of the secure-boot guide again.

---

## Part 3. Install the firmware on the Cube

**Plugged in: the Cube only.** Unplug the Dabao.

10. **Upload the firmware:**

    ```bash
    just upload out/CubeOrangePlus-copter-holodi-signed-holodi-chip.apj <cube>
    ```

    If it waits forever for the board: run `pkill -f mavproxy.py`, replug the Cube, and run it again.

11. **Check that the Cube booted it.** Wait 10 seconds, then in any terminal:

    ```bash
    system_profiler SPUSBDataType | grep -iE "cube|bootloader"
    ```

    You should see `CubeOrange+`.
    If you see `CubeOrange+-Secure-BL-v10`: the bootloader refused the image. Redo Part 2.

12. **Tell the Cube that TELEM1 is Holodi's port.** Connect MAVProxy:

    ```bash
    just mav <cube>
    ```

    At the `STABILIZE>` prompt:

    ```text
    param set SERIAL1_PROTOCOL 51
    param set SERIAL1_BAUD 921
    param set BRD_SER1_RTSCTS 0
    reboot
    ```

    `51` is the Holodi protocol, `921` matches the Dabao's 921600 baud, and `BRD_SER1_RTSCTS 0` turns off flow control on the RTS and CTS pins, which aren't wired.
    If `SERIAL1_PROTOCOL 51` is refused: the Cube isn't running the new firmware. Redo step 10.

13. **Check the Cube's Holodi status.** MAVProxy reconnects by itself after the reboot. Then:

    ```text
    ftp get @SYS/holodi.txt
    !cat holodi.txt
    ```

    You should see `paired no`, `port SERIAL1`, `fw_key ardupilot-blake2b`, and `fw_key_slots 1`.
    Leave MAVProxy with `exit`.

---

## Part 4. Pair Holodi and the Cube

**Plugged in: both boards.** Pairing is how the Cube learns Holodi's public key. You do it once.

14. **Open two terminals:**
    - **Terminal DABAO:** open the Dabao console (use `+++` if needed).
    - **Terminal CUBE:** `just mav <cube>`

15. **In CUBE**, ask for pairing at the next boot:

    ```text
    param set HOLODI_PAIR 1
    ```

16. **In DABAO**, open Holodi's side for 2 minutes:

    ```text
    blob pair
    ```

17. **In CUBE**, within those 2 minutes:

    ```text
    reboot
    ```

18. **Check that it worked.** A few seconds later, CUBE should show:

    ```text
    AP: Holodi: paired, UID <UID>
    ```

    In DABAO, type `blob status`. You should see `paired <UID>` with the same UID, and `pairing_completed 1`.

    If DABAO shows `pairing_requests 0`: nothing from the Cube reaches the Dabao. Check `SERIAL1_PROTOCOL 51` and `SERIAL1_BAUD 921` on the Cube, then look at the wire from the Cube's TX to PB13.
    If `pairing_requests` climbs but the Cube never says `paired`: look at the wire from PB14 to the Cube's RX.
    If more than 2 minutes passed: start again from step 15.

19. **Close both terminals** (`exit` in CUBE, `Ctrl-T` then `q` in DABAO), then **replug the Dabao** so its USB becomes the data link again.

---

## Part 5. Send commands through Holodi

**Plugged in: both boards.** Run only one MAVProxy at a time.

20. **Wait 15 seconds after replugging the Dabao**, then connect MAVProxy to the **Dabao**:

    ```bash
    just mav <dabao>
    ```

    You should see `Detected vehicle 1:1 on link 0`, and the parameter download finishing. Everything it sent went through Holodi and was signed.
    If nothing is detected: `exit`, replug the Dabao, wait 15 seconds, and try again.

21. **Read a parameter:**

    ```text
    param fetch MAV_SYSID
    ```

    You should see `MAV_SYSID = 1.0000000`.

22. **Change the flight mode and back:**

    ```text
    mode alt_hold
    mode stabilize
    ```

    You should see `Got COMMAND_ACK: DO_SET_MODE: ACCEPTED` twice, and the prompt change to `ALT_HOLD>` and back to `STABILIZE>`. **The Cube obeyed a command that Holodi signed.**
    If `alt_hold` is refused with a sensor or pre-arm message: that's ArduCopter, not Holodi. Use `mode acro` instead.

23. **Check the Cube's counters:**

    ```text
    ftp get @SYS/holodi.txt
    !cat holodi.txt
    ```

    You should see `paired <UID>`, `link_key` starting with the 8 characters from step 4, and `frames_in` above zero (frames from Holodi that the Cube verified).
    If you see `rejected bad-signature` and the modes didn't change: the Cube holds a different key. Redo Part 4.

24. Leave MAVProxy with `exit`.

---

## Part 6 (optional). Show that a different key is refused

This proves the Cube obeys only **this** Holodi's key. Afterwards you have to pair again (Part 4), so skip it if you don't want to today.

25. **Give Holodi a new key.** Run `openssl rand -hex 32` for a new seed. Open the Dabao console (use `+++`), then:

    ```text
    blob seed <the new 64 hex digits>
    ```

    It prints a **different** public key. Leave with `Ctrl-T`, `q`.

26. **Replug the Dabao, wait 15 seconds, and try a command:**

    ```bash
    just mav <dabao>
    ```

    ```text
    mode alt_hold
    ```

    You should still see the heartbeat, but **no** `ACCEPTED`, and the prompt stays `STABILIZE>`. The Cube refused it.
    `exit`.

27. **See the rejections on the Cube.** Connect to the Cube's own USB:

    ```bash
    just mav <cube>
    ```

    ```text
    ftp get @SYS/holodi.txt
    !cat holodi.txt
    ```

    You should see `rejected bad-signature` with a number above zero, and `link_key` still showing the **old** key's 8 characters.
    `exit`.

28. **Restore working commands:** repeat Part 4 (steps 14 to 19), then step 22 to confirm.

---

## Part 7. Lock the Cube's other ports

**Plugged in: both boards.** After this, the only way to command the Cube is through Holodi. **Read Part 8 first.**

29. **Through Holodi**, turn the lock on:

    ```bash
    just mav <dabao>
    ```

    ```text
    param set HOLODI_REQUIRE 1
    reboot
    ```

30. **Check that commands through Holodi still work.** After MAVProxy reconnects:

    ```text
    mode alt_hold
    mode stabilize
    ftp get @SYS/holodi.txt
    !cat holodi.txt
    ```

    You should see both mode changes `ACCEPTED`, and `require 1 rx_locked_channels` with a number of at least 1.
    `exit`.

31. **Check that the Cube's own USB is now ignored:**

    ```bash
    just mav <cube>
    ```

    ```text
    mode alt_hold
    ```

    You should still see the heartbeat, but **no** `ACCEPTED`, and the prompt stays `STABILIZE>`.
    `exit`.

    **Done:** the Cube now obeys only commands that Holodi has signed.

From now on, `reboot` over the Cube's USB is ignored too. To reboot the Cube, send `reboot` through `just mav <dabao>`, or unplug and replug the Cube.

---

## Part 8. Undoing it

### Turn the lock off (Holodi still works)

32. Wait 15 seconds after the Dabao was last plugged in, then:

    ```bash
    just mav <dabao>
    ```

    ```text
    param set HOLODI_REQUIRE 0
    reboot
    ```

    The Cube stays paired. To also remove the pairing, then send `param set HOLODI_PAIR 2` and `reboot`, and type `blob unpair` on the Dabao console.

### Recovery (Holodi no longer works and the lock is on)

This happens if Holodi's key changes without a new pairing, or if the Dabao is lost. The Cube then ignores every command. It can always be recovered, because its bootloader still accepts uploads.

33. **Plug in the Cube and upload the plain firmware**, the one without Holodi code:

    ```bash
    just upload out/CubeOrangePlus-copter-signed-holodi-chip.apj <cube>
    ```

    If it waits for the board, **unplug and replug the Cube's USB** while it waits. The upload then continues.

34. **Check the Cube takes commands again:** `just mav <cube>`, then `mode alt_hold`. You should see `ACCEPTED`.

35. **Before putting the Holodi firmware back**, clear the old lock and pairing. In `just mav <cube>`:

    ```text
    param set FORMAT_VERSION 0
    reboot
    ```

    **Warning:** this erases **every** Cube parameter, calibration included. It hasn't been tried on this Cube yet. After it, repeat from Part 3.

---

## What this setup doesn't protect

- **The link into Holodi.** Holodi signs whatever arrives on its USB. Here that's your laptop. On a drone it would be the radio.
- **The laptop saw the seed** in step 1. Fixing that needs a Dabao firmware update (`blob seed gen`).
- **Replay after a Cube reboot.** The Cube remembers recent frames only in RAM, so a recorded session could be played back after it restarts.
- **Telemetry authenticity.** The Cube does tag its telemetry, and Holodi checks it, but this guide doesn't test that.

## Afterwards

- Don't run `blob seed` again unless you plan to pair again (Part 4).
- Don't reflash the Dabao. The warnings from the secure-boot guide still apply.
- To re-sign the Cube firmware after a code change, repeat Part 2 with only the Dabao plugged in.
