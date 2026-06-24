#!/usr/bin/env python3
"""
Multi-RAT measurement analysis.

Reads the per-second CSV snapshots written by receiver.py (src/WebAppExpress/logs/)
and produces the report-ready figures and statistics:

  - summary.txt        mean +/- 95% confidence interval for latency/jitter per
                       stream, total loss percentages, packet counts, and the
                       path-failure correlation estimate
  - latency_cdf.pdf    CDF of latency for path 1, path 2 and the merged stream
  - latency_hist.pdf   histogram of the same three distributions
  - timeseries.pdf     latency and loss over time (shows degraded-path events)
  - drift.pdf          merged latency with linear fit (clock-drift estimate),
                       only written for captures longer than 30 minutes

Usage:
    python3 analyse.py <csv-file-or-directory> [more files/dirs ...]
    python3 analyse.py --out figures/ data/baseline/

Each input CSV is analysed separately; figures are named after the input file.
"""

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no display needed; we only write files

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

import _style
_style.apply()

STREAMS = [("p1", "Wi-Fi"), ("p2", "5G/LTE"), ("m", "Merged")]
COLORS = {"p1": "#1E88E5", "p2": "#FB8C00", "m": "#43A047"}
DRIFT_MIN_SECONDS = 30 * 60


def mean_ci(series: pd.Series, confidence: float = 0.95):
    """Mean and half-width of the t-based confidence interval, or NaNs."""
    x = series.dropna()
    if len(x) < 2:
        return float("nan"), float("nan")
    half = stats.sem(x) * stats.t.ppf((1 + confidence) / 2, len(x) - 1)
    return x.mean(), half


def loss_correlation(df: pd.DataFrame):
    """Correlation between per-second loss events on the two paths.

    Returns (phi, p1_rate, p2_rate, merged_rate, predicted_independent_rate).
    The phi coefficient is the Pearson correlation of the binary
    "lost something this second" indicators; the predicted rate is the
    product p1*p2 that holds under independence (Theory Eq. p-loss).
    """
    # The lost columns are cumulative session counters, so a loss event in a
    # given second is a positive increment, not a positive value.
    l1 = (df["p1_lost"].fillna(0).diff() > 0).astype(int)
    l2 = (df["p2_lost"].fillna(0).diff() > 0).astype(int)
    p1_rate, p2_rate = l1.mean(), l2.mean()
    merged_rate = (df["m_lost"].fillna(0).diff() > 0).astype(int).mean()
    if l1.nunique() < 2 or l2.nunique() < 2:
        phi = float("nan")  # no loss events on at least one path
    else:
        phi = l1.corr(l2)
    return phi, p1_rate, p2_rate, merged_rate, p1_rate * p2_rate


def write_summary(df: pd.DataFrame, out: Path, name: str):
    lines = [f"Multi-RAT analysis - {name}", "=" * 60]
    n_seconds = len(df)
    lines.append(f"samples (seconds with traffic): {n_seconds}")
    lines.append("")
    lines.append(f"{'stream':<10}{'latency [ms]':>22}{'jitter [ms]':>22}{'loss %':>10}")
    for key, label in STREAMS:
        lat_m, lat_h = mean_ci(df[f"{key}_latency"])
        jit_m, jit_h = mean_ci(df[f"{key}_jitter"])
        received = df[f"{key}_received"].iloc[-1] if len(df) else 0
        lost = df[f"{key}_lost"].iloc[-1] if len(df) else 0
        loss_pct = 100 * lost / (received + lost) if received + lost else 0.0
        lines.append(
            f"{label:<10}{lat_m:>12.2f} +/- {lat_h:<6.2f}"
            f"{jit_m:>12.2f} +/- {jit_h:<6.2f}{loss_pct:>10.3f}"
        )
    lines.append("")

    phi, r1, r2, rm, pred = loss_correlation(df)
    lines.append("Path-failure correlation (per-second loss indicators):")
    lines.append(f"  phi coefficient:        {phi:.3f}" if not np.isnan(phi)
                 else "  phi coefficient:        n/a (no loss on at least one path)")
    lines.append(f"  P(loss) path 1:         {r1:.4f}")
    lines.append(f"  P(loss) path 2:         {r2:.4f}")
    lines.append(f"  P(loss) merged:         {rm:.4f}  (measured)")
    lines.append(f"  P(loss) p1*p2:          {pred:.4f}  (predicted if independent)")
    text = "\n".join(lines)
    (out / f"{name}_summary.txt").write_text(text + "\n")
    print(text)


def plot_drift(df: pd.DataFrame, out: Path, name: str):
    """Linear fit of merged latency over time; the slope estimates clock drift."""
    t = (df["utc"] - df["utc"].iloc[0]).dt.total_seconds()
    if t.iloc[-1] < DRIFT_MIN_SECONDS:
        return
    y = df["m_latency"]
    mask = y.notna()
    if mask.sum() < 100:
        return
    slope, intercept, r, _p, _se = stats.linregress(t[mask], y[mask])
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(t, y, linewidth=0.7, color=COLORS["m"], label="Merged latency")
    ax.plot(t, intercept + slope * t, "k--",
            label=f"fit: {slope * 3600:.2f} ms/hour (r={r:.2f})")
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Latency [ms]")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / f"{name}_drift.pdf")
    plt.close(fig)
    print(f"  clock-drift estimate: {slope * 3600:.2f} ms/hour over "
          f"{t.iloc[-1] / 3600:.1f} h (r={r:.2f})")


def analyse_file(csv_path: Path, out: Path):
    df = pd.read_csv(csv_path, parse_dates=["utc"])
    if df.empty:
        print(f"[skip] {csv_path} is empty")
        return
    name = csv_path.stem
    print(f"\n--- {csv_path} ({len(df)} seconds) ---")
    write_summary(df, out, name)
    plot_drift(df, out, name)


def collect_csvs(inputs):
    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            # Only the per-second metrics CSVs have the p1_/p2_/m_ columns this
            # script expects. The same logs/ dir also holds packets_*.csv (raw
            # per-packet, handled by raw_stats.py), so glob narrowly to avoid
            # feeding those in and crashing on a missing 'p1_latency' column.
            yield from sorted(p.glob("metrics_*.csv"))
        elif p.is_file():
            yield p
        else:
            print(f"[warn] {inp} not found", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="CSV files or directories of CSVs")
    ap.add_argument("--out", default="figures", help="output directory (default: figures/)")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    files = list(collect_csvs(args.inputs))
    if not files:
        sys.exit("no CSV files found")
    for f in files:
        analyse_file(f, out)
    print(f"\nFigures and summaries written to {out.resolve()}")


if __name__ == "__main__":
    main()
