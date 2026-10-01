#!/usr/bin/env python3
"""Requirement 1 - a tampered firmware update must not boot.

Runs the real HoloDi-keyed secure bootloader and real ArduCopter on an emulated
Kakute H7 (STM32H743) under Renode, once per image:

  genuine   ArduCopter built with --signed-fw, signed with HoloDi's private key
  tampered  the genuine image with its version banner edited after signing
  unsigned  the official ArduCopter release (not signed with HoloDi's key)

Pass criteria: genuine -> ArduPilot answers MAVLink and the CPU runs the app;
tampered/unsigned -> no MAVLink and the CPU never leaves the bootloader sector.

Usage: .venv/bin/python holodi/req1_secure_boot.py [--sign] [--only NAME]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import zlib
from pathlib import Path

from pymavlink import mavutil

ROOT = Path(__file__).resolve().parents[1]
AP = ROOT / "ardupilot"
BUILD = ROOT / "holodi" / "build"
KEY = ROOT / "holodi" / "keys" / "holodi_private_key.dat"
BOOTLOADER = BUILD / "KakuteH7_bl.bin"
SIGNED = BUILD / "arducopter-holodi-signed.apj"
TAMPERED = BUILD / "arducopter-holodi-tampered.apj"
PY = ROOT / ".venv" / "bin" / "python"

UART_PORT = 5762
MONITOR_PORT = 1234
BOOTLOADER_END = 0x0802_0000  # sector 0 (128 KiB) holds the bootloader on H743


def sign_and_tamper() -> None:
    src = BUILD / "arducopter.apj"  # from sim/build_secure.sh
    SIGNED.write_text(src.read_text())
    subprocess.run([str(PY), str(AP / "Tools/scripts/signing/make_secure_fw.py"), str(SIGNED), str(KEY)], check=True)

    d = json.loads(SIGNED.read_text())
    img = bytearray(zlib.decompress(base64.b64decode(d["image"])))
    at = img.find(b"ArduCopter V")
    if at < 0:
        sys.exit("version banner not found in image")
    img[at : at + 12] = b"EvilCopter V"
    d["image"] = base64.b64encode(zlib.compress(bytes(img), 9)).decode()
    TAMPERED.write_text(json.dumps(d, indent=4))
    print(f"signed:   {SIGNED.relative_to(ROOT)}\ntampered: {TAMPERED.relative_to(ROOT)} (banner patched after signing)")


def official_firmware() -> str:
    out = subprocess.run(
        [str(PY), str(AP / "Tools/renode/renode_firmware.py"), "KakuteH7", "--vehicle", "Copter", "--channel", "stable"],
        check=True, capture_output=True, text=True,
    ).stdout
    return out.split()[0]


def monitor_pc() -> int | None:
    """Ask the Renode monitor (telnet) where the CPU is executing."""

    def drain(s: socket.socket, seconds: float) -> str:
        buf, end = b"", time.time() + seconds
        while time.time() < end:
            try:
                buf += s.recv(4096)
            except socket.timeout:
                pass
        return buf.decode(errors="replace")

    try:
        with socket.create_connection(("localhost", MONITOR_PORT), timeout=5) as s:
            s.settimeout(0.5)
            drain(s, 2)
            s.sendall(b"mach set 0\r\n")
            drain(s, 2)
            s.sendall(b"sysbus.cpu PC\r\n")
            m = re.search(r"sysbus\.cpu PC\s+0x([0-9A-Fa-f]+)", drain(s, 3))
            return int(m.group(1), 16) if m else None
    except OSError:
        return None


def boot(name: str, firmware: str, wait_s: int) -> dict:
    state = BUILD / "state" / name
    shutil.rmtree(state, ignore_errors=True)  # fresh flash/param storage per run
    state.mkdir(parents=True)
    env = dict(os.environ, FW=firmware, BL=str(BOOTLOADER))
    log = open(BUILD / "state" / f"{name}.log", "w")
    proc = subprocess.Popen(
        [str(ROOT / "sim" / "run_fc.sh"), "--serial", "2", "--port", str(MONITOR_PORT), "--state-dir", str(state)],
        env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        hb = version = None
        deadline = time.time() + wait_s
        conn = None
        while time.time() < deadline and conn is None:
            try:
                conn = mavutil.mavlink_connection(f"tcp:localhost:{UART_PORT}", source_system=255, retries=0)
            except OSError:
                time.sleep(1)
        if conn is not None:
            hb = conn.wait_heartbeat(timeout=max(1, deadline - time.time()))
            if hb:
                st = conn.recv_match(type="STATUSTEXT", blocking=True, timeout=5)
                version = st.text if st else None
        pc = monitor_pc()
        return {"name": name, "heartbeat": hb is not None, "pc": pc, "banner": version,
                "in_bootloader": pc is not None and pc < BOOTLOADER_END}
    finally:
        # run.py starts Renode outside our process group; match it by its state dir.
        subprocess.run(["pkill", "-f", str(state)], check=False)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            subprocess.run(["pkill", "-9", "-f", str(state)], check=False)
        log.close()
        time.sleep(2)  # let the TCP ports close before the next boot


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sign", action="store_true", help="(re)sign the built firmware and regenerate the tampered copy")
    ap.add_argument("--only", choices=["genuine", "tampered", "unsigned"])
    ap.add_argument("--wait", type=int, default=90, help="seconds to wait for MAVLink per boot")
    args = ap.parse_args()

    if args.sign or not SIGNED.exists() or not TAMPERED.exists():
        sign_and_tamper()

    cases = [("tampered", str(TAMPERED), False), ("genuine", str(SIGNED), True), ("unsigned", official_firmware(), False)]
    ok_all = True
    for name, fw, should_boot in cases:
        if args.only and name != args.only:
            continue
        print(f"\n== {name}: booting under Renode with the HoloDi secure bootloader ...", flush=True)
        r = boot(name, fw, args.wait)
        pc = f"0x{r['pc']:08x}" if r["pc"] is not None else "unknown"
        booted = r["heartbeat"] and not r["in_bootloader"]
        state = "firmware accepted: ArduPilot is running" if booted else "firmware refused: the board stays in its bootloader"
        print(f"   {state}  (MAVLink={'yes' if r['heartbeat'] else 'no'}, PC={pc}{', ' + r['banner'] if r['banner'] else ''})")
        ok = booted == should_boot and (should_boot or r["in_bootloader"])
        ok_all &= ok
        print(f"   {'PASS' if ok else 'FAIL'}: expected {'boot' if should_boot else 'refusal'}")
    print("\nRequirement 1:", "PASS" if ok_all else "FAIL")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
