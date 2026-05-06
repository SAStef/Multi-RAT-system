import socket, struct, time, binascii, threading, requests

PORT1 = 6967
PORT2 = 6968
HDR_FMT = "!IIQBBHx"
HDR_SIZE = struct.calcsize(HDR_FMT)
DASHBOARD_URL = "http://34.32.45.194:3000/metrics"

def crc(data):
    return binascii.crc_hqx(data, 0xFFFF)

def rx(sock):
    port = sock.getsockname()[1]
    print(f"Listening on port {port}")

    while True:
        data, addr = sock.recvfrom(65535)
        if len(data) < HDR_SIZE:
            continue

        seq, session, ts, path, ttl, cs = struct.unpack(HDR_FMT, data[:HDR_SIZE])
        payload = data[HDR_SIZE:]

        if crc(payload) != cs:
            print(f"[BAD CRC] port={port}")
            continue

        latency = (time.time_ns() - ts) / 1e6
        print(f"[RX] port={port} path={path} seq={seq} latency={latency:.2f}ms from={addr}")

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