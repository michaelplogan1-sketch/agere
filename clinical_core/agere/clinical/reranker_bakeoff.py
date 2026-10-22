"""Reranker comparison (BGE reranker v2-m3 vs Qwen3-Reranker-0.6B) and the per-query-type choice the retriever reads.

MODEL_SELECTION_REVIEW (2026-10-08): "Compare BGE reranker v2-m3 with Qwen3-Reranker-0.6B when retrieval becomes the next
measured bottleneck ... retain excluded/contradicting evidence." This module makes that comparison on the active stack and
adopts the winner per query type:

  QwenReranker          Qwen3-Reranker-0.6B (causal LM, Apache-2.0, 1.2 GB): relevance = logit(yes) - logit(no) after the
                        model card's judge prompt; same rank()/identity/cache contract as local_encoders.LocalReranker
  query_type(question)  medication | lab | condition | social | exception | general (deterministic vocabulary)
  evaluation_set()      patient-scoped questions per type whose relevant records are known from STRUCTURED fields
                        (labs.name / measurement concept, drug names / classes, ICD-10 categories) or, for narrative
                        types, from term families stated before any retrieval runs; neither reranker defines relevance
  run(stack_dir)        every reranker reorders the SAME fused candidate set per question (RRF order is the no-rerank
                        baseline); nDCG@8, MRR and P@8 per type; the concept-graph retriever (med_graph_rag) is compared
                        with hybrid retrieval on record recall@8 against the patient's full relevant-record set
  choice(question)      the adopted reranker and whether the graph fallback joins fusion, per query type
                        (config/agere/reranker_choice.json); bge when no measurement exists
Ties keep the incumbent (BGE). Local models only; zero paid calls; synthetic data only.
"""
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import sqlite3
import time

import numpy as np

from ...contracts import digest

VERSION = 'reranker-bakeoff/1'
ROOT = Path(__file__).resolve().parents[3]
CHOICE = ROOT / 'config' / 'agere' / 'reranker_choice.json'
QWEN_PATH = ROOT / 'models' / 'qwen3-reranker-0.6b'
BGE_PATH = ROOT / 'models' / 'bge-reranker-v2-m3'
TYPES = ('medication', 'lab', 'condition', 'social', 'exception', 'general')
QWEN_INSTRUCTION = 'Given a question about one patient, judge whether this passage from the patient record helps answer it'

# ---- query typing ----------------------------------------------------------------------------------------------------
_EXCEPTION = re.compile(r'\b(?:stop(?:ped)?|discontinu\w*|intoleran\w*|allerg\w*|adverse|side effects?|declin\w*|refus\w*|'
                        r'contraindicat\w*|why not|not on|not taking|held|tolerate)\b', re.I)
_SOCIAL = re.compile(r'\b(?:transport\w*|ride|afford\w*|cost|financial|bills?|food|housing|homeless|caregiver|lonel\w*|'
                     r'social|barrier\w*|insurance problem|language|interpreter)\b', re.I)
_LAB = re.compile(r'\b(?:a1c|hba1c|egfr|gfr|ldl|cholesterol|creatinine|potassium|bnp|lab\w*|result\w*|glucose|sodium|'
                  r'hemoglobin|uacr|albumin|blood pressure|bp)\b', re.I)
_MED = re.compile(r'\b(?:medication\w*|drugs?|prescri\w*|fill\w*|refill\w*|dose|taking|statins?|metformin|insulin|'
                  r'lisinopril|losartan|amlodipine|atorvastatin|rosuvastatin|metoprolol|carvedilol|spironolactone|furosemide|'
                  r'empagliflozin|dapagliflozin|sglt2|glp-?1|semaglutide|beta[- ]?blockers?|ace inhibitors?|arbs?|diuretics?|'
                  r'apixaban|warfarin|aspirin)\b', re.I)
_COND = re.compile(r'\b(?:diagnos\w*|condition\w*|diabetes|diabetic|heart failure|chf|hypertension|htn|ckd|kidney disease|'
                   r'copd|asthma|depression|anemia|obesity|coronary|afib|atrial fibrillation|hyperlipidemia|problem list)\b', re.I)


