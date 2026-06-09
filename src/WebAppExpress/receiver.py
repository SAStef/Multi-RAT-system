"""
Multi-RAT receiver for the Android sender app.

Receives UDP packets on:
  - 6967: WiFi / path 1
  - 6968: Cellular / path 2

Packet format, matching Multi-RAT-Sender/NetworkClient.kt:
  seq(I=4) | session(I=4) | ts_ns(Q=8) | path(B=1) | ttl(B=1) | crc16(H=2) | pad(x=1)

The receiver posts one metrics snapshot per second to the Express dashboard. And now also the App 
It also tracks raw UDP packets, so tcpdump-visible packets still appear in the
dashboard even if the packet format or CRC does not parse.
"""

import binascii
import collections
import json
import platform
import socket
import struct
import threading
import time
import urllib.request

PORT1 = 6967
PORT2 = 6968
HDR_FMT = "!IIQBBHx"
HDR_SIZE = struct.calcsize(HDR_FMT)
FRER_WINDOW = 2000
EXPRESS_URL = "http://localhost:3000/metrics"
ANDROID_URL = "http://PHONE_IP:8080/metrics"  # replacement needed
SEND_TO_ANDROID = True
PATH_LABELS = {1: "WiFi", 2: "5G/LTE"}

_IS_WINDOWS = platform.system() == "Windows"
_HAS_RECVMSG = hasattr(socket.socket, "recvmsg") and not _IS_WINDOWS
_IP_RECVTTL = getattr(socket, "IP_RECVTTL", 24)
_IP_TTL = getattr(socket, "IP_TTL", 4)
_TTL_TYPES = {_IP_RECVTTL, _IP_TTL}

metrics_lock = threading.Lock()
stop_event = threading.Event()
t0 = time.perf_counter()


def crc16(data: bytes) -> int:
    return binascii.crc_hqx(data, 0xFFFF)


def new_path_metrics() -> dict:
    return {
        "received": 0,
        "raw_received": 0,
        "raw_window": 0,
        "malformed": 0,
        "unknown_path": 0,
        "lost": 0,
        "duplicates": 0,
        "crc_errors": 0,
        "last_seq": -1,
        "last_latency": None,
        "bytes_window": 0,
        "raw_bytes_window": 0,
        "last_from": None,
        "latency_hist": collections.deque(maxlen=500),
        "jitter_hist": collections.deque(maxlen=500),
        "hops_hist": collections.deque(maxlen=500),
    }


def new_merged_metrics() -> dict:
    return {
        "received": 0,
        "lost": 0,
        "last_seq": -1,
        "last_latency": None,
        "bytes_window": 0,
        "latency_hist": collections.deque(maxlen=500),
        "jitter_hist": collections.deque(maxlen=500),
    }


metrics = {1: new_path_metrics(), 2: new_path_metrics()}
merged_metrics = new_merged_metrics()

_frer_seen = collections.deque(maxlen=FRER_WINDOW)
_frer_set = set()


def frer_is_duplicate(session_id: int, seq: int) -> bool:
    key = (session_id, seq)
    if key in _frer_set:
        return True
    if len(_frer_seen) == FRER_WINDOW:
        _frer_set.discard(_frer_seen[0])
    _frer_seen.append(key)
    _frer_set.add(key)
    return False


def update_parsed_metrics(m: dict, seq: int, latency_ms: float, payload_size: int, hops=None):
    m["received"] += 1
    if m["last_seq"] >= 0 and seq > m["last_seq"] + 1:
        m["lost"] += seq - m["last_seq"] - 1
    m["last_seq"] = seq

    jitter_ms = abs(latency_ms - m["last_latency"]) if m["last_latency"] is not None else 0.0
    m["last_latency"] = latency_ms

    m["bytes_window"] += payload_size
    m["latency_hist"].append(latency_ms)
    m["jitter_hist"].append(jitter_ms)
    if hops is not None and "hops_hist" in m:
        m["hops_hist"].append(hops)


