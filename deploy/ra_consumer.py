"""RedisAdapter Protocol v1.0 consumer helper (reference code).

Decodes the `{TCLK}` key space this board publishes into the lab's redis-clock-server
deployment, using ONLY the core RA protocol pieces: the {baseKey}:subKey schema, the
RA_Time stream ID (<ms>-<ns_within_ms>), and the raw little-endian `_` payload.

Four sub-keys per base key, all streams except the watchdog hash:

    {TCLK}:<HEX>      int64  LE, 8 B   time of that occurrence, ns since the Unix epoch
    {TCLK}:<HEX>_C    int64  LE, 8 B   occurrences of <HEX> since this producer started
    {TCLK}:STREAM     uint16 LE, 2 B   event id, one entry per event, the combined feed
    {TCLK}:watchdog   hash            field per producer, value = producer build version

<HEX> is the event code as exactly two uppercase zero-padded hex digits. Payload
widths are load-bearing: a consumer reading the wrong width gets a garbage value, so
the decoders here unpack a fixed size and raise rather than truncate."""
import struct

_I64 = "<q"                                  # <HEX> and <HEX>_C payloads
_U16 = "<H"                                  # STREAM payload
TIME_LEN = struct.calcsize(_I64)             # 8
COUNT_LEN = struct.calcsize(_I64)            # 8
EVENT_LEN = struct.calcsize(_U16)            # 2


def ts_key(base, event):
    """Per-code event-time stream, e.g. ("TCLK", 0x1D) -> {TCLK}:1D."""
    return "{%s}:%02X" % (base, event)


def count_key(base, event):
    """Per-code occurrence-counter stream, e.g. ("TCLK", 0x1D) -> {TCLK}:1D_C."""
    return "{%s}:%02X_C" % (base, event)


def stream_key(base):
    """The combined event feed, one entry per event in time order."""
    return "{%s}:STREAM" % base


def watchdog_key(base):
    """The liveness hash: one field per producer, short expiry, refreshed while alive."""
    return "{%s}:watchdog" % base


def ra_time_from_id(entry_id):
    """Redis Stream ID 'ms-seq' -> RA_Time (ns since Unix epoch)."""
    if isinstance(entry_id, (bytes, bytearray)):
        entry_id = entry_id.decode("ascii")
    ms, seq = entry_id.split("-")
    return int(ms) * 1_000_000 + int(seq)


def _payload(field_map):
    """The mandatory `_` field. Keys may be bytes (decode_responses=False, the correct
    setting for reading a binary payload) or str."""
    buf = field_map.get(b"_")
    if buf is None:
        buf = field_map.get("_")
    if buf is None:
        raise ValueError("stream entry has no `_` primary field")
    return bytes(buf)


def decode_int64(field_map):
    """`_` of a {TCLK}:<HEX> or {TCLK}:<HEX>_C entry -> int (ns since epoch, or count)."""
    return struct.unpack(_I64, _payload(field_map))[0]


def decode_event_id(field_map):
    """`_` of a {TCLK}:STREAM entry -> the event code."""
    return struct.unpack(_U16, _payload(field_map))[0]
