"""Doubly-robust off-policy evaluation (OPE) of outreach policies from the logged outreach outcomes (FRONTIER #37).

Before recommending an outreach policy, estimate what it WOULD have achieved on the logged contacts (Dudik, Langford & Li
2011, doubly robust policy evaluation; Su, Dimakopoulou, Krishnamurthy & Dudik 2019/2020, DR with optimistic shrinkage).

Log: every outreach_attempts record (outreach_learning.attempts) is one decision: context x = (eligibility risk score,
first vs repeat attempt, program), action a = channel (mail / phone / text), reward r = 1 when the outcome is 'engaged'.
Logged propensities p(a|x) are NOT received in the warehouse: they are estimated by multinomial logistic regression on x
(stated as an assumption in every answer). The reward model q(x, a) is a logistic regression on x within each channel.
Both are cross-fitted (5 patient folds) so no row is scored by a model that saw it.

Estimators for a target policy pi(a|x):
  DM      mean_i sum_a pi(a|x_i) q(x_i, a)
  IPS     mean_i w_i r_i                     w_i = pi(a_i|x_i) / p(a_i|x_i)   (propensities clipped at `clip`)
  SNIPS   sum w_i r_i / sum w_i
  DR      DM + mean_i w_i (r_i - q(x_i, a_i))
  DR-os   DR with optimistic shrinkage w -> lambda w / (w^2 + lambda), lambda chosen to minimise variance + squared shift
Patient-cluster bootstrap (models held fixed) gives 95% intervals for each value and for the DR difference from the logged
policy. A policy is RECOMMENDED only when the lower bound of its DR gain over the logged policy is above zero; otherwise the
answer says no candidate demonstrably beats what was done.

Candidate policies: logged (the estimated logging policy), uniform, best_arm (the channel with the highest posterior mean),
thompson (P(best) per arm from outreach_learning's Thompson bandit, by segment), risk_ranked (phone for high/medium risk
tier, text for low: a declared rule) and model_greedy (argmax of the reward model). Synthetic warehouse data only.
"""
from collections import defaultdict
from pathlib import Path
import hashlib
import json
import re
import sqlite3

import numpy as np

VERSION = 'off-policy-evaluation/1'
OP = 'policy_evaluation'
POLICIES = ('logged', 'uniform', 'best_arm', 'thompson', 'risk_ranked', 'model_greedy')
SEGMENTS = ('none', 'program', 'risk_tier', 'attempt')

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'OFF-POLICY EVALUATION of outreach policies from the logged outreach outcomes: would the bandit / best channel / '
                  'risk-ranked / model policy have engaged more patients than what we actually did? Doubly robust, IPS, SNIPS and '
                  'direct-method estimates with bootstrap intervals; recommends a policy only when its DR gain is demonstrably positive.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'policies': list(POLICIES), 'segment_by': 'none|program|risk_tier|attempt', 'bootstrap': 300,
                           'clip': 0.02, 'shrinkage': True, 'seed': 0},
            'meaning': 'Doubly robust off-policy value (engagement rate per contact) of candidate outreach policies on the logged '
                       'outreach_attempts decisions; estimated logging propensities (multinomial logistic on risk score, attempt, program; '
                       'not received), cross-fitted reward model, IPS/SNIPS/DM/DR/DR-shrinkage with patient-cluster bootstrap 95% intervals; '
                       'recommendation only when the DR gain over the logged policy has a positive lower bound.'}
NATIVE = ('value',)
NEEDS_STACK = False


def schema(obj, string, array):
    return obj({'policies': array(string(POLICIES), 'Candidate policies to evaluate; empty = all'),
                'segment_by': string(SEGMENTS, 'Segment for the Thompson policy (P(best) per segment)'),
                'bootstrap': {'type': 'integer', 'minimum': 0, 'maximum': 2000, 'description': 'Bootstrap resamples (0 = no intervals)'},
                'clip': {'type': 'number', 'minimum': 0.001, 'maximum': 0.2, 'description': 'Minimum estimated propensity'},
                'shrinkage': {'type': 'boolean', 'description': 'Also report DR with optimistic shrinkage (Su et al.)'},
                'seed': {'type': 'integer', 'minimum': 0, 'maximum': 1000000}})


