"""Train the pairwise candidate selector (clinical_core/agere/clinical/candidate_selector.py). Free: no model or paid call.

  .venv/bin/python tools/learned_routing/train_candidate_selector.py [--write]
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from clinical_core.agere.clinical import candidate_selector as S  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument('--write', action='store_true'); a = ap.parse_args(argv)
    model = S.train(S.labels())
    print(json.dumps({k: v for k, v in model.items() if k != 'weights'}, indent=1))
    if a.write: S.save(model)


if __name__ == '__main__':
    main()
