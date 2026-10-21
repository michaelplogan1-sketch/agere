"""Local normalizer: rewrite a client's wording into precise English the free parser reads completely.

A fine-tuned local model (Qwen3-4B + LoRA adapter, MLX, on this machine; no network) proposes one canonical question or
UNSUPPORTED. The proposal is used only when deterministic guards show it keeps the client's numbers, years and negation,
and the free parser then compiles it exactly like any other question. The answer names the canonical wording it used.
Synthetic warehouse only.
"""
import os
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE_MODEL = ROOT / 'models' / 'qwen3-4b'
ADAPTER = Path(os.environ.get('AGERE_NORMALIZER_ADAPTER') or ROOT / 'artifacts' / 'agere-local-planner-v1' / 'adapter')
VERSION = 'local-normalizer/1'
UNSUPPORTED = 'UNSUPPORTED'
SYSTEM = ('Rewrite the analytics question into the precise wording the Agere parser reads. Keep every number, year, '
          'negation, condition, population, source, status, grouping and metric. If the question cannot be expressed '
          f'that way, answer {UNSUPPORTED}. Answer with the rewritten question only.')

NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen '
    'eighteen nineteen twenty'.split())}
NUMBER_WORDS.update({'thirty': '30', 'forty': '40', 'fifty': '50', 'sixty': '60', 'seventy': '70', 'eighty': '80',
                     'ninety': '90', 'hundred': '100'})
NEGATION = re.compile(r"\bw/o\b|\b(?:no|not|never|without|neither|nor|none|missing|missed|didn't|doesn't|don't|haven't|hasn't|"
                      r"excluding|exclude|except|lack(?:ing)?|gaps?)\b|n't\b", re.I)


def numbers(text):
    """The set of numeric constraints in a question (digits and number words; 2029-30 is 2029 and 2030)."""
    t = text.lower().replace(',', '')
    found = set()
    for a, b in re.findall(r'\b(20\d\d)\s*[-/–]\s*(\d{2,4})\b', t):
        found |= {a, b if len(b) == 4 else a[:2] + b}
        t = t.replace(f'{a}-{b}', ' ')
    # Digits inside a name or code (A1c, PHQ-9, SGLT2, COVID-19) are not constraints.
    found |= {str(float(n)).rstrip('0').rstrip('.') for n in re.findall(r'(?<![a-z\d.])(?<![a-z]-)\d+(?:\.\d+)?(?![a-z]*\d)', t)}
    found |= {NUMBER_WORDS[w] for w in re.findall(r'[a-z]+', t) if w in NUMBER_WORDS}
    return found


def guard(client, canonical):
    """None when the canonical keeps the client's numbers and negation, else the reason it was rejected."""
    if numbers(client) != numbers(canonical):
        return f'numbers differ: {sorted(numbers(client))} vs {sorted(numbers(canonical))}'
    if bool(NEGATION.search(client)) != bool(NEGATION.search(canonical)):
        return 'negation differs'
    return None


def prompt(question):
    return [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': question}]


_MODEL = {'loaded': None, 'failed': None}
_LOCK = threading.Lock()


def available():
    return ADAPTER.joinpath('adapters.safetensors').exists() and BASE_MODEL.exists()


def _load():
    with _LOCK:
        if _MODEL['loaded'] is None and _MODEL['failed'] is None:
            try:
                from mlx_lm import load
                _MODEL['loaded'] = load(str(BASE_MODEL), adapter_path=str(ADAPTER))
            except Exception as exc:  # noqa: BLE001 - a missing local model means the normalizer declines
                _MODEL['failed'] = repr(exc)
        return _MODEL['loaded']


def propose(question):
    """The model's canonical rewording, UNSUPPORTED, or None when no local model is installed."""
    if not available(): return None
    loaded = _load()
    if loaded is None: return None
    from mlx_lm import generate
    from mlx_lm.sample_utils import make_sampler
    model, tokenizer = loaded
    text = tokenizer.apply_chat_template(prompt(question), add_generation_prompt=True, tokenize=False,
                                         enable_thinking=False)
    with _LOCK:
        out = generate(model, tokenizer, prompt=text, max_tokens=96, sampler=make_sampler(temp=0.0), verbose=False)
    out = re.sub(r'<think>.*?</think>', '', out, flags=re.S).strip().splitlines()
    return out[0].strip() if out else None


def disclosure(interpretation):
    return f"Interpreted as \"{interpretation['normalized_question']}\" (local normalizer, {VERSION}); the free parser compiled that wording."
