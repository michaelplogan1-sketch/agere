"""Retrieve-then-rank ICD-10 / CMS-HCC suspecting, confirmed by System One ('hcc_suspects' analysis_graph operation).

Never free generation: every candidate code comes from a closed retrieval channel, is ranked in code, and only the top
candidates per patient are confirmed by a typed System One choice over their own evidence.
  retrieve   recapture      V28 HCCs coded in the prior collection year and not yet this year (risk_adjustment
                            open_recapture_gaps: codes, last supporting encounter, RAF delta)
             evidence_rules risk_adjustment.suspects_for (KDIGO eGFR, ADA HbA1c / diabetes drugs, LVEF, BMI, PHQ) and
                            problem-list entries with no eligible code ('documented_not_coded')
             notes          the patient's received text (stack.retriever: FTS5 + MiniLM + BGE) matched against a closed
                            phrase -> ICD-10 table (NOTE_TERMS); negated, family and hypothetical sentences are skipped
             Candidates whose HCC (or one above it in the V28 hierarchy) is already coded this year are dropped.
  rank       score = channel weight + evidence rows + RAF delta + note corroboration; one row per (patient, HCC).
  confirm    top `top` per patient: system_one.decide (local student, then the decider bake-off order for task
             'hcc_confirm') reads a bounded evidence list (structured rows + up to 4 anchored note sentences) and answers
             yes / no / unknown: does the evidence support this condition as current for the patient? Paid only live; memoized.
A suspect is a documentation review prompt with cited evidence, never a diagnosis.
"""
import re
import time
from pathlib import Path

VERSION = 'hcc-suspecting/1'
CHANNELS = ('recapture', 'evidence_rules', 'notes')
WEIGHTS = {'recapture': 1.0, 'evidence_rules': 0.8, 'documented_not_coded': 0.9, 'notes': 0.5}
HARD_MAX_PATIENTS = 200
CONFIRM_OPTIONS = {'yes': 'The evidence supports this condition as current for this patient',
                   'no': 'The evidence contradicts this condition for this patient (normal results, denied, resolved, a relative)',
                   'unknown': 'Insufficient, contradictory or unresolved evidence'}
TASK_KEY = 'hcc_confirm'
NOTE_TERMS = [  # closed phrase table: (pattern, illustrative ICD-10, plain label)
    (r'\bhfref\b|\bsystolic (?:heart )?failure\b|heart failure with reduced', 'I5022', 'heart failure with reduced ejection fraction'),
    (r'\bchf\b|\bcongestive heart failure\b|\bheart failure\b|\bhfpef\b', 'I509', 'heart failure'),
    (r'\batrial fibrillation\b|\bafib\b|\ba-fib\b', 'I4891', 'atrial fibrillation'),
    (r'\bcopd\b|\bemphysema\b|chronic obstructive', 'J449', 'chronic obstructive pulmonary disease'),
    (r'\bckd (?:stage )?(?:5|v)\b|\besrd\b|end[- ]stage renal', 'N185', 'chronic kidney disease stage 5'),
    (r'\bckd (?:stage )?(?:4|iv)\b', 'N184', 'chronic kidney disease stage 4'),
    (r'\bckd (?:stage )?3b\b', 'N1832', 'chronic kidney disease stage 3b'),
    (r'\bckd (?:stage )?(?:3a?|iii)\b', 'N1831', 'chronic kidney disease stage 3a'),
    (r'\btype 2 diabetes\b|\bt2dm\b|\bdm2\b|\bdiabetes mellitus\b', 'E119', 'type 2 diabetes mellitus'),
    (r'\bmorbid(?:ly)? obes\w*|\bbmi (?:4\d|5\d)\b', 'E6601', 'morbid obesity'),
    (r'\bmajor depress\w*|\bmdd\b', 'F331', 'major depressive disorder, recurrent, moderate'),
    (r'\bdementia\b|\balzheimer', 'F0390', 'dementia'),
    (r'\brheumatoid arthritis\b', 'M069', 'rheumatoid arthritis'),
    (r'\bcirrhosis\b', 'K7469', 'cirrhosis of liver'),
    (r'\bseizure disorder\b|\bepilepsy\b', 'G40909', 'epilepsy'),
    (r'\bschizophreni\w*', 'F209', 'schizophrenia'),
    (r'\bbipolar\b', 'F319', 'bipolar disorder'),
    (r'\balcohol use disorder\b|\balcohol dependence\b', 'F1020', 'alcohol dependence'),
]
_NOTE_RX = [(re.compile(p, re.I), icd, label) for p, icd, label in NOTE_TERMS]
NOTE_QUERY = ('heart failure HFrEF atrial fibrillation COPD chronic kidney disease CKD stage diabetes morbid obesity major depression '
              'dementia rheumatoid arthritis cirrhosis epilepsy schizophrenia bipolar alcohol dependence')