def validate(p):
    allowed = {'policies', 'segment_by', 'bootstrap', 'clip', 'shrinkage', 'seed'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('policy_evaluation parameters: ' + ', '.join(sorted(allowed)))
    pols = list(dict.fromkeys(p.get('policies') or POLICIES))
    if any(x not in POLICIES for x in pols): raise ValueError(f'policies: subset of {POLICIES}')
    if 'logged' not in pols: pols.insert(0, 'logged')  # the comparison baseline is always evaluated
    out = {'policies': pols, 'segment_by': p.get('segment_by', 'none'), 'bootstrap': p.get('bootstrap', 300),
           'clip': p.get('clip', 0.02), 'shrinkage': p.get('shrinkage', True), 'seed': p.get('seed', 0)}
    if out['segment_by'] not in SEGMENTS: raise ValueError(f'segment_by: {SEGMENTS}')
    if type(out['bootstrap']) is not int or not 0 <= out['bootstrap'] <= 2000: raise ValueError('bootstrap 0..2000')
    if isinstance(out['clip'], bool) or not isinstance(out['clip'], (int, float)) or not 0.001 <= out['clip'] <= 0.2:
        raise ValueError('clip 0.001..0.2')
    if type(out['shrinkage']) is not bool: raise ValueError('shrinkage: true|false')
    if type(out['seed']) is not int or not 0 <= out['seed'] <= 1000000: raise ValueError('seed 0..1000000')
    out['clip'] = float(out['clip'])
    return out


# ---- models ---------------------------------------------------------------------------------------------------------
def _softmax_fit(X, y, k, l2=1.0, iters=300):
    """Multinomial logistic regression (L2, gradient descent with backtracking). -> W (d x k)."""
    n, d = X.shape
    W = np.zeros((d, k))
    Y = np.eye(k)[y]

    def loss_grad(W):
        Z = X @ W; Z -= Z.max(axis=1, keepdims=True)
        P = np.exp(Z); P /= P.sum(axis=1, keepdims=True)
        loss = -np.sum(Y * np.log(P + 1e-12)) / n + l2 * np.sum(W[1:] ** 2) / (2 * n)
        G = X.T @ (P - Y) / n; G[1:] += l2 * W[1:] / n
        return loss, G
    step = 1.0
    loss, G = loss_grad(W)
    for _ in range(iters):
        while step > 1e-6:
            W2 = W - step * G
            l2_, G2 = loss_grad(W2)
            if l2_ <= loss - 0.5 * step * np.sum(G * G): break
            step *= 0.5
        if step <= 1e-6: break
        W, loss, G = W2, l2_, G2
        step = min(step * 2, 8.0)
    return W


def _softmax(X, W):
    Z = X @ W; Z -= Z.max(axis=1, keepdims=True)
    P = np.exp(Z)
    return P / P.sum(axis=1, keepdims=True)


def _logit_fit(X, y, l2=1.0):
    """Binary logistic via the 2-class softmax; constant rate when y has one class."""
    if y.min() == y.max(): return ('const', float(y.mean()))
    return ('w', _softmax_fit(X, y.astype(int), 2, l2))


def _logit_predict(m, X):
    if m[0] == 'const': return np.full(len(X), m[1])
    return _softmax(X, m[1])[:, 1]


def _fold(pid, k=5):
    return int(hashlib.sha256(str(pid).encode()).hexdigest()[:8], 16) % k


def crossfit(X, a, r, pids, k_actions, folds=5):
    """-> (propensity matrix P (n x K), reward matrix Q (n x K)), each row scored by models fitted without its patient fold."""
    n = len(a)
    P = np.full((n, k_actions), 1.0 / k_actions); Q = np.full((n, k_actions), r.mean() if n else 0.0)
    fold = np.array([_fold(p, folds) for p in pids])
    for f in range(folds):
        test, train = fold == f, fold != f
        if not test.any() or train.sum() < 5: continue
        present = np.unique(a[train])
        W = _softmax_fit(X[train], a[train], k_actions)
        Pf = _softmax(X[test], W)
        missing = [j for j in range(k_actions) if j not in present]
        if missing: Pf[:, missing] = 1e-3; Pf /= Pf.sum(axis=1, keepdims=True)
        P[test] = Pf
        for j in range(k_actions):
            m = a[train] == j
            if m.sum() >= 3: Q[test, j] = _logit_predict(_logit_fit(X[train][m], r[train][m]), X[test])
            elif m.any(): Q[test, j] = r[train][m].mean()
    return P, Q


# ---- estimators -----------------------------------------------------------------------------------------------------
def estimates(pi, P, Q, a, r, clip=0.02, shrinkage=True):
    """pi, P, Q: n x K; a: logged action index; r: reward. -> {'dm', 'ips', 'snips', 'dr', 'dr_os', 'lambda', 'max_weight', 'ess'}."""
    n = len(a)
    idx = np.arange(n)
    p_log = np.maximum(P[idx, a], clip)
    w = pi[idx, a] / p_log
    dm_i = (pi * Q).sum(axis=1)
    resid = r - Q[idx, a]
    out = {'dm': float(dm_i.mean()), 'ips': float((w * r).mean()),
           'snips': float((w * r).sum() / w.sum()) if w.sum() > 0 else None,
           'dr': float((dm_i + w * resid).mean()), 'max_weight': round(float(w.max()), 3),
           'ess': round(float(w.sum() ** 2 / (w * w).sum()), 1) if (w * w).sum() > 0 else 0.0}
    out['_dr_i'] = dm_i + w * resid
    if shrinkage:
        best, best_score, best_lam = out['dr'], None, None
        for lam in np.quantile(w * w, [0.5, 0.75, 0.9, 0.95, 0.99, 1.0]) * np.array([1, 1, 1, 1, 1, 10]):
            if lam <= 0: continue
            ws = lam * w / (w * w + lam)
            vals = dm_i + ws * resid
            score = vals.var() / n + (vals.mean() - out['dr']) ** 2
            if best_score is None or score < best_score: best, best_score, best_lam = float(vals.mean()), score, float(lam)
        out['dr_os'], out['lambda'] = best, (round(best_lam, 4) if best_lam is not None else None)
    return out


# ---- policies -------------------------------------------------------------------------------------------------------
def policy_matrix(name, rows, actions, P, Q, thompson=None):
    n, K = len(rows), len(actions)
    pi = np.zeros((n, K))
    if name == 'logged': return P.copy()
    if name == 'uniform': return np.full((n, K), 1.0 / K)
    if name == 'model_greedy':
        pi[np.arange(n), Q.argmax(axis=1)] = 1.0; return pi
    if name == 'best_arm':
        rates = []
        for j in range(K):
            got = [x['responded'] for x in rows if x['channel'] == actions[j]]
            rates.append((sum(got) + 1) / (len(got) + 2))
        pi[:, int(np.argmax(rates))] = 1.0; return pi
    if name == 'risk_ranked':
        want = lambda x: 'phone' if x['risk_tier'] in ('high', 'medium') else 'text'
        for i, x in enumerate(rows):
            ch = want(x)
            pi[i, actions.index(ch) if ch in actions else 0] = 1.0
        return pi
    if name == 'thompson':
        for i, x in enumerate(rows):
            probs = (thompson or {}).get(x['segment']) or {}
            v = np.array([probs.get(c, 0.0) for c in actions])
            pi[i] = v / v.sum() if v.sum() > 0 else 1.0 / K
        return pi
    raise ValueError(name)


def _thompson(rows, segment_by, seed):
    """{segment: {channel: P(best)}} from outreach_learning's Thompson bandit over (channel, segment) arms."""
    from .outreach_bandit import ThompsonOutreach
    from .outreach_learning import PRIOR
    by = defaultdict(list)
    for x in rows: by[x['segment']].append({'arm': (x['channel'], x['segment']), 'responded': x['responded']})
    out = {}
    for seg, log in by.items():
        b = ThompsonOutreach.from_log(log, PRIOR)
        out[seg] = {s['arm'][0]: s['p_best'] for s in b.summary(seed=seed)}
    return out


# ---- data -----------------------------------------------------------------------------------------------------------
def logged_decisions(db, segment_by='none', keep=None, as_of=None):
    from .outreach_learning import RESPONDED, risk_tier, segment_of
    rows = []
    for rid, pid, day, payload in db.execute("SELECT record_id, patient_id, substr(event_date,1,10), payload FROM record "
                                             "WHERE stream = 'outreach_attempts' ORDER BY event_date, record_id"):
        if (keep is not None and pid not in keep) or (as_of and day and day > as_of): continue
        r = json.loads(payload or '{}')
        if not r.get('channel'): continue
        try: score = float(r.get('eligibility_risk_score'))
        except (TypeError, ValueError): score = None
        rows.append({'record_id': rid, 'patient_id': pid, 'date': day, 'channel': str(r['channel']),
                     'responded': str(r.get('outcome')) in RESPONDED, 'risk_score': score, 'risk_tier': risk_tier(score),
                     'repeat': r.get('attempt_number') not in (1, '1', None), 'program': str(r.get('program') or 'unknown'),
                     'segment': segment_of(r, segment_by)})
    return rows


def design(rows):
    scores = [x['risk_score'] for x in rows if x['risk_score'] is not None]
    mu = float(np.mean(scores)) if scores else 0.0
    sd = float(np.std(scores)) or 1.0 if scores else 1.0
    programs = sorted({x['program'] for x in rows})[1:]  # first program is the reference level
    X = [[1.0, ((x['risk_score'] if x['risk_score'] is not None else mu) - mu) / sd, float(x['risk_score'] is None),
          float(x['repeat']), *[float(x['program'] == g) for g in programs]] for x in rows]
    return np.array(X, float), ['intercept', 'risk_score_std', 'risk_missing', 'repeat_attempt', *[f'program={g}' for g in programs]]


def evaluate(rows, p):
    """rows: logged_decisions(); p: validated parameters -> report core."""
    actions = sorted({x['channel'] for x in rows})
    if len(rows) < 20 or len(actions) < 2:
        return {'status': 'insufficient_log', 'decisions': len(rows), 'actions': actions, 'policies': [], 'recommendation': None}
    X, features = design(rows)
    a = np.array([actions.index(x['channel']) for x in rows]); r = np.array([float(x['responded']) for x in rows])
    pids = [x['patient_id'] for x in rows]
    P, Q = crossfit(X, a, r, pids, len(actions))
    th = _thompson(rows, p['segment_by'], p['seed']) if 'thompson' in p['policies'] else None
    logged_value = float(r.mean())
    out_rows, per_i = [], {}
    for name in p['policies']:
        pi = policy_matrix(name, rows, actions, P, Q, th)
        e = estimates(pi, P, Q, a, r, p['clip'], p['shrinkage'])
        per_i[name] = (pi, e.pop('_dr_i'))
        mix = {actions[j]: round(float(pi[:, j].mean()), 4) for j in range(len(actions))}
        out_rows.append({'policy': name, 'channel_mix': mix, **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in e.items()}})
    # patient-cluster bootstrap, models held fixed
    if p['bootstrap']:
        rng = np.random.default_rng(p['seed'])
        groups = defaultdict(list)
        for i, pid in enumerate(pids): groups[pid].append(i)
        keys = list(groups)
        boot = defaultdict(list)
        base_i = per_i['logged'][1]
        for _ in range(p['bootstrap']):
            pick = rng.integers(0, len(keys), len(keys))
            idx = np.concatenate([groups[keys[j]] for j in pick])
            lv = r[idx].mean()
            for name, (_pi, dr_i) in per_i.items():
                boot[name].append(dr_i[idx].mean())
                boot[name + ':gain'].append(dr_i[idx].mean() - lv)
        for row in out_rows:
            v = np.array(boot[row['policy']]); g = np.array(boot[row['policy'] + ':gain'])
            row['dr_ci95'] = [round(float(np.quantile(v, 0.025)), 4), round(float(np.quantile(v, 0.975)), 4)]
            row['dr_gain_vs_logged'] = round(row['dr'] - logged_value, 4)
            row['dr_gain_ci95'] = [round(float(np.quantile(g, 0.025)), 4), round(float(np.quantile(g, 0.975)), 4)]
    else:
        for row in out_rows: row['dr_gain_vs_logged'] = round(row['dr'] - logged_value, 4)
    winners = [x for x in out_rows if x['policy'] != 'logged' and (x.get('dr_gain_ci95') or [0])[0] > 0]
    rec = max(winners, key=lambda x: x['dr']) if winners else None
    out_rows.sort(key=lambda x: -x['dr'])
    return {'status': 'evaluated', 'decisions': len(rows), 'patients': len(set(pids)), 'actions': actions, 'features': features,
            'logged_value': round(logged_value, 4), 'policies': out_rows,
            'recommendation': ({'policy': rec['policy'], 'dr': rec['dr'], 'gain': rec['dr_gain_vs_logged'], 'gain_ci95': rec['dr_gain_ci95']}
                               if rec else None),
            'propensity_source': 'estimated (multinomial logistic on risk score, attempt, program; logged propensities not received)',
            'period': {'start': rows[0]['date'], 'end': rows[-1]['date']}}


