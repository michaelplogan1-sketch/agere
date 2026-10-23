"""System One checks a question spec against the question, part by part (docs/agere/QUESTION_SPEC.md, "How a spec is filled").

    verify(stack, question, spec, catalog, ledger=...) -> receipt

The spec's read-back (spec_readback.parts) gives one sentence per population, subject, measure, predicate, combination, window, grouping,
ordering and analysis, plus one completeness sentence for the whole read-back. Each sentence goes to System One as the EVIDENCE of one typed
question, "does this part match what the question asks for?", with the options yes / partly / no (and the shared unknown). A part is

  pass         System One (Jev, then Perplexity below the bar) says yes
  fail         it says no, or partly with high confidence: the part does not match the question
  unverified   neither provider cleared its bar (or the call failed / ran out of time)

The spec is `confirmed` only when every part passes. A failed part is repaired once (feedback() is the text the Haiku filler gets) or the
question is `unresolved`; it is never shipped silently. For spec-path answers this replaces the result-summary cross-check
(answer_checks); the decision-context and other mandatory checks still run. Every call is cached by request hash in the provider ledger.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor

from . import spec_readback

VERSION = 'spec-verify/1'
TASK = 's1_spec_part'
OPTIONS = {'yes': 'The step matches what the question asks for.',
           'partly': 'The step matches only in part: right idea but the wrong scope, window, level or detail, or something is missing.',
           'no': 'The step contradicts the question or does not correspond to anything it asks for.'}
HINTS = {
    'population': 'Is this who or what the question is about?',
    'subject': 'Is this the patient the question is about?',
    'measure': 'Is this what the question asks to be reported (a count, a list, an average, a share)?',
    'predicate': 'Is this one of the conditions the question puts on the population, with the same polarity (with / without)?',
    'structure': 'Does this combine the conditions the way the question does (and / or / not)?',
    'window': 'Does the question\'s time phrase mean exactly this window?',
    'grouping': 'Does the question ask for this breakdown?',
    'ordering': 'Does the question ask for this ordering or cut-off?',
    'analysis': 'Does the question ask for this analysis?',
    'completeness': 'Does this statement cover everything the question asks for, with nothing added and nothing missing?',
}


def accept_p():
    try: return float(os.environ.get('AGERE_SPEC_VERIFY_ACCEPT', '0.7'))
    except ValueError: return 0.7


def partly_p():
    try: return float(os.environ.get('AGERE_SPEC_VERIFY_PARTLY', '0.85'))
    except ValueError: return 0.85


PROVIDER = None  # test hook: a fake System One decide() used instead of the live providers


def decide_default(*args, **kwargs):
    if PROVIDER is not None: return PROVIDER(*args, **kwargs)
    from .system_one_gate import s1
    mod = s1()
    if mod is None: raise RuntimeError('System One is not installed')
    return mod.decide(*args, **kwargs)


def prompt(question, part):
    return (f'Question asked: {question}\nA system will answer it by doing the step in the evidence. {HINTS.get(part["kind"], HINTS["predicate"])} '
            f'Answer yes if the step matches, partly if it matches only in part, no if it contradicts the question.')


def check_part(question, part, *, as_of, ledger, decide):
    """-> the part with System One's label, probability, provider, cost and status (pass / fail / unverified)."""
    started = time.perf_counter()
    try:
        out = decide(prompt(question, part), OPTIONS, [{'id': part['id'], 'text': part['text'], 'date': as_of, 'note_id': part['id']}],
                     as_of=as_of, threshold=accept_p(), task_key=TASK, ledger=ledger, allow_live=True)
    except Exception as exc:  # noqa: BLE001 - heal, don't block: the part is unverified
        return {**part, 'label': 'unknown', 'p': 0.0, 'provider': None, 'status': 'unverified', 'error': f'{type(exc).__name__}: {str(exc)[:160]}',
                'calls': 0, 'cost_usd': 0.0, 'seconds': round(time.perf_counter() - started, 3)}
    from .system_one_gate import cost
    probs = out.get('calibrated_probabilities') or out.get('probabilities') or {}
    label = out.get('label') or 'unknown'
    p = float(probs.get(label, 0.0))
    decided = out.get('decided_by') is not None
    calls, usd = cost(out)
    if label == 'yes' and decided: status = 'pass'
    elif label == 'no' and decided: status = 'fail'
    elif label == 'partly' and decided and p >= partly_p(): status = 'fail'
    else: status = 'unverified'
    return {**part, 'label': label, 'p': round(p, 4), 'p_yes': round(float(probs.get('yes', 0.0)), 4), 'provider': out.get('decided_by') or out.get('provider'),
            'status': status, 'calls': calls, 'cost_usd': round(usd, 6), 'cache_hit': bool(out.get('cache_hit')),
            'seconds': round(time.perf_counter() - started, 3)}


