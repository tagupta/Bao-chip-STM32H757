#!/usr/bin/env bash
# Boot Xous for the Dabao board on the Baochip Verilator model.
# This is `verilate.sh -t xous`: xtask bao1x-sim, which selects board-dabao.
# It is not the bare-metal `iron` / nto-tests target.
#
# The simulation prints the debug UART and does not exit on its own. Ctrl-C to stop.
# First run compiles the SoC model; later runs reuse it. Expect a few minutes to boot.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export RUSTUP_TOOLCHAIN=1.98.1
unset CARGO_TARGET_DIR
export PATH="$ROOT/.venv/bin:$ROOT/dabao-sdk/xpack-riscv-none-elf-gcc-15.2.0-1/bin:$ROOT/sim/bin:$HOME/.cargo/bin:$PATH"
export PYTHONPATH="$ROOT/baochip-1x/deps/litex:$ROOT/baochip-1x/deps/migen"
cd "$ROOT/baochip-1x/verilate"
exec ./verilate.sh -t xous -s fast "$@"