ASSUMPTIONS = ['The outreach outcome log is synthetic warehouse data, not live outreach results.',
               'Logged propensities are not received: the logging policy is estimated from risk score, attempt number and program. '
               'Unmeasured reasons for channel choice would bias every estimate.',
               'Reward = outcome engaged on that contact; repeat contacts are separate decisions (patient-cluster bootstrap).',
               'Bootstrap intervals hold the cross-fitted propensity and reward models fixed (model-fitting uncertainty not included).',
               'risk_ranked is a declared rule (phone for high/medium eligibility risk tier, text for low), not a fitted policy.']
METHOD = ('Off-policy evaluation on the logged outreach_attempts decisions: cross-fitted (5 patient folds) multinomial-logistic logging '
          'propensities and per-channel logistic reward model; direct method, IPS, self-normalised IPS, doubly robust (Dudik, Langford & Li '
          '2011) and DR with optimistic shrinkage (Su et al. 2020); patient-cluster bootstrap 95% intervals; a policy is recommended only '
          'when the lower bound of its DR gain over the logged policy is above zero.')


def run(db, patient_ids, p, as_of):
    keep = set(patient_ids) if patient_ids is not None else None
    rows = logged_decisions(db, p['segment_by'], keep, as_of)
    core = evaluate(rows, p)
    return {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'outcome_source': 'synthetic warehouse outreach_attempts log',
            **core, 'patient_ids': sorted({x['patient_id'] for x in rows}), 'method': METHOD, 'assumptions': ASSUMPTIONS}


