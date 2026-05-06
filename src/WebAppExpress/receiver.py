import socket, struct, time, binascii, threading, requests

PORT1 = 6967
PORT2 = 6968
HDR_FMT = "!IIQBBHx"
HDR_SIZE = struct.calcsize(HDR_FMT)
DASHBOARD_URL = "http://34.32.45.194:3000/metrics"

def crc(data):
    return binascii.crc_hqx(data, 0xFFFF)

# ── Metrics ────────────────────────────────────────────────────────────────────
def new_path_metrics() -> dict:
    return {
        "received":        0,
        "raw_received":    0,
        "raw_window":      0,
        "malformed":       0,
        "unknown_path":    0,
        "lost":            0,
        "duplicates":      0,
        "crc_errors":      0,
        "last_seq":        -1,
        "last_latency":    None,
        "bytes_window":    0,
        "raw_bytes_window": 0,
        "last_from":       None,
        "latency_hist":    collections.deque(maxlen=500),
        "jitter_hist":     collections.deque(maxlen=500),
        "hops_hist":       collections.deque(maxlen=500),
    }

def new_merged_metrics() -> dict:
    return {
        "received":        0,
        "lost":            0,
        "last_seq":        -1,
        "last_latency":    None,
        "bytes_window":    0,
        "latency_hist":    collections.deque(maxlen=500),
        "jitter_hist":     collections.deque(maxlen=500),
    }

metrics        = {1: new_path_metrics(), 2: new_path_metrics()}
merged_metrics = new_merged_metrics()
metrics_lock   = threading.Lock()
t0             = time.perf_counter()
stop_event     = threading.Event()

# ── FRER deduplication (sliding window, O(1) lookup) ─────────────────────────
_frer_seen: collections.deque = collections.deque(maxlen=FRER_WINDOW)
_frer_set:  set                = set()

    while True:
        data, addr = sock.recvfrom(65535)
        if len(data) < HDR_SIZE:
            continue

        seq, session, ts, path, ttl, cs = struct.unpack(HDR_FMT, data[:HDR_SIZE])
        payload = data[HDR_SIZE:]

        if crc(payload) != cs:
            print(f"[BAD CRC] port={port}")
            continue

    m["bytes_window"] += payload_size
    m["latency_hist"].append(latency_ms)
    m["jitter_hist"].append(jitter_ms)
    if hops is not None and "hops_hist" in m:
        m["hops_hist"].append(hops)

# ── Receiver thread — one per UDP socket ──────────────────────────────────────
def receiver_thread(sock: socket.socket):
    if _HAS_RECVMSG:
        try:
            requests.post(DASHBOARD_URL, json={
                "port": port,
                "path": path,
                "seq": seq,
                "latency": round(latency, 2),
                "from": str(addr),
                "session": session,
                "ttl": ttl,
            }, timeout=0.5)
        except Exception:
            pass
    else:
        print("[Receiver] recvmsg not available on this platform — hops will show as —")

    sock.settimeout(1.0)
    port   = sock.getsockname()[1]
    socket_path = 1 if port == PORT1 else 2
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

            with metrics_lock:
                metrics[socket_path]["raw_received"] += 1
                metrics[socket_path]["raw_window"] += 1
                metrics[socket_path]["raw_bytes_window"] += len(data)
                metrics[socket_path]["last_from"] = f"{addr[0]}:{addr[1]}"

            if len(data) < HDR_SIZE:
                with metrics_lock:
                    metrics[socket_path]["malformed"] += 1
                print(f"[Path {socket_path}] MALFORMED len={len(data)} from={addr}")
                continue

            # Extract received TTL from IP ancillary data
            received_ttl = None
            for cmsg_level, cmsg_type, cmsg_data in ancdata:
                if cmsg_level == socket.IPPROTO_IP and cmsg_type in _TTL_TYPES:
                    received_ttl = struct.unpack('B', cmsg_data[:1])[0]
                    break

            seq, session_id, ts_ns, path, sent_ttl, cs = struct.unpack(HDR_FMT, data[:HDR_SIZE])
            payload = data[HDR_SIZE:]
            metric_path = path if path in metrics else socket_path

            if path not in metrics:
                with metrics_lock:
                    metrics[socket_path]["unknown_path"] += 1
                print(f"[Path {socket_path}] UNKNOWN HEADER PATH path={path} seq={seq} from={addr}")

            if crc16(payload) != cs:
                with metrics_lock:
                    metrics[metric_path]["crc_errors"] += 1
                print(f"[Path {metric_path}] BAD CRC seq={seq} header_path={path} from={addr}")
                continue

            latency_ms = max(0.0, (time.time_ns() - ts_ns) / 1_000_000)
            hops = (sent_ttl - received_ttl) if received_ttl is not None else None

            with metrics_lock:
                _update(metrics[metric_path], seq, latency_ms, len(payload), hops)

                if frer_is_duplicate(session_id, seq):
                    metrics[metric_path]["duplicates"] += 1
                    print(f"[FRER DROP] seq={seq} path={metric_path} — duplicate eliminated", flush=True)
                    continue

                # First copy — deliver to merged stream
                _update(merged_metrics, seq, latency_ms, len(payload))

    except Exception as exc:
        print(f"[Receiver] Error: {exc}")
    finally:
        sock.close()

