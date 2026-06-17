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

echo ">> Generating boxplot + stats table ..."
python3 "$ANALYSIS/raw_stats.py" --out "$ANALYSIS/figures" "$LOCAL_LOGS"/

echo
echo ">> Done. Figures + tables in: $ANALYSIS/figures"
echo "   (run analyse.py in the venv for CDF/histogram/timeseries as well)"
