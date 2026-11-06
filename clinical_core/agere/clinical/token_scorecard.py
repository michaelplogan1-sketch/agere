"""Token-economics scorecard over per-question cost telemetry (cost_telemetry), Mike's token rules as numbers.

  answered_no_llm        share of questions answered with no model at all (no paid call, no local Qwen tier answered)
  answered_no_paid_call  share answered with no paid call (local models allowed)
  llm_calls_per_question by model (Claude, Jev, Perplexity), plus totals
  cost_per_question      mean priced USD per question and per answered question; questions with unpriced calls are counted
                         separately (unknown is never $0)
  system_one             how often Jev/Perplexity decided something (answered, routed, checked, picked a fallback), and
                         questions where a Claude call came before any System One call (System One first is the rule)
  opus_without_failed_sonnet  Opus calls with no failed Sonnet review before them in the same question: each is a FAILURE
  stages                 median seconds per stage; slowest cascade tiers
check(card) lists the rule violations (an empty list passes). Pure functions; no I/O.
"""
import statistics
from collections import Counter, defaultdict

VERSION = 'token-scorecard/1'
CLAUDE_HINT = ('claude',)
S1_PROVIDERS = ('jev', 'perplexity', 'perplexity_agent')


def _answered(t): return t.get('status') == 'answered'


def system_one_first_violations(t):
    """True when this question's first Claude call precedes every System One call (or none happened)."""
    seq = [a['provider'] for a in t.get('attempts') or [] if not a.get('cache_hit')]
    if 'claude' not in seq: return False
    first = seq.index('claude')
    return not any(p in S1_PROVIDERS for p in seq[:first])


def scorecard(telemetries, *, question_set=None):
    rows = [t for t in telemetries if isinstance(t, dict)]
    n = len(rows)
    if not n: return {'version': VERSION, 'questions': 0}
    answered = [t for t in rows if _answered(t)]
    by_model, s1_kinds, tiers = Counter(), Counter(), Counter()
    for t in rows:
        by_model.update(t.get('calls_by_model') or {})
        tiers[t.get('answered_by') or 'none'] += 1
        for d in t.get('system_one_decided') or []: s1_kinds[d.split(':')[0]] += 1
    known = [t for t in rows if t.get('usd_known', True)]
    known_answered = [t for t in answered if t.get('usd_known', True)]
    opus = [(t['question_id'], t['opus_without_failed_sonnet']) for t in rows
            if t.get('opus_without_failed_sonnet') and t.get('opus_review_order_is_policy_failure', True)]
    stages = defaultdict(list)
    tier_seconds = defaultdict(list)
    for t in rows:
        for k, v in (t.get('stages') or {}).items():
            if isinstance(v, (int, float)): stages[k].append(v)
        for c in (t.get('stages') or {}).get('cascade') or []:
            if isinstance(c.get('seconds'), (int, float)): tier_seconds[c['tier']].append(c['seconds'])
    usd = [t.get('api_usd') or 0.0 for t in known]
    card = {
        'version': VERSION, 'question_set': question_set, 'questions': n, 'answered': len(answered),
        'answered_no_llm': round(sum(1 for t in answered if t.get('llm_free')) / n, 4),
        'answered_no_paid_call': round(sum(1 for t in answered if t.get('no_paid_call')) / n, 4),
        'answered_by_local_model': round(sum(1 for t in answered if t.get('local_llm')) / n, 4),
        'llm_calls_per_question': {m: round(c / n, 4) for m, c in sorted(by_model.items())},
        'llm_calls_total': dict(sorted(by_model.items())),
        'claude_calls_per_question': round(sum(t.get('claude_calls', 0) for t in rows) / n, 4),
        'system_one_calls_per_question': round(sum(t.get('system_one_calls', 0) for t in rows) / n, 4),
        'cost_per_question_usd': round(sum(usd) / len(known), 6) if known else None,
        'cost_per_answered_usd': round(sum(t.get('api_usd') or 0.0 for t in known_answered) / len(known_answered), 6) if known_answered else None,
        'total_usd_known': round(sum(usd), 6), 'questions_with_unpriced_calls': n - len(known),
        'tokens': {k: sum((t.get('tokens') or {}).get(k, 0) or 0 for t in rows)
                   for k in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens')},
        'system_one': {'decided_share': round(sum(1 for t in rows if t.get('system_one_decided')) / n, 4),
                       'decided_by_kind': dict(s1_kinds),
                       'answered_by_system_one': round(sum(1 for t in answered if t.get('answered_by') in ('atomic_system_one', 'typed_decider')) / n, 4),
                       'claude_before_system_one': [t['question_id'] for t in rows if system_one_first_violations(t)]},
        'opus_without_failed_sonnet': {'failures': len(opus), 'calls': sum(len(x) for _, x in opus),
                                       'questions': [q for q, _ in opus]},
        'answered_by': dict(tiers.most_common()),
        'median_stage_seconds': {k: round(statistics.median(v), 3) for k, v in sorted(stages.items())},
        'slowest_tiers': sorted(({'tier': k, 'median_seconds': round(statistics.median(v), 3), 'runs': len(v)}
                                 for k, v in tier_seconds.items()), key=lambda r: -r['median_seconds'])[:8],
    }
    card['violations'] = check(card)
    return card


def check(card):
    """Rule violations on a scorecard: Opus without a failed Sonnet review, Claude before System One, unpriced calls."""
    out = []
    if (card.get('opus_without_failed_sonnet') or {}).get('failures'):
        out.append(f"{card['opus_without_failed_sonnet']['failures']} question(s) called Opus without a failed Sonnet review first")
    early = (card.get('system_one') or {}).get('claude_before_system_one') or []
    if early: out.append(f'{len(early)} question(s) called Claude before any System One (Jev/Perplexity) judgment')
    if card.get('questions_with_unpriced_calls'):
        out.append(f"{card['questions_with_unpriced_calls']} question(s) have paid calls without a priced cost")
    return out


def render(card):
    """Plain-text scorecard."""
    if not card.get('questions'): return 'No questions recorded.'
    lines = [f"Token-economics scorecard: {card['questions']} questions ({card['answered']} answered)",
             f"  answered with no LLM            {card['answered_no_llm']:.1%}",
             f"  answered with no paid call      {card['answered_no_paid_call']:.1%}",
             f"  LLM calls per question          " + ', '.join(f'{m} {v}' for m, v in card['llm_calls_per_question'].items()) or 'none',
             f"  cost per question               ${card['cost_per_question_usd'] or 0:.4f} (answered ${card['cost_per_answered_usd'] or 0:.4f}); "
             f"unpriced: {card['questions_with_unpriced_calls']}",
             f"  Jev/Perplexity decided          {card['system_one']['decided_share']:.1%} {card['system_one']['decided_by_kind']}",
             f"  Opus without failed Sonnet      {card['opus_without_failed_sonnet']['failures']} question(s) = failures",
             f"  answered by                     {card['answered_by']}"]
    lines += ['  VIOLATION: ' + v for v in card['violations']]
    return '\n'.join(lines)
