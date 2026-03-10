"""
Multi-RAT Receiver — FRER (IEEE 802.1CB Frame Replication and Elimination)

Thread architecture:
  - receiver_thread(sock)  × N paths  — one dedicated thread per UDP socket,
                                        each radio interface handled in parallel
  - aggregator_thread()               — 1-second tick: compute averages and
                                        feed the live plot series
  - main thread                       — matplotlib event loop

  All shared state is protected by metrics_lock.
  stop_event signals all threads to exit cleanly on Ctrl-C.
"""

import socket
import struct
import time
import threading
import collections
import binascii

import matplotlib.pyplot as plt
import matplotlib.animation as animation

# ── Configuration ──────────────────────────────────────────────────────────────
PORT1 = 6967
PORT2 = 6968

HDR_FMT  = "!IIQBHx"                   # seq(I) session(I) ts_ns(Q) path(B) crc16(H) pad(x)
HDR_SIZE = struct.calcsize(HDR_FMT)    # 20 bytes

HISTORY     = 60    # seconds of data visible in the live plot
FRER_WINDOW = 2000  # sliding window of (session_id, seq) pairs for dedup

PATH_LABELS = {1: "Path 1", 2: "Path 2"}  # update when real interfaces are known

# ── Sockets ────────────────────────────────────────────────────────────────────
sock1 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock1.bind(("0.0.0.0", PORT1))
sock2.bind(("0.0.0.0", PORT2))
print(f"Listening on UDP {PORT1} (path 1) and {PORT2} (path 2)...\n")

# ── CRC helper ─────────────────────────────────────────────────────────────────
def crc16(data: bytes) -> int:
    return binascii.crc_hqx(data, 0xFFFF)

# ── Per-path metrics ───────────────────────────────────────────────────────────
def new_path_metrics() -> dict:
    return {
        # Cumulative counters
        "received":     0,
        "lost":         0,
        "duplicates":   0,
        "crc_errors":   0,
        # State for online calculations
        "last_seq":        -1,
        "last_latency":    None,
        "bytes":           0,
        "start_time":      None,
        # Rolling 1-second buckets (cleared by aggregator each tick)
        "latency_hist":    collections.deque(maxlen=500),
        "jitter_hist":     collections.deque(maxlen=500),
        "throughput_hist": collections.deque(maxlen=500),
        # Time-series fed to the live plot (one point per second)
        "ts":              collections.deque(maxlen=HISTORY),
        "plot_latency":    collections.deque(maxlen=HISTORY),
        "plot_jitter":     collections.deque(maxlen=HISTORY),
        "plot_throughput": collections.deque(maxlen=HISTORY),
        "plot_loss":       collections.deque(maxlen=HISTORY),
    }

metrics      = {1: new_path_metrics(), 2: new_path_metrics()}
metrics_lock = threading.Lock()
t0           = time.perf_counter()  # reference time for the x-axis
stop_event   = threading.Event()    # set on shutdown to stop all threads

# ── FRER deduplication table ───────────────────────────────────────────────────
# Maintains a sliding window of recently seen (session_id, seq) pairs.
# O(1) lookup via the companion set; the deque evicts the oldest entry when full.
_frer_seen: collections.deque = collections.deque(maxlen=FRER_WINDOW)
_frer_set:  set                = set()

def frer_is_duplicate(session_id: int, seq: int) -> bool:
    """Return True if this (session_id, seq) was already delivered (duplicate)."""
    key = (session_id, seq)
    if key in _frer_set:
        return True
    # Window full → evict oldest entry from the set before the deque drops it
    if len(_frer_seen) == FRER_WINDOW:
        _frer_set.discard(_frer_seen[0])
    _frer_seen.append(key)
    _frer_set.add(key)
    return False

# ── Per-path metric update (called for every valid packet, including dupes) ────
def update_metrics(path: int, seq: int, latency_ms: float, payload_size: int):
    m = metrics[path]
    m["received"] += 1

    # Sequence-gap loss estimation
    if m["last_seq"] >= 0 and seq > m["last_seq"] + 1:
        m["lost"] += seq - m["last_seq"] - 1
    m["last_seq"] = seq

    # Jitter — RFC 3550 simple absolute difference
    jitter_ms = abs(latency_ms - m["last_latency"]) if m["last_latency"] is not None else 0.0
    m["last_latency"] = latency_ms

    # Throughput (cumulative bytes → kbps)
    m["bytes"] += payload_size
    now = time.perf_counter()
    if m["start_time"] is None:
        m["start_time"] = now
    elapsed = now - m["start_time"]
    throughput_kbps = (m["bytes"] * 8 / 1000) / elapsed if elapsed > 0 else 0.0

    m["latency_hist"].append(latency_ms)
    m["jitter_hist"].append(jitter_ms)
    m["throughput_hist"].append(throughput_kbps)

# ── Aggregator thread — builds 1-second plot points ───────────────────────────
def aggregator_thread():
    while True:
        time.sleep(1.0)
        with metrics_lock:
            now = time.perf_counter() - t0
            print()
            for path, m in metrics.items():
                if not m["latency_hist"]:
                    continue

                avg_lat = sum(m["latency_hist"]) / len(m["latency_hist"])
                avg_jit = sum(m["jitter_hist"])  / len(m["jitter_hist"])
                avg_thr = sum(m["throughput_hist"]) / len(m["throughput_hist"])
                total   = m["received"] + m["lost"]
                loss    = m["lost"] / total * 100 if total else 0.0

                # Append to time-series
                m["ts"].append(now)
                m["plot_latency"].append(avg_lat)
                m["plot_jitter"].append(avg_jit)
                m["plot_throughput"].append(avg_thr)
                m["plot_loss"].append(loss)

                # Clear rolling buckets for next second
                m["latency_hist"].clear()
                m["jitter_hist"].clear()
                m["throughput_hist"].clear()

                print(
                    f"[{PATH_LABELS[path]}]  "
                    f"latency={avg_lat:7.2f} ms  "
                    f"jitter={avg_jit:6.2f} ms  "
                    f"throughput={avg_thr:8.2f} kbps  "
                    f"loss={loss:5.1f}%  "
                    f"dupes={m['duplicates']}  "
                    f"crc_err={m['crc_errors']}"
                )

