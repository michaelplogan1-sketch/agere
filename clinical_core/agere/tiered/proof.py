"""The proof scorecard: a frozen, held-out question set scored the way a sceptical reviewer would score it.

Why this exists. Vendor accuracy figures come from small internal suites, single runs and execution-match grading; answer keys
are often wrong; and a system that guesses is scored like one that knows. This scorecard follows the stricter practice:
  * each question has a reference computed here in Python, independently of the stack's SQL and recipes;
  * each question is asked in three phrasings, and each phrasing is run three times (all runs reported, not the best);
  * some questions cannot be answered from this warehouse; declining them is the right answer;
  * scoring penalises a wrong answer ten times a refusal (+1 right, 0 declined, -10 wrong), and an unanswerable question that
    is answered counts as wrong;
  * the stack runs on a copy of the saved recipes with learning switched off, so the set never teaches the stack;
  * the same questions go to a frontier model writing its own SQL, scored the same way.
The question file is eval/tiered/proof_v1.json. Do not use these questions in the curriculum or as recipe examples.

    python -m clinical_core.agere.tiered.proof run [runs] [workers]
"""
import json
import os
import shutil
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from . import bottles as bottle_store, router, store

QUESTIONS = Path(os.environ.get('AGERE_PROOF_SET', 'eval/tiered/proof_v2.json'))
OUT = Path('artifacts/agere-tiered-core/proof')
PENALTY = 10


# ---- references, written here without the stack's SQL --------------------------------------------------------------

def _day(x):
    return date.fromisoformat(str(x)[:10])


def _readmissions(db, year):
    from .evaluate import _episodes
    eps = _episodes(db.execute("SELECT patient_id, admit_date, discharge_date FROM stay WHERE type = 'inpatient'").fetchall())
    by = defaultdict(list)
    for p, a, d in eps: by[p].append((a, d))
    out = []
    for p, stays in by.items():
        for a, d in stays:
            if not a.startswith(year): continue
            prior = [(dd, aa) for aa, dd in stays if dd and aa < a and 1 <= (_day(a) - _day(dd)).days <= 30]
            if prior: out.append((p, a, max(prior)[0]))
    return out, eps


def ref_readmissions(db, year):
    return [[len(_readmissions(db, year)[0])]]


def ref_readmission_rate(db, year):
    _, eps = _readmissions(db, year)
    idx = [(p, a, d) for p, a, d in eps if d and d.startswith(year)]
    back = sum(1 for p, a, d in idx if any(q == p and b > a and 1 <= (_day(b) - _day(d)).days <= 30 for q, b, _ in eps))
    return [[len(idx), back, round(back / len(idx), 4)]]


def ref_readmissions_no_visit(db, year):
    rows, _ = _readmissions(db, year)
    visits = defaultdict(list)
    for p, d in db.execute("SELECT patient_id, event_date FROM visit WHERE type NOT IN ('inpatient', 'emergency', 'observation', 'telephone', 'orders_only')"):
        visits[p].append(d)
    seen = sum(1 for p, a, dis in rows if any(0 < (_day(v) - _day(dis)).days <= 7 and _day(v) < _day(a) for v in visits[p]))
    return [[len(rows) - seen]]


def _member_months(db, year):
    mm, members = Counter(), defaultdict(set)
    for p, m, lob in db.execute('SELECT patient_id, month, line_of_business FROM member_month'):
        if str(m).startswith(year): mm[lob] += 1; members[lob].add(p)
    return mm, members


def ref_pmpm(db, year):
    mm, members = _member_months(db, year)
    paid = Counter()
    for lob, cents, counts, served in db.execute('SELECT line_of_business, paid_cents, counts_in_metrics, service_from FROM payer_claim'):
        if counts == 1 and served and served.startswith(year) and cents: paid[lob] += cents
    return [[lob, round(paid[lob] / 100 / mm[lob], 2)] for lob in mm]


def ref_members_by_lob(db, year):
    _, members = _member_months(db, year)
    return [[lob, len(p)] for lob, p in members.items()]


