"""Causal ML for heterogeneous effects: cross-fitted double ML, R-learner CATE and an honest causal forest (numpy / sklearn).

Partially linear model Y = theta(X) T + g(X) + e, T = e(X) + v (Robinson 1988; Chernozhukov et al. 2018 double/debiased ML).
  nuisances   out-of-fold m(X) = E[Y|X] and e(X) = P(T=1|X) by gradient boosting (K folds; a member's nuisances come from
              models that never saw it). Residuals ry = Y - m(X), rt = T - e(X).
  plr_projection     theta = sum(rt ry) / sum(rt^2) (Neyman-orthogonal score), sandwich SE from psi = (ry - theta rt) rt / mean(rt^2).
  r_learner   CATE tau(x) = argmin sum rt^2 (ry / rt - tau(x))^2 (Nie & Wager 2021): a weighted regressor on the pseudo-outcome
              ry / rt with weights rt^2, cross-fitted.
  causal forest  GRF-style honest forest (Athey, Tibshirani & Wager 2019): each tree takes a subsample, grows its structure on
              one half from the R-learner pseudo-outcome (weights rt^2) and re-estimates every leaf effect on the other half as
              sum(rt ry) / sum(rt^2); prediction = mean over trees of the leaf effect, out-of-bag for training members;
              variable importance = weighted split importance.
Effects are tau = E[Y1 - Y0 | X] on the outcome scale; uplift.py turns them into benefit (sign by direction). Observational:
valid under no unmeasured confounding and overlap given X.
"""
import math

import numpy as np

VERSION = 'causal-hte/2'
LEARNERS = ('dml', 'causal_forest')


def _gbm(kind, seed):
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    args = dict(max_iter=150, learning_rate=0.06, max_leaf_nodes=15, min_samples_leaf=20, l2_regularization=1.0, random_state=seed)
    return HistGradientBoostingClassifier(**args) if kind == 'classifier' else HistGradientBoostingRegressor(**args)


def nuisances(X, t, y, fold, train_mask=None, seed=7, clip=0.02):
    """Out-of-fold m(X) = E[Y|X] and e(X) = P(T=1|X) (gradient boosting). train_mask restricts training rows."""
    X, t, y = np.asarray(X, float), np.asarray(t, int), np.asarray(y, float)
    n = len(y)
    train_mask = np.ones(n, bool) if train_mask is None else np.asarray(train_mask, bool)
    m, e = np.zeros(n), np.zeros(n)
    for f in np.unique(fold):
        te, tr = fold == f, (fold != f) & train_mask
        m[te] = _gbm('regressor', seed).fit(X[tr], y[tr]).predict(X[te])
        if len(np.unique(t[tr])) < 2: e[te] = t[tr].mean()
        else: e[te] = _gbm('classifier', seed).fit(X[tr], t[tr]).predict_proba(X[te])[:, 1]
    return m, np.clip(e, clip, 1 - clip)


def plr_projection(y, t, m, e):
    """Partially linear residual projection with sandwich SE; not generally the population ATE under heterogeneity."""
    ry, rt = np.asarray(y, float) - m, np.asarray(t, float) - e
    den = float(np.mean(rt * rt))
    theta = float(np.sum(rt * ry) / np.sum(rt * rt))
    psi = (ry - theta * rt) * rt / den
    se = float(np.std(psi, ddof=1) / math.sqrt(len(psi)))
    return {'estimate': theta, 'se': se, 'ci95': [theta - 1.96 * se, theta + 1.96 * se], 'n': int(len(psi)),
            'estimand': 'conditional_treatment_variance_weighted_projection',
            'population': 'declared analysis sample with residual treatment variation',
            'population_ate': False,
            'method': 'Cross-fitted partially linear double ML projection (Neyman-orthogonal residual-on-residual score)'}


def r_learner(X, ry, rt, fold, train_mask=None, seed=7, min_weight=1e-3):
    """Cross-fitted R-learner CATE: weighted boosting on ry/rt with weights rt^2."""
    X = np.asarray(X, float)
    n = len(ry)
    train_mask = np.ones(n, bool) if train_mask is None else np.asarray(train_mask, bool)
    w = rt * rt
    pseudo = ry * rt / np.maximum(w, 1e-6)  # = ry / rt, sign-safe
    ok = w > min_weight
    tau = np.zeros(n)
    for f in np.unique(fold):
        te, tr = fold == f, (fold != f) & train_mask & ok
        tau[te] = _gbm('regressor', seed).fit(X[tr], pseudo[tr], sample_weight=w[tr]).predict(X[te])
    return tau