def query_type(question):
    q = question or ''
    if _EXCEPTION.search(q) and (_MED.search(q) or _COND.search(q) or _LAB.search(q)): return 'exception'
    if _SOCIAL.search(q): return 'social'
    if _LAB.search(q): return 'lab'
    if _MED.search(q): return 'medication'
    if _COND.search(q): return 'condition'
    return 'general'


# ---- the adopted choice ------------------------------------------------------------------------------------------------
_CACHE = {}


def load_choice(path=None):
    p = Path(path or os.environ.get('AGERE_RERANKER_CHOICE') or CHOICE)
    key = (str(p), p.stat().st_mtime if p.exists() else None)
    if key not in _CACHE:
        _CACHE.clear()
        try:
            _CACHE[key] = json.loads(p.read_text()) if p.exists() else {}
        except (OSError, ValueError):
            _CACHE[key] = {}
    return _CACHE[key]


def choice(question, path=None):
    """-> {'type', 'reranker': 'bge'|'qwen', 'graph_fallback': bool, 'basis'}"""
    t = query_type(question)
    c = (load_choice(path).get('by_type') or {}).get(t) or {}
    return {'type': t, 'reranker': c.get('reranker', 'bge') if c.get('reranker') in ('bge', 'qwen') else 'bge',
            'graph_fallback': bool(c.get('graph_fallback', False)),
            'basis': c.get('basis', 'no measurement for this query type: incumbent BGE reranker')}


