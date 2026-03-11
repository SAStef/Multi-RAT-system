"""
Multi-RAT Receiver — FRER + HTTP bridge to Express

Runs the full FRER receiver logic and posts 1-second metric snapshots
to the Express server at http://localhost:3000/metrics via HTTP POST.

Run AFTER starting server.js:
    node server.js        (terminal 1)
    python receiver.py    (terminal 2)
    python ../Sender/Sender.py  (terminal 3)
"""

import socket
import struct
import threading
import collections
import binascii
import time
import json
import urllib.request

# ── Configuration ──────────────────────────────────────────────────────────────
PORT1        = 6967
PORT2        = 6968
HDR_FMT      = "!IIQBBHx"               # seq(I) session(I) ts_ns(Q) path(B) sent_ttl(B) crc16(H) pad(x)
HDR_SIZE     = struct.calcsize(HDR_FMT)  # 21 bytes
FRER_WINDOW  = 2000
EXPRESS_URL  = "http://localhost:3000/metrics"

# IP_RECVTTL: macOS=24, Linux=12
_IP_RECVTTL  = getattr(socket, 'IP_RECVTTL', 24)

# ── CRC ────────────────────────────────────────────────────────────────────────
def crc16(data: bytes) -> int:
    return binascii.crc_hqx(data, 0xFFFF)

# ── Metrics ────────────────────────────────────────────────────────────────────
def new_path_metrics() -> dict:
    return {
        "received":        0,
        "lost":            0,
        "duplicates":      0,
        "crc_errors":      0,
        "last_seq":        -1,
        "last_latency":    None,
        "bytes":           0,
        "start_time":      None,
        "latency_hist":    collections.deque(maxlen=500),
        "jitter_hist":     collections.deque(maxlen=500),
        "throughput_hist": collections.deque(maxlen=500),
        "hops_hist":       collections.deque(maxlen=500),
    }

def new_merged_metrics() -> dict:
    return {
        "received":        0,
        "lost":            0,
        "last_seq":        -1,
        "last_latency":    None,
        "bytes":           0,
        "start_time":      None,
        "latency_hist":    collections.deque(maxlen=500),
        "jitter_hist":     collections.deque(maxlen=500),
        "throughput_hist": collections.deque(maxlen=500),
    }

metrics        = {1: new_path_metrics(), 2: new_path_metrics()}
merged_metrics = new_merged_metrics()
metrics_lock   = threading.Lock()
t0             = time.perf_counter()
stop_event     = threading.Event()

# ── FRER deduplication ────────────────────────────────────────────────────────
_frer_seen: collections.deque = collections.deque(maxlen=FRER_WINDOW)
_frer_set:  set                = set()

def frer_is_duplicate(session_id: int, seq: int) -> bool:
    key = (session_id, seq)
    if key in _frer_set:
        return True
    if len(_frer_seen) == FRER_WINDOW:
        _frer_set.discard(_frer_seen[0])
    _frer_seen.append(key)
    _frer_set.add(key)
    return False

# ── Per-packet metric update ───────────────────────────────────────────────────
def _update(m: dict, seq: int, latency_ms: float, payload_size: int, hops=None):
    m["received"] += 1
    if m["last_seq"] >= 0 and seq > m["last_seq"] + 1:
        m["lost"] += seq - m["last_seq"] - 1
    m["last_seq"] = seq

    jitter_ms = abs(latency_ms - m["last_latency"]) if m["last_latency"] is not None else 0.0
    m["last_latency"] = latency_ms

    m["bytes"] += payload_size
    now = time.perf_counter()
    if m["start_time"] is None:
        m["start_time"] = now
    elapsed = now - m["start_time"]
    throughput_kbps = (m["bytes"] * 8 / 1000) / elapsed if elapsed > 0 else 0.0

    m["latency_hist"].append(latency_ms)
    m["jitter_hist"].append(jitter_ms)
    m["throughput_hist"].append(throughput_kbps)
    if hops is not None and "hops_hist" in m:
        m["hops_hist"].append(hops)

