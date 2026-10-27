"""Send likely schemas up front; repairs send only the change (Build brief 2.2 / 2.3).

likely(catalog, question)   before the first planner call, request_discovery.search (stemmed, value-aware) over the
                            question's content words picks the top relations and received streams by search score, and
                            the analytical operators whose contract meaning overlaps the question most (top-k by score,
                            not "at most 3 with two shared words"). shared_planner seeds its schema slice with them, so the
                            model rarely spends a full-prompt turn on need_tables / need_operations / need_streams.
repair_feedback(audit, previous)
                            after a failed semantic review, the repair turn carries the review issues plus only the
                            top-level parts of the previous proposal the issues touch (the failing fragment) instead of the
                            whole proposal; the full proposal stays server-side.
apply_patch(last, plan)     the model may answer a repair with {"patch": {key: replacement}}: merged here onto the kept
                            proposal, so unchanged parts are neither resent nor regenerated. A complete proposal is used as
                            is. Repair count is unchanged (Mike's policy); the static catalog stays a cache read.
Never raises into planning: any failure falls back to the existing behaviour.
"""
import json
import os
import re

from .request_plan import STOP_WORDS, fingerprint

VERSION = 'schema-prefetch/2'
ALWAYS_KEEP = ('source',)  # the fragment always names what it reads from
PATCH_INSTRUCTION = ('Your previous proposal is kept server-side; failing_fragment shows only the parts the review flagged. '
                     'Return {"patch": {"<top-level key>": <replacement value>, ...}} replacing only those keys (any key of '
                     'the previous proposal may be replaced), or return the complete corrected proposal. Preserve every '
                     'requirement of the original question.')


def _k(name, default):
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


def terms(question):
    words = re.findall(r'[A-Za-z][A-Za-z0-9-]*|\d+', str(question))
    out = [w for w in words if w.lower() not in STOP_WORDS and len(w) > 2]
    return list(dict.fromkeys(out))[:24]


def likely(catalog, question, *, tables=None, operations=None, streams=None):
    """-> {'tables', 'operations', 'streams', 'scores', 'version'}; empty lists when nothing matches or search fails."""
    tables = _k('AGERE_PREFETCH_TABLES', 5) if tables is None else tables
    operations = _k('AGERE_PREFETCH_OPERATIONS', 4) if operations is None else operations
    streams = _k('AGERE_PREFETCH_STREAMS', 2) if streams is None else streams
    out = {'version': VERSION, 'tables': [], 'operations': [], 'streams': [], 'scores': {}}
    words = terms(question)
    if not words: return out
    try:
        from .request_discovery import search, tokens
        found = search(catalog, words, limit=max(60, len(catalog.tables) + len(catalog.stream_fields)))['candidates']
        rel = [c for c in found if c['kind'] == 'relation' and c['name'] in catalog.tables]
        strm = [c for c in found if c['kind'] == 'received_stream' and c['name'] in catalog.stream_fields]
        out['tables'] = [c['name'] for c in rel[:tables]]
        if tables:
            # The ranked slice is a starting point, not an allowlist. Generic
            # documentation words can outrank the relation explicitly named by
            # the question or the relation holding its exact category value.
            # Expand using received metadata only; no training target is read.
            wanted = tokens(question)
            named = [name for name in catalog.tables if tokens(name) and tokens(name) <= wanted]
            categorical = []
            for candidate in rel:
                for values in candidate.get('matched_values', {}).values():
                    if any(tokens(value) and tokens(value) <= wanted and
                           any(not word.isdigit() for word in tokens(value)) for value in values):
                        categorical.append(candidate['name'])
                        break
            selected = list(dict.fromkeys([*out['tables'], *named, *categorical]))
            # A named identifier can point to a dimension omitted by ranking
            # (e.g. account_id -> account). This is a schema-discovery hint,
            # never a declared foreign key or permission to generate a join.
            dimensions = [column[:-3] for name in selected for column in catalog.tables[name]
                          if column.endswith('_id') and column[:-3] in catalog.tables and
                          column in catalog.tables[column[:-3]]]
            out['tables'] = list(dict.fromkeys([*selected, *dimensions]))
            out['schema_expansion'] = {'named_relations': named,
                'exact_category_relations': categorical, 'identifier_candidates': list(dict.fromkeys(dimensions)),
                'joins_established': False}
        out['streams'] = [c['name'] for c in strm[:streams]]
        out['scores'].update({c['name']: c['score'] for c in rel[:tables] + strm[:streams]})
        from .graph_planner import CONTRACTS
        wanted = tokens(' '.join(words))
        text, ranked = ' '.join(words).lower(), []
        for op, info in CONTRACTS.items():
            if op in ('sql', 'review'): continue
            score = len(wanted & tokens(op.replace('_', ' ') + ' ' + str(info.get('meaning', ''))))
            if score >= 2 or op.replace('_', ' ') in text: ranked.append((-score, op))
        out['operations'] = [op for _, op in sorted(ranked)[:operations]]
        out['scores'].update({op: -s for s, op in sorted(ranked)[:operations]})
    except Exception as exc:  # noqa: BLE001 - prefetch is an optimisation; the model can still ask for schemas
        out['error'] = f'{type(exc).__name__}: {exc}'[:200]
    return out


def _tokens(value):
    return set(re.findall(r'[a-z0-9]+', json.dumps(value, default=str).lower().replace('_', ' '))) - STOP_WORDS


def fragment(previous, issues):
    """Top-level parts of a proposal the review issues touch (by shared field / value words); None when the issues touch
    everything or nothing (then the whole proposal is sent, as before)."""
    if not isinstance(previous, dict) or not issues: return None
    said = _tokens(issues)
    per_key = {k: _tokens({k: v}) for k, v in previous.items()}
    common = {t for t in said if sum(t in toks for toks in per_key.values()) > max(1, len(per_key) // 3)}  # e.g. the table name
    hit = {k: v for k, v in previous.items() if k in ALWAYS_KEEP or (per_key[k] & (said - common))}
    flagged = [k for k in hit if k not in ALWAYS_KEEP]
    if not flagged or len(hit) >= len(previous): return None
    return hit


def repair_feedback(audit, previous, *, kind='request', extra=None):
    """Diff-only repair feedback, or None (caller keeps its full-proposal feedback)."""
    try:
        frag = fragment(previous, list((audit or {}).get('issues') or []))
    except Exception:  # noqa: BLE001
        return None
    if frag is None: return None
    return {'semantic_review': audit, 'failing_fragment': frag, 'previous_proposal_kind': kind,
            'previous_proposal_keys': sorted(previous), 'previous_proposal_id': fingerprint(previous)[:16],
            'instruction': PATCH_INSTRUCTION, **(extra or {})}


def apply_patch(last, plan):
    """last: (kind, kept proposal) or None. A {"patch": {...}} reply merges onto the kept proposal; anything else is returned
    unchanged. -> plan"""
    if not (isinstance(plan, dict) and set(plan) == {'patch'} and isinstance(plan['patch'], dict) and last): return plan
    kind, kept = last
    if not isinstance(kept, dict): return plan
    merged = {**kept, **plan['patch']}
    return {'analysis': merged} if kind == 'analysis' else merged
