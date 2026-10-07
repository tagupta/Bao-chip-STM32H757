#!/usr/bin/env python3
"""Baochip root-of-trust firmware - simulation stand-in.

This program takes the place that, on real silicon, will be occupied by a
Rust task running under Xous on the Baochip SoC. It owns:

    * the HoloDi public key (compiled into the Baochip image / stored in a
      write-locked region of Baochip's own flash)
    * the H743 bootloader bundle (bootloader bytes + Ed25519 signature,
      stored in Baochip-owned non-volatile memory)

It drives the Baochip -> H743 wire. In simulation that wire is a single TCP
socket exposed by :class:`AP_BaochipController` (see
``Tools/renode/peripherals/common/AP_BaochipController.cs``). On real
hardware the same opcodes travel over the SPI pins MOSI/MISO/SCLK/CS plus
an NRST GPIO that holds the H743 in reset until Baochip is satisfied.

Boot sequence (architecture sections 5, 6 and 13; same policy as firmware A
in ``firmware/baochip_A/rot.c``):

    1. Hold H743 in reset.                                   # RESET_HOLD
    2. Verify Baochip's bundle against HoloDi public key.    # local EdDSA
       - If BAD: never release reset (exit 2).
    3. Read H743 sector 0 and authenticate what is there.    # FLASH_READ
       - authentic B          -> go to 6 (normal boot, no rewrite)
       - blank, or --mode provision -> go to 4 (provisioning)
       - anything else        -> keep reset (exit 3, B_malicious)
    4. Erase sector 0 and write the verified B.              # FLASH_ERASE/WRITE
    5. Read back and authenticate again.                     # FLASH_READ
    6. Release reset. H743 fetches SP/PC from the vector     # RESET_RELEASE
       table at 0x08000000 and starts the trusted bootloader.

The bootloader then verifies the HoloDi-signed ArduCopter application and
transfers control to it, closing the chain of trust.
"""

from __future__ import annotations

import argparse
import socket
import struct
import sys
import time
from pathlib import Path

# Local module: bundle format + Ed25519 verify.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import boot_bundle  # noqa: E402
import holodi_eddsa  # noqa: E402

# H743 flash geometry
FLASH_BASE = 0x0800_0000
BOOTLOADER_SECTOR_SIZE = 128 * 1024  # STM32H743 sector 0 is 128 KiB

# Wire opcodes - must match AP_BaochipController.cs
OP_READ_ID = 0x9F
OP_FLASH_WRITE = 0x02
OP_FLASH_READ = 0x03
OP_FLASH_ERASE = 0xE4
OP_RESET_HOLD = 0xB1
OP_RESET_RELEASE = 0xB0

# How much data to put in one FLASH_WRITE frame. The frame length field is
# 16 bits, so this must stay well below 65535.
WRITE_CHUNK = 2048

RC_RELEASED = 0
RC_ERROR = 1
RC_BAD_BUNDLE = 2
RC_UNTRUSTED_B = 3


class VerifierError(RuntimeError):
    pass


class Wire:
    """One SPI chip-select per request, length-prefixed on top of TCP."""

    def __init__(self, host: str, port: int, connect_timeout: float = 30.0):
        self.host = host
        self.port = port
        self.sock = self._connect(connect_timeout)

    def _connect(self, timeout: float) -> socket.socket:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                s = socket.create_connection((self.host, self.port), timeout=5)
                s.settimeout(30)
                return s
            except OSError as error:
                last_error = error
                time.sleep(0.2)
        raise VerifierError(
            f"could not reach the Baochip controller at "
            f"{self.host}:{self.port}: {last_error}")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def _send(self, frame: bytes) -> bytes:
        if len(frame) > 0xFFFF:
            raise VerifierError(f"frame too large: {len(frame)}")
        header = struct.pack(">H", len(frame))
        self.sock.sendall(header + frame)
        reply_header = self._recv_exact(2)
        reply_len, = struct.unpack(">H", reply_header)
        reply = self._recv_exact(reply_len)
        if not reply:
            raise VerifierError("empty reply from H743")
        if reply[0] != 0x00:
            msg = reply[1:].decode("ascii", errors="replace")
            raise VerifierError(f"H743 controller returned error: {msg}")
        return reply[1:]

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise VerifierError("H743 controller closed the connection")
            buf += chunk
        return buf

    # --- high-level ops ---

    def read_id(self) -> bytes:
        return self._send(bytes([OP_READ_ID]))

    def flash_erase(self, addr: int, length: int) -> None:
        self._send(struct.pack(">BII", OP_FLASH_ERASE, addr, length))

    def flash_write(self, addr: int, data: bytes) -> None:
        for off in range(0, len(data), WRITE_CHUNK):
            chunk = data[off:off + WRITE_CHUNK]
            self._send(struct.pack(">BIH", OP_FLASH_WRITE,
                                   addr + off, len(chunk)) + chunk)

    def flash_read(self, addr: int, length: int) -> bytes:
        result = bytearray()
        for off in range(0, length, WRITE_CHUNK):
            chunk_len = min(WRITE_CHUNK, length - off)
            data = self._send(struct.pack(">BIH", OP_FLASH_READ,
                                          addr + off, chunk_len))
            if len(data) != chunk_len:
                raise VerifierError(
                    f"short FLASH_READ: asked {chunk_len}, got {len(data)}")
            result += data
        return bytes(result)

    def reset_hold(self) -> None:
        self._send(bytes([OP_RESET_HOLD]))

    def reset_release(self) -> None:
        self._send(bytes([OP_RESET_RELEASE]))


