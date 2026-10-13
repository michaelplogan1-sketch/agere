"""Usage: .venv/bin/python -m tools.learning_loop --stack <stack dir> [--out DIR] [--receipts DIR] [--cases-dir bench/regressions]
       [--bottling-dir artifacts/bottling] [--existing-bottling DIR ...] [--write-bottling] [--retrain-threshold 100] [--json]

The stack's learning.sqlite3 is read-only input; nothing is written into the stack."""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.learning_loop.loop import run, card  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(prog='python -m tools.learning_loop')
    p.add_argument('--stack', default=str(ROOT / 'artifacts/agere-healthcare-v123/stack'))
    p.add_argument('--name', help='stack name for the output directory (default: the stack directory parent name)')
    p.add_argument('--out', help='output directory (default artifacts/learning-loop/<name>)')
    p.add_argument('--receipts', help='provider receipts directory (default <stack>/provider-receipts)')
    p.add_argument('--cases-dir', help='also write regression cases here (e.g. bench/regressions)')
    p.add_argument('--bottling-dir', help='bottling dataset directory (default artifacts/bottling)')
    p.add_argument('--existing-bottling', nargs='*', default=[], help='directories whose answers-*/promoted-*/data are already bottled')
    p.add_argument('--write-bottling', action='store_true', help='write promoted pairs to <bottling-dir>/promoted-<date>.jsonl')
    p.add_argument('--retrain-threshold', type=int, default=100)
    p.add_argument('--min-support', type=int, default=2)
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    stack = Path(a.stack)
    name = a.name or stack.parent.name
    out = Path(a.out) if a.out else ROOT / 'artifacts/learning-loop' / name
    summary = run(stack, out, receipts=a.receipts, cases_dir=a.cases_dir, bottling_dir=a.bottling_dir,
                  existing_bottling=a.existing_bottling, name=name, write_bottling=a.write_bottling,
                  retrain_threshold=a.retrain_threshold, min_support=a.min_support)
    print(json.dumps(summary, indent=1, default=str) if a.json else card(summary))
    print(f'\nwritten: {out}')


if __name__ == '__main__':
    main()
