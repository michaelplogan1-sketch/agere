"""Why a guideline heart-failure medication is not being taken, as typed judgments on short passages.

  code          finds every passage that follows a mention of a pillar medication (the drug's own line or entry),
                and keeps each distinct passage once however many documents repeat it
  System One    one multiple-choice judgment per passage: which kind of reason it states, or none
  small model   the same choice, only for passages System One cannot decide; then the frontier model
  code          the passage itself is the quote (the document's own words by construction); code groups and counts

No model writes or copies text. A passage no reader decides is reported as undecided.
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import models, store

PILLARS = {'Evidence-based beta blocker': ('carvedilol', 'metoprolol succinate', 'metoprolol', 'bisoprolol', 'beta-blocker', 'beta blocker'),
           'RAAS inhibitor (ACE inhibitor, ARB or ARNI)': ('lisinopril', 'enalapril', 'ramipril', 'losartan', 'valsartan', 'sacubitril', 'entresto', 'ace inhibitor', 'arni', 'raas'),
           'MRA': ('spironolactone', 'eplerenone', 'mra'),
           'SGLT2 inhibitor': ('dapagliflozin', 'empagliflozin', 'farxiga', 'jardiance', 'sglt2')}
OTHER_DRUG = re.compile(r'\b[a-z][a-z-]{3,}(?:\s[a-z-]+)?\s\d+(?:\.\d+)?\s?(?:mg|mcg|units?)\b', re.I)  # "furosemide 40 mg": where another drug's entry begins
OPTIONS = {'side_effect': 'The patient had or reports a side effect or adverse effect, or did not like how the medication made them feel.',
           'cost_or_supply': 'Cost, access, running out of supply, or the burden of taking pills.',
           'patient_declined': 'The patient declined, does not agree, or was unsure it was still needed.',
           'lab_or_vital_limit': 'A lab value or vital sign limits it: potassium, kidney function, low blood pressure or heart rate.',
           'regimen_unclear': 'The clinician held or did not increase it because the current directions or actual use are unclear, or the patient attributes stopping to another clinician.',
           'other_reason': 'Another stated reason for not taking, holding, stopping, not starting or not increasing it.',
           'none': 'The passage states no reason for the medication not being taken, held, stopped, not started or not increased.'}
MIN_CONFIDENCE = 0.6
MAX_PASSAGES = 400
WINDOW = 420
SYSTEM = '''You classify one short passage from a patient record about one medication. Choose the single option that describes
what the passage says about that medication. Return exactly one JSON object: {"label": "<option key>"}.
Options:
''' + '\n'.join(f'- {k}: {v}' for k, v in OPTIONS.items())
_NAMES = sorted(((n, p) for p, names in PILLARS.items() for n in names), key=lambda x: -len(x[0]))
_MENTION = re.compile(r'(?<![a-z])(' + '|'.join(re.escape(n) for n, _ in _NAMES) + r')(?![a-z])', re.I)


def passages(texts):
    """-> [{'pillar','drug','text','record_ids','first','last'}]: each distinct passage once, with every document that carries it."""
    found = {}
    for rid, doc in texts.items():
        text = ' '.join(doc['text'].split())
        for m in _MENTION.finditer(text):
            start = text.rfind('. ', 0, m.start()) + 2 if text.rfind('. ', 0, m.start()) >= 0 and m.start() - text.rfind('. ', 0, m.start()) < 160 else m.start()
            rest = text[m.end():m.end() + WINDOW]
            stop = OTHER_DRUG.search(rest, 12)  # the next drug's entry ends this one's
            end = m.end() + (stop.start() if stop else len(rest))
            cut = text.rfind('. ', m.end(), end)
            body = text[start:(cut + 1 if cut > m.end() + 20 else end)].strip()
            if len(body) < 25: continue
            drug = m.group(1).lower()
            pillar = next(p for n, p in _NAMES if n == drug)
            key = (pillar, re.sub(r'\d{4}-\d{2}-\d{2}', '#', body.lower()))
            hit = found.setdefault(key, {'pillar': pillar, 'drug': drug, 'text': body, 'record_ids': [], 'dates': []})
            if rid not in hit['record_ids']: hit['record_ids'].append(rid); hit['dates'].append(str(doc.get('event_date') or ''))
    out = list(found.values())
    for p in out: p['first'], p['last'] = min(p['dates']), max(p['dates'])
    return out


def _system_one(passage, s1_ledger, as_of):
    from ..clinical import system_one
    usd = 0.0
    for name in ('perplexity', 'jev'):
        try:
            got = system_one.choice(f"Medication: {passage['drug']}\nPassage from the record: {passage['text']}\nWhat does the passage say about this medication?",
                                    OPTIONS, [], as_of=as_of, provider=name, ledger=s1_ledger)
        except Exception as exc:
            reason = f'{type(exc).__name__}: {str(exc)[:60]}'
            continue
        usd += got.get('cost_usd') or 0.0
        label = got.get('label'); p = float((got.get('probabilities') or {}).get(label, 0.0)) if label else 0.0
        if label in OPTIONS and p >= MIN_CONFIDENCE: return {'by': 'system1', 'label': label, 'confidence': round(p, 3), 'usd': usd, 'tokens': 0}
        reason = 'below the confidence threshold'
    return {'by': 'system1', 'label': None, 'usd': usd, 'tokens': 0, 'reason': reason}


def _model(tier, passage):
    text, step = models._claude(models.SLM if tier == 'slm' else models.LLM, SYSTEM, f"Medication: {passage['drug']}\nPassage: {passage['text']}")
    try:
        label = models._json(text).get('label')
    except ValueError:
        label = None
    return {'by': tier, 'label': label if label in OPTIONS else None, 'usd': step['usd'], 'tokens': step['tokens'], **({} if label in OPTIONS else {'reason': 'unusable output'})}


def judge(passage, readers, s1_ledger, as_of):
    attempts = []
    for tier in readers:
        got = _system_one(passage, s1_ledger, as_of) if tier == 'system1' else _model(tier, passage)
        attempts.append(got)
        if got['label']: break
    return {**passage, 'label': attempts[-1]['label'], 'by': attempts[-1]['by'] if attempts[-1]['label'] else None, 'attempts': attempts}


def execute(question, sql, params, read, db, *, readers=('system1', 'slm', 'llm'), s1_ledger=None):
    """Reading mode 'medication_reasons'. -> (columns, rows, validation, steps, evidence), in the shape read.execute returns."""
    used = set(re.findall(r'(?<![:\w]):([a-z_][a-z0-9_]*)', sql))
    columns, selected = store.run(sql, {k: v for k, v in (params or {}).items() if k in used}, db)
    if 'record_id' not in columns: raise store.RecipeError('A reading plan must select record_id')
    ids = list(dict.fromkeys(r[columns.index('record_id')] for r in selected))
    texts = {i: t for i, t in store.texts(ids, db).items() if t['text'].strip()}
    found = passages(texts)
    if len(found) > MAX_PASSAGES: raise store.RecipeError(f'{len(found)} medication passages; this tier judges at most {MAX_PASSAGES} per question')
    clock, started = store.as_of(db), time.monotonic()
    with ThreadPoolExecutor(8) as pool:
        judged = list(pool.map(lambda p: judge(p, readers, s1_ledger, clock), found)) if found else []
    steps = []
    for tier in readers:
        tries = [a for j in judged for a in j['attempts'] if a['by'] == tier]
        if tries: steps.append({'tier': 'read', 'reader': tier, 'calls': len(tries), 'decided': sum(bool(a['label']) for a in tries), 'tokens': sum(a['tokens'] for a in tries),
                                'usd': sum(a['usd'] for a in tries), 'outcome': 'read', **({'model': models.SLM if tier == 'slm' else models.LLM} if tier != 'system1' else {'model': 'system-one'}),
                                **({'undecided_reasons': sorted({str(a.get('reason'))[:80] for a in tries if not a['label']})[:3]} if any(not a['label'] for a in tries) else {})})
    stated = [j for j in judged if j['label'] and j['label'] != 'none']
    undecided = [j for j in judged if not j['label']]
    rows = [[j['pillar'], j['drug'], j['label'], j['text'], j['first'], j['last'], len(j['record_ids'])] for j in sorted(stated, key=lambda j: (j['pillar'], j['first']))]
    if not rows: rows = [['(none)', '(none)', 'no reason stated', f'{len(judged)} passages about these medications were read; none states a reason', '', '', 0]]
    checks = [{'name': 'documents_selected', 'ok': bool(ids), 'detail': f'{len(ids)} documents'},
              {'name': 'passages_are_document_words', 'ok': all(any(j['text'] in ' '.join(texts[r]['text'].split()) for r in j['record_ids']) for j in judged), 'detail': f'{len(judged)} passages'},
              {'name': 'every_passage_decided', 'ok': not undecided, 'detail': f'{len(undecided)} undecided'}]
    evidence = [{'record_id': j['record_ids'][0], 'label': 'yes' if j['label'] and j['label'] != 'none' else 'no' if j['label'] else None, 'by': j['by'],
                 'value': f"{j['drug']}: {j['label']}" if j['label'] and j['label'] != 'none' else None, 'quote': j['text'] if j['label'] and j['label'] != 'none' else None,
                 'documents_carrying_it': len(j['record_ids'])} for j in judged]
    steps.append({'tier': 'read', 'outcome': 'passages_found_by_code', 'passages': len(judged), 'documents': len(ids), 'seconds': round(time.monotonic() - started, 2), 'tokens': 0, 'usd': 0.0})
    return ['pillar', 'medication', 'reason_kind', 'passage', 'first_date', 'last_date', 'documents'], rows, {'ok': all(c['ok'] for c in checks), 'checks': checks}, steps, evidence
