"""Confidently-wrong answer guard: never answer "none" without looking everywhere (docs/agere/DATA_CONNECTION_ARCHITECTURE.md).

Before this, the answer ladder escalated only a decline. A route that answered the wrong question with confidence shipped:
'How many Medicare annual wellness visits in 2029?' -> 0 (the data sat in a raw stream the modeled layer did not cover), a
dollar question routed to the claims-review engine -> '7' (a count of review flags), PHQ-9 questions -> 0 rows. This module
runs on the EXECUTED answer of every auto-planned route (local compiler, rewrite, learned/semantic caches, bottled model,
metric router, operator router, hosted SQL, typed router, frontier, exact-question cache):

1. Suspicion (deterministic, `suspicion`): a count or sum of 0, an empty set or table (absence not asked), a dollar question
   answered with a count / patient list / multi-row table, a dollar total under $100, a percent outside [0, 100], a rate
   answered without a division (oracles.shape, QC-SHAPE). Extends QC-EMPTY / QC-SHAPE / QC-UNITS from answer-level warnings to
   a routing decision.
2. Catalog probe (`probe`, local, no API): the question's key terms (stemmed words and code literals such as G0438, PHQ-9) are
   looked up in an inverted index over the FULL received catalog - every stream name, field name, low-cardinality value and
   list-element code value in the source profile - weighted by inverse stream frequency; the local sentence encoder (when one
   is loaded) only re-ranks. Likely sources are then checked against the warehouse (read-only, indexed counts on the
   semantic layer's record table) for rows in the question's period.
3. Decision (`review`): a likely source the answer did not read (or a shape mismatch) -> re-plan ONCE through the verified
   hosted SQL ladder with that source's stream forced into the writer's schema slice and a note naming the matched values.
   A re-plan that passes the suspicion checks replaces the answer; a re-plan that again finds 0 while reading the probed
   source confirms the zero; anything else abstains naming the unused source ("wellness_visit_services has 202 records in
   2029 with hcpcs_code 'G0438' that this answer didn't read") - AGERE_GUARD_POLICY=low keeps the number at low confidence
   instead. A zero for which the probe finds nothing anywhere ships with a stated catalog check (QC-CATALOG, info).
4. Cross-check (`cross_check`): when the answer's path is not certified (gate_policy.assess, or the calibrated-threshold
   fallback when gate_policy is absent), one independent path runs - hosted SQL for the local compiler / metric router /
   operator router (the original answer is a free candidate in the ladder, so a matching primary skips the second model),
   the relaxed compiler or the metric router for hosted SQL and its caches - and is compared under verification_v2's
   agreement rules. Agree -> confidence detail 'cross_checked'. Disagree -> the frontier planner breaks the tie when
   available; otherwise both readings are returned at low confidence with the reason. AGERE_CROSS_CHECK=on|off.

Every extra call is recorded as a planner step (economical_planner.economics prices it) and in receipt['answer_guard'].
Synthetic data only. AGERE_ANSWER_GUARD=off disables 1-3.
"""
import json, math, os, re, sqlite3, time
from collections import defaultdict

VERSION = 'answer-guard/1'

# ---------------------------------------------------------------------------------------------------- question text
_STOP = {'how', 'many', 'much', 'what', 'which', 'who', 'whom', 'whose', 'the', 'and', 'for', 'with', 'did', 'does', 'was', 'were',
         'are', 'had', 'has', 'have', 'from', 'that', 'this', 'there', 'their', 'our', 'any', 'all', 'per', 'each', 'into', 'over',
         'between', 'they', 'them', 'been', 'its', 'only', 'also', 'list', 'show', 'give', 'count', 'number', 'patient', 'patients',
         'total', 'year', 'month', 'during', 'since', 'than', 'more', 'less', 'least', 'most', 'get', 'got', 'can', 'you', 'not',
         'record', 'records', 'data', 'value', 'amount', 'people', 'member', 'members', 'last', 'first', 'out', 'when', 'where',
         'january', 'february', 'march', 'april', 'june', 'july', 'august', 'september', 'october', 'november', 'december'}
_CODE_RX = re.compile(r'(?<![\w-])(?:[a-z]\d{2,4}[a-z0-9]{0,3}|\d{4,5}-\d|[a-z]{2,5}-\d{1,2}[a-z]?)(?![\w-])', re.I)
_YEAR = re.compile(r'\b((?:19|20)\d{2})\b')
_ISO = re.compile(r'\b(\d{4}-\d{2}-\d{2})\b')


def _stem(t):
    if len(t) > 3 and t.endswith('s') and not t.endswith('ss'): t = t[:-1]
    for suf in ('ation', 'ion', 'ing', 'ed', 'al'):
        if t.endswith(suf) and len(t) - len(suf) >= 4:
            t = t[:-len(suf)]; break
    return t[:7]


def _code_forms(text):
    return {re.sub(r'[^a-z0-9]', '', m.group(0).lower()) for m in _CODE_RX.finditer(str(text))}


def terms(text):
    """Stemmed content words plus normalised code literals ('PHQ-9' -> 'phq9', 'G0438' -> 'g0438')."""
    text = str(text or '')
    out = []
    for w in re.findall(r'[a-z0-9]+', text.lower().replace('_', ' ')):
        if len(w) <= 2 or w in _STOP or w.isdigit(): continue
        out.append(w if re.search(r'\d', w) else _stem(w))
    out += sorted(_code_forms(text))
    return list(dict.fromkeys(out))


def period_of(question):
    """(start, end) ISO dates the question names: two dates, one date, or one year; None otherwise."""
    dates = _ISO.findall(question or '')
    if len(dates) >= 2: return min(dates), max(dates)
    years = sorted(set(_YEAR.findall(_ISO.sub(' ', question or ''))))
    if dates and not years: return dates[0], dates[0]
    if years: return f'{years[0]}-01-01', f'{years[-1]}-12-31'
    return None


# ---------------------------------------------------------------------------------------------------- 1. suspicion
_ABSENCE_Q = re.compile(r'\b(?:no|not|never|without|none|zero|missing|lacks?|lacking|absent|fail(?:ed|s)? to)\b', re.I)
_MONEY_Q = re.compile(r'\$|\bdollars?\b|\busd\b|\b(?:how much|total|sum|net)\b[^?]*\b(?:paid|pay|payments?|spend|spent|spending|costs?|'
                      r'revenue|reimburse\w*|charges?|charged|billed|allowed|owed?|balances?|collected|cash)\b|'
                      r'\b(?:paid|spent|spend|cost|billed|allowed|charged|collected|reimbursed)\b[^?]*\bhow much\b', re.I)
