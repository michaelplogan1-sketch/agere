"""Why a guideline heart-failure medication is not being taken, as typed judgments on short passages.

  code          finds every passage that follows a mention of a pillar medication (the drug's own line or entry),
                and keeps each distinct passage once however many documents repeat it
  encoder       (local MiniLM) finds sentences that read like a reason but name no medication; the local
                reranker (BGE) keeps the best few. These are candidates only: nothing is answered by retrieval.
  System One    one multiple-choice judgment per passage: which kind of reason it states, or none
  local model   (Qwen, on this machine, no per-call cost) for passages System One cannot decide; its choice is
                used only when it agrees with the option System One was leaning to
  small model   the same choice for what is still undecided; then the frontier model
  code          the passage itself is the quote (the document's own words by construction); code groups and counts

No model writes or copies text. A passage no reader decides is reported as undecided.
"""
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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
           'not_taken_no_reason': 'The passage says the medication is not being used, was used on few or no days, or has none left, without giving a reason.',
           'other_reason': 'Another stated reason for not taking, holding, stopping, not starting or not increasing it.',
           'none': 'The passage states no reason for the medication not being taken, held, stopped, not started or not increased.'}
MIN_CONFIDENCE = 0.6
MAX_PASSAGES = 400
REASON_KINDS = ('side_effect', 'cost_or_supply', 'patient_declined', 'lab_or_vital_limit', 'regimen_unclear', 'other_reason')
UNNAMED = 'Heart failure therapy (medication not named in the passage)'
RETRIEVED_PER_PATIENT = 8      # sentences the encoder and reranker may add as candidates
QUERY = 'The reason a heart failure medication was held, stopped, not started, not increased or is not being taken: blood pressure, potassium, kidney function, side effect, cost, supply, or the patient declined.'
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


_retrieval_state, _retrieval_lock = {}, threading.Lock()


def retrieval_available():
    root = _models_dir()
    return os.environ.get('AGERE_TIERED_ENCODER', 'off') == 'on' and root is not None and (root / 'minilm-l6-v2').is_dir() and (root / 'bge-reranker-v2-m3').is_dir()


def retrieved(texts, have, limit=RETRIEVED_PER_PATIENT):
    """Sentences that read like a reason but lie in no passage found by medication name. Local encoder for recall, local
    reranker for order; both offline. -> (passages in the same shape as passages(), with the medication unnamed; stats)."""
    covered = ' '.join(p['text'].lower() for p in have)
    seen = {}
    for rid, doc in texts.items():
        for sentence in (x.strip() for x in re.split(r'(?<=[.;:])\s+', ' '.join(doc['text'].split()))):
            if not 30 <= len(sentence) <= 320 or sentence.lower() in covered: continue
            hit = seen.setdefault(re.sub(r'\d{4}-\d{2}-\d{2}', '#', sentence.lower()), {'pillar': UNNAMED, 'drug': 'heart failure medication', 'text': sentence, 'record_ids': [], 'dates': []})
            if rid not in hit['record_ids']: hit['record_ids'].append(rid); hit['dates'].append(str(doc.get('event_date') or ''))
    pool = list(seen.values())
    if not pool: return [], {'candidates': 0, 'kept': 0}
    started = time.monotonic()
    with _retrieval_lock:
        os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', TOKENIZERS_PARALLELISM='false')
        from sentence_transformers import CrossEncoder, SentenceTransformer, util
        if 'encoder' not in _retrieval_state:
            _retrieval_state['encoder'] = SentenceTransformer(str(_models_dir() / 'minilm-l6-v2'), device='cpu')
            _retrieval_state['reranker'] = CrossEncoder(str(_models_dir() / 'bge-reranker-v2-m3'), device='cpu', max_length=256)
        encoder = _retrieval_state['encoder']
        scores = util.cos_sim(encoder.encode([QUERY], normalize_embeddings=True), encoder.encode([p['text'] for p in pool], normalize_embeddings=True, batch_size=64))[0].tolist()
        short = [p for _, p in sorted(zip(scores, pool), key=lambda x: -x[0])[:4 * limit]]
        ranked = [float(v) for v in _retrieval_state['reranker'].predict([(QUERY, p['text']) for p in short])]
    best = [p for _, p in sorted(zip(ranked, short), key=lambda x: -x[0])[:limit]]
    for p in best: p['first'], p['last'], p['found_by'] = min(p['dates']), max(p['dates']), 'encoder_and_reranker'
    return best, {'candidates': len(pool), 'kept': len(best), 'seconds': round(time.monotonic() - started, 2)}


def _system_one(passage, s1_ledger, as_of):
    from ..clinical import system_one
    usd, leaning, reason = 0.0, None, 'no provider answered'
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
        reason, leaning = 'below the confidence threshold', (label if label in OPTIONS else leaning)
    return {'by': 'system1', 'label': None, 'usd': usd, 'tokens': 0, 'reason': reason, 'leaning': leaning}


def _model(tier, passage):
    text, step = models._claude(models.SLM if tier == 'slm' else models.LLM, SYSTEM, f"Medication: {passage['drug']}\nPassage: {passage['text']}")
    try:
        label = models._json(text).get('label')
    except ValueError:
        label = None
    return {'by': tier, 'label': label if label in OPTIONS else None, 'usd': step['usd'], 'tokens': step['tokens'], **({} if label in OPTIONS else {'reason': 'unusable output'})}


def _models_dir():
    root = os.environ.get('AGERE_MODELS_DIR') or (str(Path(os.environ['AGERE_BASE_MODEL']).parent) if os.environ.get('AGERE_BASE_MODEL') else None)
    return Path(root) if root and Path(root).is_dir() else None


