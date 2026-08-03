"""Background Redis writer for the readout publisher.

A bounded in-process queue decouples the caller (the UIO drain thread) from Redis
latency: submit() never blocks; if the queue is full it drops the OLDEST record
(counted) so the hardware FIFO drain can never stall on a Redis hiccup. A writer
thread pops records in batches and pipelines every write the record carries:

    per write:   XADD <key> <guarded_ms>-<guarded_ns_in_ms> <fields> MAXLEN ~ <maxlen>

A record is {ra_time, writes: [(key, fields), ...]}: the publisher decides which keys
one event touches (see redis_publish.RecordBuilder, which emits the contract's
{TCLK}:<HEX>, {TCLK}:<HEX>_C and {TCLK}:STREAM writes), and the sink stays generic.
`published` counts RECORDS (events), not XADDs.

Stream IDs come from the event's RA_Time (ns since epoch), encoded as <ms>-<ns_in_ms>,
with a monotonic guard PER KEY at ns resolution so a backward WR re-arm jump (or two
events with an identical RA_Time) cannot make XADD error (Redis requires increasing
IDs). Per-key means the shared STREAM feed absorbs the bumps that its per-code streams
never see, matching the deployment's documented max(raw, last+1) policy.

Liveness is one field in a watchdog HASH: HSET the producer's build version, then
HEXPIRE that field with a short TTL, refreshed on a period that beats the TTL. Field
TTLs need redis-server >= 7.4 and redis-py >= 5.1; an older client is reported through
`last_error` rather than failing silently.

On any Redis error it counts the dropped batch, records `last_error`, reconnects with
backoff, continues.

Redis is reached through an injected `connect` factory (default: a real redis-py
client). redis-py is imported lazily inside that factory so this module imports cleanly
on a machine without redis-py and the unit tests run with a stub."""
import os
import queue
import threading
import time


def resolve_auth(username, password, environ=None):
    """Redis AUTH credentials: an explicit value wins, else the REDIS_USERNAME /
    REDIS_PASSWORD environment variables. Returns (username, password), each None when
    unset (an empty string is treated as unset). Reading the password from the env, not
    an argument, keeps it out of the process argv so it never shows up in `ps`."""
    environ = os.environ if environ is None else environ
    return (username or environ.get("REDIS_USERNAME") or None,
            password or environ.get("REDIS_PASSWORD") or None)


def _default_connect(host, port, username=None, password=None):
    import redis   # lazy: module imports without redis-py present (PC unit tests)
    return redis.Redis(host=host, port=port, username=username, password=password,
                       socket_connect_timeout=1.0, socket_timeout=1.0)


