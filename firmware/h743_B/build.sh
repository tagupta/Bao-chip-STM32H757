#!/usr/bin/env bash
# Build and sign the H743 secure bootloader B and the flight firmware C.
#
#   firmware/h743_B/build.sh            # build in Docker, then sign
#   firmware/h743_B/build.sh --no-build # sign the existing holodi/build outputs
#
# B is the ArduPilot bootloader for KakuteH7 with exactly one trusted public
# key compiled in (HoloDi's; --omit-ardupilot-keys drops ArduPilot's own), so
# it will only jump to firmware carrying a valid HoloDi signature.
#
# Outputs (holodi/build/):
#   B.bin    bootloader B, raw image for H743 sector 0 (0x08000000)
#   B.bundle B + HoloDi signature: what firmware A verifies and installs
#   C.apj    ArduCopter signed with the HoloDi key: what B verifies
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${PY:-$ROOT/.venv/bin/python}"
KEY="${KEY:-$ROOT/holodi/keys/holodi_private_key.dat}"
PUB="${PUB:-$ROOT/holodi/keys/holodi_public_key.dat}"
OUT="$ROOT/holodi/build"

if [[ "${1:-}" != "--no-build" ]]; then
  "$ROOT/sim/build_secure.sh"
fi
[[ -f "$OUT/KakuteH7_bl.bin" && -f "$OUT/arducopter.apj" ]] || {
  echo "missing $OUT/KakuteH7_bl.bin or arducopter.apj; run without --no-build"; exit 1; }

cp "$OUT/KakuteH7_bl.bin" "$OUT/B.bin"

"$PY" - "$OUT/B.bin" "$PUB" "$ROOT/ardupilot/Tools/scripts/signing/ArduPilotKeys" <<'EOF'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[2]).resolve().parents[1]))
import boot_bundle
b = Path(sys.argv[1]).read_bytes()
if boot_bundle.load_public_key(Path(sys.argv[2])) not in b:
    sys.exit("B does not contain the HoloDi public key: refusing to sign it")
for k in Path(sys.argv[3]).glob("*.dat"):
    if boot_bundle.load_public_key(k) in b:
        sys.exit(f"B still trusts ArduPilot key {k.name}: rebuild with --omit-ardupilot-keys")
print("B trusts the HoloDi key only")
EOF

"$PY" "$ROOT/holodi/boot_bundle.py" sign "$OUT/B.bin" "$OUT/B.bundle" --key "$KEY"
"$PY" "$ROOT/holodi/boot_bundle.py" verify "$OUT/B.bundle" --pub "$PUB"

"$PY" "$ROOT/holodi/sign_apj.py" sign "$OUT/arducopter.apj" "$OUT/C.apj" --key "$KEY"
"$PY" "$ROOT/holodi/sign_apj.py" verify "$OUT/C.apj" --pub "$PUB"

echo "B: $OUT/B.bundle   C: $OUT/C.apj"
