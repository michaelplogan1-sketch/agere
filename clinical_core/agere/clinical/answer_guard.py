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
   Latency: `start` launches the independent path in a worker thread as soon as the primary plan is known, so it runs while
   the primary executes (the hosted ladder reads the executed primary answer as its free candidate after its own first model
   call). Nothing starts when the check cannot change the decision: switch off, a certified-and-passed path, an unsupported
   plan, or a breakdown / list question on a non-hosted route (a table; it is checked after execution if it turns out
   comparable). An abstention, requires_input or table answer drops a started check. The answer waits for the check at most
   until AGERE_CROSS_CHECK_BUDGET_S (default 45) after the question started; past that it ships with confidence
   'unverified' and the reason 'cross-check timed out' (never upgraded without the check), and the worker finishes in the
   background so its model calls land in the ledger cache for the next ask. An independent path that gives no answer is
   'unavailable', not a disagreement.

5. Declines (`decline_claim`, `_review_decline`): a route that declines because data "is not received / never populated / not
   observed" (local refusal, operator-router grounding failure, hosted MissingData) gets the same full-catalog probe; likely
   sources -> one hosted re-plan with them exposed (an answer replaces the decline), none -> the decline ships with the
   catalog check stated. Requests for user input (contract terms) are left alone.
6. Zero vs. the data it filtered (`zero_contradictions`): a zero whose SQL filtered a value the stream holds, read through
   current_record (latest version only) of a status-history stream or spelled differently, re-plans naming the stored value.
The probe also matches acronyms the catalog holds (Social Vulnerability Index -> svi_*), drug classes through member drug
names, a stream the question names outright, the stream name with values of two fields of one record, and bookkeeping
fields by meaning (corrections -> supersedes_record_id, date precision -> event_date_precision); the raw-catalog slice adds
inbound record links, small coded vocabularies and cross-feed bookkeeping lines.