SKIP = re.compile(r"\b(?:denies|denied|no (?:history|hx|evidence) of|negative for|ruled out|rule out|r/o|family history|mother|father|"
                  r"brother|sister|risk of|screen(?:ing)? for|if |resolved)\b", re.I)


def confirm_question(condition):
    return f'For this patient only, does the submitted evidence support {condition} as a current condition?'


# -- parameters -------------------------------------------------------------------------------------------------------
def validate(p):
    allowed = {'year', 'top', 'confirm', 'maximum_patients', 'channels', 'question'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError(f'hcc_suspects parameters: {sorted(allowed)}')
    y = p.get('year')
    if y is not None and (type(y) is not int or not 2000 <= y <= 2100): raise ValueError('hcc_suspects year: a collection year or null')
    top = p.get('top', 3)
    if type(top) is not int or not 1 <= top <= 10: raise ValueError('hcc_suspects top: 1..10 candidates confirmed per patient')
    cap = p.get('maximum_patients', 10)
    if type(cap) is not int or not 1 <= cap <= HARD_MAX_PATIENTS: raise ValueError(f'hcc_suspects maximum_patients: 1..{HARD_MAX_PATIENTS}')
    ch = p.get('channels') or list(CHANNELS)
    if not isinstance(ch, list) or not ch or any(c not in CHANNELS for c in ch): raise ValueError(f'hcc_suspects channels: {CHANNELS}')
    q = p.get('question', '')
    if not isinstance(q, str) or len(q) > 1000: raise ValueError('hcc_suspects question: bounded text')
    return {'year': y, 'top': top, 'confirm': bool(p.get('confirm', True)), 'maximum_patients': cap, 'channels': ch, 'question': q}


# -- retrieve -----------------------------------------------------------------------------------------------------------
def semantic_path(stack):
    path = Path(stack.path)
    if (path/'run.json').exists():
        from . import semantic_layer
        return semantic_layer.ensure(path)
    return path/'semantic.sqlite3'


def structured(stack, ids, year):
    """risk_adjustment report restricted to these patients (coverage all; suspects + recapture)."""
    from . import risk_adjustment as ra
    params = {'coverage': 'all', 'focus': 'suspects', 'top': 500, **({'year': year} if year else {})}
    return ra.execute(semantic_path(stack), ids, params, stack.retriever.meta['snapshot']['as_of'])


def _row_text(e):
    v = e.get('value')
    return f"{e.get('name') or e.get('source') or 'record'} {v if v is not None else ''} on {e.get('date') or 'undated'} ({e.get('source') or 'record'})".strip()


def candidates_for(pat, model):
    """Structured candidates for one risk_adjustment patient row -> [{hcc, label, icd10, channel, score, evidence, ...}]."""
    out = []
    for g in pat.get('open_recapture_gaps') or []:
        last = g.get('last_supporting') or {}
        out.append({'hcc': g['hcc'], 'label': g.get('label'), 'icd10': (g.get('codes') or [None])[0], 'channel': 'recapture',
                    'score': WEIGHTS['recapture'] + min(1.0, max(0.0, g.get('raf_delta') or 0.0)), 'raf_delta': g.get('raf_delta'),
                    'evidence': [{'id': f"dx:{last.get('ref')}", 'text': f"Coded {last.get('icd10')} on a {last.get('kind')} dated {last.get('date')} "
                                  f"({last.get('reason')}); not yet coded this year.", 'date': last.get('date'), 'note_id': str(last.get('ref'))}]
                    if last else [], 'record_ids': [str(last.get('ref'))] if last else []})
    for s in pat.get('suspects') or []:
        if not s.get('hcc'): continue
        ch = 'documented_not_coded' if s['category'] == 'documented_not_coded' else 'evidence_rules'
        ev = [{'id': f"rec:{e.get('record_id')}", 'text': _row_text(e), 'date': e.get('date'), 'note_id': str(e.get('record_id'))}
              for e in s.get('evidence') or []]
        out.append({'hcc': s['hcc'], 'label': s.get('hcc_label') or s.get('label'), 'icd10': s.get('illustrative_icd10'),
                    'channel': ch, 'rule': s['category'], 'score': WEIGHTS[ch] + 0.05 * min(6, s.get('evidence_rows') or 0) +
                    min(1.0, max(0.0, s.get('raf_delta_if_documented') or 0.0)), 'raf_delta': s.get('raf_delta_if_documented'),
                    'evidence': ev, 'record_ids': [str(e.get('record_id')) for e in s.get('evidence') or []]})
    return out


def note_candidates(stack, pid, model, age, sex):
    """Closed-table matches in the patient's top-ranked received text -> candidates with their sentences."""
    from . import risk_adjustment as ra
    from .evidence_packets import sentences
    found = stack.retriever.search(NOTE_QUERY, patient=pid, limit=20)
    out = {}
    for row in found.get('evidence') or []:
        src = row.get('source') or {}
        for a, b, s in sentences(row.get('text') or ''):
            if SKIP.search(s): continue
            for rx, icd, label in _NOTE_RX:
                if not rx.search(s): continue
                hccs = ra._model_hccs(model, [icd], age, sex)
                if not hccs: continue
                c = out.setdefault(hccs[0], {'hcc': hccs[0], 'label': model['labels'].get(hccs[0]) or label, 'icd10': icd, 'channel': 'notes',
                                             'score': WEIGHTS['notes'], 'evidence': [], 'record_ids': []})
                if len(c['evidence']) < 4:
                    c['evidence'].append({'id': f"passage:{row.get('id')}:{a}", 'text': s[:700], 'date': (src.get('event_date') or '')[:10] or None,
                                          'note_id': str(src.get('record_id'))})
                    c['record_ids'].append(str(src.get('record_id'))); c['score'] += 0.1
                break  # the most specific phrase in the sentence wins
    return list(out.values())


def rank(cands, coded, model):
    """Merge by HCC (channels corroborate), drop already-coded HCCs (hierarchy-aware), sort by score."""
    from . import risk_adjustment as ra
    merged = {}
    for c in cands:
        if ra._same_or_higher(model, c['hcc'], coded): continue
        m = merged.get(c['hcc'])
        if m is None:
            merged[c['hcc']] = {**c, 'channels': [c['channel']]}
        else:
            m['score'] += 0.25 * c['score']; m['channels'] = list(dict.fromkeys(m['channels'] + [c['channel']]))
            m['evidence'] = (m['evidence'] + c['evidence'])[:8]; m['record_ids'] = list(dict.fromkeys(m['record_ids'] + c['record_ids']))
            if m.get('raf_delta') is None: m['raf_delta'] = c.get('raf_delta')
    return sorted(merged.values(), key=lambda c: (-c['score'], c['hcc']))


# -- execute ------------------------------------------------------------------------------------------------------------
def run(stack, patient_ids, params, as_of, *, decide=None):
    started = time.perf_counter()
    p = validate(params)
    from . import risk_adjustment as ra
    from .atomic_questions import ask, dated, _spent
    scoped = sorted(patient_ids)
    ids = scoped[:p['maximum_patients']]
    model = ra.load_model('V28')
    report = structured(stack, ids, p['year']) if ids and {'recapture', 'evidence_rules'} & set(p['channels']) else {}
    pats = {x['patient_id']: x for x in report.get('patients') or []}
    rows, calls, usd, errors, by = [], 0, 0.0, 0, {}
    live = bool(getattr(stack, 'allow_live', False))
    from . import system_one_deciders
    order = system_one_deciders.order_for(TASK_KEY)
    for pid in ids:
        pat = pats.get(pid)
        age, sex = (pat or {}).get('age') or 70, 2 if (pat or {}).get('sex') == 'F' else 1
        coded = {h['hcc'] for h in ((pat or {}).get('hccs') or {}).get('V28', [])}
        cands = [c for c in (candidates_for(pat, model) if pat else []) if
                 (c['channel'] == 'recapture' and 'recapture' in p['channels']) or (c['channel'] != 'recapture' and 'evidence_rules' in p['channels'])]
        if 'notes' in p['channels']:
            try: cands += note_candidates(stack, pid, model, age, sex)
            except Exception as exc:  # noqa: BLE001 - text retrieval failing leaves the structured channels
                errors += 1; by['notes_failed'] = by.get('notes_failed', 0) + 1
        ranked = rank(cands, coded, model)
        for i, c in enumerate(ranked):
            row = {'patient_id': pid, 'rank': i + 1, 'hcc': c['hcc'], 'label': c['label'], 'icd10': c['icd10'], 'channels': c['channels'],
                   'score': round(c['score'], 3), 'raf_delta': c.get('raf_delta'), 'record_ids': c['record_ids'][:6],
                   'confirmation': None, 'probability': None, 'decided_by': None, 'sources': []}
            if p['confirm'] and i < p['top']:
                ev = [d for d in (dated({**e, 'date': e.get('date') or 'undated', 'text': e['text']}, as_of) for e in c['evidence']) if d]
                d = ask(confirm_question(f"{c['label']} ({c['icd10']})"), CONFIRM_OPTIONS, ev, as_of=as_of[:10], task_key=TASK_KEY,
                        providers=list(order), allow_live=live, decide=decide)
                k, u = _spent(d); calls, usd = calls + k, usd + u
                errors += bool(d.get('error')); by[d.get('decided_by') or 'none'] = by.get(d.get('decided_by') or 'none', 0) + 1
                texts = {e['id']: e for e in ev}
                cited = [x for x in d.get('evidence_ids') or [] if x in texts] or ([ev[0]['id']] if ev and d['label'] != 'unknown' else [])
                row.update(confirmation=d['label'], probability=(d.get('calibrated_probabilities') or d.get('probabilities') or {}).get(d['label']),
                           decided_by=d.get('decided_by'), sources=[{'patient_id': pid, 'evidence_id': x, 'text': texts[x]['text'],
                                                                     'date': texts[x]['date'], 'record_id': texts[x]['note_id']} for x in cited[:3]])
            rows.append(row)
    return {'schema': VERSION, 'as_of': as_of[:10], 'parameters': p, 'population_patients': len(scoped), 'evaluated_patient_ids': ids,
            'capped': len(scoped) > len(ids), 'rows': rows, 'risk_adjustment': {k: report.get(k) for k in ('version', 'status', 'period')},
            'system_one': {'decisions': sum(1 for r in rows if r['confirmation']), 'new_paid_api_calls': calls, 'estimated_usd': round(usd, 6),
                           'errors': errors, 'decided_by': by, 'order': list(order)},
            'new_paid_api_calls': calls, 'do_not_cache': errors > 0, 'elapsed_seconds': round(time.perf_counter() - started, 3),
            'clinical_decision_authority': False}


def answer(report, plan_, question):
    """Answer contract for analysis_graph.build_answer_core."""
    rows = report['rows']
    confirmed = [r for r in rows if r['confirmation'] == 'yes']
    chosen = list(dict.fromkeys(r['patient_id'] for r in (confirmed if report['parameters']['confirm'] else rows)))
    n = len(report['evaluated_patient_ids'])
    s1 = report['system_one']
    cap = (f"Capped: read the first {n} of {report['population_patients']} patients in scope (maximum "
           f"{report['parameters']['maximum_patients']} per call).") if report['capped'] else None
    value = (f"{len(rows)} ranked HCC suspects for {n} patients; {len(confirmed)} confirmed by System One "
             f"({len(chosen)} patients with a confirmed suspect)")
    method = (f"{VERSION}: closed retrieval channels ({', '.join(report['parameters']['channels'])}) -> ranked in code -> top "
              f"{report['parameters']['top']} per patient confirmed by System One (local student, then {' -> '.join(s1['order'])}).")
    limitations = [method, 'Suspects are documentation review prompts with cited evidence, not diagnoses; no code is generated freely.',
                   f"System One confirmations {s1['decisions']} (new paid calls {s1['new_paid_api_calls']}, ~${s1['estimated_usd']:.4f}; decided by {s1['decided_by']})."]
    if cap: limitations.insert(0, cap); value += f' [{cap}]'
    table = [{k: r[k] for k in ('patient_id', 'rank', 'hcc', 'label', 'icd10', 'channels', 'score', 'raf_delta', 'confirmation', 'probability', 'decided_by')}
             for r in rows]
    partial = report['capped'] or plan_.get('coverage') != 'supported_observation'
    return {'answer_type': 'table', 'count': len(chosen), 'patient_ids': chosen, 'value': value, 'table': table,
            'sources': [s for r in confirmed for s in r['sources']], 'method': method, 'limitations': limitations,
            'status': 'partial' if partial else 'answered', 'membership_role': 'selected'}


# -- cascade rung -------------------------------------------------------------------------------------------------------
QUESTION = re.compile(r'\b(?:suspect\w*|undocumented|uncoded|not (?:yet )?coded|missing (?:hccs?|diagnos\w+|codes?)|coding gaps?|'
                      r'documentation gaps?|recapture|un-?captured|should be coded|could be coded)\b', re.I)
SUBJECT = re.compile(r'\b(?:hccs?|icd-?10|diagnos\w+|conditions?|risk adjustment|raf|risk scores?|codes?)\b', re.I)


def hcc_question(question):
    return bool(QUESTION.search(question) and SUBJECT.search(question))


def graph_of(question, ids, params):
    from .evidence_proposers import cohort_node, graph_of as base
    from .cohorts import empty
    population = cohort_node(ids, f"The named patient(s): {', '.join(ids[:10])}") if ids else \
        {'id': 'population', 'operation': 'cohort', 'inputs': [], 'parameters': {'specification': empty()}, 'purpose': 'All observed patients'}
    node = {'id': 'suspects', 'operation': 'hcc_suspects', 'inputs': ['population'], 'parameters': params,
            'purpose': 'Retrieve candidate ICD-10/HCCs from closed channels, rank in code, confirm the top ones with System One'}
    return base(question, [population, node])


def tier(stack, question, population=None, *, decide=None, maximum_patients=None):
    """Ladder-rung contract: (graph, receipt) or None. Free retrieval/ranking; System One confirmations only live (memoized,
    so stack.run_graph replays them free)."""
    if not hcc_question(question): return None
    started = time.perf_counter()
    from .evidence_proposers import named_patients, population_ids
    ids = named_patients(stack, question) or population_ids(population) or []
    params = {'question': question[:1000], 'top': 3, 'confirm': True, 'channels': list(CHANNELS),
              'maximum_patients': max(1, min(HARD_MAX_PATIENTS, int(maximum_patients or (len(ids) if ids else 10))))}
    as_of = stack.retriever.meta['snapshot']['as_of']
    graph = graph_of(question, ids, params)
    from .analysis_graph import validate as validate_graph
    validate_graph(graph, as_of)
    if ids:
        pre = run(stack, ids, params, as_of, decide=decide)
    else:
        from .cohorts import select
        pre = run(stack, select(stack.path, graph['nodes'][0]['parameters']['specification'])['patient_ids'], params, as_of, decide=decide)
    if not pre['rows']: return None
    s1 = pre['system_one']
    return graph, {'route': 'hcc_suspecting', 'model': 'system_one (local student -> ' + ' -> '.join(s1['order']) + ')', 'frontier_called': False,
                   'new_paid_api_calls': s1['new_paid_api_calls'],
                   'steps': [{'name': 'system_one', 'receipt': {'cost_estimate': {'estimated_usd': s1['estimated_usd']},
                                                                'cache_hit': s1['new_paid_api_calls'] == 0, 'decided_by': s1['decided_by']}}],
                   'cost_class': 'free retrieval/ranking; Jev/Perplexity ~$0.0002 per confirmation',
                   'components': ['hcc_suspecting', 'risk_adjustment', 'condition_suspects', 'warehouse_retriever', 'system_one',
                                  'decider_bakeoff', 'system_one_student'],
                   'preflight': {'patients': len(pre['evaluated_patient_ids']), 'candidates': len(pre['rows']),
                                 'confirmed': sum(1 for r in pre['rows'] if r['confirmation'] == 'yes'), 'errors': s1['errors']},
                   'elapsed_seconds': round(time.perf_counter() - started, 3)}
