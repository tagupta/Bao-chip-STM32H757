#!/usr/bin/env bash
# Run firmware A's policy + ROM-bootloader client on the host against an
# emulated H743, with the real signed B bundle and HoloDi public key.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
A="$(cd "$HERE/.." && pwd)"
ROOT="$(cd "$A/../.." && pwd)"
PY="${PY:-$ROOT/.venv/bin/python}"
BUNDLE="${BUNDLE:-$ROOT/holodi/build/B.bundle}"
PUB="${PUB:-$ROOT/holodi/keys/holodi_public_key.dat}"
STOCK="${STOCK:-$ROOT/ardupilot/Tools/bootloaders/KakuteH7_bl.bin}"
MONO="$ROOT/ardupilot/libraries/AP_CheckFirmware"
OUT="$A/build/host"
mkdir -p "$OUT"

"$PY" "$A/gen_payload.py" "$BUNDLE" "$PUB" "$OUT/rot_payload.c" >/dev/null
cc -O1 -g -Wall -Wextra -Werror -I"$A" -I"$MONO" -o "$OUT/host_test" \
   "$HERE/host_test.c" "$A/rot.c" "$A/stm32_rom.c" "$OUT/rot_payload.c" \
   -x c "$MONO/monocypher.cpp"
"$OUT/host_test" "$STOCK"
