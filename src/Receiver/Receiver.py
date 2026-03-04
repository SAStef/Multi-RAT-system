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

        now_ns = time.time_ns()
        latency_ms = (now_ns - ts_ns) / 1_000_000

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