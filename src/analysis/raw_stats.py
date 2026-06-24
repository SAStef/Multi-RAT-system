# makes the stats and boxplots from the raw packet logs (packets_*.csv)
import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import _style
_style.apply()

GROUPS = [(name, _style.STREAM_COLORS[name]) for name in ("Wi-Fi", "5G/LTE", "Merged")]
STAT_COLS = ("count", "mean", "std", "min", "p5", "q1",
             "median", "q3", "p95", "max")


def load_rows(csv_path: Path):
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))


def _to_float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def latency_groups(rows):
    out = {name: [] for name, _ in GROUPS}
    for r in rows:
        if r.get("crc_ok") != "1":
            continue
        lat = _to_float(r.get("latency_ms"))
        if lat is None:
            continue
        if r.get("path") == "1":
            out["Wi-Fi"].append(lat)
        elif r.get("path") == "2":
            out["5G/LTE"].append(lat)
        if r.get("is_duplicate") == "0":
            out["Merged"].append(lat)
    return {k: np.array(v, dtype=float) for k, v in out.items()}


def describe(x: np.ndarray):
    if x.size == 0:
        return {c: float("nan") for c in STAT_COLS} | {"count": 0}
    return {
        "count": int(x.size),
        "mean": float(np.mean(x)),
        "std": float(np.std(x, ddof=1)) if x.size > 1 else 0.0,
        "min": float(np.min(x)),
        "p5": float(np.percentile(x, 5)),
        "q1": float(np.percentile(x, 25)),
        "median": float(np.median(x)),
        "q3": float(np.percentile(x, 75)),
        "p95": float(np.percentile(x, 95)),
        "max": float(np.max(x)),
    }


def path_counts(rows):
    summary = {}
    for p in ("1", "2"):
        prows = [r for r in rows if r.get("path") == p]
        seqs = {}
        for r in prows:
            sid = r.get("session_id")
            seq = _to_float(r.get("seq"))
            if seq is None:
                continue
            seqs.setdefault(sid, set()).add(int(seq))
        span = lost = 0
        for s in seqs.values():
            span += max(s) - min(s) + 1
            lost += (max(s) - min(s) + 1) - len(s)
        dupes = sum(1 for r in prows if r.get("is_duplicate") == "1")
        crc_err = sum(1 for r in prows if r.get("crc_ok") == "0")
        summary[p] = {
            "arrived": len(prows),
            "lost": lost,
            "loss_pct": (100.0 * lost / span) if span else 0.0,
            "duplicates": dupes,
            "crc_errors": crc_err,
        }
    return summary


def write_stats(groups, counts, out: Path, name: str):
    path = out / f"{name}_latency_stats.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stream", *STAT_COLS])
        for g, _ in GROUPS:
            d = describe(groups[g])
            w.writerow([g] + [d[c] if c == "count" else round(d[c], 3) for c in STAT_COLS])
        w.writerow([])
        w.writerow(["path", "arrived", "lost", "loss_pct", "duplicates", "crc_errors"])
        for p in ("1", "2"):
            c = counts[p]
            w.writerow([p, c["arrived"], c["lost"], round(c["loss_pct"], 3),
                        c["duplicates"], c["crc_errors"]])


    print(f"\n--- {name} ---")
    print(f"{'stream':<8}{'n':>7}{'mean':>9}{'median':>9}{'p95':>9}{'std':>9}  [ms]")
    for g, _ in GROUPS:
        d = describe(groups[g])
        if d["count"]:
            print(f"{g:<8}{d['count']:>7}{d['mean']:>9.2f}{d['median']:>9.2f}"
                  f"{d['p95']:>9.2f}{d['std']:>9.2f}")
        else:
            print(f"{g:<8}{0:>7}{'--':>9}{'--':>9}{'--':>9}{'--':>9}")
    for p in ("1", "2"):
        c = counts[p]
        print(f"  path {p}: arrived={c['arrived']} lost={c['lost']} "
              f"({c['loss_pct']:.2f}%) dupes={c['duplicates']} crc_err={c['crc_errors']}")
    print(f"  stats table -> {path}")


