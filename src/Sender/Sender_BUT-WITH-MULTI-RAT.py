import socket as s
import os
import time
import struct
import binascii

# ── Configuration ──────────────────────────────────────────────────────────────
ip           = "10.209.154.26"   # Receiver IP
Wifiport     = 6967              # Path 1 — Wi-Fi UDP channel
Cellularport = 6968              # Path 2 — 4G/5G UDP channel

WIFI_IF_IP   = "10.209.188.129"  # Local IP of the Wi-Fi interface (bind path 1 here)
                                  # Set to "" to let the OS pick the interface automatically

pps          = 20.0
payload_size = 32
count        = 0                  # 0 = run forever
SENT_TTL     = 64                 # Initial TTL embedded in header so receiver can calculate hops
HDR_FMT      = "!IIQBBHx"        # seq(I) session(I) ts_ns(Q) path(B) sent_ttl(B) crc16(H) pad(x)
HDR_SIZE     = struct.calcsize(HDR_FMT)

sock1 = s.socket(s.AF_INET, s.SOCK_DGRAM)
sock2 = s.socket(s.AF_INET, s.SOCK_DGRAM)

# Bind each socket to its respective network interface so packets actually
# leave on the correct radio (Wi-Fi vs 4G/5G).  If WIFI_IF_IP is empty the
# OS picks the default route for path 1 as well.
sock1.bind((WIFI_IF_IP, 0))
sock2.bind(("", 0))

sock1.setsockopt(s.IPPROTO_IP, s.IP_TTL, SENT_TTL)
sock2.setsockopt(s.IPPROTO_IP, s.IP_TTL, SENT_TTL)

session_id = int.from_bytes(os.urandom(4), "big")

interval  = 1.0 / pps if pps > 0 else 0.0
seq       = 0
next_send = time.perf_counter()

def crc16(data: bytes) -> int:
    return binascii.crc_hqx(data, 0xFFFF)


try:
    while True:
        if count and seq >= count:
            break
        now = time.perf_counter()
        if interval > 0 and now < next_send:
            time.sleep(next_send - now)
        ts_ns   = time.time_ns()
        payload = os.urandom(payload_size)
        cs      = crc16(payload)

        # Path 1 — Wi-Fi
        packet1 = struct.pack(HDR_FMT, seq, session_id, ts_ns, 1, SENT_TTL, cs) + payload
        sock1.sendto(packet1, (ip, Wifiport))
        # Path 2 — 4G/5G
        packet2 = struct.pack(HDR_FMT, seq, session_id, ts_ns, 2, SENT_TTL, cs) + payload
        sock2.sendto(packet2, (ip, Cellularport))

        if seq % 100 == 0:
            print(f"seq={seq} sent on Wi-Fi(path=1) and 4G/5G(path=2)")
        seq      += 1
        next_send += interval

except KeyboardInterrupt:
    print("Stopped")
finally:
    sock1.close()
    sock2.close()
    print("Finished")