_PER_UNIT_Q = re.compile(r'\b(?:average|avg|mean|median|per (?:claim|member|patient|visit|month|day)|pmpm|pmpy|rate|ratio|percent|'
                         r'percentage|share|proportion|%)\b|%', re.I)
_PERCENT_Q = re.compile(r'%|\bpercent(?:age)?\b|\bshare\b|\bproportion\b', re.I)
_MONEY_COL = re.compile(r'usd|dollar|cents|amount|paid|cost|allowed|billed|spend|charge|balance|revenue|cash|payment', re.I)
_TABLE_Q = re.compile(r'\b(?:by|per|for each|each|break(?:s)? ?(?:it )?(?:down|out)|split|tabulate|monthly|quarterly|weekly|trend|list)\b'
                      r'(?! 1,?000)', re.I)


def money_question(question):
    q = question or ''
    return bool(_MONEY_Q.search(q)) and not re.search(r'\bhow many\b|\bnumber of\b', q, re.I)


def _num(v): return isinstance(v, (int, float)) and not isinstance(v, bool)


def _asserted(answer):
    return isinstance(answer, dict) and answer.get('answer_type') not in (None, 'unsupported') and \
        answer.get('status') in ('answered', 'partial')


def suspicion(question, answer):
    """-> [{'kind', 'message'}]: why the answer may be confidently wrong (empty list = nothing suspicious)."""
    if not _asserted(answer): return []
    q, t, out = question or '', answer.get('answer_type'), []
    absence = bool(_ABSENCE_Q.search(q))
    v = answer.get('count') if t == 'count' else answer.get('value') if t == 'value' else None
    if not absence:
        if t in ('count', 'value') and _num(v) and v == 0:
            out.append({'kind': 'zero', 'message': 'the answer is 0'})
        elif t == 'patient_set' and not (answer.get('patient_ids') or answer.get('count')):
            out.append({'kind': 'empty', 'message': 'the answer is an empty patient set'})
        elif t == 'table' and isinstance(answer.get('table'), list) and not answer['table']:
            out.append({'kind': 'empty', 'message': 'the answer is an empty table'})
    if money_question(q):
        rows = [r for r in (answer.get('table') or []) if isinstance(r, dict)] if isinstance(answer.get('table'), list) else []
        if t in ('count', 'patient_set'):
            out.append({'kind': 'money_shape', 'message': f"the question asks for dollars but the answer is a {'count' if t == 'count' else 'list of patients'}"})
        elif t == 'table' and rows and not _TABLE_Q.search(q) and (len(rows) > 1 or not any(_MONEY_COL.search(k) for k in rows[0])):
            out.append({'kind': 'money_shape', 'message': f'the question asks for one dollar amount but the answer is a {len(rows)}-row table'
                                                          + ('' if len(rows) > 1 else ' with no money column')})
        elif t == 'value' and _num(v) and v != 0 and abs(v) < 100 and not _PER_UNIT_Q.search(q):
            out.append({'kind': 'money_small', 'message': f'the question asks for a dollar total but the answer is {v}'})
    if _PERCENT_Q.search(q) and t == 'value' and _num(v) and not (0 <= v <= 100.0001) and not re.search(r'\bper 1,?000\b', q, re.I):
        out.append({'kind': 'rate_range', 'message': f'the question asks for a percentage but the answer is {v}'})
    try:
        from . import oracles
        if oracles.shape(q, answer):
            out.append({'kind': 'rate_shape', 'message': 'the question asks for a rate but the answer has no division'})
    except Exception:  # noqa: BLE001 - a check never breaks answering
        pass
    return out


ZERO_KINDS = {'zero', 'empty'}


# ---------------------------------------------------------------------------------------------------- 2. catalog probe
_SKIP_FIELDS = {'synthetic', 'source_table', 'identity_link_basis', 'identity_status', 'event_date_precision', 'source_patient_id',
                'patient_id', 'source_system', 'organization_id'}
_INDEX = {}


class _Index:
    def __init__(self, profile):
        self.inv = defaultdict(list)  # term -> [(stream, kind, field, value, records)]
        self.streams = profile.get('streams') or {}
        for s, spec in self.streams.items():
            for t in terms(s): self.inv[t].append((s, 'name', None, None, spec.get('records') or 0))
            for f, fs in (spec.get('fields') or {}).items():
                if not fs.get('populated') or f in _SKIP_FIELDS or f.endswith(('_id', '_ids')): continue
                for t in terms(f): self.inv[t].append((s, 'field', f, None, fs['populated']))
                for src in ('values', 'element_values'):
                    for v, n in (fs.get(src) or {}).items():
                        if not isinstance(v, str) or not v or len(v) > 80: continue
                        for t in terms(v): self.inv[t].append((s, 'value' if src == 'values' else 'element', f, v, n))
        self.df = {t: len({e[0] for e in lst}) for t, lst in self.inv.items()}


def _index(profile):
    key = (profile.get('source_digest'), id(profile))
    if key not in _INDEX:
        if len(_INDEX) > 4: _INDEX.clear()
        _INDEX[key] = _Index(profile)
    return _INDEX[key]


def _code_fields(spec, limit=3):
    """Standard-code columns of a stream ({'hcpcs_code': ['G0438']}): HCPCS/CPT/LOINC/ICD/NDC values name what a source holds."""
    out = {}
    for f, fs in (spec.get('fields') or {}).items():
        if not re.search(r'(?:^|_)(?:code|codes|hcpcs|cpt|loinc|icd10?|ndc|drg|snomed|rxnorm)(?:_|$)', f) or not fs.get('populated'): continue
        vals = fs.get('values') or {}
        if vals and len(vals) <= 12:
            out[f] = sorted(vals, key=lambda v: -vals[v])[:limit]
    return out


def _connect_ro(path):
    from . import sql_query
    target = sql_query.semantic_layer.ensure(path)
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    deadline = time.perf_counter() + 8
    db.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 10000)
    db.set_authorizer(sql_query._authorizer)
    return db


def _period_counts(db, cand, period):
    """Rows of the stream (and of its best matching values) in the period, from the read-only record table."""
    s = cand['stream']
    lo, hi = period if period else ('0000-00-00', '9999-12-31')
    cand['period'] = list(period) if period else None
    cand['period_records'] = db.execute('SELECT count(*) FROM record WHERE stream = ? AND event_date BETWEEN ? AND ?', (s, lo, hi)).fetchone()[0]
    for ev in cand['evidence'][:3]:
        if ev.get('value') is None: continue
        f = ev['field']
        if not re.fullmatch(r'[A-Za-z0-9_]+', f): continue
        if ev['kind'] == 'element':
            sql = (f"SELECT count(*) FROM record WHERE stream = ? AND event_date BETWEEN ? AND ? AND EXISTS "
                   f"(SELECT 1 FROM json_each(json_extract(payload,'$.{f}')) WHERE value = ?)")
        else:
            sql = f"SELECT count(*) FROM record WHERE stream = ? AND event_date BETWEEN ? AND ? AND json_extract(payload,'$.{f}') = ?"
        ev['period_records'] = db.execute(sql, (s, lo, hi, ev['value'])).fetchone()[0]