def execute(path, patient_ids, params, as_of):
    from . import semantic_layer
    p = validate(params)
    target = semantic_layer.ensure(path) if (Path(path)/'run.json').exists() else Path(path)/'semantic.sqlite3'
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        return run(db, patient_ids, p, str(as_of)[:10])
    finally:
        db.close()


def check_recommendation(db, keep=None, as_of=None, segment_by='none', seed=0, bootstrap=200):
    """Short OPE receipt for outreach_learning: DR value of the Thompson and best-arm policies vs the logged policy."""
    p = validate({'policies': ['logged', 'thompson', 'best_arm'], 'segment_by': segment_by, 'bootstrap': bootstrap, 'seed': seed})
    core = evaluate(logged_decisions(db, segment_by, keep, as_of), p)
    if core['status'] != 'evaluated': return {'status': core['status'], 'decisions': core['decisions']}
    by = {x['policy']: x for x in core['policies']}
    return {'status': 'evaluated', 'version': VERSION, 'decisions': core['decisions'], 'logged_value': core['logged_value'],
            'policies': {k: {f: by[k].get(f) for f in ('dr', 'dr_ci95', 'dr_gain_vs_logged', 'dr_gain_ci95', 'ips', 'snips')} for k in by},
            'recommendation': core['recommendation'], 'propensity_source': core['propensity_source']}