# ── Receiver thread ────────────────────────────────────────────────────────────
def receiver_thread(sock: socket.socket):
    try:
        sock.setsockopt(socket.IPPROTO_IP, _IP_RECVTTL, 1)
    except OSError:
        print("[Receiver] Warning: IP_RECVTTL not supported — hops will not be tracked")

    sock.settimeout(1.0)
    port   = sock.getsockname()[1]
    ancbuf = socket.CMSG_SPACE(1) if hasattr(socket, 'CMSG_SPACE') else 32
    print(f"[Receiver] Listening on UDP port {port}")

    try:
        while not stop_event.is_set():
            try:
                data, ancdata, _flags, addr = sock.recvmsg(65535, ancbuf)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                break

            if len(data) < HDR_SIZE:
                continue

            received_ttl = None
            for cmsg_level, cmsg_type, cmsg_data in ancdata:
                if cmsg_level == socket.IPPROTO_IP and cmsg_type == socket.IP_TTL:
                    received_ttl = struct.unpack('B', cmsg_data[:1])[0]

            seq, session_id, ts_ns, path, sent_ttl, cs = struct.unpack(HDR_FMT, data[:HDR_SIZE])
            payload = data[HDR_SIZE:]

            if crc16(payload) != cs:
                with metrics_lock:
                    if path in metrics:
                        metrics[path]["crc_errors"] += 1
                continue

            latency_ms = (time.time_ns() - ts_ns) / 1_000_000
            hops = (sent_ttl - received_ttl) if received_ttl is not None else None

            with metrics_lock:
                if path in metrics:
                    _update(metrics[path], seq, latency_ms, len(payload), hops)

                if frer_is_duplicate(session_id, seq):
                    if path in metrics:
                        metrics[path]["duplicates"] += 1
                    continue

                _update(merged_metrics, seq, latency_ms, len(payload))

    except Exception as exc:
        print(f"[Receiver] Error: {exc}")
    finally:
        sock.close()

# ── Aggregator — builds snapshots and POSTs to Express ───────────────────────
def _snapshot(m: dict):
    if not m["latency_hist"]:
        return None

    avg_lat = sum(m["latency_hist"]) / len(m["latency_hist"])
    avg_jit = sum(m["jitter_hist"])  / len(m["jitter_hist"])
    avg_thr = sum(m["throughput_hist"]) / len(m["throughput_hist"])
    total   = m["received"] + m["lost"]
    loss    = m["lost"] / total * 100 if total else 0.0

    hops_val = None
    if "hops_hist" in m and m["hops_hist"]:
        hops_val = round(sum(m["hops_hist"]) / len(m["hops_hist"]))

    result = {
        "latency":    round(avg_lat, 2),
        "jitter":     round(avg_jit, 2),
        "throughput": round(avg_thr, 2),
        "loss":       round(loss, 2),
        "received":   m["received"],
        "lost":       m["lost"],
        "hops":       hops_val,
    }

    m["latency_hist"].clear()
    m["jitter_hist"].clear()
    m["throughput_hist"].clear()
    if "hops_hist" in m:
        m["hops_hist"].clear()

    return result

def post_to_express(payload: dict):
    """Fire-and-forget HTTP POST to Express — drops silently if server is not up."""
    try:
        body = json.dumps(payload).encode()
        req  = urllib.request.Request(
            EXPRESS_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1)
    except Exception:
        pass  # Express not running yet or busy — skip this tick

def aggregator_thread():
    while not stop_event.is_set():
        time.sleep(1.0)
        payload = {"time": round(time.perf_counter() - t0, 1), "paths": {}, "merged": {}}

        with metrics_lock:
            for path, m in metrics.items():
                snap = _snapshot(m)
                if snap:
                    snap["duplicates"] = m["duplicates"]
                    snap["crc_errors"] = m["crc_errors"]
                    payload["paths"][str(path)] = snap

            snap = _snapshot(merged_metrics)
            if snap:
                payload["merged"] = snap

        post_to_express(payload)

# ── Start ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    sock1 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock1.bind(("0.0.0.0", PORT1))
    sock2.bind(("0.0.0.0", PORT2))

    threading.Thread(target=receiver_thread, args=(sock1,), daemon=True).start()
    threading.Thread(target=receiver_thread, args=(sock2,), daemon=True).start()
    threading.Thread(target=aggregator_thread, daemon=True).start()

    print("Receiver running — posting metrics to", EXPRESS_URL)
    print("Press Ctrl-C to stop")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_event.set()
        print("\nStopped")
