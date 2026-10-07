#!/usr/bin/env python3
"""Sign (or verify) an ArduPilot APJ with the HoloDi key: firmware C.

Byte-compatible with ``ardupilot/Tools/scripts/signing/make_secure_fw.py``
(same descriptor layout, same Monocypher-3 signature), but uses
:mod:`holodi_eddsa` so it does not need the pymonocypher C extension.

The H743 secure bootloader B checks this signature over the image with the
app descriptor cut out (``flash1 + flash2``) before it will jump to C.

    sign_apj.py sign   in.apj out.apj [--key holodi_private_key.dat]
    sign_apj.py verify signed.apj     [--pub holodi_public_key.dat]
"""

from __future__ import annotations

import argparse
import base64
import json
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import boot_bundle  # noqa: E402
import holodi_eddsa  # noqa: E402

DESCRIPTOR = b"\x41\xa3\xe5\xf2\x65\x69\x92\x07"
DESC_LEN = 92
SIG_VERSION = 30437
SIG_LEN = 64


def _image(apj: dict) -> bytes:
    return zlib.decompress(base64.b64decode(apj["image"]))


def _split(img: bytes) -> tuple[int, bytes]:
    offset = img.find(DESCRIPTOR)
    if offset < 0:
        raise ValueError("no signed APP_DESCRIPTOR; build C with --signed-fw")
    offset += len(DESCRIPTOR)
    return offset, img[:offset] + img[offset + DESC_LEN:]


def sign(apj: dict, secret_key: bytes) -> dict:
    img = _image(apj)
    offset, signed_region = _split(img)
    signature = holodi_eddsa.sign(secret_key, signed_region)
    desc = struct.pack("<IQ64s", SIG_LEN + 8, SIG_VERSION, signature)
    out = img[:offset + 16] + desc + img[offset + DESC_LEN:]
    if len(out) != len(img):
        raise RuntimeError("image length changed while signing")
    signed = dict(apj)
    signed["image"] = base64.b64encode(zlib.compress(out, 9)).decode()
    signed["signed_firmware"] = True
    return signed


def verify(apj: dict, pk: bytes) -> bool:
    img = _image(apj)
    offset, signed_region = _split(img)
    sig_len, version, signature = struct.unpack_from("<IQ64s", img, offset + 16)
    if sig_len != SIG_LEN + 8 or version != SIG_VERSION:
        return False
    return holodi_eddsa.verify(signature, pk, signed_region)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sign")
    s.add_argument("apj", type=Path)
    s.add_argument("output", type=Path)
    s.add_argument("--key", type=Path, default=None)
    v = sub.add_parser("verify")
    v.add_argument("apj", type=Path)
    v.add_argument("--pub", type=Path, default=None)
    args = ap.parse_args()

    apj = json.loads(args.apj.read_text())
    if args.cmd == "sign":
        signed = sign(apj, boot_bundle.load_private_key(args.key))
        args.output.write_text(json.dumps(signed, indent=4))
        print(f"wrote {args.output}")
        return 0
    ok = verify(apj, boot_bundle.load_public_key(args.pub))
    print("OK" if ok else "BAD")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