def describe_check(c):
    """One sentence for outreach_learning's headline."""
    if not c or c.get('status') != 'evaluated': return None
    t = c['policies'].get('thompson') or {}
    ci = t.get('dr_gain_ci95')
    text = (f"Off-policy check before recommending (doubly robust, estimated propensities): the Thompson policy would have engaged "
            f"{t.get('dr', 0):.1%} of contacts vs {c['logged_value']:.1%} logged" + (f" (gain 95% CI {ci[0]:+.1%} to {ci[1]:+.1%})" if ci else ''))
    rec = c.get('recommendation')
    text += (f"; {rec['policy']} demonstrably beats the logged policy" if rec else '; no candidate policy demonstrably beats what was done, so '
             'the split above is exploration, not a proven improvement')
    return text


def headline(r):
    if r['status'] != 'evaluated':
        return (f"Off-policy evaluation needs at least 20 logged outreach decisions across two or more channels; found {r['decisions']} "
                f"({', '.join(r.get('actions') or []) or 'no channels'})")
    parts = [f"{x['policy']} {x['dr']:.1%}" + (f" (95% CI {x['dr_ci95'][0]:.1%}-{x['dr_ci95'][1]:.1%})" if x.get('dr_ci95') else '')
             for x in r['policies']]
    text = (f"Doubly robust engagement per contact on {r['decisions']} logged outreach decisions ({r['patients']} patients, "
            f"{r['period']['start']} to {r['period']['end']}, synthetic log): " + '; '.join(parts) + f". Logged rate {r['logged_value']:.1%}")
    rec = r['recommendation']
    if rec:
        text += (f". Recommend {rec['policy']}: DR gain {rec['gain']:+.1%} (95% CI {rec['gain_ci95'][0]:+.1%} to {rec['gain_ci95'][1]:+.1%}) "
                 'over the logged policy')
    else:
        text += '. No candidate policy has a DR gain whose 95% lower bound is above zero: none is recommended over the logged policy'
    return text


