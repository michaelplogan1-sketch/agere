"""Independent known-answer references for the clinical workflow tasks CT-01..CT-10, and their scorer.

Written straight from the task definitions (releases/<rel>/questions/clinical_tasks.jsonl and
docs/agere/REQUEST_CLINICAL_WORKFLOW_TASKS.md) against the delivered tables, sharing NO code with the engine
(clinical_core/agere/clinical/clinical_tasks.py): drugs are matched by NAME (not drug_class), fills are paid payer pharmacy
claims (counts_as_fill) plus pharmacy dispenses whose latest status for the fill is 'dispensed', conditions are ICD-10
prefixes on the raw diagnosis tables, labs are the raw measurement rows. Never reads the generator's private/ truth.

    .venv/bin/python bench/clinical_task_reference.py [--build <agere build>] [--index 2028-12-31]
prints per task and step: precision / recall / F1 of the engine's patient (or patient x attribute) keys against the
reference, aggregates only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys

REPO = Path(__file__).resolve().parents[1]
DRUGS = {  # task drug list: name -> (group, name patterns)
    'carvedilol': ('beta-blocker', ['carvedilol']), 'bisoprolol': ('beta-blocker', ['bisoprolol']),
    'metoprolol succinate': ('beta-blocker', ['metoprolol succinate', 'metoprolol er', 'metoprolol xl', 'metoprolol extended']),
    'sacubitril/valsartan': ('RAAS', ['sacubitril']),
    **{n: ('RAAS', [n]) for n in ('lisinopril', 'enalapril', 'ramipril', 'captopril', 'benazepril', 'quinapril', 'fosinopril',
                                  'perindopril', 'trandolapril', 'losartan', 'valsartan', 'candesartan', 'irbesartan',
                                  'olmesartan', 'telmisartan')},
    'spironolactone': ('MRA', ['spironolactone']), 'eplerenone': ('MRA', ['eplerenone']),
    **{n: ('SGLT2i', [n]) for n in ('dapagliflozin', 'empagliflozin', 'canagliflozin', 'ertugliflozin', 'sotagliflozin')},
    **{n: ('GLP-1 RA', [n]) for n in ('semaglutide', 'liraglutide', 'dulaglutide', 'exenatide', 'lixisenatide', 'tirzepatide')},
    **{n: ('statin', [n]) for n in ('atorvastatin', 'rosuvastatin', 'simvastatin', 'pravastatin', 'lovastatin', 'fluvastatin', 'pitavastatin')},
    **{n: ('OAC', [n]) for n in ('apixaban', 'rivaroxaban', 'dabigatran', 'edoxaban', 'warfarin')},
    **{n: ('any beta-blocker', [n]) for n in ('atenolol', 'metoprolol', 'propranolol', 'nadolol', 'nebivolol', 'labetalol')},
}
PILLARS = ('beta-blocker', 'RAAS', 'MRA', 'SGLT2i')
ASCVD = ('I20', 'I21', 'I22', 'I23', 'I24', 'I25', 'I63', 'G45', 'I70', 'I739', 'Z951', 'Z955')
TARGET = {'carvedilol': 50, 'metoprolol succinate': 200, 'bisoprolol': 10, 'sacubitril/valsartan': 400, 'lisinopril': 20,
          'enalapril': 20, 'ramipril': 10, 'captopril': 150, 'losartan': 150, 'valsartan': 320, 'candesartan': 32,
          'spironolactone': 25, 'eplerenone': 50, 'dapagliflozin': 10, 'empagliflozin': 10}


def drug_name(text):
    """Task rule: sacubitril/valsartan before valsartan; metoprolol counts as succinate only when it says so."""
    t = (text or '').lower()
    if 'sacubitril' in t: return 'sacubitril/valsartan'
    if 'metoprolol' in t:
        return 'metoprolol succinate' if re.search(r'succinate|\ber\b|\bxl\b|extended|24 ?hr', t) else 'metoprolol'
    for name, (_, pats) in DRUGS.items():
        if any(p in t for p in pats): return name
    return None


def groups(name):
    if name is None: return set()
    g = {DRUGS[name][0]} if name in DRUGS else set()
    if g & {'beta-blocker'}: g.add('any beta-blocker')
    return g


class Reference:
    def __init__(self, semantic, index):
        self.db = sqlite3.connect(f'file:{semantic}?mode=ro', uri=True)
        self.index = index
        dead = {r[0] for r in self.db.execute('SELECT patient_id FROM death WHERE substr(death_date,1,10) <= ?', (index,))}
        self.people = {r[0]: (r[1], (r[2] or '')[:1].upper()) for r in self.db.execute('SELECT patient_id, birth_date, sex_reported FROM patient')
                       if r[0] not in dead}
        self._fills = None

    # -- conditions: raw tables, prefix match with dots ignored
    def codes(self, prefixes, since=None):
        out = {}
        rows = list(self.db.execute("SELECT patient_id, icd10, service_from FROM payer_claim_diagnosis WHERE counts_in_metrics = 1"))
        rows += list(self.db.execute("SELECT patient_id, icd10, event_date FROM encounter_diagnosis"))
        rows += list(self.db.execute("SELECT patient_id, icd10, first_recorded FROM problem_list_entry"))
        rows += list(self.db.execute("SELECT patient_id, icd10, coalesce(event_date, recorded_date) FROM condition WHERE is_diagnosis = 1 "
                                     "OR (is_diagnosis IS NULL AND diagnosis_basis = 'ehr_problem_list')"))
        for pid, code, day in rows:
            c, d = (code or '').replace('.', '').upper(), (day or '')[:10]
            if pid in self.people and d and d <= self.index and (since is None or d > since) and c.startswith(tuple(prefixes)):
                out[pid] = max(out.get(pid, ''), d)
        return out

    # -- fills: paid payer pharmacy claims + dispenses whose latest status is dispensed
    def fills(self):
        if self._fills is None:
            out = []
            for pid, day, name, supply, qty in self.db.execute(
                    "SELECT patient_id, fill_date, drug_name, days_supply, quantity FROM payer_pharmacy_claim WHERE counts_as_fill = 1"):
                out.append((pid, (day or '')[:10], name, supply, {'quantity_dispensed': qty, 'days_supply': supply}))
            latest = {}
            for rid, pid, day, rec, payload in self.db.execute(
                    "SELECT record_id, patient_id, event_date, recorded_date, payload FROM record WHERE stream = 'pharmacy_dispenses'"):
                p = json.loads(payload)
                key = p.get('fill_id') or rid
                if key not in latest or (rec or '', rid) > latest[key][0]: latest[key] = ((rec or '', rid), pid, day, p)
            first = {}
            for (_, pid, day, p) in latest.values():
                if p.get('status') == 'dispensed':
                    out.append((pid, (day or '')[:10], p.get('medication_name'), p.get('days_supply_reported'), p))
            self._fills = [f for f in out if f[0] in self.people and f[1] and f[1] <= self.index]
        return self._fills

    def on(self):
        """{patient: {group}} on the index date: fill date + days supply >= index - 90 days."""
        from datetime import date, timedelta
        floor = (date.fromisoformat(self.index) - timedelta(days=90)).isoformat()
        out = {}
        for pid, day, name, supply, _ in self.fills():
            end = (date.fromisoformat(day) + timedelta(days=int(supply or 30))).isoformat()
            if end >= floor: out.setdefault(pid, set()).update(groups(drug_name(name)))
        return out

    def lab(self, concept):
        out = {}
        for pid, day, v in self.db.execute(
                "SELECT patient_id, substr(event_date,1,10), value_canonical FROM measurement WHERE concept = ? AND result_current = 1 "
                "AND excluded_reason IS NULL AND value_canonical IS NOT NULL", (concept,)):
            if pid in self.people and day and day <= self.index and (pid not in out or (day, v) > out[pid]): out[pid] = (day, v)
        return out

    def ef(self):
        out = {}
        for pid, day, v in self.db.execute(
                "SELECT patient_id, substr(event_date,1,10), value FROM note_fact WHERE fact_type = 'ejection_fraction' AND coalesce(negated,0) = 0"):
            try: v = float(v)
            except (TypeError, ValueError): continue
            if pid in self.people and day and day <= self.index and (pid not in out or (day, -v) > (out[pid][0], -out[pid][1])):
                out[pid] = (day, v)
        return out

    def age(self, pid):
        b = (self.people[pid][0] or '')[:10]
        if not b: return None
        return int(self.index[:4]) - int(b[:4]) - ((self.index[5:]) < b[5:])

    # -- tasks
    def hfref(self):
        hf, coded, ef = self.codes(['I50']), self.codes(['I502', 'I504']), self.ef()
        return {p for p in hf if p in coded or (p in ef and ef[p][1] <= 40)}

    def tasks(self):
        on = self.on()
        hf = self.hfref()
        ct01b = {(p, g) for p in hf for g in PILLARS if g not in on.get(p, set())}
        t2d, ascvd, ckd = self.codes(['E11']), self.codes(ASCVD), self.codes(['N18'])
        ct04a = {p for p in t2d if p in ascvd or p in ckd}
        ct04b = {p for p in ct04a if not on.get(p, set()) & {'SGLT2i', 'GLP-1 RA'}}
        c = {k: self.codes(v) for k, v in {'hf': ['I50'], 'htn': ['I10', 'I11', 'I12', 'I13', 'I15', 'I16'], 'dm': ['E10', 'E11'],
                                           'stroke': ['I63', 'G45', 'I74', 'Z8673'], 'vasc': ['I21', 'I22', 'I252', 'I70', 'I739']}.items()}
        score = lambda p: (sum(w for k, w in (('hf', 1), ('htn', 1), ('dm', 1), ('stroke', 2), ('vasc', 1)) if p in c[k])
                           + (2 if (self.age(p) or 0) >= 75 else 1 if (self.age(p) or 0) >= 65 else 0) + (self.people[p][1] == 'F'))
        ct05a = {(p, score(p)) for p in self.codes(['I48']) if score(p) >= 2}
        ct05b = {p for p, _ in ct05a if 'OAC' not in on.get(p, set())}
        uacr = self.lab('uacr')
        ct06a = {p for p in self.codes(['N183', 'N184', 'N185', 'N186']) if p in uacr and uacr[p][1] >= 30}
        ldl = {p for p, in self.db.execute("SELECT DISTINCT patient_id FROM measurement WHERE concept = 'ldl' AND result_current = 1 AND "
                                           "excluded_reason IS NULL AND value_canonical >= 190 AND substr(event_date,1,10) <= ?", (self.index,))}
        dm = self.codes(['E10', 'E11'])
        ct07a = {p for p in self.people if p in ascvd or (p in dm and 40 <= (self.age(p) or 0) <= 75) or p in ldl}
        k = self.lab('potassium')
        ct09 = {p for p in self.people if 'MRA' in on.get(p, set()) and p in k and k[p][1] >= 5.5}
        from datetime import date
        y = date.fromisoformat(self.index)
        mi = self.codes(['I21', 'I22'], since=y.replace(year=y.year - 1).isoformat())
        ct08_bb = {p for p in mi if 'any beta-blocker' not in on.get(p, set())}
        return {
            'CT-01': {'a': {(p,) for p in hf}, 'b': ct01b},
            'CT-03': {'a': self.underdosed(hf, on)},
            'CT-04': {'a': {(p,) for p in ct04a}, 'b': {(p,) for p in ct04b}},
            'CT-05': {'a': ct05a, 'b': {(p,) for p in ct05b}},
            'CT-06': {'a': {(p,) for p in ct06a}, 'b': {(p,) for p in ct06a if 'RAAS' not in on.get(p, set())},
                      'c': {(p,) for p in ct06a if 'SGLT2i' not in on.get(p, set())}},
            'CT-07': {'a': {(p,) for p in ct07a}, 'b': {(p,) for p in ct07a if 'statin' not in on.get(p, set())}},
            'CT-08': {'a': {(p,) for p in mi}, 'b_beta_blocker': {(p,) for p in ct08_bb}},
            'CT-09': {'a': {(p,) for p in ct09}},
            'CT-10': {'a_structured': self.allergy_conflicts()},
        }

    def underdosed(self, hf, on):
        from datetime import date, timedelta
        floor = (date.fromisoformat(self.index) - timedelta(days=90)).isoformat()
        cur = {}
        for pid, day, name, supply, payload in self.fills():
            n = drug_name(name)
            if pid not in hf or n not in TARGET: continue
            g = DRUGS[n][0]
            if (date.fromisoformat(day) + timedelta(days=int(supply or 30))).isoformat() < floor: continue
            if (pid, g) not in cur or (day, n) > cur[(pid, g)][:2]: cur[(pid, g)] = (day, n, name, payload)
        out = set()
        for (pid, g), (day, n, name, p) in cur.items():
            mg = self.daily_mg(name, p)
            if mg is not None and mg < TARGET[n]: out.add((pid, n))
        return out

    @staticmethod
    def daily_mg(name, p):
        t = (name or '').lower()
        m = re.search(r'(\d+(?:\.\d+)?)\s*(?:mg)?\s*/\s*(?:[a-z]+\s*)?(\d+(?:\.\d+)?)\s*mg', t) if 'sacubitril' in t else None
        unit = float(m.group(1)) + float(m.group(2)) if m else (float(re.search(r'(\d+(?:\.\d+)?)\s*mg', t).group(1)) if re.search(r'(\d+(?:\.\d+)?)\s*mg', t) else None)
        q, d = p.get('quantity_dispensed') or p.get('quantity'), p.get('days_supply') or p.get('days_supply_reported')
        if unit and q and d: return round(unit * float(q) / float(d), 2)
        dose, times = p.get('label_dose'), p.get('label_times_per_day')
        if unit and isinstance(dose, (int, float)) and times: return round(unit * dose * times, 2)
        if isinstance(dose, str) and re.search(r'(\d+(?:\.\d+)?)\s*mg', dose):
            freq = {'daily': 1, 'qd': 1, 'qhs': 1, 'qam': 1, 'bid': 2, 'twice daily': 2, 'tid': 3}.get((p.get('label_frequency') or '').lower())
            if freq: return round(float(re.search(r'(\d+(?:\.\d+)?)\s*mg', dose).group(1)) * freq, 2)
        return None

    def allergy_conflicts(self):
        """(patient, drug group) where a structured allergy names a drug that is filled after the allergy was recorded."""
        out = set()
        allergies = list(self.db.execute("SELECT patient_id, lower(coalesce(substance, substance_received)), substr(first_recorded,1,10) "
                                         "FROM allergy_entry WHERE coalesce(no_known_allergies,0) = 0 AND first_recorded IS NOT NULL"))
        fills = {}
        for pid, day, name, *_ in self.fills(): fills.setdefault(pid, []).append((day, (name or '').lower()))
        for pid, sub, day in allergies:
            if pid not in self.people or not sub or day > self.index: continue
            for fday, name in fills.get(pid, []):
                if fday > day and re.search(r'\b' + re.escape(sub.split()[0].rstrip('s')) + r'(?!te)', name): out.add((pid, sub.split()[0])); break
        return out


def prf(got, ref):
    tp = len(got & ref)
    p = tp / len(got) if got else (1.0 if not ref else 0.0)
    r = tp / len(ref) if ref else 1.0
    return {'precision': round(p, 4), 'recall': round(r, 4), 'f1': round(2 * p * r / (p + r), 4) if p + r else 0.0,
            'engine': len(got), 'reference': len(ref)}


def engine_keys(build_stack, index, as_of):
    """The engine's answer keys per task/step, from clinical_tasks.execute (the same engine the question path runs)."""
    sys.path.insert(0, str(REPO))
    from clinical_core.agere.clinical import clinical_tasks as C
    out = {}
    for task in C.TASKS:
        r = C.execute(build_stack, None, {'task': task, 'step': 'all', 'index_date': index}, as_of)
        s = r['steps']
        out[task] = s
    keys = {
        'CT-01': {'a': {(x['patient_id'],) for x in out['CT-01']['a']}, 'b': {(x['patient_id'], g) for x in out['CT-01']['b'] for g in x['missing_pillars']}},
        'CT-03': {'a': {(x['patient_id'], x['drug']) for x in out['CT-03']['a']}},
        'CT-04': {k: {(x['patient_id'],) for x in out['CT-04'][k]} for k in ('a', 'b')},
        'CT-05': {'a': {(x['patient_id'], x['cha2ds2_vasc']) for x in out['CT-05']['a']}, 'b': {(x['patient_id'],) for x in out['CT-05']['b']}},
        'CT-06': {k: {(x['patient_id'],) for x in out['CT-06'][k]} for k in ('a', 'b', 'c')},
        'CT-07': {k: {(x['patient_id'],) for x in out['CT-07'][k]} for k in ('a', 'b')},
        'CT-08': {'a': {(x['patient_id'],) for x in out['CT-08']['a']},
                  'b_beta_blocker': {(x['patient_id'],) for x in out['CT-08']['b'] if 'beta-blocker' in x['missing']}},
        'CT-09': {'a': {(x['patient_id'],) for x in out['CT-09']['a']}},
        'CT-10': {'a_structured': {(x['patient_id'], x['drug']) for x in out['CT-10']['a']}},
    }
    return keys, out


