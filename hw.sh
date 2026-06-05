#!/usr/bin/env bash
# hw.sh — hardware-build task wrapper for the Kria KR260 flow (Vivado).
# The hardware counterpart to sim.sh. (PowerShell users: use .\hw.ps1 instead.)
#
# Usage:
#   ./hw.sh build         # RTL -> bitstream via vivado/build.tcl (batch)
#   ./hw.sh gui           # open the generated project in the Vivado GUI
#   ./hw.sh clean         # delete vivado/build/
#
# Finds Vivado via (first that works): $VIVADO env var, then `vivado` on PATH.
# Loading the bitstream onto the board is intentionally NOT handled here — do
# that yourself (JTAG Hardware Manager, fpgautil, or xmutil loadapp).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_TCL="$ROOT/vivado/build.tcl"
BUILD_DIR="$ROOT/vivado/build"

resolve_vivado() {
    if [[ -n "${VIVADO:-}" && -x "$VIVADO" ]]; then echo "$VIVADO"; return; fi
    if command -v vivado >/dev/null 2>&1; then command -v vivado; return; fi
    cat >&2 <<'EOF'
Vivado not found. Install AMD Vivado (ML Standard Edition is free and supports
the KR260 / xck26), then make it resolvable in ONE of these ways:
    VIVADO=/tools/Xilinx/Vivado/2024.2/bin/vivado ./hw.sh build
    export VIVADO=/tools/Xilinx/Vivado/2024.2/bin/vivado
    # or add Vivado's bin/ to PATH
EOF
    exit 1
}

task="${1:-build}"
case "$task" in
    build)
        vivado="$(resolve_vivado)"
        echo "==> building bitstream with $vivado"
        "$vivado" -mode batch -source "$BUILD_TCL" -nojournal -log "$ROOT/vivado/build.log"
        echo "==> done. Bitstream is under vivado/build/uart_echo.runs/impl_1/"
        ;;
    gui)
        vivado="$(resolve_vivado)"
        xpr="$BUILD_DIR/uart_echo.xpr"
        [[ -f "$xpr" ]] || { echo "No project at $xpr - run: ./hw.sh build" >&2; exit 1; }
        echo "==> opening $xpr"
        "$vivado" "$xpr" &
        ;;
    clean)
        if [[ -d "$BUILD_DIR" ]]; then rm -rf "$BUILD_DIR"; echo "removed vivado/build/"; else echo "nothing to clean"; fi
        ;;
    *)
        echo "usage: ./hw.sh {build|gui|clean}" >&2; exit 1 ;;
esac
