#!/usr/bin/env bash
# Build firmware A (Baochip root of trust for the H743) and sign it for
# Baochip's boot1.
#
#   firmware/baochip_A/build.sh
#
# Inputs (override with env vars):
#   BUNDLE  signed bootloader-B bundle   (default holodi/build/B.bundle)
#   PUB     HoloDi public key             (default holodi/keys/holodi_public_key.dat)
#   ROT_NRST_INVERTED      0 = PB2 wired to NRST, 1 = PB2 drives an N-MOSFET
#   ROT_WRITE_PROTECT_B    1 = set WRP on H743 sector 0 after provisioning
#   ROT_CHECK_FULL_SECTOR  1 = require the unused tail of sector 0 erased
#
# Output: firmware/baochip_A/build/holodi_rot_A.uf2
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SDK="$ROOT/dabao-sdk"
PY="${PY:-$ROOT/.venv/bin/python}"
BUNDLE="${BUNDLE:-$ROOT/holodi/build/B.bundle}"
PUB="${PUB:-$ROOT/holodi/keys/holodi_public_key.dat}"
CROSS="${CROSS:-$SDK/xpack-riscv-none-elf-gcc-15.2.0-1/bin/riscv-none-elf-}"
MONO="$ROOT/ardupilot/libraries/AP_CheckFirmware"
OUT="$HERE/build"
NAME=holodi_rot_A

[[ -x "${CROSS}gcc" ]] || { echo "RISC-V toolchain not found at ${CROSS}gcc"; exit 1; }
[[ -f "$BUNDLE" ]] || { echo "no B bundle at $BUNDLE (run firmware/h743_B/build.sh)"; exit 1; }

echo "[A] refusing to embed an unauthentic B: verifying $BUNDLE"
"$PY" "$ROOT/holodi/boot_bundle.py" verify "$BUNDLE" --pub "$PUB"

mkdir -p "$OUT/obj"
"$PY" "$HERE/gen_payload.py" "$BUNDLE" "$PUB" "$OUT/rot_payload.c"

ARCH="-march=rv32imac_zicsr_zifencei -mabi=ilp32"
CFLAGS="-Os -ffreestanding -nostdlib -g -ffunction-sections -fdata-sections"
DEFS="-DROT_NRST_INVERTED=${ROT_NRST_INVERTED:-0}"
DEFS="$DEFS -DROT_WRITE_PROTECT_B=${ROT_WRITE_PROTECT_B:-1}"
DEFS="$DEFS -DROT_CHECK_FULL_SECTOR=${ROT_CHECK_FULL_SECTOR:-1}"

INC="-I$HERE -I$MONO"
for d in common/bao_base common/bao_stdlib bao1x/hardware_regs; do INC="$INC -I$SDK/src/$d/include"; done
for d in "$SDK"/src/bao1x/hardware_*/include; do INC="$INC -I$d"; done
INC="$INC -I$SDK/src/boards/include -I$SDK/src/sevs"

SDK_SRCS="bao1x/hardware_gpio/gpio.c bao1x/hardware_uart/uart.c
          common/bao_stdlib/stdio.c common/bao_stdlib/delay.c
          common/bao_stdlib/stdlib.c sevs/sevs_assert_target.c"

OBJS="$OUT/obj/crt0.o"
${CROSS}gcc $ARCH -g -c -o "$OUT/obj/crt0.o" "$SDK/src/runtime/crt0.S"
for f in $SDK_SRCS; do
  o="$OUT/obj/$(echo "$f" | tr '/' '_' | sed 's/\.c$/.o/')"
  ${CROSS}gcc $ARCH $CFLAGS $INC -c -o "$o" "$SDK/src/$f"
  OBJS="$OBJS $o"
done
${CROSS}gcc $ARCH $CFLAGS -I"$MONO" -x c -c -o "$OUT/obj/monocypher.o" "$MONO/monocypher.cpp"
OBJS="$OBJS $OUT/obj/monocypher.o"
for f in "$HERE"/rot.c "$HERE"/stm32_rom.c "$HERE"/port_dabao.c "$OUT"/rot_payload.c; do
  o="$OUT/obj/$(basename "${f%.c}").o"
  ${CROSS}gcc $ARCH $CFLAGS -Wall -Wextra -Werror $DEFS $INC -c -o "$o" "$f"
  OBJS="$OBJS $o"
done

${CROSS}gcc $ARCH -T "$SDK/bao1x.ld" -nostdlib -nostartfiles -Wl,--gc-sections \
  -o "$OUT/$NAME.elf" $OBJS -lgcc
${CROSS}objcopy -O binary "$OUT/$NAME.elf" "$OUT/$NAME.bin"
${CROSS}size "$OUT/$NAME.elf"

# Baochip boot1 only runs images carrying a valid Ed25519ph signature block.
"$PY" "$SDK/tools/sign_and_uf2.py" "$OUT/$NAME.bin" "$OUT/$NAME.uf2" 0x60060000 0xa7d76373
echo "[A] built $OUT/$NAME.uf2"
