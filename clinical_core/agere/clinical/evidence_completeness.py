"""Evidence-set completeness and conclusion-guided retrieval: fetch the combination of sources a conclusion needs, not more of the same.

Relevance ranking returns the passages most similar to the question. For a question like "is the patient on metformin?" that
can be five notes that all say "continue metformin" and nothing about whether it was ever dispensed or later stopped. A
conclusion is only as good as the *set* behind it (LongHarness arXiv 2610.13075, MSS-Complement arXiv 2609.20050; Agere research
review 2026-10-20, item 1). This module makes the set explicit:

  CONCLUSIONS   per type of conclusion, the components its evidence needs, each defined by the received source streams that can
                carry it and, where the stream is shared, a text cue:
                  current_treatment   prescribing, dispensing, administration, discontinuation, current status
                  provider_cost       claim source, provider linkage, observation period, version handling, denominator
                  lab_result          result, order, follow-up
                  social_need         screen, vendor attribute, referral
                  condition_status    problem list / assessment, encounter, claim diagnosis
  assess(question, rows)    which conclusion types the question asks for (deterministic vocabulary) and, per component, the rows
                            that carry it. The definitions are fixed here, independent of any model: the retriever never
                            defines completeness for itself with a model that then certifies its own answer.
  complete(search, question, rows, limit)   for every missing component run one targeted search (the question's subject terms
                            plus the component's cue words, bounded), keep only hits that really carry the component, and
                            re-select the set: completers in, redundant rows out, protected rows and the sole carrier of any
                            component never dropped. Missing components that stay missing are reported so the answer can
                            request them or return a supported partial answer; nothing is guessed.
Reported as result['evidence_set'] by warehouse_retrieval.Retriever.search for patient-scoped questions (default on). No model
calls; the extra searches are the retriever's own bounded local searches; synthetic data only.
"""
import re

VERSION = 'evidence-set-completeness/1'
EXTRA_SEARCHES = 5          # component searches per question: the largest conclusion type has five components
EXTRA_PER_COMPONENT = 1     # rows a component search may add

_MED = (r'\b(?:medication|medicine|drug|prescri\w*|statin|metformin|insulin|lisinopril|losartan|amlodipine|atorvastatin|rosuvastatin|metoprolol|carvedilol|'
        r'spironolactone|furosemide|empagliflozin|dapagliflozin|sglt2|glp-?1|semaglutide|beta[- ]?blocker|ace inhibitor|arb|diuretic|apixaban|warfarin|aspirin|'
        r'anticoagulant|inhaler|antihypertensive)\w*')
_LAB = r'\b(?:a1c|hba1c|egfr|ldl|creatinine|potassium|bnp|uacr|lab\w*|glucose|sodium|hemoglobin|cholesterol|test result)\b'
_COND = r'\b(?:diabet\w*|hypertension|heart failure|chf|ckd|kidney disease|copd|asthma|depression|anemia|obesity|coronary|afib|atrial fibrillation|cancer|diagnos\w*|condition)\b'