# ── Aggregator — 1-second snapshots → Express ─────────────────────────────────
def _snapshot(m: dict):
    has_parsed = bool(m["latency_hist"])
    if not has_parsed and "raw_received" not in m:
        return None

    avg_lat = sum(m["latency_hist"]) / len(m["latency_hist"]) if has_parsed else None
    avg_jit = sum(m["jitter_hist"])  / len(m["jitter_hist"]) if has_parsed else None
    throughput_kbps = m["bytes_window"] * 8 / 1000  # bytes in last 1s → kbps
    raw_throughput_kbps = m.get("raw_bytes_window", 0) * 8 / 1000
    total   = m["received"] + m["lost"]
    loss    = m["lost"] / total * 100 if total else 0.0

    hops_val = None
    if "hops_hist" in m and m["hops_hist"]:
        hops_val = round(sum(m["hops_hist"]) / len(m["hops_hist"]))

    result = {
        "latency":    round(avg_lat, 2) if avg_lat is not None else None,
        "jitter":     round(avg_jit, 2) if avg_jit is not None else None,
        "throughput": round(throughput_kbps, 2),
        "raw_throughput": round(raw_throughput_kbps, 2),
        "loss":       round(loss, 2),
        "received":   m["received"],
        "raw_received": m.get("raw_received"),
        "raw_window": m.get("raw_window"),
        "malformed":  m.get("malformed"),
        "unknown_path": m.get("unknown_path"),
        "lost":       m["lost"],
        "hops":       hops_val,
        "last_from":  m.get("last_from"),
    }

    m["bytes_window"] = 0
    if "raw_bytes_window" in m:
        m["raw_bytes_window"] = 0
    if "raw_window" in m:
        m["raw_window"] = 0
    m["latency_hist"].clear()
    m["jitter_hist"].clear()
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
                snap["duplicates"] = m["duplicates"]
                snap["crc_errors"] = m["crc_errors"]
                payload["paths"][str(path)] = snap
                if snap["latency"] is None:
                    print(
                        f"[{PATH_LABELS[path]}]  "
                        f"raw={snap['raw_received']}  parsed={snap['received']}  "
                        f"raw_thr={snap['raw_throughput']:8.2f} kbps  "
                        f"malformed={snap['malformed']}  crc_err={snap['crc_errors']}  "
                        f"unknown_path={snap['unknown_path']}  last_from={snap['last_from']}"
                    )
                else:
                    print(
                        f"[{PATH_LABELS[path]}]  "
                        f"latency={snap['latency']:7.2f} ms  "
                        f"jitter={snap['jitter']:6.2f} ms  "
                        f"throughput={snap['throughput']:8.2f} kbps  "
                        f"loss={snap['loss']:5.1f}%  "
                        f"raw={snap['raw_received']}  parsed={snap['received']}  "
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

s1 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

s1.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

s1.bind(("0.0.0.0", PORT1))
s2.bind(("0.0.0.0", PORT2))

threading.Thread(target=rx, args=(s1,), daemon=True).start()
threading.Thread(target=rx, args=(s2,), daemon=True).start()

while True:
    time.sleep(1)