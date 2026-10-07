#!/usr/bin/env python3
"""Full chain-of-trust test: Baochip -> H743 bootloader -> H743 application.

This exercises every link of the chain described in ``requirements.md``:

    Power-on -> Baochip establishes trust ->
    Baochip verifies H743 bootloader (SPI, this harness) ->
    H743 executes trusted bootloader ->
    bootloader verifies application (HoloDi signature in Monocypher) ->
    H743 executes application (ArduCopter, confirmed via MAVLink heartbeat).

Cases (names follow the architecture document's milestones):

    genuine             M3: blank H743 sector 0. Baochip provisions B, which
                        runs signed C. Expectation: MAVLink heartbeat.
    normal-boot         Section 6: authentic B already in flash. Baochip
                        authenticates it without rewriting. Heartbeat.
    tampered-boot       Baochip's own copy of B altered after signing.
                        Baochip refuses; the CPU never executes; no MAVLink.
    malicious-B         M5: B_malicious already in H743 flash. Baochip
                        refuses; the CPU never executes; no MAVLink.
    tampered-app        M4: C altered after signing. Baochip accepts B, B
                        refuses C, PC stays inside the bootloader sector.
    unsigned-app        M4: official (non-HoloDi) ArduCopter. Same end
                        state as tampered-app from an outside observer.

Simulation model
----------------
In this simulator we represent Baochip as :mod:`baochip_verifier` (a Python
process) talking to the H743 Renode machine through a TCP wire exposed by
:class:`AP_BaochipController`. On real hardware the same opcodes travel over
MOSI/MISO/SCLK/CS and the NRST pin from Baochip. The chain of trust is
functionally identical.

Usage: .venv/bin/python holodi/req_full_chain.py [--only NAME] [--sign]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

from pymavlink import mavutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "holodi"))
import boot_bundle  # noqa: E402
import sign_apj  # noqa: E402

AP = ROOT / "ardupilot"
BUILD = ROOT / "holodi" / "build"
KEY = ROOT / "holodi" / "keys" / "holodi_private_key.dat"
PUB = ROOT / "holodi" / "keys" / "holodi_public_key.dat"
BOOTLOADER = BUILD / "B.bin"
MALICIOUS_BOOTLOADER = BUILD / "B_malicious.bin"
BUNDLE_GENUINE = BUILD / "B.bundle"
BUNDLE_TAMPERED = BUILD / "B.tampered.bundle"
SIGNED = BUILD / "C.apj"
TAMPERED_APP = BUILD / "C.tampered.apj"
PY = ROOT / ".venv" / "bin" / "python"
CTRL_CS = AP / "Tools/renode/peripherals/common/AP_BaochipController.cs"

UART_PORT = 5762
MONITOR_PORT = 1234
BAO_PORT = 5950
BOOTLOADER_END = 0x0802_0000   # STM32H743 sector 0 (bootloader) upper bound


# ---------------------------- image preparation ----------------------------

def sign_and_tamper_app() -> None:
    """Produce HoloDi-signed and byte-tampered ArduCopter APJs."""
    src = json.loads((BUILD / "arducopter.apj").read_text())
    d = sign_apj.sign(src, boot_bundle.load_private_key(KEY))
    SIGNED.write_text(json.dumps(d, indent=4))
    img = bytearray(zlib.decompress(base64.b64decode(d["image"])))
    at = img.find(b"ArduCopter V")
    if at < 0:
        sys.exit("version banner not found in image")
    img[at:at + 12] = b"EvilCopter V"
    d["image"] = base64.b64encode(zlib.compress(bytes(img), 9)).decode()
    TAMPERED_APP.write_text(json.dumps(d, indent=4))


def build_bundles() -> None:
    """Produce the genuine and tampered B bundles, and a raw B_malicious."""
    if not BOOTLOADER.exists():
        BOOTLOADER.write_bytes((BUILD / "KakuteH7_bl.bin").read_bytes())
    genuine = boot_bundle.build_bundle(BOOTLOADER,
                                       boot_bundle.load_private_key(KEY))
    BUNDLE_GENUINE.write_bytes(genuine)
    BUNDLE_TAMPERED.write_bytes(boot_bundle.tamper(genuine))
    malicious = bytearray(BOOTLOADER.read_bytes())
    malicious[128] ^= 0x01
    MALICIOUS_BOOTLOADER.write_bytes(bytes(malicious))


def official_firmware() -> str:
    """Return the path to the official (non-HoloDi) ArduCopter stable ELF."""
    out = subprocess.run(
        [str(PY), str(AP / "Tools/renode/renode_firmware.py"),
         "KakuteH7", "--vehicle", "Copter", "--channel", "stable"],
        check=True, capture_output=True, text=True,
    ).stdout
    return out.split()[0]


# ------------------------------ Renode monitor -----------------------------

def monitor_recv(sock: socket.socket, seconds: float) -> str:
    buf, end = b"", time.time() + seconds
    while time.time() < end:
        try:
            buf += sock.recv(4096)
        except socket.timeout:
            pass
    return buf.decode(errors="replace")


def monitor_cpu() -> tuple[int | None, int | None]:
    """Ask the Renode monitor for the H743 PC and executed-instruction count."""
    try:
        with socket.create_connection(("127.0.0.1", MONITOR_PORT), timeout=5) as s:
            s.settimeout(0.4)
            monitor_recv(s, 1.5)
            s.sendall(b"mach set 0\r\n")
            monitor_recv(s, 1.0)
            s.sendall(b"sysbus.cpu PC\r\n")
            text = monitor_recv(s, 2.0)
            m = re.search(r"sysbus\.cpu PC\s*\r?\n\s*0x([0-9A-Fa-f]+)", text)
            pc = int(m.group(1), 16) if m else None
            s.sendall(b"sysbus.cpu ExecutedInstructions\r\n")
            text = monitor_recv(s, 2.0)
            m = re.search(r"ExecutedInstructions\s*\r?\n\s*(0x[0-9A-Fa-f]+|\d+)",
                          text)
            executed = int(m.group(1), 0) if m else None
            return pc, executed
    except OSError:
        return None, None


# ----------------------------- the actual boot -----------------------------

def wait_for_port(port: int, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
            s.close()
            return True
        except OSError:
            time.sleep(0.3)
    return False


def any_port_live(ports: list[int]) -> bool:
    for port in ports:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.2)
            s.close()
            return True
        except OSError:
            continue
    return False


def kill_tree(proc: subprocess.Popen, state_dir: Path) -> None:
    """Stop run.py and Renode. run.py starts Renode in a new session so a
    killpg on run.py does not reach it; we match Renode by its unique
    --state-dir argument instead."""
    subprocess.run(["pkill", "-f", str(state_dir)], check=False)
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            subprocess.run(["pkill", "-9", "-f", str(state_dir)], check=False)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


def boot_case(name: str, bundle: Path, firmware_path: str, flash_bl: Path,
              erase_sector: bool, wait_s: int) -> dict:
    """Run one case end-to-end and return observed outcomes.

    flash_bl is what the H743 has in sector 0 at power-on (erased first if
    erase_sector); the Baochip controller halts the CPU before `start`.
    """
    state = BUILD / "state" / name
    shutil.rmtree(state, ignore_errors=True)
    state.mkdir(parents=True)
    # Belt-and-braces: a stale Renode from an aborted previous run would
    # happily answer our verifier on 5950 and give us a false pass. Kill
    # anything matching the state directory of ANY previous case first.
    subprocess.run(["pkill", "-9", "-f",
                    str(BUILD / "state")], check=False)
    for _ in range(20):
        if not any_port_live([BAO_PORT, UART_PORT, MONITOR_PORT]):
            break
        time.sleep(0.5)
    renode_log = open(state / "renode.log", "w")
    exec_desc = (
        'machine LoadPlatformDescriptionFromString '
        f'"baochipCtrl: Miscellaneous.AP_BaochipController @ none '
        f'{{ port: {BAO_PORT} }}"'
    )
    renode_cmd = [
        str(PY), str(AP / "Tools/renode/run.py"), "KakuteH7",
        "--vehicle", "Copter",
        "--elf", firmware_path,
        "--bootloader", str(flash_bl),
        *(["--erase-bootloader-sector"] if erase_sector else []),
        "--state-dir", str(state),
        "--serial", "2",
        "--port", str(MONITOR_PORT),
        "--uart-port", str(UART_PORT),
        "--renode", str(ROOT / "tools/Renode.app/Contents/MacOS/renode"),
        "--pre-start-exec", f"include @{CTRL_CS}",
        "--pre-start-exec", exec_desc,
    ]
    env = dict(os.environ,
               PATH=f"/opt/homebrew/opt/binutils/bin:{os.environ.get('PATH','')}",
               PYTHONWARNINGS="ignore")
    renode = subprocess.Popen(
        renode_cmd, env=env, cwd=str(AP),
        stdout=renode_log, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, start_new_session=True)
    verifier_log = state / "verifier.log"
    verifier_rc = None
    try:
        if not wait_for_port(BAO_PORT, timeout=60):
            return {"name": name, "error": "Renode never opened Baochip port",
                    "heartbeat": False, "pc": None, "verifier_rc": None,
                    "in_bootloader": False}

        with open(verifier_log, "w") as log:
            verifier = subprocess.Popen(
                [str(PY), str(ROOT / "holodi" / "baochip_verifier.py"),
                 "--bundle", str(bundle), "--pub", str(PUB),
                 "--host", "127.0.0.1", "--port", str(BAO_PORT),
                 "--no-hold"],
                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            verifier_rc = verifier.wait(timeout=60)

        heartbeat = False
        banner = None
        if verifier_rc == 0:
            # Trusted bootloader was installed and released. Give the H743
            # time to run the bootloader + the application and come up on
            # MAVLink.
            try:
                conn = mavutil.mavlink_connection(
                    f"tcp:localhost:{UART_PORT}",
                    source_system=255, retries=5)
                hb = conn.wait_heartbeat(timeout=max(10, wait_s))
                heartbeat = hb is not None
                if hb:
                    st = conn.recv_match(type="STATUSTEXT", blocking=True,
                                         timeout=5)
                    banner = st.text if st else None
            except OSError:
                heartbeat = False
        else:
            # Baochip refused; also give the clock a moment so we read
            # a representative PC rather than catching mid-fault handling.
            time.sleep(2)

        pc, executed = monitor_cpu()
        return {
            "name": name,
            "verifier_rc": verifier_rc,
            "rewrote_b": "erasing H743" in verifier_log.read_text(),
            "heartbeat": heartbeat,
            "banner": banner,
            "pc": pc,
            "executed": executed,
            "in_bootloader": pc is not None and pc < BOOTLOADER_END,
        }
    finally:
        kill_tree(renode, state)
        renode_log.close()
        # Give the kernel a moment to release the bound TCP ports before
        # the next case starts a fresh Renode.
        for _ in range(30):
            if not any_port_live([BAO_PORT, UART_PORT, MONITOR_PORT]):
                break
            time.sleep(0.5)


# ------------------------------- test driver -------------------------------

RC_BAD_BUNDLE = 2
RC_UNTRUSTED_B = 3

CASES = [
    # (name, bundle, firmware, sector-0 contents at power-on, erase it,
    #  should_boot, expected verifier rc, expect B to be rewritten)
    ("genuine",       BUNDLE_GENUINE,  str(SIGNED),       BOOTLOADER,           True,  True,  0,              True),
    ("normal-boot",   BUNDLE_GENUINE,  str(SIGNED),       BOOTLOADER,           False, True,  0,              False),
    ("tampered-boot", BUNDLE_TAMPERED, str(SIGNED),       BOOTLOADER,           True,  False, RC_BAD_BUNDLE,  False),
    ("malicious-B",   BUNDLE_GENUINE,  str(SIGNED),       MALICIOUS_BOOTLOADER, False, False, RC_UNTRUSTED_B, False),
    ("tampered-app",  BUNDLE_GENUINE,  str(TAMPERED_APP), BOOTLOADER,           True,  False, 0,              True),
    ("unsigned-app",  BUNDLE_GENUINE,  None,              BOOTLOADER,           True,  False, 0,              True),
]


def classify(result: dict, should_boot: bool, want_rc: int,
             want_rewrite: bool) -> tuple[bool, str]:
    rc = result.get("verifier_rc")
    hb = result["heartbeat"]
    pc = result["pc"]

    if rc != want_rc:
        return False, f"verifier rc={rc}, expected {want_rc}"

    # Expectations by layer of refusal:
    if want_rc != 0:
        # Baochip must refuse and the CPU must never have run an instruction.
        ok = (not hb) and result["executed"] == 0
        reason = ("Baochip refused; H743 executed 0 instructions" if ok else
                  f"H743 ran anyway (heartbeat={hb}, "
                  f"executed={result['executed']})")
        return ok, reason

    if not result["executed"]:
        return False, "Baochip released the H743 but it never executed"

    if result["rewrote_b"] != want_rewrite:
        return False, ("Baochip rewrote B on a normal boot" if
                       result["rewrote_b"] else
                       "Baochip did not provision a blank sector")

    if should_boot:
        ok = hb
        reason = ("MAVLink heartbeat observed" if ok else
                  "no MAVLink after Baochip released the H743")
        if ok and not want_rewrite:
            reason += " (installed B authenticated, not rewritten)"
        return ok, reason

    # Baochip released, but the application should NOT boot (the H743
    # bootloader must refuse). We expect no heartbeat and the PC to be
    # inside the bootloader sector.
    ok = (not hb) and (pc is not None and pc < BOOTLOADER_END)
    reason = ("H743 bootloader refused the application; "
              f"PC=0x{pc:08x}" if ok and pc is not None else
              f"unexpected: heartbeat={hb}, PC="
              f"{'0x%08x'%pc if pc is not None else 'unknown'}")
    return ok, reason


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=[c[0] for c in CASES],
                    help="run only one case")
    ap.add_argument("--sign", action="store_true",
                    help="(re)sign the firmware and (re)build the bundles")
    ap.add_argument("--wait", type=int, default=90,
                    help="seconds to wait for MAVLink per boot (default: 90)")
    args = ap.parse_args()

    if args.sign or not SIGNED.exists() or not TAMPERED_APP.exists():
        print("[harness] signing + tampering ArduCopter...", flush=True)
        sign_and_tamper_app()
    if (args.sign or not BUNDLE_GENUINE.exists() or
            not BUNDLE_TAMPERED.exists() or not MALICIOUS_BOOTLOADER.exists()):
        print("[harness] building H743 bootloader bundles...", flush=True)
        build_bundles()

    official = None
    if args.only in (None, "unsigned-app"):
        print("[harness] resolving official ArduCopter stable image...",
              flush=True)
        official = official_firmware()

    ok_all = True
    for (name, bundle, fw, flash_bl, erase, should_boot, want_rc,
         want_rewrite) in CASES:
        if args.only and name != args.only:
            continue
        firmware = fw if fw is not None else official
        print(f"\n== {name}: sector0={flash_bl.name}"
              f"{' (erased)' if erase else ''} app={Path(firmware).name}",
              flush=True)
        result = boot_case(name, bundle, firmware, flash_bl, erase, args.wait)
        if "error" in result:
            print(f"   SETUP ERROR: {result['error']}")
            ok_all = False
            continue
        ok, reason = classify(result, should_boot, want_rc, want_rewrite)
        pc = (f"0x{result['pc']:08x}" if result["pc"] is not None
              else "unknown")
        print(f"   verifier_rc={result['verifier_rc']}  "
              f"rewrote_B={'yes' if result['rewrote_b'] else 'no'}  "
              f"heartbeat={'yes' if result['heartbeat'] else 'no'}  "
              f"PC={pc}  executed={result['executed']}")
        print(f"   {'PASS' if ok else 'FAIL'}: {reason}")
        ok_all = ok_all and ok

    print("\nFull chain:", "PASS" if ok_all else "FAIL")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
