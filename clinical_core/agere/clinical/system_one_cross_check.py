"""Answer-guard cross-check, System One first (Mike 2026-10-22: System One before any Claude model; Claude only escalates).

cross_check() asks BOTH System One families (Jev and the Perplexity decider) the same typed question system_one_gate.check
asks ("does what the executed plan computed answer the question as asked?"), each on its own, so the answer is checked by
two model families instead of one. The Jev request is byte-identical to system_one_gate.check's first step, so when both
run on one proposal Jev is charged once (provider-ledger cache hit).
  confirmed    both families say it answers the question (fully / partially), each with p >= AGERE_S1_CROSS_CONFIRM (default
               0.8) -> the answer is cross-checked; no Claude call
  weak         both lean yes but one is below that bar -> not a confirmation (annotated like unavailable)
  refuted      both say it answers a different question with p >= AGERE_S1_CROSS_REJECT (default 0.7) -> concrete
               contradiction: this proposal is rejected and the next proposer tries (heal, never block)
  split        the families disagree -> the existing independent Claude path (answer_guard.PendingCheck) runs, but only
               when Claude is available (no billing/rate outage earlier in the question)
  unavailable  a family failed, was not live or said unknown -> annotated; no Claude call
Costs are returned for answer_checks to charge to the cascade (cascade.spend).
"""
import os

VERSION = 'system-one-cross-check/1'
FAMILIES = ('jev', 'perplexity')
YES = {'answers_fully', 'answers_partially'}
NO = 'answers_a_different_question'


def reject_threshold():
    try:
        return float(os.environ.get('AGERE_S1_CROSS_REJECT', '0.7'))
    except ValueError:
        return 0.7


def confirm_threshold():
    try:
        return float(os.environ.get('AGERE_S1_CROSS_CONFIRM', '0.8'))
    except ValueError:
        return 0.8


def cross_check(stack, question, graph, result, *, ledger=None, as_of_date=None, s1=None):
    """-> {'verdict', 'votes': {family: {'label', 'p'} | {'error'}}, 'calls', 'cost_usd', 'version'}"""
    from .system_one_gate import CHECK_OPTIONS, as_of, render, s1 as load
    mod = s1 or load()
    when = as_of_date or as_of(stack)
    summary = render(question, graph, result)
    text = f'Question as asked: {question}\nDoes the computed result below answer this question as asked?'
    evidence = [{'id': 'computed', 'text': summary, 'date': when, 'note_id': 'executed_plan'}]
    votes, calls, usd = {}, 0, 0.0
    for fam in FAMILIES:
        try:
            r = mod.choice(text, CHECK_OPTIONS, evidence, as_of=when, provider=fam, ledger=ledger, allow_live=True, task_key='s1_answer_check')
        except Exception as exc:  # noqa: BLE001 - one family failing leaves 'unavailable', never an error
            votes[fam] = {'error': f'{type(exc).__name__}: {exc}'[:160]}; continue
        if r.get('status') == 'prepared' or not r.get('label'):
            votes[fam] = {'error': 'not answered (prepared / no live call)'}; continue
        probs = r.get('calibrated_probabilities') or r.get('probabilities') or {}
        votes[fam] = {'label': r['label'], 'p': round(float(probs.get(r['label'], 0.0)), 4), 'cache_hit': bool(r.get('cache_hit'))}
        calls += 0 if r.get('cache_hit') else 1
        usd += float(r.get('cost_usd') or 0.0)
    labels = [v.get('label') for v in votes.values()]
    if len(labels) == len(FAMILIES) and all(l in YES for l in labels):
        verdict = 'confirmed' if all(v['p'] >= confirm_threshold() for v in votes.values()) else 'weak'
    elif len(labels) == len(FAMILIES) and all(l == NO for l in labels) and all(v['p'] >= reject_threshold() for v in votes.values()):
        verdict = 'refuted'
    elif any(l in YES for l in labels) and any(l == NO for l in labels):
        verdict = 'split'
    else:
        verdict = 'unavailable'
    return {'verdict': verdict, 'votes': votes, 'calls': calls, 'cost_usd': round(usd, 6), 'version': VERSION}


def describe(out):
    return ', '.join(f"{f} {v['label'].replace('_', ' ')} (p={v['p']:.2f})" if 'label' in v else f"{f} unavailable"
                     for f, v in out['votes'].items())
