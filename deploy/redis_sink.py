"""Background Redis writer for the readout publisher.

A bounded in-process queue decouples the caller (the UIO drain thread) from Redis
latency: submit() never blocks; if the queue is full it drops the OLDEST record
(counted) so the hardware FIFO drain can never stall on a Redis hiccup. A writer
thread pops records in batches and, per record, pipelines:
  XADD <stream> <guarded_ms>-* <fields> MAXLEN ~ <maxlen>
  HSET <index_key> <index_fields>
  HINCRBY <index_key> count 1
On any Redis error it counts the dropped batch, reconnects with backoff, continues.

Stream IDs come from event time (ms), with a per-stream monotonic guard so a backward
WR re-arm jump cannot make XADD error (Redis requires increasing IDs).

Redis is reached through an injected `connect` factory (default: a real redis-py
client). redis-py is imported lazily inside that factory so this module imports cleanly
on a machine without redis-py and the unit tests run with a stub."""
import queue
import threading
import time


def _default_connect(host, port):
    import redis   # lazy: module imports without redis-py present (PC unit tests)
    return redis.Redis(host=host, port=port,
                       socket_connect_timeout=1.0, socket_timeout=1.0)


class RedisSink:
    def __init__(self, host="127.0.0.1", port=6379, maxlen=1_000_000,
                 queue_size=100_000, batch=1000, connect=None):
        self.maxlen = maxlen
        self.batch = batch
        self._connect = connect or (lambda: _default_connect(host, port))
        self._q = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._last_ms = {}                       # per-stream monotonic-ID guard
        self.published = 0
        self.queue_dropped = 0
        self.redis_dropped = 0
        self.reconnects = 0

    # ---- producer side (drain thread) ----
    def submit(self, record):
        """Enqueue one event record. Never blocks: on a full queue drop the OLDEST
        record (counted), then enqueue this one. A record is:
        {stream, id_ms, fields, index_key, index_fields}."""
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
                    "queued": self._q.qsize()}

    def _drain_batch(self):
        batch = []
        for _ in range(self.batch):
            try:
                batch.append(self._q.get_nowait())
            except queue.Empty:
                break
        return batch

    def _write_batch(self, client, batch):
        pipe = client.pipeline(transaction=False)
        for rec in batch:
            stream = rec["stream"]
            ms = rec["id_ms"]
            last = self._last_ms.get(stream, 0)
            if ms < last:                        # monotonic guard: never go backward
                ms = last
            self._last_ms[stream] = ms
            pipe.xadd(stream, rec["fields"], id="%d-*" % ms,
                      maxlen=self.maxlen, approximate=True)
            pipe.hset(rec["index_key"], mapping=rec["index_fields"])
            pipe.hincrby(rec["index_key"], "count", 1)
        pipe.execute()

    def _run(self):
        client = None
        while True:
            if self._stop.is_set() and self._q.empty():
                break
            if client is None:
                try:
                    client = self._connect()
                except Exception:
                    with self._lock:
                        self.reconnects += 1
                    if self._stop.is_set():
                        break                    # stopping AND cannot connect: give up rest
                    time.sleep(0.5)
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
            except Exception:
                with self._lock:
                    self.redis_dropped += len(batch)
                    self.reconnects += 1
                client = None                    # force reconnect next iteration
