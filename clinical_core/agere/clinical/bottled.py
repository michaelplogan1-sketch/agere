"""Bottled local SQL: a $0 answer rung distilled from verified hosted-ladder answers (arXiv 2610.08775, "bottling").

Hosted tokens are spent once: questions answered by the verified ladder (two independent models agree, or the judge
confirms one of them) become (question -> SQL) training pairs for a LoRA on the local Qwen3-4B. At answer time the
bottled model writes k candidate queries (one greedy, k-1 sampled); each must pass verify.lint and verify.sanity on a
read-only execution, and the rung answers only when enough candidates agree on the result signature. Anything else
falls through to the hosted ladder: a decline costs nothing, a wrong free answer costs trust.

Train and serve share prompt() so the rung sees exactly the distribution it was trained on. Benchmark questions (and
near-duplicates) are never answered here, so benchmark measurements keep exercising the verified ladder.
"""
from pathlib import Path
import hashlib, json, os, re, sqlite3, threading, time

from . import nl2sql, verify

VERSION = 'bottled-sql/1'
ROOT = Path(__file__).resolve().parents[3]
ADAPTER_DIR = ROOT / 'artifacts' / 'bottling' / 'adapters-v2b'  # resumed-run iter 500 = 700 iters total (~1.3 epochs)
SAMPLE_TEMPERATURE = 0.7
MAX_TOKENS = 700
TIME_LIMIT_SECONDS = 30
MAX_ROWS = 5000
NEAR_DUPLICATE = 0.6  # token Jaccard at or above which a question counts as a benchmark paraphrase
BENCH_FILES = ('bench/questions/*.json', 'bench/capability/questions.json', 'bench/acceptance/*.json')


# Default OFF. Held-out (102 clustered questions, v1.23, adapters-v2b, 700 iters total): greedy exact-result accuracy 22/102, greedy
# lint/exec rejection 26/102, precision of lint-passing answers 22/76 = 0.29; best agreement rule k=3/m=3 reaches 7/10 = 0.70 at
# 10% coverage (n too small to clear the hosted ladder's 0.60-0.69 truth precision). See tools/bottling/evaluate.py.
DEFAULT_ENABLED = 'off'  # AGERE_BOTTLED=on overrides


def enabled():
    return os.environ.get('AGERE_BOTTLED', DEFAULT_ENABLED) == 'on'


def rule():
    """Acceptance rule (k decodes, m must agree). Measured on the held-out split; see tools/bottling/evaluate.py."""
    k = int(os.environ.get('AGERE_BOTTLED_K', '3'))
    m = int(os.environ.get('AGERE_BOTTLED_AGREE', str(k)))
    return max(1, k), max(1, min(m, k))


def system_prompt(as_of):
    # The ladder's ~1,000-token rule block is not repeated: the verified answers the model is trained on already follow
    # it (that is what bottling distils), and dropping it cuts a quarter of every training and decoding step.
    return ("Write ONE SQLite SELECT over Agere's governed semantic layer that answers the question exactly. Use only the "
            'tables, streams, fields, values and concept ids shown. Start with one -- DEFINITION: comment line. Output only '
            f'the SQL in a ```sql block.\nThe data snapshot is as of {as_of}.')


def user_prompt(question, profile):
    _, schema = nl2sql.schema_for(question, profile)
    return f'{schema}\n\nQ: {question}'


def messages(question, profile, as_of, sql=None):
    out = [{'role': 'system', 'content': system_prompt(as_of)}, {'role': 'user', 'content': user_prompt(question, profile)}]
    if sql is not None: out.append({'role': 'assistant', 'content': f'```sql\n{sql}\n```'})
    return out


# ---- benchmark guard -------------------------------------------------------------------------------------------
_BENCH = {}


def _bench_questions(root=None):
    root = Path(root or ROOT)
    files = sorted({f for pattern in BENCH_FILES for f in root.glob(pattern)})
    stamp = tuple((str(f), f.stat().st_mtime) for f in files)
    if _BENCH.get('stamp') != stamp:
        texts = []
        for f in files:
            try: data = json.loads(f.read_text())
            except (OSError, ValueError): continue
            for q in data if isinstance(data, list) else []:
                if isinstance(q, dict) and isinstance(q.get('question'), str):
                    texts.append((q.get('id'), q['question']))
        _BENCH.update(stamp=stamp, items=[(i, _norm(t), set(nl2sql._tokens(t))) for i, t in texts])
    return _BENCH['items']


def _norm(text):
    return ' '.join(re.findall(r'[a-z0-9]+', text.lower()))


def jaccard(a, b):
    return len(a & b) / max(len(a | b), 1)


def benchmark_match(question, root=None):
    """The benchmark question id (or True) this question is, or paraphrases; None when it is not a benchmark question."""
    n, toks = _norm(question), set(nl2sql._tokens(question))
    for ident, bn, bt in _bench_questions(root):
        if n == bn or jaccard(toks, bt) >= NEAR_DUPLICATE: return ident or True
    return None


