"""Drug-drug interaction (DDI) screening over concurrent fills ('ddi_screening' operation).

Concurrency: two fills of one patient are concurrent when their supply windows overlap (fill.fill_date .. fill.supply_end, the
semantic layer's dispensed-supply window) by at least min_overlap_days inside the period. Each concurrent pair is screened
against RULES, a curated interaction table: every rule names its two sides (ingredient-name patterns and/or drug classes from
the drug_class vocabulary), the clinical effect, a severity (contraindicated / major / moderate), management text and its
provenance (FDA prescribing information section or FDA Drug Safety Communication; the ONC high-priority DDI list, Phansalkar
et al., JAMIA 2012;19:735-43, where the pair is on it). Interaction facts are public; the table is curated, not exhaustive,
and that is stated in every answer.

Match confidence: 'ingredient' when both drugs matched a rule by ingredient name; 'class' when a side matched only by its
drug_class (e.g. an unnamed NSAID). Class-level matches are the lower-confidence ones: review(stack, report) asks System One
(local student, then the decider bake-off order: Jev -> Perplexity) to confirm up to `confirm_top` of them, live only, with
the rule text and the fill records as evidence; paid calls and cost are returned in the report (new_paid_api_calls) so they
reach the cascade usage and receipts. Dose, route, renal function and timing within the day are not received/considered.
Flags are medication-review prompts, never orders.
"""
from collections import defaultdict
import re

from . import revenue_common as rc

VERSION = 'ddi-screening/2'
OP = 'ddi_screening'
TASK_KEY = 'ddi_screening_confirm'
SEVERITIES = ('contraindicated', 'major', 'moderate')
CONFIRM_OPTIONS = {'yes': 'The interaction applies to this patient\'s concurrent drugs', 'no': 'It does not apply to these drugs'}

FDA_DSC_OPIOID_BZD = 'FDA Drug Safety Communication 2016-08-31: boxed warning for opioids with benzodiazepines'
_NSAID = r'ibuprofen|naproxen|meloxicam|diclofenac|celecoxib|indomethacin|ketorolac|etodolac|nabumetone|piroxicam'
_SSRI = r'fluoxetine|sertraline|escitalopram|citalopram|paroxetine|fluvoxamine'
_SEROTONERGIC = _SSRI + r'|duloxetine|venlafaxine|desvenlafaxine|trazodone|tramadol|meperidine|fentanyl|linezolid'
_OPIOID = r'oxycodone|hydrocodone|morphine|hydromorphone|fentanyl|codeine|tramadol|meperidine|methadone|buprenorphine|tapentadol|oxymorphone'
_BZD = r'diazepam|clonazepam|lorazepam|alprazolam|temazepam|chlordiazepoxide|midazolam|triazolam'
_K_SPARING = r'spironolactone|eplerenone|amiloride|triamterene'
_RAAS_CLASSES = ('ace_inhibitor', 'arb', 'arni')
_CYP3A4_STRONG = r'clarithromycin|erythromycin|itraconazole|ketoconazole|posaconazole|voriconazole|ritonavir|cobicistat|nefazodone'
_QT = r'azithromycin|clarithromycin|erythromycin|ciprofloxacin|levofloxacin|moxifloxacin|ondansetron|haloperidol|methadone|amiodarone|sotalol|quetiapine'

