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
# Publish to a different Redis (a lab RedisAdapter server) with REDIS_HOST / REDIS_PORT,
# default 127.0.0.1:6379. The pre-flight ping and the archiver follow the same target:
#     sudo env REDIS_HOST=redis.example.fnal.gov bash run_pipeline.sh
# Use `sudo env VAR=...` (not `export VAR=...; sudo ...`): sudo resets the environment,
# so the exported form is SILENTLY ignored and you would publish to the local Redis.
# The launch banner prints the target that was actually used; check it.
set -euo pipefail

TCLK_DEV="${1:-/dev/uio4}"
ACLK_DEV="${2:-/dev/uio5}"
WR_DEV="${3:-/dev/uio6}"
SESSION="kr260"
FORCE="${FORCE:-0}"
DROP="${DROP-07}"      # PL drop-mask codes (hex, comma-separated). Default: 0x07, the 720 Hz
                      # flood. DROP="" (set-but-empty) keeps every code; note ${DROP-07} not
                      # ${DROP:-07}, so an explicit empty value is honored. The mask register
                      # PERSISTS across launches (clears only on PL reload): launching with
                      # DROP="" does not un-drop codes a previous launch set; clear the bit
                      # explicitly (io.wr(FILTER_CFG, code) with bit8=0) or reload the PL.
ARCHIVE="${ARCHIVE-1}"  # 1 = also run stream_archive.py (daily CSVs of every published
                        # event, ~260 MB/day/source; needed for supercycle analysis of
                        # runs longer than the ~2.8 h Redis stream retention).
                        # Set ARCHIVE="" to disable.
REDIS_HOST="${REDIS_HOST:-127.0.0.1}"   # publish target (publishers + archiver + pre-flight).
REDIS_PORT="${REDIS_PORT:-6379}"        # A remote server must bind externally and allow this
                                        # host; no auth is configured, so use a trusted link.
HERE="$(cd "$(dirname "$0")" && pwd)"

# --- pre-flight: Redis ---
if ! redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping 2>/dev/null | grep -q PONG; then
    echo "!! redis-cli -h $REDIS_HOST -p $REDIS_PORT ping did not return PONG." >&2
    echo "   Is redis-server running and reachable from this host?" >&2
    echo "   A remote server also needs 'bind' set externally and protected-mode off." >&2
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
# Publishers run under `until` so a CRASH (nonzero exit) restarts them after 5 s -- on
# restart the drop-mask is re-applied and the stats log appends a new run. A clean
# Ctrl-C exits 0 and ends the loop, so the manual stop flow is unchanged.
# The wr window runs `wr_time.py guard`: the STRICT timebase unlocks permanently on any
# WR reference blip (or ACLK GT relock) and every event stamps UNSYNC until re-armed;
# the guard auto-re-arms so a blip costs seconds, not the rest of a multi-day run.
tmux new-session -d -s "$SESSION" -n tclk \
    "cd '$HERE' && until python3 redis_publish.py $TCLK_DEV --src tclk --drop '$DROP' --statlog stats-tclk.jsonl --redis-host '$REDIS_HOST' --redis-port '$REDIS_PORT'; do echo '# publisher exited nonzero; restarting in 5 s'; sleep 5; done; exec bash"
tmux new-window -t "$SESSION" -n aclk \
    "cd '$HERE' && until python3 redis_publish.py $ACLK_DEV --src aclk --drop '$DROP' --statlog stats-aclk.jsonl --redis-host '$REDIS_HOST' --redis-port '$REDIS_PORT'; do echo '# publisher exited nonzero; restarting in 5 s'; sleep 5; done; exec bash"
# The guard and archiver restart UNCONDITIONALLY (even after a clean Ctrl-C):
# a stray Ctrl-C in one of these panes once silently ended archiving for hours.
# Their intended stop is killing the session, which is the normal stop flow.
tmux new-window -t "$SESSION" -n wr \
    "cd '$HERE' && while true; do python3 wr_time.py $WR_DEV guard; echo '# wr guard exited; restarting in 5 s'; sleep 5; done"
if [ -n "$ARCHIVE" ]; then
    tmux new-window -t "$SESSION" -n archive \
        "cd '$HERE' && while true; do nice -n 10 python3 stream_archive.py --redis-host '$REDIS_HOST' --redis-port '$REDIS_PORT'; echo '# archiver exited; restarting in 5 s'; sleep 5; done"
fi

echo "# publishing to Redis $REDIS_HOST:$REDIS_PORT"
echo "# launched tmux session '$SESSION' (windows: tclk, aclk, wr guard${ARCHIVE:+, archive})."
echo "#   attach : sudo tmux attach -t $SESSION      (detach with Ctrl-b d)"
echo "#   stop   : sudo tmux send-keys -t $SESSION:tclk C-c ; sudo tmux send-keys -t $SESSION:aclk C-c"
echo "#            (Ctrl-C makes each publisher write its FINAL snapshot), then:"
echo "#            sudo tmux kill-session -t $SESSION"
echo "#   report : sudo python3 stats_report.py stats-tclk.jsonl stats-aclk.jsonl"
