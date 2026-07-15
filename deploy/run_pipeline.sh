#!/usr/bin/env bash
# Launch the KR260 TCLK+ACLK Redis publishers in a detached tmux session, each writing a
# JSONL stats log for the later error-check. Pre-flight refuses to launch unless Redis is
# reachable AND the WR timebase is fully locked, because an unlocked timebase stamps every
# event UNSYNC and the publisher would drop them all (a wasted day-long run).
#
# Run as root (the publishers mmap /dev/uio*):
#     sudo ./run_pipeline.sh [TCLK_UIO] [ACLK_UIO] [WR_UIO]
# Defaults: /dev/uio4 (tclk)  /dev/uio5 (aclk)  /dev/uio6 (wr).
# Match indices with:  grep . /sys/class/uio/uio*/name
# Override the WR-lock refusal with FORCE=1 (e.g. deliberately capturing UNSYNC).
set -euo pipefail

TCLK_DEV="${1:-/dev/uio4}"
ACLK_DEV="${2:-/dev/uio5}"
WR_DEV="${3:-/dev/uio6}"
SESSION="kr260"
FORCE="${FORCE:-0}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# --- pre-flight: Redis ---
if ! redis-cli ping 2>/dev/null | grep -q PONG; then
    echo "!! redis-cli ping did not return PONG. Is redis-server running?" >&2
    exit 1
fi

# --- pre-flight: WR timebase locked ---
WRSTATUS="$(python3 "$HERE/wr_time.py" "$WR_DEV" status || true)"
echo "$WRSTATUS"
if ! echo "$WRSTATUS" | grep -q "locked_tclk=1" || ! echo "$WRSTATUS" | grep -q "locked_aclk=1"; then
    echo "!! WR timebase is not fully locked (need locked_tclk=1 and locked_aclk=1)." >&2
    echo "   Arm it first:  sudo python3 wr_time.py $WR_DEV arm   (see wr.md)" >&2
    if [ "$FORCE" != "1" ]; then
        echo "   Refusing to launch (every event would be UNSYNC-dropped)." >&2
        echo "   Re-run with FORCE=1 to override." >&2
        exit 1
    fi
    echo "   FORCE=1 set: launching anyway." >&2
fi

# --- already running? ---
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "!! tmux session '$SESSION' already exists. Attach: sudo tmux attach -t $SESSION" >&2
    exit 1
fi

# --- launch (exec bash keeps each window open after Ctrl-C so final stats stay visible) ---
tmux new-session -d -s "$SESSION" -n tclk \
    "cd '$HERE' && python3 redis_publish.py $TCLK_DEV --src tclk --statlog stats-tclk.jsonl; exec bash"
tmux new-window -t "$SESSION" -n aclk \
    "cd '$HERE' && python3 redis_publish.py $ACLK_DEV --src aclk --statlog stats-aclk.jsonl; exec bash"

echo "# launched tmux session '$SESSION' (windows: tclk, aclk)."
echo "#   attach : sudo tmux attach -t $SESSION      (detach with Ctrl-b d)"
echo "#   stop   : sudo tmux send-keys -t $SESSION:tclk C-c ; sudo tmux send-keys -t $SESSION:aclk C-c"
echo "#            (Ctrl-C makes each publisher write its FINAL snapshot), then:"
echo "#            sudo tmux kill-session -t $SESSION"
echo "#   report : sudo python3 stats_report.py stats-tclk.jsonl stats-aclk.jsonl"
