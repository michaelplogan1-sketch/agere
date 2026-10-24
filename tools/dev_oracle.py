"""Development oracle for the decision context: 35 questions, each with the direct SQL that defines its exact patient set.

    python tools/dev_oracle.py --url http://localhost:8885            # the live service (X-Agere-Evaluation: 1)
    python tools/dev_oracle.py --inprocess [--stack <stack dir>]      # this checkout, in process, no local model
    python tools/dev_oracle.py --only 3,5 --out <file.json>

The SQL follows the decision context (clinical_core/agere/clinical/decision_context.py), written here independently of it so the
two cannot agree by sharing a bug: patients are LIVING patients (no row in `death`) unless the question asks for the deceased;
a lab means the LATEST result (measurement_latest) unless the question says ever / any / at any time; a condition is the governed
evidence (EHR diagnosis or problem-list condition rows, plus counted payer-claim diagnoses, plus laboratory-evidence rows for
diabetes and CKD); "last year" is the 12 months ending at the as-of date; a calendar year is that calendar year.

Per question: status, exact patient-set match, false positives, false negatives, tier, seconds, cost. Synthetic data only. The
questions were written against the semantic tables, never from a sealed bank.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_STACK = ROOT / 'artifacts/agere-healthcare-v28-sample-0.30/stack'

DX = lambda *prefixes: '(' + ' OR '.join(f"icd10 LIKE '{p}%'" for p in prefixes) + ')'
# a condition: EHR diagnosis rows (problem-list rows with an unknown diagnosis flag included) + counted payer-claim diagnoses
# [+ laboratory-evidence condition rows of the named concepts: diabetes and CKD only]
EVIDENCE = ("SELECT patient_id, icd10, event_date FROM condition WHERE is_diagnosis = 1 OR (is_diagnosis IS NULL AND diagnosis_basis = 'ehr_problem_list') "
            "UNION ALL SELECT patient_id, icd10, service_from FROM payer_claim_diagnosis WHERE counts_in_metrics = 1")


def HAS(*prefixes, lab=()):
    sql = f"SELECT patient_id FROM ({EVIDENCE}) WHERE {DX(*prefixes)} AND (event_date IS NULL OR event_date <= :as_of)"
    if lab:  # a subselect: compound operators associate left to right, so the UNION must not leak into INTERSECT / EXCEPT
        # the lab rule (Mike 2026-10-24) from the independent dev-set definitions: diabetes = any HbA1c >= 6.5 %; CKD = two eGFR < 60 90+ days apart
        from tools.dev_set.definitions import LAB_EVIDENCE
        # an HbA1c cannot tell type 1 from type 2: the rule adds to "diabetes", never to a typed diabetes concept
        rule = LAB_EVIDENCE['diabetes'] if 'diabetes' in lab else (LAB_EVIDENCE['ckd'] if any('kidney' in n or 'ckd' in n for n in lab) else None)
        if rule: sql = f"SELECT patient_id FROM ({sql} UNION SELECT patient_id FROM ({rule}))"
    return sql


YEAR = "date(:as_of, '-12 months')"
FILL = lambda cls: f"SELECT patient_id FROM fill WHERE drug_class = '{cls}' AND fill_date > {YEAR} AND fill_date <= :as_of"
ANY_FILL = lambda *classes: 'SELECT patient_id FROM (' + ' UNION '.join(FILL(c) for c in classes) + ')'
LAB = lambda concept, cmp, value: (f"SELECT patient_id FROM measurement_latest WHERE concept = '{concept}' AND value_canonical {cmp} {value} "
                                   "AND event_date <= :as_of")
STAY = lambda kind, year: f"SELECT patient_id FROM utilization_event WHERE kind = '{kind}' AND substr(event_date, 1, 4) = '{year}'"
AGE = "(CAST(strftime('%Y', :as_of) AS INT) - CAST(strftime('%Y', birth_date) AS INT) - (strftime('%m-%d', :as_of) < strftime('%m-%d', birth_date)))"
CKD = ('N18', 'E08.2', 'E09.2', 'E10.2', 'E11.2', 'E13.2', 'I12', 'I13')
HTN = ('I10', 'I11', 'I12', 'I13', 'I15', 'I16')
DIABETES = ('E08', 'E09', 'E10', 'E11', 'E13')
HF = ('I50', 'I11.0', 'I13.0', 'I13.2')
DEPRESSION = ('F32', 'F33', 'F34.1', 'F53')  # the library concept
CAD = ('I20', 'I21', 'I22', 'I23', 'I24', 'I25')
OBESITY = ('E66.0', 'E66.1', 'E66.2', 'E66.8', 'E66.9', 'E66.01')  # the library concept: BMI 30+, not overweight (E66.3)
DM_LAB = ('diabetes', 'type_1_diabetes', 'type_2_diabetes')
CKD_LAB = ('chronic_kidney_disease',)
DIAB = lambda: HAS(*DIABETES, lab=DM_LAB)
CKDS = lambda: HAS(*CKD, lab=CKD_LAB)

# (question, set SQL, denominator SQL or None, what is asked about the deceased: False = living only)
DEV = [
    ('How many patients have opioid use disorder?', HAS('F11')),
    ('How many patients with diabetes have an HbA1c over 9?', f"{DIAB()} INTERSECT {LAB('hba1c', '>', 9)}"),
    ('Which patients have heart failure but no SGLT2 inhibitor fill in the last year?', f"{HAS(*HF)} EXCEPT {FILL('sglt2_inhibitor')}"),
    ('How many patients have atrial fibrillation but no anticoagulant fill in the last year?', f"{HAS('I48')} EXCEPT {FILL('anticoagulant')}"),
    ('How many patients with COPD had an inpatient stay in 2029?', f"{HAS('J43', 'J44')} INTERSECT {STAY('inpatient', 2029)}"),  # COPD = J43 emphysema + J44 (the library concept)
    ('How many patients have atrial fibrillation and heart failure?', f"{HAS('I48')} INTERSECT {HAS(*HF)}"),
    ('How many patients have type 2 diabetes and chronic kidney disease?', f"{HAS('E11', lab=('type_2_diabetes',))} INTERSECT {CKDS()}"),
    ('How many patients have COPD or asthma?', HAS('J43', 'J44', 'J45')),
    ('How many patients with hypertension have no statin fill in the last year?', f"{HAS(*HTN)} EXCEPT {FILL('statin')}"),
    ('How many patients with chronic kidney disease have an eGFR below 30?', f"{CKDS()} INTERSECT {LAB('egfr', '<', 30)}"),
    ('How many patients have an LDL over 190?', LAB('ldl', '>', 190)),
    ('How many patients have had an ED visit in 2029?', STAY('ed_visit', 2029)),
    ('How many patients with diabetes had an inpatient stay in 2028?', f"{DIAB()} INTERSECT {STAY('inpatient', 2028)}"),
    ('What share of patients with heart failure had an ED visit in 2029?', f"{HAS(*HF)} INTERSECT {STAY('ed_visit', 2029)}", HAS(*HF)),
    ('How many patients with diabetes have a metformin fill in the last year but no GLP-1 agonist fill in the last year?',
     f"{DIAB()} INTERSECT {FILL('metformin')} EXCEPT {FILL('glp1_agonist')}"),
    ('How many patients have a statin fill in the last year and an LDL over 100?', f"{FILL('statin')} INTERSECT {LAB('ldl', '>', 100)}"),
    ('How many patients have a hemoglobin below 8?', LAB('hemoglobin', '<', 8)),
    ('How many female patients are 85 or older?', f"SELECT patient_id FROM patient WHERE sex_reported = 'F' AND {AGE} >= 85"),
    ('How many patients on Medicare have heart failure?',
     f"{HAS(*HF)} INTERSECT SELECT patient_id FROM patient WHERE coverage_type IN ('medicare', 'dual', 'medigap')"),
    ('How many patients with atrial fibrillation have no ED visit in 2029?', f"{HAS('I48')} EXCEPT {STAY('ed_visit', 2029)}"),
]
HELDOUT = [
    ('Count the patients with major depression who had no SSRI or SNRI fill in the past year.', f"{HAS(*DEPRESSION)} EXCEPT {ANY_FILL('ssri', 'snri')}"),
    ('Which patients with coronary artery disease have an LDL of 100 or higher?', f"{HAS(*CAD)} INTERSECT {LAB('ldl', '>=', 100)}"),
    ('How many people over 65 have dementia?', f"SELECT patient_id FROM patient WHERE {AGE} > 65 INTERSECT {HAS('F01', 'F02', 'F03', 'G30')}"),
    ('Number of patients with schizophrenia or bipolar disorder', HAS('F20', 'F31')),
    ('How many patients with obesity also have diabetes?', f"{HAS(*OBESITY)} INTERSECT {DIAB()}"),
    ('List the patients with alcohol use disorder who went to the ED in 2029.', f"{HAS('F10')} INTERSECT {STAY('ed_visit', 2029)}"),
    ('How many patients with hypothyroidism filled levothyroxine in the last year?', f"{HAS('E03')} INTERSECT {FILL('levothyroxine')}"),
    ('What share of patients with diabetes have a statin fill in the last year?', f"{DIAB()} INTERSECT {FILL('statin')}", DIAB()),
    ('How many patients have a potassium above 5.5?', LAB('potassium', '>', 5.5)),
    ('Which patients with heart failure had an observation stay in 2029?', f"{HAS(*HF)} INTERSECT {STAY('observation', 2029)}"),
    ('How many male patients under 40 have hypertension?', f"SELECT patient_id FROM patient WHERE sex_reported = 'M' AND {AGE} < 40 INTERSECT {HAS(*HTN)}"),
    ('How many patients with COPD have no inhaled corticosteroid, LAMA or LABA fill in the last year?',
     f"{HAS('J43', 'J44')} EXCEPT {ANY_FILL('inhaled_corticosteroid', 'lama', 'laba')}"),
    ('How many patients have a TSH over 10?', LAB('tsh', '>', 10)),
    ('How many patients with a history of stroke have no antiplatelet or anticoagulant fill in the last year?',
     f"{HAS('I63')} EXCEPT {ANY_FILL('antiplatelet', 'anticoagulant')}"),
    ('How many patients have stage 4 or stage 5 chronic kidney disease?', HAS('N18.4', 'N18.5')),
]
QUESTIONS = [(i, *q) for i, q in enumerate(DEV + HELDOUT, 1)]

# the question asks for the deceased: the oracle then keeps everyone (mirrors decision_context.population)
ASKS_DECEASED = re.compile(r'\b(?:deceased|died|dead|death|deaths|mortality|dying|passed away|including (?:the )?(?:deceased|dead)|'
                           r'all patients ever|ever enrolled)\b', re.I)


def truth_sets(db, as_of, question, sql, denominator=None):
    """The exact patient set (and the share's denominator set) for one question."""
    # living people: no death row, and a duplicate chart of a person already counted is not a second patient (decision context)
    living = ('' if ASKS_DECEASED.search(question) else ' WHERE patient_id NOT IN (SELECT patient_id FROM death)'
              " AND patient_id NOT IN (SELECT patient_id FROM patient WHERE data_segment = 'duplicate_chart')")
    wrap = lambda s: {r[0] for r in db.execute(f'SELECT DISTINCT patient_id FROM ({s}){living}', {'as_of': as_of})}
    return wrap(sql), (wrap(denominator) if denominator else None)


def ask_url(url, question, timeout=300):
    body = json.dumps({'question': question}).encode()
    req = urllib.request.Request(url.rstrip('/') + '/api/stack/compose', data=body,
                                 headers={'Content-Type': 'application/json', 'X-Agere-Evaluation': '1'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        text = exc.read().decode('utf-8', 'replace')
        try: return {'error': json.loads(text).get('error', text)[:300]}
        except ValueError: return {'error': text[:300]}
    except Exception as exc:
        return {'error': f'{type(exc).__name__}: {exc}'[:300]}


def make_inprocess(stack_dir):
    """Stack + conversation.handle_compose in this process. Local models stay off; a loaded MLX module aborts the run."""
    os.environ.update(AGERE_EVALUATION='1', AGERE_LOCAL_TYPED='off', AGERE_LOCAL_NORMALIZER='off', AGERE_LOCAL_QWEN='off',
                      AGERE_LOCAL_MODEL='off', AGERE_LOCAL_SQL='off', AGERE_BOTTLED='off', AGERE_BOTTLED_COHORT='off', AGERE_LOCAL_CANDIDATE='off')
    blocked = []

    class NoLocalModel:  # nothing may import MLX in this process: an attempt is recorded and fails like an absent runtime
        def find_spec(self, name, path=None, target=None):
            if name.split('.')[0] in ('mlx', 'mlx_lm'):
                blocked.append(name)
                if os.environ.get('DEV_ORACLE_TRACE'): import traceback; traceback.print_stack()
                raise ImportError(f'{name}: local models are disabled in the in-process oracle')
    from run_integrated_stack import keys
    keys()
    from clinical_core.agere.clinical.stack_runtime import Stack
    from clinical_core.agere.clinical import conversation
    stack = Stack(str(stack_dir), allow_live=True)
    sys.meta_path.insert(0, NoLocalModel())  # after the encoders loaded (transformers probes for mlx while importing)

    def ask(question):
        try:
            result = conversation.handle_compose(stack, {'question': question})
        except Exception as exc:
            return {'error': f'{type(exc).__name__}: {exc}'[:300]}
        if blocked:
            raise SystemExit(f'a local model tried to load ({blocked[0]}); stopping')
        return result
    return ask


def score(answer, truth, denominator):
    """-> row fields comparing one compose response with the oracle sets."""
    if 'error' in answer or not isinstance(answer.get('answer'), dict):
        return {'status': 'refused', 'reason': answer.get('error') or (answer.get('answer') or {}).get('status') or 'no answer'}
    a = answer['answer']
    if a.get('status') != 'answered':
        return {'status': a.get('status') or 'refused', 'reason': (a.get('missing') or a.get('limitations') or [''])[0]}
    ids = a.get('patient_ids')
    row = {'status': 'answered', 'count': a.get('count'), 'headline': a.get('headline')}
    if ids is not None:
        got = set(ids)
        row.update(match=got == truth, fp=len(got - truth), fn=len(truth - got), got=len(got))
    else:
        row.update(match=(a.get('count') == len(truth)) if denominator is None else True, fp=None, fn=None, got=a.get('count'), count_only=True)
    if denominator is not None:  # a share: the percent must be the oracle's numerator over its denominator, as well as the numerator set
        want = round(100.0 * len(truth) / len(denominator), 2) if denominator else None
        value = a.get('value')
        if not isinstance(value, (int, float)):
            m = re.search(r'(\d+(?:\.\d+)?)\s*%', str(a.get('headline') or ''))
            value = float(m.group(1)) if m else None
        row.update(oracle_denominator=len(denominator), oracle_share=want, share=None if value is None else round(value, 2))
        row['match'] = bool(row.get('match')) and value is not None and want is not None and abs(value - want) < 0.01
    return row


def summary_line(rows):
    """Exact membership matches are reported apart from count-only matches (no patient_ids returned, only the count agreed)."""
    answered = [r for r in rows if r['status'] == 'answered']
    exact = sum(1 for r in answered if r.get('match') and not r.get('count_only'))
    count_only = sum(1 for r in answered if r.get('match') and r.get('count_only'))
    return f"answered {len(answered)}/{len(rows)}; exact match {exact}; count-only {count_only}; matched total {exact + count_only}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--url', default=None, help='send each question to this service (default http://localhost:8885 unless --inprocess)')
    ap.add_argument('--inprocess', action='store_true')
    ap.add_argument('--stack', type=Path, default=DEFAULT_STACK)
    ap.add_argument('--only', default='')
    ap.add_argument('--out', type=Path, default=None)
    a = ap.parse_args()
    url = a.url or 'http://localhost:8885'
    db = sqlite3.connect(f"file:{a.stack / 'semantic.sqlite3'}?mode=ro", uri=True)
    as_of = db.execute("SELECT value FROM meta WHERE key = 'as_of'").fetchone()[0][:10]
    ask = make_inprocess(a.stack) if a.inprocess else (lambda q: ask_url(url, q))
    rows = []
    for n, question, sql, *rest in QUESTIONS:
        if a.only and str(n) not in a.only.split(','): continue
        truth, denominator = truth_sets(db, as_of, question, sql, rest[0] if rest else None)
        t = time.perf_counter()
        response = ask(question)
        seconds = round(time.perf_counter() - t, 1)
        row = {'n': n, 'question': question, 'oracle': len(truth), **score(response, truth, denominator)}
        telemetry = response.get('cost_telemetry') or {}
        row.update(tier=telemetry.get('answered_by') or (response.get('planner_receipt') or {}).get('route'), seconds=seconds,
                   usd=(response.get('cost_receipt') or {}).get('usd', telemetry.get('api_usd')),
                   decision_context=(response.get('answer') or {}).get('decision_context', {}).get('version') if isinstance(response.get('answer'), dict) else None)
        rows.append(row)
        print(json.dumps(row, default=str), flush=True)
    answered = [r for r in rows if r['status'] == 'answered']
    print(f"\n| # | question | oracle | got | match | FP | FN | status | tier | s | usd |")
    for r in rows:
        print(f"| {r['n']} | {r['question']} | {r['oracle']} | {r.get('got', '')} | {r.get('match', '')} | {r.get('fp', '')} | {r.get('fn', '')} | "
              f"{r['status']} | {r.get('tier')} | {r['seconds']} | {r.get('usd')} |")
    print(summary_line(rows))
    if a.out: a.out.write_text(json.dumps(rows, indent=1, default=str))


if __name__ == '__main__':
    main()