_local_state, _local_lock = {}, threading.Lock()


def local_available():
    return os.environ.get('AGERE_TIERED_LOCAL', 'off') == 'on' and _models_dir() is not None and (_models_dir() / 'qwen3-4b').is_dir()


def _local(passage, leaning):
    """The local Qwen model picks an option. Offline; one generation at a time. Its choice counts only when it is the
    option System One was already leaning to: two independent weak signals that agree."""
    started = time.monotonic()
    with _local_lock:
        os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
        from mlx_lm import load, generate
        from mlx_lm.sample_utils import make_sampler
        if 'model' not in _local_state:
            _local_state['model'], _local_state['tokenizer'] = load(str(_models_dir() / 'qwen3-4b'), tokenizer_config={'local_files_only': True, 'trust_remote_code': False})
        tok = _local_state['tokenizer']
        prompt = tok.apply_chat_template([{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': f"Medication: {passage['drug']}\nPassage: {passage['text']}"}],
                                         tokenize=True, add_generation_prompt=True)
        text = generate(_local_state['model'], tok, prompt=prompt, max_tokens=24, sampler=make_sampler(temp=0.0), verbose=False)
    try:
        label = models._json(text).get('label')
    except ValueError:
        label = next((k for k in OPTIONS if k in str(text)), None)
    agreed = label in OPTIONS and label == leaning
    return {'by': 'local', 'label': label if agreed else None, 'usd': 0.0, 'tokens': 0, 'seconds': round(time.monotonic() - started, 2),
            **({} if agreed else {'reason': f'local model chose {label}; System One was leaning to {leaning}'})}


def judge(passage, readers, s1_ledger, as_of):
    attempts, leaning = [], None
    for tier in readers:
        if tier == 'local' and not leaning: continue  # nothing to agree with
        got = _system_one(passage, s1_ledger, as_of) if tier == 'system1' else _local(passage, leaning) if tier == 'local' else _model(tier, passage)
        attempts.append(got)
        leaning = got.get('leaning') or leaning
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
    retrieval = None
    if retrieval_available():
        more, retrieval = retrieved(texts, found)
        found += more
    if local_available() and 'local' not in readers and 'system1' in readers:
        readers = tuple(x for r in readers for x in ((r, 'local') if r == 'system1' else (r,)))
    if len(found) > MAX_PASSAGES: raise store.RecipeError(f'{len(found)} medication passages; this tier judges at most {MAX_PASSAGES} per question')
    clock, started = store.as_of(db), time.monotonic()
    with ThreadPoolExecutor(8) as pool:
        judged = list(pool.map(lambda p: judge(p, readers, s1_ledger, clock), found)) if found else []
    steps = []
    for tier in readers:
        tries = [a for j in judged for a in j['attempts'] if a['by'] == tier]
        if tries: steps.append({'tier': 'read', 'reader': tier, 'calls': len(tries), 'decided': sum(bool(a['label']) for a in tries), 'tokens': sum(a['tokens'] for a in tries),
                                'usd': sum(a['usd'] for a in tries), 'outcome': 'read', 'model': {'system1': 'system-one', 'local': 'qwen3-4b (local)', 'slm': models.SLM, 'llm': models.LLM}[tier],
                                **({'undecided_reasons': sorted({str(a.get('reason'))[:80] for a in tries if not a['label']})[:3]} if any(not a['label'] for a in tries) else {})})
    stated = [j for j in judged if j['label'] in REASON_KINDS]
    unused = [j for j in judged if j['label'] == 'not_taken_no_reason']
    undecided = [j for j in judged if not j['label']]
    rows = [[j['pillar'], j['drug'], j['label'], j['text'], j['first'], j['last'], len(j['record_ids'])] for j in sorted(stated, key=lambda j: (j['pillar'], j['first']))]
    rows += [[j['pillar'], j['drug'], 'non-use reported, no reason given', j['text'], j['first'], j['last'], len(j['record_ids'])] for j in sorted(unused, key=lambda j: (j['pillar'], j['first']))]
    if not rows: rows = [['(none)', '(none)', 'no reason stated', f'{len(judged)} passages about these medications were read; none states a reason', '', '', 0]]
    checks = [{'name': 'documents_selected', 'ok': bool(ids), 'detail': f'{len(ids)} documents'},
              {'name': 'passages_are_document_words', 'ok': all(any(j['text'] in ' '.join(texts[r]['text'].split()) for r in j['record_ids']) for j in judged), 'detail': f'{len(judged)} passages'},
              {'name': 'every_passage_decided', 'ok': not undecided, 'detail': f'{len(undecided)} undecided'}]
    evidence = [{'record_id': j['record_ids'][0], 'label': 'yes' if j['label'] in REASON_KINDS else 'no' if j['label'] else None, 'by': j['by'],
                 'value': f"{j['drug']}: {j['label']}" if j['label'] in REASON_KINDS else None, 'quote': j['text'] if j['label'] in REASON_KINDS else None,
                 'documents_carrying_it': len(j['record_ids'])} for j in judged]
    if retrieval: steps.append({'tier': 'read', 'outcome': 'candidates_from_encoder_and_reranker', 'model': 'minilm-l6-v2 + bge-reranker-v2-m3 (local)', **retrieval, 'tokens': 0, 'usd': 0.0})
    steps.append({'tier': 'read', 'outcome': 'passages_found_by_code', 'passages': len(judged), 'documents': len(ids), 'seconds': round(time.monotonic() - started, 2), 'tokens': 0, 'usd': 0.0})
    return ['pillar', 'medication', 'reason_kind', 'passage', 'first_date', 'last_date', 'documents'], rows, {'ok': all(c['ok'] for c in checks), 'checks': checks}, steps, evidence