def write_latex(groups, counts, out: Path, name: str):
    label = name.replace(".", "_")
    rows = []
    for g, _ in GROUPS:
        d = describe(groups[g])
        if d["count"]:
            rows.append(f"    {g} & {d['count']} & {d['mean']:.2f} & {d['median']:.2f} "
                        f"& {d['p95']:.2f} & {d['std']:.2f} \\\\")
        else:
            rows.append(f"    {g} & 0 & -- & -- & -- & -- \\\\")
    tex = (
        "\\begin{table}[H]\n"
        "  \\centering\n"
        "  \\caption{Per-packet latency distribution [ms].}\n"
        f"  \\label{{tab:{label}_latency}}\n"
        "  \\begin{tabular}{lrrrrr}\n"
        "    \\toprule\n"
        "    Stream & $n$ & Mean & Median & p95 & Std \\\\\n"
        "    \\midrule\n"
        + "\n".join(rows) + "\n"
        "    \\bottomrule\n"
        "  \\end{tabular}\n"
        "\\end{table}\n"
    )
    (out / f"{name}_latency_stats.tex").write_text(tex)
    print(f"  LaTeX table -> {out / (name + '_latency_stats.tex')}")


# cut off the few huge clock-offset spikes so the plots arent squished
def robust_limits(arrays, lo_pct=0.0, hi_pct=99.0, pad_frac=0.05):
    allv = np.concatenate([np.asarray(a) for a in arrays if len(a)])
    if allv.size == 0:
        return None
    lo = np.percentile(allv, lo_pct)
    hi = np.percentile(allv, hi_pct)
    pad = max((hi - lo) * pad_frac, 0.5)
    return lo - pad, hi + pad


def plot_boxplot(groups, out: Path, name: str):
    labels = [g for g, _ in GROUPS if groups[g].size]
    data = [groups[g] for g, _ in GROUPS if groups[g].size]
    colors = [c for g, c in GROUPS if groups[g].size]
    if not data:
        print(f"[skip boxplot] {name}: no valid latency samples")
        return
    fig, ax = plt.subplots(figsize=(6, 4))
    bp = ax.boxplot(data, showfliers=True, patch_artist=True, widths=0.6,
                    medianprops=dict(color="black", linewidth=1.6),
                    whiskerprops=dict(color="0.4"),
                    capprops=dict(color="0.4"),
                    flierprops=dict(marker="o", markersize=3, alpha=0.35,
                                    markerfacecolor="0.5", markeredgecolor="none"))
    ax.set_xticks(range(1, len(labels) + 1))
    ax.set_xticklabels(labels)
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.6)
        patch.set_edgecolor(color)
    ax.set_ylabel("Latency [ms]")
    ax.set_title(f"Latency distribution — {name}")
    ax.grid(axis="x", visible=False)
    fig.tight_layout()
    fig.savefig(out / f"{name}_latency_boxplot.pdf")
    plt.close(fig)
    print(f"  boxplot -> {out / (name + '_latency_boxplot.pdf')}")


def plot_cdf(groups, out: Path, name: str):
    if not any(groups[g].size for g, _ in GROUPS):
        print(f"[skip cdf] {name}: no valid latency samples")
        return
    fig, ax = plt.subplots(figsize=(6, 4))
    for g, color in GROUPS:
        x = np.sort(groups[g])
        if x.size:
            ax.plot(x, np.arange(1, x.size + 1) / x.size, color=color, label=g)
    ax.set_xlabel("Latency [ms]")
    ax.set_ylabel("CDF")
    ax.set_ylim(0, 1)


    lim = robust_limits([groups[g] for g, _ in GROUPS], hi_pct=99.5)
    if lim:
        ax.set_xlim(*lim)
    ax.set_title(f"Latency CDF — {name}")
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(out / f"{name}_latency_cdf.pdf")
    plt.close(fig)
    print(f"  cdf -> {out / (name + '_latency_cdf.pdf')}")


def plot_hist(groups, out: Path, name: str):
    if not any(groups[g].size for g, _ in GROUPS):
        print(f"[skip hist] {name}: no valid latency samples")
        return


    lim = robust_limits([groups[g] for g, _ in GROUPS], hi_pct=99.0)
    fig, ax = plt.subplots(figsize=(6, 4))
    for g, color in GROUPS:
        x = groups[g]
        if x.size:
            ax.hist(x, bins=40, range=lim, alpha=0.5, color=color, label=g,
                    edgecolor="none")
    ax.set_xlabel("Latency [ms]")
    ax.set_ylabel("Packets")
    if lim:
        ax.set_xlim(*lim)
    ax.set_title(f"Latency histogram — {name}")
    ax.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(out / f"{name}_latency_hist.pdf")
    plt.close(fig)
    print(f"  hist -> {out / (name + '_latency_hist.pdf')}")


