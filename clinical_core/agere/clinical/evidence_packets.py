"""Bounded, subject-anchored evidence packets with learned evidence selection, for every reader on the question path.

Readers (local students, MedGemma, Jev, Perplexity, the atomic System One sub-questions) all get the SAME packet:
  1. candidates   stack.retriever.search(query, patient=...) (FTS5 BM25 + MiniLM + RRF + BGE rerank), up to 20 passages.
  2. selection    learned source-category prior: source_selection.rank (MiniLM schema descriptors of the patient's received
                  categories, BGE-reranked against the question) orders the categories; passages from the top categories
                  are preferred. The G2 cosine threshold (MIN_COSINE) is not used as a gate: the reranker ORDER is the prior,
                  so selection never drops evidence to zero (bounded_evidence contract: never silently chop a sentence).
  3. anchoring    each passage splits into sentences with exact offsets into the source field; sentences naming the subject
                  (drug-class ingredients, class words) or a question content word are kept, with the previous sentence as
                  separate reference context (bounded_evidence.retrieve's 'previous').
  4. decision-preserving budget: score = passage rank + category prior + anchor hits; copy-forward duplicates fold into
                  the latest; at most 2 sentences per source record so one long note cannot crowd out a later stop or a
                  structured barrier; at most MAX_PASSAGES (8, bounded_evidence.MAX_PASSAGES), returned in date order.
Budget left after the anchored sentences carries each remaining passage's best context sentence (heal, never empty).
Rows keep the retriever format ({'id','text','source':{patient_id, record_id, field, start, end, event_date, ...}}), so
evidence_proposers.targets / atomic_questions.passage_evidence read them unchanged; 'start'/'end' are the sentence's.
"""
import re
import threading
import time
from collections import OrderedDict

from .bounded_evidence import MAX_PASSAGES

VERSION = 'evidence-packets/1'
CANDIDATES = 20
PER_RECORD = 2
SELECTION_TOP = 4
_SPLIT = re.compile(r'(?<=[.!?;])\s+(?=\S)|\n+')
STOP = set('a an and are as at be by did do does for from had has have in is it its of on or patient patients that the their '
           'there this to was were what which who whom why with chart note notes document documents evidence submitted establish '
           'only for any documented currently status not taking'.split())
CLASS_WORDS = {'sglt2_inhibitor': r'sglt-?2|gliflozin', 'mra': r'\bmra\b|mineralocorticoid', 'beta_blocker': r'beta[- ]?blocker',
               'ace_inhibitor': r'ace[- ]?inhibitor|\bacei\b', 'arb': r'\barbs?\b|angiotensin receptor', 'arni': r'\barni\b|entresto',
               'statin': r'\bstatins?\b', 'rasi': r'\bace\b|\barb\b|\barni\b|lisinopril|losartan|valsartan|sacubitril|pril\b|sartan\b',
               'glp1_agonist': r'glp-?1', 'insulin': r'insulin', 'anticoagulant': r'anticoag|doac'}
_SEL, _SEL_LOCK, SEL_SIZE = OrderedDict(), threading.Lock(), 512


def subject_pattern(subject):
    """Regex for a medication_state subject or drug class: ingredient names plus class words."""
    if not subject: return None
    from . import drug_classes
    parts = []
    try:
        from .medication_state import PILLARS
        classes = PILLARS.get(subject) or [subject]
    except Exception:  # noqa: BLE001 - no pillar table: the subject is the class
        classes = [subject]
    for c in classes:
        if c in drug_classes.CLASSES: parts.append(drug_classes.CLASSES[c])
        if c == 'hf_evidence_based_beta_blocker': parts.append(drug_classes.CLASSES['beta_blocker'])
    if subject in CLASS_WORDS: parts.append(CLASS_WORDS[subject])
    for c in classes:
        if c in CLASS_WORDS: parts.append(CLASS_WORDS[c])
    return re.compile('|'.join(f'(?:{p})' for p in parts), re.I) if parts else None


def content_words(query):
    return [w for w in re.findall(r'[a-z][a-z0-9-]{3,}', query.lower()) if w not in STOP]


def sentences(text):
    """[(start, end, sentence)] with exact offsets into text."""
    out, pos = [], 0
    for piece in _SPLIT.split(text):
        if not piece.strip():
            pos += len(piece); continue
        start = text.find(piece, pos)
        if start < 0: continue
        lead = len(piece) - len(piece.lstrip())
        s = piece.strip()
        out.append((start + lead, start + lead + len(s), s))
        pos = start + len(piece)
    return out


