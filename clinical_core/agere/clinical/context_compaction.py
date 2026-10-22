"""Constraint-preserving context compaction: fit a model context to a native token budget without losing what the question depends on.

Why: context_builder packs whole units and, when the packet does not fit the model's tokens, refuses (for_model raised "Native
model token budget exceeded"). Refusing walls the user out. Naive compaction is worse: "Lost in Compaction" (arXiv 2608.11242)
shows summaries silently dropping the original constraints. This module heals the overflow and makes the loss auditable
(Agere research review 2026-10-20, item 6; LCM-style recoverability, papers.voltropy.com/LCM):

  PINNED   structured fields compaction can never touch: the original question verbatim and every requirement stated in it
           (requirement_ledger: numbers, comparators, qualifiers, AND/OR trees), the scope (streams, concepts, cohort and date
           window), action permissions (dispatch / clinical-decision flags and the limits text), evidence versions (snapshot,
           source digest, record hashes of every retained unit), missing-evidence and opposing-report fields.
  TIERS    each evidence unit is kept whole (t0), then trimmed (t1: text cut to its head, long field values cut, annotations and
           provenance kept), then reduced to a field digest (t2: id, date, kind, concept/assertion pairs, protected reasons;
           no text), then elided (t3: only a pointer). Demotion order: oldest, least relevant first; a demotion that would not make the
           unit smaller is skipped. Every non-protected unit goes to t3 before any protected unit is touched. A protected unit
           (qualified or historical annotation, exception/stop/decline language, requested concept) is trimmed, then digested, and
           only as a last resort reduced to a protected pointer that keeps its protected reasons and concept/assertion pairs
           (negative evidence stays visible as a pointer, never silently gone). If even that cannot fit, fit=False.
  POINTERS every demoted unit carries an expansion pointer {source_ref, field, start, end, id}; the originals stay in the context
           receipt and the record store, so nothing is lost, only deferred. Digests are field extracts, never generated text:
           retrieval aids, not accepted evidence (retrieval_aid_only).
  VERIFY   verify() re-reads the compacted context and fails when a pinned field changed, a number/date of the question is
           missing, a protected unit vanished or lost its reasons, a unit is neither retained nor pointed to, or a retained unit's record hash
           differs from the original. compact() never returns a context that fails verification.

compact(context, count_tokens, max_tokens) -> {'context', 'fit', 'tokens', 'before_tokens', 'tiers', 'verified', 'failures'}
No model calls; synthetic data only.
"""
import copy
import json
import re

VERSION = 'constraint-preserving-compaction/1'
PINNED_KEYS = ('schema', 'synthetic', 'patient_id', 'snapshot', 'task', 'limits', 'missing_evidence', 'opposing_report_concepts',
               'documentary_requirements', 'versioned_program_context')
T1_TEXT = 240
T1_VALUE = 80
TIER_NAMES = ('t0_whole', 't1_trimmed', 't2_digest', 't3_pointer')


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def pinned_block(context, question=None):
    """The fields compaction cannot erase, with the requirement ledger of the original question beside them."""
    pins = {k: copy.deepcopy(context[k]) for k in PINNED_KEYS if k in context}
    q = question if question is not None else (context.get('task') or {}).get('question') or ''
    try:
        from . import requirement_ledger
        reqs = [{k: r[k] for k in ('id', 'kind', 'text') if k in r} for r in requirement_ledger.build(q)]
    except Exception:  # noqa: BLE001 - the ledger is an addition to the pins, never a reason to fail the compaction
        reqs = []
    pins['question_requirements'] = reqs
    return pins


def _pointer(unit):
    return {'source_ref': unit.get('source_ref'), 'field': unit.get('field'), 'start': unit.get('start'), 'end': unit.get('end'), 'id': unit.get('id')}


def _date(unit, sources):
    s = (sources.get(unit.get('source_ref')) or {}).get('source') or {}
    return s.get('event_date') or ''


def _short(v, n):
    if isinstance(v, str) and len(v) > n: return v[:n] + '...'
    if isinstance(v, dict): return {k: _short(x, n) for k, x in v.items()}
    if isinstance(v, list): return [_short(x, n) for x in v[:8]]
    return v


