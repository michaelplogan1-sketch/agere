"""Triage trials, 2026-11-17: paired, clustered confidence intervals for the "beats baseline" claims, and proof_v14
re-scored under confidence-target scoring (right +1, abstain 0, wrong -t/(1-t)). Code only, no model.

Method (research row: paired, clustered statistics): every comparison is per item on the same items; the difference is
the mean of paired differences; the 95% interval is a cluster bootstrap (whole clusters resampled, 10,000 draws, seed 17),
clusters = question template / area / valuation date as stated per comparison.

    PYTHONPATH=. python eval/tiered/trial_stats.py
"""
import json
import random
from collections import defaultdict
from pathlib import Path

T = Path('artifacts/agere-tiered-core/trials')
P = Path('artifacts/agere-tiered-core/proof')
WS = Path('/Users/mikel/clinical-insights-local/artifacts/agere-integration-recovery-20261104/workspace/artifacts/agere-tiered-core/proof')
R = random.Random(17)


def cluster_ci(pairs, draws=10000):
    """pairs: [(cluster, difference)] -> (mean difference, low, high, clusters, items)."""
    by = defaultdict(list)
    for c, d in pairs: by[c].append(d)
    keys = list(by)
    mean = sum(d for _, d in pairs) / len(pairs)
    stats = []
    for _ in range(draws):
        pick = [R.choice(keys) for _ in keys]
        xs = [d for k in pick for d in by[k]]
        stats.append(sum(xs) / len(xs))
    stats.sort()
    return round(mean, 4), round(stats[int(0.025 * draws)], 4), round(stats[int(0.975 * draws)], 4), len(keys), len(pairs)


def line(name, res, unit=''):
    m, lo, hi, k, n = res
    verdict = 'survives' if lo > 0 or hi < 0 else 'interval includes 0'
    return f'{name}: {m:+}{unit} [{lo:+}, {hi:+}] over {n} items in {k} clusters -> {verdict}'


def voi():
    """Per answer: candidate minus current rule. (a) asks; (b) asks that hid a >5% difference (material silent answers)."""
    import sys
    sys.path.insert(0, '.')
    from clinical_core.agere.tiered import readings
    rs = [json.loads(l) for l in open(P / 'journal-trial_voi_1-stack_voi-runs2-strict-phrasingsall.jsonl')]
    asks, material = [], []
    for r in rs:
        tpl = r['id'].split('-')[0]
        rc = r.get('readings_checked') or {}
        cand_ask = r['tier'] == 'readings'
        if rc.get('voi'):
            other = rc['others'][0]['rows']
            cur_ask = not readings.same(r['rows'], other, r['question'])
            ha, hb = readings._headline(r['rows']), readings._headline(other)
            big = len(ha) != len(hb) or any(abs(x - y) / max(abs(x), abs(y), 1e-9) > 0.05 for x, y in zip(ha, hb) if x is not None and y is not None)
            material.append((tpl, 1.0 if (cur_ask and big) else 0.0))
        else:
            cur_ask = cand_ask
            material.append((tpl, 0.0))
        asks.append((tpl, float(cand_ask) - float(cur_ask)))
    return [line('VOI: asks per answer, candidate minus current rule', cluster_ci(asks)),
            line('VOI: silent answers hiding a >5% difference per answer (candidate; current rule has 0)', cluster_ci(material))]