# ---- Qwen3 reranker ---------------------------------------------------------------------------------------------------
class QwenReranker:
    PREFIX = ('<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct '
              'provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n')
    SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'

    def __init__(self, path=QWEN_PATH, *, cache, instruction=QWEN_INSTRUCTION, threads=4):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from ...local_encoders import verified_manifest
        self.manifest = verified_manifest(path)
        self.instruction = instruction
        self.identity = digest({'manifest': self.manifest, 'runtime': 'qwen3-yes-no-logit/1', 'instruction': instruction})
        self.torch = torch
        torch.set_num_threads(threads)
        self.tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False, padding_side='left')
        self.model = AutoModelForCausalLM.from_pretrained(path, local_files_only=True, trust_remote_code=False,
                                                          dtype=torch.float32).eval()
        self.yes = self.tokenizer.convert_tokens_to_ids('yes')
        self.no = self.tokenizer.convert_tokens_to_ids('no')
        self.limit = 4096
        self.db = sqlite3.connect(cache, check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS reranker_scores (key TEXT PRIMARY KEY, score REAL NOT NULL)')
        self.db.commit()
        self.stats = {'cache_hits': 0, 'pairs_scored': 0, 'input_tokens': 0, 'inference_seconds': 0.0}

    def close(self):
        self.db.close()

    def _text(self, query, doc):
        return f'{self.PREFIX}<Instruct>: {self.instruction}\n<Query>: {query}\n<Document>: {doc}{self.SUFFIX}'

    def score(self, query, texts):
        out = [None] * len(texts); todo = []
        for i, t in enumerate(texts):
            key = digest([self.identity, query, t])
            row = self.db.execute('SELECT score FROM reranker_scores WHERE key=?', (key,)).fetchone()
            if row: out[i] = row[0]; self.stats['cache_hits'] += 1
            else: todo.append((i, key, t))
        for start in range(0, len(todo), 8):
            batch = todo[start:start + 8]
            enc = self.tokenizer([self._text(query, t) for _, _, t in batch], return_tensors='pt', padding=True, truncation=False)
            if enc['input_ids'].shape[1] > self.limit:
                raise ValueError('Reranker input exceeds explicit compute/context bound; no truncation performed')
            t0 = time.perf_counter()
            with self.torch.inference_mode():
                logits = self.model(**enc).logits[:, -1, :]
            s = (logits[:, self.yes] - logits[:, self.no]).float().tolist()
            self.stats['inference_seconds'] += time.perf_counter() - t0
            self.stats['pairs_scored'] += len(batch); self.stats['input_tokens'] += int(enc['attention_mask'].sum())
            with self.db:
                for (i, key, _), v in zip(batch, s):
                    if not math.isfinite(v): raise ValueError('Nonfinite reranking score')
                    out[i] = v
                    self.db.execute('INSERT OR IGNORE INTO reranker_scores VALUES (?,?)', (key, v))
        return out

    def rank(self, query, evidence, *, top_k=8):
        scores = self.score(query, [e['text'] for e in evidence])
        ranked = [{**e, 'reranker_logit': s} for e, s in zip(evidence, scores)]
        ranked.sort(key=lambda r: (not r['protected'], -r['reranker_logit'], r['note_id'], r['start']))
        return ranked[:top_k]


# ---- evaluation set ---------------------------------------------------------------------------------------------------
# concept -> (question template, structured matcher over (stream, payload), text terms for narrative relevance)
LAB_CONCEPTS = {'hba1c': (r'\ba1c\b|hba1c|hemoglobin a1c', ['a1c', 'hba1c']), 'egfr': (r'\begfr\b|glomerular', ['egfr', 'kidney function']),
                'potassium': (r'^potassium|\bpotassium\b|^k$', ['potassium']), 'ldl': (r'\bldl\b', ['ldl', 'cholesterol'])}
MED_CONCEPTS = {'metformin': r'metformin', 'atorvastatin': r'atorvastatin', 'lisinopril': r'lisinopril', 'amlodipine': r'amlodipine',
                'furosemide': r'furosemide', 'metoprolol': r'metoprolol'}
COND_CONCEPTS = {'heart failure': (r'^I50', ['heart failure', 'chf', 'hfref', 'hfpef']), 'diabetes': (r'^E1[0-3]', ['diabetes', 'diabetic']),
                 'chronic kidney disease': (r'^N18', ['ckd', 'kidney disease', 'renal']), 'hypertension': (r'^I1[0-6]', ['hypertension', 'htn', 'blood pressure'])}
SOCIAL_CONCEPTS = {'transportation': ['transport', 'ride', 'getting to appointments', 'bus'], 'medication cost': ['afford', 'cost', 'copay', 'expensive'],
                   'food insecurity': ['food', 'meals', 'groceries', 'hungry']}
TEMPLATES = {'lab': 'What were the patient\'s {c} results?', 'medication': 'Is the patient taking {c}?',
             'condition': 'Does the patient have {c}?', 'social': 'Does the patient report problems with {c}?',
             'exception': 'Did the patient stop or not tolerate {c}?'}
_MED_STREAMS = ('pharmacy_dispenses', 'medication_orders', 'payer_pharmacy_claims', 'pharmacy_history', 'medication_reconciliation',
                'pharmacy_claim_transactions', 'medication_lists')
_NARRATIVE = ('notes', 'telephone_messages', 'medication_followup_reports', 'encounters', 'imported_documents')


def _text_of(payload):
    from ..narrative_sources import text_fields
    try:
        return ' '.join(p['text'] for p in text_fields(payload)).lower()
    except (KeyError, TypeError):
        return ''


def relevant(kind, concept, stream, payload):
    """Structured relevance label of one record for one evaluation concept (stated before retrieval; no reranker involved)."""
    text = None
    def txt():
        nonlocal text
        if text is None: text = _text_of(payload)
        return text
    if kind == 'lab':
        rx, terms = LAB_CONCEPTS[concept]
        if stream in ('labs', 'laboratory_result_notifications') and re.search(rx, str(payload.get('name') or payload.get('test') or ''), re.I): return True
        return stream in _NARRATIVE and any(re.search(r'\b' + re.escape(t) + r'\b', txt()) for t in terms)
    if kind in ('medication', 'exception'):
        rx = MED_CONCEPTS[concept]
        if stream in _MED_STREAMS and re.search(rx, ' '.join(str(payload.get(k) or '') for k in ('medication_name', 'drug_name', 'drug')), re.I):
            return kind == 'medication' or str(payload.get('status') or '').lower() in ('stopped', 'discontinued', 'held', 'cancelled')
        if stream in _NARRATIVE and re.search(rx, txt()):
            if kind == 'medication': return True
            from .protected_channels import polarity
            return polarity(txt(), concept) == 'negated'
        return False
    if kind == 'condition':
        rx, terms = COND_CONCEPTS[concept]
        if stream in ('conditions', 'condition_assessments', 'problem_lists') and re.search(rx, str(payload.get('icd10') or ''), re.I): return True
        return stream in _NARRATIVE and any(re.search(r'\b' + re.escape(t) + r'\b', txt()) for t in terms)
    if kind == 'social':
        return stream in _NARRATIVE + ('sdoh_vendor_attributes', 'social_needs', 'sdoh_screenings') and \
            any(t in txt() or t in json.dumps(payload).lower() for t in SOCIAL_CONCEPTS[concept])
    return False


def evaluation_set(stack_dir, *, per_concept=3, seed='reranker-bakeoff-1'):
    """[{'type', 'concept', 'patient', 'question', 'relevant_records': [...]}] with patients sampled among those who hold at
    least one relevant record for the concept (labels from structured fields / stated term families)."""
    db = sqlite3.connect((Path(stack_dir) / 'stack.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
    rng = random.Random(seed)
    patients = sorted(r[0] for r in db.execute('SELECT id FROM patients'))
    rng.shuffle(patients)
    concepts = ([('lab', c) for c in LAB_CONCEPTS] + [('medication', c) for c in MED_CONCEPTS] + [('condition', c) for c in COND_CONCEPTS]
                + [('social', c) for c in SOCIAL_CONCEPTS] + [('exception', c) for c in ('metformin', 'lisinopril', 'atorvastatin', 'metoprolol')])
    out, need = [], {c: per_concept for c in concepts}
    try:
        for pid in patients:
            if not any(need.values()): break
            recs = [(rid, stream, json.loads(payload)) for rid, stream, payload in db.execute('SELECT id, stream, payload FROM records WHERE patient=?', (pid,))]
            for (kind, concept), left in need.items():
                if not left: continue
                rel = sorted({rid for rid, stream, payload in recs if relevant(kind, concept, stream, payload)})
                if not rel: continue
                out.append({'type': kind, 'concept': concept, 'patient': pid, 'question': TEMPLATES[kind].format(c=concept),
                            'relevant_records': rel})
                need[(kind, concept)] -= 1
    finally:
        db.close()
    return out


# ---- metrics ----------------------------------------------------------------------------------------------------------
def _ndcg(rels, k=8):
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(rels[:k]))
    ideal = sorted(rels, reverse=True)
    idcg = sum(r / math.log2(i + 2) for i, r in enumerate(ideal[:k]))
    return dcg / idcg if idcg else None


def _mrr(rels):
    return next((1 / (i + 1) for i, r in enumerate(rels) if r), 0.0)


def _record_recall(rows, relevant_set, k=8):
    got = []
    for r in rows:
        rid = r['source']['record_id']
        if rid not in got: got.append(rid)
        if len(got) >= k: break
    if not relevant_set: return None
    return len(set(got) & relevant_set) / min(k, len(relevant_set))


def run(stack_dir, *, out=None, per_concept=3, models=('bge', 'qwen'), save=True, progress=None):
    """Measured comparison on the active stack; writes config/agere/reranker_choice.json (adopted winner per type)."""
    from .warehouse_retrieval import Retriever
    from . import med_graph_rag
    stack_dir = Path(stack_dir)
    t0 = time.time()
    items = evaluation_set(stack_dir, per_concept=per_concept)
    retriever = Retriever(stack_dir, rerank=False)
    cache = stack_dir / 'model-cache.sqlite3'
    rerankers = {}
    try:
        from ...local_encoders import LocalReranker
        if 'bge' in models: rerankers['bge'] = LocalReranker(BGE_PATH, cache=cache)
        if 'qwen' in models: rerankers['qwen'] = QwenReranker(QWEN_PATH, cache=cache)
        graph = med_graph_rag.GraphRetriever(stack_dir) if (stack_dir / med_graph_rag.FILE).exists() else None
        rows = []
        for n, it in enumerate(items, 1):
            cand = retriever.candidates(it['question'], patient=it['patient'])
            rel = set(it['relevant_records'])
            label = [1 if c['source']['record_id'] in rel else 0 for c in cand]
            row = {'type': it['type'], 'concept': it['concept'], 'patient': it['patient'], 'question': it['question'],
                   'candidates': len(cand), 'relevant_in_candidates': sum(label), 'relevant_records': len(rel), 'metrics': {}}
            row['metrics']['rrf'] = {'ndcg8': _ndcg(label), 'mrr': _mrr(label), 'p8': sum(label[:8]) / 8}
            ranked_by = {}
            for name, rr in rerankers.items():
                t1 = time.perf_counter()
                ranked = rr.rank(it['question'], [{**c, 'protected': False} for c in cand], top_k=len(cand)) if cand else []
                lab = [1 if c['source']['record_id'] in rel else 0 for c in ranked]
                ranked_by[name] = ranked
                row['metrics'][name] = {'ndcg8': _ndcg(lab), 'mrr': _mrr(lab), 'p8': sum(lab[:8]) / 8, 'seconds': round(time.perf_counter() - t1, 3)}
            best = ranked_by.get('bge') or cand
            row['recall8'] = {'hybrid': _record_recall(best, rel)}
            if graph is not None:
                g = graph.search(it['question'], patient=it['patient'], limit=24)['candidates']
                row['recall8']['graph'] = _record_recall(g, rel)
                fused = Counter()
                for ranking in (best, g):
                    for rank, c in enumerate(dict.fromkeys(x['id'] for x in ranking), 1): fused[c] += 1 / (60 + rank)
                byid = {c['id']: c for c in [*best, *g]}
                row['recall8']['hybrid_plus_graph'] = _record_recall([byid[i] for i, _ in fused.most_common()], rel)
            rows.append(row)
            if progress: progress(n, len(items), row)
    finally:
        retriever.close()
        for r in rerankers.values(): r.close()
    report = summarize(rows, list(rerankers))
    report.update({'schema': VERSION, 'synthetic': True, 'stack': str(stack_dir), 'source_digest': retriever.meta['source_digest'],
                   'questions': len(rows), 'seconds': round(time.time() - t0, 1), 'new_paid_api_calls': 0,
                   'models': {'bge': str(BGE_PATH.name), 'qwen': str(QWEN_PATH.name)},
                   'labels': 'Structured-field relevance (labs.name, drug names/status, ICD-10 categories) and stated term families for '
                             'narrative records, fixed before retrieval; every reranker reorders the same fused candidates.',
                   'rows': rows})
    if save:
        Path(out or CHOICE).write_text(json.dumps({k: v for k, v in report.items() if k != 'rows'}, indent=1))
        (Path(out or CHOICE).with_name('reranker_bakeoff_rows.json')).write_text(json.dumps(rows, indent=1))
    return report


def summarize(rows, models):
    by_type = {}
    for t in TYPES:
        rs = [r for r in rows if r['type'] == t]
        if not rs: continue
        agg = {}
        for m in ['rrf', *models]:
            vals = [r['metrics'][m]['ndcg8'] for r in rs if m in r['metrics'] and r['metrics'][m]['ndcg8'] is not None]
            agg[m] = {'ndcg8': round(sum(vals) / len(vals), 4) if vals else None, 'n': len(vals),
                      'mrr': round(sum(r['metrics'][m]['mrr'] for r in rs if m in r['metrics']) / len(rs), 4),
                      'p8': round(sum(r['metrics'][m]['p8'] for r in rs if m in r['metrics']) / len(rs), 4)}
            secs = [r['metrics'][m].get('seconds') for r in rs if m in r['metrics'] and r['metrics'][m].get('seconds') is not None]
            if secs: agg[m]['seconds_per_question'] = round(sum(secs) / len(secs), 3)
        rec = {}
        for k in ('hybrid', 'graph', 'hybrid_plus_graph'):
            vals = [r['recall8'][k] for r in rs if r.get('recall8', {}).get(k) is not None]
            if vals: rec[k] = round(sum(vals) / len(vals), 4)
        bge, qwen = (agg.get('bge') or {}).get('ndcg8'), (agg.get('qwen') or {}).get('ndcg8')
        winner = 'qwen' if (qwen is not None and bge is not None and qwen > bge + 0.005) else 'bge'
        graph_fallback = bool(rec.get('hybrid_plus_graph') is not None and rec.get('hybrid') is not None
                              and rec['hybrid_plus_graph'] > rec['hybrid'] + 0.005)
        by_type[t] = {'reranker': winner, 'graph_fallback': graph_fallback, 'ndcg8': agg, 'record_recall8': rec, 'questions': len(rs),
                      'basis': f"nDCG@8 bge {bge} vs qwen {qwen} on {len(rs)} questions; record recall@8 hybrid {rec.get('hybrid')} vs "
                               f"hybrid+graph {rec.get('hybrid_plus_graph')}"}
    return {'by_type': by_type, 'adopted': {t: v['reranker'] for t, v in by_type.items()},
            'graph_fallback_types': sorted(t for t, v in by_type.items() if v['graph_fallback'])}
