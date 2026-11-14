"""Disposable MLX process: native crashes/timeouts cannot strand the HTTP lock.

Local typed interpreter. When the training orchestrator has promoted a typed adapter (train_all learner 'typed', pointer
<runs>/*/promoted/typed.json, or AGERE_TYPED_ADAPTER), the worker serves it with the SHORT prompt it was trained on
(tools/training/datasets.typed_messages: TYPED_SYSTEM + only the likely tables, ~1-2k tokens instead of the ~35k-token
runtime planner prompt): one generation, then the shared compiler recompiles the request against the original question
and the literal-question check reads the compiled SQL; its verdict is the local review the cascade tier requires (the
cascade recompiles and literal-checks again, then answer_checks runs after execution). If the adapter is missing, its
output does not parse/compile, or the check finds a dropped phrase, the worker falls back to the long-prompt local
interpreter (shared_planner.interpret with the base model) unless AGERE_LOCAL_TYPED_FALLBACK=off. That fallback no longer sends the
~48k-token catalog: local_prompt.Shaper trims each prompt to AGERE_LOCAL_TYPED_PROMPT_TOKENS (default 8000) and the model asks
for any missing table/rule/concept (need_*), which adds only that fragment.

The process is long-lived (`--serve`, JSON lines on stdin/stdout, see shared_planner.PersistentWorker): models load once and
stay resident, MLX buffers go back after every generation, and a prompt whose attention cache would exceed the 4 GB budget is
declined with a reason (never shortened). Still disposable: the server kills it on timeout and starts a fresh one.
"""
import gc
import json
import os
import re
import time
from pathlib import Path
import sys

VERSION = 'local-typed-worker/3'
ROOT = Path(__file__).resolve().parents[3]


def _base():
    return Path(os.environ.get('AGERE_BASE_MODEL') or next((p / 'models' / 'qwen3-4b' for p in (ROOT, *ROOT.parents)
                                                           if (p / 'models' / 'qwen3-4b').exists()), ROOT / 'models' / 'qwen3-4b'))