threading.Thread(target=aggregator_thread, daemon=True).start()

# ── UDP receiver thread — one instance per socket/path ────────────────────────
def receiver_thread(sock: socket.socket):
    """Dedicated receive loop for a single UDP socket (= one radio path).

    Runs independently so each radio interface is handled in parallel.
    Blocks on recvfrom with a 1-second timeout so it can check stop_event
    and exit cleanly when the program shuts down.
    """
    sock.settimeout(1.0)
    try:
        while not stop_event.is_set():
            try:
                data, addr = sock.recvfrom(65535)
            except TimeoutError:
                continue  # no data — check stop_event and loop

            if len(data) < HDR_SIZE:
                print(f"[{addr}] Packet too short ({len(data)} bytes), skipping")
                continue

            seq, session_id, ts_ns, path, cs = struct.unpack(HDR_FMT, data[:HDR_SIZE])
            payload = data[HDR_SIZE:]

            # ── CRC validation ─────────────────────────────────────────────────
            if crc16(payload) != cs:
                with metrics_lock:
                    if path in metrics:
                        metrics[path]["crc_errors"] += 1
                print(f"[Path {path}] BAD CRC seq={seq} from={addr}")
                continue

            latency_ms = (time.time_ns() - ts_ns) / 1_000_000

            with metrics_lock:
                # Record per-path metrics for ALL valid packets (including dupes)
                # so both radio paths are visible in the comparison plot
                if path in metrics:
                    update_metrics(path, seq, latency_ms, len(payload))

                # ── FRER elimination ───────────────────────────────────────────
                if frer_is_duplicate(session_id, seq):
                    if path in metrics:
                        metrics[path]["duplicates"] += 1
                    # Drop — do not deliver to application layer
                    continue

            # First copy → delivered to application layer
            # (extend here with forwarding, buffering, etc.)

    except Exception as exc:
        print(f"Receiver error on path: {exc}")
    finally:
        sock.close()

# Start one dedicated thread per socket so each path runs in parallel
threading.Thread(target=receiver_thread, args=(sock1,), name="rx-path1", daemon=True).start()
threading.Thread(target=receiver_thread, args=(sock2,), name="rx-path2", daemon=True).start()

# ── Live plot ──────────────────────────────────────────────────────────────────
COLORS = {
    1: {"line": "#4C9BE8", "loss": "#F5A623"},
    2: {"line": "#50C878", "loss": "#E05C5C"},
}

fig, axes = plt.subplots(4, 1, figsize=(12, 9), sharex=True)
fig.suptitle("Multi-RAT — FRER Live Monitor", fontsize=13, fontweight="bold")

ax_lat, ax_jit, ax_thr, ax_loss = axes
ax_lat.set_ylabel("Latency (ms)")
ax_jit.set_ylabel("Jitter (ms)")
ax_thr.set_ylabel("Throughput (kbps)")
ax_loss.set_ylabel("Loss (%)")
ax_loss.set_xlabel("Time (s)")
ax_loss.set_ylim(-1, 105)

plot_lines = {}
for path in (1, 2):
    c  = COLORS[path]["line"]
    cl = COLORS[path]["loss"]
    lbl = PATH_LABELS[path]
    plot_lines[path] = {
        "lat":  ax_lat.plot([], [], color=c,  lw=1.5, label=lbl)[0],
        "jit":  ax_jit.plot([], [], color=c,  lw=1.5, label=lbl)[0],
        "thr":  ax_thr.plot([], [], color=c,  lw=1.5, label=lbl)[0],
        "loss": ax_loss.plot([], [], color=cl, lw=1.5, label=lbl, drawstyle="steps-post")[0],
    }

for ax in axes:
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(True, alpha=0.3)

plt.tight_layout()


def animate(_frame):
    with metrics_lock:
        for path, m in metrics.items():
            if not m["ts"]:
                continue
            ts = list(m["ts"])
            plot_lines[path]["lat"].set_data(ts, list(m["plot_latency"]))
            plot_lines[path]["jit"].set_data(ts, list(m["plot_jitter"]))
            plot_lines[path]["thr"].set_data(ts, list(m["plot_throughput"]))
            plot_lines[path]["loss"].set_data(ts, list(m["plot_loss"]))

    now = time.perf_counter() - t0
    for ax in axes:
        ax.set_xlim(max(0, now - HISTORY), now + 2)
        ax.relim()
        ax.autoscale_view(scalex=False)

    return [line for p in plot_lines.values() for line in p.values()]


ani = animation.FuncAnimation(
    fig, animate, interval=1000, blit=False, cache_frame_data=False
)

try:
    plt.show()
except KeyboardInterrupt:
    pass
finally:
    stop_event.set()  # signal all receiver threads to exit
    print("\n--- Final summary ---")
    for path, m in metrics.items():
        total    = m["received"] + m["lost"]
        loss_pct = (m["lost"] / total * 100) if total > 0 else 0.0
        print(
            f"  {PATH_LABELS[path]}: "
            f"received={m['received']}  "
            f"lost={m['lost']} ({loss_pct:.1f}%)  "
            f"duplicates={m['duplicates']}  "
            f"crc_errors={m['crc_errors']}"
        )