def tier(unit, level):
    """One evidence unit at a tier (0 whole .. 2 digest). Tier 3 is a pointer entry, not a unit."""
    if level == 0: return unit
    ptr = _pointer(unit)
    if level == 1:
        out = {k: v for k, v in unit.items() if k not in ('text', 'reported_fields')}
        if 'text' in unit: out['text'] = _short(unit['text'], T1_TEXT)
        if 'reported_fields' in unit: out['reported_fields'] = _short(unit['reported_fields'], T1_VALUE)
        out.update(compacted='t1_trimmed', expand=ptr)
        return out
    return {'id': unit.get('id'), 'source_ref': unit.get('source_ref'), 'kind': unit.get('kind'), 'field': unit.get('field'),
            'concepts': sorted({(a.get('concept'), a.get('assertion')) for a in unit.get('annotations') or []}),
            'protected_reasons': unit.get('protected_reasons') or [], 'compacted': 't2_digest', 'retrieval_aid_only': True, 'expand': ptr}


def _priority(unit, sources, concepts):
    """Higher = keep whole longer: protected first, then requested concepts, then the most recent."""
    prot = bool(unit.get('protected_reasons'))
    wanted = any(a.get('concept') in concepts for a in unit.get('annotations') or [])
    return (prot, wanted, _date(unit, sources), unit.get('id') or '')


ID = 12                                   # hex characters of a unit id kept in a pointer (unique within one patient's context)
COLUMNS = ('id', 'source_ref', 'start', 'end', 'reasons', 'concepts')


def assemble(context, units, levels, sources, requirements=()):
    """The context with each unit at its tier; tier-3 units collapse into one compact pointer table (columns + rows; protected
    reasons by index into a legend), so a pointer costs a few tokens while staying resolvable."""
    evidence, rows, legend = [], [], []
    for u, lv in zip(units, levels):
        if lv < 3: evidence.append(tier(u, lv)); continue
        reasons = [legend.index(r) if r in legend else (legend.append(r) or len(legend) - 1) for r in (u.get('protected_reasons') or [])]
        concepts = sorted({f"{a.get('concept')}:{a.get('assertion')}" for a in u.get('annotations') or []}) if reasons else []
        rows.append([(u.get('id') or '')[:ID], u.get('source_ref'), u.get('start'), u.get('end'), reasons, concepts])
    keep_refs = {u.get('source_ref') for u, lv in zip(units, levels) if lv < 3}
    out = {k: copy.deepcopy(v) for k, v in context.items() if k not in ('evidence', 'sources')}
    out['sources'] = {r: v for r, v in sources.items() if r in keep_refs}
    out['evidence'] = evidence
    out['question_requirements'] = list(requirements)
    out['compaction'] = {'schema': VERSION, 'tiers': {n: sum(1 for lv in levels if lv == i) for i, n in enumerate(TIER_NAMES)},
                         'elided': {'columns': list(COLUMNS), 'rows': rows, 'reasons_legend': legend},
                         'expansion': 'originals remain in the context receipt and record store; fetch by pointer (id prefix + source_ref)',
                         'retrieval_aid_only': True, 'pinned_fields_untouched': list(PINNED_KEYS)}
    return out


def pointers(compacted):
    """-> {id prefix: {'source_ref', 'start', 'end', 'protected_reasons': [...], 'concepts': [...]}} from a compacted context."""
    table = (compacted.get('compaction') or {}).get('elided') or {'rows': [], 'reasons_legend': []}
    legend = table.get('reasons_legend') or []
    return {r[0]: {'source_ref': r[1], 'start': r[2], 'end': r[3], 'protected_reasons': sorted(legend[i] for i in r[4]), 'concepts': r[5]}
            for r in table.get('rows') or []}