def _log(msg: str) -> None:
    print(f"[BAOCHIP] {msg}", flush=True)


def classify_sector(wire: Wire, bootloader_len: int, signature: bytes,
                    public_key: bytes) -> str:
    """Authenticate H743 sector 0: 'valid', 'blank' or 'invalid'."""
    data = wire.flash_read(FLASH_BASE, BOOTLOADER_SECTOR_SIZE)
    if data.count(0xFF) == len(data):
        return "blank"
    signed, tail = data[:bootloader_len], data[bootloader_len:]
    if tail.count(0xFF) != len(tail):
        return "invalid"
    if not holodi_eddsa.verify(signature, public_key, signed):
        return "invalid"
    return "valid"


def _refuse(hold_forever: bool, rc: int) -> int:
    if hold_forever:
        _log("holding reset indefinitely (ctrl-C to exit).")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    return rc


def run(bundle_path: Path, public_key_path: Path | None,
        host: str, port: int, hold_forever: bool = True,
        mode: str = "auto") -> int:
    """Perform the root-of-trust sequence.

    mode: 'auto' (provision only a blank sector), 'provision' (always
    reinstall B, the recovery strap on hardware) or 'boot' (never write).

    Returns RC_RELEASED, RC_BAD_BUNDLE or RC_UNTRUSTED_B; in both refusal
    cases the H743 remains halted. Connection errors raise.
    """
    public_key = boot_bundle.load_public_key(public_key_path)
    _log(f"loaded HoloDi public key ({len(public_key)} bytes)")

    bundle = bundle_path.read_bytes()
    _log(f"loaded H743 bootloader bundle: {bundle_path} ({len(bundle)} bytes)")

    wire = Wire(host, port)
    try:
        ident = wire.read_id()
        _log(f"linked to H743 controller: id={ident!r}")

        _log("asserting H743 reset")
        wire.reset_hold()

        _log("verifying bootloader signature against HoloDi public key...")
        if not boot_bundle.verify(bundle, public_key):
            _log("REFUSED: bootloader signature INVALID. "
                 "H743 will be held in reset.")
            return _refuse(hold_forever, RC_BAD_BUNDLE)

        bootloader_bytes, signature = boot_bundle.unpack(bundle)
        _log(f"signature OK ({len(bootloader_bytes)} bytes of trusted "
             f"bootloader)")

        if len(bootloader_bytes) > BOOTLOADER_SECTOR_SIZE:
            raise VerifierError(
                f"bootloader is {len(bootloader_bytes)} bytes, exceeds "
                f"sector 0 ({BOOTLOADER_SECTOR_SIZE} bytes)")

        state = classify_sector(wire, len(bootloader_bytes), signature,
                                public_key)
        _log(f"H743 sector 0 currently holds: {state} bootloader")

        if state == "valid" and mode != "provision":
            _log("installed bootloader authentic; no rewrite needed")
            _log("releasing H743 from reset -- trusted bootloader now runs")
            wire.reset_release()
            return RC_RELEASED

        if mode == "boot" or (state == "invalid" and mode != "provision"):
            _log("REFUSED: H743 sector 0 holds an unauthenticated "
                 "bootloader. H743 will be held in reset.")
            return _refuse(hold_forever, RC_UNTRUSTED_B)

        _log(f"erasing H743 flash bootloader sector "
             f"(0x{FLASH_BASE:08x}+0x{BOOTLOADER_SECTOR_SIZE:x})")
        wire.flash_erase(FLASH_BASE, BOOTLOADER_SECTOR_SIZE)

        _log(f"writing {len(bootloader_bytes)} bytes of bootloader to "
             f"0x{FLASH_BASE:08x}")
        wire.flash_write(FLASH_BASE, bootloader_bytes)

        _log("reading back for verification")
        if classify_sector(wire, len(bootloader_bytes), signature,
                           public_key) != "valid":
            raise VerifierError(
                "bootloader read-back failed authentication; "
                "refusing to release reset")
        _log("read-back authentic")

        _log("releasing H743 from reset -- trusted bootloader now runs")
        wire.reset_release()
        return RC_RELEASED
    finally:
        wire.close()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", type=Path, required=True,
                    help="H743 bootloader bundle produced by boot_bundle.py")
    ap.add_argument("--pub", type=Path, default=None,
                    help="HoloDi public key "
                         "(default: holodi/keys/holodi_public_key.dat)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5950)
    ap.add_argument("--no-hold", action="store_true",
                    help="exit immediately after a signature failure "
                         "rather than looping (useful in test harnesses)")
    ap.add_argument("--mode", choices=["auto", "provision", "boot"],
                    default="auto",
                    help="auto: install B only into a blank sector; "
                         "provision: always reinstall B; "
                         "boot: authenticate only, never write")
    args = ap.parse_args()
    try:
        return run(args.bundle, args.pub, args.host, args.port,
                   hold_forever=not args.no_hold, mode=args.mode)
    except VerifierError as error:
        _log(f"ERROR: {error}")
        return RC_ERROR


if __name__ == "__main__":
    sys.exit(main())
