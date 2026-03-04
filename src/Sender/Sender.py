import socket as s
import os
import time
import struct

ip = "10.209.154.26"  
port1 = 6967         # First UDP channel
port2 = 6968         # Second UDP channel
pps = 20.0          
payload_size = 32   
count = 1000         
HDR_FMT = "!IIQB3x"

sock1 = s.socket(s.AF_INET, s.SOCK_DGRAM)
sock2 = s.socket(s.AF_INET, s.SOCK_DGRAM)
session_id = int.from_bytes(os.urandom(4), "big")

interval = 1.0 / pps if pps > 0 else 0.0
seq = 0
next_send = time.perf_counter()

try:
    while True:
        if count and seq >= count:
            break
        now = time.perf_counter()
        if interval > 0 and now < next_send:
            time.sleep(next_send - now)
        ts_ns = time.time_ns()
        payload = os.urandom(payload_size)
        # Path 1
        packet1 = struct.pack(HDR_FMT, seq, session_id, ts_ns, 1) + payload
        sock1.sendto(packet1, (ip, port1))

        # Path 2 
        packet2 = struct.pack(HDR_FMT, seq, session_id, ts_ns, 2) + payload
        sock2.sendto(packet2, (ip, port2))

        if seq % 100 == 0:
            print(f"Sent {seq}")
        seq += 1
        next_send += interval

except KeyboardInterrupt:
    print("Stopped")
finally:
    sock1.close()
    sock2.close()
    print("Finished")