def pdc():
    """Per patient-class: plain SQL adherent (>=0.8) minus course code adherent, clustered by drug class. Recomputed."""
    import sys
    sys.path.insert(0, '.')
    import sqlite3
    from datetime import date
    from clinical_core.agere.tiered import store
    from clinical_core.agere.clinical import therapy_courses as tc
    db = sqlite3.connect(store.path().resolve().as_uri() + '?mode=ro', uri=True)
    fs = db.execute('select min(fill_date) from fill').fetchone()[0]
    classes = ['statin', 'ace_inhibitor', 'arb', 'metformin', 'sulfonylurea', 'dpp4_inhibitor', 'sglt2_inhibitor']
    a = {(r['patient_id'], r['drug_class']): r['pdc'] for r in tc.courses(db, [2029], '2030-05-27', fs)
         if r['drug_class'] in classes and r['pdc'] is not None and r['fills'] >= 2}
    b = {}
    q = ("SELECT drug_class, patient_id, COUNT(*), MIN(fill_date), SUM(days_supply) FROM fill WHERE substr(fill_date,1,4)='2029' "
         f"AND drug_class IN ({','.join('?' * len(classes))}) GROUP BY 1,2 HAVING COUNT(*)>=2")
    for cls, pid, n, f, s in db.execute(q, classes):
        b[(pid, cls)] = min(1.0, (s or 0) / ((date(2029, 12, 31) - date.fromisoformat(f[:10])).days + 1))
    pairs = [(cls, float(b[(pid, cls)] >= 0.8) - float(a[(pid, cls)] >= 0.8)) for (pid, cls) in a if (pid, cls) in b]
    by_patient = [(pid, d) for (pid, cls), d in zip([k for k in a if k in b], [d for _, d in pairs])]
    return [line('PDC: plain SQL minus course code, share adherent (clustered by class, 7 clusters)', cluster_ci(pairs)),
            line('PDC: same, clustered by patient', cluster_ci(by_patient))]


def runout():
    rs = [json.loads(l) for l in open(T / 'runout-journal.jsonl')]
    llm = {json.loads(l)['key']: json.loads(l) for l in open(T / 'llm-alone-runout.jsonl')}
    out = []
    for other in ('settlement', 'bornhuetter_ferguson', 'paid_to_date'):
        pairs = [(r['valuation'], abs(r['error_pct'][other]) - abs(r['error_pct']['chain_ladder'])) for r in rs]
        out.append(line(f'runout: |error| {other} minus chain-ladder (6 valuation dates)', cluster_ci(pairs), ' pts'))
    pairs = [(r['valuation'], abs(llm[r['valuation']]['error_pct']) - abs(r['error_pct']['chain_ladder'])) for r in rs if llm.get(r['valuation'], {}).get('error_pct') is not None]
    out.append(line('runout: |error| LLM alone minus chain-ladder', cluster_ci(pairs), ' pts'))
    return out


def equivalence():
    """Recipes from the LLM-alone sample (30 flagged + 30 passing): defects found, bounded check minus LLM alone."""
    real = {203, 211, 221, 390, 758, 750}
    rs = [json.loads(l) for l in open(T / 'llm-alone-equivalence.jsonl')]
    caught = [(r['key'], float(r['verdict'].startswith('differ')) - float(r.get('correct') is False)) for r in rs if r['key'] in real]
    false = [(r['key'], float(r['verdict'].startswith('differ')) - float(r.get('correct') is False)) for r in rs if r['key'] not in real]
    return [line('equivalence: real defects caught, bounded check minus LLM alone (6 defects)', cluster_ci(caught)),
            line('equivalence: false flags per non-defect recipe, bounded check minus LLM alone (54 recipes)', cluster_ci(false))]


def credibility():
    """Per practice squared error, raw minus credibility, 2028->2029 (clusters = practices)."""
    import sys
    sys.path.insert(0, '.')
    from clinical_core.agere.tiered import store
    import collections
    db = store.connect()
    mm = collections.defaultdict(int); paid = collections.defaultdict(float); pm = {}
    for pid, m, prac in db.execute('select patient_id, month, attribution_practice_id from member_month where attribution_practice_id is not null'):
        mm[(prac, m[:4])] += 1; pm[(pid, m)] = prac
    for pid, sd, n in db.execute('select patient_id, service_date, net_payer_paid_cents from claim where service_date is not null'):
        prac = pm.get((pid, sd[:7]))
        if prac: paid[(prac, sd[:4])] += (n or 0) / 100
    rate = lambda p, y: paid[(p, y)] / mm[(p, y)]
    ps = [p for (p, y) in mm if y == '2028' and mm[(p, '2029')] and mm[(p, '2028')]]
    k = 598.0   # fitted on 2027-2028 in trial_credibility.py
    grand = sum(paid[(p, '2028')] for p in ps) / sum(mm[(p, '2028')] for p in ps)
    cred = lambda p: (mm[(p, '2028')] / (mm[(p, '2028')] + k)) * rate(p, '2028') + (k / (mm[(p, '2028')] + k)) * grand
    pairs = [(p, (rate(p, '2028') - rate(p, '2029')) ** 2 - (cred(p) - rate(p, '2029')) ** 2) for p in ps]
    return [line('credibility: squared PMPM error, raw minus credibility, 2028->2029 (practices)', cluster_ci(pairs), ' $^2')]