Every extra call is recorded as a planner step (economical_planner.economics prices it) and in receipt['answer_guard'].
Synthetic data only. AGERE_ANSWER_GUARD=off disables 1-3.
"""
import inspect, json, math, os, re, sqlite3, threading, time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
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
    if len(t) >= 7 and t.endswith('ly'): t = t[:-2]  # 'exactly' -> 'exact', 'monthly' -> 'month' ('family' stays)
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


_TITLE_RUN = re.compile(r"\b[A-Z][a-z]+(?:\s+(?:of\s+|and\s+)?[A-Z][a-z]+){1,3}\b")


def acronyms(text, lower_phrase=False):
    """{acronym: [stemmed component terms]} for multi-word names a source may abbreviate: 'Social Vulnerability Index' ->
    {'svi': ['soci', 'vulnera', 'index']}. Title-case runs of 2-4 words (every contiguous sub-run), or, with lower_phrase,
    the whole text as one phrase. Only acronyms of 3+ letters; the caller keeps those the catalog actually holds."""
    out = {}
    runs = [str(text or '')] if lower_phrase else [m.group(0) for m in _TITLE_RUN.finditer(str(text or ''))]
    for run in runs:
        words = [w for w in re.findall(r'[A-Za-z]+', run) if w.lower() not in ('of', 'and', 'the')]
        for i in range(len(words)):
            for j in range(i + 2, min(len(words), i + 4) + 1):
                part = words[i:j]
                acr = ''.join(w[0] for w in part).lower()
                if len(acr) >= 3: out.setdefault(acr, [t for t in terms(' '.join(part)) if t != acr])
    return out


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
# Bookkeeping fields every feed may carry, and the words a question uses for what they mean. They are skipped as plain
# fields (they name no subject), but a question about corrections or date precision is ABOUT them.
FIELD_ALIASES = {
    'supersedes_record_id': 'correction corrected replacement replaced amendment amended revision revised supersedes superseded',
    'event_date_precision': 'date precision imprecise approximate partial date',
}
FIELD_MEANING = {
    'supersedes_record_id': 'set on a correction: the id of the earlier record of the same stream it replaces',
    'event_date_precision': 'how precise event_date is (minute | day | month | year | unknown)',
}
_DRUG_FIELD = re.compile(r'(?:^|_)(?:drug|medication|med|name|product|ingredient|rx|generic|brand)(?:_|$)')


def drug_class_terms(value):
    """Question words that name the drug class of a medication string ('Semaglutide' -> glp1, glp, agonist): a question says
    'GLP-1' or 'statin', a claim row says 'semaglutide' / 'Ozempic'. Ingredient and brand tables in drug_classes."""
    try:
        from .drug_classes import classify
        classes = classify(value)
    except Exception:  # noqa: BLE001 - an aid to retrieval only
        return []
    out = []
    for c in classes:
        for t in terms(c.replace('_', ' ')):
            out.append(t)
            m = re.fullmatch(r'([a-z]{2,})\d+', t)
            if m: out.append(m.group(1))  # 'glp1' also answers to 'glp' ('GLP-1' tokenises as glp + code glp1)
    return list(dict.fromkeys(out))


class _Index:
    def __init__(self, profile):
        self.inv = defaultdict(list)  # term -> [(stream, kind, field, value, records)]
        self.streams = profile.get('streams') or {}
        for s, spec in self.streams.items():
            for t in terms(s): self.inv[t].append((s, 'name', None, None, spec.get('records') or 0))
            for f, fs in (spec.get('fields') or {}).items():
                if fs.get('populated') and f in FIELD_ALIASES:
                    for t in terms(FIELD_ALIASES[f]): self.inv[t].append((s, 'alias', f, None, fs['populated']))
                if not fs.get('populated') or f in _SKIP_FIELDS or f.endswith(('_id', '_ids')): continue
                for t in terms(f): self.inv[t].append((s, 'field', f, None, fs['populated']))
                drug = bool(_DRUG_FIELD.search(f))
                for src in ('values', 'element_values'):
                    for v, n in (fs.get(src) or {}).items():
                        if not isinstance(v, str) or not v or len(v) > 80: continue
                        kind = 'value' if src == 'values' else 'element'
                        vt = terms(v)
                        for t in vt: self.inv[t].append((s, kind, f, v, n))
                        if drug:
                            for t in drug_class_terms(v):
                                if t not in vt: self.inv[t].append((s, kind, f, v, n))
        self.df = {t: len({e[0] for e in lst}) for t, lst in self.inv.items()}


_INDEX_LOCK = threading.Lock()
_COUNTS = {}  # (path, source_digest, stream, period, field, value, kind) -> period record count (read-only warehouse)


def _index(profile):
    """The catalog's inverted index, built once per process per source digest (the probe and the writer's raw-catalog slice
    run on every guarded / hosted question). A profile without a digest is keyed by identity and kept alive with its index so
    the id cannot be reused."""
    digest = profile.get('source_digest')
    key = ('digest', digest) if isinstance(digest, str) and digest else ('id', id(profile))
    hit = _INDEX.get(key)
    if hit is not None and (key[0] == 'digest' or hit[0] is profile): return hit[1]
    with _INDEX_LOCK:
        hit = _INDEX.get(key)
        if hit is None or (key[0] == 'id' and hit[0] is not profile):
            if len(_INDEX) > 4: _INDEX.clear()
            hit = _INDEX[key] = (profile, _Index(profile))
        return hit[1]


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


def _period_counts(db, cand, period, memo=None):
    """Rows of the stream (and of its best matching values) in the period, from the read-only record table. memo(key, query)
    caches counts per process (the warehouse is read-only for a source digest)."""
    s = cand['stream']
    lo, hi = period if period else ('0000-00-00', '9999-12-31')
    cand['period'] = list(period) if period else None
    count = memo or (lambda key, run: run())
    cand['period_records'] = count((s, lo, hi), lambda: db.execute('SELECT count(*) FROM record WHERE stream = ? AND event_date BETWEEN ? AND ?',
                                                                    (s, lo, hi)).fetchone()[0])
    for ev in cand['evidence'][:3]:
        if ev.get('value') is None: continue
        f = ev['field']
        if not re.fullmatch(r'[A-Za-z0-9_]+', f): continue
        if ev['kind'] == 'element':
            sql = (f"SELECT count(*) FROM record WHERE stream = ? AND event_date BETWEEN ? AND ? AND EXISTS "
                   f"(SELECT 1 FROM json_each(json_extract(payload,'$.{f}')) WHERE value = ?)")
        else:
            sql = f"SELECT count(*) FROM record WHERE stream = ? AND event_date BETWEEN ? AND ? AND json_extract(payload,'$.{f}') = ?"
        ev['period_records'] = count((s, lo, hi, f, ev['kind'], ev['value']), lambda: db.execute(sql, (s, lo, hi, ev['value'])).fetchone()[0])


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


# Dense re-ranking with the second pinned encoder (local_encoders.PinnedSentenceEncoder, UltraBERT-Embed-150M). One document
# per received stream (name, populated non-bookkeeping fields, up to 3 values of each low-cardinality text field - the format
# measured in tools/encoder_bakeoff), embedded once per (encoder identity, source digest) and held in memory; the vectors
# also persist in the encoder's content-addressed cache, so a restart re-reads them instead of re-encoding. A question costs
# one query encoding (with the model's query prompt) and one matrix product over all streams.
DENSE_VERSION = 'catalog-dense/1'
DENSE_POOL = 10     # dense top-N streams join the lexical top 15 as candidates
RRF_K = 60          # reciprocal-rank fusion constant (Cormack et al. 2009 default; not tuned on the evaluation cases)
_DENSE_SKIP = {'available_date', 'available_time', 'encounter_id', 'event_date', 'event_date_precision', 'event_time',
               'identity_link_basis', 'identity_status', 'organization_id', 'patient_id', 'record_id', 'recorded_date',
               'recorded_time', 'signed_time', 'source_patient_id', 'source_system', 'source_table', 'supersedes_record_id', 'synthetic'}
_DENSE = {}
_DENSE_LOCK = threading.Lock()


def catalog_document(stream, spec, max_chars=1200):
    fields = [f for f in sorted(spec.get('fields') or {}) if f not in _DENSE_SKIP]
    vals = []
    for f in fields:
        d = spec['fields'][f] or {}
        if d.get('values') and d.get('distinct', 99) <= 12:
            vals += [str(v).replace('_', ' ') for v in list(d['values'])[:3] if isinstance(v, str) and len(v) <= 60]
    text = f"{stream.replace('_', ' ')}. fields: {', '.join(f.replace('_', ' ') for f in fields)}. values: {', '.join(vals)}"
    return text[:max_chars]


def dense_encoder():
    """The second pinned encoder for catalog retrieval, or None (AGERE_CATALOG_DENSE=off, AGERE_SECOND_ENCODER=off, weights
    absent or refused) - the probe then keeps its lexical ranking and the optional stack-encoder re-rank."""
    if os.environ.get('AGERE_CATALOG_DENSE', 'on') == 'off': return None
    try:
        from ...local_encoders import second_encoder
        return second_encoder()
    except Exception:  # noqa: BLE001 - retrieval aid only
        return None


def _dense_catalog(dense, profile, idx):
    digest = profile.get('source_digest')
    # the stream set is part of the key: two profiles sharing a digest label (fixtures) must not share vectors
    key = (dense.identity, ('digest', digest, tuple(sorted(idx.streams))) if isinstance(digest, str) and digest else ('id', id(profile)))
    hit = _DENSE.get(key)
    if hit is not None and (key[1][0] == 'digest' or hit[0] is profile): return hit[1], hit[2]
    with _DENSE_LOCK:
        hit = _DENSE.get(key)
        if hit is None or (key[1][0] == 'id' and hit[0] is not profile):
            names = sorted(idx.streams)
            matrix = dense.encode_documents([catalog_document(s, idx.streams[s] or {}) for s in names])
            if len(_DENSE) > 4: _DENSE.clear()
            hit = _DENSE[key] = (profile, names, matrix)
        return hit[1], hit[2]


def _dense_scores(dense, question, profile, idx):
    """{stream: cosine} over the full catalog, or {} when the encoder fails (lexical ranking stands)."""
    if dense is None or not idx.streams: return {}
    try:
        names, matrix = _dense_catalog(dense, profile, idx)
        q = dense.encode_queries([question])[0]
        return {s: float(x) for s, x in zip(names, matrix @ q) if s in idx.streams}
    except Exception:  # noqa: BLE001 - the dense stage only re-ranks
        return {}


def probe(question, profile, *, path=None, k=3, encoder=None, used=(), dense='auto'):
    """Search the full received catalog for sources matching the question's key terms.
    Ranking: lexical (inverted index, IDF-weighted, exact code/value bonuses) is the first stage. With the second pinned
    encoder available (dense='auto' -> dense_encoder(); pass None to disable or an encoder to inject), its dense top 10
    join the lexical top 15 and candidates are ordered lexically-'likely' first, then by reciprocal-rank fusion of the
    lexical and dense ranks; 'likely' itself (the guard's re-plan / abstain trigger) stays purely lexical and
    warehouse-checked. Without it, the stack encoder (when given) re-ranks as before.
    -> {'terms', 'unmatched_terms', 'period', 'streams_searched', 'candidates': [{'stream', 'score', 'coverage', 'matched_terms',
       'evidence': [{'field', 'value', 'kind', 'records', 'period_records'?}], 'records', 'period_records'?, 'likely', 'used',
       'dense_similarity'?, 'dense_rank'?, 'fused'?}], 'dense'?: {...}}"""
    idx = _index(profile)
    n = max(len(idx.streams), 1)
    qterms = terms(question)
    # Multi-word names a source abbreviates ('Social Vulnerability Index' -> svi_* fields): an acronym the catalog holds
    # stands for its words, so a stream matching it is credited with them.
    acr = {a: parts for a, parts in acronyms(question).items() if a in idx.df and a not in qterms and parts}
    qterms = qterms + [a for a in acr if a not in qterms]
    known = [t for t in qterms if t in idx.df]
    idf = {t: math.log(1 + n / idx.df[t]) for t in known}
    for parts in acr.values():
        for t in parts: idf.setdefault(t, math.log(1 + n / idx.df[t]) if t in idx.df else math.log(1 + n))
    weighted = [t for t in idf if t not in acr]
    total = sum(idf[t] for t in weighted)
    codes = _code_forms(question)
    per = defaultdict(lambda: {'terms': {}, 'evidence': {}})
    for t in known:
        credit = [t] if t not in acr else acr[t]
        for s, kind, f, v, cnt in idx.inv[t]:
            e = per[s]
            for c in credit:
                e['terms'][c] = max(e['terms'].get(c, 0), {'name': 1.0, 'value': 1.0, 'element': 1.0, 'alias': 1.0, 'field': 0.6}[kind])
            if kind != 'name':
                ev = e['evidence'].setdefault((f, v), {'field': f, 'value': v, 'kind': kind, 'records': cnt, 'terms': set()})
                ev['terms'].update(credit)
            else:
                e.setdefault('name_hits', set()).update(credit)
    distinct_cut = max(3, n * 0.03)
    df = lambda t: idx.df.get(t, 1)
    cands = []
    for s, e in per.items():
        matched = e['terms']
        score = sum(idf[t] * w for t, w in matched.items())
        coverage = sum(idf[t] for t in matched) / total if total else 0.0
        evidence = sorted(e['evidence'].values(), key=lambda x: (-(x['value'] is not None), -sum(idf[t] for t in x['terms']), -x['records']))
        for ev in evidence:  # a single value naming several of the question's terms is strong evidence
            if ev['value'] is not None and len(ev['terms']) >= 2: score += 0.5 * sum(idf[t] for t in ev['terms'])
        # a named code counts when a field / stream name carries it or a stored value repeats; one free-text mention
        # ('PHQ-9 12, sleep poor' once in a chief complaint) is not a source for the code
        code_terms = codes & set(matched)
        code_hit = bool(code_terms) and (bool(code_terms & e.get('name_hits', set())) or any(
            code_terms & ev['terms'] and (ev['value'] is None or ev['records'] >= 3) for ev in evidence))
        exact = [ev for ev in evidence if ev['value'] is not None and re.sub(r'[^a-z0-9]', '', ev['value'].lower()) in codes]
        if exact: score += 2.0 + 0.5 * math.log10(1 + max(ev['records'] for ev in exact))  # a stored value that IS the named code
        best_value = max((ev['records'] for ev in (exact or [ev for ev in evidence if ev['value'] is not None])), default=0)
        score += 0.2 * math.log10(1 + best_value)  # among equal matches, the source holding more of it
        distinctive = [t for t in matched if df(t) <= distinct_cut and matched[t] >= 1.0]
        # one item must carry most of the question: the stream's name, one stored value or field, or the name with values of
        # two different fields of the same record ('appointments' + status 'rescheduled' + reason 'transportation'). Terms
        # scattered over unrelated values of ONE field ('hip_fracture', 'joint_replacement') are not a source for 'hip replacement'.
        stream_terms = set(terms(s))
        name_terms = {t for t in matched if t in stream_terms or t in e.get('name_hits', set())}
        name_match = len(stream_terms) >= 2 and stream_terms <= set(qterms)  # the question names the stream outright
        if name_match: score += 0.5 * sum(idf.get(t, 0) for t in stream_terms)
        items = [name_terms] + [set(ev['terms']) | name_terms for ev in evidence]
        top_ev = sorted(evidence, key=lambda x: -sum(idf[t] for t in x['terms']))[:12]
        for i, a in enumerate(top_ev):
            for b in top_ev[i + 1:]:
                if a['field'] != b['field'] and (a['value'] is not None or b['value'] is not None or name_terms):
                    items.append(set(a['terms']) | set(b['terms']) | name_terms)
        item_cov = max((sum(idf[t] for t in it if t in weighted) / total for it in items if it), default=0.0) if total else 0.0
        likely = code_hit or name_match or (item_cov >= 0.6 and bool(distinctive))
        cands.append({'stream': s, 'score': round(score + (3.0 if code_hit else 0.0), 4), 'coverage': round(coverage, 3),
                      'matched_terms': sorted(matched), 'code_match': code_hit, 'name_match': name_match, 'records': (idx.streams[s] or {}).get('records'),
                      'evidence': [{**{k: v for k, v in ev.items() if k != 'terms'}, 'terms': sorted(ev['terms'])} for ev in evidence[:4]],
                      'codes': _code_fields(idx.streams[s]), 'item_coverage': round(item_cov, 3), 'likely': likely, 'used': s in set(used)})
    cands.sort(key=lambda c: (-c['score'], c['stream']))
    top = cands[:15]
    dense = dense_encoder() if dense == 'auto' else dense
    t_dense = time.perf_counter()
    dsims = _dense_scores(dense, question, profile, idx)
    dense_receipt = None
    if dsims:
        top.sort(key=lambda c: (-c['likely'], -round(c['item_coverage'], 2), -c['score'], c['stream']))
        lrank = {c['stream']: i + 1 for i, c in enumerate(top)}
        dorder = sorted(dsims, key=lambda s: (-dsims[s], s))
        drank = {s: i + 1 for i, s in enumerate(dorder)}
        for s in dorder[:DENSE_POOL]:
            if s in lrank: continue
            top.append({'stream': s, 'score': 0.0, 'coverage': 0.0, 'matched_terms': [], 'code_match': False,
                        'records': (idx.streams[s] or {}).get('records'), 'evidence': [], 'codes': _code_fields(idx.streams[s]),
                        'item_coverage': 0.0, 'likely': False, 'used': s in set(used), 'dense_only': True})
        for c in top:
            s = c['stream']
            c['dense_similarity'] = round(dsims[s], 4); c['dense_rank'] = drank[s]
            c['fused'] = round((1 / (RRF_K + lrank[s]) if s in lrank else 0.0) + 1 / (RRF_K + drank[s]), 6)
        top.sort(key=lambda c: (-c['likely'], -c['fused'], c['stream']))
        dense_receipt = {'version': DENSE_VERSION, 'encoder': dense.identity[:16], 'fusion': f'rrf{RRF_K}',
                         'seconds': round(time.perf_counter() - t_dense, 4)}
    else:
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
            base = (str(path), profile.get('source_digest'))

            def memo(key, run):
                key = base + key
                if key not in _COUNTS:
                    if len(_COUNTS) > 4096: _COUNTS.clear()
                    _COUNTS[key] = run()
                return _COUNTS[key]
            if not (isinstance(base[1], str) and base[1]): memo = None  # no digest: the store may change under the same path
            try:
                for c in top[:k + 1]:
                    try:
                        _period_counts(db, c, period, memo)
                    except sqlite3.Error:
                        continue
                    best = next((ev for ev in c['evidence'] if ev.get('period_records') is not None), None)
                    if c['likely'] and (c['period_records'] == 0 or (best is not None and best['period_records'] == 0
                                                                     and all((ev.get('period_records') or 0) == 0 for ev in c['evidence'] if ev.get('value')))):
                        c['likely'] = False
                        c['why_not_likely'] = 'no matching records in the period' if period else 'no matching records'
            finally:
                db.close()
    out = {'version': VERSION, 'terms': qterms, 'unmatched_terms': [t for t in qterms if t not in idx.df], 'period': period,
           'streams_searched': len(idx.streams), 'fields_and_values_indexed': sum(len(v) for v in idx.inv.values()),
           'candidates': top[:k + 1]}
    if dense_receipt: out['dense'] = dense_receipt
    return out


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
        return f"{c['stream']} has {n} records{when} with " + ' / '.join(bits) + (c.get('contradiction_note') or '')
    n = c.get('period_records') if c.get('period_records') is not None else c.get('records')
    return f"{c['stream']} has {n} records{when} matching {', '.join(c.get('matched_terms') or [])}"


# ---------------------------------------------------------------------------------------------------- zero vs. the data it filtered
_LITERAL = re.compile(r"'((?:[^']|'')*)'")


def _norm_value(v): return re.sub(r'[\s_\-]+', '', str(v).strip().lower())


def zero_contradictions(answer, profile, used, period=None, path=None):
    """A zero / empty answer whose own SQL filters a stream it read on a value the catalog holds, in a way that cannot see it:
      - 'version': the SQL reads current_record (latest version per business id) of a versioned stream, and the value sits
        on that stream's records - status-history feeds (appointments: booked -> rescheduled -> fulfilled) carry a reason or
        status on an EARLIER version, so the latest-version view drops it;
      - 'spelling': the literal differs from a stored value only by case / spaces / underscores ('Transportation' vs
        'transportation').
    -> probe-style candidates (likely, used) naming the stream, field, stored value and its record count. Deterministic, local."""
    sql = str((answer or {}).get('query') or '')
    if not sql or not profile: return []
    code = re.sub(r'--[^\n]*', '', sql)
    literals = {m.replace("''", "'") for m in _LITERAL.findall(code)}
    streams = profile.get('streams') or {}
    literals -= set(streams)
    if not literals: return []
    from .semantic_layer import VERSIONED_STREAMS
    latest_view = bool(re.search(r'\bcurrent_record\b', code, re.I))
    out = []
    for s in sorted(used):
        spec = streams.get(s) or {}
        for f, fs in (spec.get('fields') or {}).items():
            vals = fs.get('values') or {}
            if not vals or f"$.{f}'" not in code.replace('"', "'"): continue
            for lit in literals:
                kind, stored = None, None
                if lit in vals and latest_view and s in VERSIONED_STREAMS:
                    kind, stored = 'version', lit
                elif lit not in vals:
                    stored = next((v for v in vals if isinstance(v, str) and _norm_value(v) == _norm_value(lit)), None)
                    kind = 'spelling' if stored is not None else None
                if not kind: continue
                ev = {'field': f, 'value': stored, 'kind': 'value', 'records': vals[stored], 'terms': []}
                c = {'stream': s, 'score': 99.0, 'coverage': 1.0, 'matched_terms': [], 'code_match': False, 'records': spec.get('records'),
                     'evidence': [ev], 'codes': {}, 'item_coverage': 1.0, 'likely': True, 'used': True, 'contradiction': kind}
                if kind == 'version':
                    c['contradiction_note'] = (f" on its record versions, but current_record keeps only the latest version per "
                                               f"{VERSIONED_STREAMS[s]} and drops values carried by earlier versions (read record WHERE stream = "
                                               f"'{s}' over all versions)")
                else:
                    c['contradiction_note'] = f" (the query filtered on {lit!r}, a spelling no record uses)"
                if path is not None and period:
                    try:
                        db = _connect_ro(path)
                        try: _period_counts(db, c, period)
                        finally: db.close()
                    except (OSError, ValueError, KeyError, sqlite3.Error):
                        pass
                    if ev.get('period_records') == 0: continue
                out.append(c)
    return out


# ---------------------------------------------------------------------------------------------------- declines
# A route that declines because the data "is not received / never populated / not observed" must have looked everywhere:
# the modeled layer and a route's own bindings are a fast path, never the boundary of what was received.
_ABSENT = re.compile(r"not received|never populated|not observed|no received|none (?:is|are) received|not in the (?:received )?data|"
                     r"not disclosed|is not received|are not received|does not exist|no received records|none received|not available in",
                     re.I)
_ABSENT_ISSUES = {'stream_unavailable', 'field_unavailable', 'concept_not_observed', 'value_not_observed', 'evidence_not_observed'}


def catalog_holds(phrase, profile):
    """[(stream, field|None)] whose NAME carries the phrase - every content word as a whole name token (plural-insensitive),
    or its acronym ('social vulnerability index' -> svi_overall_percentile, 'deaths' -> death_records). A refusal that says
    such data "is not received" is wrong for this release. Names only (stream and populated field names), never free-text
    values, and whole tokens only: 'interpreter' is not 'interpreting_clinician_id'."""
    if not profile or not phrase: return []
    idx = _index(profile)
    sing = lambda w: w[:-1] if len(w) > 3 and w.endswith('s') and not w.endswith('ss') else w
    words = {sing(w) for w in re.findall(r'[a-z0-9]+', phrase.lower()) if len(w) > 2 and w not in _STOP}
    short = phrase.strip().lower()
    acr = set(acronyms(phrase, lower_phrase=True)) | ({short} if re.fullmatch(r'[a-z]{3,6}', short) else set())
    tokens = lambda name: {sing(t) for t in name.lower().split('_') if t}
    hits = set()
    for s, spec in idx.streams.items():
        if words and words <= tokens(s): hits.add((s, None))
        for f, fs in (spec.get('fields') or {}).items():
            if not fs.get('populated') or f in _SKIP_FIELDS: continue
            ft = tokens(f)
            if (words and words <= ft) or (acr & ft): hits.add((s, f))
    return sorted(hits, key=lambda x: (x[0], x[1] or ''))


def decline_claim(answer, receipt=None):
    """The data-absence claim of a declined answer ('Patient coverage is never populated.'), or None (an answer, a decline
    for another reason, or a request for user input such as contract terms)."""
    if not isinstance(answer, dict) or answer.get('status') not in ('unsupported', 'grounding_failed'): return None
    if 'requires_input' in str((receipt or {}).get('route') or ''): return None
    issues = [g for g in answer.get('grounding') or [] if isinstance(g, dict) and g.get('severity') == 'error']
    texts = [str(m) for m in answer.get('missing') or []] + [str(g.get('detail') or '') for g in issues]
    hit = next((t for t in texts if _ABSENT.search(t)), None)
    if hit is None and any(g.get('issue') in _ABSENT_ISSUES for g in issues): hit = next((t for t in texts if t), 'data not observed')
    return hit


def _review_decline(stack, question, graph, receipt, result, claim, *, ledger, profile, hosted, encoder, guard, live, calls0):
    """Full-catalog probe for a declined answer. Likely sources -> one hosted re-plan with them exposed (an answer replaces
    the decline); none -> the decline stands with the catalog check stated (QC-CATALOG)."""
    from .answer_checks import _add
    p = probe(question, profile, path=stack.path, encoder=encoder)
    likely = [c for c in p['candidates'] if c['likely']]
    guard['triggers'] = [{'kind': 'declined', 'message': f'the answer declined: {claim}'[:300]}]
    guard['probe'] = {k: p[k] for k in ('terms', 'unmatched_terms', 'period', 'streams_searched')} | {
        'candidates': [{k: c.get(k) for k in ('stream', 'score', 'likely', 'matched_terms', 'period_records')} for c in p['candidates']]}
    what = ', '.join(p['terms'][:6]) or 'the question terms'
    if not likely:
        result = {**result, 'answer': dict(result['answer'])}
        _add(result['answer'], 'QC-CATALOG', 'FM-09', f"Catalog check: searched all {p['streams_searched']} received streams (names, fields, "
             f"values and codes) for {what}; none holds what the question asks for, so the decline stands.", 'info',
             streams_searched=p['streams_searched'])
        guard['decision'] = 'decline_confirmed_by_catalog'
        return graph, receipt, result
    names = [c['stream'] for c in likely[:3]]
    found_text = '; '.join(describe_source(c) for c in likely[:3])
    guard['reason'] = f'{found_text} (an earlier route declined: {claim})'[:600]
    if live and os.environ.get('AGERE_GUARD_REPLAN', 'on') != 'off':
        note = (f'\n\nNOTE: an earlier route declined with "{claim[:300]}". A full-catalog check found received data that may answer '
                f'this: {found_text}. Answer from these sources (raw streams via record WHERE stream = ...; json_extract on payload).')
        try:
            g2, r2, res2 = hosted(stack, question, ledger, extra_streams=tuple(names), feedback=note, stage='answer_guard_decline_replan')
            usd = r2.pop('_cost', None)
            if usd is None: usd = sum((s.get('receipt') or {}).get('cost_estimate', {}).get('estimated_usd') or 0 for s in r2.get('steps') or [])
            ans2 = res2.get('answer') if isinstance(res2, dict) else None
            guard['replan'] = {'route': r2.get('route'), 'answer': _summary(ans2), 'cost_usd': round(usd, 6),
                               'paid_calls': r2.get('new_paid_api_calls', 0), 'streams_forced': names}
            if _asserted(ans2) and not suspicion(question, ans2):
                r2['steps'] = list(receipt.get('steps') or []) + list(r2.get('steps') or [])
                r2['new_paid_api_calls'] = calls0 + (r2.get('new_paid_api_calls') or 0)
                r2['replaced_route'] = {'route': receipt.get('route'), 'answer': _summary(result['answer']), 'declined': claim[:300]}
                r2['local_compiler_declined'] = receipt.get('local_compiler_declined')
                guard['decision'] = 'replanned_after_decline'
                guard['original'] = _summary(result['answer'])
                res2 = {**res2, 'answer': dict(ans2)}
                _add(res2['answer'], 'QC-CATALOG', 'FM-09', f"Catalog check: an earlier route declined ({claim[:200]}); the full catalog holds "
                     f"{found_text}, and this answer reads it.", 'info', sources=names)
                return g2, r2, res2
            _add_step(receipt, 'answer_guard_decline_replan', usd, r2.get('new_paid_api_calls', 0))
        except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
            usd, n = getattr(exc, 'guard_cost', 0.0), getattr(exc, 'guard_calls', 0)
            guard['replan'] = {'error': str(exc)[:300], 'cost_usd': round(usd or 0.0, 6), 'paid_calls': n, 'streams_forced': names}
            _add_step(receipt, 'answer_guard_decline_replan', usd, n, error=str(exc)[:200])
    guard['decision'] = 'decline_contradicted_by_catalog'
    result = {**result, 'answer': dict(result['answer'])}
    _add(result['answer'], 'QC-CATALOG', 'FM-09', f"Catalog check: the decline says {claim[:200]!r}, but the full catalog holds {found_text}; "
         + ('a re-plan reading it gave no answer.' if live else 'hosted answering is off, so it was not re-read.'), 'warning', sources=names)
    return graph, receipt, result


# ---------------------------------------------------------------------------------------------------- raw-catalog slice
RAW_SLICE_BYTES = 1800


DENSE_SLICE_RANK = 2  # a stream the dense stage ranks in its top 2 is shown to the writer even without lexical evidence


def bookkeeping_lines(question, profile):
    """Cross-feed facts for a question about a bookkeeping meaning (corrections, date precision): every stream that populates
    the field, with counts - such a question spans feeds, so one stream's slice line is not enough."""
    idx = _index(profile)
    qt = set(terms(question))
    out = []
    for f, words in FIELD_ALIASES.items():
        if not qt & set(terms(words)): continue
        carried = sorted(((s, (spec.get('fields') or {}).get(f, {}).get('populated') or 0) for s, spec in idx.streams.items()), key=lambda x: -x[1])
        carried = [(s, n) for s, n in carried if n]
        if not carried: continue
        vals = (profile['streams'][carried[0][0]]['fields'][f].get('values') or {}) if f == 'event_date_precision' else {}
        out.append(f"- across feeds: {f} ({FIELD_MEANING[f]}) is populated on {len(carried)} streams: "
                   + ', '.join(f'{s} ({n})' for s, n in carried[:12]) + (' ...' if len(carried) > 12 else '')
                   + ('; count non-null per stream (record table, json_extract(payload,\'$.' + f + '\'))'))
    return out


_INBOUND = {}


def inbound_links(path, profile, stream, sample=300):
    """'behavioral_followup_reports.plan_record_id': streams whose records point AT this stream's records (read from the
    semantic layer's link table on a sample of the stream's record ids; indexed on target_record_id). [] without a store."""
    if path is None: return []
    key = (str(path), profile.get('source_digest'), stream)
    if key in _INBOUND: return _INBOUND[key]
    out = []
    try:
        db = _connect_ro(path)
        try:
            ids = [r[0] for r in db.execute('SELECT record_id FROM record WHERE stream = ? LIMIT ?', (stream, sample))]
            if ids:
                marks = ','.join('?' * len(ids))
                out = [f'{s}.{f}' for s, f in db.execute(f'SELECT DISTINCT stream, field FROM link WHERE target_record_id IN ({marks}) '
                                                         'AND stream <> ? AND field <> \'supersedes_record_id\' ORDER BY 1, 2 LIMIT 6',
                                                         (*ids, stream))]
        finally:
            db.close()
    except (OSError, ValueError, KeyError, sqlite3.Error):
        out = []
    if isinstance(profile.get('source_digest'), str):
        if len(_INBOUND) > 2048: _INBOUND.clear()
        _INBOUND[key] = out
    return out


def raw_catalog_slice(question, profile, path=None, shown=(), k=4, budget=RAW_SLICE_BYTES, encoder=None, dense='auto'):
    """-> (text, receipt): the received streams the catalog probe ranks for the question (not already shown in full) with
    the fields and exact values that matched, for the hosted SQL writer; bounded by `budget` characters. Raw streams are
    queried through the read-only `record` table (json_extract on payload), under the semantic SQL guard."""
    if os.environ.get('AGERE_RAW_CATALOG', 'on') == 'off': return '', None
    t0 = time.perf_counter()
    try:
        # profile counts only: per-period warehouse counts cost seconds on large streams and the writer runs on every hosted ask
        found = probe(question, profile, path=None, k=k + len(shown), encoder=encoder, dense=dense)
    except Exception as exc:  # noqa: BLE001 - retrieval is an aid, never a blocker
        return '', {'error': f'{type(exc).__name__}: {exc}'[:200]}
    picks, alias_pick = [], False
    for c in found['candidates']:
        if c['stream'] in set(shown) or c['stream'] not in profile['streams']: continue
        if c['likely'] or c['item_coverage'] >= 0.5 or (c.get('dense_rank') or 10 ** 6) <= DENSE_SLICE_RANK:
            picks.append(c)
        elif not alias_pick and any(ev.get('kind') == 'alias' for ev in c['evidence']):
            picks.append(c); alias_pick = True  # the best-ranked stream whose bookkeeping field the question is about
    picks = picks[:k]
    extra = {'dense': found['dense']} if found.get('dense') else {}
    across = bookkeeping_lines(question, profile)
    if not picks and not across: return '', {'streams': [], 'version': VERSION, 'seconds': round(time.perf_counter() - t0, 3), **extra}
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
        # meaning the bare field list hides: bookkeeping fields the question is about (corrections, date precision), the
        # record-to-record links a follow-through question needs, and the vocabulary of small coded fields
        aliased = [ev['field'] for ev in c['evidence'] if ev.get('kind') == 'alias']
        meaning = [f"{f} ({FIELD_MEANING.get(f, 'see field name')})" for f in dict.fromkeys(aliased)]
        links = [f for f, fs in sorted(spec['fields'].items()) if fs.get('populated') and f.endswith(('_record_id', '_record_ids'))
                 and f != 'supersedes_record_id'][:6]
        shown_vals = {ev['field'] for ev in c['evidence'] if ev.get('value') is not None}
        vocab = []
        for f, fs in sorted(spec['fields'].items(), key=lambda kv: -(kv[1].get('populated') or 0)):
            v = fs.get('values') or {}
            if f in _SKIP_FIELDS or f in shown_vals or not (2 <= len(v) <= 8) or any(len(str(x)) > 32 for x in v): continue
            vocab.append(f"{f}: {'|'.join(str(x) for x in sorted(v, key=lambda x: -v[x]))}")
            if len(vocab) >= 3: break
        line = (f"- {c['stream']} ({spec.get('records')} records"
                + (f", {c['period_records']} in {found['period'][0][:4]}" if c.get('period_records') is not None and found['period'] else '')
                + f"): {vals + '; ' if vals else ''}fields: {', '.join(fields)}"
                + (f"; {'; '.join(meaning)}" if meaning else '')
                + (f"; links (record_id of another record): {', '.join(links)}" if links else '')
                + (f"; linked from: {', '.join(inbound)}" if (inbound := inbound_links(path, profile, c['stream'])) else '')
                + (f"; values: {'; '.join(vocab)}" if vocab else ''))
        if len(head) + sum(map(len, lines)) + len(line) + 1 > budget:
            line = line[:max(0, budget - len(head) - sum(map(len, lines)) - 4)].rstrip(', ') + '...'
            if len(line) < 40: break
        lines.append(line); used.append(c['stream'])
    for line in across:
        if len(head) + sum(map(len, lines)) + len(line) + 1 > budget + 400: break  # cross-feed facts get a small reserve
        lines.append(line)
    return head + '\n' + '\n'.join(lines), {'streams': used, 'bookkeeping': [l[:60] for l in across], 'version': VERSION, 'period': found['period'],
                                             'seconds': round(time.perf_counter() - t0, 3), **extra}


# ---------------------------------------------------------------------------------------------------- sources an answer read
# Semantic tables and the received streams they are built from (semantic_layer.py). A table's appearance in the SQL counts
# as reading these streams; columns that carry another stream's facts are listed separately.
TABLE_STREAMS = {
    'visit': ('encounters',), 'encounter': ('encounters',), 'encounter_diagnosis': ('encounters',), 'adt_visit': ('adt', 'adt_messages'),
    'stay': ('encounters', 'adt'), 'utilization_event': ('encounters', 'adt'),
    'claim': ('medical_claims', 'medical_claim_submissions', 'payer_cash_allocations', 'provider_remittance_advice', 'payer_medical_claims'),
    'claim_adjustment': ('medical_claims', 'provider_remittance_advice'), 'claim_line': ('medical_claim_lines',),
    'payer_claim': ('payer_medical_claims',), 'payer_claim_line': ('payer_medical_claim_lines',), 'payer_claim_diagnosis': ('payer_medical_claims',),
    'payer_pharmacy_claim': ('payer_pharmacy_claims',),
    'measurement': ('labs', 'vitals', 'mood_questionnaires'), 'measurement_latest': ('labs', 'vitals', 'mood_questionnaires'),
    'condition': ('conditions', 'condition_assessments'), 'problem_list_entry': ('conditions',),
    'appointment': ('appointments',), 'immunization': ('immunizations', 'payer_medical_claim_lines'), 'enrollment_span': ('enrollment_spans',),
    'member_month': ('enrollment_spans', 'coverage'), 'patient': ('coverage', 'demographic_updates'),
    'medication': ('pharmacy_dispenses', 'medication_orders', 'medication_lists', 'pharmacy_claims', 'pharmacy_claim_transactions',
                   'medication_administrations', 'reported_medication_entries', 'pharmacy_history'),
    'prescription': ('medication_orders',), 'prescription_fill': ('pharmacy_dispenses',), 'medication_list_entry': ('medication_lists',),
    'social_need': ('social_screens', 'financial_screens'), 'smoking_status': ('social_history',), 'sdoh_vendor': ('sdoh_vendor_attributes',),
    'note_fact': ('notes', 'imported_documents', 'hie_documents', 'scanned_documents'), 'clinical_note': ('notes',),
    'hie_document': ('hie_documents',), 'scanned_document': ('scanned_documents',), 'scanned_document_text': ('scanned_documents',),
    'wellness_visit': ('wellness_visit_services', 'payer_medical_claim_lines'),
    'cancer_screening': ('cancer_screening_results', 'payer_medical_claim_lines'),
    'claim_adjudication': ('medical_claims', 'payer_medical_claims'),
    'pharmacy_claim_event': ('pharmacy_claim_transactions', 'payer_pharmacy_claims'),
    'returned_fill': ('pharmacy_dispenses', 'payer_pharmacy_claims'), 'allergy_entry': ('allergies',),
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


def review(stack, question, graph, receipt, result, *, ledger=None, frontier=None, profile=None, hosted=None, encoder=None,
           pending=None, deadline=None):
    """Guard and cross-check one executed answer. -> (graph, receipt, result, new_paid_calls).
    hosted(stack, question, ledger, extra_streams=..., feedback=..., independent=..., stage=...) -> (graph, receipt, result) replaces
    hosted_sql (tests); frontier is the compose() frontier callback. pending: the cross-check start() began with the primary
    plan; deadline: the question's cross-check deadline (perf_counter time)."""
    hosted = hosted or hosted_sql
    if not isinstance(receipt, dict) or not isinstance(result, dict) or not isinstance(result.get('answer'), dict):
        if pending is not None: pending.cancel()
        return graph, receipt, result, 0
    t0 = time.perf_counter()
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
    claim = decline_claim(result['answer'], receipt) if enabled() and not found else None
    if claim and profile is not None:
        graph, receipt, result = _review_decline(stack, question, graph, receipt, result, claim, ledger=ledger, profile=profile,
                                                 hosted=hosted, encoder=encoder, guard=guard, live=live, calls0=calls0)
    elif found and profile is not None:
        guard['triggers'] = found
        kinds = {f['kind'] for f in found}
        p = probe(question, profile, path=stack.path, encoder=encoder)
        used = used_sources(graph, result['answer'], profile['streams'])
        for c in p['candidates']: c['used'] = c['stream'] in used
        contra = zero_contradictions(result['answer'], profile, used, p['period'], stack.path) if kinds <= ZERO_KINDS else []
        if contra:
            guard['contradictions'] = [{k: c.get(k) for k in ('stream', 'contradiction', 'evidence')} for c in contra]
            p['candidates'] = contra + [c for c in p['candidates'] if c['stream'] not in {x['stream'] for x in contra}]
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
            targets = contra or [c for c in likely if not c['used']] or likely
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
                confirms_zero = still and {f['kind'] for f in still} <= ZERO_KINDS and zero_only and any(c in read for c in names) \
                    and not (contra and zero_contradictions(res2.get('answer'), profile, read))
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
    t1 = time.perf_counter()
    if guard.get('decision') != 'abstained':
        graph, receipt, result = cross_check(stack, question, graph, receipt, result, ledger=ledger, frontier=frontier, profile=profile,
                                             hosted=hosted, encoder=encoder, pending=pending, deadline=deadline)
    elif pending is not None:
        pending.cancel()
    receipt.setdefault('answer_guard', guard)['timings'] = {'guard_probe_s': round(t1 - t0, 3), 'cross_check_s': round(time.perf_counter() - t1, 3)}
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


def comparable(answer):
    """An asserted count / value / patient set: the only shapes the cross-check can compare (tables, abstentions and
    requires_input answers are never cross-checked)."""
    return _asserted(answer) and answer.get('answer_type') in COMPARABLE


def _mine(answer, route):
    return {'label': route or 'route', 'result': as_result(answer), 'definition': answer.get('computed') or answer.get('interpretation')}


def _independent(stack, question, receipt, primary, *, ledger, profile, hosted, live):
    """One independent path -> (label, graph, receipt-or-None, result, usd, paid_calls) or (None, reason, usd, paid_calls).
    primary() returns the primary answer (it may block until the primary path has executed; the hosted ladder only reads it as
    a free candidate after its own first model call, so the two run at the same time)."""
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
    try:
        g, r, res = hosted(stack, question, ledger, independent=[lambda q: _mine(primary(), route)], stage='cross_check')
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        return (None, f'hosted SQL declined: {exc}'[:200], getattr(exc, 'guard_cost', 0.0), getattr(exc, 'guard_calls', 0))
    usd = r.pop('_cost', None)
    if usd is None: usd = sum((s.get('receipt') or {}).get('cost_estimate', {}).get('estimated_usd') or 0 for s in r.get('steps') or [])
    return ('hosted_verified_sql', g, r, res, usd, r.get('new_paid_api_calls', 0))


# ---- concurrency and the latency budget
# The independent path starts as soon as the primary plan is known (stack_runtime.compose, before the primary executes) and
# runs in a worker thread; the answer waits for it at most until the per-question deadline (AGERE_CROSS_CHECK_BUDGET_S from
# the question's start, default 45 s). A check that is not back by then leaves the answer 'unverified' with the reason
# 'cross-check timed out' (finish); the worker finishes in the background and its model calls land in the ledger cache, so
# the next ask of the same question reuses them.
DEFAULT_BUDGET_S = 45.0
_POOL = None
_POOL_LOCK = threading.Lock()


def budget_s():
    try:
        v = float(os.environ.get('AGERE_CROSS_CHECK_BUDGET_S', DEFAULT_BUDGET_S))
    except ValueError:
        v = DEFAULT_BUDGET_S
    return v if math.isfinite(v) and v >= 0 else DEFAULT_BUDGET_S


def _pool():
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix='agere-cross-check')
        return _POOL


def _own_ledger(ledger):
    """A separate BudgetLedger connection on the same file for a worker thread (a BudgetLedger is never shared
    concurrently); None for anything else (test doubles are used as given)."""
    from ..gateway import BudgetLedger
    return BudgetLedger(ledger.path) if isinstance(ledger, BudgetLedger) else None


class _NotComparable(BaseException):
    """Raised inside a hosted cross-check when the primary answer turned out not comparable (table, abstention, error): it
    is a BaseException so the ladder's free-source wrapper (which absorbs Exception) lets it abort the ladder before the
    second / judge calls."""


class PendingCheck:
    """One independent path running in a worker thread. provide(answer) hands it the primary answer; result() waits for it
    until the deadline."""

    def __init__(self, stack, question, receipt, *, ledger, profile, hosted, deadline):
        self.receipt, self.deadline, self.started = receipt, deadline, time.perf_counter()
        self.route = effective_route(receipt)
        self._answer = Future()
        live = bool(getattr(stack, 'allow_live', False)) and ledger is not None
        self.future = _pool().submit(self._run, stack, question, receipt, ledger, profile, hosted or hosted_sql, live)

    def _primary(self):
        try:  # never wait for a primary answer that would arrive after the deadline anyway
            answer = self._answer.result(timeout=max(0.0, self.deadline - time.perf_counter()) + 5.0)
        except FutureTimeout:
            answer = None
        if answer is None: raise _NotComparable()
        return answer

    def _run(self, stack, question, receipt, ledger, profile, hosted, live):
        own = _own_ledger(ledger)
        if own is not None: ledger = own
        try:
            return _independent(stack, question, receipt, self._primary, ledger=ledger, profile=profile, hosted=hosted, live=live)
        except _NotComparable:
            return (None, 'cancelled: the primary answer is not comparable', 0.0, 0)
        finally:
            if own is not None: own.close()

    def provide(self, answer):
        if not self._answer.done(): self._answer.set_result(dict(answer) if comparable(answer) else None)

    def cancel(self):
        self.provide(None)
        self.future.cancel()

    def result(self):
        """-> the _independent tuple, or None when the deadline passed first."""
        try:
            return self.future.result(timeout=max(0.0, self.deadline - time.perf_counter()))
        except FutureTimeout:
            return None
        except Exception as exc:  # noqa: BLE001 - a failed check never fails the answer; it is reported as unavailable
            return (None, f'cross-check failed: {type(exc).__name__}: {exc}'[:200], 0.0, 0)


# analysis_graph.build_answer_core: a plan ending in one of these engines answers with a table (or unsupported), never a
# count / value / patient set ('vbc', 'aggregate', 'sql' and population nodes can, so they are not listed)
_TABLE_OPERATIONS = {'trajectory', 'calibration', 'scheduling', 'survival', 'briefing', 'pathway', 'discover', 'outreach', 'knowledge',
                     'early_signal', 'profile', 'monitor', 'claims_review', 'risk_adjustment', 'network', 'target_trial', 'line_of_therapy',
                     'trial_feasibility', 'loop_closure', 'diagnostic_delay', 'risk_outcomes', 'contract_strategy', 'market', 'demand',
                     'program_effect', 'explain'}


def _predicted_table(question, route, graph=None):
    """Before execution: the plan itself returns a table (a final SQL node with returns 'table', or an operation that always
    answers with one), or a non-hosted route answers a breakdown / list question. A table is never compared, so a check
    started now would be wasted; the executed answer decides instead (cross_check starts one then if it is comparable)."""
    nodes = (graph or {}).get('nodes') or []
    last = nodes[-1] if nodes and isinstance(nodes[-1], dict) else {}
    if last.get('operation') == 'sql' and (last.get('parameters') or {}).get('returns') == 'table': return True
    if last.get('operation') in _TABLE_OPERATIONS: return True
    return route not in HOSTED_ROUTES and bool(_TABLE_Q.search(question or '')) and not re.search(r'^\s*(?:which|who)\b', question or '', re.I)


def start(stack, question, graph, receipt, *, ledger=None, profile=None, hosted=None, encoder=None, deadline=None):
    """Start the cross-check as soon as the primary plan is known (before it executes). -> PendingCheck or None when the
    answer cannot need one (switch off, certified path, unsupported plan, predicted table); cross_check then decides on the
    executed answer as before."""
    if not cross_check_enabled() or not isinstance(receipt, dict): return None
    if (graph or {}).get('coverage') == 'unsupported' or not (graph or {}).get('nodes') or receipt.get('route') == 'hosted_missing_data':
        return None
    if _predicted_table(question, effective_route(receipt), graph): return None
    if certification(receipt, encoder)['certified']: return None
    return PendingCheck(stack, question, receipt, ledger=ledger, profile=profile, hosted=hosted,
                        deadline=deadline if deadline is not None else time.perf_counter() + budget_s())


def _bounded(fn, deadline):
    """Run fn() in the worker pool; -> (done, value). Never waits past the deadline (and starts nothing once it has passed)."""
    if deadline - time.perf_counter() <= 0: return False, None
    fut = _pool().submit(fn)
    try:
        return True, fut.result(timeout=max(0.0, deadline - time.perf_counter()))
    except FutureTimeout:
        return False, None


def _readings(answer, other, label, route):
    from . import verification_v2 as v2
    def one(a, src, default):
        return {'definition': a.get('computed') or a.get('interpretation'), 'query': a.get('query'), 'returns': a.get('answer_type'),
                'result': v2._preview(as_result(a)), 'sources': [src], 'default': default}
    return {'readings': [one(answer, route, True), one(other, label, False)], 'differs_by': ['answer path'],
            'clarifying_question': None, 'version': VERSION}


def cross_check(stack, question, graph, receipt, result, *, ledger=None, frontier=None, profile=None, hosted=None, encoder=None,
                pending=None, deadline=None):
    """One independent path for an answer whose path is not certified; see the module docstring. -> (graph, receipt, result).
    pending: the PendingCheck started before the primary executed (start); a check started for a different receipt (the guard
    re-planned) is dropped and a new one runs. deadline: perf_counter time after which the answer ships without the check."""
    hosted = hosted or hosted_sql
    answer = result.get('answer') if isinstance(result, dict) else None
    info = {'version': VERSION}
    receipt.setdefault('answer_guard', {'version': VERSION})['cross_check'] = info

    def drop(): pending is not None and pending.cancel()
    if not cross_check_enabled(): drop(); info['status'] = 'off'; return graph, receipt, result
    if not comparable(answer):
        drop(); info.update(status='skipped', reason='answer shape is not comparable across paths'); return graph, receipt, result
    cert = certification(receipt, encoder)
    info['certification'] = cert
    if cert['certified']:
        drop(); info.update(status='skipped', reason='every gate on the path is certified'); return graph, receipt, result
    live = bool(getattr(stack, 'allow_live', False)) and ledger is not None
    if pending is not None and pending.receipt is not receipt:
        info['restarted'] = 'the guard replaced the primary answer'; drop(); pending = None
    if deadline is None: deadline = pending.deadline if pending is not None else time.perf_counter() + budget_s()
    if pending is None:
        pending = PendingCheck(stack, question, receipt, ledger=ledger, profile=profile, hosted=hosted, deadline=deadline)
        info['started'] = 'after execution'
    else:
        info['started'] = 'with the primary plan (concurrent with execution)'
    pending.provide(answer)
    t = time.perf_counter()
    out = pending.result()
    info.update(budget_s=budget_s(), waited_s=round(time.perf_counter() - t, 3))
    if out is None:
        # Never block past the budget and never upgrade without the check: the answer ships as the gate policy rates it,
        # 'unverified' with this reason (finish); the worker completes in the background and its calls stay in the ledger cache.
        info.update(status='timed_out', reason='cross-check timed out', background=True,
                    elapsed_s=round(time.perf_counter() - pending.started, 3))
        return graph, receipt, result
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
    if not _asserted(other):
        # The independent path declined (unsupported / no answer): there is nothing to compare, so it neither confirms nor
        # contradicts the primary answer - no tie-break, no 'low'; the answer keeps its gate-policy confidence.
        info.update(status='unavailable', reason=f"the independent path ({label}) gave no answer "
                                                 f"({other.get('status') or other.get('answer_type') or 'none'})")
        return graph, receipt, result
    if label.endswith('(relaxed)'):
        # The relaxed compile answered a different question (it dropped words it could not read, e.g. 'uncontrolled'):
        # its agreement is a weak confirmation, but its different answer is no evidence against the primary answer and
        # must never replace it (nor become an alternative reading).
        info.update(status='unavailable', reason=f"the independent path ({label}) dropped words of the question "
                                                 f"({', '.join((receipt.get('local_compiler_declined') or {}).get('unexplained') or []) or 'unread words'}); "
                                                 'a different answer from it is not comparable')
        return graph, receipt, result
    # Disagreement: the frontier planner breaks the tie when available.
    route = effective_route(receipt)
    if live and frontier is not None and os.environ.get('AGERE_CROSS_CHECK_FRONTIER', 'on') != 'off':
        try:
            from .analysis_graph import catalog

            def tiebreak():
                # its own ledger connection: on a timeout the request moves on (and closes its ledger) while this finishes
                own = _own_ledger(ledger)
                try:
                    takes = own is not None and 'ledger' in inspect.signature(frontier).parameters
                    fg, fr = frontier(catalog(stack.path), ledger=own) if takes else frontier(catalog(stack.path))
                finally:
                    if own is not None: own.close()
                return fg, fr, stack.run_graph(fg, question=question)
            done, value = _bounded(tiebreak, deadline)
            if not done: raise TimeoutError('frontier tie-break timed out (cross-check budget)')
            fg, fr, fres = value
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
        except (ValueError, RuntimeError, OSError, KeyError, sqlite3.Error) as exc:  # TimeoutError is an OSError
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
    elif cc.get('status') == 'timed_out':
        # Never upgraded without the check: a gate-policy 'low' stays low; anything else ships 'unverified' with the reason.
        prior = dict(out.get('confidence_detail') or {})
        if out.get('confidence') != 'low':
            set_confidence(out, 'unverified', 'cross-check timed out', source='cross_check', guard=VERSION,
                           **{k: prior[k] for k in ('gates', 'rule') if k in prior}, gate_basis=prior.get('basis'))
        _add(out, 'QC-CROSSCHECK', 'FM-13', f"Unverified: the independent cross-check did not finish within the {cc.get('budget_s')} s "
             'budget; the answer is not cross-checked.', 'info')
    elif cc.get('status') == 'disagree':
        set_confidence(out, 'low', 'cross-check disagreement: ' + str(cc.get('reason')), source='cross_check', guard=VERSION)
        _add(out, 'QC-CROSSCHECK', 'FM-13', 'Low confidence: an independent path disagrees (' + str(cc.get('reason'))[:300] + ').')
    return {**result, 'answer': out}
