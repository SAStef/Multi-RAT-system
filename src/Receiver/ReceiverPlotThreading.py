import socket as s
import struct
import time
import select
import threading
import collections
import binascii

PORT1 = 6967
PORT2 = 6968
HDR_FMT = "!IIQBHx"
HDR_SIZE = struct.calcsize(HDR_FMT)  # 20 bytes

sock1 = s.socket(s.AF_INET, s.SOCK_DGRAM)
sock2 = s.socket(s.AF_INET, s.SOCK_DGRAM)
sock1.bind(("0.0.0.0", PORT1))
sock2.bind(("0.0.0.0", PORT2))
print(f"Listening on UDP {PORT1} (path 1) and {PORT2} (path 2)...\n")

def new_path_metrics():
    return {
        "received":        0,
        "last_seq":        -1,
        "lost":            0,
        "last_latency":    None,
        "bytes":           0,
        "start_time":      None,
        "latency_hist":    collections.deque(maxlen=200),
        "jitter_hist":     collections.deque(maxlen=200),
        "throughput_hist": collections.deque(maxlen=200),
    }

metrics = {
    1: new_path_metrics(),
    2: new_path_metrics(),
}

metrics_lock = threading.Lock()

def printer_thread():
    while True:
        time.sleep(1.0)
        with metrics_lock:
            print("")
            for path, m in metrics.items():
                if not m["latency_hist"]:
                    continue
                avg_lat = sum(m["latency_hist"]) / len(m["latency_hist"])
                avg_jit = sum(m["jitter_hist"]) / len(m["jitter_hist"])
                avg_thr = sum(m["throughput_hist"]) / len(m["throughput_hist"])
                total   = m["received"] + m["lost"]
                loss    = m["lost"] / total * 100 if total else 0.0
                print(f"[Path {path} — 1s avg]  latency={avg_lat:.2f}ms  jitter={avg_jit:.2f}ms  throughput={avg_thr:.2f}kbps  loss={loss:.1f}%")
                m["latency_hist"].clear()
                m["jitter_hist"].clear()
                m["throughput_hist"].clear()

t = threading.Thread(target=printer_thread, daemon=True)
t.start()

def update_and_print(path, seq, latency_ms, payload_size, addr):
    m = metrics[path]

    # --- Packet count & loss ---
    m["received"] += 1
    if m["last_seq"] >= 0 and seq > m["last_seq"] + 1:
        lost = seq - m["last_seq"] - 1
        m["lost"] += lost
    m["last_seq"] = seq

    # --- Jitter (RFC 3550 style: |prev_latency - cur_latency|) ---
    if m["last_latency"] is not None:
        jitter_ms = abs(latency_ms - m["last_latency"])
    else:
        jitter_ms = 0.0
    m["last_latency"] = latency_ms

    # --- Throughput ---
    m["bytes"] += payload_size
    now = time.perf_counter()
    if m["start_time"] is None:
        m["start_time"] = now
    elapsed = now - m["start_time"]
    throughput_kbps = (m["bytes"] * 8 / 1000) / elapsed if elapsed > 0 else 0.0

    # --- Loss rate ---
    total_expected = m["received"] + m["lost"]
    loss_pct = (m["lost"] / total_expected * 100) if total_expected > 0 else 0.0

    # --- Save to history ---
    m["latency_hist"].append(latency_ms)
    m["jitter_hist"].append(jitter_ms)
    m["throughput_hist"].append(throughput_kbps)

    print(
        f"[Path {path}] "
        f"seq={seq:<6} "
        f"addr={addr[0]}:{addr[1]}  "
        f"latency={latency_ms:>8.2f} ms  "
        f"jitter={jitter_ms:>7.2f} ms  "
        f"throughput={throughput_kbps:>8.2f} kbps  "
        f"lost={m['lost']} ({loss_pct:.1f}%)",
        flush=True,
    )
def crc16(data: bytes) -> int:
    return binascii.crc_hqx(data, 0xFFFF)

try:
    while True:
        readable, _, _ = select.select([sock1, sock2], [], [], 1.0)
        for sock in readable:
            data, addr = sock.recvfrom(4096)

            if len(data) < HDR_SIZE:
                print(f"[{addr}] Packet too short ({len(data)} bytes), skipping", flush=True)
                continue

            seq, session_id, ts_ns, path, cs = struct.unpack(HDR_FMT, data[:HDR_SIZE])
            payload = data[HDR_SIZE:]

            if crc16(payload) != cs:
                print(f"BAD CRC seq={seq} path={path} from={addr}")
                continue

            now_ns = time.time_ns()
            latency_ms = (now_ns - ts_ns) / 1_000_000

            with metrics_lock:
                update_and_print(path, seq, latency_ms, len(payload), addr)

except KeyboardInterrupt:
    print("\n--- Final summary ---")
    for path, m in metrics.items():
        total = m["received"] + m["lost"]
        loss_pct = (m["lost"] / total * 100) if total > 0 else 0.0
        print(f"  Path {path}: received={m['received']}  lost={m['lost']} ({loss_pct:.1f}%)")
finally:
    sock1.close()
    sock2.close()
    print("Sockets closed")