def verify(stack, question, spec, catalog, *, ledger=None, as_of=None, decide=None, workers=None, deadline_seconds=None, parts=None):
    """System One against every part of the spec. -> {'version', 'verdict': confirmed | failed | unverified, 'confirmed': bool,
    'parts': [...], 'failed': [ids], 'unverified': [ids], 'score': mean P(yes), 'calls', 'cost_usd', 'seconds', 'read_back'}"""
    started = time.perf_counter()
    decide = decide or decide_default
    as_of = str(as_of or (getattr(catalog, 'identity', None) or {}).get('as_of') or '')[:10] or None
    items = list(parts) if parts is not None else spec_readback.parts(spec, catalog, question, as_of)
    read_back = spec_readback.read_back(spec, catalog, question, as_of)
    items.append({'id': 'completeness', 'kind': 'completeness', 'text': read_back, 'quotes': []})
    path = getattr(ledger, 'path', ledger) if ledger is not None else None  # each worker opens its own connection to the same ledger
    path = str(path) if path else None
    workers = workers or int(os.environ.get('AGERE_SPEC_VERIFY_WORKERS', '6'))
    deadline = None if deadline_seconds is None else started + deadline_seconds

    def run(part):
        if deadline is not None and time.perf_counter() >= deadline:
            return {**part, 'label': 'unknown', 'p': 0.0, 'provider': None, 'status': 'unverified', 'error': 'time', 'calls': 0, 'cost_usd': 0.0}
        return check_part(question, part, as_of=as_of, ledger=path, decide=decide)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(items)))) as pool:
        checked = list(pool.map(run, items))
    failed = [p['id'] for p in checked if p['status'] == 'fail']
    unverified = [p['id'] for p in checked if p['status'] == 'unverified']
    verdict = 'failed' if failed else 'confirmed' if not unverified else 'unverified'
    scored = [p.get('p_yes', 0.0) for p in checked]
    return {'version': VERSION, 'verdict': verdict, 'confirmed': verdict == 'confirmed', 'parts': checked, 'failed': failed, 'unverified': unverified,
            'score': round(sum(scored) / len(scored), 4) if scored else 0.0, 'calls': sum(p.get('calls', 0) for p in checked),
            'cost_usd': round(sum(p.get('cost_usd', 0.0) for p in checked), 6), 'seconds': round(time.perf_counter() - started, 3),
            'read_back': read_back}


def feedback(receipt):
    """What the filler is told when parts failed: each failed part's sentence, the question words it was bound to, and the verdict."""
    return [{'part': p['id'], 'kind': p['kind'], 'the_step_read_back_as': p['text'], 'question_words': p.get('quotes') or [],
             'system_one_said': p['label'], 'confidence': p['p']} for p in receipt['parts'] if p['status'] == 'fail']


def summary(receipt):
    """The receipt trimmed for an answer: verdict, counts and each part's sentence and verdict."""
    return {'version': receipt['version'], 'verdict': receipt['verdict'], 'confirmed': receipt['confirmed'], 'score': receipt['score'],
            'calls': receipt['calls'], 'cost_usd': receipt['cost_usd'], 'seconds': receipt['seconds'],
            'parts': [{k: p.get(k) for k in ('id', 'kind', 'text', 'label', 'p', 'provider', 'status')} for p in receipt['parts']]}
