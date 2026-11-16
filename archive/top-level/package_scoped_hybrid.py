"""Package the post-diagnostic, review-only hybrid; no new fitting or promotion."""
from pathlib import Path
import hashlib
import json
import shutil
import joblib
from sklearn.pipeline import Pipeline
from clinical_core.agere.clinical import scoped_reader, scoped_hybrid
from clinical_core.agere.clinical.evidence_reader import AXES
from clinical_core.agere.clinical.scoped_hybrid import WholeWindowAdapter

ROOT = Path(__file__).resolve().parent
V2 = ROOT/'artifacts/agere-evidence-reader-v2'
V3 = ROOT/'artifacts/agere-evidence-reader-v3'
OUT = ROOT/'artifacts/agere-evidence-reader-v3-hybrid'


def main():
    if (OUT/'model-card.json').exists():raise ValueError('Hybrid already frozen')
    OUT.mkdir(exist_ok=True)
    old_card = json.loads((V2/'model-card.json').read_text()); new_card = json.loads((V3/'model-card.json').read_text())
    hashes = {}; roles = {}
    for axis in AXES:
        dest = OUT/f'{axis}-scoped_augmented.joblib'
        if axis == 'relation':
            source = V3/dest.name
            assert hashlib.sha256(source.read_bytes()).hexdigest() == new_card['weight_hashes'][source.name]
            shutil.copyfile(source, dest); roles[axis] = 'v3 target-scoped relation head'
        else:
            source = V2/f'{axis}-lexical.joblib'
            assert hashlib.sha256(source.read_bytes()).hexdigest() == old_card['weight_hashes'][source.name]
            model = Pipeline([('input', WholeWindowAdapter()), ('frozen_v2', joblib.load(source))])
            joblib.dump(model, dest, compress=3); roles[axis] = 'unchanged v2 whole-window head'
        hashes[dest.name] = hashlib.sha256(dest.read_bytes()).hexdigest()
    card = {'schema': scoped_reader.VERSION, 'decision_authority': False, 'synthetic': True,
        'weight_hashes': hashes, 'runtime_hash': hashlib.sha256(Path(scoped_reader.__file__).read_bytes()).hexdigest(),
        'adapter_hash': hashlib.sha256(Path(scoped_hybrid.__file__).read_bytes()).hexdigest(),
        'runtime_axes': roles, 'primary': 'relation_scoped_other_axes_preserved',
        'selection': 'Post-hoc engineering choice informed by v3 fresh diagnostic: relation improved, assertion regressed. That diagnostic is now development evidence, not independent validation of this hybrid.',
        'scope_applies_to_axes': ['relation'], 'foundation_model_finetuned': False, 'acceptance_threshold': None,
        'limitations': ['All outputs abstain from clinical authority', 'No independent clinical validation',
            'Known v2 assertion and temporal failures remain; only relation interpretation changed']}
    (OUT/'model-card.json').write_text(json.dumps(card, indent=2))
    # Verify the preserved heads before running the warehouse.
    rows = json.loads((V3/'fresh-cases.json').read_text())
    from clinical_core.agere.clinical.scoped_reader import ScopedReader
    from clinical_core.agere.clinical.evidence_reader import input_feature
    reader = ScopedReader(OUT, OUT/'verification-cache.sqlite3')
    try:
        predictions = reader.predict([r['input'] for r in rows])
        for axis in ('assertion', 'subject', 'time'):
            model = joblib.load(V2/f'{axis}-lexical.joblib')
            old = model.predict([input_feature(r['input']) for r in rows])
            assert all(p['axes'][axis]['label'] == a for p, a in zip(predictions, old))
        fresh = json.loads((V3/'fresh-evaluation.json').read_text())
        metrics = {}
        for axis, m in fresh['metrics'].items():
            variant = 'scoped_augmented' if axis == 'relation' else 'v2_whole'
            metrics[axis] = {'teacher_agreement_examples': m['teacher_consensus_cases'],
                'local_matches_teacher_consensus': m['matches'][variant]}
        (OUT/'warehouse-transfer.json').write_text(json.dumps({'synthetic': True, 'model_identity': reader.identity,
            'examples': len(rows), 'metrics': metrics, 'review_candidates': None,
            'scope': 'Post-hoc hybrid selection on this diagnostic; not independent validation or clinical truth.'}, indent=2))
        (OUT/'preserved-heads-check.json').write_text(json.dumps({'examples': len(rows), 'unchanged_axes': ['assertion','subject','time'],
            'status': 'passed', 'new_paid_api_calls': 0}, indent=2))
    finally:reader.close()
    print(json.dumps({'packaged': str(OUT), 'roles': roles, 'clinical_promotion': False}))


if __name__ == '__main__':main()
