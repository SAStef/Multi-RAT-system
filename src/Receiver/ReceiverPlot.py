import socket as s
import struct
import time
import select

PORT1    = 6967
PORT2    = 6968
HDR_FMT  = "!IIQB3x"
HDR_SIZE = struct.calcsize(HDR_FMT)  # 20 bytes

STATS_INTERVAL = 5.0  # Print stats every N seconds

# Per-path metrics
metrics = {
    1: {
        "received":       0,
        "last_seq":       -1,
        "lost":           0,
        "latencies":      [],   # ms
        "last_latency":   None, # for jitter calculation
        "jitters":        [],   # ms
        "bytes":          0,
        "window_start":   None, # for throughput window
    },
    2: {
        "received":       0,
        "last_seq":       -1,
        "lost":           0,
        "latencies":      [],
        "last_latency":   None,
        "jitters":        [],
        "bytes":          0,
        "window_start":   None,
    },
}

sock1 = s.socket(s.AF_INET, s.SOCK_DGRAM)
sock2 = s.socket(s.AF_INET, s.SOCK_DGRAM)
sock1.bind(("0.0.0.0", PORT1))
sock2.bind(("0.0.0.0", PORT2))

print(f"Listening on UDP {PORT1} (path 1) and {PORT2} (path 2)...")
print(f"Printing stats every {STATS_INTERVAL:.0f}s\n")

last_stats_time = time.perf_counter()


def update_metrics(path: int, seq: int, ts_ns: int, pkt_size: int):
    m   = metrics[path]
    now = time.time_ns()

    # --- Latency ---
    latency_ms = (now - ts_ns) / 1_000_000
    m["latencies"].append(latency_ms)

    # --- Jitter (RFC 3550: mean absolute difference between consecutive latencies) ---
    if m["last_latency"] is not None:
        jitter = abs(latency_ms - m["last_latency"])
        m["jitters"].append(jitter)
    m["last_latency"] = latency_ms

    # --- Packet loss (gap in sequence numbers) ---
    if m["last_seq"] >= 0:
        gap = seq - m["last_seq"] - 1
        if gap > 0:
            m["lost"] += gap
    m["last_seq"] = seq

    # --- Throughput ---
    if m["window_start"] is None:
        m["window_start"] = time.perf_counter()
    m["bytes"]    += pkt_size
    m["received"] += 1


def print_stats():
    print("=" * 60)
    for path, m in metrics.items():
        rx   = m["received"]
        lost = m["lost"]
        total_expected = rx + lost

        # Latency
        avg_lat = sum(m["latencies"]) / len(m["latencies"]) if m["latencies"] else 0.0
        min_lat = min(m["latencies"])                        if m["latencies"] else 0.0
        max_lat = max(m["latencies"])                        if m["latencies"] else 0.0

        # Jitter
        avg_jitter = sum(m["jitters"]) / len(m["jitters"]) if m["jitters"] else 0.0

        # Throughput
        elapsed = time.perf_counter() - m["window_start"] if m["window_start"] else 1
        throughput_kbps = (m["bytes"] * 8 / 1000) / elapsed if elapsed > 0 else 0.0

        # Packet loss %
        loss_pct = (lost / total_expected * 100) if total_expected > 0 else 0.0

        print(f"  Path {path}:")
        print(f"    Received     : {rx} packets")
        print(f"    Latency      : avg={avg_lat:.2f}ms  min={min_lat:.2f}ms  max={max_lat:.2f}ms")
        print(f"    Jitter       : avg={avg_jitter:.2f}ms")
        print(f"    Packet loss  : {lost} lost / {total_expected} expected ({loss_pct:.1f}%)")
        print(f"    Throughput   : {throughput_kbps:.1f} kbps")
        print()
    print("=" * 60 + "\n")


try:
    while True:
        readable, _, _ = select.select([sock1, sock2], [], [], 1.0)
        for sock in readable:
            data, addr = sock.recvfrom(4096)
            if len(data) < HDR_SIZE:
                print(f"[{addr}] Packet too short ({len(data)} bytes), skipping")
                continue

            seq, session_id, ts_ns, path = struct.unpack(HDR_FMT, data[:HDR_SIZE])

            update_metrics(path, seq, ts_ns, len(data))

            latency_ms = (time.time_ns() - ts_ns) / 1_000_000
            print(
                f"path={path} seq={seq:>6} session={session_id:#010x} "
                f"latency={latency_ms:>7.2f}ms  addr={addr}"
            )

        # Print stats every STATS_INTERVAL seconds
        if time.perf_counter() - last_stats_time >= STATS_INTERVAL:
            print_stats()
            last_stats_time = time.perf_counter()

except KeyboardInterrupt:
    print("\nFinal stats:")
    print_stats()
finally:
    sock1.close()
    sock2.close()
    print("Sockets closed")