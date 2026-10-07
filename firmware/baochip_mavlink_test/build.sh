#!/usr/bin/env bash
# Build the Dabao -> Cube Orange+ MAVLink relay test and sign it for boot1.
#
#   firmware/baochip_mavlink_test/build.sh            # LOITER (mode 5)
#   MODE=2 firmware/baochip_mavlink_test/build.sh     # ALT_HOLD, works without GPS
#
# Output: firmware/baochip_mavlink_test/build/mavlink_test.uf2
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SDK="$ROOT/dabao-sdk"
PY="${PY:-$ROOT/.venv/bin/python}"
CROSS="${CROSS:-$SDK/xpack-riscv-none-elf-gcc-15.2.0-1/bin/riscv-none-elf-}"
OUT="$HERE/build"
NAME=mavlink_test

[[ -x "${CROSS}gcc" ]] || { echo "RISC-V toolchain not found at ${CROSS}gcc"; exit 1; }
mkdir -p "$OUT/obj"

ARCH="-march=rv32imac_zicsr_zifencei -mabi=ilp32"
CFLAGS="-Os -ffreestanding -nostdlib -g -ffunction-sections -fdata-sections"
DEFS="-DMAVLINK_TEST_MODE=${MODE:-5}u"

INC="-I$HERE"
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
${CROSS}gcc $ARCH $CFLAGS -Wall -Wextra -Werror $DEFS $INC -c -o "$OUT/obj/$NAME.o" "$HERE/$NAME.c"
OBJS="$OBJS $OUT/obj/$NAME.o"

${CROSS}gcc $ARCH -T "$SDK/bao1x.ld" -nostdlib -nostartfiles -Wl,--gc-sections \
  -o "$OUT/$NAME.elf" $OBJS -lgcc
${CROSS}objcopy -O binary "$OUT/$NAME.elf" "$OUT/$NAME.bin"
${CROSS}size "$OUT/$NAME.elf"

"$PY" "$SDK/tools/sign_and_uf2.py" "$OUT/$NAME.bin" "$OUT/$NAME.uf2" 0x60060000 0xa7d76373
echo "[mavlink_test] built $OUT/$NAME.uf2 (mode ${MODE:-5})"
