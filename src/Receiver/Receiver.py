import socket as s
import struct
import time
import select
import binascii

PORT1    = 6967
PORT2    = 6968
HDR_FMT  = "!IIQBBHx"   # seq(I) session(I) ts_ns(Q) path(B) sent_ttl(B) crc16(H) pad(x)
HDR_SIZE = struct.calcsize(HDR_FMT)  # 21 bytes

sock1 = s.socket(s.AF_INET, s.SOCK_DGRAM)
sock2 = s.socket(s.AF_INET, s.SOCK_DGRAM)
sock1.bind(("0.0.0.0", PORT1))
sock2.bind(("0.0.0.0", PORT2))
print(f"Listening on UDP {PORT1} (path 1) and {PORT2} (path 2)...")

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

            seq, session_id, ts_ns, path, sent_ttl, cs = struct.unpack(HDR_FMT, data[:HDR_SIZE])
            payload = data[HDR_SIZE:]

            if crc16(payload) != cs:
                print(f"[Path {path}] BAD CRC seq={seq} from={addr}", flush=True)
                continue

            latency_ms = (time.time_ns() - ts_ns) / 1_000_000

            print(
                f"addr={addr} path={path} seq={seq} "
                f"session={session_id:#010x} "
                f"latency={latency_ms:.2f}ms "
                f"sent_ttl={sent_ttl} "
                f"payload={payload.hex()}"
                , flush=True)

except KeyboardInterrupt:
    print("\nStopped by user")
finally:
    sock1.close()
    sock2.close()
    print("Sockets closed")