def _encoder_scores(encoder, question, streams, idx):
    if encoder is None or not streams: return {}
    try:
        import numpy as np
        docs = []
        for s in streams:
            spec = idx.streams[s]
            fields = [f for f, fs in (spec.get('fields') or {}).items() if fs.get('populated') and f not in _SKIP_FIELDS][:25]
            docs.append(s.replace('_', ' ') + ': ' + ', '.join(f.replace('_', ' ') for f in fields))
        vec = np.asarray(encoder.encode([question] + docs), dtype=float)
        norms = np.linalg.norm(vec, axis=1)
        sims = vec[1:] @ vec[0] / np.maximum(norms[1:] * norms[0], 1e-12)
        return {s: float(x) for s, x in zip(streams, sims)}
    except Exception:  # noqa: BLE001 - the encoder only re-ranks; lexical evidence decides
        return {}


def probe(question, profile, *, path=None, k=3, encoder=None, used=()):
    """Search the full received catalog for sources matching the question's key terms.
    -> {'terms', 'unmatched_terms', 'period', 'streams_searched', 'candidates': [{'stream', 'score', 'coverage', 'matched_terms',
       'evidence': [{'field', 'value', 'kind', 'records', 'period_records'?}], 'records', 'period_records'?, 'likely', 'used'}]}"""
    idx = _index(profile)
    n = max(len(idx.streams), 1)
    qterms = terms(question)
    known = [t for t in qterms if t in idx.df]
    idf = {t: math.log(1 + n / idx.df[t]) for t in known}
    total = sum(idf.values())
    codes = _code_forms(question)
    per = defaultdict(lambda: {'terms': {}, 'evidence': {}})
    for t in known:
        for s, kind, f, v, cnt in idx.inv[t]:
            e = per[s]
            e['terms'][t] = max(e['terms'].get(t, 0), {'name': 1.0, 'value': 1.0, 'element': 1.0, 'field': 0.6}[kind])
            if kind != 'name':
                ev = e['evidence'].setdefault((f, v), {'field': f, 'value': v, 'kind': kind, 'records': cnt, 'terms': set()})
                ev['terms'].add(t)
    distinct_cut = max(3, n * 0.03)
    cands = []
    for s, e in per.items():
        matched = e['terms']
        score = sum(idf[t] * w for t, w in matched.items())
        coverage = sum(idf[t] for t in matched) / total if total else 0.0
        evidence = sorted(e['evidence'].values(), key=lambda x: (-(x['value'] is not None), -sum(idf[t] for t in x['terms']), -x['records']))
        for ev in evidence:  # a single value naming several of the question's terms is strong evidence
            if ev['value'] is not None and len(ev['terms']) >= 2: score += 0.5 * sum(idf[t] for t in ev['terms'])
        code_hit = bool(codes & set(matched))
        exact = [ev for ev in evidence if ev['value'] is not None and re.sub(r'[^a-z0-9]', '', ev['value'].lower()) in codes]
        if exact: score += 2.0 + 0.5 * math.log10(1 + max(ev['records'] for ev in exact))  # a stored value that IS the named code
        best_value = max((ev['records'] for ev in (exact or [ev for ev in evidence if ev['value'] is not None])), default=0)
        score += 0.2 * math.log10(1 + best_value)  # among equal matches, the source holding more of it
        distinctive = [t for t in matched if idx.df[t] <= distinct_cut and matched[t] >= 1.0]
        # one item (the stream's name, or one stored value) must carry most of the question: terms scattered over unrelated
        # values ('hip_fracture', 'joint_replacement') are not a source for 'hip replacement'
        name_terms = {t for t in matched if t in set(terms(s))}
        items = [name_terms] + [set(ev['terms']) | name_terms for ev in evidence if ev['value'] is not None]
        item_cov = max((sum(idf[t] for t in it) / total for it in items if it), default=0.0) if total else 0.0
        cands.append({'stream': s, 'score': round(score + (3.0 if code_hit else 0.0), 4), 'coverage': round(coverage, 3),
                      'matched_terms': sorted(matched), 'code_match': code_hit, 'records': (idx.streams[s] or {}).get('records'),
                      'evidence': [{**{k: v for k, v in ev.items() if k != 'terms'}, 'terms': sorted(ev['terms'])} for ev in evidence[:4]],
                      'codes': _code_fields(idx.streams[s]), 'item_coverage': round(item_cov, 3), 'likely': code_hit or (item_cov >= 0.6 and bool(distinctive)), 'used': s in set(used)})
    cands.sort(key=lambda c: (-c['score'], c['stream']))
    top = cands[:15]
    sims = _encoder_scores(encoder, question, [c['stream'] for c in top], idx)
    for c in top:
        if c['stream'] in sims:
            c['encoder_similarity'] = round(sims[c['stream']], 4); c['score'] = round(c['score'] + 0.5 * sims[c['stream']], 4)
    top.sort(key=lambda c: (-c['likely'], -round(c['item_coverage'], 2), -c['score'], c['stream']))
    top = top[:max(k, 1) * 2]
    period = period_of(question)
    if path is not None:
        try:
            db = _connect_ro(path)
        except (OSError, ValueError, KeyError, sqlite3.Error):
            db = None
        if db is not None:
            try:
                for c in top[:k + 1]:
                    try:
                        _period_counts(db, c, period)
                    except sqlite3.Error:
                        continue
                    best = next((ev for ev in c['evidence'] if ev.get('period_records') is not None), None)
                    if c['likely'] and (c['period_records'] == 0 or (best is not None and best['period_records'] == 0
                                                                     and all((ev.get('period_records') or 0) == 0 for ev in c['evidence'] if ev.get('value')))):
                        c['likely'] = False
                        c['why_not_likely'] = 'no matching records in the period' if period else 'no matching records'
            finally:
                db.close()
    return {'version': VERSION, 'terms': qterms, 'unmatched_terms': [t for t in qterms if t not in idx.df], 'period': period,
            'streams_searched': len(idx.streams), 'fields_and_values_indexed': sum(len(v) for v in idx.inv.values()),
            'candidates': top[:k + 1]}


