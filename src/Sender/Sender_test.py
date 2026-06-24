# simple sender, sends the same packet on both paths
import socket as s
import os
import time
import struct
import binascii

ip           = "10.209.169.34"
port1        = 6967
port2        = 6968
pps          = 20.0
payload_size = 32
count        = 0       # 0 = send forever
SENT_TTL     = 64
HDR_FMT      = "!IIQBBHx"
HDR_SIZE     = struct.calcsize(HDR_FMT)

sock1 = s.socket(s.AF_INET, s.SOCK_DGRAM)
sock2 = s.socket(s.AF_INET, s.SOCK_DGRAM)
# set ttl so the receiver can figure out the hop count
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

        # same packet on both paths (path field differs)
        packet1 = struct.pack(HDR_FMT, seq, session_id, ts_ns, 1, SENT_TTL, cs) + payload
        sock1.sendto(packet1, (ip, port1))
        packet2 = struct.pack(HDR_FMT, seq, session_id, ts_ns, 2, SENT_TTL, cs) + payload
        sock2.sendto(packet2, (ip, port2))

        if seq % 100 == 0:
            print(f"Sent {seq}")
        seq      += 1
        next_send += interval

except KeyboardInterrupt:
    print("Stopped")
finally:
    sock1.close()
    sock2.close()
    print("Finished")