def verify(original, compacted, question=None):
    """-> list of failure strings (empty = every constraint survived)."""
    fails = []
    for k in PINNED_KEYS:
        if k in original and compacted.get(k) != original[k]: fails.append(f'pinned field {k} changed')
    q = question if question is not None else (original.get('task') or {}).get('question') or ''
    text = encode(compacted)
    for lit in set(re.findall(r'\d{4}-\d{2}-\d{2}|\d+(?:\.\d+)?', q)):
        if lit not in text: fails.append(f'literal {lit} of the question is missing')
    orig_units = {u.get('id'): u for u in original.get('evidence') or []}
    kept = {u.get('id'): u for u in compacted.get('evidence') or []}
    points = pointers(compacted)
    for uid, u in orig_units.items():
        if uid in kept: continue
        if (uid or '')[:ID] in points:
            e = points[(uid or '')[:ID]]
            if u.get('protected_reasons') and e.get('protected_reasons') != sorted(u.get('protected_reasons')):
                fails.append(f'protected unit {uid[:12]} was elided without its protected reasons')
            continue
        fails.append(f'unit {(uid or "")[:12]} is neither retained nor pointed to')
    for uid, u in kept.items():
        if uid not in orig_units: fails.append(f'unit {(uid or "")[:12]} is not in the original context'); continue
        if orig_units[uid].get('protected_reasons') != u.get('protected_reasons'): fails.append(f'protected reasons of {uid[:12]} changed')
        if orig_units[uid].get('source_ref') != u.get('source_ref'): fails.append(f'source of {uid[:12]} changed')
        ref = u.get('source_ref')
        if (compacted.get('sources') or {}).get(ref) != (original.get('sources') or {}).get(ref): fails.append(f'evidence version (record hash) of {ref} changed')
    return fails


def compact(context, count_tokens, max_tokens, question=None):
    """Fit a context_builder model_context to max_tokens. count_tokens(text) -> int (the caller's own tokenizer)."""
    before = count_tokens(encode(context))
    units = list(context.get('evidence') or [])
    sources = context.get('sources') or {}
    base = {'context': context, 'before_tokens': before, 'schema': VERSION}
    if before <= max_tokens:
        return {**base, 'fit': True, 'tokens': before, 'tiers': {'t0_whole': len(units)}, 'verified': True, 'failures': [], 'compacted': False}
    wanted = set((context.get('task') or {}).get('concepts') or [])
    order = sorted(range(len(units)), key=lambda i: _priority(units[i], sources, wanted))  # least valuable first
    pins = pinned_block(context, question)['question_requirements']
    levels = [0] * len(units)
    # per-unit token cost at each tier (estimate), so demotion does not re-tokenise the whole context at every step
    cost = [[count_tokens(encode(tier(u, lv))) + 1 if lv < 3 else
             count_tokens(encode([(u.get('id') or '')[:ID], u.get('source_ref'), u.get('start'), u.get('end'), [0] if u.get('protected_reasons') else []])) + 1
             for lv in range(4)] for u in units]
    overhead = count_tokens(encode(assemble(context, [], [], sources, pins)))
    used = overhead + sum(c[0] for c in cost)
    prot = lambda i: bool(units[i].get('protected_reasons'))
    # every non-protected unit goes all the way to a pointer before any protected unit is touched; a step that would not shrink the unit is skipped
    for protected_pass in (False, True):
        for target in (1, 2, 3):
            for i in order:
                if used <= max_tokens: break
                if prot(i) != protected_pass or levels[i] >= target: continue
                if cost[i][target] < cost[i][levels[i]]:
                    used += cost[i][target] - cost[i][levels[i]]; levels[i] = target
            if used <= max_tokens: break
        if used <= max_tokens: break
    result = assemble(context, units, levels, sources, pins)
    tokens = count_tokens(encode(result))
    while tokens > max_tokens:                   # the estimate was short: keep demoting against the real count
        step = next((i for i in order if levels[i] < 3 and not prot(i)), None)
        if step is None: step = next((i for i in order if levels[i] < 3), None)
        if step is None: break
        levels[step] = 3 if levels[step] >= 2 else levels[step] + 1
        result = assemble(context, units, levels, sources, pins); tokens = count_tokens(encode(result))
    failures = verify(context, result, question)
    return {**base, 'context': result, 'fit': tokens <= max_tokens and not failures, 'tokens': tokens, 'tiers': result['compaction']['tiers'],
            'verified': not failures, 'failures': failures, 'compacted': True}