COMPONENTS = {
    'current_treatment': {
        'when': lambda low: bool(re.search(_MED, low)) and bool(re.search(r'\b(?:taking|takes|on|currently|current|still|adher\w*|using|use|stopp\w*|discontinu\w*|held|fill\w*|refill\w*|dispens\w*|picked up|why)\b', low)),
        'components': {
            'prescribing': {'label': 'a prescription or order', 'streams': ('medication_orders', 'received_prescription_requests', 'prescription_reassessment_decisions', 'original_regimen_decisions'),
                            'cue': r'\b(?:prescribed|prescription|ordered|order|started|initiated)\b', 'query': 'prescribed ordered prescription'},
            'dispensing': {'label': 'a pharmacy dispense or fill', 'streams': ('pharmacy_dispenses', 'payer_pharmacy_claims', 'pharmacy_claims', 'pharmacy_claim_transactions', 'pharmacy_history', 'received_pharmacy_information'),
                           'cue': r'\b(?:dispensed|filled|fill|picked up|pharmacy claim|days supply)\b', 'query': 'dispensed filled pharmacy fill'},
            'administration': {'label': 'an administration record', 'streams': ('medication_administrations',), 'cue': r'\b(?:administered|given|infusion|injection)\b', 'query': 'administered given dose'},
            'discontinuation': {'label': 'a stop, hold or change record', 'streams': ('medication_intent_changes', 'medication_review_decisions'),
                                'cue': r'\b(?:stopped|stop|discontinued|held|hold|no longer|tapered|declined|intoleran\w*|switched)\b', 'query': 'stopped discontinued held declined intolerance'},
            'current_status': {'label': 'a statement of current use', 'streams': ('medication_lists', 'reported_medication_entries', 'medication_reconciliation', 'medication_followup_reports'),
                               'cue': r'\b(?:currently taking|reports taking|taking|not taking|medication list|reconcil\w*)\b', 'query': 'currently taking medication list reconciliation'}}},
    'provider_cost': {
        'when': lambda low: bool(re.search(r'\b(?:cost|costs|spend|spent|pay|paid|allowed|charge[sd]?|billed|pmpm|payment)\b', low)) and bool(re.search(r'\b(?:provider|physician|doctor|practice|clinic|specialist|hospital|facility)s?\b', low)),
        'components': {
            'claim_source': {'label': 'claims carrying the amounts', 'streams': ('payer_medical_claims', 'medical_claims', 'payer_medical_claim_lines', 'medical_claim_lines'), 'cue': None, 'query': 'claim allowed paid amount'},
            'provider_linkage': {'label': 'the provider on the claim', 'streams': ('provider_remittance_advice',), 'cue': r'\b(?:rendering|billing|npi|provider)\b', 'query': 'rendering billing provider npi'},
            'observation_period': {'label': 'the coverage period observed', 'streams': ('enrollment_spans', 'coverage_verifications'), 'cue': None, 'query': 'enrollment coverage period'},
            'version_handling': {'label': 'claim versions, corrections or reversals', 'streams': ('claim_corrections', 'claim_acknowledgements', 'medical_claim_submissions'), 'cue': r'\b(?:corrected|replacement|void|reversal|adjust\w*)\b', 'query': 'claim correction replacement void adjustment'},
            'denominator': {'label': 'member months in the denominator', 'streams': ('enrollment_spans',), 'cue': None, 'query': 'enrollment member months'}}},
    'lab_result': {
        'when': lambda low: bool(re.search(_LAB, low)),
        'components': {
            'result': {'label': 'the result itself', 'streams': ('labs', 'vitals'), 'cue': None, 'query': 'result value'},
            'order': {'label': 'the order or collection', 'streams': ('laboratory_orders', 'laboratory_specimens'), 'cue': r'\b(?:ordered|collected|specimen)\b', 'query': 'ordered collected specimen'},
            'follow_up': {'label': 'acknowledgement or follow-up', 'streams': ('result_acknowledgements', 'laboratory_followup_contacts', 'laboratory_result_notifications'), 'cue': r'\b(?:acknowledg\w*|notified|follow[- ]?up|contacted)\b', 'query': 'result acknowledged follow-up notified'}}},
    'social_need': {
        'when': lambda low: bool(re.search(r'\b(?:transport\w*|housing|food|afford\w*|financial|social need\w*|sdoh|caregiver|lonel\w*|barrier\w*)\b', low)),
        'components': {
            'screen': {'label': 'a social-needs screen', 'streams': ('social_screens',), 'cue': None, 'query': 'screen positive social needs'},
            'vendor_attribute': {'label': 'a vendor attribute', 'streams': ('sdoh_vendor_attributes',), 'cue': None, 'query': 'vendor neighbourhood risk'},
            'referral': {'label': 'a referral or its outcome', 'streams': ('referrals', 'referral_status_updates'), 'cue': r'\b(?:referr\w*|connected|scheduled)\b', 'query': 'referral outcome connected'}}},
    'condition_status': {
        'when': lambda low: bool(re.search(_COND, low)) and bool(re.search(r'\b(?:has|have|history|diagnos\w*|status|active|resolved|condition|problem)\b', low)) and not re.search(_MED, low),
        'components': {
            'problem_list': {'label': 'a problem-list entry or assessment', 'streams': ('conditions', 'condition_assessments'), 'cue': None, 'query': 'problem list assessment diagnosis'},
            'encounter': {'label': 'an encounter that addressed it', 'streams': ('encounters', 'encounter_text_fragments'), 'cue': None, 'query': 'encounter visit assessment'},
            'claim_diagnosis': {'label': 'a diagnosis on a claim', 'streams': ('payer_medical_claims', 'medical_claims'), 'cue': None, 'query': 'claim diagnosis'}}},
}


