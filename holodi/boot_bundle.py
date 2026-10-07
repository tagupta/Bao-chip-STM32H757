#!/usr/bin/env python3
"""Build the H743 bootloader bundle that Baochip verifies and installs.

The bundle is what in a real system would sit in Baochip-owned storage (its
own flash, or a shared SPI NOR dedicated to the root of trust): the H743
bootloader binary, together with an Ed25519 signature produced with the
HoloDi private key. Baochip (sim: :mod:`baochip_verifier`, hardware: a Rust
task on Xous) checks the signature against the HoloDi public key compiled
into its own image before it will write the bootloader into H743 flash.

Layout (little-endian):
    magic           "BAOBOOT1"      8 bytes
    bootloader_len  uint32          4 bytes
    signature       Ed25519         64 bytes
    bootloader      raw bytes       bootloader_len bytes

Only `bootloader` participates in the signature. `magic` and `bootloader_len`
are plain framing so that Baochip can walk the bundle without having to be
told its layout out of band.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import holodi_eddsa  # noqa: E402

MAGIC = b"BAOBOOT1"
HEADER = struct.Struct("<8sI64s")  # magic, bootloader_len, signature
ROOT = Path(__file__).resolve().parents[1]
KEYS = ROOT / "holodi" / "keys"


def _decode_key(kind: str, text: str) -> bytes:
    tag = f"{kind}_KEYV1:"
    if not text.startswith(tag):
        sys.exit(f"not a HoloDi {kind} key: wrong tag")
    import base64
    return base64.b64decode(text[len(tag):])


def load_private_key(path: Path | None = None) -> bytes:
    path = path or (KEYS / "holodi_private_key.dat")
    return _decode_key("PRIVATE", path.read_text().strip())


def load_public_key(path: Path | None = None) -> bytes:
    path = path or (KEYS / "holodi_public_key.dat")
    return _decode_key("PUBLIC", path.read_text().strip())


def pack(bootloader: bytes, signature: bytes) -> bytes:
    """Wrap raw bytes + signature in the bundle framing."""
    if len(signature) != 64:
        raise ValueError(f"signature must be 64 bytes (got {len(signature)})")
    return HEADER.pack(MAGIC, len(bootloader), signature) + bootloader


def unpack(bundle: bytes) -> tuple[bytes, bytes]:
    """Return (bootloader_bytes, signature)."""
    if len(bundle) < HEADER.size:
        raise ValueError("bundle too small for header")
    magic, length, signature = HEADER.unpack_from(bundle, 0)
    if magic != MAGIC:
        raise ValueError(f"bad bundle magic {magic!r}")
    expected = HEADER.size + length
    if len(bundle) != expected:
        raise ValueError(
            f"bundle length {len(bundle)} does not match header ({expected})")
    bootloader = bundle[HEADER.size:HEADER.size + length]
    return bootloader, signature


def sign_bytes(bootloader: bytes,
               private_key: bytes | None = None) -> bytes:
    """Produce an Ed25519 signature over the bootloader bytes."""
    key = private_key if private_key is not None else load_private_key()
    return holodi_eddsa.sign(key, bootloader)


def verify(bundle: bytes, public_key: bytes | None = None) -> bool:
    """Return True iff the bundle's signature matches the public key."""
    pk = public_key if public_key is not None else load_public_key()
    bootloader, signature = unpack(bundle)
    return holodi_eddsa.verify(signature, pk, bootloader)


def build_bundle(bootloader_path: Path,
                 private_key: bytes | None = None) -> bytes:
    data = bootloader_path.read_bytes()
    return pack(data, sign_bytes(data, private_key))


def tamper(bundle: bytes, offset: int = 128) -> bytes:
    """Flip one bit in the bootloader bytes without touching the signature."""
    bootloader, signature = unpack(bundle)
    if not bootloader:
        raise ValueError("empty bootloader")
    offset = min(offset, len(bootloader) - 1)
    patched = bytearray(bootloader)
    patched[offset] ^= 0x01
    return pack(bytes(patched), signature)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sign", help="sign a bootloader into a bundle")
    s.add_argument("bootloader", type=Path)
    s.add_argument("output", type=Path)
    s.add_argument("--key", type=Path, default=None,
                   help="private key (default: holodi/keys/holodi_private_key.dat)")
    v = sub.add_parser("verify", help="verify a bundle")
    v.add_argument("bundle", type=Path)
    v.add_argument("--pub", type=Path, default=None)
    t = sub.add_parser("tamper", help="flip a byte in the bootloader of a bundle")
    t.add_argument("bundle", type=Path)
    t.add_argument("output", type=Path)
    t.add_argument("--offset", type=int, default=128)
    args = ap.parse_args()

    if args.cmd == "sign":
        key = load_private_key(args.key) if args.key else None
        bundle = build_bundle(args.bootloader, key)
        args.output.write_bytes(bundle)
        print(f"wrote {args.output} ({len(bundle)} bytes, "
              f"{len(bundle) - HEADER.size} bytes of bootloader)")
        return 0
    if args.cmd == "verify":
        pk = load_public_key(args.pub) if args.pub else None
        ok = verify(args.bundle.read_bytes(), pk)
        print("OK" if ok else "BAD")
        return 0 if ok else 1
    if args.cmd == "tamper":
        patched = tamper(args.bundle.read_bytes(), args.offset)
        args.output.write_bytes(patched)
        print(f"wrote {args.output} (byte {args.offset} flipped)")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