def plot_timeline(rows, out: Path, name: str):
    lat = {g: ([], []) for g, _ in GROUPS}
    seqs = {g: [] for g, _ in GROUPS}
    times = []
    for r in rows:
        t = _to_float(r.get("time"))
        if t is None:
            continue
        times.append(t)
        if r.get("crc_ok") == "1":
            l = _to_float(r.get("latency_ms"))
            if l is not None:
                if r.get("path") == "1":
                    lat["Wi-Fi"][0].append(t); lat["Wi-Fi"][1].append(l)
                elif r.get("path") == "2":
                    lat["5G/LTE"][0].append(t); lat["5G/LTE"][1].append(l)
                if r.get("is_duplicate") == "0":
                    lat["Merged"][0].append(t); lat["Merged"][1].append(l)
        s = _to_float(r.get("seq"))
        if s is not None:
            s = int(s)
            if r.get("path") == "1":
                seqs["Wi-Fi"].append((t, s))
            elif r.get("path") == "2":
                seqs["5G/LTE"].append((t, s))
            if r.get("is_duplicate") == "0":
                seqs["Merged"].append((t, s))

    if not times:
        print(f"[skip timeline] {name}: no samples")
        return

    t0 = min(times)

    def loss_series(pairs, bin_s=1.0):
        bins = {}
        for t, s in pairs:
            bins.setdefault(int((t - t0) // bin_s), set()).add(s)
        xs, ys = [], []
        for b in sorted(bins):
            ss = bins[b]
            span = max(ss) - min(ss) + 1
            xs.append(b * bin_s)
            ys.append(100.0 * (span - len(ss)) / span if span > 0 else 0.0)
        return np.array(xs), np.array(ys)

    def break_gaps(x, y, max_dt):
        # put a nan where the sender was stopped so the line isnt drawn over the gap
        x = np.asarray(x, float); y = np.asarray(y, float)
        if x.size < 2:
            return x, y
        for i in np.where(np.diff(x) > max_dt)[0][::-1]:
            x = np.insert(x, i + 1, np.nan)
            y = np.insert(y, i + 1, np.nan)
        return x, y

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    for g, color in GROUPS:
        t, l = lat[g]
        if t:
            order = np.argsort(t)
            x, y = break_gaps(np.asarray(t)[order] - t0, np.asarray(l)[order], 1.0)
            ax1.plot(x, y, color=color, linewidth=0.8, label=g)
    for g, color in GROUPS:
        if seqs[g]:
            x, y = loss_series(seqs[g])
            x, y = break_gaps(x, y, 2.0)
            ax2.plot(x, y, color=color, linewidth=1.2, label=g)
    ax1.set_ylabel("Latency [ms]")
    ax2.set_ylabel("Loss [%]")
    ax2.set_xlabel("Time [s]")
    ax2.set_ylim(bottom=0)


    lim = robust_limits([lat[g][1] for g, _ in GROUPS], hi_pct=99.5)
    if lim:
        ax1.set_ylim(*lim)
    ax1.set_title(f"Latency and loss over time — {name}")
    ax1.legend(loc="upper right", ncol=3)
    fig.tight_layout()
    fig.savefig(out / f"{name}_timeline.pdf")
    plt.close(fig)
    print(f"  timeline -> {out / (name + '_timeline.pdf')}")


# find packets that arrived on both paths so we can compare the two latencies
def paired_by_seq(rows):
    sess = Counter(r.get("session_id") for r in rows if r.get("crc_ok") == "1")
    if not sess:
        return []
    sid = sess.most_common(1)[0][0]
    lat = {1: {}, 2: {}}
    for r in rows:
        if r.get("session_id") != sid or r.get("crc_ok") != "1":
            continue
        p = r.get("path")
        seq = _to_float(r.get("seq"))
        l = _to_float(r.get("latency_ms"))
        if p in ("1", "2") and seq is not None and l is not None:
            lat[int(p)].setdefault(int(seq), l)
    common = sorted(set(lat[1]) & set(lat[2]))
    return [(s, lat[1][s], lat[2][s]) for s in common]


def _style_stem(container, color, markersize=6, fill=False):
    markerline, stemlines, baseline = container
    plt.setp(markerline, color=color, markersize=markersize,
             markerfacecolor=(color if fill else "none"),
             markeredgecolor=("white" if fill else color),
             markeredgewidth=(0.7 if fill else 1.0))
    plt.setp(stemlines, color=color, linewidth=(0.9 if fill else 1.0),
             alpha=(0.4 if fill else 1.0))
    plt.setp(baseline, visible=False)


def plot_latency_stem(rows, out: Path, name: str, window: int):
    paired = paired_by_seq(rows)[:window]
    if not paired:
        print(f"[skip latency-stem] {name}: no packets seen on both paths")
        return
    x = np.arange(1, len(paired) + 1)
    l1 = [a for _, a, _ in paired]
    l2 = [b for _, _, b in paired]
    fig, ax = plt.subplots(figsize=(9, 4))
    _style_stem(ax.stem(x - 0.15, l1, basefmt=" "), _style.STREAM_COLORS["Wi-Fi"], fill=True)
    _style_stem(ax.stem(x + 0.15, l2, basefmt=" "), _style.STREAM_COLORS["5G/LTE"], fill=True)
    ax.plot([], [], "o-", color=_style.STREAM_COLORS["Wi-Fi"],
            markerfacecolor=_style.STREAM_COLORS["Wi-Fi"], markeredgecolor="white",
            label="Stream 1 (Wi-Fi)")
    ax.plot([], [], "o-", color=_style.STREAM_COLORS["5G/LTE"],
            markerfacecolor=_style.STREAM_COLORS["5G/LTE"], markeredgecolor="white",
            label="Stream 2 (5G/LTE)")
    ax.set_xlabel("Packet [-]")
    ax.set_ylabel("Latency [ms]")


    lo, hi = min(l1 + l2), max(l1 + l2)
    pad = max((hi - lo) * 0.1, 1.0)
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_title(f"Per-packet latency — {name}")
    ax.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(out / f"{name}_latency_stem.pdf")
    plt.close(fig)
    print(f"  latency-stem -> {out / (name + '_latency_stem.pdf')}")


def plot_diffdelay_stem(rows, out: Path, name: str, window: int):
    paired = paired_by_seq(rows)[:window]
    if not paired:
        print(f"[skip diff-delay] {name}: no packets seen on both paths")
        return
    x = np.arange(1, len(paired) + 1)
    dd = [abs(a - b) for _, a, b in paired]
    fig, ax = plt.subplots(figsize=(9, 4))
    _style_stem(ax.stem(x, dd, basefmt=" "), "#5E35B1", fill=True)
    ax.plot([], [], "o-", color="#5E35B1", markerfacecolor="#5E35B1",
            markeredgecolor="white", label="Wi-Fi vs 5G/LTE differential delay")
    ax.set_xlabel("Packet [-]")
    ax.set_ylabel("Differential delay [ms]")
    ax.set_ylim(bottom=0)
    ax.set_title(f"Inter-path differential delay — {name}")
    ax.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(out / f"{name}_diffdelay_stem.pdf")
    plt.close(fig)
    print(f"  diff-delay -> {out / (name + '_diffdelay_stem.pdf')}")


def _t0(rows):
    times = [_to_float(r.get("time")) for r in rows]
    times = [t for t in times if t is not None]
    return min(times) if times else 0.0


def write_perpacket(rows, out: Path, name: str):
    # full per-packet log, time zeroed, with stream name + merged flag
    t0 = _t0(rows)
    path = out / f"{name}_perpacket.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "path", "stream", "session_id", "seq",
                    "latency_ms", "hops", "crc_ok", "is_duplicate", "in_merged"])
        for r in rows:
            t = _to_float(r.get("time"))
            ts = round(t - t0, 4) if t is not None else ""
            p = r.get("path")
            stream = "Wi-Fi" if p == "1" else ("5G/LTE" if p == "2" else "")
            in_merged = 1 if (r.get("crc_ok") == "1" and r.get("is_duplicate") == "0") else 0
            w.writerow([ts, p, stream, r.get("session_id"), r.get("seq"),
                        r.get("latency_ms"), r.get("hops"), r.get("crc_ok"),
                        r.get("is_duplicate"), in_merged])
    print(f"  per-packet raw -> {path}")


