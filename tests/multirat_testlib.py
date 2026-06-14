#!/usr/bin/env python3
"""
Delt afsender- og fejl-injektions-logik for Multi-RAT testene.

Køres IKKE direkte — bruges af test_t1..t5-filerne. Sender nøjagtig samme
wire-format som telefonen (NetworkClient.kt) og receiver.py, så receiver +
dashboard bruges helt uændret:

  seq(I=4) | session(I=4) | ts_ns(Q=8) | path(B=1) | ttl(B=1) | crc16(H=2) | pad(x=1)
  path 1 -> UDP 6967 (Wi-Fi)   path 2 -> UDP 6968 (5G/LTE)

Hver test kalder run(...) med sine egne parametre.
"""

import argparse
import binascii
import os
import random
import socket
import struct
import sys
import threading
import time

HDR_FMT = "!IIQBBHx"
PORTS = {1: 6967, 2: 6968}
LABELS = {1: "Wi-Fi", 2: "5G/LTE"}


def crc16(data: bytes) -> int:
    return binascii.crc_hqx(data, 0xFFFF)


def parse_ip() -> str:
    """Tillader 'python3 test_xx.py --ip <server-ip>'. Standard = localhost."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default="127.0.0.1", help="receiver-IP (standard: localhost)")
    return ap.parse_args().ip


class _State:
    def __init__(self):
        self.enabled = {1: True, 2: True}
        self.running = True
        self.lock = threading.Lock()


def _keyboard(state: _State):
    for line in sys.stdin:
        c = line.strip().lower()
        with state.lock:
            if c == "1":
                state.enabled[1] = not state.enabled[1]
                print(f"  >> Wi-Fi  -> {'ON' if state.enabled[1] else 'OFF'}")
            elif c == "2":
                state.enabled[2] = not state.enabled[2]
                print(f"  >> 5G/LTE -> {'ON' if state.enabled[2] else 'OFF'}")
            elif c == "q":
                state.running = False
                print("  >> stopper ...")
                break


def run(title, ip="127.0.0.1", pps=20, payload=32, ttl=64, duration=0,
        loss=(0.0, 0.0), corrupt=(0.0, 0.0), reorder=(0.0, 0.0),
        phases=None, seed=1):
    """Send duplikerede pakker på begge paths med valgfri fejl-injektion.

    loss/corrupt/reorder : (path1, path2) sandsynligheder 0..1
    phases   : liste af (varighed_sek, {1:bool, 2:bool}); sidste varighed = None
               betyder 'kør til q/Ctrl-C'. Bruges til degraded-testen.
    duration : 0 = kør til q/Ctrl-C.
    """
    if seed is not None:
        random.seed(seed)

    socks = {}
    for p, port in PORTS.items():
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
        socks[p] = sk

    session_id = int.from_bytes(os.urandom(4), "big")
    state = _State()
    threading.Thread(target=_keyboard, args=(state,), daemon=True).start()

    interval = 1.0 / pps if pps > 0 else 0.0
    holdback = {1: None, 2: None}
    sent = {1: 0, 2: 0}
    dropped = {1: 0, 2: 0}
    corrupted = {1: 0, 2: 0}
    reordered = {1: 0, 2: 0}

    print("=" * 64)
    print(f"  {title}  ->  {ip}")
    print(f"  {pps:.0f} pps, payload {payload}B, path 1->{PORTS[1]}  path 2->{PORTS[2]}")
    print("  Taster: 1=toggle Wi-Fi  2=toggle 5G  q=quit   (tryk Enter efter)")
    print("=" * 64)

    def send_now(path: int, packet: bytes):
        try:
            socks[path].sendto(packet, (ip, PORTS[path]))
            sent[path] += 1
        except OSError:
            pass  # fire-and-forget: en kortvarigt utilgængelig receiver stopper ikke testen

    seq = 0
    start = time.perf_counter()
    next_send = start
    last_log = start
    phase_idx = -1
    try:
        while state.running:
            now = time.perf_counter()
            elapsed = now - start
            if duration and elapsed >= duration:
                break
            if interval > 0 and now < next_send:
                time.sleep(min(next_send - now, 0.05))
                continue

            # --- tidsstyrede faser (kun degraded) ---
            if phases:
                acc, idx = 0.0, len(phases) - 1
                for i, (d, _en) in enumerate(phases):
                    if d is None or elapsed < acc + d:
                        idx = i
                        break
                    acc += d
                if idx != phase_idx:
                    phase_idx = idx
                    with state.lock:
                        state.enabled = dict(phases[idx][1])
                        en = dict(state.enabled)
                    tag = " ".join(f"{LABELS[p]}={'ON' if en[p] else 'OFF'}" for p in (1, 2))
                    print(f"\n=== t={elapsed:4.0f}s  FASE {idx + 1}: {tag} ===")

            ts_ns = time.time_ns()
            payload_bytes = os.urandom(payload)
            cs = crc16(payload_bytes)

            for path in (1, 2):
                with state.lock:
                    on = state.enabled[path]
                if not on:
                    continue

                if random.random() < loss[path - 1]:
                    dropped[path] += 1
                    continue

                wire = payload_bytes
                if random.random() < corrupt[path - 1]:
                    bad = bytearray(payload_bytes)
                    bad[0] ^= 0xFF  # ødelæg én byte -> CRC fejler hos receiver
                    wire = bytes(bad)
                    corrupted[path] += 1

                packet = struct.pack(HDR_FMT, seq, session_id, ts_ns, path, ttl, cs) + wire

                if random.random() < reorder[path - 1] and holdback[path] is None:
                    holdback[path] = packet      # gem nuværende, send senere
                    reordered[path] += 1
                    continue
                if holdback[path] is not None:
                    send_now(path, packet)           # send den nye FØRST
                    send_now(path, holdback[path])   # derefter den gamle -> ude af rækkefølge
                    holdback[path] = None
                    continue

                send_now(path, packet)

            seq += 1
            next_send += interval

            if now - last_log >= 1.0:
                last_log = now
                with state.lock:
                    en = dict(state.enabled)
                extra = ""
                if any(loss):
                    extra += f" dropped={dropped[1]}/{dropped[2]}"
                if any(corrupt):
                    extra += f" corrupted={corrupted[1]}/{corrupted[2]}"
                if any(reorder):
                    extra += f" reordered={reordered[1]}/{reordered[2]}"
                print(f"[t={elapsed:4.0f}s] seq={seq:5d}  "
                      f"Wi-Fi={'ON ' if en[1] else 'OFF'} 5G={'ON ' if en[2] else 'OFF'}  "
                      f"sent={sent[1]}/{sent[2]}{extra}")

    except KeyboardInterrupt:
        pass
    finally:
        for sk in socks.values():
            sk.close()
        print("\n" + "-" * 64)
        print(f"FÆRDIG ({title}). Logiske pakker: {seq}")
        for p in (1, 2):
            print(f"  {LABELS[p]}: sendt={sent[p]} dropped={dropped[p]} "
                  f"corrupt={corrupted[p]} reordered={reordered[p]}")
        print("  Aflæs path- og MERGED-tal på dashboardet.")
        print("-" * 64)
