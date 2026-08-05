"""Is the system clock trustworthy enough to label a WR second?

`wr_time.py arm` reads the system clock to name the UTC second of the NEXT PPS
edge, and the PL then counts from there forever. A clock that is wrong at that
instant writes a whole-second error into every subsequent timestamp, and the
status check immediately afterwards CANNOT see it: it compares HW against the
same wrong system clock, so both agree and everything looks healthy. The error
only surfaces later, once the system clock is corrected.

That is not theoretical. This board has a dead RTC (`timedatectl` reports
`RTC time: Thu 1970-01-01`), so every boot starts ~58 days wrong until chrony
reaches the network and steps it:

    Aug 3 01:46:01  System clock was stepped by 5047494.677722 seconds
    Aug 3 12:31:19  System clock was stepped by 5086002.156533 seconds

Arming inside that window would label the timebase weeks off. A milder version of
the same class of failure has already put whole seconds into published events.

This module is pure text parsing so it is testable without hardware; wr_time.py
supplies the command output.
"""
import re
import subprocess

# A correct arm needs the clock right to well under half a second, since the error
# it produces is quantised to whole seconds. 100 ms leaves wide margin while still
# catching anything that could round to a different second.
MAX_ARM_OFFSET_S = 0.1


def timedatectl_synchronized(text):
    """True only on an explicit 'System clock synchronized: yes'."""
    m = re.search(r"System clock synchronized:\s*(\w+)", text or "")
    return bool(m) and m.group(1).lower() == "yes"


def parse_chrony_tracking(text):
    """`chronyc tracking` output -> {ref_id, stratum, system_time_s, leap}.

    `system_time_s` is signed: positive when the clock is FAST of NTP, negative
    when slow. Missing fields come back as None rather than a guess."""
    out = {"ref_id": None, "stratum": None, "system_time_s": None, "leap": None}
    if not text:
        return out
    m = re.search(r"Reference ID\s*:\s*(\S+)", text)
    if m:
        out["ref_id"] = m.group(1)
    m = re.search(r"Stratum\s*:\s*(\d+)", text)
    if m:
        out["stratum"] = int(m.group(1))
    m = re.search(r"System time\s*:\s*([0-9.]+)\s*seconds\s+(fast|slow)", text)
    if m:
        out["system_time_s"] = float(m.group(1)) * (1.0 if m.group(2) == "fast" else -1.0)
    m = re.search(r"Leap status\s*:\s*(.+)", text)
    if m:
        out["leap"] = m.group(1).strip()
    return out


def clock_is_trustworthy(timedatectl_text, chrony_text, max_offset_s=MAX_ARM_OFFSET_S):
    """(ok, reason). Every check must PASS affirmatively: absent or unparseable
    output blocks the arm rather than being read as healthy, because 'we could not
    tell' and 'it is fine' must never be the same answer here."""
    if not timedatectl_synchronized(timedatectl_text):
        return False, ("timedatectl does not report 'System clock synchronized: yes'; "
                       "the clock is not NTP-disciplined yet")

    t = parse_chrony_tracking(chrony_text)
    if t["ref_id"] is None:
        return False, ("no usable `chronyc tracking` output; cannot confirm the clock "
                       "is disciplined (is chrony installed and running?)")
    if t["ref_id"] in ("00000000", "0.0.0.0"):
        return False, ("chrony has no reference (ref_id 00000000): no selectable "
                       "source, so the clock is free-running")
    if t["leap"] and "not synchronis" in t["leap"].lower():
        return False, "chrony reports leap status 'Not synchronised'"
    if t["system_time_s"] is None:
        return False, "could not read the current offset from `chronyc tracking`"
    if abs(t["system_time_s"]) > max_offset_s:
        return False, ("system clock offset is %+.6f s from NTP (limit %.3f s); "
                       "arming now would label the WR second wrong"
                       % (t["system_time_s"], max_offset_s))

    return True, ("clock synchronized, chrony ref %s stratum %s, offset %+.6f s"
                  % (t["ref_id"], t["stratum"], t["system_time_s"]))


def _run(cmd):
    """Command stdout, or '' if the tool is missing or fails. Never raises: a
    missing tool must surface as a blocked arm with a clear reason, not a crash."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.stdout or ""
    except Exception:
        return ""


def check_system_clock(max_offset_s=MAX_ARM_OFFSET_S):
    """Read the live system state and judge it. Returns (ok, reason)."""
    return clock_is_trustworthy(_run(["timedatectl"]),
                                _run(["chronyc", "tracking"]),
                                max_offset_s)
