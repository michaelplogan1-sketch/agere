"""Freeze an ablation of target scope and unrelated-topic augmentation."""
from pathlib import Path
from collections import Counter
import hashlib
import json
import time
import joblib
import numpy as np
from sklearn.pipeline import Pipeline
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from threadpoolctl import threadpool_limits

from clinical_core.contracts import digest
from clinical_core.agere.clinical.evidence_reader import AXES, input_feature
from clinical_core.agere.clinical import scoped_reader
from clinical_core.agere.clinical.scoped_reader import VERSION, pipeline, noise_variants

ROOT = Path(__file__).resolve().parent
OLD = ROOT/'artifacts/agere-evidence-reader-v2'
OUT = ROOT/'artifacts/agere-evidence-reader-v3'


def main():
    OUT.mkdir(exist_ok=True)
    if (OUT/'model-card.json').exists():raise ValueError('Frozen model exists; use a new version for another fit')
    plan = {'primary': 'scoped_augmented', 'controls': ['v2_whole', 'whole_augmented', 'scoped'],
        'hypothesis': 'Target clause/section separation and unrelated-topic augmentation reduce context spillover.',
        'fit': 'Same per-axis agreed v2 training rows, C=4, balanced class loss. Augmentations each receive 1/3 sample weight; no added supervision.',
        'evaluation': 'Previously exposed diagnostic sets plus a new warehouse sample selected only after all weights freeze. Teacher consensus is not clinical truth.',
        'promotion': 'No automatic clinical authority. Compare all axes and expose regressions; fresh sample cannot establish clinical readiness.'}
    (OUT/'plan.json').write_text(json.dumps(plan, indent=2))
    rows = [json.loads(x) for x in (OLD/'examples.jsonl').read_text().splitlines()]
    for p in sorted(OLD.glob('repair-*.json')):rows.extend(json.loads(p.read_text()))
    labels = json.loads((OLD/'labels.json').read_text())
    excluded = set(json.loads((OLD/'leakage-audit.json').read_text())['quarantined'])
    hashes, reports = {}, {}; started = time.perf_counter()
    with threadpool_limits(limits=4):
        for axis in AXES:
            fit_rows = [r for r in rows if r['split'] == 'train' and r['example_id'] not in excluded and
                r['authored_labels'][axis] == labels[r['example_id']]['jev'][axis]['label'] == labels[r['example_id']]['perplexity'][axis]['label']]
            classes = Counter(r['authored_labels'][axis] for r in fit_rows)
            if set(classes) != set(AXES[axis]):raise ValueError('Missing agreed class')
            reports[axis] = {'training_examples': len(fit_rows), 'class_counts': dict(classes), 'models': {}}
            for variant in ('v2_whole', 'whole_augmented', 'scoped', 'scoped_augmented'):
                if variant == 'v2_whole':
                    model = joblib.load(OLD/f'{axis}-lexical.joblib')
                else:
                    x, y, weights = [], [], []
                    for r in fit_rows:
                        examples = noise_variants(r['input']) if variant.endswith('augmented') else [r['input']]
                        x.extend(examples); y.extend([r['authored_labels'][axis]]*len(examples)); weights.extend([1/len(examples)]*len(examples))
                    if variant == 'whole_augmented':
                        model = Pipeline([('tfidf', TfidfVectorizer(analyzer='char_wb', ngram_range=(3, 5), max_features=40000, sublinear_tf=True)),
                                          ('classifier', LogisticRegression(C=4., class_weight='balanced', max_iter=2000, random_state=812))])
                        x = [input_feature(v) for v in x]
                    else:model = pipeline()
                    model.fit(x, y, classifier__sample_weight=np.array(weights))
                    path = OUT/f'{axis}-{variant}.joblib'; joblib.dump(model, path, compress=3)
                    hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
                metrics = {}
                for split in ('calibration', 'test'):
                    selected = [r for r in rows if r['split'] == split and r['example_id'] not in excluded]
                    inputs = [r['input'] for r in selected]
                    predictions = model.predict(inputs if variant.startswith('scoped') else [input_feature(x) for x in inputs])
                    metrics[split] = {'examples': len(selected), 'authored_agreement': float(accuracy_score([r['authored_labels'][axis] for r in selected], predictions)),
                        'scope': 'Previously exposed development diagnostic, not a new held-out claim',
                        'predictions': [{'example_id': r['example_id'], 'label': str(p)} for r, p in zip(selected, predictions)]}
                reports[axis]['models'][variant] = metrics
    card = {'schema': VERSION, 'decision_authority': False, 'synthetic': True, 'primary': plan['primary'],
        'runtime_hash': hashlib.sha256(Path(scoped_reader.__file__).read_bytes()).hexdigest(), 'weight_hashes': hashes,
        'metrics': reports, 'dataset_digest': digest(rows), 'labels_digest': digest(labels), 'plan': plan,
        'fit_seconds': time.perf_counter()-started, 'foundation_model_finetuned': False,
        'limitation': 'Heuristic clause/section boundaries and synthetic training; outputs remain review-only. No clinical acceptance threshold.'}
    (OUT/'model-card.json').write_text(json.dumps(card, indent=2))
    print(json.dumps({'fit_seconds': card['fit_seconds'], 'metrics': {a: {v: m['test']['authored_agreement'] for v, m in r['models'].items()} for a, r in reports.items()}}, indent=2))


if __name__ == '__main__':main()