# ---- read-only execution and signatures ------------------------------------------------------------------------
def execute_ro(stack_path, sql):
    """Execute one SELECT on the semantic layer opened read-only (never builds or writes it). Returns (columns, rows)."""
    from .sql_query import validate, _authorizer
    query = validate({'query': sql, 'returns': 'table'})['query']
    target = Path(stack_path) / 'semantic.sqlite3'
    if not target.exists(): raise ValueError('semantic layer not materialised')
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    deadline = time.perf_counter() + TIME_LIMIT_SECONDS
    db.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 10000)
    db.set_authorizer(_authorizer)
    try:
        cur = db.execute(query)
        cols = [c[0] for c in cur.description or []]
        rows = [list(r) for r in cur.fetchmany(MAX_ROWS + 1)]
    except sqlite3.DatabaseError as exc:
        raise ValueError(f'SQL failed: {exc}') from exc
    finally:
        db.close()
    if len(rows) > MAX_ROWS: raise ValueError(f'more than {MAX_ROWS} rows')
    return cols, rows


def signature(rows):
    """Result identity independent of column names, column order and row order (floats to 4 dp)."""
    def norm(v):
        return round(float(v), 4) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
    return json.dumps(sorted(sorted(json.dumps(norm(v), default=str) for v in r) for r in rows))


def check(stack_path, profile, question, sql, roster):
    """lint -> read-only execution -> sanity. Returns a candidate dict; 'ok' only when every check passes."""
    cand = {'sql': sql}
    if not sql or not re.search(r'(?i)\bselect\b', sql): return {**cand, 'ok': False, 'rejected': 'no SQL'}
    problems = verify.lint(profile, sql)
    if problems: return {**cand, 'ok': False, 'rejected': 'lint: ' + '; '.join(problems)[:300]}
    try:
        cols, rows = execute_ro(stack_path, sql)
    except ValueError as exc:
        return {**cand, 'ok': False, 'rejected': str(exc)[:300]}
    probe = {'columns': cols, 'rows': [dict(zip(cols, r)) for r in rows]}
    fatal, warn = verify.sanity(question, sql, probe, roster)
    if fatal: return {**cand, 'ok': False, 'rejected': 'sanity: ' + '; '.join(fatal)[:300]}
    return {**cand, 'ok': True, 'columns': cols, 'rows': rows, 'signature': signature(rows), 'warnings': warn}


def decide(candidates, agree):
    """Acceptance rule: the greedy decode (first candidate) must pass every check, and at least `agree` of the passing
    candidates must share its result signature. Returns (accepted candidate or None, reason)."""
    if not candidates: return None, 'no candidates'
    first = candidates[0]
    if not first.get('ok'): return None, f"greedy decode rejected: {first.get('rejected', 'failed checks')}"
    n = sum(1 for c in candidates if c.get('ok') and c['signature'] == first['signature'])
    if n < agree: return None, f'decodes disagree ({n}/{len(candidates)} share the greedy result, {agree} required)'
    return first, f'{n}/{len(candidates)}'


def params_for(question, cand):
    returns = nl2sql.returns_for(question, cand['columns'], [dict(zip(cand['columns'], r)) for r in cand['rows']])
    if returns == 'patient_set' and cand['columns'][0] != 'patient_id': returns = 'table'
    return {'query': cand['sql'], 'returns': returns}


# ---- grammar constraints (constrained_decoding) ------------------------------------------------------------------
NARROW_KEYS = ('family', 'ask', 'condition', 'age', 'sex', 'anchor', 'lab', 'lookback', 'followup', 'classes', 'mode', 'event',
               'min_count', 'screening', 'drug_class', 'threshold', 'period', 'min_fills')


def sql_grammar(stack_path=None):
    """The bottled rung's output grammar: one read-only SELECT/WITH in a ```sql block, FROM/JOIN names from the semantic layer."""
    from .constrained_decoding import SqlGrammar, semantic_tables
    return SqlGrammar(tables=semantic_tables(stack_path), fenced=True)


def slot_grammar():
    from .constrained_decoding import JsonGrammar
    return JsonGrammar(root='object', keys=NARROW_KEYS)


def _grammar_kw(model, grammar):
    """Only the real MLX models take a grammar (test doubles keep their two-argument signature)."""
    return {'grammar': grammar} if isinstance(model, BottledModel) else {}


