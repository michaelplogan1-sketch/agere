"""Retrain the semantic statement students on the authored curriculum with the current code, adding the frozen
BioClinical ModernBERT head beside lexical and MiniLM, and print the validation comparison.

Same rows (artifacts/agere-semantic-students-v1/minilm/examples.jsonl: train / calibration / validation), same logistic
heads, temperature and selective-threshold policy; only the representation differs. Output:
artifacts/agere-semantic-students-v2/{lexical,minilm,bioclinical}/ (semantic_student.DEFAULT_DIR) and comparison.json.
Usage: python tools/train_semantic_students_v2.py [--kinds lexical minilm bioclinical]
"""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clinical_core.agere.clinical import semantic_student as S  # noqa: E402

SOURCE = ROOT/'artifacts/agere-semantic-students-v1/minilm/examples.jsonl'


def summary(m):
    v, c = m['validation'], m['calibration']
    return {'statement_accuracy': round(v['axes']['statement']['accuracy'], 4), 'exact_match': round(v['exact_match'], 4),
            'selected_coverage': round(v['selected_coverage'], 4), 'selected_precision': v['selected_precision'],
            'threshold': c['threshold'], 'axes_accuracy': {a: round(x['accuracy'], 4) for a, x in v['axes'].items()},
            'training_seconds': round(m['training_seconds'], 1)}


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument('--kinds', nargs='+', default=['lexical', 'minilm', 'bioclinical'])
    a = ap.parse_args(argv)
    rows = [json.loads(l) for l in SOURCE.read_text().splitlines() if l.strip()]
    prov = json.loads((SOURCE.parent/'manifest.json').read_text())['provenance']
    out = {}
    for kind in a.kinds:
        target = S.DEFAULT_DIR/kind
        if (target/'manifest.json').exists():
            m = S.verify(target)
        else:
            m = S.fit(rows, target, kind=kind, supervision='authored_synthetic_development', provenance={**prov, 'retrained_from': str(SOURCE)})
        out[kind] = summary(m); print(kind, json.dumps(out[kind]))
    (S.DEFAULT_DIR/'comparison.json').write_text(json.dumps({'source': str(SOURCE), 'validation': out}, indent=2) + '\n')


if __name__ == '__main__':
    main()