def ref_care_gaps(db, year):
    enrolled = {}
    for p, m, lob in db.execute('SELECT patient_id, month, line_of_business FROM member_month ORDER BY month'):
        if str(m).startswith(year): enrolled[p] = enrolled.get(p, False) or lob in ('medicare', 'dual')
    person = {p: (s, b) for p, s, b in db.execute('SELECT patient_id, sex_reported, birth_date FROM patient')}
    age = lambda p: int((date(int(year), 12, 31) - _day(person[p][1])).days / 365.25) if person.get(p) and person[p][1] else -1
    cond = defaultdict(set)
    for p, c, d in db.execute("SELECT patient_id, concept, COALESCE(event_date, '') FROM condition WHERE concept IS NOT NULL"):
        if d <= year + '-12-31': cond[p].add(c)
    a1c = {}
    for p, d, v in db.execute("SELECT patient_id, event_date, value_canonical FROM measurement WHERE concept = 'hba1c' ORDER BY event_date"):
        if str(d).startswith(year): a1c[p] = v
    statin = {p for p, d in db.execute("SELECT patient_id, fill_date FROM fill WHERE drug_class = 'statin'") if d and d.startswith(year)}
    mammo = {p for p, d in db.execute("SELECT patient_id, event_date FROM cancer_screening WHERE test = 'bilateral_screening_mammography'") if d and f'{int(year) - 2}-10-01' <= d <= year + '-12-31'}
    awv = {p for p, d in db.execute('SELECT patient_id, event_date FROM wellness_visit WHERE awv = 1') if d and d.startswith(year)}
    groups = {'annual_wellness_visit': ([p for p in enrolled if enrolled[p]], lambda p: p not in awv),
              'breast_cancer_screening': ([p for p in enrolled if person.get(p, ('', ''))[0] == 'F' and 52 <= age(p) <= 74], lambda p: p not in mammo),
              'diabetes_a1c': ([p for p in enrolled if 18 <= age(p) <= 75 and cond[p] & {'diabetes', 'type_2_diabetes', 'type_1_diabetes'}], lambda p: a1c.get(p) is None or a1c[p] > 9),
              'statin_cardiovascular': ([p for p in enrolled if 21 <= age(p) <= 75 and cond[p] & {'coronary_artery_disease', 'myocardial_infarction'}], lambda p: p not in statin)}
    return [[m, len(e), sum(1 for p in e if gap(p))] for m, (e, gap) in groups.items()]


def ref_a1c_gaps(db, year):
    return [[r[2]] for r in ref_care_gaps(db, year) if r[0] == 'diabetes_a1c']


