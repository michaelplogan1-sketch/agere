#!/bin/bash
# One heavy job at a time: local LoRA learners on the current datasets, then label the 1M corpus, rebuild datasets and
# retrain the normalizer on it. Each step logs to artifacts/training/chain/; a failing step never stops the next.
cd "$(dirname "$0")/../.." || exit 1
export AGERE_LOCAL_TYPED=off AGERE_LOCAL_NORMALIZER=off
PY=.venv/bin/python; LOG=artifacts/training/chain; mkdir -p $LOG
step() { echo "$(date '+%F %T') start $1" >> $LOG/chain.log; shift_name=$1; shift; caffeinate -i "$@" > $LOG/$shift_name.log 2>&1; echo "$(date '+%F %T') end $shift_name exit $?" >> $LOG/chain.log; }
step narrow   $PY tools/training/train_all.py --datasets artifacts/training/datasets --out artifacts/training/runs/narrow-1022 --learners narrow --allow-mlx --eval-limit 200
step bottled  $PY tools/training/train_all.py --datasets artifacts/training/datasets --out artifacts/training/runs/bottled-1022 --learners bottled --allow-mlx --eval-limit 120
while pgrep -f "question_factory.py --n 1000000" > /dev/null; do sleep 60; done
step label1m  $PY tools/training/label.py --questions artifacts/training/questions-1m --out artifacts/training/labels-1m --workers 6
step datasets $PY tools/training/datasets.py --labels artifacts/training/labels-1m,artifacts/training/labels,artifacts/training/traffic --out artifacts/training/datasets-1m
step norm1m   $PY tools/training/train_all.py --datasets artifacts/training/datasets-1m --out artifacts/training/runs/normalizer-1m --learners normalizer --allow-mlx --eval-limit 400
# Self-improving loop: harvested traffic/corrections -> datasets (+ outcome-labelled tier router) -> retrain -> promote.
step selfimprove $PY tools/training/self_improve.py --allow-mlx --learners router,shape,tier_router,normalizer