def typed_adapter():
    """The promoted typed adapter directory (held-out win in train_all), or None."""
    if os.environ.get('AGERE_COMPONENT_RELEASE_ROOT'):
        from .adapter_learning import runtime_adapter
        return runtime_adapter('typed',_base())
    if os.environ.get('AGERE_TYPED_ADAPTER'):
        p = Path(os.environ['AGERE_TYPED_ADAPTER'])
        return p if (p / 'adapters.safetensors').exists() else None
    for base in (ROOT, *ROOT.parents):
        runs = base / 'artifacts' / 'training' / 'runs'
        if not runs.exists(): continue
        for pointer in sorted(runs.glob('*/promoted/typed.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                adapter = Path(json.loads(pointer.read_text())['adapter'])
            except (OSError, ValueError, KeyError):
                continue
            adapter = adapter if adapter.is_absolute() else base / adapter
            if (adapter / 'adapters.safetensors').exists(): return adapter
        break
    return None


def short_messages(catalog, question):
    """The training-time typed prompt with the likely tables for this question (schema_prefetch)."""
    from tools.training.datasets import typed_messages
    from .schema_prefetch import likely
    tables = likely(catalog, question, operations=0, streams=0)['tables']
    return typed_messages(question, catalog.tables, tables), tables


def decode(text):
    raw=re.sub(r'^```(?:json)?\s*|\s*```$', '', re.sub(r'<think>.*?</think>', '', text, flags=re.S).strip())
    start=raw.find('{')
    if start<0:raise ValueError('Typed output contains no JSON object')
    # Ignore a formatting preamble only. Never skip a malformed outer object to
    # select a nested plan, or silently discard a second plan/trailing content.
    value,end=json.JSONDecoder().raw_decode(raw[start:])
    if raw[start+end:].strip():raise ValueError('Typed output has trailing content or multiple plans')
    if not isinstance(value, dict): raise ValueError('typed output must be an object')
    return value


def compile_value(catalog, question, value, population):
    from . import request_plan, request_program, general_analysis
    if 'cohort_family_slots' in value: raise ValueError('cohort-family slots are served by the narrow rung, not this worker')
    if set(value) == {'program'}: return request_program.prepare(catalog, question, value['program'], population=population)
    if set(value) == {'analysis'}: return general_analysis.prepare(catalog, question, value['analysis'], population=population)
    plan = request_plan.normalize_wire(catalog, question, value)
    return request_plan.compile_request(catalog, question, plan, population=population)


def interpret_short(catalog, question, population, generate, adapter=None):
    """generate(messages) -> text. -> (contract, interpretation) with a compile + literal-check review."""
    from .literal_check import missing
    from .proposers import evidence_of, request_unresolved
    messages, tables = short_messages(catalog, question)
    started = time.perf_counter()
    text = generate(messages)
    seconds = round(time.perf_counter() - started, 2)
    contract = compile_value(catalog, question, decode(text), population)
    gaps = missing(question, evidence_of(contract))
    unresolved = request_unresolved(contract)
    review = {'equivalent': not gaps and not unresolved, 'issues': list(gaps) + [f'unresolved: {u}' for u in unresolved][:8],
              'method': 'typed_adapter_compile_literal_check'}
    prompt_chars = sum(len(m['content']) for m in messages)
    return contract, {'interpreter': 'qwen_typed_adapter', 'review': 'typed_adapter_compile_literal_check', 'semantic_review': review,
                      'attempts': [{'model': f'qwen3-4b+{Path(adapter).name if adapter else "adapter"}', 'seconds': seconds, 'api_tokens': 0,
                                    'prompt': 'datasets.typed_messages', 'prompt_chars': prompt_chars, 'tables': tables,
                                    'formatting_preamble_removed':not text.lstrip().startswith(('{','```','<think>'))}],
                      'execution_accepted': False, 'worker': VERSION}


class LocalDeclined(ValueError):
    """The local model will not run this prompt (memory budget); the reason is the message."""


def _release():
    """Hand freed MLX buffers back so the next generation (or the server's own models) can use the memory."""
    try:
        import mlx.core as mx
    except ImportError:
        return
    mx.clear_cache()


def fit_check(n_tokens, max_tokens, kv_per_token, budget_gb=None):
    """Decline (never shorten) when one decode's attention cache over prompt + output would exceed the shared-memory budget."""
    from .bottled import KV_BUDGET_GB
    budget = (budget_gb or KV_BUDGET_GB) * 2**30
    if kv_per_token and (n_tokens + max_tokens) * kv_per_token > budget:
        raise LocalDeclined(f'local model declined: a {n_tokens}-token prompt exceeds the {budget / 2**30:g} GB attention-cache budget')


_RESIDENT = {}


def resident(key, build):
    """Load once per process: build() runs only when key is not already resident; a new key evicts the old one (one model set in memory)."""
    if key not in _RESIDENT:
        _RESIDENT.clear(); gc.collect(); _release()
        _RESIDENT[key] = build()
    return _RESIDENT[key]


def completion_allowance(prompt_tokens,config,kv_per_token,budget_gb=None):
    """Complete within the real checkpoint/attention-cache contract, never a 700-token quota."""
    from .bottled import KV_BUDGET_GB
    cfg=config.get('text_config',config)
    context=cfg.get('max_position_embeddings')
    if type(context)is not int or context<1:raise LocalDeclined('Native checkpoint context capacity is unavailable')
    if type(prompt_tokens)is not int or prompt_tokens<0:raise ValueError('Prompt token count required')
    cache_tokens=int((budget_gb or KV_BUDGET_GB)*2**30/kv_per_token)if kv_per_token else context
    capacity=min(context,cache_tokens)-prompt_tokens
    if capacity<1:raise LocalDeclined('Complete prompt exhausts the native context/attention-cache capacity; prompt was not shortened')
    return capacity


def _build_typed(adapter):
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    from mlx_lm import load, generate
    from mlx_lm.sample_utils import make_sampler
    import mlx.core as mx
    from .bottled import MLX_CACHE_GB, kv_bytes_per_token
    mx.set_cache_limit(int(MLX_CACHE_GB * 2**30))
    model, tok = load(str(_base()), adapter_path=str(adapter))
    kv = kv_bytes_per_token(_base())

    from .constrained_decoding import JsonGrammar, processors

    def run(messages):
        prompt = tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False, enable_thinking=False)
        n_prompt=len(tok.encode(prompt, add_special_tokens=False))
        capacity=completion_allowance(n_prompt,json.loads((_base()/'config.json').read_text()),kv)
        fit_check(n_prompt,capacity,kv)
        try:
            # grammar-constrained: the typed request is one JSON object (constrained_decoding); decode() still parses it
            return generate(model, tok, prompt=prompt, max_tokens=capacity, sampler=make_sampler(temp=0.0), verbose=False,
                            logits_processors=processors(tok, JsonGrammar(root='object')))
        finally:
            _release()
    return run


def mlx_generate(adapter):
    return resident(('typed', str(adapter)), lambda: _build_typed(adapter))


class _Guarded:
    """The long-prompt base model with the same memory discipline: budget check before, buffers returned after."""
    def __init__(self, model):
        self._m = model

    def __getattr__(self, name):
        return getattr(self._m, name)

    def __call__(self, system, user, max_tokens=400, **kw):
        from .bottled import kv_bytes_per_token
        from .nl2sql import MODEL_DIR
        tok = self._m.tokenizer
        prompt = tok.apply_chat_template([{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                                         tokenize=False, add_generation_prompt=True, enable_thinking=False)
        fit_check(len(tok.encode(prompt, add_special_tokens=False)), max_tokens, kv_bytes_per_token(MODEL_DIR))
        try:
            return self._m(system, user, max_tokens=max_tokens, **kw)
        finally:
            _release()


def long_prompt(catalog, question, population):
    from .shared_planner import interpret
    from .nl2sql import LocalModel
    from .local_prompt import budget
    # the small model gets the shaped prompt (only this question's tables, vocabulary and rule parts), not the ~48k-token catalog
    return interpret(catalog, question, population=population, model=_Guarded(LocalModel.get()), force_model=True, prompt_budget=budget())


def handle(item, *, generate_factory=mlx_generate, fallback=long_prompt):
    from .request_plan import Catalog
    c = item['catalog']
    catalog = Catalog(Path(c['target']), c['tables'], c['identity'], c['stream_fields'])
    adapter, short_error = typed_adapter(), None
    if adapter is None and os.environ.get('AGERE_LOCAL_TYPED_FALLBACK','on')=='off':
        raise LocalDeclined('No qualified typed adapter is available; base-model fallback is disabled')
    if adapter is not None:
        try:
            contract, interpretation = interpret_short(catalog, item['question'], item['population'], generate_factory(adapter), adapter)
            if interpretation['semantic_review']['equivalent'] or os.environ.get('AGERE_LOCAL_TYPED_FALLBACK', 'on') == 'off':
                return contract, interpretation
            short_error = 'literal check: ' + '; '.join(interpretation['semantic_review']['issues'])[:300]
        except Exception as exc:  # noqa: BLE001 - the long-prompt interpreter still runs (heal, don't block)
            short_error = f'{type(exc).__name__}: {exc}'[:300]
            if os.environ.get('AGERE_LOCAL_TYPED_FALLBACK', 'on') == 'off': raise
    contract, interpretation = fallback(catalog, item['question'], item['population'])
    if short_error: interpretation = {**interpretation, 'typed_adapter_declined': short_error}
    return contract, interpretation


def serve(stdin, stdout, handler=None):
    """One JSON request per line in, one JSON reply per line out: {'ok': [contract, interpretation]} | {'declined': reason} |
    {'error': text}. AGERE_* settings travel with each request so the server's switches stay live in a long-lived worker."""
    handler = handler or handle
    for line in stdin:
        if not line.strip(): continue
        try:
            item = json.loads(line)
            env = item.pop('env', None)
            if env is not None:
                for k in [k for k in os.environ if k.startswith('AGERE_') and k not in env]: del os.environ[k]
                os.environ.update(env)
            reply = {'ok': handler(item)}
        except LocalDeclined as exc:
            reply = {'declined': str(exc)}
        except Exception as exc:  # noqa: BLE001 - the server reports the failure; the worker stays up
            reply = {'error': f'{type(exc).__name__}: {exc}'[:500]}
        finally:
            _release()
        stdout.write(json.dumps(reply, default=str) + '\n'); stdout.flush()


def main():
    if '--serve' in sys.argv[1:]:
        out = os.fdopen(os.dup(1), 'w')  # replies own the real stdout; library prints go to stderr
        os.dup2(2, 1); sys.stdout = sys.stderr
        print(json.dumps({'ready': VERSION}), file=out, flush=True)
        return serve(sys.stdin, out)
    item = json.load(sys.stdin)
    print(json.dumps(handle(item), default=str))


if __name__ == '__main__':
    main()