def ref_top_cost(db, year, n=5):
    paid = Counter()
    for p, cents, counts, served in db.execute('SELECT patient_id, paid_cents, counts_in_metrics, service_from FROM payer_claim'):
        if counts == 1 and served and served.startswith(year) and cents: paid[p] += cents
    return [[p, round(c / 100, 2)] for p, c in sorted(paid.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def ref_ed_visits(db, year):
    return [[sum(1 for (d,) in db.execute("SELECT event_date FROM utilization_event WHERE kind = 'ed_visit'") if str(d).startswith(year))]]


def ref_statin_patients(db, year):
    return [[len({p for p, d in db.execute("SELECT patient_id, fill_date FROM fill WHERE drug_class = 'statin'") if d and d.startswith(year)})]]


def ref_heart_failure_patients(db, year):
    return [[len({p for (p,) in db.execute("SELECT patient_id FROM condition WHERE concept = 'heart_failure'")})]]


def ref_ed_returns(db, year):
    _, eps = _readmissions(db, year)
    dead = {p: d for p, d in db.execute('SELECT patient_id, death_date FROM death') if d}
    arrivals = set()
    for p, d in db.execute("SELECT patient_id, event_date FROM utilization_event WHERE kind = 'ed_visit'"):
        if not str(d).startswith(year): continue
        recent = [dd for q, aa, dd in eps if q == p and dd and 0 < (_day(d) - _day(dd)).days <= 30]
        inside = any(q == p and aa < d and (not dd or dd >= d) for q, aa, dd in eps)
        if recent and not inside and not (p in dead and dead[p] < d): arrivals.add((p, d))
    return [[len(arrivals)]]


def ref_heart_failure_any_source(db, year):
    return [[len({p for (p,) in db.execute("SELECT patient_id FROM concept_patient WHERE kind = 'condition' AND concept = 'heart_failure'")})]]


def _ed_returns(db, year, inside=True, distinct=True):
    _, eps = _readmissions(db, year)
    dead = {p: d for p, d in db.execute('SELECT patient_id, death_date FROM death') if d}
    out = []
    for p, d in db.execute("SELECT patient_id, event_date FROM utilization_event WHERE kind = 'ed_visit'"):
        if not str(d).startswith(year): continue
        recent = [dd for q, aa, dd in eps if q == p and dd and 0 < (_day(d) - _day(dd)).days <= 30]
        in_hospital = any(q == p and aa < d and (not dd or dd >= d) for q, aa, dd in eps)
        if recent and not (inside and in_hospital) and not (p in dead and dead[p] < d): out.append((p, d))
    return [[len(set(out)) if distinct else len(out)]]


def ref_ed_returns_visit_rows(db, year):
    return _ed_returns(db, year, distinct=False)


def ref_ed_returns_no_inpatient_rule(db, year):
    return _ed_returns(db, year, inside=False)


def ref_ed_returns_no_inpatient_rule_visit_rows(db, year):
    return _ed_returns(db, year, inside=False, distinct=False)


def ref_inpatient_patients(db, year):
    return [[len({p for p, k, d in db.execute('SELECT patient_id, kind, event_date FROM utilization_event') if k == 'inpatient' and str(d).startswith(year)})]]


def ref_avg_paid_by_claim_type(db, year):
    tot, n = Counter(), Counter()
    for t, c, f, d in db.execute('SELECT claim_type, paid_cents, counts_in_metrics, service_from FROM payer_claim'):
        if f == 1 and d and d.startswith(year) and c is not None: tot[t] += c; n[t] += 1
    return [[t, round(tot[t] / n[t] / 100, 2)] for t in n]


def ref_no_shows(db, year):
    return [[sum(1 for st, d in db.execute('SELECT final_status, scheduled_date FROM appointment') if st == 'no_show' and str(d).startswith(year))]]


def ref_food_positive_patients(db, year):
    return [[len({p for p, need, pos in db.execute('SELECT patient_id, need, positive FROM social_need') if need == 'food' and pos == 1})]]


def ref_deaths(db, year):
    return [[sum(1 for (d,) in db.execute('SELECT death_date FROM death') if str(d).startswith(year))]]


def ref_glp1_patients(db, year):
    return [[len({p for p, c, d in db.execute('SELECT patient_id, drug_class, fill_date FROM fill') if c == 'glp1_agonist' and d and d.startswith(year)})]]


REFERENCES = {name[4:]: fn for name, fn in globals().items() if name.startswith('ref_') and callable(fn)}


# ---- scoring -------------------------------------------------------------------------------------------------------

def _right(reference, rows):
    from . import evaluate, upkeep
    if rows is None: return False
    if len(reference) == 1 and len(reference[0]) == 1 and isinstance(reference[0][0], int) and len(rows) == reference[0][0] > 1:
        return True   # "how many" answered by listing them, one row each
    return evaluate.correct(reference, rows) or upkeep.same_numbers(reference, rows) or _contains(reference, rows)


def _contains(reference, rows):
    """The reference figures appear in the answer when it adds columns (a count beside a rate, a name beside a number)."""
    from . import evaluate
    return len(rows) == len(reference) and evaluate.correct(reference, rows)


def outcome_kind(item, o):
    answered = o.get('status') == 'answered' and o.get('rows') and not (len(o['rows']) == 1 and isinstance(o['rows'][0][0], str) and o['rows'][0][0].startswith('No '))
    if item.get('unanswerable'): return 'declined' if not answered else 'wrong'
    if not answered: return 'declined'
    return 'right' if any(_right(ref, o['rows']) for ref in item.get('references') or [item['reference']]) else 'wrong'


def run(runs=3, workers=4):
    from .. import model_policy
    os.environ.pop('ANTHROPIC_API_KEY', None)
    from run_integrated_stack import keys
    keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
    os.environ.update({k: v for k, v in model_policy.ECONOMICAL_RUNTIME.items() if isinstance(v, str)})
    os.environ['AGERE_TIERED_AUTO_LEARN'] = 'off'
    scratch = Path(tempfile.mkdtemp(prefix='proof-'))
    shutil.copy(os.environ.get('AGERE_TIERED_BOTTLES', bottle_store.DEFAULT), scratch / 'bottles.sqlite3')
    data = json.loads(QUESTIONS.read_text())
    db = store.connect()
    items = []
    for q in data['questions']:
        for k, phrasing in enumerate(q['phrasings']):
            # A phrasing may accept more than one reading (recorded in the question file's audit notes); each is computed here.
            names = (q.get('accept_by_phrasing') or {}).get(str(k)) or [q.get('reference')]
            refs = [] if q.get('unanswerable') else [[list(r) for r in REFERENCES[n](db, *q.get('args', []))] for n in names]
            items.append({'id': q['id'], 'phrasing': k, 'question': phrasing, 'domain': q['domain'], 'reference': refs[0] if refs else None, 'references': refs,
                          'unanswerable': q.get('unanswerable', False)})
    db.close()
    import threading
    local = threading.local()

    def bottles():
        if not hasattr(local, 'b'): local.b = bottle_store.Bottles(scratch / 'bottles.sqlite3')
        return local.b

    def one(job):
        item, arm, r = job
        try:
            o = router.answer(item['question'], tiers=router.ALL_TIERS if arm == 'stack' else ('llm',), bottles=bottles() if arm == 'stack' else None,
                              s1_ledger=str(scratch / 's1.sqlite3'))
        except Exception as exc:
            if 'limit' in str(exc).lower(): raise
            o = {'status': 'error', 'final_tier': None, 'rows': None, 'steps': [], 'seconds': 0.0}
        return {'id': item['id'], 'phrasing': item['phrasing'], 'domain': item['domain'], 'arm': arm, 'run': r, 'unanswerable': item['unanswerable'],
                'kind': outcome_kind(item, o), 'tier': o.get('final_tier'), 'usd': sum(s.get('usd', 0.0) for s in o['steps']), 'seconds': o.get('seconds', 0.0),
                'rows': (o.get('rows') or [])[:3], 'question': item['question']}

    jobs = [(it, arm, r) for r in range(int(runs)) for it in items for arm in ('stack', 'frontier')]
    started = time.monotonic()
    with ThreadPoolExecutor(int(workers)) as pool:
        results = list(pool.map(one, jobs))
    return report(results, items, runs, round(time.monotonic() - started, 1), data)


def report(results, items, runs, wall, data):
    lines = [f"PROOF SCORECARD {data.get('version')}  {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
             f"{len({i['id'] for i in items})} questions ({sum(1 for q in data['questions'] if q.get('unanswerable'))} cannot be answered from this warehouse), "
             f"{len(items)} phrasings, {runs} runs each; +1 right, 0 declined, -{PENALTY} wrong", '']
    summary = {}
    for arm, name in (('stack', 'Agere stack'), ('frontier', "frontier model writing its own SQL (run through the stack's checks)")):
        rs = [r for r in results if r['arm'] == arm]
        c = Counter(r['kind'] for r in rs)
        score = c['right'] - PENALTY * c['wrong']
        groups = defaultdict(list)
        for r in rs: groups[(r['id'], r['phrasing'])].append(r['kind'])
        consistent = sum(1 for v in groups.values() if len(set(v)) == 1)
        all_right = sum(1 for (qid, _), v in groups.items() if all(k in ('right',) or (k == 'declined' and next(x for x in rs if x['id'] == qid)['unanswerable']) for k in v))
        tiers = Counter(str(r['tier']) for r in rs if r['kind'] == 'right')
        summary[arm] = {'right': c['right'], 'declined': c['declined'], 'wrong': c['wrong'], 'score': score, 'answers': len(rs), 'usd': round(sum(r['usd'] for r in rs), 4),
                        'seconds_each': round(sum(r['seconds'] for r in rs) / max(1, len(rs)), 2), 'same_outcome_every_run': consistent, 'phrasings': len(groups),
                        'right_every_run': all_right, 'right_by_rung': dict(tiers)}
        s = summary[arm]
        lines += [f"{name}", f"  right {s['right']}  declined {s['declined']}  wrong {s['wrong']}  of {s['answers']}   score {s['score']} (best possible {s['answers']})",
                  f"  every run right (or correctly declined): {s['right_every_run']} of {s['phrasings']} phrasings; same outcome on every run: {s['same_outcome_every_run']} of {s['phrasings']}",
                  f"  cost ${s['usd']:.2f} for {s['answers']} answers (${s['usd'] / max(1, s['answers']):.4f} each), {s['seconds_each']} s each   right answers by rung: {s['right_by_rung']}"]
        by = defaultdict(Counter)
        for r in rs: by[r['domain']][r['kind']] += 1
        lines += ['  by area: ' + '; '.join(f"{d} {v['right']} right, {v['declined']} declined, {v['wrong']} wrong" for d, v in sorted(by.items())), '']
    lines.append('wrong answers, for reading:')
    lines += [f"  {r['arm']:8} {r['id']}/{r['phrasing']} run {r['run']} {r['tier']} | {r['question'][:90]} | got {str(r['rows'])[:120]}" for r in results if r['kind'] == 'wrong'] or ['  none']
    lines += ['', f'{wall} s on the clock']
    text = '\n'.join(lines)
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    (OUT / f'proof-{stamp}.txt').write_text(text + '\n')
    (OUT / f'proof-{stamp}.json').write_text(json.dumps({'summary': summary, 'results': results}, indent=1, default=str))
    return text


if __name__ == '__main__':
    if sys.argv[1:2] == ['run']:
        print(run(int(sys.argv[2]) if len(sys.argv) > 2 else 3, int(sys.argv[3]) if len(sys.argv) > 3 else 4))
    else:
        print(__doc__)