def target_scoring():
    """proof_v14 plain phrasing re-scored: right +1, declined 0, wrong -t/(1-t), for t = 0.5, 0.75, 0.9 (penalty 1, 3, 9);
    RS(10) is the current proof score."""
    out = ['proof_v14 re-scored (confidence-target): arm, answers, right, wrong, declined, RS(10), t=0.5, t=0.75, t=0.9; accuracy / error / abstention']
    arms = {}
    for f in ('journal-proof_v14-stack-runs1-strict-phrasings0.jsonl', 'journal-proof_v14-stack+frontier-runs1-strict-phrasings0.jsonl',
              'journal-proof_v14-assistant-runs1-strict-phrasings0.jsonl'):
        for l in open(WS / f):
            r = json.loads(l)
            if f.startswith('journal-proof_v14-stack+') and r['arm'] == 'stack': continue   # the first run; the later stack journal is the re-score
            arms.setdefault(r['arm'], {})[r['id']] = r['kind']
    for arm, ks in arms.items():
        n = len(ks); right = sum(k in ('right', 'asked_right') for k in ks.values()); wrong = sum(k == 'wrong' for k in ks.values()); dec = n - right - wrong
        s = lambda pen: right - pen * wrong
        out.append(f'  {arm:9} {n} answers: right {right}, wrong {wrong}, declined or 0 {dec}; RS(10) {s(10)}, t=0.5 {s(1)}, t=0.75 {s(3)}, t=0.9 {s(9)}; '
                   f'{100 * right / n:.0f}% / {100 * wrong / n:.0f}% / {100 * dec / n:.0f}%')
    # paired: stack minus assistant per question, under t = 0.9, clustered by area (domain)
    dom = {json.loads(l)['id']: json.loads(l)['domain'] for l in open(WS / 'journal-proof_v14-stack-runs1-strict-phrasings0.jsonl')}
    val = lambda k: 1.0 if k in ('right', 'asked_right') else -9.0 if k == 'wrong' else 0.0
    if 'stack' in arms and 'assistant' in arms:
        pairs = [(dom[q], val(arms['stack'][q]) - val(arms['assistant'][q])) for q in arms['stack'] if q in arms['assistant']]
        out.append(line('  proof_v14 t=0.9 score per question, Agere minus assistant (clustered by area)', cluster_ci(pairs)))
        pairs10 = [(dom[q], (1.0 if arms['stack'][q] in ('right', 'asked_right') else -10.0 if arms['stack'][q] == 'wrong' else 0.0)
                    - (1.0 if arms['assistant'][q] in ('right', 'asked_right') else -10.0 if arms['assistant'][q] == 'wrong' else 0.0)) for q in arms['stack'] if q in arms['assistant']]
        out.append(line('  proof_v14 RS(10) per question, Agere minus assistant (clustered by area)', cluster_ci(pairs10)))
        acc = [(dom[q], float(arms['stack'][q] in ('right', 'asked_right')) - float(arms['assistant'][q] in ('right', 'asked_right'))) for q in arms['stack'] if q in arms['assistant']]
        out.append(line('  proof_v14 accuracy per question, Agere minus assistant (clustered by area)', cluster_ci(acc)))
    return out


if __name__ == '__main__':
    for part in (voi, pdc, runout, equivalence, credibility, target_scoring):
        for l in part(): print(l)