def describe_source(c):
    """'wellness_visit_services has 202 records in 2029 with hcpcs_code 'G0438'' (plain words for receipts and abstentions)."""
    period = c.get('period')
    when = ''
    if period:
        when = f' in {period[0][:4]}' if period[0][:4] == period[1][:4] and period[0][5:] == '01-01' and period[1][5:] == '12-31' \
            else f' between {period[0]} and {period[1]}'
    vals = [ev for ev in c.get('evidence') or [] if ev.get('value') is not None and (ev.get('period_records') is None or ev['period_records'] > 0)]
    if vals:
        bits = [f"{ev['field']} {ev['value']!r}" for ev in vals[:2]] + [f"{f} " + '/'.join(repr(v) for v in vs) for f, vs in (c.get('codes') or {}).items()
                                                              if not any(ev['field'] == f for ev in vals[:2])][:2]
        n = vals[0].get('period_records') if vals[0].get('period_records') is not None else vals[0].get('records')
        return f"{c['stream']} has {n} records{when} with " + ' / '.join(bits)
    n = c.get('period_records') if c.get('period_records') is not None else c.get('records')
    return f"{c['stream']} has {n} records{when} matching {', '.join(c.get('matched_terms') or [])}"


# ---------------------------------------------------------------------------------------------------- raw-catalog slice
RAW_SLICE_BYTES = 1800


def raw_catalog_slice(question, profile, path=None, shown=(), k=4, budget=RAW_SLICE_BYTES, encoder=None):
    """-> (text, receipt): the received streams the catalog probe ranks for the question (not already shown in full) with
    the fields and exact values that matched, for the hosted SQL writer; bounded by `budget` characters. Raw streams are
    queried through the read-only `record` table (json_extract on payload), under the semantic SQL guard."""
    if os.environ.get('AGERE_RAW_CATALOG', 'on') == 'off': return '', None
    try:
        # profile counts only: per-period warehouse counts cost seconds on large streams and the writer runs on every hosted ask
        found = probe(question, profile, path=None, k=k + len(shown), encoder=encoder)
    except Exception as exc:  # noqa: BLE001 - retrieval is an aid, never a blocker
        return '', {'error': f'{type(exc).__name__}: {exc}'[:200]}
    picks = [c for c in found['candidates'] if c['stream'] not in set(shown) and (c['likely'] or c['item_coverage'] >= 0.5)][:k]
    if not picks: return '', {'streams': [], 'version': VERSION}
    head = ('\n\nRECEIVED STREAMS MATCHING THE QUESTION (raw records, full catalog; query them as record WHERE stream = \'<stream>\' '
            "with json_extract(payload,'$.<field>'); values are exact spellings)")
    lines, used = [], []
    for c in picks:
        spec = profile['streams'][c['stream']]
        vals = '; '.join(f"{ev['field']} = {ev['value']!r} ({ev.get('period_records', ev['records'])} rows"
                         + (' in period' if ev.get('period_records') is not None and found['period'] else '') + ')'
                         for ev in [e for e in c['evidence'] if e.get('value') is not None][:2])
        fields = [f for f, fs in sorted(spec['fields'].items()) if fs.get('populated') and f not in _SKIP_FIELDS
                  and not f.endswith(('_time', '_record_id', '_record_ids'))][:20]
        line = (f"- {c['stream']} ({spec.get('records')} records"
                + (f", {c['period_records']} in {found['period'][0][:4]}" if c.get('period_records') is not None and found['period'] else '')
                + f"): {vals + '; ' if vals else ''}fields: {', '.join(fields)}")
        if len(head) + sum(map(len, lines)) + len(line) + 1 > budget:
            line = line[:max(0, budget - len(head) - sum(map(len, lines)) - 4)].rstrip(', ') + '...'
            if len(line) < 40: break
        lines.append(line); used.append(c['stream'])
    return head + '\n' + '\n'.join(lines), {'streams': used, 'version': VERSION, 'period': found['period']}


# ---------------------------------------------------------------------------------------------------- sources an answer read
# Semantic tables and the received streams they are built from (semantic_layer.py). A table's appearance in the SQL counts
# as reading these streams; columns that carry another stream's facts are listed separately.
TABLE_STREAMS = {
    'visit': ('encounters',), 'encounter': ('encounters',), 'encounter_diagnosis': ('encounters',), 'adt_visit': ('adt', 'adt_messages'),
    'stay': ('encounters', 'adt'), 'utilization_event': ('encounters', 'adt'),
    'claim': ('medical_claims', 'medical_claim_submissions', 'payer_cash_allocations', 'provider_remittance_advice'),
    'claim_adjustment': ('medical_claims', 'provider_remittance_advice'), 'claim_line': ('medical_claim_lines',),
    'payer_claim': ('payer_medical_claims',), 'payer_claim_line': ('payer_medical_claim_lines',), 'payer_claim_diagnosis': ('payer_medical_claims',),
    'payer_pharmacy_claim': ('payer_pharmacy_claims',),
    'measurement': ('labs', 'vitals', 'mood_questionnaires'), 'measurement_latest': ('labs', 'vitals', 'mood_questionnaires'),
    'condition': ('conditions', 'condition_assessments'), 'problem_list_entry': ('conditions',),
    'appointment': ('appointments',), 'immunization': ('immunizations',), 'enrollment_span': ('enrollment_spans',),
    'member_month': ('enrollment_spans', 'coverage'), 'patient': ('coverage', 'demographic_updates'),
    'medication': ('pharmacy_dispenses', 'medication_orders', 'medication_lists', 'pharmacy_claims', 'pharmacy_claim_transactions',
                   'medication_administrations', 'reported_medication_entries', 'pharmacy_history'),
    'prescription': ('medication_orders',), 'prescription_fill': ('pharmacy_dispenses',), 'medication_list_entry': ('medication_lists',),
    'social_need': ('social_screens', 'financial_screens'), 'smoking_status': ('social_history',), 'sdoh_vendor': ('sdoh_vendor_attributes',),
    'note_fact': ('notes', 'imported_documents', 'hie_documents', 'scanned_documents'), 'clinical_note': ('notes',),
    'hie_document': ('hie_documents',), 'scanned_document': ('scanned_documents',), 'scanned_document_text': ('scanned_documents',),
    'wellness_visit': ('wellness_visit_services',), 'allergy_entry': ('allergies',),
}
COLUMN_STREAMS = {'awv': ('wellness_visit_services',), 'wellness_visit_type': ('wellness_visit_services',)}


