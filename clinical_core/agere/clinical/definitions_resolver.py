"""Cited standard definitions for terms a question depends on but neither states nor finds governed.

Use case #6 (Mike, 2026-10-22): "MSSP avoidable spend", "HEDIS breast cancer screening gap", "attributed lives",
"HCC recapture rate" were refused ("no definition"; "recreating governed definition prohibited"). Now:
  detect(question)   deterministic term extraction (program qualifier + metric head, VBC jargon, measure codes), each
                     checked against every governed layer: config/agere/definitions.json, the governed metric layer
                     (request_vocabulary.aliases), the measure library config/agere/measures/*.json,
                     default_definitions.ALIASES and vbc_knowledge titles. A governed match whose extra qualifier words
                     the governed text never mentions ("MSSP" on avoidable spend) is ambiguous: one System One typed
                     question decides; without System One the term is resolved (cheap, and the answer names it).
  resolve(term)      one Perplexity Agent API request (gateway ENDPOINTS['perplexity_agent'], preset fast, medium only
                     when fast returns no cited definition) asking for the CMS/NCQA/AHRQ standard and its citations.
                     Sources come from the provider's own search results, never model-written URLs. Results are kept in
                     config/agere/definitions_resolved.json (versioned, retrieval date) so each term is paid once;
                     identical requests also replay from the provider ledger at $0 (context_student.send).
  context(question)  hosted-context block for shared_planner.schema_slice / general_analysis: the cited definitions plus
                     the instruction to answer under them and disclose definition and source.
Only the term text is sent: no patient data, no user identity, no email in headers.
"""
from __future__ import annotations
import json, os, re, threading
from datetime import date
from pathlib import Path

VERSION = 'definitions-resolver/1'
SCHEMA = 'agere-definitions-resolved/1'
ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / 'config' / 'agere' / 'definitions_resolved.json'
RECEIPTS = ROOT / 'artifacts' / 'definitions-resolved' / 'receipts'
PROVIDER = 'perplexity_agent'
PRESETS = ('fast', 'medium')
_LOCK = threading.Lock()

# ---------------------------------------------------------------- detection --------------------------------------------
QUALIFIERS = (r'MSSP|ACO REACH|ACO|REACH|HEDIS|CMS-HCC|CMS|AHRQ|NCQA|MIPS|eCQM|PQA|CAHPS|HCC|PQI(?:\s+\d{2})?|UDS|'
              r'Medicare Advantage|Medicaid|Medicare|Star Ratings?|Stars|MLR|TCOC|PCMH|APM|MACRA|QPP|PSI|IQI')
HEADS = (r'rates?|spend(?:ing)?|costs?|gaps?|admissions?|composites?|measures?|scores?|lives|savings|benchmarks?|ratios?|'
         r'pmpm|adherence|compliance|utilization|leakage|recapture|closure|attribution|readmissions?|visits?|screenings?|'
         r'percent(?:age)?|indicators?')
_CODE = r'(?:\s*\((?:[A-Z][A-Za-z0-9-]{1,10})\))?'
# Qualifier, up to five words, metric head words (lazy middle so "screening gap" keeps both heads), optional "(BCS-E)".
_QUALIFIED = re.compile(rf'\b(?:{QUALIFIERS})\b(?:[ \t]+[A-Za-z0-9][\w/-]*){{0,5}}?[ \t]+(?:{HEADS})\b(?:[ \t]+(?:{HEADS})\b)*{_CODE}(?:[ \t]+(?:{HEADS})\b)*', re.I)
JARGON = [r'\battributed (?:lives|members|patients|beneficiaries|population)\b', r'\b(?:hcc|risk|diagnosis) recapture(?: rate)?\b',
          r'\bmedical loss ratio\b', r'\b(?:care )?gap closure rate\b', r'\b(?:network |referral |out-of-network )?leakage(?: rate)?\b',
          r'\btotal cost of care\b', r'\bavoidable (?:spend|spending|cost|admissions?|ed visits?)\b', r'\bambulatory[- ]care[- ]sensitive admissions?\b',
          r'\bpreventable (?:admissions?|ed visits?|hospitali[sz]ations?)\b', r'\brisk adjustment factor\b', r'\bcoding intensity\b',
          r'\bannual wellness visit rate\b', r'\bpost[- ]acute (?:spend|spending|cost)\b', r'\bsnf (?:days|admissions?) per 1,?000\b',
          r'\bshared savings rate\b', r'\bminimum savings rate\b', r'\bbenchmark(?:ed)? expenditures?\b']
