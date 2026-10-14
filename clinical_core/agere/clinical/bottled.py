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
ADAPTER_DIR = ROOT / 'artifacts' / 'bottling' / 'adapters-v2'
SAMPLE_TEMPERATURE = 0.7
MAX_TOKENS = 700
TIME_LIMIT_SECONDS = 30
MAX_ROWS = 5000
NEAR_DUPLICATE = 0.6  # token Jaccard at or above which a question counts as a benchmark paraphrase
BENCH_FILES = ('bench/questions/*.json', 'bench/capability/questions.json', 'bench/acceptance/*.json')


DEFAULT_ENABLED = 'off'  # set from the held-out precision bar (tools/bottling/evaluate.py); AGERE_BOTTLED=on|off overrides


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

    def __call__(self, msgs, k=1, temperature=SAMPLE_TEMPERATURE, seed=7, max_tokens=MAX_TOKENS):
        """k completions for one prompt in a single batch: index 0 greedy, the rest sampled. Returns (texts, seconds)."""
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
            uids = gen.insert([tokens[-1:]] * k, [max_tokens] * k, caches=[cache] * k, samplers=samplers)
            out = {u: [] for u in uids}
            try:
                while responses := gen.next_generated():
                    for r in responses:
                        if r.finish_reason != 'stop': out[r.uid].append(r.token)
            finally:
                gen.close()
            seconds = time.perf_counter() - start
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
    texts, seconds = model(messages(question, profile, as_of), k=k)
    receipt['decode_seconds'] = round(seconds, 2)
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
    """One greedy bottled decode as an independent 'local' candidate for verification v2 (same dict shape as
    verification_v2.local_model_candidate). None when the bottled model is absent or its query fails lint/sanity."""
    if model is None:
        if os.environ.get('AGERE_LOCAL_CANDIDATE', 'on') == 'off' or not BottledModel.available(): return None
        model = BottledModel.get()
    if profile is None:
        from . import source_profile
        profile = source_profile.load(stack_path)
    texts, _ = model(messages(question, profile, as_of), k=1)
    db = sqlite3.connect(f'file:{Path(stack_path) / "semantic.sqlite3"}?mode=ro', uri=True)
    try: roster = db.execute('SELECT count(*) FROM patient').fetchone()[0]
    finally: db.close()
    cand = check(stack_path, profile, question, nl2sql._extract_sql(texts[0]), roster)
    if not cand['ok']: return None
    params = params_for(question, cand)
    return {'label': 'local_model', 'sql': params['query'], 'returns': params['returns'], 'model': getattr(model, 'identity', None)}
