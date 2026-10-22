"""Jev second check on lab / drug / encounter normalization: at build and on answers that depend on normalized codes.

tools/normalization/jev_check.py asks Jev one typed choice per normalization dictionary entry (the dictionary mapping, the
rule mapping when it differs, or none) and writes jev_choice / jev_p into config/agere/normalization/*.json. Here:
  verdict(entry)        agreed (Jev picked the dictionary mapping) / rules_preferred (Jev sided with the rules against the
                        dictionary) / rejected (Jev chose none) / unchecked.
  withheld(domain)      source strings whose DICTIONARY mapping the second check does not support (rejected, or rules
                        preferred) at p >= MIN_P. semantic_layer._normalize skips the dictionary concept / extra drug classes
                        for exactly these at build; rule mappings and source codes are untouched (rules stay authoritative).
  build_table(db)       normalization_check table in the semantic layer: every dictionary string with its Jev verdict, so
                        SQL and the planners can read it.
  annotate(...)         answer check (answer_checks.check): the lab concepts / drug classes / encounter concepts a plan reads
                        -> the dictionary source strings feeding them and Jev's verdict on each; contested strings are named
                        in the answer's limitations with their source-row counts. Strings never checked (new since the last
                        jev_check run) get a live Jev typed check (System One, task normalization_check, <= LIVE_MAX per
                        answer, allow_live only; cost reported).
Only terminology strings travel to Jev: no patient rows, values or identifiers.
"""
import json
import re
import threading
from pathlib import Path

VERSION = 'normalization-check/1'
ROOT = Path(__file__).resolve().parents[3]
DIR = ROOT/'config'/'agere'/'normalization'
DOMAINS = ('labs', 'medications', 'encounters')
MIN_P = 0.8
LIVE_MAX = 8
TASK_KEY = 'normalization_check'
_LOCK, _CACHE = threading.Lock(), {}


def entries(domain):
    path = DIR/f'{domain}.json'
    try: stamp = path.stat().st_mtime
    except OSError: return {}
    with _LOCK:
        hit = _CACHE.get(domain)
        if hit and hit[0] == stamp: return hit[1]
    data = json.loads(path.read_text()).get('entries') or {}
    with _LOCK: _CACHE[domain] = (stamp, data)
    return data


def verdict(entry):
    c = entry.get('jev_choice')
    if not c: return 'unchecked'
    return {'dictionary': 'agreed', 'dictionary_and_rules': 'agreed', 'rules': 'rules_preferred', 'none': 'rejected'}.get(c, 'unchecked')


def withheld(domain, min_p=MIN_P):
    """{source string: verdict} whose dictionary mapping the Jev check does not support at p >= min_p."""
    out = {}
    for k, v in entries(domain).items():
        variants = list(v['variants_by_unit'].values()) if isinstance(v.get('variants_by_unit'), dict) else [v]
        for e in variants:
            s = verdict(e)
            if s in ('rejected', 'rules_preferred') and (e.get('jev_p') or 0) >= min_p:
                out[k] = s
    return out


def mapping(domain, e):
    if domain == 'medications': return ', '.join(e.get('drug_classes') or []) or None
    return e.get('agere_concept')


def build_table(db):
    """(Re)create normalization_check(domain, source_string, mapping, jev_choice, jev_p, status, withheld, source_count)."""
    db.execute('DROP TABLE IF EXISTS normalization_check')
    db.execute('CREATE TABLE normalization_check(domain TEXT, source_string TEXT, mapping TEXT, jev_choice TEXT, jev_p REAL, '
               'status TEXT, withheld INTEGER, source_count INTEGER)')
    rows = []
    for d in DOMAINS:
        held = withheld(d)
        for k, v in entries(d).items():
            rows.append((d, k, mapping(d, v), v.get('jev_choice'), v.get('jev_p'), verdict(v), int(k in held), v.get('source_count')))
    db.executemany('INSERT INTO normalization_check VALUES (?,?,?,?,?,?,?,?)', rows)
    db.execute('CREATE INDEX normalization_check_mapping ON normalization_check(domain, mapping)')
    return {'rows': len(rows), 'withheld': sum(r[6] for r in rows), 'version': VERSION}


# -- question path ----------------------------------------------------------------------------------------------------
def vocab():
    from . import concepts, drug_classes
    return {'labs': set(concepts.MEASUREMENTS), 'medications': set(drug_classes.CLASSES), 'encounters': set(concepts.ENCOUNTERS)}


def plan_concepts(graph):
    """{domain: {concept ids}} the plan reads (any node parameter string, SQL included, naming a normalized concept)."""
    text = json.dumps([n.get('parameters') for n in (graph or {}).get('nodes') or [] if isinstance(n, dict)], default=str).lower()
    try:
        from .medication_state import PILLARS
    except Exception:  # noqa: BLE001
        PILLARS = {}
    found = {}
    for d, ids in vocab().items():
        hit = {c for c in ids if re.search(rf'(?<![a-z0-9_]){re.escape(c.lower())}(?![a-z0-9_])', text)}
        if d == 'medications':
            for pillar, cls in PILLARS.items():
                if re.search(rf'(?<![a-z0-9_]){pillar}(?![a-z0-9_])', text) or re.search(r'\bgdmt\b', text): hit |= set(cls)
            if 'hf_evidence_based_beta_blocker' in hit: hit.add('beta_blocker')
        if hit: found[d] = hit
    return found


