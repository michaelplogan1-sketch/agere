"""Independent N-version verification for the hosted SQL ladder (FMEA FM-13, FM-19; research report items 3 and 7).

Two models of the same family agreeing is weak evidence: they share training data and prompts, so they make the same
mistake together (every adjudicated hosted-route engine error in blind sets M/N/O passed Haiku+Sonnet agreement or the
judge). Candidates therefore carry a *family*:

  hosted      any hosted model (primary, second, judge)                         - one family, however many models
  local       the bottled local SQL model (nl2sql.MODEL_DIR), when present        - independent weights
  compiler    a deterministic plan from Agere's own question compiler, compiled  - independent of any model
              without the strictness that made it decline (unexplained filler words dropped; never numbers or
              operator words), used only as a candidate

All valid candidates are executed and clustered by result signature (verify.signature: order- and alias-insensitive;
a count and a patient list agree when the count is the list's size). Acceptance:

  * the winning cluster holds >= 2 families                        -> accepted ('independent')
  * the local model disagrees with the hosted agreement            -> disputed (judge sees every candidate); a relaxed
                                                                      compiler dissent is recorded and blocks capture as a
                                                                      verified pair, but does not route (dissent_routes())
  * hosted-only agreement                                          -> accepted only when the deterministic oracles
                                                                      (oracles.py + high-precision answer checks) find
                                                                      nothing; otherwise disputed (judge)
When the two best-supported clusters differ by a definitional choice their DEFINITION lines state (date field, status,
counting unit, enrollment, inclusivity) rather than by an error, both readings are returned (FM-19): never silently pick.
Nothing here calls a hosted model; the free candidates replace, not add to, paid calls (when a free independent
candidate matches the primary, the second hosted model is not called).
"""
import json, os, re

VERSION = 'verification-v2/1'
FAMILY = {'primary': 'hosted', 'second': 'hosted', 'judge': 'hosted', 'local_model': 'local', 'compiler': 'compiler'}
# Oracle / answer-check findings that block hosted-only agreement (route to the judge). QC-PERIOD (a partial latest
# period is a disclosure, not an error), QC-DENOM (info) and QC-VERSION (static; superseded here by the partition
# oracle's row-vs-key count) do not.
ROUTING_CONTROLS = {'QC-NUMBERS', 'QC-UNITS', 'QC-PARTS', 'QC-CLOCK', 'QC-PERTURB', 'QC-PARTITION', 'QC-SHAPE', 'QC-EMPTY'}


def dissent_routes():
    """Families whose disagreement with a hosted agreement sends the question to the judge. The relaxed compiler answers
    a slightly broader question (it dropped words it could not interpret), so on the M/N/O replay its dissent was right
    5 times and wrong 7 times: by default it only blocks capture as a verified pair (AGERE_COMPILER_DISSENT=route makes
    it route). The local model has independent weights and routes."""
    return {'local'} | ({'compiler'} if os.environ.get('AGERE_COMPILER_DISSENT', 'capture') == 'route' else set())


def family(label):
    return FAMILY.get(label, 'hosted' if label.startswith(('claude', 'hosted')) else label)


# ---- result agreement -------------------------------------------------------------------------------------------------
def signature(result):
    from . import verify
    return verify.signature(result)


def _scalar(result):
    if not isinstance(result, dict): return None
    if 'value' in result and isinstance(result['value'], (int, float)) and not isinstance(result['value'], bool): return float(result['value'])
    rows = result.get('rows') or []
    if len(rows) == 1:
        vals = list(rows[0].values()) if isinstance(rows[0], dict) else list(rows[0])
        if len(vals) == 1 and isinstance(vals[0], (int, float)) and not isinstance(vals[0], bool): return float(vals[0])
    return None


def _patients(result):
    if not isinstance(result, dict): return None
    if isinstance(result.get('patient_ids'), list): return set(result['patient_ids'])
    rows, cols = result.get('rows') or [], result.get('columns') or []
    if cols[:1] == ['patient_id'] and rows:
        return {(r.get('patient_id') if isinstance(r, dict) else r[0]) for r in rows}
    return None


def same_answer(a, b):
    """Two results say the same thing: equal signatures, equal scalars, equal patient sets, or a count equal to a set's size."""
    if signature(a) == signature(b): return True
    sa, sb = _scalar(a), _scalar(b)
    if sa is not None and sb is not None: return round(sa, 4) == round(sb, 4)
    pa, pb = _patients(a), _patients(b)
    if pa is not None and pb is not None: return pa == pb
    if sa is not None and pb is not None: return sa == len(pb)
    if sb is not None and pa is not None: return sb == len(pa)
    return False


