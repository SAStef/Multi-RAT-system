#!/usr/bin/env bash
#
# One-shot: run a test against the live 24/7 cloud receiver, fetch the raw
# per-packet CSV it produced, and generate the latency boxplot + stats table.
#
# Usage:
#   ./run_test.sh <test-file> <seconds> [--fresh]
#
#   <test-file>  one of test_t1_degraded.py .. test_t5_corrupt.py
#   <seconds>    how long to run the test (auto-stops; needs the --seconds flag
#                that the test files now support)
#   --fresh      restart the receiver on the server BEFORE the test, so the
#                fetched CSV contains ONLY this test (briefly interrupts the
#                live dashboard). Recommended for clean per-test data.
#
# Example:
#   ./run_test.sh test_t3_loss.py 60 --fresh
#
# Tip: run `ssh-add ~/.ssh/id_ed25519` once first so you are not asked for the
# key passphrase on every SSH/SCP step below.

set -euo pipefail

# ── CONFIG (adjust if the server moves) ──────────────────────────────────────
SERVER_IP="34.32.45.194"
SSH_KEY="$HOME/.ssh/id_ed25519"
SSH_USER="viktormunk"
PM2_APP="multi-rat"
REMOTE_LOGS="/home/sastefirta/Multi-RAT-system/src/WebAppExpress/logs"
# ─────────────────────────────────────────────────────────────────────────────

TEST="${1:?usage: ./run_test.sh <test-file> <seconds> [--fresh]}"
SECONDS_RUN="${2:?usage: ./run_test.sh <test-file> <seconds> [--fresh]}"
FRESH="${3:-}"

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
LOCAL_LOGS="$REPO/src/WebAppExpress/logs"
ANALYSIS="$REPO/src/analysis"
SSH="ssh -i $SSH_KEY $SSH_USER@$SERVER_IP"

mkdir -p "$LOCAL_LOGS"

if [ "$FRESH" = "--fresh" ]; then
  echo ">> Restarting receiver on server for a clean CSV ..."
  $SSH "sudo -u sastefirta pm2 restart $PM2_APP" >/dev/null
  sleep 2
fi

echo ">> Running $TEST against $SERVER_IP for ${SECONDS_RUN}s ..."
python3 "$HERE/$TEST" --ip "$SERVER_IP" --seconds "$SECONDS_RUN"

echo ">> Fetching the raw CSVs the server just wrote ..."
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
# outputs (plots + raw stats CSV) stay together and never mix with another run's.
RUN_ID="$(basename "${PKT:-$MET}" .csv | sed -E 's/^(packets|metrics)_//')"
FIG="$ANALYSIS/figures/run_$RUN_ID"
mkdir -p "$FIG"
echo ">> This run: $(basename "${PKT:-—}")  $(basename "${MET:-—}")"
echo ">> Figures + data -> $FIG"

if [ -n "$PKT" ]; then
  echo ">> Per-packet plots + stats CSV (raw_stats.py) ..."
  python3 "$ANALYSIS/raw_stats.py" --out "$FIG" "$PKT"
fi

# CDF / histogram / timeseries / summary need pandas + scipy from the analysis
# venv (src/analysis/venv). Run them too if that venv exists; otherwise say how.
VENV_PY="$ANALYSIS/venv/bin/python"
if [ -n "$MET" ]; then
  if [ -x "$VENV_PY" ]; then
    echo ">> Summary + drift (analyse.py) ..."
    "$VENV_PY" "$ANALYSIS/analyse.py" --out "$FIG" "$MET"
  else
    echo ">> Skipping analyse.py (no venv). To enable summary/drift:"
    echo "     python3 -m venv $ANALYSIS/venv"
    echo "     $ANALYSIS/venv/bin/pip install -r $ANALYSIS/requirements.txt"
  fi
fi

echo
echo ">> Done. Figures + raw CSV data for this run in: $FIG"