def score(build, index, as_of=None):
    build = Path(build)
    as_of = as_of or index
    semantic = build/'stack/semantic.sqlite3'
    ref = Reference(semantic, index).tasks()
    got, raw = engine_keys(build/'stack', index, as_of or index)
    out = {}
    for task, steps in ref.items():
        out[task] = {}
        for step, keys in steps.items():
            g = got[task][step]
            if step == 'a_structured':  # recall only: the structured allergy subset of the engine's conflicts
                drugs = {(p, d) for p, d in keys}
                hit = {(p, d) for p, d in drugs if any(gp == p and (d in (gd or '') or d in json.dumps(x).lower())
                                                       for (gp, gd) in g for x in raw[task]['a'] if x['patient_id'] == gp)}
                out[task][step] = {'recall': round(len(hit) / len(drugs), 4) if drugs else 1.0, 'reference': len(drugs), 'engine': len(g)}
            else:
                out[task][step] = prf(g, keys)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build', type=Path, default=Path(json.loads((REPO/'config/agere/active_warehouse.json').read_text())['build']))
    ap.add_argument('--index', default='2028-12-31')
    ap.add_argument('--as-of', default=None)
    a = ap.parse_args(argv)
    as_of = a.as_of or json.loads((REPO/'config/agere/active_warehouse.json').read_text())['snapshot']['as_of']
    print(json.dumps(score(a.build, a.index, as_of), indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
