"""Local model -> governed SQL over the semantic layer, for questions the grammar cannot parse.

The model never explores and never sees raw JSON dumps: it receives a compact,
retrieved slice of the data dictionary (the semantic tables, the few received
streams relevant to the question with their populated fields and observed
values, and the concept ids) and writes one SELECT. The query is validated and
executed by the read-only governor; an execution error gets exactly one repair
attempt with the error message. Runs on the local Qwen3-4B (MLX) by default:
no API tokens. A hosted model can be supplied as the same callable.
"""
from pathlib import Path
import json, math, os, re, time
from collections import Counter

from . import concepts, source_profile, sql_query

VERSION = 'local-nl2sql/1'
MODEL_DIR = Path(__file__).resolve().parents[3] / 'models' / 'qwen3-4b'
TABLES = """patient(patient_id, age_reported, sex_reported ['F','M'], coverage_type ['commercial','medicare','medicaid','uninsured'], coverage_status, pcp_clinician_id, pcp_practice_id)
condition(record_id, patient_id, concept, label, icd10, snomed, event_date, available_date, organization_id)  -- one row per (record, concept)
measurement(record_id, patient_id, concept, name, value, unit, value_canonical, unit_canonical, result_status, event_date, available_date, organization_id)  -- labs and vitals; compare value_canonical
visit(encounter_id, patient_id, type, type_concept, status, event_date, end_date, available_date, organization_id, attending_clinician_id)  -- one row per encounter; count visits here
stay(patient_id, admit_date, discharge_date, type, organization_id, readmitted_30d, days_to_readmission)  -- distinct hospital stays
concept_patient(concept, kind, patient_id, records, first_date, last_date)  -- who has each concept
link(record_id, stream, field, target_record_id)  -- every *_record_id reference between records
record(record_id, patient_id, stream, event_date, recorded_date, available_date, organization_id, payload)  -- every received record; other fields via json_extract(payload,'$.field')"""
RULES = """Write ONE SQLite SELECT that answers the question. Rules:
- Use only the tables/fields shown. Dates are 'YYYY-MM-DD' strings; event_date = when it happened, available_date = when it arrived.
- Patients: count(DISTINCT patient_id). Records/rows: count(*). Visits: use visit (encounter rows repeat per status version).
- Clinical conditions, labs, vitals and encounter types: use the concept columns (they cover ICD-10, SNOMED and label variants).
- Stream fields not in a typed table: json_extract(payload,'$.field') on record WHERE stream='<stream>'. Booleans compare to 1/0.
- Day differences: julianday(a) - julianday(b). Year: substr(date,1,4). Percent: 100.0 * part / whole.
- Lab and vital values: ALWAYS the measurement table (both vitals layouts are merged there); never json_extract on vitals/labs.
- Hospital stays: stay (type is 'inpatient' or 'observation'; filter type when the question says inpatient).
- Record-to-record linkage: the link table (field names like study_record_id). Never join record to itself unless the ON clause
  equates an id or patient_id; never produce a cross join.
- Use field values exactly as listed for the stream (e.g. a status or vaccine value).
- Output only the SQL in a ```sql block, nothing else."""
EXAMPLES = [  # generic semantic-layer patterns; retrieved per question (not benchmark answers)
    ("How many patients with diabetes have an HbA1c above 9?",
     "SELECT count(DISTINCT c.patient_id) FROM condition c JOIN measurement m ON m.patient_id = c.patient_id WHERE c.concept = 'diabetes' AND m.concept = 'hba1c' AND m.value_canonical > 9"),
    ("Which patients with COPD have ever had oxygen saturation below 88?",
     "SELECT DISTINCT c.patient_id FROM condition c JOIN measurement m ON m.patient_id = c.patient_id WHERE c.concept = 'copd' AND m.concept = 'spo2' AND m.value_canonical < 88"),
    ("How many asthma patients have no spirometry result at all?",
     "SELECT count(DISTINCT patient_id) FROM concept_patient WHERE concept = 'asthma' AND patient_id NOT IN (SELECT patient_id FROM record WHERE stream = 'diagnostic_test_reports')"),
    ("How many patients with hypertension but no chronic kidney disease are there?",
     "SELECT count(*) FROM concept_patient h WHERE h.concept = 'hypertension' AND NOT EXISTS (SELECT 1 FROM concept_patient k WHERE k.patient_id = h.patient_id AND k.concept = 'chronic_kidney_disease')"),
    ("Break down 2028 urgent care visits by site.",
     "SELECT organization_id, count(*) AS visit_count FROM visit WHERE type_concept = 'urgent_care_visit' AND substr(event_date,1,4) = '2028' GROUP BY 1 ORDER BY 2 DESC"),
    ("How many specialist visits happened in March 2029?",
     "SELECT count(*) FROM visit WHERE type_concept = 'specialist_visit' AND event_date BETWEEN '2029-03-01' AND '2029-03-31'"),
    ("Average number of telehealth contacts per patient among patients who had any in 2028?",
     "SELECT avg(n) FROM (SELECT patient_id, count(*) AS n FROM visit WHERE type_concept = 'telehealth_contact' AND substr(event_date,1,4) = '2028' GROUP BY patient_id)"),
    ("Office visits by PCP panel in 2029: each assigned PCP and the visit count.",
     "SELECT p.pcp_clinician_id, count(v.encounter_id) AS visit_count FROM patient p JOIN visit v ON v.patient_id = p.patient_id WHERE v.type_concept = 'primary_care_visit' AND substr(v.event_date,1,4) = '2029' AND p.pcp_clinician_id IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"),
    ("How many patients had three or more separate inpatient stays?",
     "SELECT count(*) FROM (SELECT patient_id FROM stay WHERE type = 'inpatient' GROUP BY patient_id HAVING count(*) >= 3)"),
    ("How many observation stays were followed by a readmission within 30 days?",
     "SELECT count(*) FROM stay WHERE type = 'observation' AND readmitted_30d = 1"),
    ("How many patients had a specialist visit within 14 days after an urgent care visit?",
     "SELECT count(DISTINCT a.patient_id) FROM visit a JOIN visit b ON b.patient_id = a.patient_id WHERE a.type_concept = 'urgent_care_visit' AND b.type_concept = 'specialist_visit' AND julianday(b.event_date) - julianday(a.event_date) BETWEEN 1 AND 14"),
    ("How many pharmacy dispenses were returned to stock in 2027?",
     "SELECT count(*) FROM record WHERE stream = 'pharmacy_dispenses' AND json_extract(payload,'$.status') = 'returned_to_stock' AND substr(event_date,1,4) = '2027'"),
    ("Count denied medical claims by service organization for 2028 service dates.",
     "SELECT json_extract(payload,'$.service_organization_id') AS organization_id, count(*) AS claim_count FROM record WHERE stream = 'medical_claims' AND json_extract(payload,'$.status') = 'denied' AND substr(json_extract(payload,'$.service_date'),1,4) = '2028' GROUP BY 1 ORDER BY 2 DESC"),
    ("What was the total billed amount in dollars on 2027 medical claims?",
     "SELECT sum(json_extract(payload,'$.billed_cents')) / 100.0 FROM record WHERE stream = 'medical_claims' AND substr(json_extract(payload,'$.service_date'),1,4) = '2027'"),
    ("How many medical claims from 2028 service dates arrived more than 30 days after the service date?",
     "SELECT count(*) FROM record WHERE stream = 'medical_claims' AND substr(json_extract(payload,'$.service_date'),1,4) = '2028' AND julianday(available_date) - julianday(json_extract(payload,'$.service_date')) > 30"),
    ("On average how many days after the event do lab results arrive?",
     "SELECT avg(julianday(available_date) - julianday(event_date)) FROM measurement WHERE source_table = 'labs' AND event_date IS NOT NULL AND available_date IS NOT NULL"),
    ("How many heart rate readings have no event date?",
     "SELECT count(*) FROM measurement WHERE concept = 'heart_rate' AND event_date IS NULL"),
    ("How many medication orders have no dispense linked to them?",
     "SELECT count(*) FROM record o WHERE o.stream = 'medication_orders' AND NOT EXISTS (SELECT 1 FROM link l WHERE l.target_record_id = o.record_id AND l.stream = 'pharmacy_dispenses')"),
    ("How many referrals never received any status update?",
     "SELECT count(*) FROM record r WHERE r.stream = 'referrals' AND NOT EXISTS (SELECT 1 FROM link l WHERE l.target_record_id = r.record_id AND l.stream = 'referral_status_updates')"),
    ("What share of Medicare members have heart failure?",
     "SELECT 100.0 * count(DISTINCT cp.patient_id) / (SELECT count(*) FROM patient WHERE coverage_type = 'medicare') FROM patient p JOIN concept_patient cp ON cp.patient_id = p.patient_id AND cp.concept = 'heart_failure' WHERE p.coverage_type = 'medicare'"),
    ("What was the cancellation rate for appointments scheduled in 2027?",
     "SELECT 100.0 * sum(json_extract(payload,'$.status') = 'cancelled') / count(*) FROM record WHERE stream = 'appointments' AND substr(json_extract(payload,'$.scheduled_date'),1,4) = '2027'"),
    ("Among patients reporting food difficulty, what percent had an urgent care visit?",
     "SELECT 100.0 * count(DISTINCT v.patient_id) / (SELECT count(DISTINCT patient_id) FROM record WHERE stream = 'social_screens' AND json_extract(payload,'$.food_difficulty_reported') = 1) FROM visit v WHERE v.type_concept = 'urgent_care_visit' AND v.patient_id IN (SELECT patient_id FROM record WHERE stream = 'social_screens' AND json_extract(payload,'$.food_difficulty_reported') = 1)"),
    ("How many diabetics reported trouble paying for care on a social screen?",
     "SELECT count(DISTINCT r.patient_id) FROM record r JOIN concept_patient cp ON cp.patient_id = r.patient_id AND cp.concept = 'diabetes' WHERE r.stream = 'social_screens' AND json_extract(r.payload,'$.difficulty_paying_for_care_reported') = 1"),
    ("How many immunizations of a given vaccine were completed in May 2029?",
     "SELECT count(*) FROM record WHERE stream = 'immunizations' AND json_extract(payload,'$.administration_status') = 'completed' AND event_date BETWEEN '2029-05-01' AND '2029-05-31'"),
    ("Payer-paid dollars on pharmacy claims filled in October 2026?",
     "SELECT sum(json_extract(payload,'$.payer_paid_cents')) / 100.0 FROM record WHERE stream = 'pharmacy_claims' AND event_date BETWEEN '2026-10-01' AND '2026-10-31'"),
    ("Patient payments in 2028 split by payment channel, total cents.",
     "SELECT json_extract(payload,'$.payment_channel') AS payment_channel, sum(json_extract(payload,'$.amount_cents')) AS total_cents FROM record WHERE stream = 'patient_payments' AND substr(event_date,1,4) = '2028' GROUP BY 1"),
    ("How many appointments are scheduled after the snapshot date?",
     "SELECT count(*) FROM record WHERE stream = 'appointments' AND json_extract(payload,'$.scheduled_date') > '2029-08-15'"),
    ("How many imaging reports mention pneumonia?",
     "SELECT count(*) FROM record WHERE stream = 'imaging_reports' AND lower(payload) LIKE '%pneumonia%'"),
]