def used_sources(graph, answer, streams):
    """Received streams the answer's plan read: named in the plan (cohort specifications, stream literals) or behind a semantic
    table / column its SQL reads."""
    plan_text = json.dumps(graph or {}, default=str)
    sql = ' '.join(str(x) for x in [(answer or {}).get('query')] + [n.get('parameters', {}).get('query') for n in (graph or {}).get('nodes') or []
                                                                     if isinstance(n, dict) and isinstance(n.get('parameters'), dict)] if x)
    text = plan_text + ' ' + sql
    used = {s for s in streams if re.search(r'(?<![a-z0-9_])' + re.escape(s) + r'(?![a-z0-9_])', text)}
    for t, ss in TABLE_STREAMS.items():
        if re.search(r'\b(?:from|join)\s+' + re.escape(t) + r'\b', sql, re.I): used |= set(ss)
    code = re.sub(r"'(?:[^']|'')*'", "''", sql)  # column names, not string literals
    for col, ss in COLUMN_STREAMS.items():
        if re.search(r'(?<![a-z0-9_])' + re.escape(col) + r'(?![a-z0-9_])', code): used |= set(ss)
    return used


# ---------------------------------------------------------------------------------------------------- hosted re-plan
def _models(stack, ledger):
    from .nl2sql import HostedModel, Decider
    from ..gateway import BudgetLedger
    primary = HostedModel(ledger, stack.receipts, model=os.environ.get('AGERE_PRIMARY_MODEL', 'claude-haiku-5-5'))
    second = HostedModel(BudgetLedger(ledger.path), stack.receipts, model=os.environ.get('AGERE_SECOND_MODEL', 'claude-sonnet-5-5'))
    judge = HostedModel(ledger, stack.receipts, model=os.environ.get('AGERE_JUDGE_MODEL', 'claude-opus-5-5'))
    decider = Decider(ledger, stack.receipts) if os.environ.get('AGERE_DECIDER_JUDGE', 'on') != 'off' else None
    return primary, second, judge, decider


def hosted_sql(stack, question, ledger, *, extra_streams=(), feedback='', independent=None, stage='answer_guard_replan', profile=None):
    """One run of the verified hosted SQL ladder with `extra_streams` forced into the writer's schema slice and `feedback`
    appended to the question. -> (graph, receipt, result). Raises ValueError (MissingData included) when it declines."""
    from . import nl2sql, verification_v2, oracles, metric_router, source_profile
    from .analysis_graph import validate
    as_of = stack.retriever.meta['snapshot']['as_of']
    profile = profile if profile is not None else source_profile.load(stack.path)
    primary, second, judge, decider = _models(stack, ledger)
    oracle = None
    if independent is None and os.environ.get('AGERE_VERIFICATION_V2', 'on') != 'off':
        independent = verification_v2.default_sources(stack.path, as_of, profile)
    if os.environ.get('AGERE_VERIFICATION_V2', 'on') != 'off':
        oracle = verification_v2.oracle_for(stack.path, question, as_of,
                                            oracles.readonly_executor(stack.path) if os.environ.get('AGERE_PARTITION_ORACLE', 'on') != 'off' else None)
    cost = lambda: sum(getattr(m, 'cost', 0.0) or 0.0 for m in (primary, second, judge, decider) if m is not None)
    try:
        params, ladder_result, ladder = nl2sql._verified_answer(stack.path, question, as_of, primary, second, judge, decider, tuple(extra_streams),
                                                     independent, oracle, feedback=feedback)
    except ValueError as exc:
        exc.guard_cost = cost()
        exc.guard_calls = sum(1 for a in ((getattr(exc, 'receipt', None) or {}).get('attempts') or []) if not a.get('cache_hit'))
        raise
    if not params or not params.get('query'): raise ValueError('the ladder chose a candidate without a query')
    graph = validate({'interpretation': question, 'coverage': 'supported_observation', 'unmet_requirements': [],
                      'nodes': [{'id': 'answer', 'operation': 'sql', 'inputs': [], 'parameters': params,
                                 'purpose': 'Hosted small-model query over the semantic layer and raw catalog'}]}, as_of)
    try:
        acceptance = metric_router.acceptance(question, params.get('query'), ladder, as_of, source=oracles.readonly_executor(stack.path))
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        acceptance = {'status': 'low_confidence', 'basis': f'acceptance check failed: {exc}'[:300]}
    paid = sum(1 for a in ladder.get('attempts') or [] if not a.get('cache_hit'))
    receipt = {'route': 'hosted_verified_sql', 'model': primary.model, 'new_paid_api_calls': paid, 'frontier_called': False,
               'hosted_sql': ladder, 'acceptance': acceptance, 'guard_stage': stage,
               'steps': [{'stage': stage, 'receipt': {'cost_estimate': {'estimated_usd': cost()}}}]}
    from . import verify
    ladder['capture'] = {'plan': params, 'signature': json.dumps(verify.signature(ladder_result), default=str)[:4000],
                         'models': [a.get('model') for a in ladder.get('attempts') or []], 'agreement': ladder.get('agreement')}
    result = stack.run_graph(graph, question=question)
    receipt['_cost'] = cost()
    return graph, receipt, result


# ---------------------------------------------------------------------------------------------------- 3. review
def enabled():
    return os.environ.get('AGERE_ANSWER_GUARD', 'on') != 'off'


def policy():
    p = os.environ.get('AGERE_GUARD_POLICY', 'abstain')
    return p if p in ('abstain', 'low') else 'abstain'


def _summary(answer):
    if not isinstance(answer, dict): return None
    return {k: answer.get(k) for k in ('answer_type', 'status', 'count', 'value', 'query')
            if answer.get(k) is not None} | ({'patients': len(answer['patient_ids'])} if isinstance(answer.get('patient_ids'), list) else {}) \
        | ({'rows': len(answer['table'])} if isinstance(answer.get('table'), list) else {})


def _add_step(receipt, stage, usd, calls, **detail):
    receipt.setdefault('steps', []).append({'stage': stage, 'receipt': {'cost_estimate': {'estimated_usd': round(usd or 0.0, 6)}}, **detail})
    receipt['new_paid_api_calls'] = (receipt.get('new_paid_api_calls') or 0) + (calls or 0)


def abstain(answer, reason, names):
    """Abstention naming the unused source; the withheld number stays in answer['withheld'] (policy 'low' keeps it)."""
    from .answer_checks import _add
    out = dict(answer)
    if policy() == 'abstain':
        out['withheld'] = _summary(answer)
        out.update(count=None, value=None, table=None, patient_ids=None, answer_type='unsupported', status='partial')
        out['missing'] = list(out.get('missing') or []) + [reason]
    _add(out, 'QC-CATALOG', 'FM-09', ('Not shipped: ' if policy() == 'abstain' else 'Low confidence: ') + reason, sources=names)
    return out


