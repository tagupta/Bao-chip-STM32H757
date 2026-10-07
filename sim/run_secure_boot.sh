#!/usr/bin/env bash
# Boot the H743 under the full Baochip -> bootloader -> application chain
# of trust. This is a one-shot interactive demo:
#
#   1. starts Renode with the CPU halted before its first instruction and
#      AP_BaochipController listening on tcp:localhost:$BAO_PORT;
#   2. runs the Baochip verifier (holodi/baochip_verifier.py), the host
#      stand-in for firmware A (firmware/baochip_A): it checks the HoloDi
#      signature over its bundle, authenticates what H743 sector 0 holds,
#      installs B only if the sector is blank, and releases NRST only if
#      the B in flash is authentic.
#
# After the verifier returns, the H743 is executing a Baochip-approved
# bootloader. The bootloader itself (also HoloDi-keyed) then validates and
# runs the ArduCopter application. MAVLink is exposed on tcp:localhost:5762.
#
# For automated per-case testing, use holodi/req_full_chain.py instead.
#
# Usage:
#   sim/run_secure_boot.sh                          # first boot: blank sector 0
#   FLASH_STATE=installed sim/run_secure_boot.sh    # normal boot: B already there
#   FLASH_BL=holodi/build/B_malicious.bin FLASH_STATE=installed \
#       sim/run_secure_boot.sh                      # M5: B_malicious in flash
#   BUNDLE=path/to/bundle sim/run_secure_boot.sh    # custom bundle
#
# Environment:
#   BAO_PORT       TCP port for the Baochip<->H743 wire (default 5950)
#   MONITOR_PORT   Renode monitor port                  (default 1234)
#   UART_PORT      MAVLink UART port                    (default 5762)
#   STATE_DIR      Renode state directory
#   BUNDLE         Signed H743 bootloader bundle (B.bundle)
#   FLASH_BL       What H743 sector 0 holds at power-on (default B.bin)
#   FLASH_STATE    blank (erase sector 0 first) or installed
#   MODE           verifier mode: auto, provision or boot (default auto)
#   FW             H743 application (C.apj)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AP="$ROOT/ardupilot"
PY="$ROOT/.venv/bin/python"
export PATH="/opt/homebrew/opt/binutils/bin:$PATH"
export PYTHONWARNINGS=ignore

BAO_PORT="${BAO_PORT:-5950}"
MONITOR_PORT="${MONITOR_PORT:-1234}"
UART_PORT="${UART_PORT:-5762}"
STATE_DIR="${STATE_DIR:-$ROOT/holodi/build/state/secure_boot}"
BUNDLE="${BUNDLE:-$ROOT/holodi/build/B.bundle}"
FLASH_BL="${FLASH_BL:-$ROOT/holodi/build/B.bin}"
FLASH_STATE="${FLASH_STATE:-blank}"
MODE="${MODE:-auto}"
FW="${FW:-$ROOT/holodi/build/C.apj}"
PUB_KEY="${PUB_KEY:-$ROOT/holodi/keys/holodi_public_key.dat}"
CTRL_CS="$AP/Tools/renode/peripherals/common/AP_BaochipController.cs"

if [[ ! -f "$BUNDLE" || ! -f "$FW" ]]; then
  echo "[secure_boot] missing $BUNDLE or $FW: run firmware/h743_B/build.sh --no-build"
  exit 1
fi
ERASE=()
[[ "$FLASH_STATE" == blank ]] && ERASE=(--erase-bootloader-sector)

rm -rf "$STATE_DIR"
mkdir -p "$STATE_DIR"

RENODE_LOG="$STATE_DIR/renode.log"
echo "[secure_boot] Renode log -> $RENODE_LOG"
(
  cd "$AP"
  "$PY" Tools/renode/run.py KakuteH7 --vehicle Copter \
    --elf "$FW" --bootloader "$FLASH_BL" ${ERASE[@]+"${ERASE[@]}"} \
    --state-dir "$STATE_DIR" --serial 2 --port "$MONITOR_PORT" \
    --uart-port "$UART_PORT" \
    --renode "$ROOT/tools/Renode.app/Contents/MacOS/renode" \
    --pre-start-exec "include @$CTRL_CS" \
    --pre-start-exec "machine LoadPlatformDescriptionFromString \"baochipCtrl: Miscellaneous.AP_BaochipController @ none { port: $BAO_PORT }\""
) >"$RENODE_LOG" 2>&1 &
RENODE_PID=$!
trap 'kill -TERM $RENODE_PID 2>/dev/null || true; wait $RENODE_PID 2>/dev/null || true' EXIT INT TERM

echo "[secure_boot] waiting for Renode to come up on :$BAO_PORT ..."
for _ in $(seq 1 60); do
  if "$PY" -c "import socket; s=socket.socket(); s.settimeout(0.3); s.connect(('127.0.0.1', $BAO_PORT))" 2>/dev/null; then
    break
  fi
  sleep 0.5
done

echo "[secure_boot] starting Baochip verifier"
if "$PY" "$ROOT/holodi/baochip_verifier.py" \
    --bundle "$BUNDLE" --pub "$PUB_KEY" --mode "$MODE" \
    --host 127.0.0.1 --port "$BAO_PORT" --no-hold; then
  echo "[secure_boot] verifier accepted. MAVLink on tcp:localhost:$UART_PORT"
  echo "[secure_boot] Ctrl-C to stop Renode."
else
  rc=$?
  echo "[secure_boot] verifier returned $rc (H743 remains halted)."
fi
wait $RENODE_PID
