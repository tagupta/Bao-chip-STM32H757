#!/usr/bin/env bash
# Build the HoloDi secure bootloader and HoloDi-signed ArduCopter for KakuteH7 in Docker.
# Outputs: holodi/build/KakuteH7_bl.{bin,elf}, holodi/build/arducopter.apj (unsigned, signed-fw layout)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
docker build -q -t holodi-ap-build "$ROOT/sim" >/dev/null
docker run --rm -v "$ROOT":/work holodi-ap-build bash -ec '
  Tools/scripts/build_bootloaders.py KakuteH7 \
      --signing-key=/work/holodi/keys/holodi_public_key.dat --omit-ardupilot-keys
  ./waf configure --board KakuteH7 --signed-fw
  ./waf copter
  mkdir -p /work/holodi/build
  cp Tools/bootloaders/KakuteH7_bl.bin Tools/bootloaders/KakuteH7_bl.elf build/KakuteH7/bin/arducopter.apj /work/holodi/build/
  git checkout -- Tools/bootloaders/   # keep the upstream bootloaders pristine
'
echo "built into holodi/build/"
