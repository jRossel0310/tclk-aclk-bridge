#!/usr/bin/env bash
# hw.sh — hardware-build task wrapper for the Kria KR260 flow (Vivado).
# The hardware counterpart to sim.sh, kept at parity with hw.ps1.
# (PowerShell users: use .\hw.ps1 instead.)
#
# Usage:
#   ./hw.sh build         # RTL -> bitstream via vivado/build.tcl (batch)
#   ./hw.sh gui           # open the generated project in the Vivado GUI
#   ./hw.sh clean         # delete the build dir
#
# Finds Vivado via (first that works): $VIVADO env var, then `vivado` on PATH.
# Loading the bitstream onto the board is intentionally NOT handled here — do
# that yourself (JTAG Hardware Manager, fpgautil, or xmutil loadapp).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_TCL="$ROOT/vivado/build.tcl"

# Repo-local default to match hw.ps1 (./build/kria/<name>); override with
# $KRIA_BUILD_DIR. NOTE: Vivado's IP Integrator breaks on spaces in the project
# path (this repo may live under "Summer 2026"); the build runs from a space-free
# parent, but set $KRIA_BUILD_DIR to a space-free dir if you still hit it.
# hw.ps1 is the full-featured wrapper (also does bootgen packaging + deploy).
BUILD_DIR_NATIVE="${KRIA_BUILD_DIR:-$ROOT/build/kria/uart_echo}"
PARENT_NATIVE="$(dirname "$BUILD_DIR_NATIVE")"

# Under git-bash/MSYS on Windows, Vivado is a Windows .exe and needs Windows-style
# paths; cygpath converts. On Linux there's no cygpath and paths pass through.
to_win() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }

export KRIA_BUILD_DIR="$(to_win "$BUILD_DIR_NATIVE")"
BUILD_TCL_ARG="$(to_win "$BUILD_TCL")"
LOG_NATIVE="$PARENT_NATIVE/build.log"
LOG_ARG="$(to_win "$LOG_NATIVE")"

resolve_vivado() {
    if [[ -n "${VIVADO:-}" && -x "$VIVADO" ]]; then echo "$VIVADO"; return; fi
    if command -v vivado >/dev/null 2>&1; then command -v vivado; return; fi
    cat >&2 <<'EOF'
Vivado not found. Install AMD Vivado (ML Standard Edition is free and supports
the KR260 / xck26), then make it resolvable in ONE of these ways:
    VIVADO=/c/Xilinx/Vivado/2024.2/bin/vivado ./hw.sh build
    export VIVADO=/c/Xilinx/Vivado/2024.2/bin/vivado
    # or add Vivado's bin/ to PATH
EOF
    exit 1
}

# Vivado's batch IP-Integrator rule init intermittently fails to read its own .tcl
# files (antivirus scanning Vivado's many small scripts mid-load). It fails fast,
# before synthesis, so retry ONLY that flake — never a real synth/impl failure.
BD_FLAKE_RE="couldn't read file|create_bd_design' failed|Error in initialization of Rule object|Failed to load customization data|Failed to load feature|bd::utils::"

task="${1:-build}"
case "$task" in
    build)
        vivado="$(resolve_vivado)"
        mkdir -p "$PARENT_NATIVE"
        max=6
        for ((attempt=1; attempt<=max; attempt++)); do
            echo "==> building bitstream with $vivado (attempt $attempt/$max)"
            echo "    output dir: $KRIA_BUILD_DIR"
            # Run from a space-free cwd; IP Integrator also chokes on spaces in cwd.
            ( cd "$PARENT_NATIVE" && "$vivado" -mode batch -source "$BUILD_TCL_ARG" -nojournal -log "$LOG_ARG" )
            rc=$?
            if [[ $rc -eq 0 ]]; then
                echo "==> done. Bitstream: $BUILD_DIR_NATIVE/uart_echo.runs/impl_1/uart_echo_bd_wrapper.bit"
                exit 0
            fi
            if [[ $attempt -lt $max ]] && grep -qE "$BD_FLAKE_RE" "$LOG_NATIVE" 2>/dev/null; then
                echo "==> block-design init flaked (Vivado batch IPI bug); retrying..."
                continue
            fi
            echo "Vivado build failed (exit $rc) - see $LOG_NATIVE" >&2
            exit "$rc"
        done
        ;;
    gui)
        vivado="$(resolve_vivado)"
        xpr_native="$BUILD_DIR_NATIVE/uart_echo.xpr"
        [[ -f "$xpr_native" ]] || { echo "No project at $xpr_native - run: ./hw.sh build" >&2; exit 1; }
        echo "==> opening $xpr_native"
        "$vivado" "$(to_win "$xpr_native")" &
        ;;
    clean)
        if [[ -d "$BUILD_DIR_NATIVE" ]]; then rm -rf "$BUILD_DIR_NATIVE"; echo "removed $BUILD_DIR_NATIVE"; else echo "nothing to clean"; fi
        ;;
    *)
        echo "usage: ./hw.sh {build|gui|clean}" >&2; exit 1 ;;
esac
