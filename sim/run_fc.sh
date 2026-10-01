#!/usr/bin/env bash
# Boot the emulated Kakute H7 (STM32H743) under Renode: real bootloader + real ArduCopter.
# MAVLink on the primary UART: tcp:localhost:5762
#
#   sim/run_fc.sh                 # stock bootloader + official ArduCopter stable ELF
#   FW=path/to/fw.elf BL=path/to/bl.bin sim/run_fc.sh [extra run.py args]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AP="$ROOT/ardupilot"
PY="$ROOT/.venv/bin/python"
export PATH="/opt/homebrew/opt/binutils/bin:$PATH"   # run.py needs an ARM-capable objcopy
export PYTHONWARNINGS=ignore

if [[ -z "${FW:-}" ]]; then
  FW="$("$PY" "$AP/Tools/renode/renode_firmware.py" KakuteH7 --vehicle Copter --channel stable | awk '{print $1}')"
fi
BL="${BL:-$AP/Tools/bootloaders/KakuteH7_bl.bin}"

cd "$AP"
exec "$PY" Tools/renode/run.py KakuteH7 --vehicle Copter \
  --elf "$FW" --bootloader "$BL" \
  --renode "$ROOT/tools/Renode.app/Contents/MacOS/renode" "$@"
