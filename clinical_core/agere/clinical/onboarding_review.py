"""Onboarding review: questions about how this data was received and onboarded, answered from the recorded receipts.

Topics (parameter 'topic'):
  feeds          what was received and in what format: native feed formats and standards (X12 837/835/834, HL7 v2, FHIR bulk,
                 NCPDP, CCLF, EHR CSV extracts, scanned documents) for a native workspace (source_format.json / ingest.json /
                 delivery facts); for a warehouse build, the delivery manifest, streams and records, code systems and outside
                 documents (ingestion.json)
  canonical      how much of the received data maps to the canonical model and its Tuva input-layer / OMOP CDM tables
                 (onboarding/canonical_map.py; for the active build plus the row counts of the built Tuva warehouse when it
                 holds the same roster)
  unmapped       what did not map: streams with no canonical table (and the recorded reason), source tables kept only raw,
                 open field-mapping questions of a field-mapped workspace
  round_trip     whether an OMOP-shaped copy of these people, onboarded through the workspace path, reproduced the source and the
                 native build (onboarding/round_trip.py; round_trip.json of OMOP workspaces whose reference is this build)
  verification   the onboarding checks recorded at build/activation (verification.json)
  overview       one line from each of the above

Context: parameter 'workspace' (slug under artifacts/workspaces), else the workspace that contains the stack path, else the
warehouse build that contains it (config/agere/active_warehouse.json build). Read-only; no model calls; synthetic data only.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

VERSION = 'onboarding-review/1'
TOPICS = ('feeds', 'canonical', 'unmapped', 'round_trip', 'verification', 'overview')
MODELS = ('both', 'tuva', 'omop')
ROOT = Path(__file__).resolve().parents[3]
DEFINITION = {
    'feeds': 'received feeds by format and standard, with file and record counts from the ingest receipts',
    'canonical': 'share of received records mapped to a canonical table = records_mapped / records_total (denominator: all received records), '
                 'with the Tuva input-layer and OMOP CDM table each canonical table feeds',
    'unmapped': 'sources not mapped to any canonical table (missing mapping, with the recorded reason) and open field-mapping questions',
    'round_trip': 'OMOP round trip: OMOP rows vs modeled rows per table, and aggregates vs the native build of the same people (1% tolerance)',
    'verification': 'onboarding verification checks recorded at build / activation (passed or not)',
    'overview': 'feeds, canonical mapping share (records_mapped / records_total), unmapped sources, round trip and verification',
}
NATIVE_SCALARS = ('count', 'value')
PRODUCT = {'population': 'none', 'view': 'table',
           'use': 'ONBOARDING / DATA RECEIPT questions about how this data arrived and was onboarded (not clinical content): which '
                  'source feeds were received and in what format (X12 837/835/834, HL7 v2, FHIR, NCPDP, CCLF, EHR CSV, scanned '
                  'documents); how much of the data maps to the canonical model / OMOP / Tuva and which sources or fields did not '
                  'map and why; whether the OMOP round trip reproduced the counts; whether onboarding verification passed.'}
CONTRACT = {'inputs': [], 'parameters': {'topic': '|'.join(TOPICS), 'model': '|'.join(MODELS), 'workspace': '', 'definition': ''},
            'meaning': 'Answers from the recorded onboarding receipts (ingest / source-format detection, canonical mapping receipt with '
                       'Tuva and OMOP targets, unmapped sources with reasons, OMOP round-trip parity, verification) of the active '
                       'build or a named workspace. Use for feed/format, mapping coverage, unmapped field and round-trip questions.'}


def parameter_schema(catalog, obj, string, array):
    return obj({'topic': string(TOPICS, 'feeds | canonical (mapping coverage) | unmapped | round_trip | verification | overview'),
                'model': string(MODELS, 'canonical target: both, tuva or omop'),
                'workspace': string(None, 'workspace slug, or empty for the active build'),
                'definition': string(None, 'leave empty: filled with the computation used')})


def validate(p):
    if not isinstance(p, dict) or set(p) - {'topic', 'model', 'workspace', 'definition'}:
        raise ValueError('onboarding_review parameters: topic, model, workspace, definition')
    topic = p.get('topic') or 'overview'
    if topic not in TOPICS: raise ValueError(f'topic: one of {TOPICS}')
    model = p.get('model') or 'both'
    if model not in MODELS: raise ValueError(f'model: one of {MODELS}')
    ws = p.get('workspace') or ''
    if ws and not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,80}', ws): raise ValueError('workspace: a workspace slug')
    return {'topic': topic, 'model': model, 'workspace': ws, 'definition': DEFINITION[topic]}


# ------------------------------------------------------------------------------------------------ context
def _workspaces_root():
    from ..onboarding.workspace import WORKSPACES
    main = ROOT.parent.parent.parent / 'artifacts' / 'workspaces' if '.claude' in ROOT.parts else None  # worktree: main checkout's
    return WORKSPACES if WORKSPACES.is_dir() or main is None else main


def context(path, workspace=''):
    """-> ('workspace', dir) or ('build', dir)."""
    if workspace:
        d = _workspaces_root() / workspace
        if not (d / 'workspace.json').is_file(): raise ValueError(f'no workspace {workspace!r}')
        return 'workspace', d
    p = Path(path).resolve()
    for d in (p, *p.parents[:4]):
        if (d / 'workspace.json').is_file(): return 'workspace', d
    for d in (p, *p.parents[:2]):
        if (d / 'ingestion.json').is_file() or (d / 'activation.json').is_file(): return 'build', d
    return 'build', p.parent


def _json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def _pct(x): return 'n/a' if x is None else f'{100 * x:.1f}%'


# ------------------------------------------------------------------------------------------------ topics
def feeds(kind, d):
    rows = []
    if kind == 'workspace':
        det = _json(d / 'source_format.json') or {}
        ing = _json(d / 'ingest.json') or {}
        state = _json(d / 'workspace.json') or {}
        delivery = ((state.get('steps') or {}).get('build') or {}).get('delivery') or {}
        if det.get('standards'):
            rows = [{'standard': k, 'files': v, 'parser': None} for k, v in det['standards'].items()]
            parsers = det.get('formats') or {}
        else:
            parsers = ing.get('by_parser') or {}
            rows = [{'standard': k, 'files': v, 'parser': k} for k, v in sorted(parsers.items(), key=lambda kv: -kv[1])]
        kind_ = det.get('kind') or state.get('source_kind') or ('native_delivery' if str(state.get('source', '')).startswith('native://') else 'warehouse')
        head = (f"{kind_.replace('_', ' ')}: {det.get('files') or ing.get('files') or sum(parsers.values()) or 0:,} files received"
                + (f" ({', '.join(f'{k} {v:,}' for k, v in sorted(parsers.items(), key=lambda kv: -kv[1]))})" if parsers else '')
                + (f"; {ing['rows']:,} rows parsed into {len(ing.get('tables') or {})} landing tables" if ing.get('rows') else '')
                + (f"; {ing['unparsed']:,} files no native parser reads" if ing.get('unparsed') else '')
                + (f"; delivery manifest lists {delivery['files']:,} files ({delivery.get('resends', 0):,} resends, "
                   f"{delivery.get('gaps', 0):,} missing periods)" if delivery.get('files') else '') + '.')
        if kind_ == 'omop' or kind_ == 'tuva_input_layer' or kind_ == 'warehouse':
            prof = _json(d / 'profile.json') or {}
            tables = prof.get('tables') or {}
            if tables:
                rows = [{'standard': f'{kind_} table', 'source_table': t, 'rows': (v or {}).get('rows')} for t, v in tables.items()]
                head = f"{kind_.replace('_', ' ')} database with {len(tables)} tables" + (f" ({', '.join((det.get('signature') or [])[:8])})" if det.get('signature') else '') + '.'
        return head, rows
    ing = _json(d / 'ingestion.json') or {}
    counts = ing.get('counts') or {}
    patients = counts.get('patients')
    streams = {k: v for k, v in counts.items() if k != 'patients'}
    from ..onboarding import canonical_map
    try:
        entries = canonical_map._map().get('streams', {})
    except (OSError, ValueError):
        entries = {}
    rows = [{'stream': s, 'records': n, 'canonical_tables': ', '.join((entries.get(s) or {}).get('tables') or []) or None}
            for s, n in sorted(streams.items(), key=lambda kv: -kv[1])]
    codes = ing.get('records_by_code_system') or {}
    docs = ing.get('outside_documents') or {}
    head = (f"One warehouse delivery ({ing.get('manifest_schema', 'warehouse')} manifest, adapter {ing.get('adapter', '?')}) as of "
            f"{ing.get('as_of', '?')}: {len(streams)} source streams, {sum(streams.values()):,} records"
            + (f" for {patients:,} patients" if patients else '')
            + (f"; standard code systems: {', '.join(f'{k} {v:,}' for k, v in sorted(codes.items(), key=lambda kv: -kv[1])[:8])}" if codes else '')
            + (f"; {docs.get('files', 0):,} scanned outside documents (PDF, text read by {docs.get('reader', 'OCR')})" if docs else '') + '.')
    return head, rows


def canonical(kind, d, model='both'):
    from ..onboarding import canonical_map
    rep = (_json(d / 'canonical.json') if kind == 'workspace' else None) or (
        canonical_map.for_build(d) if kind == 'build' else canonical_map.entity_receipt(d / 'semantic.sqlite3'))
    if rep.get('error'): raise ValueError(rep['error'])
    share = {'tuva': rep.get('share_tuva'), 'omop': rep.get('share_omop')}.get(model, rep.get('share_mapped'))
    label = {'tuva': 'a Tuva input-layer table', 'omop': 'an OMOP CDM table'}.get(model, 'a canonical table')
    head = (f"{_pct(share)} of {rep.get('records_total', 0):,} received records map to {label} "
            f"({rep.get('records_mapped', 0):,} to any canonical table = {_pct(rep.get('share_mapped'))}; Tuva {_pct(rep.get('share_tuva'))}, "
            f"OMOP {_pct(rep.get('share_omop'))}); {len(rep.get('unmapped') or [])} sources map to none")
    tuva = rep.get('tuva_build')
    if tuva and model in ('both', 'tuva'):
        il = tuva.get('input_layer') or {}
        head += (f"; the built Tuva warehouse ({'same roster' if tuva.get('roster_match') else 'roster differs'}: input_layer.patient "
                 f"{il.get('patient', 0):,} vs {tuva.get('received_patients') or '?'} received patients) holds "
                 f"{sum(il.values()):,} input-layer rows in {len(il)} tables")
    rows = [r for r in rep.get('tables') or [] if model == 'both' or r.get(model)]
    return head + '.', rows, share, rep


def unmapped(kind, d):
    _, _, _, rep = canonical(kind, d)
    rows = [{'source': u['source'], 'records': u['records'], 'reason': u['reason']} for u in rep.get('unmapped') or []]
    for t in rep.get('unmapped_source_tables') or []:
        rows.append({'source': t, 'records': None, 'reason': 'source table no modeled entity uses (kept as raw_' + t + ')'})
    for f in rep.get('unmapped_fields') or []:
        rows.append({'source': f"{f['entity']}.{f.get('field') or '*'}", 'records': None, 'reason': f['reason']})
    n = sum(r['records'] or 0 for r in rows)
    head = (f"{len(rows)} sources or fields did not map to the canonical model ({n:,} received records): "
            + '; '.join(f"{r['source']} ({r['reason'][:80]})" for r in rows[:4]) + ('' if len(rows) <= 4 else ' ...')) if rows else \
        'Every received source maps to a canonical table; no open field-mapping questions.'
    return head, rows


def _round_trips_for(build):
    target = (Path(build) / 'stack' / 'semantic.sqlite3').resolve()
    out = []
    root = _workspaces_root()
    for f in sorted(root.glob('*/round_trip.json')) if root.is_dir() else []:
        r = _json(f) or {}
        ref = (r.get('native_parity') or {}).get('reference')
        if ref and Path(ref).resolve() == target: out.append((f.parent.name, r))
    return out


def round_trip(kind, d):
    found = ([(d.name, _json(d / 'round_trip.json'))] if kind == 'workspace' and (d / 'round_trip.json').is_file()
             else _round_trips_for(d) if kind == 'build' else [])
    if not found:
        return ('No OMOP round trip is recorded for this data: onboard an OMOP copy of its people as a workspace '
                '(Workspace.create(name, "sqlite:///omop.sqlite").run()) to record one.'), [], None
    rows, verdicts = [], []
    for slug, r in found:
        for c in (r.get('source_parity') or {}).get('checks', []):
            rows.append({'workspace': slug, 'check': f"OMOP {c['omop_table']} vs modeled {c['entity']}", 'omop_or_onboarded': c['omop_rows'],
                         'modeled_or_native': c['modeled_rows'], 'match': c['match']})
        np_ = r.get('native_parity') or {}
        for a in np_.get('aggregates') or []:
            rows.append({'workspace': slug, 'check': f"native build: {a['aggregate']}", 'omop_or_onboarded': a['onboarded'],
                         'modeled_or_native': a['native'], 'match': a['within_tolerance']})
        verdicts.append((slug, r, np_))
    slug, r, np_ = verdicts[-1]
    people = next((c for c in (r.get('source_parity') or {}).get('checks', []) if c['omop_table'] == 'person'), None)
    head = (f"{'Yes' if r.get('reproduced') else 'No'}: workspace {slug} onboarded an OMOP copy"
            + (f" of {people['omop_rows']} people and modeled {people['modeled_rows']}" if people else '')
            + f"; OMOP-to-model table counts {'all match' if (r.get('source_parity') or {}).get('all_match') else 'differ'}"
            + (f"; against the native build of the same {np_.get('found_in_reference')} people, aggregates "
               f"{'are all within' if np_.get('all_within_tolerance') else 'are not all within'} {100 * (np_.get('tolerance') or 0.01):.0f}% and "
               f"{np_.get('acceptance_matched')}/{np_.get('acceptance_total')} acceptance questions match" if np_.get('found_in_reference') else ''))
    off = [a for a in np_.get('aggregates') or [] if not a['within_tolerance']]
    if off:
        head += '; differing: ' + ', '.join(f"{a['aggregate']} {a['onboarded']:,} vs native {a['native']:,}" for a in off[:5]) + (' ...' if len(off) > 5 else '')
    head += '.'
    return head, rows, bool(r.get('reproduced'))


def verification(kind, d):
    v = _json(d / 'verification.json') or {}
    checks = v.get('checks')
    if isinstance(checks, list):
        rows = [{'check': c.get('name') or c.get('check'), 'passed': c.get('passed'), 'annotation': bool(c.get('annotation'))} for c in checks]
    else:
        rows = [{'check': k, 'passed': val} for k, val in v.items() if isinstance(val, bool)]
    if kind == 'build':
        cc = _json(d / 'client-checks.json') or {}
        for k, val in cc.items():
            if isinstance(val, dict) and 'passed' in val: rows.append({'check': f'client check: {k}', 'passed': val['passed']})
    failed = [r['check'] for r in rows if r.get('passed') is False and not r.get('annotation')]
    head = (f"{len(rows) - len(failed)} of {len(rows)} onboarding checks passed" + (f"; failed: {', '.join(failed[:5])}" if failed else '')
            + '.') if rows else 'No onboarding verification is recorded for this data.'
    return head, rows


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    kind, d = context(path, p['workspace'])
    report = {'version': VERSION, 'topic': p['topic'], 'model': p['model'], 'context': kind, 'source': str(d), 'as_of': as_of,
              'definition': p['definition'], 'value': None}
    if p['topic'] == 'feeds':
        report['headline'], report['rows'] = feeds(kind, d)
    elif p['topic'] == 'canonical':
        report['headline'], report['rows'], report['value'], rep = canonical(kind, d, p['model'])
        report['records_total'], report['records_mapped'] = rep.get('records_total'), rep.get('records_mapped')
    elif p['topic'] == 'unmapped':
        report['headline'], report['rows'] = unmapped(kind, d)
    elif p['topic'] == 'round_trip':
        report['headline'], report['rows'], report['value'] = round_trip(kind, d)
    elif p['topic'] == 'verification':
        report['headline'], report['rows'] = verification(kind, d)
    else:
        parts, rows = [], []
        for name, fn in (('feeds', lambda: feeds(kind, d)), ('canonical', lambda: canonical(kind, d)[:2]),
                         ('unmapped', lambda: unmapped(kind, d)), ('round_trip', lambda: round_trip(kind, d)[:2]),
                         ('verification', lambda: verification(kind, d))):
            try:
                h, _ = fn()
            except Exception as exc:  # noqa: BLE001 - one missing receipt never hides the others
                h = f'not available ({type(exc).__name__}: {exc})'[:200]
            parts.append(f'{name}: {h}'); rows.append({'topic': name, 'finding': h})
        report['headline'], report['rows'] = ' | '.join(parts), rows
    return report


def answer(report, plan, question):
    basis = f"Onboarding receipts of {report['context']} {report['source']} ({VERSION}); {report['definition']}."
    out = {'status': 'answered', 'answer_type': 'table', 'table': report['rows'], 'headline': report['headline'],
           'value': report['headline'], 'patient_ids': [], 'method': basis,
           'definitions': [{'term': f"onboarding {report['topic']}", 'basis': report['definition']}],
           'limitations': [basis, 'Describes how the data was received and mapped, not clinical content.']}
    if report['topic'] == 'canonical' and report.get('value') is not None:
        out.update(answer_type='value', value=round(100 * report['value'], 1), unit='percent of received records',
                   numerator=report.get('records_mapped'), denominator=report.get('records_total'))
    if report['topic'] == 'round_trip' and report.get('value') is not None:
        out.update(verdict='yes' if report['value'] else 'no')
    return out


# ------------------------------------------------------------------------------------------------ exact routing
_ONBOARD = r'(?:onboard(?:ed|ing)?|receiv(?:e|ed)|deliver(?:ed|y|ies)?|ingest(?:ed)?|sen[dt]|land(?:ed)?)'
_DQ = re.compile(r'\b(late|missing|resen[dt]|re-sen[dt]|truncat|empty|gaps?|duplicat|zero[- ]byte|header[- ]only|v2|replacement|'
                 r'patients?|members?|claims? (?:paid|denied)|how many files)\b', re.I)
PATTERNS = [
    ('round_trip', re.compile(r'\bround[- ]trip\b|\bomop\b.*\breproduc|\breproduc.*\bomop\b', re.I)),
    ('unmapped', re.compile(r"\b(?:could ?n[o']t|can ?n[o']t|did ?n[o']t|were ?n[o']t|was ?n[o']t|not|never|un)[- ]?(?:be |get |been )?map(?:ped)?\b|\bunmapped\b|"
                            r"\bfail(?:ed)? to map\b|\bno (?:canonical )?mapping\b", re.I)),
    ('canonical', re.compile(r'\b(?:omop|tuva|canonical (?:data )?model|common data model|cdm)\b', re.I)),
    ('feeds', re.compile(rf'\b(?:what|which)\b.*\b(?:source )?(?:feeds?|formats?|file types?|data sources?|source systems?)\b.*\b{_ONBOARD}\b|'
                         rf'\b{_ONBOARD}\b.*\bin what formats?\b|\bwhat formats?\b.*\b{_ONBOARD}\b', re.I)),
    ('verification', re.compile(r'\bonboarding\b.*\b(?:verif\w*|checks?|pass(?:ed)?)\b', re.I)),
]


def local_question(question, as_of=None):
    """Exact routing for onboarding questions; a question about late/missing/duplicate files or about patients is left to
    the data-quality families and the clinical tiers."""
    q = ' '.join((question or '').split())
    for topic, rx in PATTERNS:
        if not rx.search(q): continue
        if topic == 'canonical' and not re.search(r'\b(?:map\w*|coverage|cover(?:ed|s)?|how much|share|percent\w*|proportion|fraction|fit|conform\w*)\b', q, re.I):
            continue
        if topic in ('feeds', 'verification', 'canonical') and _DQ.search(q): continue
        model = 'tuva' if re.search(r'\btuva\b', q, re.I) and not re.search(r'\bomop\b', q, re.I) else \
            'omop' if re.search(r'\bomop\b', q, re.I) and not re.search(r'\btuva\b', q, re.I) else 'both'
        from .foundation_engines import graph
        return graph(question, 'onboarding_review', {'topic': topic, 'model': model, 'workspace': '', 'definition': DEFINITION[topic]},
                     f'Onboarding review: {topic}')
    return None