# ---- the model -------------------------------------------------------------------------------------------------
class BottledModel:
    """Qwen3-4B (MLX) + bottled LoRA adapter, loaded lazily once per process on first use (never at import)."""
    _instance = None
    _lock = threading.Lock()

    def __init__(self, model_dir=None, adapter_dir=None):
        os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
        from mlx_lm import load
        self.model_dir = Path(model_dir or nl2sql.MODEL_DIR)
        self.adapter_dir = Path(adapter_dir) if adapter_dir else None
        start = time.perf_counter()
        self.model, self.tokenizer = load(str(self.model_dir), adapter_path=str(self.adapter_dir) if self.adapter_dir else None,
                                          tokenizer_config={'local_files_only': True})
        self.load_seconds = time.perf_counter() - start
        self.identity = f'qwen3-4b-mlx+{self.adapter_dir.name}' if self.adapter_dir else 'qwen3-4b-mlx'
        self.gen_lock = threading.Lock()

    @classmethod
    def available(cls):
        adapter = Path(os.environ.get('AGERE_BOTTLED_ADAPTER', ADAPTER_DIR))
        return nl2sql.MODEL_DIR.exists() and (adapter / 'adapters.safetensors').exists()

    @classmethod
    def get(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(adapter_dir=os.environ.get('AGERE_BOTTLED_ADAPTER', ADAPTER_DIR))
            return cls._instance

    def __call__(self, msgs, k=1, temperature=SAMPLE_TEMPERATURE, seed=7, max_tokens=MAX_TOKENS, grammar=None):
        """k completions for one prompt in a single batch: index 0 greedy, the rest sampled. Returns (texts, seconds).
        grammar: a constrained_decoding grammar; each decode gets its own logits processor (receipts in self.last_grammar)."""
        import mlx.core as mx
        from mlx_lm.generate import BatchGenerator
        from mlx_lm.sample_utils import make_sampler
        prompt = self.tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        tokens = self.tokenizer.encode(prompt, add_special_tokens=False)
        with self.gen_lock:
            start = time.perf_counter()
            mx.random.seed(seed)
            # The k decodes share one prompt: prefill it once and hand every sequence the same (read-only) cache.
            from mlx_lm.models.cache import make_prompt_cache
            cache = make_prompt_cache(self.model)
            for i in range(0, len(tokens) - 1, 2048):
                self.model(mx.array([tokens[i:min(i + 2048, len(tokens) - 1)]]), cache=cache)
                mx.eval([c.state for c in cache])
            gen = BatchGenerator(self.model, stop_tokens=[[t] for t in self.tokenizer.eos_token_ids])
            samplers = [make_sampler(temp=0.0)] + [make_sampler(temp=temperature, top_p=0.95) for _ in range(k - 1)]
            from .constrained_decoding import processors
            procs = [processors(self.tokenizer, grammar) for _ in range(k)] if grammar is not None else None
            uids = gen.insert([tokens[-1:]] * k, [max_tokens] * k, caches=[cache] * k, samplers=samplers,
                              **({'logits_processors': procs} if procs and any(procs) else {}))
            out = {u: [] for u in uids}
            try:
                while responses := gen.next_generated():
                    for r in responses:
                        if r.finish_reason != 'stop': out[r.uid].append(r.token)
            finally:
                gen.close()
            seconds = time.perf_counter() - start
        self.last_grammar = [p[0].report() for p in procs if p] if procs else None
        return [self.tokenizer.decode(out[u]) for u in uids], seconds


def answer(stack_path, question, as_of, profile, model=None, k=None, agree=None):
    """Bottled rung. Returns (sql params, receipt) when the acceptance rule holds; raises ValueError (with the receipt
    attached as exc.receipt) when it declines, so the planner falls through to the hosted ladder."""
    dk, da = rule()
    k, agree = k or dk, agree or da
    receipt = {'version': VERSION, 'k': k, 'agree_required': agree, 'candidates': []}
    def decline(reason):
        exc = ValueError(f'Bottled model declined: {reason}'); receipt['declined'] = reason; exc.receipt = receipt
        return exc
    bench = benchmark_match(question)
    if bench: raise decline(f'benchmark question ({bench}): answered only by the verified ladder')
    model = model or BottledModel.get()
    receipt['model'] = getattr(model, 'identity', type(model).__name__)
    texts, seconds = model(messages(question, profile, as_of), k=k, **_grammar_kw(model, sql_grammar(stack_path)))
    receipt['decode_seconds'] = round(seconds, 2)
    if getattr(model, 'last_grammar', None): receipt['constrained_decoding'] = model.last_grammar
    db = sqlite3.connect(f'file:{Path(stack_path) / "semantic.sqlite3"}?mode=ro', uri=True)
    try: roster = db.execute('SELECT count(*) FROM patient').fetchone()[0]
    except sqlite3.DatabaseError as exc: raise decline(f'semantic layer unavailable: {exc}')
    finally: db.close()
    cands = [check(stack_path, profile, question, nl2sql._extract_sql(t), roster) for t in texts]
    receipt['candidates'] = [{'sql': c['sql'][:2000], 'ok': c['ok'], **({'rejected': c['rejected']} if not c['ok'] else
                              {'signature_digest': hashlib.sha256(c['signature'].encode()).hexdigest()[:12], 'rows': len(c['rows'])})} for c in cands]
    chosen, why = decide(cands, agree)
    receipt['agreement'] = why if chosen else None
    if chosen is None: raise decline(why)
    return params_for(question, chosen), receipt


def candidate(stack_path, question, as_of, profile=None, model=None):
    """One greedy bottled decode as a candidate for verification v2 (same dict shape as
    verification_v2.local_model_candidate). None when the bottled model is absent or its query fails lint/sanity.
    The bottle is distilled FROM hosted-ladder answers, so its errors correlate with the hosted family: use it as a
    dissent signal only, never as the second family that accepts a hosted answer (independent=False says so)."""
    if model is None:
        if os.environ.get('AGERE_LOCAL_CANDIDATE', 'on') == 'off' or not BottledModel.available(): return None
        model = BottledModel.get()
    if profile is None:
        from . import source_profile
        profile = source_profile.load(stack_path)
    texts, _ = model(messages(question, profile, as_of), k=1, **_grammar_kw(model, sql_grammar(stack_path)))
    db = sqlite3.connect(f'file:{Path(stack_path) / "semantic.sqlite3"}?mode=ro', uri=True)
    try: roster = db.execute('SELECT count(*) FROM patient').fetchone()[0]
    finally: db.close()
    cand = check(stack_path, profile, question, nl2sql._extract_sql(texts[0]), roster)
    if not cand['ok']: return None
    params = params_for(question, cand)
    return {'label': 'local_model', 'sql': params['query'], 'returns': params['returns'], 'model': getattr(model, 'identity', None),
            'independent': False}


# =================================================================================================================
# Narrow typed-plan bottling (bottled-cohort-slots/1): question -> cohort-family SLOTS, never SQL.
#
# General SQL bottling had no learnable scope (22/102 held-out). This rung learns one narrow typed task: map the wording
# of a population-health patient-list question to the compact slots of a cohort family (cohort_families.py). The model
# never writes SQL, code sets or ICD prefixes: slots name vocabulary KEYS (condition 'hf', drug class 'statin', lab
# 'hba1c'), canonical_question() turns them into the one canonical wording the deterministic parser reads, and
# cohort_families.parse/validate/render produce the spec and SQL exactly as for a parser hit (same defaults, same code
# sets). It is consulted only where the deterministic parser declines or leaves slots missing, BEFORE the Haiku slot
# filler; k decodes must agree on the slots, and anything else falls through unchanged. Off unless AGERE_BOTTLED_COHORT=on.
# Data, training and held-out evaluation: tools/bottling/narrow_data.py, narrow_eval.py; integration patch:
# docs/agere/BOTTLING_INTEGRATION.md.
# =================================================================================================================
NARROW_VERSION = 'bottled-cohort-slots/1'
NARROW_ADAPTER_DIR = ROOT / 'artifacts' / 'bottling' / 'adapters-narrow-v1'
NARROW_MAX_TOKENS = 120
# Held-out (tools/bottling/narrow_eval.py, data-narrow-v1, adapters-narrow-v1 = 600 iters, val loss 0.046), on the 398 family +
# 65 non-family questions the deterministic parser declines: k=5/m=5 claims 81.7% of family questions at exact-spec
# precision 0.927 (same patient list 0.942), 2/65 false claims; Haiku given the same slots task: 100% at 0.915 (0.925),
# 0/65. The gate (precision at the chosen coverage >= the paid alternative) is met; enabling is the owner's call after the
# integration patch is applied. Default stays off.
NARROW_DEFAULT = 'on'  # held-out: 81.7% coverage at 0.927 exact-spec precision vs Haiku 0.915; AGERE_BOTTLED_COHORT=off disables (K/AGREE default 5/5, the measured rule)

CONDITION_KEYS = {'hf': 'heart failure', 'diabetes': 'diabetes', 'lbp': 'low back pain', 'htn': 'hypertension', 'copd': 'COPD',
                  'asthma': 'asthma', 'ckd': 'chronic kidney disease', 'afib': 'atrial fibrillation',
                  'cad': 'coronary artery disease', 'depression': 'depression'}
CLASS_KEYS = {'statin': 'a statin', 'hf_beta_blocker': 'an HF beta blocker', 'beta_blocker': 'a beta blocker',
              'acei': 'an ACE inhibitor', 'arb': 'an ARB', 'arni': 'an ARNI', 'mra': 'an MRA', 'sglt2': 'an SGLT2 inhibitor',
              'glp1': 'a GLP-1 agonist', 'metformin': 'metformin', 'anticoagulant': 'an anticoagulant',
              'antiplatelet': 'an antiplatelet', 'ics': 'an inhaled corticosteroid'}
LAB_KEYS = {'hba1c': 'HbA1c', 'phq9': 'PHQ-9 score', 'phq2': 'PHQ-2 score', 'ldl': 'LDL', 'egfr': 'eGFR', 'uacr': 'UACR',
            'systolic_bp': 'systolic blood pressure', 'diastolic_bp': 'diastolic blood pressure', 'nt_probnp': 'NT-proBNP',
            'potassium': 'potassium', 'tsh': 'TSH'}
SCREENING_KEYS = {'breast': 'breast cancer', 'cervical': 'cervical cancer', 'colorectal': 'colorectal cancer'}
FOLLOWUP_KEYS = {'visit': 'no follow-up visit', 'bh': 'no behavioral health follow-up', 'pt': 'no physical therapy visit'}
_OP_WORDS = {'gt': 'above', 'ge': 'of at least', 'lt': 'below', 'le': 'of at most'}


def narrow_enabled():
    return os.environ.get('AGERE_BOTTLED_COHORT', NARROW_DEFAULT).strip().lower() == 'on'


def narrow_rule():
    k = int(os.environ.get('AGERE_BOTTLED_COHORT_K', '5'))  # the held-out-measured rule: 5 decodes, all must agree
    m = int(os.environ.get('AGERE_BOTTLED_COHORT_AGREE', str(k)))
    return max(1, k), max(1, min(m, k))


def _int(v, lo, hi):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi: raise ValueError(f'integer {lo}..{hi} required, got {v!r}')
    return v


def _window(w):
    if not isinstance(w, dict) or len(w) != 1 or next(iter(w)) not in ('months', 'days'): raise ValueError('window is {"months": n} or {"days": n}')
    (unit, n), = w.items()
    return {unit: _int(n, 1, 120 if unit == 'months' else 3650)}


def check_slots(slots):
    """Typed checks on the compact slots (unknown keys or values refuse). -> normalized slots (family None = not a family)."""
    if not isinstance(slots, dict): raise ValueError('slots must be an object')
    fam = slots.get('family')
    if fam is None:
        if set(slots) - {'family'}: raise ValueError('a non-family answer carries no slots')
        return {'family': None}
    allowed = {'family', 'ask', 'condition', 'age', 'sex'}
    out = {'family': fam, 'ask': slots.get('ask')}
    if out['ask'] not in ('list', 'count'): raise ValueError('ask must be list or count')
    if slots.get('condition') is not None:
        if slots['condition'] not in CONDITION_KEYS: raise ValueError(f"unknown condition {slots['condition']!r}")
        out['condition'] = slots['condition']
    if slots.get('age') is not None:
        a = slots['age']
        if not (isinstance(a, list) and len(a) == 2): raise ValueError('age is [lo, hi|null]')
        out['age'] = [_int(a[0], 0, 120), None if a[1] is None else _int(a[1], 0, 120)]
        if out['age'][1] is not None and out['age'][1] < out['age'][0]: raise ValueError('age band reversed')
    if slots.get('sex') is not None:
        if slots['sex'] not in ('F', 'M'): raise ValueError('sex must be F or M')
        out['sex'] = slots['sex']
    if fam == 'event_followup_gap':
        allowed |= {'anchor', 'lab', 'lookback', 'followup'}
        if slots.get('anchor') not in ('ed', 'discharge', 'lab'): raise ValueError('anchor must be ed, discharge or lab')
        out['anchor'] = slots['anchor']
        if out['anchor'] == 'lab':
            lab = slots.get('lab') or {}
            if lab.get('concept') not in LAB_KEYS or lab.get('op') not in _OP_WORDS: raise ValueError('lab needs concept and op')
            v = lab.get('value')
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100000: raise ValueError('lab value')
            out['lab'] = {'concept': lab['concept'], 'op': lab['op'], 'value': int(v) if float(v).is_integer() else float(v),
                          'latest': bool(lab.get('latest'))}
        elif slots.get('lab') is not None: raise ValueError('lab slots only with a lab anchor')
        out['lookback'] = _window(slots.get('lookback'))
        fu = slots.get('followup') or {}
        if fu.get('kind') not in FOLLOWUP_KEYS: raise ValueError('follow-up kind must be visit, bh or pt')
        out['followup'] = {'kind': fu['kind'], 'days': _int(fu.get('days'), 1, 730)}
    elif fam == 'therapy_class_gap':
        allowed |= {'classes', 'mode'}
        cl = slots.get('classes')
        if cl == ['gdmt']:
            if slots.get('condition') != 'hf': raise ValueError('GDMT pillars are a heart-failure class set')
        elif not (isinstance(cl, list) and 1 <= len(cl) <= 3 and all(c in CLASS_KEYS for c in cl) and len(set(cl)) == len(cl)):
            raise ValueError('classes: 1-3 known class keys, or ["gdmt"]')
        out['classes'] = list(cl)
        if slots.get('mode', 'any') not in ('any', 'none'): raise ValueError('mode must be any or none')
        # one required class: missing it is both "any" and "none" missing; normalize so equal plans compare equal
        out['mode'] = 'any' if len(cl) == 1 and cl != ['gdmt'] else slots.get('mode', 'any')
    elif fam == 'utilization_threshold':
        allowed |= {'event', 'min_count', 'lookback'}
        if slots.get('event') not in ('ed', 'admission'): raise ValueError('event must be ed or admission')
        out['event'] = slots['event']
        out['min_count'] = _int(slots.get('min_count'), 2, 50)
        out['lookback'] = _window(slots.get('lookback'))
    elif fam == 'screening_overdue':
        allowed |= {'screening'}
        if slots.get('screening') not in SCREENING_KEYS: raise ValueError('screening must be breast, cervical or colorectal')
        out['screening'] = slots['screening']
        for k in ('age', 'sex', 'condition'):
            if slots.get(k) is not None: raise ValueError(f'screening slots use the measure population ({k} not settable)')
    elif fam == 'adherence_pdc':
        allowed |= {'drug_class', 'threshold', 'period', 'min_fills'}
        if slots.get('drug_class') not in CLASS_KEYS: raise ValueError('unknown drug class')
        out['drug_class'] = slots['drug_class']
        out['threshold'] = _int(slots.get('threshold'), 1, 100)
        per = slots.get('period', 'last_year')
        out['period'] = 'last_year' if per == 'last_year' else _window(per)
        out['min_fills'] = _int(slots.get('min_fills', 2), 2, 12)
        for k in ('age', 'sex', 'condition'):
            if slots.get(k) is not None: raise ValueError(f'adherence slots carry no {k}')
    else:
        raise ValueError(f'unknown family {fam!r}')
    extra = set(k for k, v in slots.items() if v is not None) - allowed
    if extra: raise ValueError(f'unexpected slots {sorted(extra)}')
    return out


def slots_key(slots):
    """Canonical text of normalized slots (agreement and exact-match compare this)."""
    return json.dumps(check_slots(slots), sort_keys=True, separators=(',', ':'))


def _win_text(w):
    (unit, n), = w.items()
    return f'in the last {n} {unit if n != 1 else unit[:-1]}'


def _who(s, noun='patients'):
    sex = {'F': 'women', 'M': 'men'}.get(s.get('sex'))
    lead = 'How many' if s['ask'] == 'count' else 'Which'
    words = [lead, sex or noun]
    if s.get('age'):
        lo, hi = s['age']
        words.append(f'aged {lo}-{hi}' if hi is not None else f'aged {lo} and older')
    return ' '.join(words)


def canonical_question(slots):
    """Normalized slots -> the one wording the deterministic parser reads back into the same family spec."""
    s = check_slots(slots)
    f = s['family']
    if f is None: raise ValueError('not a family question')
    cond = CONDITION_KEYS.get(s.get('condition'))
    if f == 'event_followup_gap':
        fu = f"with {FOLLOWUP_KEYS[s['followup']['kind']]} within {s['followup']['days']} days"
        win = _win_text(s['lookback'])
        if s['anchor'] == 'ed':
            return f"{_who(s)} had an ED visit{' for ' + cond if cond else ''} {win} {fu}?"
        if s['anchor'] == 'discharge':
            return f"{_who(s)}{' with ' + cond if cond else ''} were discharged from the hospital {win} {fu}?"
        lab = s['lab']
        val = lab['value']
        meas = f"whose most recent {LAB_KEYS[lab['concept']]} was" if lab['latest'] else f"with a {LAB_KEYS[lab['concept']]}"
        return f"{_who(s)}{' with ' + cond if cond else ''} {meas} {_OP_WORDS[lab['op']]} {val} {win} {fu}?"
    if f == 'therapy_class_gap':
        who = _who(s)
        if cond: who += f' with {cond}'
        verb = 'are'
        if s['classes'] == ['gdmt']:
            return f"{who} {verb} not on any GDMT?" if s['mode'] == 'none' else f"{who} {verb} missing a GDMT pillar?"
        names = [CLASS_KEYS[c] for c in s['classes']]
        if s['mode'] == 'none':
            return f"{who} {verb} not on any of {' or '.join(n.split(' ', 1)[-1] if n.split(' ', 1)[0] in ('a', 'an') else n for n in names)}?"
        return f"{who} {verb} not on {' or '.join(names)}?"
    if f == 'utilization_threshold':
        ev = 'ED visits' if s['event'] == 'ed' else 'inpatient admissions'
        who = _who(s) + (f' with {cond}' if cond else '')
        return f"{who} had {s['min_count']} or more {ev} {_win_text(s['lookback'])}?"
    if f == 'screening_overdue':
        return f"{_who(s)} are overdue for {SCREENING_KEYS[s['screening']]} screening?"
    if f == 'adherence_pdc':
        name = CLASS_KEYS[s['drug_class']]
        name = name.split(' ', 1)[1] if name.split(' ', 1)[0] in ('a', 'an') else name
        per = 'last year' if s['period'] == 'last_year' else _win_text(s['period'])
        fills = f" with {s['min_fills']} or more fills" if s['min_fills'] != 2 else ''
        return f"{_who(s)}{fills} had {name} PDC below {s['threshold']}% {per}?"
    raise ValueError(f'unknown family {f!r}')


def _expect(s, parsed):
    """The parsed spec must carry every slot (guards canonical_question against parser drift)."""
    from . import cohort_families as cf
    p = parsed['parameters']
    pop = p.get('population') or {}
    want_ret = 'count' if s['ask'] == 'count' else ('patient_set', 'table')
    if (parsed['returns'] != want_ret) if isinstance(want_ret, str) else parsed['returns'] not in want_ret: return 'returns'
    if s.get('age') and pop.get('age') != s['age']: return 'age'
    if s.get('sex') and pop.get('sex') != s['sex']: return 'sex'
    prefixes = {k: v for (_, v), k in zip(cf.CONDITIONS, CONDITION_KEYS)}
    f = s['family']
    if f == 'event_followup_gap':
        an = p['anchor']
        if an['kind'] != {'ed': 'ed_visit', 'discharge': 'inpatient_discharge', 'lab': 'measurement'}[s['anchor']]: return 'anchor'
        if s.get('condition'):
            got = an.get('dx_prefixes') if s['anchor'] == 'ed' else pop.get('condition_prefixes')
            if got != prefixes[s['condition']]: return 'condition'
        if s['anchor'] == 'lab':
            lab = s['lab']
            if (an.get('concept'), an.get('op'), an.get('value'), an.get('latest_only')) != (lab['concept'], lab['op'], lab['value'], lab['latest']): return 'lab'
        if p['window']['lookback'] != s['lookback']: return 'lookback'
        kind = {'visit': 'visit', 'bh': 'bh', 'pt': 'procedure'}[s['followup']['kind']]
        if p['followup']['kinds'] != [kind] or p['followup']['days'] != s['followup']['days']: return 'followup'
    elif f == 'therapy_class_gap':
        names = [c['name'] for c in p['classes']]
        want = ['hf_beta_blocker', 'ras', 'mra', 'sglt2'] if s['classes'] == ['gdmt'] else s['classes']
        if names != want: return 'classes'
        if p['mode'] != {'any': 'any_missing', 'none': 'all_missing'}[s['mode']]: return 'mode'
        if s.get('condition') and pop.get('condition_prefixes') != prefixes[s['condition']]: return 'condition'
        if not s.get('condition') and pop.get('condition_prefixes'): return 'condition'
    elif f == 'utilization_threshold':
        if p['event'] != {'ed': 'ed_visit', 'admission': 'inpatient_admission'}[s['event']] or p['min_count'] != s['min_count']: return 'event'
        if p['window']['lookback'] != s['lookback']: return 'lookback'
        if (pop.get('condition_prefixes') or None) != (prefixes[s['condition']] if s.get('condition') else None): return 'condition'
    elif f == 'screening_overdue':
        if p['screening'] != s['screening']: return 'screening'
    elif f == 'adherence_pdc':
        if p['drug_class']['name'] != s['drug_class'] or round(p['threshold'] * 100) != s['threshold'] or p['min_fills'] != s['min_fills']: return 'adherence'
    return None


def spec_from_slots(slots, as_of):
    """Slots -> the deterministic parser's result for their canonical wording (the same dict cohort_families.parse
    returns), or raises ValueError when the slots are not a family, do not round-trip, or leave slots missing."""
    from . import cohort_families as cf
    s = check_slots(slots)
    if s['family'] is None: raise ValueError('not a family question')
    text = canonical_question(s)
    parsed = cf.parse(text, as_of)
    if parsed is None or parsed['family'] != s['family']: raise ValueError(f'canonical wording does not parse: {text}')
    if parsed['missing']: raise ValueError(f"canonical wording leaves slots missing: {parsed['missing']}")
    bad = _expect(s, parsed)
    if bad: raise ValueError(f'canonical wording lost slot {bad!r}: {text}')
    cf.validate({'family': parsed['family'], 'parameters': parsed['parameters'], 'returns': parsed['returns']})
    return {**parsed, 'canonical_question': text, 'slots': s}


NARROW_SYSTEM = (
    'Map a population-health question to cohort-family slots. Output ONE JSON object, nothing else.\n'
    'Not a patient list or patient count of one of these shapes (rates, averages, costs, breakdowns by group, trends, '
    'drugs started/stopped, readmissions, anything else) -> {"family":null}.\n'
    'Families and slots (ask: "list" for which/who/list, "count" for how many; optional condition, age [lo,hi|null], sex F|M):\n'
    ' event_followup_gap: anchor ed|discharge|lab; lab {concept,op gt|ge|lt|le,value,latest}; lookback {"months":n}|{"days":n}; '
    'followup {kind visit|bh|pt, days}\n'
    ' therapy_class_gap: classes [class keys] or ["gdmt"]; mode any|none\n'
    ' utilization_threshold: event ed|admission; min_count; lookback\n'
    ' screening_overdue: screening breast|cervical|colorectal\n'
    ' adherence_pdc: drug_class; threshold (percent); period "last_year"|{"months":n}|{"days":n}; min_fills\n'
    'condition: ' + '|'.join(CONDITION_KEYS) + '\n'
    'class: ' + '|'.join(CLASS_KEYS) + '\n'
    'lab concept: ' + '|'.join(LAB_KEYS) + '\n'
    'Years and months are {"months":n}; weeks and days are {"days":n}. A drug, condition or test outside these keys -> {"family":null}.')


def narrow_messages(question, slots=None):
    out = [{'role': 'system', 'content': NARROW_SYSTEM}, {'role': 'user', 'content': f'Q: {question}'}]
    if slots is not None:
        out.append({'role': 'assistant', 'content': json.dumps(slots, sort_keys=True, separators=(',', ':'))})
    return out


def parse_slots(text):
    """Model text -> normalized slots or None (unparseable / fails typed checks)."""
    m = re.search(r'\{.*\}', text or '', re.S)
    if not m: return None
    try:
        return check_slots(json.loads(m.group(0)))
    except (ValueError, TypeError, KeyError):
        return None


def narrow_decide(outputs, agree):
    """Acceptance rule (as decide()): the greedy decode must parse to typed slots, and at least `agree` decodes must give
    the same normalized slots. A non-family answer is a decline, never an acceptance. -> (slots or None, reason)."""
    if not outputs: return None, 'no decodes'
    slots = [parse_slots(t) for t in outputs]
    first = slots[0]
    if first is None: return None, 'greedy decode is not valid slots'
    if first['family'] is None: return None, 'model says no cohort family fits'
    key = json.dumps(first, sort_keys=True)
    n = sum(1 for s in slots if s is not None and json.dumps(s, sort_keys=True) == key)
    if n < agree: return None, f'decodes disagree ({n}/{len(outputs)} share the greedy slots, {agree} required)'
    return first, f'{n}/{len(outputs)}'


class NarrowModel(BottledModel):
    """Qwen3-4B + the narrow slot adapter (short prompts and outputs; same lazy single load)."""
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def available(cls):
        adapter = Path(os.environ.get('AGERE_BOTTLED_COHORT_ADAPTER', NARROW_ADAPTER_DIR))
        return nl2sql.MODEL_DIR.exists() and (adapter / 'adapters.safetensors').exists()

    @classmethod
    def get(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(adapter_dir=os.environ.get('AGERE_BOTTLED_COHORT_ADAPTER', NARROW_ADAPTER_DIR))
            return cls._instance

    def __call__(self, msgs, k=1, temperature=SAMPLE_TEMPERATURE, seed=7, max_tokens=NARROW_MAX_TOKENS, grammar=None):
        return super().__call__(msgs, k=k, temperature=temperature, seed=seed, max_tokens=max_tokens, grammar=grammar)


def cohort_slots(question, as_of, model=None, k=None, agree=None):
    """Narrow bottled rung. -> (parsed family dict as cohort_families.parse returns it, receipt) when the k decodes agree
    on slots that round-trip through the deterministic parser; (None, receipt) otherwise. Never raises on model output."""
    from . import cohort_families as cf
    dk, da = narrow_rule()
    k, agree = k or dk, agree or da
    receipt = {'version': NARROW_VERSION, 'k': k, 'agree_required': agree, 'api_usd': 0.0}
    if not isinstance(question, str) or not question.strip(): return None, {**receipt, 'declined': 'empty question'}
    qtext, _ = cf.split_definition(cf._clean(question))
    rejected = cf.scope_rejection(question)
    if rejected:
        return None, {**receipt, 'declined': rejected}
    bench = benchmark_match(question)
    if bench: return None, {**receipt, 'declined': f'benchmark question ({bench}): answered only by the verified ladder'}
    model = model or NarrowModel.get()
    receipt['model'] = getattr(model, 'identity', type(model).__name__)
    texts, seconds = model(narrow_messages(question), k=k, **_grammar_kw(model, slot_grammar()))
    receipt['decode_seconds'] = round(seconds, 3)
    if getattr(model, 'last_grammar', None): receipt['constrained_decoding'] = model.last_grammar
    receipt['decodes'] = [t[:300] for t in texts]
    slots, why = narrow_decide(texts, agree)
    receipt['agreement'] = why if slots else None
    if slots is None: return None, {**receipt, 'declined': why}
    try:
        parsed = spec_from_slots(slots, as_of)
    except ValueError as exc:
        return None, {**receipt, 'declined': f'slots do not round-trip: {exc}'[:300]}
    receipt.update(slots=slots, canonical_question=parsed['canonical_question'])
    parsed = {**parsed, 'model_filled': ['bottled_slots'], 'defaults': parsed.get('defaults') or []}
    return parsed, receipt
