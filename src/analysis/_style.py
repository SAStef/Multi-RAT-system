"""Shared matplotlib styling for the Multi-RAT report figures.

Imported by both raw_stats.py and analyse.py so every figure has the same
fonts, colours, grid and margins — i.e. one consistent, report-ready look.
Call apply() once after `matplotlib.use("Agg")`.
"""

import matplotlib as mpl

# One colour per stream, used identically in every figure.
STREAM_COLORS = {"Wi-Fi": "#1E88E5", "5G/LTE": "#FB8C00", "Merged": "#43A047"}


def apply():
    mpl.rcParams.update({
        "figure.dpi": 150,
        "savefig.dpi": 200,
        "savefig.bbox": "tight",
        "font.size": 12,
        "axes.titlesize": 13,
        "axes.titleweight": "bold",
        "axes.labelsize": 12,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "grid.linestyle": "-",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.axisbelow": True,
        "legend.frameon": True,
        "legend.framealpha": 0.9,
        "legend.edgecolor": "0.8",
        "lines.linewidth": 1.3,
    })