def cluster(candidates):
    """Group valid candidates by answer; clusters sorted by (families, members), strongest first."""
    groups = []
    for c in candidates:
        if c.get('result') is None: continue
        for g in groups:
            if same_answer(g['members'][0]['result'], c['result']):
                g['members'].append(c); break
        else:
            groups.append({'members': [c]})
    for g in groups:
        g['families'] = sorted({c['family'] for c in g['members']})
        g['labels'] = [c['label'] for c in g['members']]
    return sorted(groups, key=lambda g: (-len(g['families']), -len(g['members']), [c['order'] for c in g['members']][0]))


def blocking_findings(findings):
    return [f for f in findings or [] if f.get('severity', 'warning') == 'warning' and f.get('control') in ROUTING_CONTROLS]


def decide(candidates, oracle=None):
    """-> {'status': 'accepted'|'disputed'|'none', 'basis', 'winner', 'clusters', 'findings'}.
    oracle(candidate) -> list of findings for the hosted-only agreement case."""
    clusters = cluster(candidates)
    out = {'clusters': [{'labels': g['labels'], 'families': g['families']} for g in clusters], 'findings': []}
    if not clusters: return {**out, 'status': 'none', 'basis': 'no valid candidate'}
    top = clusters[0]
    rep = top['members'][0]
    if len(top['families']) >= 2:
        return {**out, 'status': 'accepted', 'basis': 'independent', 'winner': rep, 'agreement': '='.join(top['labels'])}
    independent_elsewhere = [c for g in clusters[1:] for c in g['members'] if c['family'] != 'hosted']
    routes = dissent_routes()
    strong = [c for c in independent_elsewhere if c['family'] in routes]
    if top['families'] == ['hosted'] and strong:
        return {**out, 'status': 'disputed', 'basis': 'independent source disagrees: ' + ', '.join(c['label'] for c in strong),
                'winner': rep}
    out['dissent'] = [c['label'] for c in independent_elsewhere]  # weak dissent: recorded, blocks capture, does not route
    if len(top['members']) >= 2:
        findings = blocking_findings(oracle(rep) if oracle else [])
        out['findings'] = findings
        if not findings:
            return {**out, 'status': 'accepted', 'basis': 'same_family_with_oracles', 'winner': rep, 'agreement': '='.join(top['labels'])}
        return {**out, 'status': 'disputed', 'basis': 'same-family agreement failed a deterministic check: ' +
                '; '.join(f['control'] for f in findings), 'winner': rep}
    return {**out, 'status': 'disputed', 'basis': 'no agreement', 'winner': None}


# ---- FM-19: definitional disagreement ---------------------------------------------------------------------------------
DEFINITION_DIMENSIONS = {
    'date field': r'\b(?:event_date|available_date|first_available_date|service_date|admit_date|discharge_date|scheduled_date|booked_date|'
                  r'start_time|end_time|first_submitted_date|first_payer_cash_date|first_adjudication_date|arrival|visit date|service date|'
                  r'admission date|discharge date|scheduled date|booked date)\b',
    'counting unit': r'\b(?:patients?|members?|visits?|encounters?|claims?|records?|rows?|stays?|admissions?|appointments?|fills?|dispenses?|versions?)\b',
    'status': r'\b(?:final|completed|cancel(?:l)?ed|paid|denied|reversed|active|open|closed|kept|no[- ]show|dispensed|ordered|listed|current|latest)\b',
    'population': r'\b(?:continuously enrolled|enrolled|attributed|assigned|all members|roster|aged? \d+|adults?|children|medicare|medicaid|commercial|'
                  r'inpatient|observation|including|excluding|excludes?|includes?)\b',
    'window': r'\b(?:inclusive|exclusive|calendar|rolling|trailing|as of|through|between|within \d+|since)\b',
}
_DEF_RX = re.compile(r'--\s*DEFINITION:\s*(.+)')


def definition_of(sql):
    m = _DEF_RX.search(sql or '')
    return m.group(1).strip() if m else None


def _shape(result):
    if _scalar(result) is not None: return 'scalar'
    if _patients(result) is not None: return 'patients'
    cols = (result or {}).get('columns') or []
    return f'table{len(cols)}'


