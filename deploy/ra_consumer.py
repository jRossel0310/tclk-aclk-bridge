"""RedisAdapter Protocol v1.0 consumer helper (reference code).

Decodes KR260 event-stream entries using ONLY the three core RA protocol pieces:
the {baseKey}:subKey key schema, the RA_Time stream ID (<ms>-<ns_within_ms>), and
the `_` binary primary payload. It deliberately ignores the human-readable extra
fields, so it demonstrates exactly what a generic RedisAdapter consumer needs to
read our primary data. See docs/superpowers/specs/2026-07-19-redisadapter-protocol-alignment-design.md."""
import struct

_STRUCT = "<IIIHB"                          # sec u32, ns u32, data u32, event u16, flags u8
PAYLOAD_LEN = struct.calcsize(_STRUCT)      # 15


def stream_key(namespace, src):
    """The braced RA key for one event source, e.g. ("KR260","tclk") -> {KR260}:tclk."""
    return "{%s}:%s" % (namespace, src)


def ra_time_from_id(entry_id):
    """Redis Stream ID 'ms-seq' -> RA_Time (ns since Unix epoch)."""
    if isinstance(entry_id, (bytes, bytearray)):
        entry_id = entry_id.decode("ascii")
    ms, seq = entry_id.split("-")
    return int(ms) * 1_000_000 + int(seq)


def decode_payload(buf):
    """Unpack the `_` field bytes -> {sec, ns, data, event, is_tclk, has_data}."""
    sec, ns, data, event, flags = struct.unpack(_STRUCT, bytes(buf))
    return {"sec": sec, "ns": ns, "data": data, "event": event,
            "is_tclk": (flags >> 1) & 1, "has_data": flags & 1}


def decode_entry(entry_id, field_map):
    """One RA stream entry (id, field map) -> the primary value dict, with `ra_time`
    added from the stream ID. `field_map` keys may be bytes (decode_responses=False,
    the correct setting for reading a binary `_`) or str. Reads only `_`."""
    payload = field_map.get(b"_")
    if payload is None:
        payload = field_map.get("_")
    if payload is None:
        raise ValueError("entry %r has no `_` primary field" % (entry_id,))
    out = decode_payload(payload)
    out["ra_time"] = ra_time_from_id(entry_id)
    return out