_JARGON = re.compile('|'.join(JARGON), re.I)
FILLER = {'our', 'the', 'a', 'an', 'of', 'for', 'in', 'by', 'total', 'overall', 'number', 'count', 'counts', 'share', 'what',
          'is', 'per', 'and', 'all', 'rate', 'rates', 'gap', 'gaps', 'measure', 'measures', 'admission', 'admissions', 'spend',
          'spending', 'cost', 'costs', 'score', 'scores', 'percent', 'percentage', 'visit', 'visits', 'members', 'patients'}


def norm(text):
    return re.sub(r'[^a-z0-9]+', ' ', str(text).lower()).strip()


def _phrase_in(alias, text):
    return bool(alias) and re.search(r'(?<![a-z0-9])' + re.escape(alias) + r'(?![a-z0-9])', text) is not None


def candidates(question):
    """Definition-dependent terms in question order; overlapping matches keep the longest."""
    spans = [(m.start(), m.end()) for rx in (_QUALIFIED, _JARGON) for m in rx.finditer(question)]
    kept = []
    for s, e in sorted(spans, key=lambda x: (x[0], -(x[1]-x[0]))):
        if kept and s < kept[-1][1]:
            if e > kept[-1][1]: kept[-1] = (kept[-1][0], e)
            continue
        kept.append((s, e))
    return list(dict.fromkeys(question[s:e].strip() for s, e in kept))


_INDEX = None


def governed_index(refresh=False):
    """[{id, layer, names (normalized aliases), regex, text}] across every governed definition layer (memoized)."""
    global _INDEX
    if _INDEX is not None and not refresh: return _INDEX
    index = []
    def add(ident, layer, names, text, regex=None):
        index.append({'id': ident, 'layer': layer, 'names': {n for n in map(norm, names) if len(n) >= 3},
                      'regex': regex, 'text': norm(text)})
    try:
        from . import request_vocabulary as rv
        defs = rv.definitions(); by_ident = {}
        for alias, ident in rv.aliases().items(): by_ident.setdefault(ident, set()).add(alias)
        for ident, names in by_ident.items():
            d = defs.get(ident, {}); m = d.get('measure_definition') or {}
            add(ident, 'governed_metric', names, ' '.join(str(x) for x in (d.get('name'), d.get('summary'), m.get('name'),
                m.get('steward'), m.get('certification'), m.get('spec_year'), m.get('programs')) if x))
    except Exception as exc:  # noqa: BLE001 - a layer that cannot load is skipped, never a refusal
        index.append({'id': 'governed_metric_unavailable', 'layer': type(exc).__name__, 'names': set(), 'regex': None, 'text': ''})
    for f in sorted((ROOT / 'config' / 'agere' / 'measures').glob('*.json')):
        try: m = json.loads(f.read_text())
        except ValueError: continue
        if not isinstance(m, dict) or 'id' not in m: continue
        add('measure.' + m['id'], 'measure_library', [m['id'], m.get('name', ''), *m.get('aliases', [])],
            ' '.join(str(m.get(k, '')) for k in ('name', 'steward', 'certification', 'spec_year', 'programs')))
    try:
        for k, d in json.loads((ROOT / 'config' / 'agere' / 'definitions.json').read_text())['definitions'].items():
            std = d.get('standard') or {}
            add('definitions.' + k, 'definitions_json', [k.replace('_', ' '), d.get('label', '')],
                ' '.join(str(x) for x in (d.get('label'), d.get('agere_default'), std.get('name'), std.get('summary')) if x))
    except (OSError, ValueError, KeyError): pass
    from . import default_definitions as dd
    for k, rx in dd.ALIASES.items():
        if k == 'measure_gap': continue  # bare "gap" is a shape, not a definition: the measure itself must be governed
        d = dd.DEFINITIONS[k]; add('default.' + k, 'default_definitions', [], d['name'] + ' ' + d['basis'], re.compile(rx, re.I))
    try:
        from . import vbc_knowledge
        for e in vbc_knowledge.load(): add('knowledge.' + e['id'], 'vbc_knowledge', [e['title']], e['title'] + ' ' + e.get('summary', ''))
    except Exception: pass  # noqa: BLE001 - explanation corpus is optional context
    _INDEX = index
    return index


