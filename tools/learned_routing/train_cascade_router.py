"""Train the learned cascade router from outcome labels (clinical_core/agere/clinical/cascade_router.py).

  .venv/bin/python tools/learned_routing/train_cascade_router.py [--write] [--report FILE]

Labels: saved non-blind bench rows (blind/heldout never used), cascade traces + traffic labels + gaps proposer logs, and every
stack's learning outcomes (corrections / quarantine mark answered routes wrong). Writes cascade_router_model.json with --write.
Free: no model or paid call.
"""
import argparse, collections, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from clinical_core.agere.clinical import cascade_router as R  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument('--write', action='store_true'); ap.add_argument('--report')
    ap.add_argument('--traffic'); ap.add_argument('--results')
    a = ap.parse_args(argv)
    rows = R.labels(results=a.results, traffic=a.traffic)
    model = R.train(rows)
    src = collections.Counter(r['source'].split(':')[0] for r in rows)
    print(f"{len(rows)} labels from {dict(src)}; {len(model['tiers'])} tiers; global success {model['global']}")
    for t, rec in sorted(model['tiers'].items(), key=lambda kv: -kv[1]['n']):
        print(f"  {t:28s} n={rec['n']:4d} right={rec['right']:4d} base={rec['base']:.3f}"
              + (f" cv={rec['cv_accuracy']:.3f} vs base-rate {rec['base_accuracy']:.3f}" if 'cv_accuracy' in rec else ''))
    if a.write: R.save(model)
    if a.report: Path(a.report).write_text(json.dumps({'sources': src, 'model': {k: v for k, v in model.items() if k != 'tiers'},
                                                        'tiers': {t: {k: v for k, v in r.items() if k != 'weights'} for t, r in model['tiers'].items()}},
                                                       indent=1, default=str))
    return model


if __name__ == '__main__':
    main()