# id: (side_a, side_b, effect, severity, management, provenance). A side = {'names': regex, 'classes': (...)}.
RULES = {
    'opioid_benzodiazepine': ({'names': _OPIOID, 'classes': ('opioid',)}, {'names': _BZD, 'classes': ('benzodiazepine',)},
        'Profound sedation, respiratory depression, coma and death', 'major',
        'Avoid co-prescribing; if unavoidable use lowest doses, shortest duration, naloxone, monitor.', FDA_DSC_OPIOID_BZD),
    'opioid_gabapentinoid': ({'names': _OPIOID, 'classes': ('opioid',)}, {'names': r'gabapentin|pregabalin', 'classes': ('gabapentinoid',)},
        'Serious breathing difficulties (respiratory depression)', 'moderate',
        'Start gabapentinoid at lowest dose; monitor for respiratory depression and sedation.',
        'FDA Drug Safety Communication 2019-12-19: gabapentinoids with CNS depressants (opioids)'),
    'warfarin_nsaid': ({'names': r'warfarin', 'classes': ()}, {'names': _NSAID, 'classes': ('nsaid',)},
        'Bleeding (GI haemorrhage), additive antithrombotic effect', 'major',
        'Avoid; prefer acetaminophen; if needed add gastroprotection and monitor INR/bleeding.',
        'ONC high-priority DDI list (Phansalkar et al., JAMIA 2012); warfarin prescribing information section 7'),
    'warfarin_tmp_smx': ({'names': r'warfarin', 'classes': ()}, {'names': r'sulfamethoxazole|trimethoprim', 'classes': ()},
        'Raised INR and bleeding (CYP2C9 inhibition)', 'major', 'Choose another antibiotic or reduce warfarin dose and check INR within 3-5 days.',
        'Warfarin prescribing information section 7 (CYP2C9 inhibitors)'),
    'doac_antiplatelet_nsaid': ({'names': r'apixaban|rivaroxaban|dabigatran|edoxaban', 'classes': ()},
        {'names': _NSAID + r'|clopidogrel|prasugrel|ticagrelor|aspirin', 'classes': ('nsaid', 'antiplatelet')},
        'Increased bleeding risk', 'moderate', 'Confirm indication for dual therapy; minimise duration; gastroprotection.',
        'Apixaban (Eliquis) and rivaroxaban (Xarelto) prescribing information section 7'),
    'clopidogrel_omeprazole': ({'names': r'clopidogrel', 'classes': ()}, {'names': r'omeprazole|esomeprazole', 'classes': ()},
        'Reduced clopidogrel activation (CYP2C19 inhibition), less antiplatelet effect', 'moderate',
        'Avoid omeprazole/esomeprazole; use pantoprazole or an H2 blocker if acid suppression is needed.',
        'FDA Drug Safety Communication 2009-11-17 / 2010-10-27; clopidogrel prescribing information section 7'),
    'statin_cyp3a4': ({'names': r'simvastatin|lovastatin', 'classes': ()}, {'names': _CYP3A4_STRONG, 'classes': ()},
        'Myopathy and rhabdomyolysis', 'contraindicated', 'Hold simvastatin/lovastatin during the course or switch statin.',
        'FDA Drug Safety Communication 2012-02-28 (statin labels: contraindicated with strong CYP3A4 inhibitors)'),
    'simvastatin_amiodarone': ({'names': r'simvastatin', 'classes': ()}, {'names': r'amiodarone', 'classes': ()},
        'Myopathy and rhabdomyolysis', 'major', 'Do not exceed simvastatin 20 mg daily with amiodarone (dose not received here).',
        'FDA Drug Safety Communication 2011-12-15; simvastatin prescribing information section 2'),
    'pde5_nitrate': ({'names': r'sildenafil|tadalafil|vardenafil|avanafil', 'classes': ()}, {'names': r'nitroglycerin|isosorbide', 'classes': ('nitrate',)},
        'Severe hypotension', 'contraindicated', 'Contraindicated; separate by >=24 h (sildenafil/vardenafil) or 48 h (tadalafil).',
        'ONC high-priority DDI list (Phansalkar et al., JAMIA 2012); sildenafil prescribing information section 4'),
    'maoi_serotonergic': ({'names': r'phenelzine|tranylcypromine|isocarboxazid|selegiline|rasagiline', 'classes': ('maoi',)},
        {'names': _SEROTONERGIC, 'classes': ('ssri', 'snri', 'sari_antidepressant')},
        'Serotonin syndrome / hypertensive crisis', 'contraindicated', 'Contraindicated; 14-day washout (5 weeks after fluoxetine).',
        'ONC high-priority DDI list (Phansalkar et al., JAMIA 2012); SSRI prescribing information section 4'),
    'serotonergic_tramadol': ({'names': _SSRI + r'|duloxetine|venlafaxine|trazodone', 'classes': ('ssri', 'snri', 'sari_antidepressant')},
        {'names': r'tramadol|meperidine|fentanyl', 'classes': ()},
        'Serotonin syndrome; tramadol also lowers seizure threshold', 'moderate', 'Monitor for serotonin toxicity; consider a non-serotonergic analgesic.',
        'Tramadol prescribing information section 5 (serotonin syndrome); FDA Drug Safety Communication 2016-03-22 (opioids)'),
    'ssri_antithrombotic': ({'names': _SSRI + r'|duloxetine|venlafaxine', 'classes': ('ssri', 'snri')},
        {'names': _NSAID + r'|warfarin|apixaban|rivaroxaban', 'classes': ('nsaid', 'anticoagulant')},
        'Increased bleeding risk (platelet serotonin depletion)', 'moderate', 'Consider gastroprotection; monitor for bleeding.',
        'SSRI/SNRI prescribing information section 5 (abnormal bleeding)'),
    'raas_potassium_sparing': ({'names': r'pril\b|sartan\b|sacubitril', 'classes': _RAAS_CLASSES}, {'names': _K_SPARING, 'classes': ('mra',)},
        'Hyperkalaemia', 'moderate', 'Check potassium and creatinine within 1 week of start and periodically.',
        'Spironolactone prescribing information section 7 (ACE inhibitors / ARBs)'),
    'raas_nsaid': ({'names': r'pril\b|sartan\b|sacubitril', 'classes': _RAAS_CLASSES}, {'names': _NSAID, 'classes': ('nsaid',)},
        'Acute kidney injury and reduced antihypertensive effect (worse with a diuretic)', 'moderate',
        'Avoid chronic NSAID use; monitor renal function and blood pressure.', 'ACE inhibitor / ARB prescribing information section 7 (NSAIDs)'),
    'tmp_smx_spironolactone': ({'names': r'sulfamethoxazole|trimethoprim', 'classes': ()}, {'names': r'spironolactone', 'classes': ()},
        'Hyperkalaemia and sudden death in older adults', 'major', 'Prefer another antibiotic; if used, check potassium within days.',
        'Antoniou et al., BMJ 2011;343:d5228 (trimethoprim-sulfamethoxazole with spironolactone)'),
    'methotrexate_trimethoprim': ({'names': r'methotrexate', 'classes': ()}, {'names': r'trimethoprim', 'classes': ()},
        'Bone-marrow suppression (additive antifolate, reduced clearance)', 'major', 'Avoid combination; monitor blood counts if unavoidable.',
        'Methotrexate prescribing information section 7'),
    'tizanidine_cyp1a2': ({'names': r'tizanidine', 'classes': ()}, {'names': r'ciprofloxacin|fluvoxamine', 'classes': ()},
        'Severe hypotension and sedation (CYP1A2 inhibition)', 'contraindicated', 'Contraindicated.',
        'ONC high-priority DDI list (Phansalkar et al., JAMIA 2012); tizanidine prescribing information section 4'),
    'citalopram_qt': ({'names': r'citalopram|escitalopram', 'classes': ()}, {'names': _QT, 'classes': ()},
        'QT prolongation and torsades de pointes', 'moderate', 'ECG and electrolytes if combined; prefer a non-QT alternative.',
        'FDA Drug Safety Communication 2011-08-24 (citalopram); FDA Drug Safety Communication 2013-03-12 (azithromycin)'),
}

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'DRUG-DRUG INTERACTION SCREEN of concurrent fills (overlapping supply windows) against a cited interaction table '
                  '(opioid+benzodiazepine, warfarin+NSAID, clopidogrel+omeprazole, statin+CYP3A4, PDE5+nitrate, ACEi/ARB+spironolactone, '
                  'QT, serotonin...); patients, pairs, overlap days, severity, management; class-level matches confirmed by System One.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'rules': [], 'severity': 'contraindicated|major|moderate', 'months': 12, 'current_only': False,
                           'min_overlap_days': 1, 'confirm_top': 10, 'top': 200},
            'meaning': 'Concurrent pairs = fills of one patient whose supply windows (fill_date..supply_end) overlap >= min_overlap_days in '
                       'the last `months` months (current_only: overlapping the as-of date), screened against the curated cited rule '
                       'table; severity filter = that level or worse; rows per patient x rule x drug pair with overlap days and fill '
                       'record ids; class-level matches are confirmed by System One (live only).'}
