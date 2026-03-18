"""
Multi-RAT Receiver — FRER (IEEE 802.1CB) + HTTP bridge to Express dashboard

Thread architecture:
  - receiver_thread(sock) × 2  — one per UDP socket, handles each path in parallel
  - aggregator_thread()        — 1-second tick: compute averages, POST to Express

All shared state protected by metrics_lock.
stop_event signals clean shutdown on Ctrl-C.
"""

import socket
import struct
import time
import threading
import collections
import binascii
import json
import urllib.request
import platform

# ── Configuration ──────────────────────────────────────────────────────────────
PORT1        = 6967
PORT2        = 6968
HDR_FMT      = "!IIQBBHx"               # seq(I) session(I) ts_ns(Q) path(B) sent_ttl(B) crc16(H) pad(x)
HDR_SIZE     = struct.calcsize(HDR_FMT)  # 21 bytes
FRER_WINDOW  = 2000
EXPRESS_URL  = "http://localhost:3000/metrics"

PATH_LABELS  = {1: "Path 1", 2: "Path 2"}

# ── Platform detection ─────────────────────────────────────────────────────────
_IS_WINDOWS  = platform.system() == 'Windows'
_HAS_RECVMSG = hasattr(socket.socket, 'recvmsg') and not _IS_WINDOWS
_IP_RECVTTL  = getattr(socket, 'IP_RECVTTL', 24)   # macOS = 24
_IP_TTL      = getattr(socket, 'IP_TTL',     4)    # Linux  = 4
_TTL_TYPES   = {_IP_RECVTTL, _IP_TTL}

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

# ── FRER deduplication (sliding window, O(1) lookup) ─────────────────────────
_frer_seen: collections.deque = collections.deque(maxlen=FRER_WINDOW)
_frer_set:  set                = set()

def frer_is_duplicate(session_id: int, seq: int) -> bool:
    """Return True if this (session_id, seq) was already delivered."""
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

    # Jitter — RFC 3550 absolute difference
    jitter_ms = abs(latency_ms - m["last_latency"]) if m["last_latency"] is not None else 0.0
    m["last_latency"] = latency_ms

    # Throughput (cumulative bytes → kbps)
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

# ── Receiver thread — one per UDP socket ──────────────────────────────────────
def receiver_thread(sock: socket.socket):
    if _HAS_RECVMSG:
        try:
            sock.setsockopt(socket.IPPROTO_IP, _IP_RECVTTL, 1)
        except OSError:
            pass
    else:
        print("[Receiver] recvmsg not available on this platform — hops will show as —")

    sock.settimeout(1.0)
    port   = sock.getsockname()[1]
    ancbuf = socket.CMSG_SPACE(1) if hasattr(socket, 'CMSG_SPACE') else 32
    print(f"[Receiver] Listening on UDP port {port}")

    try:
        while not stop_event.is_set():
            try:
                if _HAS_RECVMSG:
                    data, ancdata, _flags, addr = sock.recvmsg(65535, ancbuf)
                else:
                    data, addr = sock.recvfrom(65535)
                    ancdata = []
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                break

            if len(data) < HDR_SIZE:
                continue

            # Extract received TTL from IP ancillary data
            received_ttl = None
            for cmsg_level, cmsg_type, cmsg_data in ancdata:
                if cmsg_level == socket.IPPROTO_IP and cmsg_type in _TTL_TYPES:
                    received_ttl = struct.unpack('B', cmsg_data[:1])[0]
                    break

            seq, session_id, ts_ns, path, sent_ttl, cs = struct.unpack(HDR_FMT, data[:HDR_SIZE])
            payload = data[HDR_SIZE:]

            if crc16(payload) != cs:
                with metrics_lock:
                    if path in metrics:
                        metrics[path]["crc_errors"] += 1
                print(f"[Path {path}] BAD CRC seq={seq} from={addr}")
                continue

            latency_ms = max(0.0, (time.time_ns() - ts_ns) / 1_000_000)
            hops = (sent_ttl - received_ttl) if received_ttl is not None else None

            with metrics_lock:
                if path in metrics:
                    _update(metrics[path], seq, latency_ms, len(payload), hops)

                if frer_is_duplicate(session_id, seq):
                    if path in metrics:
                        metrics[path]["duplicates"] += 1
                    print(f"[FRER DROP] seq={seq} path={path} — duplicate eliminated", flush=True)
                    continue

                # First copy — deliver to merged stream
                _update(merged_metrics, seq, latency_ms, len(payload))

    except Exception as exc:
        print(f"[Receiver] Error: {exc}")
    finally:
        sock.close()

# ── Aggregator — 1-second snapshots → Express ─────────────────────────────────
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
    """Fire-and-forget HTTP POST — drops silently if Express is not up."""
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
        pass

def aggregator_thread():
    while not stop_event.is_set():
        time.sleep(1.0)
        payload = {"time": round(time.perf_counter() - t0, 1), "paths": {}, "merged": {}}

        with metrics_lock:
            print()
            for path, m in metrics.items():
                snap = _snapshot(m)
                if snap:
                    snap["duplicates"] = m["duplicates"]
                    snap["crc_errors"] = m["crc_errors"]
                    payload["paths"][str(path)] = snap
                    print(
                        f"[{PATH_LABELS[path]}]  "
                        f"latency={snap['latency']:7.2f} ms  "
                        f"jitter={snap['jitter']:6.2f} ms  "
                        f"throughput={snap['throughput']:8.2f} kbps  "
                        f"loss={snap['loss']:5.1f}%  "
                        f"dupes={snap['duplicates']}  crc_err={snap['crc_errors']}"
                    )

            snap = _snapshot(merged_metrics)
            if snap:
                payload["merged"] = snap
                print(
                    f"[Merged]     "
                    f"latency={snap['latency']:7.2f} ms  "
                    f"jitter={snap['jitter']:6.2f} ms  "
                    f"throughput={snap['throughput']:8.2f} kbps  "
                    f"loss={snap['loss']:5.1f}%"
                )

        post_to_express(payload)

# ── Start ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    sock1 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock1.bind(("0.0.0.0", PORT1))
    sock2.bind(("0.0.0.0", PORT2))

    threading.Thread(target=receiver_thread, args=(sock1,), name="rx-path1", daemon=True).start()
    threading.Thread(target=receiver_thread, args=(sock2,), name="rx-path2", daemon=True).start()
    threading.Thread(target=aggregator_thread, daemon=True).start()

    print("Receiver running — posting metrics to", EXPRESS_URL)
    print("Press Ctrl-C to stop")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_event.set()
        print("\nStopped")