def _tokens(text):
    return [t[:6] for t in re.findall(r'[a-z0-9]+', text.lower().replace('_', ' ')) if len(t) > 2]


def _stream_docs(profile):
    docs = {}
    for s, spec in profile['streams'].items():
        words = [s.replace('_', ' ')] * 3
        for f, fs in spec['fields'].items():
            if not fs['populated'] or f in source_profile.PLANNER_SKIP: continue
            words.append(f.replace('_', ' '))
            vals = fs.get('values') or {}
            if len(vals) <= 40: words += [str(v) for v in vals]
        docs[s] = Counter(_tokens(' '.join(words)))
    return docs


_DOCS = {}


def retrieve_streams(question, profile, k=4):
    key = id(profile)
    if key not in _DOCS: _DOCS[key] = _stream_docs(profile)
    docs = _DOCS[key]
    n = len(docs)
    df = Counter(t for d in docs.values() for t in d)
    q = Counter(_tokens(question))
    scores = {s: sum(qc * (1 + math.log(1 + d[t])) * math.log(1 + n / (1 + df[t])) for t, qc in q.items() if t in d) for s, d in docs.items()}
    words = set(re.findall(r'[a-z0-9][a-z0-9-]+', question.lower()))
    for s_name, spec in profile['streams'].items():  # a literal observed value named in the question pins its stream
        for f, fs in spec['fields'].items():
            for v in (fs.get('values') or {}):
                if len(v) >= 3 and v.lower() in words: scores[s_name] = scores.get(s_name, 0) + 25
    return [s for s, v in sorted(scores.items(), key=lambda kv: -kv[1]) if v > 0][:k]