def write_latency_timeseries(rows, out: Path, name: str):
    # latency per packet over time, merged counted as its own stream
    t0 = _t0(rows)
    path = out / f"{name}_latency_timeseries.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "stream", "session_id", "seq", "latency_ms"])
        for r in rows:
            if r.get("crc_ok") != "1":
                continue
            t = _to_float(r.get("time"))
            l = _to_float(r.get("latency_ms"))
            if t is None or l is None:
                continue
            ts = round(t - t0, 4)
            sid = r.get("session_id")
            seq = r.get("seq")
            p = r.get("path")
            if p == "1":
                w.writerow([ts, "Wi-Fi", sid, seq, round(l, 4)])
            elif p == "2":
                w.writerow([ts, "5G/LTE", sid, seq, round(l, 4)])
            if r.get("is_duplicate") == "0":
                w.writerow([ts, "Merged", sid, seq, round(l, 4)])
    print(f"  latency timeseries -> {path}")


def write_latency_cdf(groups, out: Path, name: str):
    # sorted latency + cdf value per stream
    path = out / f"{name}_latency_cdf.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stream", "latency_ms", "cdf"])
        for g, _ in GROUPS:
            x = np.sort(groups[g])
            n = x.size
            for i, v in enumerate(x, 1):
                w.writerow([g, round(float(v), 4), round(i / n, 6)])
    print(f"  latency CDF data -> {path}")


