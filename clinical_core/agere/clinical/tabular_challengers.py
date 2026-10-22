"""TabPFN-v2 tabular foundation-model CHALLENGER for the small-cohort warehouse predictors (no-show, readmission, risk).

Local weights (models/tabpfn-v2-clf, 28 MB), CPU, no network. It is fitted beside the logistic/boosted candidates in
warehouse_prediction.fit as one more retained research candidate; it becomes the default sort/score model ONLY when it beats the
incumbent (boosted_blocks) on the held-out TEST split -- lower calibrated log-loss by MARGIN and no worse AUROC. Otherwise it stays
reported and unused. AGERE_TABPFN=off disables it.
"""
import os
from pathlib import Path

import numpy as np

CKPT = Path(__file__).resolve().parents[3] / 'models' / 'tabpfn-v2-clf' / 'tabpfn-v2-classifier.ckpt'
MARGIN = 0.02
MAX_TRAIN = 3000    # CPU budget; TabPFN's in-context fit is strongest on small cohorts anyway


def available():
    if os.environ.get('AGERE_TABPFN', 'on') == 'off' or not CKPT.exists(): return False
    try:
        import tabpfn  # noqa: F401
    except ImportError:
        return False
    return True


class TabPFNCandidate:
    """sklearn-style fit/predict_proba over a (n, d) float matrix with NaN for missing (TabPFN handles NaN natively)."""
    def __init__(self, n_estimators=4, seed=1729):
        self.n_estimators, self.seed, self.model = n_estimators, seed, None

    def fit(self, X, y):
        from tabpfn import TabPFNClassifier
        X, y = np.asarray(X, float), np.asarray(y)
        if len(y) > MAX_TRAIN:
            rng = np.random.default_rng(self.seed)
            keep = np.concatenate([rng.choice(np.flatnonzero(y == c), max(1, int(MAX_TRAIN * np.mean(y == c))), replace=False)
                                   for c in np.unique(y)])
            X, y = X[keep], y[keep]
        self.model = TabPFNClassifier(model_path=str(CKPT), device='cpu', n_estimators=self.n_estimators, random_state=self.seed,
                                      ignore_pretraining_limits=True)
        self.model.fit(X, y)
        self.classes_ = self.model.classes_
        return self

    def predict_proba(self, X):
        return self.model.predict_proba(np.asarray(X, float))


def verdict(models, incumbent='boosted_blocks', challenger='tabpfn'):
    """models: report['models'] (each has test_calibrated). -> the challenge record; default_model is the challenger only if it wins."""
    out = {'challenger': challenger, 'incumbent': incumbent, 'margin': MARGIN, 'default_model': incumbent, 'wins': False}
    if challenger not in models or incumbent not in models:
        return {**out, 'reason': 'challenger not fitted'}
    c, i = models[challenger]['test_calibrated'], models[incumbent]['test_calibrated']
    out.update(challenger_log_loss=c['log_loss'], incumbent_log_loss=i['log_loss'], challenger_auroc=c['auroc'], incumbent_auroc=i['auroc'])
    if c['log_loss'] < i['log_loss'] * (1 - MARGIN) and (c['auroc'] or 0) >= (i['auroc'] or 0):
        out.update(wins=True, default_model=challenger)
    return out