def governed_match(term):
    """{'status': governed|ambiguous|ungoverned, 'match': entry id, 'layer', 'extra': qualifier words the match lacks}."""
    t = norm(term); best = None
    for e in governed_index():
        hits = [n for n in e['names'] if _phrase_in(n, t)]
        if e['regex'] is not None:
            m = e['regex'].search(term)
            if m: hits.append(norm(m.group(0)))
        if not hits: continue
        alias = max(hits, key=len)
        rest = [w for w in re.sub(r'(?<![a-z0-9])' + re.escape(alias) + r'(?![a-z0-9])', ' ', t).split() if w not in FILLER]
        extra = [w for w in rest if not _phrase_in(w, e['text'])]
        cand = (len(extra), -len(alias), e, extra)
        if best is None or cand[:2] < best[:2]: best = cand
    if best is None: return {'status': 'ungoverned', 'match': None, 'layer': None, 'extra': []}
    _, _, e, extra = best
    return {'status': 'ambiguous' if extra else 'governed', 'match': e['id'], 'layer': e['layer'], 'extra': extra,
            'governed_text': e['text'][:400]}


def _confirm_with_system_one(question, term, match, *, ledger=None, allow_live=True):
    """True when System One says the governed definition already covers the term; None when System One is unavailable."""
    try:
        from . import system_one
    except ImportError:
        return None
    options = {'governed': f'The governed definition ({match["match"]}: {match.get("governed_text", "")[:240]}) is the measure the question means by "{term}".',
               'needs_standard_definition': f'"{term}" names a different or more specific published (CMS/NCQA/AHRQ) definition than the governed one.',
               'unknown': 'Cannot tell from the question.'}
    ask = f'In the question "{question}", is the term "{term}" fully defined by the governed definition?'
    try:
        fn = getattr(system_one, 'typed_question', None)
        if fn is not None: out = fn(ask, options, ledger=ledger, allow_live=allow_live, task_key='definition_governed')
        else: out = system_one.decide(ask, options, [], as_of=date.today().isoformat(), task_key='definition_governed',
                                      ledger=ledger, allow_live=allow_live)
    except Exception:  # noqa: BLE001 - System One failing leaves the deterministic default
        return None
    if not isinstance(out, dict) or out.get('label') not in options: return None
    p = (out.get('calibrated_probabilities') or out.get('probabilities') or {}).get(out['label'], 0)
    return out['label'] == 'governed' and p >= 0.8


def detect_details(question, *, confirm=None, ledger=None, allow_live=True):
    """[{term, status, match, layer, extra, needs_definition, decided_by}] for every candidate term."""
    rows = []
    for term in candidates(question):
        m = governed_match(term); decided = 'deterministic'
        need = m['status'] == 'ungoverned'
        if m['status'] == 'ambiguous':
            verdict = (confirm or _confirm_with_system_one)(question, term, m, ledger=ledger, allow_live=allow_live)
            need = verdict is not True; decided = 'system_one' if verdict is not None else 'deterministic_default'
        rows.append({'term': term, **m, 'needs_definition': need, 'decided_by': decided})
    return rows


def detect(question, *, confirm=None, ledger=None, allow_live=True):
    """Terms the question needs a definition for that no governed layer already defines."""
    return [r['term'] for r in detect_details(question, confirm=confirm, ledger=ledger, allow_live=allow_live) if r['needs_definition']]

# ---------------------------------------------------------------- resolution -------------------------------------------
PROMPT = ('Give the standard published US definition of the healthcare analytics term "{term}" as used in value-based care, '
          'quality measurement or risk adjustment. Prefer the measure steward or program owner: CMS (MSSP, ACO REACH, '
          'Medicare Advantage, CMS-HCC, eCQM, MIPS), NCQA (HEDIS), AHRQ (Quality Indicators), PQA, then peer-reviewed or '
          'widely used industry definitions when no steward defines it. Reply with ONLY a JSON object, no prose: '
          '{{"term": "{term}", "definition": "one or two sentences in plain words", "numerator": "what is counted, or null", '
          '"denominator": "the population or base, or null", "exclusions": ["exclusions, empty if none"], '
          '"period": "measurement period or lookback, or null", "steward": "organization and measure/spec id and year", '
          '"standard": true if a steward publishes this exact term else false (then say whose convention it is in definition), '
          '"source_ids": [numbers of the search results the definition comes from]}}.')


def parse_response(raw):
    """(json object, {search result id: {title,url}}, cited ids) from an Agent API response."""
    results = {}
    for item in raw.get('output') or []:
        if item.get('type') == 'search_results':
            for r in item.get('results') or []:
                if r.get('url'): results[r.get('id')] = {'title': r.get('title') or r['url'], 'url': r['url']}
    text = ''.join(c.get('text', '') for item in raw.get('output') or [] if item.get('type') == 'message'
                   for c in item.get('content') or [] if c.get('type') == 'output_text')
    m = re.search(r'\{.*\}', text, re.S)
    if not m: raise ValueError('No JSON object in definition answer')
    value = json.loads(m.group(0))
    if not isinstance(value, dict): raise ValueError('Definition answer is not an object')
    cited = [int(x) for x in re.findall(r'\[(?:web:)?(\d+)\]', text)]
    return value, results, cited


