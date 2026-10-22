"""Perplexity Agent search as a cited EXTERNAL evidence source when the warehouse lacks the context a reader needs.

The warehouse holds what happened to the patient; it does not hold what a guideline or label says (is an SGLT2 inhibitor
contraindicated at eGFR 18, which beta blockers count for HFrEF, what is the potassium cut-off for an MRA). Readers asked
those questions from patient passages alone guess or say unknown. Here:
  needs_external(question, passages)  the question asks for general clinical criteria (contraindication, guideline,
                                       indicated, threshold, dose, interaction, eligible, criteria) or the warehouse returned
                                       no passages at all.
  search(query)                        one Perplexity Agent API request (gateway ENDPOINTS['perplexity_agent'], preset fast),
                                       answer + the provider's own search results; sources come from the search results,
                                       never model-written URLs. Cached in artifacts/external-evidence/cache.json (each query
                                       paid once; identical requests also replay from the provider ledger at $0).
  evidence_item(result, as_of)         a System One evidence item marked '[external reference, not patient data]' citing
                                       its URLs, for atomic sub-questions and the review cascade.
Only the clinical query text is sent: no patient identifiers, no record text, no user identity.
"""
import json
import os
import re
import threading
from datetime import date
from pathlib import Path

VERSION = 'external-evidence/1'
ROOT = Path(__file__).resolve().parents[3]
PROVIDER = 'perplexity_agent'
_LOCK = threading.Lock()
CRITERIA = re.compile(r'\b(?:contraindicat\w*|guideline\w*|indicat\w*|threshold|cut-?off|dose|dosing|interaction\w*|eligib\w*|'
                      r'criteria|recommend\w*|label(?:ed|ing)?|evidence[- ]based|safe(?:ty)?|renal(?:ly)? (?:dos\w+|adjust\w*)|'
                      r'black box|first[- ]line|standard of care)\b', re.I)
PATIENT_ID = re.compile(r'(?<![\w-])[A-Za-z]{0,12}(?:[-_][A-Za-z]{1,8})?[-_]?\d[\w-]{0,40}(?![\w-])')
PROMPT = ('Answer this clinical reference question for a US healthcare analyst, citing guidelines (ACC/AHA/HFSA, KDIGO, ADA, '
          'FDA labeling) or peer-reviewed sources: "{query}". Reply with ONLY a JSON object, no prose: {{"answer": "two to four '
          'sentences in plain words with the specific thresholds", "criteria": ["each specific criterion or threshold"], '
          '"source_ids": [numbers of the search results the answer comes from]}}.')


def cache_path():
    return Path(os.environ.get('AGERE_EXTERNAL_EVIDENCE_CACHE') or ROOT/'artifacts/external-evidence/cache.json')


def scrub(query):
    """The clinical part only: patient identifiers and 'this patient' phrasing removed."""
    q = PATIENT_ID.sub('a patient', str(query))
    q = re.sub(r'For this patient only, does the submitted evidence establish:\s*', '', q)
    q = re.sub(r'\b(?:this|the) patient\b', 'a patient', q, flags=re.I)
    return ' '.join(q.split())[:600]


def needs_external(question, passages=None):
    return bool(CRITERIA.search(question or '')) or passages is not None and not passages


def reference_query(atom, subject=''):
    """A general reference question for one atomic sub-question ('documented contraindication to sglt2 inhibitor' ->
    'What are the contraindications to an SGLT2 inhibitor ...')."""
    drug = (subject or '').replace('_', ' ')
    if re.search(r'contraindicat', atom, re.I):
        return f'What are the guideline and FDA-label contraindications and lab/vital thresholds (eGFR, potassium, blood pressure, heart rate) for {drug or "this drug"}?'
    if re.search(r'not indicated|indicat', atom, re.I):
        return f'For which patients is {drug or "this drug"} indicated, and when is it documented as not indicated, per current guidelines?'
    return scrub(atom)