def category_order(stack, patient, question):
    """Learned selection: the patient's received source categories ordered by source_selection.rank (schema MiniLM +
    BGE rerank). -> (ordered category ids, receipt). Memoized per (question, category set); never raises."""
    started = time.perf_counter()
    try:
        from . import record_context as C, source_selection as SS
        records, _ = C.received(stack.path, patient)
        profile = SS.descriptors(records)
        key = (question, tuple(sorted(r['id'] for r in profile)))
        with _SEL_LOCK:
            hit = _SEL.get(key)
        if hit is not None:
            return hit, {'method': SS.VERSION, 'cache_hit': True, 'categories': len(profile)}
        retriever = stack.retriever
        if getattr(retriever, 'encoder', None) is None or getattr(retriever, 'reranker', None) is None:
            return [], {'method': SS.VERSION, 'status': 'no_encoder_or_reranker'}
        routes = SS.rank(question, profile, retriever.encoder, retriever.reranker)
        order = list(dict.fromkeys(routes['exact_categories'] + [r['id'] for r in routes['candidates']]))
        with _SEL_LOCK:
            _SEL[key] = order
            while len(_SEL) > SEL_SIZE: _SEL.popitem(last=False)
        return order, {'method': SS.VERSION, 'cache_hit': False, 'categories': len(profile),
                       'above_g2_threshold': routes['selected_categories'], 'seconds': round(time.perf_counter() - started, 3)}
    except Exception as exc:  # noqa: BLE001 - selection is a prior; the retriever order still stands
        return [], {'status': 'failed', 'reason': f'{type(exc).__name__}: {exc}'[:200]}


def _day(r):
    """Sort key preferring later ISO dates (undated last)."""
    d = str(r['source'].get('event_date') or '')[:10]
    return tuple(-int(x) for x in d.split('-')) if re.fullmatch(r'\d{4}-\d{2}-\d{2}', d) else (1,)


def select(rows, query, *, subject=None, categories=(), limit=MAX_PASSAGES):
    """Pure selection over retriever rows -> (packet rows, stats). Deterministic; no I/O."""
    anchor = subject_pattern(subject)
    words = content_words(query)
    prior = {c: (SELECTION_TOP - i) / SELECTION_TOP for i, c in enumerate(list(categories)[:SELECTION_TOP])}
    scored, fallback = [], []
    for rank, row in enumerate(rows):
        src = row.get('source') or {}
        text = row.get('text') or ''
        base = int(src.get('start') or 0)
        sents = sentences(text)
        best = None
        for i, (a, b, s) in enumerate(sents):
            low = s.lower()
            hits = (2 if anchor and anchor.search(s) else 0) + sum(1 for w in words if w in low)
            score = 1.0 / (1 + rank) + prior.get(src.get('source_table'), 0.0) * 0.5 + 0.3 * hits
            item = {'id': f"{row.get('id')}:{a}", 'text': s, 'previous': sents[i - 1][2] if i else '', 'score': round(score, 4),
                    'anchored': hits > 0, 'source': {**src, 'start': base + a, 'end': base + b}, 'passage_id': row.get('id'),
                    'passage_rank': rank, 'category': src.get('source_table')}
            if hits: scored.append(item)
            if best is None or score > best['score']: best = item
        if best is not None: fallback.append(best)
    scored.sort(key=lambda r: (-r['score'], _day(r)))
    context = sorted((f for f in fallback if not f['anchored']), key=lambda r: (-r['score'], _day(r)))
    pool = scored + context  # anchored sentences first; leftover budget carries each passage's best context sentence
    seen_text, per_record, chosen = {}, {}, []
    for r in pool:
        norm = ' '.join(re.findall(r'[a-z0-9]+', r['text'].lower()))
        rid = r['source'].get('record_id')
        if norm in seen_text:  # copy-forward repeat: keep the later-dated copy
            j = seen_text[norm]
            if (r['source'].get('event_date') or '') > (chosen[j]['source'].get('event_date') or ''): chosen[j] = r
            continue
        if per_record.get(rid, 0) >= PER_RECORD: continue
        per_record[rid] = per_record.get(rid, 0) + 1
        seen_text[norm] = len(chosen)
        chosen.append(r)
        if len(chosen) >= limit: break
    chosen.sort(key=lambda r: ((r['source'].get('event_date') or '')[:10], str(r['source'].get('record_id')), r['source']['start']))
    return chosen, {'candidate_passages': len(rows), 'anchored_sentences': len(scored), 'selected': len(chosen),
                    'omitted': max(0, len(pool) - len(chosen)), 'context_sentences': sum(1 for r in chosen if not r['anchored']),
                    'fallback_unanchored': not scored and bool(fallback)}


def packet(stack, query, patient, *, subject=None, question=None, limit=MAX_PASSAGES, window=None):
    """Bounded packet for one patient -> {'schema','evidence':[rows],'selection','stats','retrieval'} (never raises for
    selection; a retrieval failure raises like stack.retriever.search)."""
    started = time.perf_counter()
    kw = {'analysis_window': window} if window and window.get('basis') == 'source_event' else {}
    found = stack.retriever.search(query[:1500], patient=patient, limit=CANDIDATES, **kw)
    rows = [r for r in found.get('evidence') or [] if r.get('text')]
    order, selection = category_order(stack, patient, question or query) if rows else ([], {'status': 'no_candidates'})
    chosen, stats = select(rows, query, subject=subject, categories=order, limit=limit)
    return {'schema': VERSION, 'patient_id': patient, 'query': query[:1500], 'subject': subject, 'evidence': chosen,
            'selection': {**selection, 'category_order': order[:SELECTION_TOP]}, 'stats': stats,
            'retrieval': {'method': found.get('method'), 'cache_hit': found.get('cache_hit'), 'candidate_passages': len(rows)},
            'bounded': {'max_passages': limit, 'per_record': PER_RECORD, 'contract': 'bounded-subject-evidence/1'},
            'complete_evidence_guarantee': False, 'new_paid_api_calls': 0, 'seconds': round(time.perf_counter() - started, 3)}