def _text(v):
    v = re.sub(r'\s*\[(?:web:)?\d+\]', '', v).strip() if isinstance(v, str) else None  # inline search-result markers
    return v if v and v.lower() not in ('null', 'none', 'n/a') else None


def entry_from(term, raw, preset):
    value, results, cited = parse_response(raw)
    ids = []
    for x in list(value.get('source_ids') or []) + cited:
        try: ids.append(int(x))
        except (TypeError, ValueError): pass
    sources = [results[i] for i in dict.fromkeys(ids) if i in results]
    exclusions = value.get('exclusions')
    exclusions = [e for e in (exclusions if isinstance(exclusions, list) else [exclusions]) if _text(e)]
    entry = {'term': term, 'definition': _text(value.get('definition')), 'numerator': _text(value.get('numerator')),
             'denominator': _text(value.get('denominator')), 'exclusions': [_text(e) for e in exclusions],
             'period': _text(value.get('period')), 'steward': _text(value.get('steward')),
             'standard': value.get('standard') if isinstance(value.get('standard'), bool) else None,
             'sources': sources, 'provider': PROVIDER, 'preset': preset, 'served_model': raw.get('served_model') or raw.get('model')}
    entry['status'] = 'not_found' if not entry['definition'] else 'cited' if sources else 'uncited'
    return entry


def _load(path):
    try: doc = json.loads(Path(path).read_text())
    except (OSError, ValueError): doc = {}
    return doc if doc.get('schema') == SCHEMA else {'schema': SCHEMA, 'entries': {}}


def _save(path, doc):
    doc.update(schema=SCHEMA, version=VERSION, status='machine-researched standard definitions with cited sources; not reviewed by a person',
               researched_via='Perplexity Agent API (POST /v1/responses, web search), preset fast then medium when fast found no cited definition',
               use='Used only for terms no governed layer defines; every answer under one names the definition and its sources.')
    doc['entries'] = dict(sorted(doc.get('entries', {}).items()))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + '.tmp'); tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + '\n'); tmp.replace(path)


def _public(entry, *, cached, cost):
    keys = ('term', 'definition', 'numerator', 'denominator', 'exclusions', 'period', 'sources', 'provider')
    out = {k: entry.get(k) for k in keys}
    out.update(steward=entry.get('steward'), standard=entry.get('standard'), status=entry.get('status'),
               retrieved=entry.get('retrieved'), cost_usd=cost, cached=cached)
    out['exclusions'] = out['exclusions'] or []; out['sources'] = out['sources'] or []
    return out


def resolve(term, *, ledger=None, allow_live=True, cache_path=None, transport=None, receipts=None, refresh=False):
    """Cited standard definition for term; cached in definitions_resolved.json. Never raises for a provider failure."""
    path = Path(cache_path or os.environ.get('AGERE_DEFINITIONS_RESOLVED') or CACHE); key = norm(term)
    with _LOCK: doc = _load(path)
    hit = doc['entries'].get(key)
    if hit and hit.get('status') == 'cited' and not refresh: return _public(hit, cached=True, cost=0.0)
    blank = {'term': term, 'provider': PROVIDER, 'exclusions': [], 'sources': []}
    if not allow_live:
        return _public(hit or {**blank, 'status': 'not_resolved_offline'}, cached=bool(hit), cost=0.0)
    from ..gateway import PersistentGateway, BudgetLedger, default_ledger_path
    if not os.environ.get(PersistentGateway.ENDPOINTS[PROVIDER][1]):
        return _public(hit or {**blank, 'status': 'provider_unavailable'}, cached=bool(hit), cost=0.0)
    from .context_student import send
    from ...contracts import digest
    own = ledger is None; ledger = ledger or BudgetLedger(default_ledger_path())
    out_dir = Path(receipts or RECEIPTS); out_dir.mkdir(parents=True, exist_ok=True)
    spend, best, errors = 0.0, None, []
    try:
        for preset in PRESETS:
            body = {'preset': preset, 'input': PROMPT.format(term=term.replace('"', "'"))}
            if refresh: body['metadata'] = {'agere_refresh': date.today().isoformat()}  # new request identity: explicit paid retry
            try:
                entry, receipt = send(body, PROVIDER, ledger, out_dir, parse=lambda raw, p=preset: entry_from(term, raw, p),
                                      transport=transport, timeout=120)
            except Exception as exc:  # noqa: BLE001 - one failed preset lets the next try; never a refusal
                errors.append(f'{preset}: {type(exc).__name__}: {str(exc)[:160]}'); continue
            spend += (receipt.get('cost_estimate') or {}).get('estimated_usd') or 0
            entry.update(request_hash=receipt.get('request_hash'), query_digest=digest(body), retrieved=date.today().isoformat())
            if best is None or (entry['status'] == 'cited') > (best['status'] == 'cited') or (best['status'] == 'not_found' and entry['definition']):
                best = entry
            if entry['status'] == 'cited': break
    finally:
        if own: ledger.close()
    if best is None:
        return {**_public(hit or {**blank, 'status': 'provider_error'}, cached=bool(hit), cost=spend), 'errors': errors}
    best['cost_usd'] = round(spend, 6)
    if best['definition']:
        with _LOCK:
            doc = _load(path); doc['entries'][key] = best; _save(path, doc)
    return _public(best, cached=False, cost=round(spend, 6))