def answer(report, plan, question):
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    return {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['policies'],
            'count': len(report['policies']), 'patient_ids': report['patient_ids'], 'value': headline(report) + '.',
            'method': report['method'], 'limitations': report['assumptions'], 'membership_role': 'evaluated',
            'definitions': [{'term': 'policy value', 'basis': 'expected engagement (outcome = engaged) per outreach contact'},
                            {'term': 'doubly robust', 'basis': 'direct-method estimate plus importance-weighted residual correction'}],
            'overall': {'policy_evaluation': {k: report.get(k) for k in ('logged_value', 'recommendation', 'propensity_source', 'decisions', 'actions')}}}


# ---- free local-compiler route ----------------------------------------------------------------------------------------
OPE_RX = (r'\boff[- ]?policy\b|\bdoubly[- ]robust\b|\bcounterfactual(?:ly)? (?:value|evaluat\w*|estimate)\b|\bpolicy (?:value|evaluation)\b|'
          r'\binverse propensity\b|\b(?:ips|snips)\b')
WOULD_RX = (r'\bwould (?:the |a |our )?(?:bandit|thompson|best[- ]arm|risk[- ]ranked|model|new|alternative|different|other)\b.{0,60}\b'
            r'(?:done|do|performed|perform|engaged|been)\b|\bwhat if we had\b.{0,60}\b(?:outreach|contact\w*|called|texted|mailed)\b|'
            r'\bhave done better than\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if re.match(r'(?:what (?:is|are)|explain|define)\b', low) and not re.search(r'\bour\b|\bwe\b', low): return None
    if not re.search(r'\boutreach\b|\bcontact\w*\b|\bengag\w*\b|\bchannels?\b|\bbandit\b|\bthompson\b', low): return None
    if not (re.search(OPE_RX, low) or re.search(WOULD_RX, low)): return None
    params = {}
    chosen = [x for x, rx in (('thompson', r'\bbandit\b|\bthompson\b'), ('best_arm', r'\bbest[- ](?:arm|channel)\b'),
                               ('risk_ranked', r'\brisk[- ]ranked\b|\bby risk\b'), ('model_greedy', r'\bmodel\b'),
                               ('uniform', r'\buniform\b|\brandom\w*\b')) if re.search(rx, low)]
    if chosen: params['policies'] = ['logged', *chosen]
    if re.search(r'\brisk (?:tiers?|levels?)\b', low): params['segment_by'] = 'risk_tier'
    elif re.search(r'\bby program\b|\beach program\b', low): params['segment_by'] = 'program'
    return plan(OP, params, 'Doubly robust off-policy evaluation of outreach policies on the logged outreach decisions', question,
                [f"policies {params.get('policies', 'all')}"])


TEMPLATES = ['Evaluate our outreach policy off-policy before we change it', 'Would the bandit policy have done better than what we did?',
             'doubly robust estimate of outreach policy value', 'off-policy evaluation of the Thompson outreach recommendation',
             'Would a risk-ranked outreach policy have engaged more patients than our current channel choice?',
             'IPS and doubly robust value of always calling versus our logged outreach', 'counterfactual value of the best-arm outreach channel',
             'Would the model policy have performed better on our outreach contacts by risk tier?']
