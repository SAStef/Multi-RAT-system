import socket as s
import struct
import time
import select
import threading
import collections

import matplotlib.pyplot as plt
import matplotlib.animation as animation

PORT1 = 6967
PORT2 = 6968
HDR_FMT = "!IIQB3x"
HDR_SIZE = struct.calcsize(HDR_FMT)  # 20 bytes

sock1 = s.socket(s.AF_INET, s.SOCK_DGRAM)
sock2 = s.socket(s.AF_INET, s.SOCK_DGRAM)
sock1.bind(("0.0.0.0", PORT1))
sock2.bind(("0.0.0.0", PORT2))
print(f"Listening on UDP {PORT1} (path 1) and {PORT2} (path 2)...\n")

HISTORY = 60  # seconds of data visible in plot

def new_path_metrics():
    return {
        "received":        0,
        "last_seq":        -1,
        "lost":            0,
        "last_latency":    None,
        "bytes":           0,
        "start_time":      None,
        # Rolling 1-second buckets for the plot
        "latency_hist":    collections.deque(maxlen=200),
        "jitter_hist":     collections.deque(maxlen=200),
        "throughput_hist": collections.deque(maxlen=200),
        # Time-series data for the live plot (one point per second)
        "ts":              collections.deque(maxlen=HISTORY),
        "plot_latency":    collections.deque(maxlen=HISTORY),
        "plot_jitter":     collections.deque(maxlen=HISTORY),
        "plot_throughput": collections.deque(maxlen=HISTORY),
        "plot_loss":       collections.deque(maxlen=HISTORY),
    }

metrics = {1: new_path_metrics(), 2: new_path_metrics()}
metrics_lock = threading.Lock()
t0 = time.perf_counter()  # reference time for x-axis


# ── Aggregator thread (1 s buckets → plot series) ─────────────────────────────

def aggregator_thread():
    while True:
        time.sleep(1.0)
        with metrics_lock:
            now = time.perf_counter() - t0
            for path, m in metrics.items():
                if not m["latency_hist"]:
                    continue
                avg_lat = sum(m["latency_hist"]) / len(m["latency_hist"])
                avg_jit = sum(m["jitter_hist"])  / len(m["jitter_hist"])
                avg_thr = sum(m["throughput_hist"]) / len(m["throughput_hist"])
                total   = m["received"] + m["lost"]
                loss    = m["lost"] / total * 100 if total else 0.0

                m["ts"].append(now)
                m["plot_latency"].append(avg_lat)
                m["plot_jitter"].append(avg_jit)
                m["plot_throughput"].append(avg_thr)
                m["plot_loss"].append(loss)

                m["latency_hist"].clear()
                m["jitter_hist"].clear()
                m["throughput_hist"].clear()

                print(
                    f"[Path {path} — 1s avg]  latency={avg_lat:.2f}ms  "
                    f"jitter={avg_jit:.2f}ms  throughput={avg_thr:.2f}kbps  "
                    f"loss={loss:.1f}%"
                )

threading.Thread(target=aggregator_thread, daemon=True).start()


# ── UDP receiver thread ────────────────────────────────────────────────────────

def update_metrics(path, seq, latency_ms, payload_size):
    m = metrics[path]
    m["received"] += 1
    if m["last_seq"] >= 0 and seq > m["last_seq"] + 1:
        m["lost"] += seq - m["last_seq"] - 1
    m["last_seq"] = seq

    jitter_ms = abs(latency_ms - m["last_latency"]) if m["last_latency"] is not None else 0.0
    m["last_latency"] = latency_ms

    m["bytes"] += payload_size
    now = time.perf_counter()
    if m["start_time"] is None:
        m["start_time"] = now
    elapsed = now - m["start_time"]
    throughput_kbps = (m["bytes"] * 8 / 1000) / elapsed if elapsed > 0 else 0.0

    m["latency_hist"].append(latency_ms)
    m["jitter_hist"].append(jitter_ms)
    m["throughput_hist"].append(throughput_kbps)


def receiver_thread():
    try:
        while True:
            readable, _, _ = select.select([sock1, sock2], [], [], 1.0)
            for sock in readable:
                data, addr = sock.recvfrom(4096)
                if len(data) < HDR_SIZE:
                    print(f"[{addr}] Packet too short ({len(data)} bytes), skipping")
                    continue
                seq, session_id, ts_ns, path = struct.unpack(HDR_FMT, data[:HDR_SIZE])
                payload = data[HDR_SIZE:]
                latency_ms = (time.time_ns() - ts_ns) / 1_000_000
                with metrics_lock:
                    update_metrics(path, seq, latency_ms, len(payload))
    except Exception as e:
        print(f"Receiver error: {e}")
    finally:
        sock1.close()
        sock2.close()
        print("Sockets closed")

threading.Thread(target=receiver_thread, daemon=True).start()


# ── Live plot ──────────────────────────────────────────────────────────────────

COLORS = {1: ("#4C9BE8", "#F5A623"), 2: ("#50C878", "#E05C5C")}

fig, axes = plt.subplots(4, 1, figsize=(11, 9), sharex=True)
fig.suptitle("UDP Path Monitor — live", fontsize=13, fontweight="bold")

ax_lat, ax_jit, ax_thr, ax_loss = axes
ax_lat.set_ylabel("Latency (ms)")
ax_jit.set_ylabel("Jitter (ms)")
ax_thr.set_ylabel("Throughput (kbps)")
ax_loss.set_ylabel("Loss (%)")
ax_loss.set_xlabel("Time (s)")
ax_loss.set_ylim(-1, 105)

lines = {}
for path in (1, 2):
    c1, c2 = COLORS[path]
    lines[path] = {
        "lat":  ax_lat.plot([], [], color=c1, lw=1.5, label=f"Path {path}")[0],
        "jit":  ax_jit.plot([], [], color=c1, lw=1.5, label=f"Path {path}")[0],
        "thr":  ax_thr.plot([], [], color=c1, lw=1.5, label=f"Path {path}")[0],
        "loss": ax_loss.plot([], [], color=c2, lw=1.5, label=f"Path {path}", drawstyle="steps-post")[0],
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
            ts  = list(m["ts"])
            lines[path]["lat"].set_data(ts, list(m["plot_latency"]))
            lines[path]["jit"].set_data(ts, list(m["plot_jitter"]))
            lines[path]["thr"].set_data(ts, list(m["plot_throughput"]))
            lines[path]["loss"].set_data(ts, list(m["plot_loss"]))

    # Auto-scale x to last HISTORY seconds
    now = time.perf_counter() - t0
    for ax in axes:
        ax.set_xlim(max(0, now - HISTORY), now + 2)
        ax.relim()
        ax.autoscale_view(scalex=False)

    return [l for p in lines.values() for l in p.values()]


ani = animation.FuncAnimation(fig, animate, interval=1000, blit=False, cache_frame_data=False)

try:
    plt.show()
except KeyboardInterrupt:
    pass
finally:
    print("\n--- Final summary ---")
    for path, m in metrics.items():
        total = m["received"] + m["lost"]
        loss_pct = (m["lost"] / total * 100) if total > 0 else 0.0
        print(f"  Path {path}: received={m['received']}  lost={m['lost']} ({loss_pct:.1f}%)")