def write_paired_latency(rows, out: Path, name: str):
    # packets that arrived on both paths + their delay difference
    path = out / f"{name}_paired_latency.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["seq", "wifi_ms", "lte_ms", "diff_delay_ms"])
        for s, a, b in paired_by_seq(rows):
            w.writerow([s, round(a, 4), round(b, 4), round(abs(a - b), 4)])
    print(f"  paired latency -> {path}")


def write_loss_timeseries(rows, out: Path, name: str, bin_s: float = 1.0):
    # packet loss per second for each stream
    t0 = _t0(rows)
    streams = {g: {} for g, _ in GROUPS}

    def add(stream, b, s):
        streams[stream].setdefault(b, set()).add(s)

    for r in rows:
        t = _to_float(r.get("time"))
        s = _to_float(r.get("seq"))
        if t is None or s is None:
            continue
        b = int((t - t0) // bin_s)
        s = int(s)
        p = r.get("path")
        if p == "1":
            add("Wi-Fi", b, s)
        elif p == "2":
            add("5G/LTE", b, s)
        if r.get("is_duplicate") == "0":
            add("Merged", b, s)

    def loss_pct(ss):
        span = max(ss) - min(ss) + 1
        return 100.0 * (span - len(ss)) / span if span > 0 else 0.0

    allbins = sorted({b for st in streams.values() for b in st})
    path = out / f"{name}_loss_timeseries.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "wifi_loss_pct", "lte_loss_pct", "merged_loss_pct"])
        for b in allbins:
            vals = []
            for g, _ in GROUPS:
                vals.append(round(loss_pct(streams[g][b]), 4) if b in streams[g] else "")
            w.writerow([round(b * bin_s, 3), *vals])
    print(f"  loss timeseries -> {path}")


def write_jitter(rows, out: Path, name: str):
    # jitter per packet. the latency diff cancels the constant clock offset.
    # rfc3550_jitter_ms is the RFC 3550 running estimate
    t0 = _t0(rows)
    streams = {g: [] for g, _ in GROUPS}
    for r in rows:
        if r.get("crc_ok") != "1":
            continue
        l = _to_float(r.get("latency_ms"))
        seq = _to_float(r.get("seq"))
        t = _to_float(r.get("time"))
        if l is None or seq is None:
            continue
        rec = (int(seq), t, l)
        p = r.get("path")
        if p == "1":
            streams["Wi-Fi"].append(rec)
        elif p == "2":
            streams["5G/LTE"].append(rec)
        if r.get("is_duplicate") == "0":
            streams["Merged"].append(rec)
    path = out / f"{name}_jitter.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stream", "seq", "time_s", "latency_ms",
                    "delta_latency_ms", "abs_delta_ms", "rfc3550_jitter_ms"])
        for g, _ in GROUPS:
            recs = sorted(streams[g], key=lambda x: x[0])
            prev_l = None
            J = 0.0
            for seq, t, l in recs:
                if prev_l is None:
                    delta = 0.0
                else:
                    delta = l - prev_l
                    J += (abs(delta) - J) / 16.0
                ts = round(t - t0, 4) if t is not None else ""
                w.writerow([g, seq, ts, round(l, 4), round(delta, 4),
                            round(abs(delta), 4), round(J, 4)])
                prev_l = l
    print(f"  jitter -> {path}")