def receiver_thread(sock: socket.socket):
    if _HAS_RECVMSG:
        try:
            sock.setsockopt(socket.IPPROTO_IP, _IP_RECVTTL, 1)
        except OSError:
            pass
    else:
        print("[Receiver] recvmsg not available - hops will show as -")

    sock.settimeout(1.0)
    port = sock.getsockname()[1]
    socket_path = 1 if port == PORT1 else 2
    ancbuf = socket.CMSG_SPACE(1) if hasattr(socket, "CMSG_SPACE") else 32
    print(f"[Receiver] Listening on UDP port {port} ({PATH_LABELS[socket_path]})")

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
                m = metrics[socket_path]
                m["raw_received"] += 1
                m["raw_window"] += 1
                m["raw_bytes_window"] += len(data)
                m["last_from"] = f"{addr[0]}:{addr[1]}"

            if len(data) < HDR_SIZE:
                with metrics_lock:
                    metrics[socket_path]["malformed"] += 1
                print(f"[{PATH_LABELS[socket_path]}] MALFORMED len={len(data)} from={addr}")
                continue

            received_ttl = None
            for cmsg_level, cmsg_type, cmsg_data in ancdata:
                if cmsg_level == socket.IPPROTO_IP and cmsg_type in _TTL_TYPES:
                    received_ttl = struct.unpack("B", cmsg_data[:1])[0]
                    break

            seq, session_id, ts_ns, header_path, sent_ttl, cs = struct.unpack(HDR_FMT, data[:HDR_SIZE])
            payload = data[HDR_SIZE:]
            metric_path = header_path if header_path in metrics else socket_path

            if header_path not in metrics:
                with metrics_lock:
                    metrics[socket_path]["unknown_path"] += 1
                print(
                    f"[{PATH_LABELS[socket_path]}] UNKNOWN HEADER PATH "
                    f"path={header_path} seq={seq} from={addr}"
                )

            if crc16(payload) != cs:
                with metrics_lock:
                    metrics[metric_path]["crc_errors"] += 1
                print(f"[{PATH_LABELS[metric_path]}] BAD CRC seq={seq} from={addr}")
                continue

            latency_ms = max(0.0, (time.time_ns() - ts_ns) / 1_000_000)
            hops = sent_ttl - received_ttl if received_ttl is not None else None

            with metrics_lock:
                update_parsed_metrics(metrics[metric_path], seq, latency_ms, len(payload), hops)

                if frer_is_duplicate(session_id, seq):
                    metrics[metric_path]["duplicates"] += 1
                    print(f"[FRER DROP] seq={seq} path={metric_path} duplicate eliminated", flush=True)
                    continue

                update_parsed_metrics(merged_metrics, seq, latency_ms, len(payload))

    except Exception as exc:
        print(f"[Receiver] Error: {exc}")
    finally:
        sock.close()


def snapshot_path(m: dict) -> dict:
    has_parsed = bool(m["latency_hist"])
    avg_lat = sum(m["latency_hist"]) / len(m["latency_hist"]) if has_parsed else None
    avg_jit = sum(m["jitter_hist"]) / len(m["jitter_hist"]) if has_parsed else None
    throughput_kbps = m["bytes_window"] * 8 / 1000
    raw_throughput_kbps = m["raw_bytes_window"] * 8 / 1000
    total = m["received"] + m["lost"]
    loss = m["lost"] / total * 100 if total else 0.0

    hops_val = None
    if m["hops_hist"]:
        hops_val = round(sum(m["hops_hist"]) / len(m["hops_hist"]))

    result = {
        "latency": round(avg_lat, 2) if avg_lat is not None else None,
        "jitter": round(avg_jit, 2) if avg_jit is not None else None,
        "throughput": round(throughput_kbps, 2),
        "raw_throughput": round(raw_throughput_kbps, 2),
        "loss": round(loss, 2),
        "received": m["received"],
        "raw_received": m["raw_received"],
        "raw_window": m["raw_window"],
        "malformed": m["malformed"],
        "unknown_path": m["unknown_path"],
        "lost": m["lost"],
        "duplicates": m["duplicates"],
        "crc_errors": m["crc_errors"],
        "hops": hops_val,
        "last_from": m["last_from"],
    }

    m["bytes_window"] = 0
    m["raw_bytes_window"] = 0
    m["raw_window"] = 0
    m["latency_hist"].clear()
    m["jitter_hist"].clear()
    m["hops_hist"].clear()
    return result


