#!/bin/sh
# Copy the synthetic demonstration data for Ask to the server's data volume. Run on the workstation, from the repo root:
#   deploy/send_demo_data.sh user@server
# Sends the semantic layer (about 9 GB, synthetic patients only) and the learned recipes. Resumable; safe to re-run.
set -eu
[ $# -eq 1 ] || { echo "usage: $0 user@server" >&2; exit 2; }
SEMANTIC=artifacts/agere-full-stack-rerun-2026-10-30/focused/runtime/stack/semantic.sqlite3
BOTTLES=artifacts/agere-tiered-core/bottles.sqlite3
# Saved System One judgments (synthetic data only): without them the first document questions on a new server are
# slower and cost more (one 267-document question cost $0.85 cold against about a cent warm).
CACHE=artifacts/agere-full-stack-rerun-2026-10-30/integrated-network/provider-budget.sqlite3
[ -f "$SEMANTIC" ] && [ -f "$BOTTLES" ] || { echo "run from the repository root" >&2; exit 2; }
ssh "$1" 'mkdir -p ~/agere-demo'
rsync -av --partial --progress "$SEMANTIC" "$BOTTLES" "$1":agere-demo/
[ -f "$CACHE" ] && rsync -av --partial --progress "$CACHE" "$1":agere-demo/
# Into the volume, owned by the app user (uid 10001), then restart so Ask turns on.
ssh "$1" 'cd ~/agere/deploy && docker compose run --rm --no-deps --user root -v ~/agere-demo:/in agere sh -c "mkdir -p /data/demo && cp /in/semantic.sqlite3 /in/bottles.sqlite3 /data/demo/ && chown -R 10001:10001 /data/demo && if [ -f /in/provider-budget.sqlite3 ] && [ ! -f /data/provider-budget.sqlite3 ]; then cp /in/provider-budget.sqlite3 /data/ && chown 10001:10001 /data/provider-budget.sqlite3; fi" && docker compose up -d agere'
echo "done: Ask reads /data/demo on the server"