def review(stack, question, graph, receipt, result, *, ledger=None, frontier=None, profile=None, hosted=None, encoder=None):
    """Guard and cross-check one executed answer. -> (graph, receipt, result, new_paid_calls).
    hosted(stack, question, ledger, extra_streams=..., feedback=..., independent=..., stage=...) -> (graph, receipt, result) replaces
    hosted_sql (tests); frontier is the compose() frontier callback."""
    hosted = hosted or hosted_sql
    if not isinstance(receipt, dict) or not isinstance(result, dict) or not isinstance(result.get('answer'), dict):
        return graph, receipt, result, 0
    encoder = encoder if encoder is not None else getattr(getattr(stack, 'retriever', None), 'encoder', None)
    if profile is None:
        try:
            from .source_profile import load
            profile = load(stack.path)
        except (OSError, KeyError, ValueError, sqlite3.Error):
            profile = None
    calls0 = receipt.get('new_paid_api_calls') or 0
    guard = {'version': VERSION, 'route': receipt.get('route')}
    live = bool(getattr(stack, 'allow_live', False)) and ledger is not None
    found = suspicion(question, result['answer']) if enabled() else []
    if found and profile is not None:
        guard['triggers'] = found
        kinds = {f['kind'] for f in found}
        p = probe(question, profile, path=stack.path, encoder=encoder)
        used = used_sources(graph, result['answer'], profile['streams'])
        for c in p['candidates']: c['used'] = c['stream'] in used
        likely = [c for c in p['candidates'] if c['likely']]
        guard['probe'] = {k: p[k] for k in ('terms', 'unmatched_terms', 'period', 'streams_searched')} | {
            'candidates': [{k: c.get(k) for k in ('stream', 'score', 'coverage', 'likely', 'used', 'matched_terms', 'period_records',
                                                  'why_not_likely')} | {'evidence': c['evidence'][:2]} for c in p['candidates']]}
        guard['used_sources'] = sorted(used)[:40]
        zero_only = kinds <= ZERO_KINDS
        if zero_only and not likely:
            from .answer_checks import _add
            what = ', '.join(p['terms'][:6]) or 'the question terms'
            msg = (f"Catalog check: searched all {p['streams_searched']} received streams (names, fields, values and codes) for {what}"
                   + (f" in {p['period'][0][:4]}" if p['period'] else '') + '; no unread source has matching records, so the zero stands.')
            result = {**result, 'answer': dict(result['answer'])}
            _add(result['answer'], 'QC-CATALOG', 'FM-09', msg, 'info', streams_searched=p['streams_searched'])
            guard['decision'] = 'zero_confirmed_by_catalog'
        elif kinds == {'money_small'} and not likely:
            guard['decision'] = 'no_catalog_evidence'
        else:
            targets = [c for c in likely if not c['used']] or likely
            names = [c['stream'] for c in targets[:3]]
            reason = (('; '.join(describe_source(c) for c in targets[:2]) + " that this answer didn't read") if targets and not targets[0]['used']
                      else ('; '.join(describe_source(c) for c in targets[:2]) + ', yet the answer is ' + found[0]['message'][len('the answer is '):])
                      if targets else f"{found[0]['message']} for this question (route {receipt.get('route')})")
            if zero_only and targets and targets[0]['used']:
                reason = '; '.join(describe_source(c) for c in targets[:2]) + ', but this answer returned nothing from it'
            guard['reason'] = reason
            replanned = None
            if live and os.environ.get('AGERE_GUARD_REPLAN', 'on') != 'off':
                note = ('\n\nNOTE: an earlier route answered "' + ', '.join(f['message'] for f in found) + '". A full-catalog check found: '
                        + ('; '.join(describe_source(c) for c in targets[:3]) if targets else 'no single matching stream')
                        + '. Read the source that holds what the question asks for (raw streams via record WHERE stream = ...).')
                try:
                    g2, r2, res2 = hosted(stack, question, ledger, extra_streams=tuple(names), feedback=note, stage='answer_guard_replan')
                    usd = r2.pop('_cost', None)
                    if usd is None: usd = sum((s.get('receipt') or {}).get('cost_estimate', {}).get('estimated_usd') or 0 for s in r2.get('steps') or [])
                    replanned = (g2, r2, res2, usd)
                    guard['replan'] = {'route': r2.get('route'), 'answer': _summary(res2.get('answer')), 'cost_usd': round(usd, 6),
                                       'paid_calls': r2.get('new_paid_api_calls', 0), 'streams_forced': names}
                except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
                    usd, n = getattr(exc, 'guard_cost', 0.0), getattr(exc, 'guard_calls', 0)
                    guard['replan'] = {'error': str(exc)[:300], 'cost_usd': round(usd or 0.0, 6), 'paid_calls': n, 'streams_forced': names}
                    _add_step(receipt, 'answer_guard_replan', usd, n, error=str(exc)[:200])
            if replanned:
                g2, r2, res2, usd = replanned
                still = suspicion(question, res2.get('answer'))
                read = used_sources(g2, res2.get('answer'), profile['streams'])
                confirms_zero = still and {f['kind'] for f in still} <= ZERO_KINDS and zero_only and any(c in read for c in names)
                if not still or confirms_zero:
                    # the re-planned answer replaces the original; the original route and its spend stay in the receipt
                    prior = {k: v for k, v in receipt.items() if k not in ('steps',)}
                    r2['steps'] = list(receipt.get('steps') or []) + list(r2.get('steps') or [])
                    r2['new_paid_api_calls'] = calls0 + (r2.get('new_paid_api_calls') or 0)
                    r2['replaced_route'] = {'route': receipt.get('route'), 'answer': _summary(result['answer']),
                                            'local_compiler_declined': prior.get('local_compiler_declined')}
                    r2['local_compiler_declined'] = prior.get('local_compiler_declined')
                    guard['decision'] = 'zero_confirmed_by_replan' if confirms_zero and still else 'replanned'
                    guard['original'] = _summary(result['answer'])
                    graph, receipt, result = g2, r2, res2
                    if confirms_zero and still:
                        from .answer_checks import _add
                        result = {**result, 'answer': dict(result['answer'])}
                        _add(result['answer'], 'QC-CATALOG', 'FM-09', f"Catalog check: {reason}; a re-plan that read {', '.join(names)} "
                             'also found none, so the zero stands.', 'info')
                else:
                    _add_step(receipt, 'answer_guard_replan', usd, r2.get('new_paid_api_calls', 0), still=[f['kind'] for f in still])
                    guard['decision'] = 'abstained'
                    result = {**result, 'answer': abstain(result['answer'], reason + '; a re-plan with it exposed '
                                                          + ', '.join(f['message'] for f in still)[:200].replace('the answer is', 'still gave'), names)}
            elif guard.get('decision') is None:
                guard['decision'] = 'abstained'
                why = ' (re-plan unavailable: hosted answering is off)' if not live else ' (the re-plan declined)'
                result = {**result, 'answer': abstain(result['answer'], reason + why, names)}
    elif found:
        guard['triggers'] = found; guard['decision'] = 'no_profile'
    receipt['answer_guard'] = guard
    if guard.get('decision') != 'abstained':
        graph, receipt, result = cross_check(stack, question, graph, receipt, result, ledger=ledger, frontier=frontier, profile=profile,
                                             hosted=hosted, encoder=encoder)
    return graph, receipt, result, (receipt.get('new_paid_api_calls') or 0) - calls0