def schema_for(question, profile):
    streams = retrieve_streams(question, profile)
    lines = []
    for s in streams:
        fields = []
        for f, fs in sorted(profile['streams'][s]['fields'].items()):
            if not fs['populated'] or f in source_profile.PLANNER_SKIP or f.endswith(('_time',)): continue
            vals = fs.get('values') or {}
            if fs['types'].get('true') or fs['types'].get('false'): fields.append(f'{f} (boolean)')
            elif vals and len(vals) <= 12 and max(map(len, vals)) <= 40: fields.append(f"{f} {sorted(vals, key=lambda v: -vals[v])[:12]}")
            elif 'numeric_range' in fs: fields.append(f"{f} (number {fs['numeric_range'][0]}..{fs['numeric_range'][1]})")
            else: fields.append(f)
        lines.append(f"stream '{s}' ({profile['streams'][s]['records']} records): " + ', '.join(fields))
    cat = concepts.catalog()
    concept_line = ('concept ids - conditions: ' + ', '.join(cat['conditions']) + '; measurements (value_canonical unit): ' +
                    ', '.join(f"{k} [{concepts.MEASUREMENTS[k]['unit']}]" for k in cat['measurements']) +
                    '; encounter type_concept: ' + ', '.join(cat['encounters']))
    return streams, f"TABLES\n{TABLES}\n\nRELEVANT RECORD STREAMS\n" + '\n'.join(lines) + f"\n\n{concept_line}"


