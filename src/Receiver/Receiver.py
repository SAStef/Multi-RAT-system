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
print(f"Listening on UDP {PORT1} (path 1) and {PORT2} (path 2)...")

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

try:
    while True:
        readable, _, _ = select.select([sock1, sock2], [], [], 1.0)  # 1s timeout
        for sock in readable:
            data, addr = sock.recvfrom(4096)
            if len(data) < HDR_SIZE:
                print(f"[{addr}] Packet too short ({len(data)} bytes), skipping", flush=True)
                continue

            seq, session_id, ts_ns, path = struct.unpack(HDR_FMT, data[:HDR_SIZE])
            payload = data[HDR_SIZE:]
            m   = metrics
            now_ns = time.time_ns()
            latency_ms = (now_ns - ts_ns) / 1_000_000
            avg_jitter = sum(m["jitters"]) / len(m["jitters"]) if m["jitters"] else 0.0

            elapsed = time.perf_counter() - m["window_start"] if m["window_start"] else 1
            throughput_kbps = (m["bytes"] * 8 / 1000) / elapsed if elapsed > 0 else 0.0

            print(
                f"addr={addr} path={path} seq={seq} "
                f"session={session_id:#010x} "
                f"latency={latency_ms:.2f}ms "
                f"payload={payload.hex()}"
            )

except KeyboardInterrupt:
    print("\nStopped by user")
finally:
    sock1.close()
    sock2.close()
    print("Sockets closed")