def snapshot_merged(m: dict):
    if not m["latency_hist"]:
        return None

    avg_lat = sum(m["latency_hist"]) / len(m["latency_hist"])
    avg_jit = sum(m["jitter_hist"]) / len(m["jitter_hist"])
    throughput_kbps = m["bytes_window"] * 8 / 1000
    total = m["received"] + m["lost"]
    loss = m["lost"] / total * 100 if total else 0.0

    result = {
        "latency": round(avg_lat, 2),
        "jitter": round(avg_jit, 2),
        "throughput": round(throughput_kbps, 2),
        "loss": round(loss, 2),
        "received": m["received"],
        "lost": m["lost"],
        "hops": None,
    }

    m["bytes_window"] = 0
    m["latency_hist"].clear()
    m["jitter_hist"].clear()
    return result


def post_json(url: str, payload: dict, name: str):
    try:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1)
    except Exception as e:
        print(f"[POST ERROR] Could not send to {name}: {e}")
def post_metrics(payload: dict):
    post_json(EXPRESS_URL, payload, "Express dashboard")

    if SEND_TO_ANDROID:
        post_json(ANDROID_URL, payload, "Android app")


def aggregator_thread():
    while not stop_event.is_set():
        time.sleep(1.0)
        payload = {"time": round(time.perf_counter() - t0, 1), "paths": {}, "merged": {}}

        with metrics_lock:
            print()
            for path, m in metrics.items():
                snap = snapshot_path(m)
                payload["paths"][str(path)] = snap
                if snap["latency"] is None:
                    print(
                        f"[{PATH_LABELS[path]}] raw={snap['raw_received']} parsed={snap['received']} "
                        f"raw_thr={snap['raw_throughput']:8.2f} kbps malformed={snap['malformed']} "
                        f"crc_err={snap['crc_errors']} unknown_path={snap['unknown_path']} "
                        f"last_from={snap['last_from']}"
                    )
                else:
                    print(
                        f"[{PATH_LABELS[path]}] latency={snap['latency']:7.2f} ms "
                        f"jitter={snap['jitter']:6.2f} ms throughput={snap['throughput']:8.2f} kbps "
                        f"loss={snap['loss']:5.1f}% raw={snap['raw_received']} parsed={snap['received']} "
                        f"dupes={snap['duplicates']} crc_err={snap['crc_errors']}"
                    )

            merged_snap = snapshot_merged(merged_metrics)
            if merged_snap:
                payload["merged"] = merged_snap
                print(
                    f"[Merged] latency={merged_snap['latency']:7.2f} ms "
                    f"jitter={merged_snap['jitter']:6.2f} ms "
                    f"throughput={merged_snap['throughput']:8.2f} kbps "
                    f"loss={merged_snap['loss']:5.1f}%"
                )

        post_metrics(payload) 


def main():
    sock1 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock1.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock1.bind(("0.0.0.0", PORT1))
    sock2.bind(("0.0.0.0", PORT2))

    threading.Thread(target=receiver_thread, args=(sock1,), name="rx-wifi", daemon=True).start()
    threading.Thread(target=receiver_thread, args=(sock2,), name="rx-cell", daemon=True).start()
    threading.Thread(target=aggregator_thread, name="metrics-aggregator", daemon=True).start()

    print("Receiver running - posting metrics to", EXPRESS_URL)
    print("Press Ctrl-C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_event.set()
        print("\nStopped")


if __name__ == "__main__":
    main()
