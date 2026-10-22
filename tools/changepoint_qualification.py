"""Run the Bayesian Blocks vs CUSUM/Page-Hinkley/EWMA change-point qualification and write
config/agere/changepoint_qualification.json (read by the trajectory and monitor engines).

Usage: .venv/bin/python tools/changepoint_qualification.py [--seed N] [--n SERIES_PER_SCENARIO] [--budget 0.1] [--out PATH]
Synthetic simulation only (seeded, under a minute)."""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clinical_core.agere.clinical import changepoint_qualification as CQ  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', type=int, default=20261018)
    ap.add_argument('--n', type=int, default=150)
    ap.add_argument('--budget', type=float, default=0.1)
    ap.add_argument('--out', type=Path, default=CQ.RESULT_FILE)
    a = ap.parse_args(argv)
    t = time.perf_counter()
    doc = CQ.qualify(seed=a.seed, n=a.n, budget=a.budget)
    doc['seconds'] = round(time.perf_counter() - t, 1)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(doc, indent=1) + '\n')
    for r in doc['results']:
        print(f"{r['method']:16s} {r['variant']:9s} FA {r['false_alarm_rate']:.2f} gapFA {r['gap_false_alarm_rate']:.2f} "
              f"lagFA {r['lag_false_alarm_rate']:.2f} det {r['detection_rate']:.2f} delay {r['median_delay_weeks']}")
    print('qualified:', doc['qualified_method'], doc['qualified_variant'], '| reference:', doc['reference'], f"| {doc['seconds']}s")
    print(CQ.note(doc))


if __name__ == '__main__':
    main()
