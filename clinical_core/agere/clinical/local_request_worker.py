"""Disposable MLX process: native crashes/timeouts cannot strand the HTTP lock.

Local typed interpreter. When the training orchestrator has promoted a typed adapter (train_all learner 'typed', pointer
<runs>/*/promoted/typed.json, or AGERE_TYPED_ADAPTER), the worker serves it with the SHORT prompt it was trained on
(tools/training/datasets.typed_messages: TYPED_SYSTEM + only the likely tables, ~1-2k tokens instead of the ~35k-token
runtime planner prompt): one generation, then the shared compiler recompiles the request against the original question
and the literal-question check reads the compiled SQL; its verdict is the local review the cascade tier requires (the
cascade recompiles and literal-checks again, then answer_checks runs after execution). If the adapter is missing, its
output does not parse/compile, or the check finds a dropped phrase, the worker falls back to the long-prompt local
interpreter (shared_planner.interpret with the base model) unless AGERE_LOCAL_TYPED_FALLBACK=off.
"""
import json
import os
import re
import time
from pathlib import Path
import sys

VERSION = 'local-typed-worker/2'
ROOT = Path(__file__).resolve().parents[3]


def _base():
    return Path(os.environ.get('AGERE_BASE_MODEL') or next((p / 'models' / 'qwen3-4b' for p in (ROOT, *ROOT.parents)
                                                           if (p / 'models' / 'qwen3-4b').exists()), ROOT / 'models' / 'qwen3-4b'))


def typed_adapter():
    """The promoted typed adapter directory (held-out win in train_all), or None."""
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
    value = json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', re.sub(r'<think>.*?</think>', '', text, flags=re.S).strip()))
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
                                    'prompt': 'datasets.typed_messages', 'prompt_chars': prompt_chars, 'tables': tables}],
                      'execution_accepted': False, 'worker': VERSION}


def mlx_generate(adapter):
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    from mlx_lm import load, generate
    from mlx_lm.sample_utils import make_sampler
    model, tok = load(str(_base()), adapter_path=str(adapter))

    def run(messages):
        prompt = tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False, enable_thinking=False)
        return generate(model, tok, prompt=prompt, max_tokens=700, sampler=make_sampler(temp=0.0), verbose=False)
    return run


def long_prompt(catalog, question, population):
    from .shared_planner import interpret
    from .nl2sql import LocalModel
    return interpret(catalog, question, population=population, model=LocalModel.get(), force_model=True)


def handle(item, *, generate_factory=mlx_generate, fallback=long_prompt):
    from .request_plan import Catalog
    c = item['catalog']
    catalog = Catalog(Path(c['target']), c['tables'], c['identity'], c['stream_fields'])
    adapter, short_error = typed_adapter(), None
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


def main():
    item = json.load(sys.stdin)
    print(json.dumps(handle(item), default=str))


if __name__ == '__main__':
    main()