def definitional_difference(a, b):
    """{'dimensions': [...], 'a': ..., 'b': ...} when two disagreeing candidates differ by a stated definitional choice
    with the same answer shape and comparable magnitude; None when the difference looks like an error."""
    da, db = definition_of((a.get('params') or {}).get('query')), definition_of((b.get('params') or {}).get('query'))
    if not da or not db or _shape(a['result']) != _shape(b['result']): return None
    sa, sb = _scalar(a['result']), _scalar(b['result'])
    if sa is not None and sb is not None:
        if sa <= 0 or sb <= 0 or max(sa, sb) / min(sa, sb) > 3: return None  # an order-of-magnitude gap is an error, not a reading
    dims = []
    for name, rx in DEFINITION_DIMENSIONS.items():
        ta = {t.lower().rstrip('s') for t in re.findall(rx, da, re.I)}
        tb = {t.lower().rstrip('s') for t in re.findall(rx, db, re.I)}
        if ta != tb and (ta or tb): dims.append({'dimension': name, 'a': sorted(ta - tb), 'b': sorted(tb - ta)})
    if not dims: return None
    return {'dimensions': dims, 'a': da, 'b': db}


def _preview(result):
    if _scalar(result) is not None: return _scalar(result)
    if _patients(result) is not None: return {'patients': len(_patients(result))}
    return {'rows': (result or {}).get('rows', [])[:12]}


def clarifying_question(diff):
    """One high-information question built from the first differing dimension (deterministic template)."""
    d = diff['dimensions'][0]
    a = ', '.join(d['a']) or 'the first reading'
    b = ', '.join(d['b']) or 'the second reading'
    return f"Which {d['dimension']} should this use: {a} or {b}?"


def readings(chosen, alternative, diff):
    """Both readings, the default first, each with its stated definition, query and result."""
    def one(c, default):
        return {'definition': definition_of(c['params']['query']) if c.get('params') else c.get('definition'),
                'query': (c.get('params') or {}).get('query'), 'returns': (c.get('params') or {}).get('returns'),
                'result': _preview(c['result']), 'sources': c.get('labels') or [c['label']], 'default': default}
    return {'readings': [one(chosen, True), one(alternative, False)], 'differs_by': [d['dimension'] for d in diff['dimensions']],
            'clarifying_question': clarifying_question(diff), 'version': VERSION}


def attach_readings(answer, readings_receipt):
    """Copy of the answer carrying both readings (default first) and the one clarifying question; a QC-READINGS warning
    makes the alternative visible in the headline area and the /ask card. Numbers of the default are unchanged."""
    from .answer_checks import _add
    out = dict(answer)
    out['readings'] = readings_receipt['readings']
    out['clarifying_question'] = readings_receipt.get('clarifying_question')
    default, alt = readings_receipt['readings'][0], readings_receipt['readings'][1]
    fmt = lambda r: json.dumps(r['result'], default=str)[:80]
    _add(out, 'QC-READINGS', 'FM-19',
         f"Two defensible readings differ by {', '.join(readings_receipt.get('differs_by') or ['definition'])}: shown is "
         f"\"{(default.get('definition') or 'the default')[:140]}\" ({fmt(default)}); the alternative \"{(alt.get('definition') or '')[:140]}\" "
         f"gives {fmt(alt)}. {readings_receipt.get('clarifying_question') or ''}".strip(),
         differs_by=readings_receipt.get('differs_by'))
    return out


def alternative_reading(chosen, candidates):
    """The best-supported candidate that disagrees with the chosen one by a definitional choice, with the difference."""
    others = [g for g in cluster(candidates) if not any(same_answer(m['result'], chosen['result']) for m in g['members'][:1])]
    for g in others:
        rep = g['members'][0]
        diff = definitional_difference(chosen, rep)
        if diff: return {**rep, 'labels': g['labels']}, diff
    return None, None


# ---- free independent candidate sources ---------------------------------------------------------------------------------
_KEEP = re.compile(r'\d')