NATIVE = ('count',)


def schema(obj, string, array):
    return obj({'rules': array(string(tuple(RULES)), 'Interaction rules to screen; empty = all'),
                'severity': string(SEVERITIES),
                'months': {'type': 'integer', 'minimum': 1, 'maximum': 60, 'description': 'Look-back window in months (default 12)'},
                'current_only': {'type': 'boolean', 'description': 'Only pairs whose overlap covers the as-of date'},
                'min_overlap_days': {'type': 'integer', 'minimum': 1, 'maximum': 365},
                'confirm_top': {'type': 'integer', 'minimum': 0, 'maximum': 50, 'description': 'Class-level matches sent to System One'},
                'top': {'type': 'integer', 'minimum': 1, 'maximum': 5000}})


def validate(p):
    allowed = {'rules', 'severity', 'months', 'current_only', 'min_overlap_days', 'confirm_top', 'top'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('ddi_screening parameters: ' + ', '.join(sorted(allowed)))
    out = {'rules': sorted(set(p.get('rules') or [])), 'severity': p.get('severity', 'moderate'), 'months': p.get('months', 12),
           'current_only': p.get('current_only', False), 'min_overlap_days': p.get('min_overlap_days', 1),
           'confirm_top': p.get('confirm_top', 10), 'top': p.get('top', 200)}
    if any(r not in RULES for r in out['rules']): raise ValueError(f'rules: subset of {tuple(RULES)}')
    if out['severity'] not in SEVERITIES: raise ValueError(f'severity: {SEVERITIES}')
    for k, lo, hi in (('months', 1, 60), ('min_overlap_days', 1, 365), ('confirm_top', 0, 50), ('top', 1, 5000)):
        if type(out[k]) is not int or not lo <= out[k] <= hi: raise ValueError(f'{k}: integer {lo}..{hi}')
    if type(out['current_only']) is not bool: raise ValueError('current_only: true|false')
    return out


def side_match(side, name, cls):
    """'ingredient' | 'class' | None for one fill against one rule side."""
    if side.get('names') and re.search(side['names'], name or ''): return 'ingredient'
    if cls and cls in side.get('classes', ()): return 'class'
    return None


def _overlap(a, b):
    s, e = max(a['start'], b['start']), min(a['end'], b['end'])
    return (s, e) if s <= e else None


def _days(s, e):
    from datetime import date
    return (date.fromisoformat(e) - date.fromisoformat(s)).days + 1


def _union_days(spans):
    total, cur = 0, None
    for s, e in sorted(spans):
        if cur and s <= cur[1]:
            cur = (cur[0], max(cur[1], e))
        else:
            if cur: total += _days(*cur)
            cur = (s, e)
    return total + (_days(*cur) if cur else 0)


def screen(fills, rules, p, start, end, as_of):
    """fills: [{patient_id, fill_id, start, end, drug, cls, record_ids}] -> rows (one per patient x rule x drug pair)."""
    rank = {s: i for i, s in enumerate(SEVERITIES)}
    active = {k: v for k, v in rules.items() if rank[v[3]] <= rank[p['severity']]}
    by_patient = defaultdict(list)
    for f in fills:
        if f['end'] < start or f['start'] > end: continue
        hits = {}
        for rid, (a, b, *_rest) in active.items():
            ma, mb = side_match(a, f['drug'], f['cls']), side_match(b, f['drug'], f['cls'])
            if ma or mb: hits[rid] = (ma, mb)
        if hits: by_patient[f['patient_id']].append((f, hits))
    groups = defaultdict(lambda: {'spans': [], 'records': set(), 'confidence': 'ingredient'})
    for pid, items in by_patient.items():
        for i in range(len(items)):
            fa, ha = items[i]
            for j in range(i + 1, len(items)):
                fb, hb = items[j]
                if fa['drug'] == fb['drug']: continue
                ov = _overlap(fa, fb)
                if not ov: continue
                s, e = max(ov[0], start), min(ov[1], end)
                if s > e or _days(s, e) < p['min_overlap_days']: continue
                if p['current_only'] and not (ov[0] <= as_of <= ov[1]): continue
                for rid in set(ha) & set(hb):
                    for x, y in ((fa, fb), (fb, fa)):  # x on side A, y on side B
                        mx, my = (ha if x is fa else hb)[rid][0], (hb if y is fb else ha)[rid][1]
                        if mx and my:
                            g = groups[(pid, rid, x['drug'], y['drug'])]
                            g['spans'].append((s, e)); g['records'] |= set(x['record_ids']) | set(y['record_ids'])
                            if 'class' in (mx, my): g['confidence'] = 'class'
                            break
    rows = []
    for (pid, rid, da, db_), g in groups.items():
        _a, _b, effect, sev, manage, prov = rules[rid]
        rows.append({'patient_id': pid, 'rule': rid, 'severity': sev, 'drug_a': da, 'drug_b': db_,
                     'first_overlap': min(s for s, _ in g['spans']), 'last_overlap': max(e for _, e in g['spans']),
                     'overlap_days': _union_days(g['spans']), 'effect': effect, 'management': manage, 'provenance': prov,
                     'match_confidence': g['confidence'], 'record_ids': sorted(g['records'])[:12],
                     'confirmation': None, 'decided_by': None})
    rows.sort(key=lambda r: (rank[r['severity']], -r['overlap_days'], r['patient_id'], r['rule']))
    return rows


def load_fills(db, keep, start, end, *, include_unknown=False):
    out, unknown = [], []
    for fid, pid, fd, se, name, cls, rec in db.execute(
            'SELECT fill_id, patient_id, fill_date, supply_end, drug_name, drug_class, record_ids FROM fill '
            'WHERE fill_date <= ? AND (supply_end IS NULL OR supply_end >= ?)', (end, start)):
        if keep is not None and pid not in keep: continue
        row = {'patient_id': pid, 'fill_id': fid, 'start': fd[:10], 'end': se[:10] if se else None,
               'drug': (name or '').lower(), 'cls': cls, 'record_ids': [r for r in (rec or '').split(',') if r]}
        (out if se else unknown).append(row)
    return (out, unknown) if include_unknown else out


def run(db, patient_ids, p, as_of):
    start, end = rc.window(as_of, p['months'])
    rules = {k: v for k, v in RULES.items() if not p['rules'] or k in p['rules']}
    keep = set(patient_ids) if patient_ids is not None else None
    fills, unknown = load_fills(db, keep, start, end, include_unknown=True)
    unknown = [f for f in unknown if any(
        SEVERITIES.index(v[3]) <= SEVERITIES.index(p['severity']) and
        (side_match(v[0], f['drug'], f['cls']) or side_match(v[1], f['drug'], f['cls'])) for v in rules.values())]
    rows = screen(fills, rules, p, start, end, as_of)
    by_rule = defaultdict(lambda: {'patients': set(), 'pairs': 0})
    for r in rows: by_rule[r['rule']]['patients'].add(r['patient_id']); by_rule[r['rule']]['pairs'] += 1
    summary = [{'rule': k, 'severity': RULES[k][3], 'patients': len(v['patients']), 'pairs': v['pairs'], 'provenance': RULES[k][5]}
               for k, v in sorted(by_rule.items(), key=lambda kv: (SEVERITIES.index(RULES[kv[0]][3]), -len(kv[1]['patients'])))]
    screened = len({f['patient_id'] for f in fills + unknown})
    assumptions = [
        'Synthetic warehouse; concurrency = overlapping dispensed supply windows (fill_date..supply_end); actual ingestion, dose, route, '
        'renal/hepatic function and stop dates other than supply exhaustion are not received.',
        f"Curated interaction table of {len(RULES)} cited rules (FDA labels / Drug Safety Communications; ONC high-priority list where "
        'noted); it is not an exhaustive interaction compendium - a pair absent from the table is not shown as safe.',
        f"Window {start} to {end}" + (' (pairs overlapping the as-of date only)' if p['current_only'] else '') +
        f"; minimum overlap {p['min_overlap_days']} day(s); severity {p['severity']} or worse.",
        'Class-level matches (drug matched only by drug class) are lower confidence; System One confirms them when live.',
        'Flags are medication-review prompts for a clinician or pharmacist, not orders.']
    if unknown:
        assumptions.append(f"{len(unknown)} relevant fill(s) have unknown supply windows; concurrent exposure for them remains unassessed.")
    return {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'period': {'start': start, 'end': end},
            'status': 'partial' if unknown else 'complete', 'unassessed_fills': unknown,
            'missing': ['Reported supply duration or dated medication reconciliation for fills with unknown supply windows.'] if unknown else [],
            'patients_with_fills': screened, 'rows': rows[:p['top']], 'total_pairs': len(rows),
            'flagged_patients': sorted({r['patient_id'] for r in rows}), 'by_rule': summary, 'assumptions': assumptions,
            'system_one': {'decisions': 0, 'new_paid_api_calls': 0, 'estimated_usd': 0.0, 'status': 'not_run'},
            'new_paid_api_calls': 0,
            'method': ('Fills per patient with overlapping supply windows screened pairwise against a curated, cited DDI rule table '
                       '(ingredient-name patterns and drug classes per side); overlap days = union of overlapping intervals inside the '
                       'window; class-level matches confirmed by System One (local student -> Jev -> Perplexity) when live.')}


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    with rc.connect(path) as db:
        return run(db, patient_ids, p, str(as_of)[:10])


def confirm_question(row):
    return (f"Concurrent use of {row['drug_a']} and {row['drug_b']}: does the interaction '{row['effect']}' "
            f"({row['rule'].replace('_', ' ')}) apply to these two drugs?")


def review(stack, report, *, decide=None):
    """System One confirmation of class-level matches (live only; cache hits free). Never raises."""
    p = report['parameters']
    todo = [r for r in report['rows'] if r['match_confidence'] == 'class'][:p['confirm_top']]
    live = bool(getattr(stack, 'allow_live', False))
    if not todo or (not live and decide is None):
        report['system_one'] = {'decisions': 0, 'new_paid_api_calls': 0, 'estimated_usd': 0.0,
                                'status': 'no class-level matches' if not todo else 'skipped (not live)'}
        return report
    from .atomic_questions import ask, _spent
    from . import system_one_deciders
    order = system_one_deciders.order_for(TASK_KEY)
    calls, usd, by, errors = 0, 0.0, defaultdict(int), 0
    for r in todo:
        _a, _b, effect, sev, manage, prov = RULES[r['rule']]
        ev = [{'id': 'rule', 'date': report['as_of'], 'text': f"Interaction rule ({prov}): {effect}; severity {sev}; {manage}"},
              {'id': 'fills', 'date': r['last_overlap'] if r['last_overlap'] <= report['as_of'] else report['as_of'],
               'text': f"Patient {r['patient_id']} filled {r['drug_a']} and {r['drug_b']} with supply overlapping {r['overlap_days']} days "
                       f"({r['first_overlap']} to {r['last_overlap']}); records {', '.join(r['record_ids'][:6])}"}]
        d = ask(confirm_question(r), CONFIRM_OPTIONS, ev, as_of=report['as_of'], task_key=TASK_KEY, providers=list(order),
                allow_live=live, decide=decide)
        k, u = _spent(d); calls += k; usd += u
        errors += bool(d.get('error')); by[d.get('decided_by') or 'none'] += 1
        r['confirmation'], r['decided_by'] = d.get('label'), d.get('decided_by')
    report['system_one'] = {'decisions': len(todo), 'new_paid_api_calls': calls, 'estimated_usd': round(usd, 6), 'errors': errors,
                            'decided_by': dict(by), 'order': list(order), 'status': 'reviewed'}
    report['new_paid_api_calls'] = calls
    if errors: report['do_not_cache'] = True
    report['assumptions'] = report['assumptions'] + [
        f"System One reviewed {len(todo)} class-level match(es): new paid calls {calls}, ~${usd:.4f}; decided by {dict(by)}."]
    return report


def headline(r):
    n = len(r['flagged_patients'])
    if not r['total_pairs'] and r.get('unassessed_fills'):
        return (f"No interacting pairs established from known supply windows; concurrency remains unassessed for "
                f"{len(r['unassessed_fills'])} relevant fill(s) with unknown duration")
    if not r['total_pairs']:
        return (f"No interacting concurrent fills found among {r['patients_with_fills']} patients with fills "
                f"({r['period']['start']} to {r['period']['end']})")
    top = '; '.join(f"{x['rule'].replace('_', ' ')} ({x['severity']}) {x['patients']} patients" for x in r['by_rule'][:5])
    return (f"{n} patients with {r['total_pairs']} interacting concurrent drug pairs among {r['patients_with_fills']} patients with fills "
            f"({r['period']['start']} to {r['period']['end']}): {top}")


def answer(report, plan, question):
    rejected = {(x['patient_id'], x['rule'], x['drug_a'], x['drug_b']) for x in report['rows'] if x.get('confirmation') == 'no'}
    ids = [pid for pid in report['flagged_patients']
           if any((r['patient_id'] == pid and (r['patient_id'], r['rule'], r['drug_a'], r['drug_b']) not in rejected) for r in report['rows'])
           or pid not in {r['patient_id'] for r in report['rows']}]
    sources = [{'patient_id': r['patient_id'], 'record_id': x, 'text': f"{r['drug_a']} + {r['drug_b']}"}
               for r in report['rows'][:20] for x in r['record_ids'][:2]]
    result = rc.answer(report, plan, value=headline(report) + '.', table=report['rows'], patient_ids=ids, membership_role='selected',
                     number=len(ids), sources=sources,
                     definitions=[{'term': 'concurrent fills', 'basis': 'overlapping dispensed supply windows (fill_date..supply_end)'},
                                  {'term': 'interaction rule', 'basis': 'curated cited table in ddi_screening.RULES'}])
    result.update(missing=list(report.get('missing') or []), unassessed_fills=report.get('unassessed_fills', []))
    return result


# ---- free local-compiler route ----------------------------------------------------------------------------------------
DDI_RX = (r'\b(?:drug[- ]drug|drug|medication|med)[- ]interactions?\b|\bddis?\b|\binteracting (?:drugs|medications|meds|prescriptions)\b|'
          r'\b(?:dangerous|contraindicated|unsafe) (?:drug |medication )?combinations?\b')
RULE_WORDS = [('opioid_benzodiazepine', r'\bopioids?\b.*\bbenzo\w*|\bbenzo\w*.*\bopioids?\b'),
              ('opioid_gabapentinoid', r'\bopioids?\b.*\b(?:gabapentin\w*|pregabalin)|\b(?:gabapentin\w*|pregabalin)\b.*\bopioids?\b'),
              ('warfarin_nsaid', r'\bwarfarin\b.*\bnsaids?\b|\bnsaids?\b.*\bwarfarin\b'),
              ('clopidogrel_omeprazole', r'\bclopidogrel\b.*\b(?:omeprazole|ppis?)\b|\b(?:omeprazole|ppis?)\b.*\bclopidogrel\b'),
              ('statin_cyp3a4', r'\b(?:simvastatin|lovastatin)\b.*\b(?:clarithromycin|erythromycin|itraconazole|ketoconazole|cyp3a4)\b|'
                                r'\b(?:clarithromycin|erythromycin|itraconazole|ketoconazole)\b.*\b(?:simvastatin|lovastatin)\b'),
              ('pde5_nitrate', r'\bnitrates?\b.*\b(?:sildenafil|pde-?5)|\b(?:sildenafil|pde-?5)\w*\b.*\bnitrates?\b'),
              ('raas_potassium_sparing', r'\b(?:ace[- ]inhibitors?|acei|arbs?|lisinopril|losartan)\b.*\b(?:spironolactone|potassium[- ]sparing)\b|'
                                         r'\b(?:spironolactone|potassium[- ]sparing)\b.*\b(?:ace[- ]inhibitors?|acei|arbs?|lisinopril|losartan)\b')]
# one-sided words only narrow an explicit interaction question
NARROW = [('citalopram_qt', r'\bqt\b'), ('maoi_serotonergic', r'\bmaois?\b'), ('serotonergic_tramadol', r'\bserotonin syndrome\b|\btramadol\b'),
          ('raas_potassium_sparing', r'\bhyperkal\w+')]


def route(question, profile, as_of):
    from .engine_suite import plan
    q = rc.low(question)
    pair = [k for k, rx in RULE_WORDS if re.search(rx, q)]
    explicit = re.search(DDI_RX, q)
    if explicit: pair += [k for k, rx in NARROW if re.search(rx, q) and k not in pair]
    combo = re.search(r'\b(?:both|together|with|and|plus|combined|concurrent\w*|same time|overlap\w*|co-?prescrib\w*)\b', q)
    if not explicit and not (pair and combo and re.search(r'\b(?:patients?|members?|who|how many|which|list)\b', q)):
        return None
    if rc.knowledge_only(q): return None
    params = {}
    if pair: params['rules'] = pair
    if re.search(r'\bcontraindicated\b', q): params['severity'] = 'contraindicated'
    elif re.search(r'\b(?:major|serious|severe|dangerous|high[- ]risk)\b', q): params['severity'] = 'major'
    if re.search(r'\b(?:currently|right now|active|today|current)\b', q): params['current_only'] = True
    m = re.search(r'\b(?:last|past)\s+(\d{1,2})\s+months?\b', q)
    if m: params['months'] = max(1, min(60, int(m.group(1))))
    elif re.search(r'\b(?:last|past) (?:year|12 months)\b', q): params['months'] = 12
    return plan(OP, params, 'Screen concurrent fills for cited drug-drug interactions', question, [f"rules {pair or 'all'}"])


TEMPLATES = ['Which patients have dangerous drug-drug interactions in their current fills?',
             'How many members are co-prescribed opioids and benzodiazepines?',
             'Screen our population for drug interactions in the last 6 months',
             'List patients on warfarin with an NSAID at the same time',
             'Any contraindicated medication combinations among our patients?',
             'Who is taking clopidogrel together with omeprazole?',
             'Find patients on an ACE inhibitor plus spironolactone at risk of hyperkalemia',
             'Run a DDI screen on concurrent prescriptions']
