#!/usr/bin/env python3
"""Emit rot_payload.c: the HoloDi public key and signed B bundle for firmware A.

    gen_payload.py <bundle> <holodi_public_key.dat> <out.c>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "holodi"))
import boot_bundle  # noqa: E402


def c_array(data: bytes) -> str:
    rows = []
    for i in range(0, len(data), 16):
        rows.append("    " + ", ".join(f"0x{b:02x}" for b in data[i:i + 16]) + ",")
    return "\n".join(rows)


def main() -> int:
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    bundle = Path(sys.argv[1]).read_bytes()
    pub = boot_bundle.load_public_key(Path(sys.argv[2]))
    boot_bundle.unpack(bundle)
    out = Path(sys.argv[3])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        '#include "rot_payload.h"\n\n'
        f"const uint8_t HOLODI_PUBKEY[32] = {{\n{c_array(pub)}\n}};\n\n"
        f"const uint32_t B_BUNDLE_LEN = {len(bundle)}u;\n"
        f"const uint8_t B_BUNDLE[{len(bundle)}] = {{\n{c_array(bundle)}\n}};\n")
    print(f"wrote {out} (bundle {len(bundle)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