# ---------------------------------------------------------------- planner context --------------------------------------
INSTRUCTION = ('resolved_definitions are cited standard (CMS/NCQA/AHRQ or named convention) definitions for terms in this '
               'question that no governed measure, concept or default definition covers. Answer under them with the received '
               'data: compute numerator/denominator/exclusions/period as written, mapping each to warehouse fields; this is '
               'applying a cited standard, not recreating a governed definition. Put each one in definitions as '
               '{"term", "basis": definition + source title and URL}. A term with computed_with_governed has no steward '
               'numerator/denominator: compute it with that governed definition and state both. Elements the data cannot support become a labelled '
               'limitation, never a refusal of the whole question.')
_MEMO = {}


def context(question, *, ledger=None, allow_live=True, confirm=None, cache_path=None, transport=None):
    """{} when nothing needs a definition, else {'instruction', 'terms': [resolved], 'definitions': [{term, basis}], 'unresolved'}."""
    memo_key = (question, allow_live, str(cache_path))
    if memo_key in _MEMO: return _MEMO[memo_key]
    rows = [r for r in detect_details(question, confirm=confirm, ledger=ledger, allow_live=allow_live) if r['needs_definition']]
    if not rows: return {}
    resolved = []
    for row in rows:
        r = resolve(row['term'], ledger=ledger, allow_live=allow_live, cache_path=cache_path, transport=transport)
        if row['match'] and not (r.get('numerator') and r.get('denominator')):
            # No computable steward definition (e.g. "MSSP avoidable spend" is an industry phrase): compute with the closest
            # governed definition and say both - the cited meaning and the governed computation used.
            r['computed_with_governed'] = row['match']
        resolved.append(r)
    got = [r for r in resolved if r.get('definition')]
    out = {'schema': VERSION, 'instruction': INSTRUCTION, 'terms': got, 'definitions': [disclosure(r) for r in got],
           'unresolved': [r['term'] for r in resolved if not r.get('definition')],
           'new_paid_api_usd': round(sum(r.get('cost_usd') or 0 for r in resolved), 6)}
    if got: _MEMO[memo_key] = out  # unresolved results are retried on the next question, never frozen in-process
    return out


def disclosure(r):
    """One analysis `definitions` entry naming the definition used and its sources."""
    parts = [r['definition']]
    for label, k in (('Numerator', 'numerator'), ('Denominator', 'denominator'), ('Period', 'period')):
        if r.get(k): parts.append(f'{label}: {r[k]}')
    if r.get('exclusions'): parts.append('Exclusions: ' + '; '.join(r['exclusions']))
    src = '; '.join(f"{s['title']} ({s['url']})" for s in r.get('sources', [])[:3]) or 'no cited source'
    steward = f" [{r['steward']}]" if r.get('steward') else ''
    governed = (f" Computed with the governed definition {r['computed_with_governed']} (no steward numerator/denominator exists)."
                if r.get('computed_with_governed') else '')
    return {'term': r['term'], 'basis': ' '.join(p.rstrip('.') + '.' for p in parts) + f'{steward} Source: {src}' + governed,
            'provider': r.get('provider'), 'retrieved': r.get('retrieved')}


def disclose(answer, ctx):
    """Add the resolved definitions an answer used to answer['definitions'] (idempotent); returns answer."""
    if not ctx or not isinstance(answer, dict): return answer
    have = {norm(d.get('term', '')) for d in answer.get('definitions') or [] if isinstance(d, dict)}
    answer['definitions'] = [*(answer.get('definitions') or []), *(d for d in ctx.get('definitions', []) if norm(d['term']) not in have)]
    return answer