def _stream(row):
    return (row.get('source') or {}).get('source_table')


def carries(row, spec):
    """True when one retrieved row carries a component: its stream is one of the component's, or (for notes and other shared streams) the text has the cue."""
    if _stream(row) in spec['streams']: return True
    return bool(spec['cue']) and _stream(row) in ('notes', 'telephone_messages', 'imported_documents', 'encounters', 'medication_followup_reports') and bool(re.search(spec['cue'], row.get('text') or '', re.I))


def types_for(question):
    low = (question or '').lower()
    return [t for t, c in COMPONENTS.items() if c['when'](low)]


def assess(question, rows):
    """-> {'types': [...], 'components': {type: {component: {'label', 'rows': [row ids], 'status': present|missing}}}, 'complete', 'missing': [(type, component)]}"""
    out = {'schema': VERSION, 'types': types_for(question), 'components': {}, 'missing': []}
    for t in out['types']:
        comp = {}
        for name, spec in COMPONENTS[t]['components'].items():
            have = [r['id'] for r in rows if carries(r, spec)]
            comp[name] = {'label': spec['label'], 'rows': have, 'status': 'present' if have else 'missing'}
            if not have: out['missing'].append((t, name))
        out['components'][t] = comp
    out['complete'] = not out['missing']
    return out


def complete(search, question, rows, limit, *, protected_ids=()):
    """Targeted retrieval for the missing components, then a re-selection inside `limit`.
    search(query) -> list of candidate rows (the retriever's own bounded search; sub-searches must not recurse into completion).
    -> (rows, report). Rows added carry row['completes'] = 'type.component'."""
    first = assess(question, rows)
    report = {'schema': VERSION, 'types': first['types'], 'before': {t: {c: v['status'] for c, v in d.items()} for t, d in first['components'].items()},
              'extra_searches': 0, 'added': [], 'dropped': [], 'unfetched': []}
    if not first['types']:
        report.update(components={}, complete=True, missing=[], guidance='no conclusion type recognised: the ranked passages stand as they are')
        return rows, report
    from . import protected_channels as PC
    subject = ' '.join(PC.subject_terms(question)[:6])
    have = {r['id'] for r in rows}
    out = list(rows)
    for t, name in first['missing']:
        if report['extra_searches'] >= EXTRA_SEARCHES: report['unfetched'].append(f'{t}.{name}'); continue
        spec = COMPONENTS[t]['components'][name]
        report['extra_searches'] += 1
        try:
            found = search(f"{subject} {spec['query']}".strip())
        except (ValueError, KeyError, OSError):
            found = []
        picked = [r for r in found if r['id'] not in have and carries(r, spec)][:EXTRA_PER_COMPONENT]
        for r in picked:
            r = {**r, 'completes': f'{t}.{name}'}
            out.append(r); have.add(r['id']); report['added'].append({'id': r['id'], 'component': f'{t}.{name}', 'stream': _stream(r)})
    # keep the set inside `limit`: drop the lowest-ranked rows that are neither protected nor the only carrier of a component
    def sole(r, rows_):
        mine = [(t, n) for t in first['types'] for n, s in COMPONENTS[t]['components'].items() if carries(r, s)]
        return any(sum(carries(x, COMPONENTS[t]['components'][n]) for x in rows_) == 1 for t, n in mine)
    while len(out) > limit:
        idx = next((i for i in range(len(out) - 1, -1, -1) if not out[i].get('protected') and not out[i].get('completes') and out[i]['id'] not in protected_ids and not sole(out[i], out)), None)
        if idx is None: break
        report['dropped'].append(out[idx]['id']); out.pop(idx)
    final = assess(question, out)
    report.update(components={t: {c: {'status': v['status'], 'rows': len(v['rows'])} for c, v in d.items()} for t, d in final['components'].items()},
                  complete=final['complete'], missing=[f'{t}.{c}' for t, c in final['missing']],
                  guidance=('every component of the conclusion has at least one carrying row' if final['complete'] else
                            'components still missing after a targeted search: ' + ', '.join(f'{t}.{c} ({COMPONENTS[t]["components"][c]["label"]})' for t, c in final['missing'])
                            + '. Request them or return a supported partial answer; absence in this set is not absence in the chart.'))
    return out, report
