import socket
import os
import time
import struct
import threading

# --- Config ---
IP           = "10.209.154.26"
PORT1        = 6967
PORT2        = 6968
PPS          = 20.0
PAYLOAD_SIZE = 32
COUNT        = 1000
HDR_FMT      = "!IIQB3x"
# --------------

sock1 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
session_id = int.from_bytes(os.urandom(4), "big")
interval   = 1.0 / PPS if PPS > 0 else 0.0


def send_packet(sock, packet, dest):
    """Send a single packet — runs in its own thread for minimal path skew."""
    try:
        sock.sendto(packet, dest)
    except OSError as e:
        print(f"[send error] {e}")


def run():
    seq       = 0
    next_send = time.perf_counter()

    try:
        while True:
            if COUNT and seq >= COUNT:
                break

            # Busy-wait the last ~1 ms for precise timing
            now = time.perf_counter()
            sleep_time = next_send - now
            if sleep_time > 0.001:
                time.sleep(sleep_time - 0.001)
            while time.perf_counter() < next_send:
                pass

            ts_ns   = time.time_ns()
            payload = os.urandom(PAYLOAD_SIZE)

            pkt1 = struct.pack(HDR_FMT, seq, session_id, ts_ns, 1) + payload
            pkt2 = struct.pack(HDR_FMT, seq, session_id, ts_ns, 2) + payload

            # Fire both paths as close together as possible
            t1 = threading.Thread(target=send_packet, args=(sock1, pkt1, (IP, PORT1)), daemon=True)
            t2 = threading.Thread(target=send_packet, args=(sock2, pkt2, (IP, PORT2)), daemon=True)
            t1.start(); t2.start()
            t1.join();  t2.join()

            if seq % 100 == 0:
                print(f"Sent seq={seq}")

            seq       += 1
            next_send += interval

    except KeyboardInterrupt:
        print("\nStopped by user")
    finally:
        sock1.close()
        sock2.close()
        print(f"Finished — sent {seq} packets on each path")


if __name__ == "__main__":
    print(f"Sending {COUNT} packets @ {PPS} pps to {IP} on ports {PORT1} and {PORT2}")
    run()