class RedisSink:
    def __init__(self, host="127.0.0.1", port=6379, maxlen=10_000,
                 queue_size=100_000, batch=1000, watchdog_key=None,
                 watchdog_field=None, watchdog_value=None,
                 watchdog_ttl=1, watchdog_period=0.9,
                 connect=None, username=None, password=None):
        self.maxlen = maxlen
        self.batch = batch
        self.watchdog_key = watchdog_key
        self.watchdog_field = watchdog_field
        self.watchdog_value = watchdog_value
        self.watchdog_ttl = watchdog_ttl
        self.watchdog_period = watchdog_period
        self._connect = connect or (lambda: _default_connect(host, port,
                                                             username=username,
                                                             password=password))
        self._q = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._last_ratime = {}                   # per-KEY monotonic RA_Time (ns) guard
        self._last_wd = 0.0                      # monotonic time of last watchdog refresh
        self.published = 0
        self.queue_dropped = 0
        self.redis_dropped = 0
        self.reconnects = 0
        self.last_error = None                   # most recent Redis failure, None when healthy

    # ---- producer side (drain thread) ----
    def submit(self, record):
        """Enqueue one event record. Never blocks: on a full queue drop the OLDEST
        record (counted), then enqueue this one. A record is {ra_time, writes}."""
        try:
            self._q.put_nowait(record)
            return
        except queue.Full:
            pass
        try:
            self._q.get_nowait()                 # drop oldest
            with self._lock:
                self.queue_dropped += 1
        except queue.Empty:
            pass
        try:
            self._q.put_nowait(record)
        except queue.Full:                       # racing producers; drop this one
            with self._lock:
                self.queue_dropped += 1

    # ---- consumer side (writer thread) ----
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, timeout=2.0):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def stats(self):
        with self._lock:
            return {"published": self.published, "queue_dropped": self.queue_dropped,
                    "redis_dropped": self.redis_dropped, "reconnects": self.reconnects,
                    "queued": self._q.qsize(), "last_error": self.last_error}

    def _note_error(self, exc):
        with self._lock:
            self.last_error = "%s: %s" % (type(exc).__name__, exc)

    def _drain_batch(self):
        batch = []
        for _ in range(self.batch):
            try:
                batch.append(self._q.get_nowait())
            except queue.Empty:
                break
        return batch

    def _guarded_id(self, key, ra):
        """RA_Time -> a stream ID strictly greater than this key's previous one."""
        last = self._last_ratime.get(key, 0)
        if ra <= last:                           # strictly increasing IDs: bump 1 ns
            ra = last + 1
        self._last_ratime[key] = ra
        ms, seq = divmod(ra, 1_000_000)          # RA_Time -> <ms>-<ns_within_ms>
        return "%d-%d" % (ms, seq)

    def _write_batch(self, client, batch):
        pipe = client.pipeline(transaction=False)
        for rec in batch:
            ra = rec["ra_time"]
            for key, fields in rec["writes"]:
                pipe.xadd(key, fields, id=self._guarded_id(key, ra),
                          maxlen=self.maxlen, approximate=True)
        pipe.execute()

    def _maybe_watchdog(self, client):
        """Refresh this producer's field in the watchdog hash every watchdog_period
        seconds: HSET the build version, then HEXPIRE the field so it disappears
        watchdog_ttl seconds after we stop refreshing. Raises on a Redis error (or on a
        redis-py too old for HEXPIRE) so the caller reconnects and records it."""
        if self.watchdog_key is None:
            return
        now = time.monotonic()
        if self._last_wd != 0.0 and (now - self._last_wd) < self.watchdog_period:
            return
        hexpire = getattr(client, "hexpire", None)
        if hexpire is None:
            raise RuntimeError(
                "this redis-py has no HEXPIRE, so the watchdog field cannot expire and "
                "liveness would read as alive forever; needs redis-py >= 5.1 "
                "(pip install -U redis) against redis-server >= 7.4")
        client.hset(self.watchdog_key, self.watchdog_field, self.watchdog_value)
        hexpire(self.watchdog_key, self.watchdog_ttl, self.watchdog_field)
        self._last_wd = now

    def _run(self):
        client = None
        while True:
            if self._stop.is_set() and self._q.empty():
                break
            if client is None:
                try:
                    client = self._connect()
                except Exception as e:
                    with self._lock:
                        self.reconnects += 1
                    self._note_error(e)
                    if self._stop.is_set():
                        break                    # stopping AND cannot connect: give up rest
                    time.sleep(0.5)
                    continue
            if not self._stop.is_set():
                try:
                    self._maybe_watchdog(client)
                except Exception as e:
                    with self._lock:
                        self.reconnects += 1
                    self._note_error(e)
                    client = None
                    time.sleep(0.5)          # back off on a Redis error (no busy-spin)
                    continue
            batch = self._drain_batch()
            if not batch:
                if self._stop.is_set():
                    break
                time.sleep(0.005)
                continue
            try:
                self._write_batch(client, batch)
                with self._lock:
                    self.published += len(batch)
                    self.last_error = None       # healthy again
            except Exception as e:
                with self._lock:
                    self.redis_dropped += len(batch)
                    self.reconnects += 1
                self._note_error(e)
                client = None                    # force reconnect next iteration
                if not self._stop.is_set():
                    time.sleep(0.5)          # back off on a Redis error (no busy-spin)