# ---------------------------------------------------------------------------------------------------- 4. cross-check
COMPARABLE = ('count', 'value', 'patient_set')
HOSTED_ROUTES = {'hosted_verified_sql', 'learned_verified_sql', 'semantic_cache', 'bottled_sql', 'local_model_sql'}
# end-to-end gate per route (gate_policy.path_gates names), for the fallback when gate_policy is absent
_ROUTE_GATE = {'local_compiler': 'local_compiler.answer', 'local_model_rewrite_compiled': 'local_rewrite.answer',
               'learned_verified_sql': 'learned_reuse.answer', 'semantic_cache': 'learned_reuse.answer', 'bottled_sql': 'bottled.answer',
               'metric_router': 'metric_router.answer', 'operator_router': 'operator_router.answer', 'hosted_verified_sql': 'freeform_sql.verified',
               'compact_retrieval_typed_router': 'typed_route.answer', 'frontier_escalation': 'frontier_planner.answer'}


def effective_route(receipt):
    if not isinstance(receipt, dict): return None
    if receipt.get('route') == 'exact_question_cache':
        return (receipt.get('prior_planning_receipt') or {}).get('route') or receipt.get('original_route')
    return receipt.get('route')


def certification(receipt, encoder=None):
    """-> {'certified': bool, 'level', 'reason', 'source'}: gate_policy.assess when present (main after d195b42), else every
    route's end-to-end gate read from calibrated_thresholds (certified only with a calibrated record)."""
    try:
        from . import gate_policy
    except ImportError:
        gate_policy = None
    if gate_policy is not None and hasattr(gate_policy, 'assess'):
        try:
            v = gate_policy.assess(receipt, encoder)
            return {'certified': v['level'] == 'verified', 'level': v['level'], 'reason': v['reason'], 'source': 'gate_policy'}
        except Exception as exc:  # noqa: BLE001 - an assessment failure is not a certificate
            return {'certified': False, 'level': 'unverified', 'reason': f'gate assessment failed: {exc}'[:200], 'source': 'gate_policy'}
    gate = _ROUTE_GATE.get(effective_route(receipt))
    if gate is None: return {'certified': False, 'level': 'unverified', 'reason': 'no gate defined for this route', 'source': 'fallback'}
    from . import calibrated_thresholds as CT
    rec = CT.get(gate, None)
    acc = (receipt.get('acceptance') or {}).get('status') if effective_route(receipt) == 'hosted_verified_sql' else 'verified'
    ok = bool(rec.get('calibrated')) and acc == 'verified'
    return {'certified': ok, 'level': 'verified' if ok else 'unverified', 'reason': f"{gate}: {rec.get('reason') or 'calibrated'}",
            'source': 'fallback'}


def cross_check_enabled():
    return os.environ.get('AGERE_CROSS_CHECK', 'on') != 'off'


def as_result(answer):
    t = answer.get('answer_type')
    if t == 'count': return {'value': answer.get('count')}
    if t == 'value': return {'value': answer.get('value')}
    if t == 'patient_set': return {'patient_ids': list(answer.get('patient_ids') or [])}
    rows = answer.get('table') or []
    return {'rows': rows, 'columns': list(rows[0]) if rows and isinstance(rows[0], dict) else []}


def _same(a, b):
    from . import verification_v2 as v2
    try:
        return v2.same_answer(as_result(a), as_result(b))
    except Exception:  # noqa: BLE001
        return False