def parse(raw):
    results = {}
    for item in raw.get('output') or []:
        if item.get('type') == 'search_results':
            for r in item.get('results') or []:
                if r.get('url'): results[r.get('id')] = {'title': r.get('title') or r['url'], 'url': r['url']}
    text = ''.join(c.get('text', '') for item in raw.get('output') or [] if item.get('type') == 'message'
                   for c in item.get('content') or [] if c.get('type') == 'output_text')
    m = re.search(r'\{.*\}', text, re.S)
    value = json.loads(m.group(0)) if m else {'answer': re.sub(r'\s*\[(?:web:)?\d+\]', '', text).strip()}
    if not isinstance(value, dict): value = {'answer': str(value)}
    ids = []
    for x in list(value.get('source_ids') or []) + re.findall(r'\[(?:web:)?(\d+)\]', text):
        try: ids.append(int(x))
        except (TypeError, ValueError): pass
    sources = [results[i] for i in dict.fromkeys(ids) if i in results] or list(results.values())[:3]
    answer = re.sub(r'\s*\[(?:web:)?\d+\]', '', str(value.get('answer') or '')).strip()
    criteria = [re.sub(r'\s*\[(?:web:)?\d+\]', '', str(c)).strip() for c in value.get('criteria') or [] if str(c).strip()]
    return {'answer': answer, 'criteria': criteria, 'sources': sources, 'served_model': raw.get('served_model') or raw.get('model'),
            'status': 'cited' if answer and sources else 'uncited' if answer else 'not_found'}


def _load():
    try: return json.loads(cache_path().read_text())
    except (OSError, ValueError): return {'schema': VERSION, 'entries': {}}


def search(query, *, ledger=None, allow_live=True, transport=None, receipts=None):
    """Cited external answer -> {'query','answer','criteria','sources','status','cost_usd','cached'}; never raises."""
    q = scrub(query)
    key = ' '.join(re.findall(r'[a-z0-9]+', q.lower()))
    with _LOCK: hit = _load()['entries'].get(key)
    if hit and hit.get('status') == 'cited': return {**hit, 'cost_usd': 0.0, 'cached': True}
    blank = {'query': q, 'answer': None, 'criteria': [], 'sources': [], 'provider': PROVIDER}
    if not allow_live: return {**(hit or blank), 'status': (hit or {}).get('status') or 'not_searched_offline', 'cost_usd': 0.0, 'cached': bool(hit)}
    from ..gateway import PersistentGateway, BudgetLedger, default_ledger_path
    if not os.environ.get(PersistentGateway.ENDPOINTS[PROVIDER][1]):
        return {**blank, 'status': 'provider_unavailable', 'cost_usd': 0.0, 'cached': False}
    from .context_student import send
    own = ledger is None; ledger = ledger or BudgetLedger(default_ledger_path())
    out = Path(receipts or ROOT/'artifacts/external-evidence/receipts'); out.mkdir(parents=True, exist_ok=True)
    try:
        body = {'preset': 'fast', 'input': PROMPT.format(query=q.replace('"', "'"))}
        result, receipt = send(body, PROVIDER, ledger, out, parse=parse, transport=transport, timeout=120)
    except Exception as exc:  # noqa: BLE001 - external context is optional; the warehouse answer stands
        return {**blank, 'status': 'provider_error', 'error': f'{type(exc).__name__}: {exc}'[:200], 'cost_usd': 0.0, 'cached': False}
    finally:
        if own: ledger.close()
    cost = 0.0 if receipt.get('cache_hit') else float((receipt.get('cost_estimate') or {}).get('estimated_usd') or 0.0)
    entry = {**blank, **result, 'retrieved': date.today().isoformat(), 'request_hash': receipt.get('request_hash')}
    if result['answer']:
        with _LOCK:
            doc = _load(); doc.setdefault('entries', {})[key] = entry; doc['schema'] = VERSION
            cache_path().parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_path().with_suffix('.tmp'); tmp.write_text(json.dumps(doc, indent=1) + '\n'); tmp.replace(cache_path())
    return {**entry, 'cost_usd': round(cost, 6), 'cached': bool(receipt.get('cache_hit'))}


def evidence_item(result, as_of):
    """System One evidence item for a cited external answer (None when there is nothing to cite)."""
    if not result or not result.get('answer'): return None
    src = '; '.join(f"{s['title']} ({s['url']})" for s in result.get('sources', [])[:3]) or 'uncited'
    crit = (' Criteria: ' + '; '.join(result['criteria'][:6]) + '.') if result.get('criteria') else ''
    text = f"[external reference, not patient data] {result['answer']}{crit} Sources: {src}"
    url = (result.get('sources') or [{}])[0].get('url') or 'perplexity_agent_search'
    key = re.sub(r'[^a-z0-9]+', '-', result.get('query', '').lower())[:60]
    return {'id': f'external:{key}', 'text': text[:700], 'date': str(as_of)[:10], 'note_id': url}
