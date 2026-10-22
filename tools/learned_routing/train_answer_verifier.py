"""Train the learned answer verifier (clinical_core/agere/clinical/answer_verifier.py) from every stack's learning.sqlite3.

  .venv/bin/python tools/learned_routing/train_answer_verifier.py [--write]
Free: no model or paid call.
"""
import argparse, collections, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from clinical_core.agere.clinical import answer_verifier as V  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument('--write', action='store_true'); a = ap.parse_args(argv)
    rows = V.labels(); model = V.train(rows)
    print(json.dumps({k: v for k, v in model.items() if k != 'weights'}, indent=1),
          dict(collections.Counter((r['source'], r['wrong']) for r in rows)))
    if a.write: V.save(model)


if __name__ == '__main__':
    main()
