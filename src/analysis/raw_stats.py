#!/usr/bin/env python3
"""
Per-packet statistics and boxplots from the raw packet logs (packets_*.csv)
written by receiver.py.

Where analyse.py works on the per-second metrics CSV (averages), this script
works on the raw per-packet samples, which is what you need for honest
distribution statistics and boxplots: every packet is one data point, so the
within-second spread is preserved.

For each input it writes, into the output directory:
  - <name>_latency_stats.csv   count, mean, std, min, p5, q1, median, q3, p95,
                               max of latency for Wi-Fi, 5G/LTE and the merged
                               stream, plus per-path loss / duplicate / CRC counts
  - <name>_latency_stats.tex   the same latency summary as a report-ready booktabs
                               table (\\input it straight into the report)
  - <name>_latency_boxplot.pdf the three latency distributions as boxplots
  - <name>_latency_boxplot.png the same, for quick sharing

Latency groups:
  Wi-Fi   = valid packets received on path 1
  5G/LTE  = valid packets received on path 2
  Merged  = valid packets that survived PREOF (the first copy of each
            (session, seq); is_duplicate == 0) — the deduplicated output stream

Usage:
    python3 raw_stats.py <packets-csv-or-directory> [more ...]
    python3 raw_stats.py --out figures/ ../WebAppExpress/logs/
"""

import argparse
import csv
import sys
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
    """Return {group: np.array of latency_ms} for the three streams."""
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
    """Per-path packet counts: arrived, lost (seq-span method), duplicates, CRC errors."""
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
    # Echo a readable table to the console only; the report-ready table is the
    # .tex file written by write_latex (the separate .csv dump was redundant).
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


def write_latex(groups, counts, out: Path, name: str):
    """Report-ready booktabs table -> <name>_latency_stats.tex (\\input it directly)."""
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
    fig, ax = plt.subplots(figsize=(6, 4))
    for g, color in GROUPS:
        x = groups[g]
        if x.size:
            ax.hist(x, bins=40, alpha=0.5, color=color, label=g, edgecolor="none")
    ax.set_xlabel("Latency [ms]")
    ax.set_ylabel("Packets")
    ax.set_title(f"Latency histogram — {name}")
    ax.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(out / f"{name}_latency_hist.pdf")
    plt.close(fig)
    print(f"  hist -> {out / (name + '_latency_hist.pdf')}")


def plot_timeline(rows, out: Path, name: str):
    """Single over-time figure with time always on the x-axis: per-packet
    latency on top, per-second loss on the bottom, one line per stream.

    Latency uses valid (crc_ok) packets. Loss is derived per 1-second bin from
    the sequence numbers (span method: a gap between min/max seq in a bin that
    is not filled by an arrival is a loss), so no per-second metrics CSV is
    needed — this one figure tells the whole story from the raw packet log."""
    lat = {g: ([], []) for g, _ in GROUPS}   # latency: (times, ms)
    seqs = {g: [] for g, _ in GROUPS}        # loss:    [(time, seq), ...]
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
    # 'time' counts from receiver start, not test start, so shift to begin at 0.
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
        """Insert NaNs where samples are >max_dt apart so the line is not drawn
        across pauses (when the sender was stopped) — no misleading diagonals."""
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
    ax1.set_title(f"Latency and loss over time — {name}")
    ax1.legend(loc="upper right", ncol=3)
    fig.tight_layout()
    fig.savefig(out / f"{name}_timeline.pdf")
    plt.close(fig)
    print(f"  timeline -> {out / (name + '_timeline.pdf')}")


def analyse_file(csv_path: Path, out: Path):
    rows = load_rows(csv_path)
    if not rows:
        print(f"[skip] {csv_path} is empty")
        return
    name = csv_path.stem
    groups = latency_groups(rows)
    counts = path_counts(rows)
    write_stats(groups, counts, out, name)
    write_latex(groups, counts, out, name)
    plot_timeline(rows, out, name)
    plot_boxplot(groups, out, name)
    plot_cdf(groups, out, name)
    plot_hist(groups, out, name)


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
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    files = list(collect(args.inputs))
    if not files:
        sys.exit("no packets_*.csv files found")
    for f in files:
        analyse_file(f, out)
    print(f"\nWritten to {out.resolve()}")


if __name__ == "__main__":
    main()
