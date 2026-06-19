#!/usr/bin/env bash
#
# Fetch the raw per-packet + per-second CSVs the live cloud receiver wrote, then
# generate every report figure from them. This is run_test.sh WITHOUT the python
# sender step — use it when the PHONE app was the sender (a real T1/T2 run over
# real Wi-Fi + 5G), so there is no local python test to start.
#
# Typical flow:
#   1. teammate runs the Kotlin app on the phone (sends to the server)
#   2. teammate stops the app
#   3. you run:   ./fetch_and_plot.sh
#
# Usage:
#   ./fetch_and_plot.sh [--fresh]
#
#   --fresh   restart the receiver on the server FIRST (clears the live CSVs) so
#             the next file holds ONLY the upcoming run. Run this BEFORE the
#             phone starts sending, not after. Briefly interrupts the dashboard.
#
# Tip: run `ssh-add ~/.ssh/id_ed25519` once first so you are not asked for the
# key passphrase on every SSH/SCP step below.

set -euo pipefail

# ── CONFIG (keep in sync with run_test.sh) ───────────────────────────────────
SERVER_IP="34.32.45.194"
SSH_KEY="$HOME/.ssh/id_ed25519"
SSH_USER="viktormunk"
PM2_APP="multi-rat"
REMOTE_LOGS="/home/sastefirta/Multi-RAT-system/src/WebAppExpress/logs"
# ─────────────────────────────────────────────────────────────────────────────

FRESH="${1:-}"

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
LOCAL_LOGS="$REPO/src/WebAppExpress/logs"
ANALYSIS="$REPO/src/analysis"
SSH="ssh -i $SSH_KEY $SSH_USER@$SERVER_IP"

mkdir -p "$LOCAL_LOGS"

if [ "$FRESH" = "--fresh" ]; then
  echo ">> Restarting receiver on server for a clean CSV ..."
  echo "   (run this BEFORE the phone starts sending, then start the app)"
  $SSH "sudo -u sastefirta pm2 restart $PM2_APP" >/dev/null
  echo ">> Done. Start the phone app now; re-run WITHOUT --fresh when finished."
  exit 0
fi

echo ">> Fetching the latest CSVs the server wrote ..."
$SSH "bash -s" <<EOF
set -e
D=/tmp/mr_fetch
rm -rf "\$D" && mkdir -p "\$D"
sudo cp "\$(sudo ls -t $REMOTE_LOGS/packets_*.csv | head -1)" "\$D"/
sudo cp "\$(sudo ls -t $REMOTE_LOGS/metrics_*.csv | head -1)" "\$D"/
sudo chown -R $SSH_USER "\$D"
EOF
scp -i "$SSH_KEY" "$SSH_USER@$SERVER_IP:/tmp/mr_fetch/*.csv" "$LOCAL_LOGS"/

# Pick THIS run's two CSVs = the ones with the newest timestamp in their name.
# Filenames are *_YYYYMMDD_HHMMSS.csv, so a plain lexical sort is chronological.
# We analyse ONLY these two files (not the whole logs/ dir), so older runs that
# are still sitting in logs/ don't each get their own confusing set of figures.
PKT="$(ls "$LOCAL_LOGS"/packets_*.csv 2>/dev/null | sort | tail -1 || true)"
MET="$(ls "$LOCAL_LOGS"/metrics_*.csv 2>/dev/null | sort | tail -1 || true)"
if [ -z "$PKT" ] && [ -z "$MET" ]; then
  echo "!! No CSVs found in $LOCAL_LOGS — nothing to plot." >&2
  exit 1
fi

# One figures subfolder per run, named after the run's timestamp, so each test's
# outputs stay together and never mix with another run's.
RUN_ID="$(basename "${PKT:-$MET}" .csv | sed -E 's/^(packets|metrics)_//')"
FIG="$ANALYSIS/figures/run_$RUN_ID"
mkdir -p "$FIG"
echo ">> This run: $(basename "${PKT:-—}")  $(basename "${MET:-—}")"
echo ">> Figures -> $FIG"

if [ -n "$PKT" ]; then
  echo ">> Boxplot + per-packet stats table (raw_stats.py) ..."
  python3 "$ANALYSIS/raw_stats.py" --out "$FIG" "$PKT"
fi

# CDF / histogram / timeseries / summary need pandas + scipy, which live in the
# analysis venv (src/analysis/venv). Run them too if that venv exists; otherwise
# tell the user how to create it.
VENV_PY="$ANALYSIS/venv/bin/python"
if [ -n "$MET" ]; then
  if [ -x "$VENV_PY" ]; then
    echo ">> CDF / histogram / timeseries / summary (analyse.py) ..."
    "$VENV_PY" "$ANALYSIS/analyse.py" --out "$FIG" "$MET"
  else
    echo ">> Skipping analyse.py (no venv). To enable CDF/hist/timeseries/summary:"
    echo "     python3 -m venv $ANALYSIS/venv"
    echo "     $ANALYSIS/venv/bin/pip install -r $ANALYSIS/requirements.txt"
  fi
fi

echo
echo ">> Done. Figures + tables for this run in: $FIG"