def _independent(stack, question, receipt, answer, *, ledger, profile, hosted, live):
    """One independent path -> (label, graph, receipt-or-None, result, usd, paid_calls) or (None, reason)."""
    route = effective_route(receipt)
    as_of = stack.retriever.meta['snapshot']['as_of']
    if route in HOSTED_ROUTES:
        ver = ((receipt.get('hosted_sql') or {}).get('verification') or {}) if route == 'hosted_verified_sql' else {}
        if ver.get('status') == 'accepted' and ver.get('basis') == 'independent':
            return ('verification_v2', None, None, None, 0.0, 0)
        if profile is not None:
            from . import verification_v2 as v2
            from .analysis_graph import validate
            try:
                g, info = v2.relaxed_compile(question, profile, as_of)
                if g is not None:
                    g = validate(g, as_of)
                    return ('local_compiler' + (' (relaxed)' if info.get('relaxed') else ''), g, None, stack.run_graph(g, question=question), 0.0, 0)
            except (ValueError, OSError, KeyError, sqlite3.Error):
                pass
        if live:
            from . import metric_router
            if metric_router.enabled():
                try:
                    routed, info = metric_router.route(question, as_of, ledger, stack.receipts, path=stack.path)
                except (ValueError, RuntimeError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
                    routed, info = None, {'router_error': str(exc)[:200], 'calls': []}
                usd, n = metric_router.cost(info), sum(0 if c.get('cache_hit') else 1 for c in info.get('calls') or [])
                if routed is not None:
                    from .analysis_graph import validate
                    try:
                        g = validate(routed, as_of)
                        return (f"metric_router ({info.get('metric')})", g, None, stack.run_graph(g, question=question), usd, n)
                    except (ValueError, OSError, sqlite3.Error) as exc:
                        return (None, f'metric router plan failed: {exc}'[:200], usd, n)
                return (None, f"no independent path: compiler declined, metric router {info.get('decision') or info.get('router_error')}", usd, n)
        return (None, 'no independent path: compiler declined and hosted answering is off', 0.0, 0)
    if not live: return (None, 'hosted answering is off', 0.0, 0)
    mine = {'label': route or 'route', 'result': as_result(answer), 'definition': answer.get('computed') or answer.get('interpretation')}
    try:
        g, r, res = hosted(stack, question, ledger, independent=[lambda q: mine], stage='cross_check')
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        return (None, f'hosted SQL declined: {exc}'[:200], getattr(exc, 'guard_cost', 0.0), getattr(exc, 'guard_calls', 0))
    usd = r.pop('_cost', None)
    if usd is None: usd = sum((s.get('receipt') or {}).get('cost_estimate', {}).get('estimated_usd') or 0 for s in r.get('steps') or [])
    return ('hosted_verified_sql', g, r, res, usd, r.get('new_paid_api_calls', 0))


def _readings(answer, other, label, route):
    from . import verification_v2 as v2
    def one(a, src, default):
        return {'definition': a.get('computed') or a.get('interpretation'), 'query': a.get('query'), 'returns': a.get('answer_type'),
                'result': v2._preview(as_result(a)), 'sources': [src], 'default': default}
    return {'readings': [one(answer, route, True), one(other, label, False)], 'differs_by': ['answer path'],
            'clarifying_question': None, 'version': VERSION}


def cross_check(stack, question, graph, receipt, result, *, ledger=None, frontier=None, profile=None, hosted=None, encoder=None):
    """One independent path for an answer whose path is not certified; see the module docstring. -> (graph, receipt, result)."""
    hosted = hosted or hosted_sql
    answer = result.get('answer') if isinstance(result, dict) else None
    info = {'version': VERSION}
    receipt.setdefault('answer_guard', {'version': VERSION})['cross_check'] = info
    if not cross_check_enabled(): info['status'] = 'off'; return graph, receipt, result
    if not _asserted(answer) or answer.get('answer_type') not in COMPARABLE:
        info.update(status='skipped', reason='answer shape is not comparable across paths'); return graph, receipt, result
    cert = certification(receipt, encoder)
    info['certification'] = cert
    if cert['certified']:
        info.update(status='skipped', reason='every gate on the path is certified'); return graph, receipt, result
    live = bool(getattr(stack, 'allow_live', False)) and ledger is not None
    out = _independent(stack, question, receipt, answer, ledger=ledger, profile=profile, hosted=hosted, live=live)
    if out[0] is None:
        _, why, usd, n = out
        if n or usd: _add_step(receipt, 'cross_check', usd, n)
        info.update(status='unavailable', reason=why, cost_usd=round(usd or 0.0, 6), paid_calls=n); return graph, receipt, result
    label, g, r, res, usd, n = out
    _add_step(receipt, 'cross_check', usd, n, path=label)
    info.update(path=label, cost_usd=round(usd or 0.0, 6), paid_calls=n)
    if res is None:
        info.update(status='agree', basis='verification v2 already found independent agreement'); return graph, receipt, result
    other = (res or {}).get('answer') or {}
    info['independent_answer'] = _summary(other)
    if _asserted(other) and _same(answer, other):
        info['status'] = 'agree'; return graph, receipt, result
    # Disagreement: the frontier planner breaks the tie when available.
    route = effective_route(receipt)
    if live and frontier is not None and os.environ.get('AGERE_CROSS_CHECK_FRONTIER', 'on') != 'off':
        try:
            from .analysis_graph import catalog
            fg, fr = frontier(catalog(stack.path))
            fres = stack.run_graph(fg, question=question)
            fusd = ((fr or {}).get('cost_estimate') or {}).get('estimated_usd') or 0.0
            _add_step(receipt, 'cross_check_frontier', fusd, 0 if (fr or {}).get('cache_hit') else 1)
            third = (fres or {}).get('answer') or {}
            info['frontier_answer'] = _summary(third)
            if _asserted(third) and _same(answer, third):
                info.update(status='agree_after_tiebreak', basis=f'frontier planner agrees with {route}'); return graph, receipt, result
            if _asserted(third) and _asserted(other) and _same(other, third):
                info.update(status='replaced', basis=f'frontier planner agrees with {label}', original=_summary(answer), original_route=route)
                if r is not None:  # the hosted path's receipt carries the answer now; spend so far is already in receipt['steps']
                    r = {**r, 'steps': list(receipt.get('steps') or []), 'new_paid_api_calls': receipt.get('new_paid_api_calls', 0),
                         'answer_guard': receipt.get('answer_guard'), 'replaced_route': {'route': route, 'answer': _summary(answer)},
                         'local_compiler_declined': receipt.get('local_compiler_declined')}
                    return g, r, res
                receipt['replaced_by_cross_check'] = label
                return g, receipt, res
        except (ValueError, RuntimeError, OSError, KeyError, sqlite3.Error) as exc:
            info['frontier_error'] = str(exc)[:200]
    info.update(status='disagree', reason=f'{route} answered {json.dumps(_summary(answer), default=str)[:160]}; '
                                          f'{label} answered {json.dumps(_summary(other), default=str)[:160]}')
    if _asserted(other):
        from .verification_v2 import attach_readings
        result = {**result, 'answer': attach_readings(answer, _readings(answer, other, label, route))}
    return graph, receipt, result


# ---------------------------------------------------------------------------------------------------- after finalize_answer
def finish(result, receipt):
    """Confidence after the answer contract (metric_router.finalize_answer): an abstention or unresolved disagreement is 'low'
    with the reason; an agreeing cross-check adds confidence_detail.cross_checked (the level itself stays what the gate
    policy says)."""
    if not isinstance(result, dict) or not isinstance(result.get('answer'), dict) or not isinstance(receipt, dict): return result
    guard = receipt.get('answer_guard') or {}
    if not guard: return result
    from .metric_router import set_confidence
    from .answer_checks import _add
    out = dict(result['answer'])
    cc = guard.get('cross_check') or {}
    if guard.get('decision') == 'abstained':
        set_confidence(out, 'low', guard.get('reason'), source='answer_guard', guard=VERSION,
                       **({'withheld': out.get('withheld')} if out.get('withheld') else {}))
    elif guard.get('decision') == 'replanned':
        detail = dict(out.get('confidence_detail') or {})
        detail.update(replanned=True, replan_reason=guard.get('reason'), original_answer=guard.get('original'))
        out['confidence_detail'] = detail
        _add(out, 'QC-CATALOG', 'FM-09', f"Re-planned: the first route ({guard.get('route')}) answered "
             f"{json.dumps(guard.get('original'), default=str)[:120]}; {guard.get('reason')}.", 'info')
    if cc.get('status') in ('agree', 'agree_after_tiebreak', 'replaced'):
        detail = dict(out.get('confidence_detail') or {})
        detail.update(cross_checked=True, cross_check={k: cc.get(k) for k in ('status', 'path', 'basis', 'cost_usd')})
        detail.setdefault('level', out.get('confidence'))
        out['confidence_detail'] = detail
        _add(out, 'QC-CROSSCHECK', 'FM-13', f"Cross-checked: an independent path ({cc.get('path')}) "
             + ('gives the same answer.' if cc['status'] == 'agree' else f"disagreed; {cc.get('basis')}."), 'info')
    elif cc.get('status') == 'disagree':
        set_confidence(out, 'low', 'cross-check disagreement: ' + str(cc.get('reason')), source='cross_check', guard=VERSION)
        _add(out, 'QC-CROSSCHECK', 'FM-13', 'Low confidence: an independent path disagrees (' + str(cc.get('reason'))[:300] + ').')
    return {**result, 'answer': out}