def feeding(domain, concepts_):
    """[(source string, entry)] whose dictionary mapping lands in these concepts."""
    out = []
    for k, v in entries(domain).items():
        variants = list(v['variants_by_unit'].values()) if isinstance(v.get('variants_by_unit'), dict) else [v]
        for e in variants:
            m = set(e.get('drug_classes') or []) if domain == 'medications' else {e.get('agere_concept')}
            if m & concepts_: out.append((k, {**e, 'source_count': v.get('source_count', e.get('source_count'))})); break
    return out


def live_check(domain, raw, entry, *, as_of, allow_live, decide=None):
    """System One typed second check for one unchecked string (same candidates as tools/normalization/jev_check.py)."""
    import sys
    if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
    from tools.normalization import jev_check
    options, _ = jev_check.CANDIDATES[domain](raw, entry)
    if decide is None:
        from . import system_one
        decide = system_one.decide
    q = jev_check.INSTRUCTIONS[domain] + f' Source string: {raw}' + (f" (unit {entry.get('canonical_unit')})" if entry.get('canonical_unit') else '')
    d = decide(q, options, [], as_of=as_of, order=('jev', 'perplexity'), task_key=TASK_KEY, allow_live=allow_live)
    calls = sum(1 for s in d.get('steps') or [] if s.get('status') == 'answered' and not s.get('cache_hit') and s.get('provider') != 'local_student')
    return {'source_string': raw, 'domain': domain, 'jev_choice': d.get('label'), 'jev_p': (d.get('calibrated_probabilities') or {}).get(d.get('label')),
            'status': verdict({'jev_choice': d.get('label')}), 'decided_by': d.get('decided_by'), 'new_paid_api_calls': calls,
            'cost_usd': d.get('cost_usd') or 0.0}


def annotate(graph, *, as_of='2030-05-27', allow_live=False, decide=None):
    """-> {'concepts', 'source_strings', 'agreed', 'contested':[...], 'unchecked', 'live':[...], 'cost'} or None when the plan
    reads no normalized concept."""
    used = plan_concepts(graph)
    if not used: return None
    contested, unchecked, n, agreed = [], [], 0, 0
    for d, cs in used.items():
        for raw, e in feeding(d, cs):
            n += 1
            s = verdict(e)
            if s == 'agreed': agreed += 1
            elif s == 'unchecked': unchecked.append((d, raw, e))
            else: contested.append({'domain': d, 'source_string': raw, 'mapping': mapping(d, e), 'jev_choice': e.get('jev_choice'),
                                    'jev_p': e.get('jev_p'), 'source_rows': e.get('source_count'), 'status': s,
                                    'withheld_at_build': (e.get('jev_p') or 0) >= MIN_P})
    live, calls, usd = [], 0, 0.0
    if unchecked and allow_live:
        for d, raw, e in unchecked[:LIVE_MAX]:
            try:
                r = live_check(d, raw, e, as_of=as_of, allow_live=True, decide=decide)
            except Exception as exc:  # noqa: BLE001 - a failed second check is reported, never blocks
                r = {'source_string': raw, 'domain': d, 'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'[:200]}
            live.append(r); calls += r.get('new_paid_api_calls', 0); usd += r.get('cost_usd') or 0.0
    contested.sort(key=lambda r: -(r['source_rows'] or 0))
    return {'version': VERSION, 'concepts': {d: sorted(c) for d, c in used.items()}, 'source_strings': n, 'agreed': agreed,
            'contested': contested, 'unchecked': [raw for _, raw, _ in unchecked], 'live': live,
            'cost': {'new_paid_api_calls': calls, 'usd': round(usd, 6)}, 'model': 'jev-1.13.0 (tools/normalization/jev_check.py)'}


def annotate_verdict(stack, graph, answer, verdict_, *, ledger=None):
    """answer_checks.check hook: annotation only (heal, never block)."""
    as_of = getattr(getattr(stack, 'retriever', None), 'meta', {}).get('snapshot', {}).get('as_of', '2030-05-27')
    out = annotate(graph, as_of=str(as_of)[:10], allow_live=bool(getattr(stack, 'allow_live', False)))
    if not out: return None
    answer['normalization_check'] = out
    verdict_['normalization_check'] = {k: out[k] for k in ('concepts', 'source_strings', 'agreed', 'unchecked')}
    verdict_['cost']['new_paid_api_calls'] += out['cost']['new_paid_api_calls']; verdict_['cost']['usd'] += out['cost']['usd']
    if out['contested']:
        top = '; '.join(f"{c['source_string']!r} -> {c['mapping'] or 'none'} (Jev: {c['jev_choice']}, p={c['jev_p']}, {c['source_rows']} rows)"
                        for c in out['contested'][:4])
        verdict_['annotations'].append(f"Normalization second check (Jev): {out['agreed']} of {out['source_strings']} source strings feeding "
                                       f"{', '.join(sorted({x for v in out['concepts'].values() for x in v}))[:120]} agree with the dictionary; "
                                       f"contested: {top}.")
    return out
