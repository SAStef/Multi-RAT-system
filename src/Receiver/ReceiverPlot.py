import socket as s
import struct
import time
import select

PORT1 = 6967
PORT2 = 6968
HDR_FMT = "!IIQB3x"
HDR_SIZE = struct.calcsize(HDR_FMT)  # 20 bytes

sock1 = s.socket(s.AF_INET, s.SOCK_DGRAM)
sock2 = s.socket(s.AF_INET, s.SOCK_DGRAM)
sock1.bind(("0.0.0.0", PORT1))
sock2.bind(("0.0.0.0", PORT2))
print(f"Listening on UDP {PORT1} (path 1) and {PORT2} (path 2)...\n")

def new_path_metrics():
    return {
        "received":     0,
        "last_seq":     -1,
        "lost":         0,
        "last_latency": None,   
        "bytes":        0,
        "start_time":   None,   
    }

metrics = {
    1: new_path_metrics(),
    2: new_path_metrics(),
}

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

try:
    while True:
        readable, _, _ = select.select([sock1, sock2], [], [], 1.0)
        for sock in readable:
            data, addr = sock.recvfrom(4096)

            if len(data) < HDR_SIZE:
                print(f"[{addr}] Packet too short ({len(data)} bytes), skipping", flush=True)
                continue

            seq, session_id, ts_ns, path = struct.unpack(HDR_FMT, data[:HDR_SIZE])
            payload = data[HDR_SIZE:]

            now_ns = time.time_ns()
            latency_ms = (now_ns - ts_ns) / 1_000_000

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