class CausalForest:
    """Honest GRF-style causal forest on residualised data (ry, rt)."""

    def __init__(self, n_trees=200, min_leaf=10, max_depth=None, sample_fraction=0.5, max_features=0.7, ridge=1e-3, seed=7):
        self.n_trees, self.min_leaf, self.max_depth = n_trees, min_leaf, max_depth
        self.sample_fraction, self.max_features, self.ridge, self.seed = sample_fraction, max_features, ridge, seed

    def fit(self, X, ry, rt):
        from sklearn.tree import DecisionTreeRegressor
        X, ry, rt = np.asarray(X, float), np.asarray(ry, float), np.asarray(rt, float)
        n, d = X.shape
        rng = np.random.default_rng(self.seed)
        w = rt * rt
        pseudo = ry * rt / np.maximum(w, 1e-6)  # = ry / rt, sign-safe
        self.trees, self.importance = [], np.zeros(d)
        size = max(2 * self.min_leaf * 2, int(n * self.sample_fraction))
        for b in range(self.n_trees):
            sub = rng.choice(n, size=min(size, n), replace=False)
            half = len(sub) // 2
            grow, est = sub[:half], sub[half:]
            tree = DecisionTreeRegressor(min_samples_leaf=self.min_leaf, max_depth=self.max_depth, max_features=self.max_features,
                                         random_state=int(rng.integers(2 ** 31 - 1)))
            tree.fit(X[grow], pseudo[grow], sample_weight=w[grow] + 1e-9)
            leaves = tree.apply(X[est])
            num, den = {}, {}
            for leaf, a, c in zip(leaves, rt[est] * ry[est], w[est]):
                num[leaf] = num.get(leaf, 0.0) + a; den[leaf] = den.get(leaf, 0.0) + c
            effect = {k: num[k] / (den[k] + self.ridge) for k in num}
            self.trees.append((tree, effect, np.zeros(n, bool)))
            self.trees[-1][2][sub] = True
            self.importance += tree.feature_importances_
        s = self.importance.sum()
        self.importance = self.importance / s if s > 0 else self.importance
        self._n = n
        return self

    def _predict(self, X, exclude=None):
        X = np.asarray(X, float)
        tot, cnt = np.zeros(len(X)), np.zeros(len(X))
        for tree, effect, used in self.trees:
            leaves = tree.apply(X)
            vals = np.array([effect.get(l, np.nan) for l in leaves])
            ok = np.isfinite(vals) if exclude is None else np.isfinite(vals) & ~used
            tot[ok] += vals[ok]; cnt[ok] += 1
        out = np.full(len(X), np.nan)
        out[cnt > 0] = tot[cnt > 0] / cnt[cnt > 0]
        return out, cnt

    def predict(self, X):
        return self._predict(X)[0]

    def oob_predict(self, X):
        """Out-of-bag CATE for the training rows (trees whose subsample excluded the row)."""
        pred, cnt = self._predict(X, exclude=True)
        full = self.predict(X)
        return np.where(cnt > 0, pred, full)


def causal_forest_cate(X, ry, rt, fold, train_mask=None, seed=7, n_trees=200, min_leaf=10):
    """Cross-fitted forest CATE (forest on other folds predicts the held-out fold) + importance averaged over folds."""
    X = np.asarray(X, float)
    n = len(ry)
    train_mask = np.ones(n, bool) if train_mask is None else np.asarray(train_mask, bool)
    tau, imp = np.zeros(n), np.zeros(X.shape[1])
    folds = np.unique(fold)
    for f in folds:
        te, tr = fold == f, (fold != f) & train_mask
        cf = CausalForest(n_trees=n_trees, min_leaf=min_leaf, seed=seed + int(f)).fit(X[tr], ry[tr], rt[tr])
        p = cf.predict(X[te])
        tau[te] = np.where(np.isfinite(p), p, np.nanmean(p) if np.isfinite(p).any() else 0.0)
        imp += cf.importance / len(folds)
    return tau, imp


def fit(X, t, y, fold, train_mask=None, learners=LEARNERS, seed=7, n_trees=200, min_leaf=10):
    """Both learners on arrays with shared cross-fitted nuisances -> {'tau': {learner: array}, 'plr_projection', 'importance', 'nuisance'}."""
    t, y = np.asarray(t, int), np.asarray(y, float)
    keep = np.ones(len(y), bool) if train_mask is None else np.asarray(train_mask, bool)
    m, e = nuisances(X, t, y, fold, keep, seed)
    ry, rt = y - m, t - e
    out = {'tau': {}, 'plr_projection': plr_projection(y[keep], t[keep], m[keep], e[keep]), 'importance': None,
           'nuisance': {'m': m, 'e': e, 'residual_outcome': ry, 'residual_treatment': rt}}
    if 'dml' in learners: out['tau']['dml'] = r_learner(X, ry, rt, fold, keep, seed)
    if 'causal_forest' in learners:
        out['tau']['causal_forest'], out['importance'] = causal_forest_cate(X, ry, rt, fold, keep, seed, n_trees, min_leaf)
    return out