def relaxed_compile(question, profile, as_of):
    """The compiler's plan for the question after dropping only the filler words it could not interpret.
    Never drops numbers or operator words (that would change the question). -> (graph, info) or (None, info)."""
    from . import local_compiler as lc
    graph, info = lc.compile(question, profile, as_of)
    if graph is not None: return graph, {**info, 'relaxed': False}
    unexplained = info.get('unexplained') or []
    if not unexplained or not str(info.get('reason', '')).startswith(('words not interpreted', 'measure ')): return None, info
    operator_words = getattr(lc, 'OPERATOR_WORDS', set())
    if any(_KEEP.search(w) or w in operator_words for w in unexplained): return None, {**info, 'relaxed_declined': 'unexplained word carries meaning'}
    q = question
    for w in sorted(set(unexplained), key=len, reverse=True):
        q = re.sub(r'(?<![a-z0-9])' + re.escape(w) + r'(?![a-z0-9])', ' ', q, flags=re.I)
    q = ' '.join(q.split())
    graph, info2 = lc.compile(q, profile, as_of)
    return graph, {**info2, 'relaxed': True, 'dropped_words': sorted(set(unexplained)), 'relaxed_question': q}


def compiler_candidate(path, question, as_of, profile):
    """Deterministic candidate: a single-SQL plan returns its query; a cohort-only plan returns the patient set.
    Other plan shapes (aggregates, engines) are not comparable to a SQL answer and give no candidate."""
    graph, info = relaxed_compile(question, profile, as_of)
    if graph is None or not graph.get('nodes'): return None
    nodes = graph['nodes']
    if len(nodes) == 1 and nodes[0]['operation'] == 'sql':
        return {'label': 'compiler', 'sql': nodes[0]['parameters']['query'], 'returns': nodes[0]['parameters'].get('returns'),
                'definition': graph.get('interpretation'), 'compile': {k: info.get(k) for k in ('relaxed', 'dropped_words')}}
    if len(nodes) == 1 and nodes[0]['operation'] == 'cohort':
        from . import cohorts
        selected = cohorts.select(path, nodes[0]['parameters']['specification'])
        if selected.get('unknown_patient_ids'): return None  # unknown membership: not a definite answer
        ids = sorted(selected['patient_ids'])
        return {'label': 'compiler', 'result': {'patient_ids': ids},
                'definition': graph.get('interpretation'), 'compile': {k: info.get(k) for k in ('relaxed', 'dropped_words')}}
    return None


def local_model_candidate(path, question, as_of, model=None):
    """Bottled local SQL model candidate, when the model directory is present (skips gracefully otherwise)."""
    from . import nl2sql
    if model is None:
        if os.environ.get('AGERE_LOCAL_CANDIDATE', 'on') == 'off' or not nl2sql.MODEL_DIR.exists(): return None
        try:
            import mlx_lm  # noqa: F401
        except ImportError:
            return None
    if model is None:
        class _Greedy:  # one deterministic generation is enough for a candidate (no self-consistency sampling)
            single = True
            def __call__(self, *a, **k): return nl2sql.LocalModel.get()(*a, **k)
        model = _Greedy()
    params, result, receipt = nl2sql.answer(path, question, as_of, model=model)
    return {'label': 'local_model', 'sql': params['query'], 'returns': params['returns'], 'model': (receipt['attempts'] or [{}])[-1].get('model')}


def default_sources(path, as_of, profile):
    """The free candidate sources for the hosted ladder, in order (each may return None)."""
    sources = []
    if os.environ.get('AGERE_COMPILER_CANDIDATE', 'on') != 'off' and profile is not None:
        sources.append(lambda q: compiler_candidate(path, q, as_of, profile))
    if os.environ.get('AGERE_LOCAL_CANDIDATE', 'on') != 'off':
        sources.append(lambda q: local_model_candidate(path, q, as_of))
    return sources


def oracle_for(path, question, as_of, execute=None):
    """Findings for one candidate: the text/partition oracles plus the high-precision answer checks."""
    from . import answer_checks, oracles

    def run(candidate):
        params, result = candidate.get('params') or {}, candidate.get('result') or {}
        query = params.get('query')
        if not query: return []
        answer = {'status': 'answered', 'answer_type': params.get('returns') or 'value', 'query': query}
        if 'value' in result: answer['value'] = result['value']
        if isinstance(result.get('patient_ids'), list):
            answer.update(patient_ids=result['patient_ids'], count=len(result['patient_ids']))
        if result.get('rows') and result.get('columns'):
            answer['table'] = [r if isinstance(r, dict) else dict(zip(result['columns'], r)) for r in result['rows'][:200]]
            if params.get('returns') == 'table': answer['answer_type'] = 'table'
        answer_checks.annotate(answer, question, as_of, execute=execute)
        return [c for c in answer.get('quality_checks') or [] if c.get('control') != 'error']
    return run