def write_interarrival(rows, out: Path, name: str):
    # ms between consecutive arrivals on each stream
    t0 = _t0(rows)
    streams = {g: [] for g, _ in GROUPS}
    for r in rows:
        t = _to_float(r.get("time"))
        if t is None:
            continue
        seq = _to_float(r.get("seq"))
        rec = (t, int(seq) if seq is not None else "")
        p = r.get("path")
        if p == "1":
            streams["Wi-Fi"].append(rec)
        elif p == "2":
            streams["5G/LTE"].append(rec)
        if r.get("is_duplicate") == "0":
            streams["Merged"].append(rec)
    path = out / f"{name}_interarrival.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stream", "seq", "time_s", "interarrival_ms"])
        for g, _ in GROUPS:
            recs = sorted(streams[g], key=lambda x: x[0])
            prev_t = None
            for t, seq in recs:
                ia = "" if prev_t is None else round((t - prev_t) * 1000.0, 4)
                w.writerow([g, seq, round(t - t0, 4), ia])
                prev_t = t
    print(f"  inter-arrival -> {path}")


def write_throughput(rows, out: Path, name: str, bin_s: float = 1.0):
    # packets per second on each stream
    t0 = _t0(rows)
    streams = {g: Counter() for g, _ in GROUPS}
    for r in rows:
        t = _to_float(r.get("time"))
        if t is None:
            continue
        b = int((t - t0) // bin_s)
        p = r.get("path")
        if p == "1":
            streams["Wi-Fi"][b] += 1
        elif p == "2":
            streams["5G/LTE"][b] += 1
        if r.get("is_duplicate") == "0":
            streams["Merged"][b] += 1
    allbins = sorted({b for c in streams.values() for b in c})
    path = out / f"{name}_throughput.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "wifi_pps", "lte_pps", "merged_pps"])
        for b in allbins:
            w.writerow([round(b * bin_s, 3)] + [streams[g][b] for g, _ in GROUPS])
    print(f"  throughput -> {path}")


# latency vs time for all three streams on one plot
def plot_latency_time(rows, out: Path, name: str):
    t0 = _t0(rows)
    series = {g: ([], []) for g, _ in GROUPS}
    for r in rows:
        if r.get("crc_ok") != "1":
            continue
        t = _to_float(r.get("time"))
        l = _to_float(r.get("latency_ms"))
        if t is None or l is None:
            continue
        ts = t - t0
        p = r.get("path")
        if p == "1":
            series["Wi-Fi"][0].append(ts); series["Wi-Fi"][1].append(l)
        elif p == "2":
            series["5G/LTE"][0].append(ts); series["5G/LTE"][1].append(l)
        if r.get("is_duplicate") == "0":
            series["Merged"][0].append(ts); series["Merged"][1].append(l)
    if not any(series[g][0] for g, _ in GROUPS):
        print(f"[skip latency-time] {name}: no samples")
        return
    fig, ax = plt.subplots(figsize=(9, 4))
    for g, color in GROUPS:
        t, l = series[g]
        if t:
            order = np.argsort(t)
            ax.plot(np.asarray(t)[order], np.asarray(l)[order], color=color,
                    linewidth=0.9, marker="o", markersize=2.5,
                    markerfacecolor=color, markeredgecolor="none", label=g)
    lim = robust_limits([np.asarray(series[g][1]) for g, _ in GROUPS], hi_pct=99.5)
    if lim:
        ax.set_ylim(*lim)
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Latency [ms]")
    ax.set_title(f"Latency over time — {name}")
    ax.legend(loc="upper right", ncol=3)
    fig.tight_layout()
    fig.savefig(out / f"{name}_latency_time.pdf")
    plt.close(fig)
    print(f"  latency-time -> {out / (name + '_latency_time.pdf')}")


# same stem plot but x axis is time instead of packet index
def plot_latency_stem_time(rows, out: Path, name: str, window: int):
    t0 = _t0(rows)
    series = {"Wi-Fi": ([], []), "5G/LTE": ([], [])}
    for r in rows:
        if r.get("crc_ok") != "1":
            continue
        t = _to_float(r.get("time"))
        l = _to_float(r.get("latency_ms"))
        if t is None or l is None:
            continue
        p = r.get("path")
        if p == "1" and len(series["Wi-Fi"][0]) < window:
            series["Wi-Fi"][0].append(t - t0); series["Wi-Fi"][1].append(l)
        elif p == "2" and len(series["5G/LTE"][0]) < window:
            series["5G/LTE"][0].append(t - t0); series["5G/LTE"][1].append(l)
    allv = series["Wi-Fi"][1] + series["5G/LTE"][1]
    if not allv:
        print(f"[skip latency-stem-time] {name}: no samples")
        return
    fig, ax = plt.subplots(figsize=(9, 4))
    for g in ("Wi-Fi", "5G/LTE"):
        t, l = series[g]
        if t:
            _style_stem(ax.stem(t, l, basefmt=" "), _style.STREAM_COLORS[g], fill=True)
    ax.plot([], [], "o-", color=_style.STREAM_COLORS["Wi-Fi"],
            markerfacecolor=_style.STREAM_COLORS["Wi-Fi"], markeredgecolor="white",
            label="Stream 1 (Wi-Fi)")
    ax.plot([], [], "o-", color=_style.STREAM_COLORS["5G/LTE"],
            markerfacecolor=_style.STREAM_COLORS["5G/LTE"], markeredgecolor="white",
            label="Stream 2 (5G/LTE)")
    lo, hi = min(allv), max(allv)
    pad = max((hi - lo) * 0.1, 1.0)
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Latency [ms]")
    ax.set_title(f"Per-packet latency over time — {name}")
    ax.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(out / f"{name}_latency_stem_time.pdf")
    plt.close(fig)
    print(f"  latency-stem-time -> {out / (name + '_latency_stem_time.pdf')}")


def analyse_file(csv_path: Path, out: Path, window: int = 40):
    rows = load_rows(csv_path)
    if not rows:
        print(f"[skip] {csv_path} is empty")
        return
    name = csv_path.stem
    groups = latency_groups(rows)
    counts = path_counts(rows)
    write_stats(groups, counts, out, name)
    write_latex(groups, counts, out, name)
    write_perpacket(rows, out, name)
    write_latency_timeseries(rows, out, name)
    write_latency_cdf(groups, out, name)
    write_paired_latency(rows, out, name)
    write_loss_timeseries(rows, out, name)
    write_jitter(rows, out, name)
    write_interarrival(rows, out, name)
    write_throughput(rows, out, name)
    plot_timeline(rows, out, name)
    plot_boxplot(groups, out, name)
    plot_cdf(groups, out, name)
    plot_hist(groups, out, name)
    plot_latency_stem(rows, out, name, window)
    plot_latency_stem_time(rows, out, name, window)
    plot_latency_time(rows, out, name)
    plot_diffdelay_stem(rows, out, name, window)


def collect(inputs):
    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            yield from sorted(p.glob("packets_*.csv"))
        elif p.is_file():
            yield p
        else:
            print(f"[warn] {inp} not found", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="packets_*.csv files or directories")
    ap.add_argument("--out", default="figures", help="output directory (default: figures/)")
    ap.add_argument("--window", type=int, default=40,
                    help="how many packets to show in the per-packet stem plots (default: 40)")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    files = list(collect(args.inputs))
    if not files:
        sys.exit("no packets_*.csv files found")
    for f in files:
        analyse_file(f, out, args.window)
    print(f"\nWritten to {out.resolve()}")


if __name__ == "__main__":
    main()