def returns_for(question, columns, rows=None):
    low = question.lower()
    if rows is not None and len(columns) == 1 and len(rows) == 1:  # scalar result: count or value, never a table
        v = next(iter(rows[0].values()))
        return 'count' if isinstance(v, int) and re.search(r'\bhow many\b|\bcount\b|\bnumber of\b', low) else 'value'
    if len(columns) >= 2 or re.search(r'\b(?:by|break ?down|split|for each|per (?:site|practice|pcp|status|channel|type)|table|columns)\b', low):
        return 'table'
    if re.search(r'\b(?:which|list|who)\b', low) and columns and columns[0] == 'patient_id': return 'patient_set'
    if re.search(r'\bhow many\b|\bcount\b|\bnumber of\b', low): return 'count'
    return 'value'


class LocalModel:
    """Qwen3-4B on MLX, loaded once per process. Deterministic decoding, thinking disabled."""
    _instance = None

    def __init__(self, path=MODEL_DIR):
        os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
        from mlx_lm import load
        start = time.perf_counter()
        self.model, self.tokenizer = load(str(path), tokenizer_config={'local_files_only': True})
        self.load_seconds = time.perf_counter() - start
        self.identity = f'qwen3-4b-mlx@{path.name}'

    @classmethod
    def get(cls):
        if cls._instance is None: cls._instance = cls()
        return cls._instance

    def __call__(self, system, user, max_tokens=400, temperature=0.0, seed=None):
        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler
        prompt = self.tokenizer.apply_chat_template([{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                                                    tokenize=False, add_generation_prompt=True, enable_thinking=False)
        start = time.perf_counter()
        if seed is not None:
            import mlx.core as mx
            mx.random.seed(seed)
        text = generate(self.model, self.tokenizer, prompt=prompt, max_tokens=max_tokens, sampler=make_sampler(temp=temperature), verbose=False)
        return text, {'seconds': round(time.perf_counter() - start, 2), 'model': self.identity, 'api_tokens': 0}


class HostedModel:
    """Cheapest capable hosted model as the next rung (default Claude Haiku 5.5): one call, schema in a
    prompt-cached block, low effort, no exploration. Uses the provider ledger for accounting and dedupe."""
    single = True

    def __init__(self, ledger, out, model='claude-haiku-5-5'):
        self.ledger, self.out, self.model = ledger, out, model
        self.identity = model
        self.cost = 0.0

    def __call__(self, system, user, max_tokens=700, temperature=0.0, seed=None):
        from .context_student import send
        schema, _, rest = user.partition('\n\nEXAMPLES\n')
        body = {'model': self.model, 'max_tokens': max_tokens, 'system': system, 'output_config': {'effort': 'low'},
                'messages': [{'role': 'user', 'content': [
                    {'type': 'text', 'text': schema, 'cache_control': {'type': 'ephemeral'}},
                    {'type': 'text', 'text': 'EXAMPLES\n' + rest}]}]}
        start = time.perf_counter()
        text, receipt = send(body, 'claude', self.ledger, self.out,
                             parse=lambda raw: ''.join(b.get('text', '') for b in raw.get('content', []) if b.get('type') == 'text'))
        usd = ((receipt.get('cost_estimate') or {}).get('estimated_usd') or 0.0) if not receipt.get('cache_hit') else 0.0
        self.cost += usd
        return text, {'seconds': round(time.perf_counter() - start, 2), 'model': self.model, 'api_usd': usd,
                      'usage': receipt.get('usage'), 'cache_hit': bool(receipt.get('cache_hit'))}


CANONICAL = """Rewrite the question into ONE canonical question Agere's compiler understands. Use only these forms
(<...> = exact names from the vocabulary below; keep every number, year and qualifier from the original; add nothing):
- How many patients have <condition> [and <condition>] [but no <condition>]?
- How many patients have <condition> and any <measurement> (over|under|at least|at most) <N>?
- Which patients have <condition> and any <measurement> over <N>?
- How many patients aged <N> or older | under <N> | female | male | with <medicare|medicaid|commercial|uninsured> coverage have <condition>?
- How many patients have any social screen reporting <food difficulty|transport difficulty|difficulty paying for care> as true [and have <condition>]?
- How many patients have any financial screen reporting difficulty paying medical bills [and have <condition>]?
- How many <emergency department visits|inpatient admissions|urgent care visits|primary care visits> in <YYYY>? / ... by <pcp|practice|payer>?
- What share of patients with <medicare|medicaid|commercial> coverage have <condition>?
- What is the average latest <measurement> among <condition> patients by <practice|pcp|payer>?
- How many <stream> records have <field> <value> [in <YYYY>]?
- Count <stream> records [in <YYYY>] by <field>.
- What is the sum of <field> across <stream> records with <field> <value> [in <YYYY>]?
- How many <stream> records lack any <stream> record linked by <field>?
- How many <stream> records arrived more than <N> days after their source event date?
- Why were <condition> patients readmitted within 30 days? / What drives <encounter or condition>? / Where is <condition> concentrated?
- What is our allowed PMPM by payer? / ED visits per 1,000 by PCP / A1c poor control rate by practice / member months in <YYYY>
Keep which date is meant (e.g. service_date, scheduled_date). If the question names output columns, end with
"Return a table with <col1> and <col2>." using those exact names.
If no form fits without changing the meaning, output exactly: NO_CANONICAL_FORM
Output only the rewritten question on one line.

Examples:
Q: quick one - roughly how many of our folks with htn ever had a systolic over 180?
A: How many patients have hypertension and any systolic blood pressure over 180?
Q: pull the list of copd people whose oxygen sat dropped below 88 at any point
A: Which patients have copd and any oxygen saturation under 88?
Q: what fraction of our medicaid population carries a depression dx?
A: What share of patients with medicaid coverage have depression?
Q: give me urgent care volume for 2028 by site please
A: How many urgent care visits in 2028? by site
Q: total patient payments in 2027 broken down by payment channel, columns payment_channel and total_cents
A: What is the sum of amount_cents across patient_payments records in 2027 by payment_channel? Return a table with payment_channel and total_cents.
Q: how many lab orders came in during March 2029?
A: How many laboratory_orders records in March 2029?
Q: any medication orders that never got a dispense tied to them? how many
A: How many medication_orders records lack any pharmacy_dispenses record linked by medication_order_record_id?
Q: kids with asthma who said transportation is hard for them on a screen - count?
A: How many patients under 18 have asthma and any social screen reporting transport difficulty as true?
Q: did our ER usage go up from 2028 to 2029 and why
A: Why did ED visits go up from 2028 to 2029?
Q: is anyone's kidney function tanking? CKD folks specifically
A: Which CKD patients have a declining eGFR?
Q: what's the causal effect of our outreach program on admissions?
A: NO_CANONICAL_FORM"""


def canonicalize(path, question, as_of, model=None):
    """Local model rewrites the question into compiler phrasing; returns the rewrite or None."""
    model = model or LocalModel.get()
    profile = source_profile.load(path)
    _, schema = schema_for(question, profile)
    text, stats = model(CANONICAL, f'{schema}\n\nQUESTION: {question}', max_tokens=120)
    rewrite = text.strip().splitlines()[0].strip().strip('"') if text.strip() else ''
    if not rewrite or 'NO_CANONICAL_FORM' in rewrite: return None, stats
    if re.search(r'[<>\[\]{}]', rewrite): return None, {**stats, 'rewrite': rewrite, 'drift': ['template syntax copied']}
    drift = meaning_drift(question, rewrite, as_of)
    if drift: return None, {**stats, 'rewrite': rewrite, 'drift': drift}
    return rewrite, stats


MEANING_CLASSES = {  # each class present in the question must be present in the rewrite
    'negation': r'\b(?:no|not|never|without|lacks?|lacking|missing|none|neither)\b',
    'patients': r'\b(?:patients?|people|persons?|members?(?! ids?\b)|kids|children|seniors|women|men|diabetics|hypertensives)\b',
    'average': r'\b(?:average|mean|median)\b', 'percent': r'\b(?:percent|percentage|share|proportion|fraction|rate)\b|%',
    'sum': r'\b(?:sum|total|dollars|amount|paid|pay)\b', 'grouping': r'\b(?:by|per|for each|split|break(?:s)? ?down|each)\b',
    'after': r'\b(?:after|later|within|before|since)\b', 'undated': r'\b(?:no|missing|unknown|without) (?:source )?event dates?\b|\bundated\b',
    'dollars': r'\bdollars?\b|\$',
    'at least': r'\bor (?:higher|above|more|greater)\b|\bat least\b|\bno (?:less|lower|fewer) than\b|>=|≥',
    'at most': r'\bor (?:lower|below|less|fewer)\b|\bat most\b|\bno (?:more|higher|greater) than\b|<=|≤',
    'granularity': r'\b(?:monthly|quarterly|weekly|daily|by (?:month|quarter|week|day)|per (?:month|quarter|week)|each (?:month|quarter|week))\b',
    'partial period': r'\b(?:first|second|1st|2nd) half\b|\bq[1-4]\b|\bquarter\b|\bh[12]\b',
    # a cutoff other than the snapshot itself (the snapshot date is blanked out before this check)
    'cutoff': r'\b(?:as of|received by|arrived by|available by)\s+(?:the\s+)?(?:\d|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|new year|end of|year[- ]end|close of)',
}


def meaning_drift(question, rewrite, as_of):
    import calendar
    spoken = calendar.month_name[int(as_of[5:7])].lower()[:3] + r'[a-z]*\.? ' + str(int(as_of[8:10])) + r'(?:st|nd|rd|th)?,? ' + as_of[:4]
    q = re.sub(spoken, ' ', re.sub(as_of, ' ', question.lower())); w = rewrite.lower()
    nums = lambda t: set(re.findall(r'\d+(?:\.\d+)?', t))
    drift = sorted(nums(q) - nums(w) - {str(int(as_of[8:10])), as_of[:4]})
    months = re.findall(r'\b(jan(?:uary)?|feb(?:ruary)?|march|april|june|july|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b', q)
    months = [m for m in months if not re.search(m[:3] + r'[a-z]* 15', q)]  # snapshot-date mentions are not constraints
    drift += [m for m in months if m[:3] not in w]
    drift += [k for k, rx in MEANING_CLASSES.items() if re.search(rx, q) and not re.search(rx, w)]
    drift += [x for x in set(re.findall(r'\b[a-z]+_[a-z_]+\b', q)) if x not in w]  # named fields/columns
    for word in set(re.findall(r'\b([a-z]+)[ _]dates?\b', q)) - {'event', 'source', 'snapshot', 'the', 'their', 'its', 'cutoff', 'study'}:
        if not re.search(r'\b' + word + r'[ _]date', w): drift.append(f'{word} date')
    return drift


def _extract_sql(text):
    m = re.search(r'```(?:sql)?\s*(.+?)```', text, re.S | re.I)
    sql = (m.group(1) if m else text).strip()
    m = re.search(r'(?is)\b(with|select)\b.*', sql)
    return m.group(0).strip().rstrip(';') if m else sql


def _plausible(path, question, sql, probe):
    """Reject impossible results (cross joins, percent out of range) so they are repaired or declined, never returned."""
    rows = probe.get('rows') or []
    if len(rows) == 1 and len(probe['columns']) == 1:
        v = next(iter(rows[0].values()))
        if isinstance(v, (int, float)):
            import sqlite3
            db = sqlite3.connect(f'file:{sql_query.semantic_layer.ensure(path)}?mode=ro', uri=True)
            try:
                roster = db.execute('SELECT count(*) FROM patient').fetchone()[0]
                streams = re.findall(r"stream\s*=\s*'([a-z_]+)'", sql)
                limit = max([db.execute('SELECT count(*) FROM record WHERE stream=?', (st,)).fetchone()[0] for st in streams] or
                            [db.execute('SELECT count(*) FROM record').fetchone()[0]])
            finally:
                db.close()
            if re.search(r'count\s*\(\s*distinct\s+\w*\.?patient_id', sql, re.I) and v > roster:
                raise ValueError(f'Implausible: {v} distinct patients exceeds the roster of {roster}; check joins.')
            if v > limit and re.search(r'count\s*\(', sql, re.I):
                raise ValueError(f'Implausible: count {v} exceeds the {limit} records in the queried stream(s); likely a cross join.')
            if re.search(r'percent|share|proportion|rate', question, re.I) and '100.0' in sql and not 0 <= v <= 100:
                raise ValueError(f'Implausible percentage {v}.')


def answer(path, question, as_of, model=None):
    """Return (sql_node_parameters, result, receipt) or raise ValueError."""
    model = model or LocalModel.get()
    profile = source_profile.load(path)
    streams, schema = schema_for(question, profile)
    qt = set(_tokens(question))
    ranked = sorted(EXAMPLES, key=lambda e: -len(qt & set(_tokens(e[0] + ' ' + e[1]))))[:7]
    shots = '\n\n'.join(f'Q: {q}\n```sql\n{sq}\n```' for q, sq in ranked)
    system = RULES + f'\nThe data snapshot is as of {as_of}.'
    user = f'{schema}\n\nEXAMPLES\n{shots}\n\nQ: {question}'
    receipt = {'version': VERSION, 'streams_retrieved': streams, 'attempts': [], 'agreement': None}

    def run(sql):
        probe = sql_query.execute(path, {'query': sql, 'returns': 'table'})
        _plausible(path, question, sql, probe)
        returns = returns_for(question, probe['columns'], probe.get('rows'))
        if returns == 'patient_set' and probe['columns'][0] != 'patient_id': returns = 'table'
        result = sql_query.execute(path, {'query': sql, 'returns': returns}) if returns != 'table' else probe
        return {'query': sql, 'returns': returns}, result

    def signature(result):
        if 'value' in result: return ('v', round(float(result['value']), 6) if isinstance(result['value'], (int, float)) else result['value'])
        if 'patient_ids' in result: return ('p', tuple(result['patient_ids']))
        return ('t', json.dumps(result.get('rows'), sort_keys=True, default=str))

    # Self-consistency: greedy + sampled formulations, executed; answer only on majority agreement.
    candidates = []
    schedule = [(0.0, None)] if getattr(model, 'single', False) else [(0.0, None), (0.6, 11)]
    for attempt, (temp, seed) in enumerate(schedule):
        text, stats = model(system, user, temperature=temp, seed=seed)
        sql = _extract_sql(text)
        entry = {'sql': sql, **stats}
        try:
            params, result = run(sql)
        except ValueError as exc:
            entry['error'] = str(exc)[:200]
            if attempt == 0:  # one repair of the greedy query with the error message
                text, stats = model(system, f'{user}\n\nYour previous SQL failed: {entry["error"]}\nPrevious SQL: {sql}\nWrite a corrected query.')
                sql = _extract_sql(text); entry = {'sql': sql, 'repair': True, **stats}
                try:
                    params, result = run(sql)
                except ValueError as exc2:
                    entry['error'] = str(exc2)[:200]
                    receipt['attempts'].append(entry); continue
            else:
                receipt['attempts'].append(entry); continue
        receipt['attempts'].append(entry)
        candidates.append((signature(result), params, result))
    votes = Counter(c[0] for c in candidates)
    if votes:
        top, n = votes.most_common(1)[0]
        receipt['agreement'] = f'{n}/{len(receipt["attempts"])}'
        if n >= 2 or (getattr(model, 'single', False) and n == 1):
            params, result = next((p_, r_) for sig, p_, r_ in candidates if sig == top)
            return params, result, receipt
        error = f'formulations disagree ({dict((str(k)[:40], v) for k, v in votes.items())})'
    else:
        error = '; '.join(a.get('error', '') for a in receipt['attempts'])[:300]
    raise ValueError(f'Local model declined: {error}')
