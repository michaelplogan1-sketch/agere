"""VBC ecosystem knowledge base: cited explanations of value-based care programs, mechanics, players and evidence.

Agere's engines answer data questions. This module answers explanation and strategy questions ("how does ACO REACH's
benchmark work?", "how are patients attributed to an MSSP ACO?", "Aledade vs agilon?", "what is Porter's value
agenda?") from a curated corpus of public-source summaries in config/agere/knowledge/*.jsonl.

Each entry: id, topic, title, questions it answers, a summary in our own words (<= ~250 words), structured facts,
citations (url, title, publisher, date, retrieved, source_type, optional short quote), last_verified, confidence and
any contradictions between sources.

Retrieval: BM25 over title / questions / entities / summary / facts, fused with cosine similarity from the stack's
local sentence encoder (stack.retriever.encoder, shallow-copied onto a private vector cache so the stack's own cache is
never written) when one is available; BM25 alone otherwise. The answer is composed extractively from the retrieved
entries with numbered citations. An optional synthesis step (hosted model) runs only when AGERE_KNOWLEDGE_SYNTHESIS=on
AND the caller allows live calls; it is off by default. The legacy clinical_core/agere/knowledge.py (research
catalog) is unrelated and untouched.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import threading
from collections import Counter
from pathlib import Path

VERSION = 'vbc-knowledge/1'
ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_DIR = ROOT / 'config' / 'agere' / 'knowledge'
CACHE = ROOT / 'artifacts' / 'vbc-knowledge' / 'encoder-cache.sqlite3'
TOPICS = ('attribution', 'program_mechanics', 'risk_adjustment', 'quality', 'cms_strategy', 'cms_models', 'market_players',
          'thought_leaders', 'evidence', 'industry_analysis')
SOURCE_TYPES = ('government', 'peer_reviewed', 'company', 'industry_association', 'consulting', 'journal_commentary', 'news',
                'book', 'academic')
CONFIDENCE = ('high', 'medium', 'low')
SUMMARY_WORDS = (25, 260)
QUOTE_WORDS = 25
DEFAULT_TOP = 5
MIN_SCORE = 0.22          # fused score below which no entry is considered to answer the question
MIN_COVERAGE = 0.5        # at least half the question's informative words must appear in some retrieved entry
RELATED_RATIO = 0.55     # further entries shown when their score is at least this share of the best


# ---------------------------------------------------------------- corpus schema -------------------------------------

def _words(text):
    return len(re.findall(r'\S+', str(text)))


def entry_problems(e):
    """Schema problems for one knowledge entry (empty list = valid)."""
    p = [f'missing {k}' for k in ('id', 'topic', 'questions', 'summary', 'facts', 'citations', 'last_verified', 'confidence') if k not in e]
    if p: return p
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,80}', str(e['id'])): p.append('id format')
    if e['topic'] not in TOPICS: p.append(f"topic {e['topic']}")
    qs = e['questions']
    if not (isinstance(qs, list) and 1 <= len(qs) <= 8 and all(isinstance(q, str) and q.strip() for q in qs)): p.append('questions')
    n = _words(e['summary'])
    if not SUMMARY_WORDS[0] <= n <= SUMMARY_WORDS[1]: p.append(f'summary words {n}')
    if not isinstance(e['facts'], dict): p.append('facts must be an object')
    if not (isinstance(e['citations'], list) and e['citations']): p.append('no citations')
    for c in e['citations'] if isinstance(e['citations'], list) else []:
        if not isinstance(c, dict): p.append('citation shape'); continue
        for k in ('url', 'title', 'publisher', 'retrieved', 'source_type'):
            if not c.get(k): p.append(f'citation missing {k}')
        if not str(c.get('url', '')).startswith('http'): p.append('citation url')
        if c.get('source_type') not in SOURCE_TYPES: p.append(f"source_type {c.get('source_type')}")
        if c.get('quote') and _words(c['quote']) > QUOTE_WORDS: p.append('quote over 25 words')
    if e['confidence'] not in CONFIDENCE: p.append('confidence')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(e['last_verified'])): p.append('last_verified')
    for c in e.get('contradictions') or []:
        if not (isinstance(c, dict) and c.get('claim') and c.get('note')): p.append('contradiction shape')
    return p


def check_files(paths):
    seen, report = {}, []
    for f in paths:
        for i, line in enumerate(Path(f).read_text().splitlines(), 1):
            if not line.strip(): continue
            try:
                e = json.loads(line)
            except ValueError as exc:
                report.append(f'{f}:{i} invalid JSON: {exc}'); continue
            p = entry_problems(e)
            if e.get('id') in seen: p.append(f"duplicate id (also {seen[e['id']]})")
            seen[e.get('id')] = f'{f}:{i}'
            if p: report.append(f"{f}:{i} {e.get('id')}: {p}")
    return len(seen), report


def corpus_files(directory=None):
    return sorted(Path(directory or KNOWLEDGE_DIR).glob('*.jsonl'))


def load(directory=None):
    """All valid entries (invalid ones raise: a broken corpus must fail loudly, not silently shrink)."""
    entries, seen = [], set()
    for f in corpus_files(directory):
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if not line.strip(): continue
            e = json.loads(line)
            p = entry_problems(e)
            if p: raise ValueError(f'{f.name}:{i} {e.get("id")}: {p}')
            if e['id'] in seen: raise ValueError(f'{f.name}:{i} duplicate id {e["id"]}')
            seen.add(e['id']); e['_file'] = f.name
            entries.append(e)
    return entries


def corpus_digest(directory=None):
    h = hashlib.sha256()
    for f in corpus_files(directory):
        h.update(f.name.encode()); h.update(f.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------- lexical index -------------------------------------

# Multi-word names and common abbreviations collapse to one token on both sides (documents and questions).
ALIASES = [
    (r'\bmedicare shared savings program\b|\bshared savings program\b', ' mssp '),
    (r'\baco reach\b|\breach aco\b|\brealizing equity,? access,? and community health\b', ' reach '),
    (r'\bglobal and professional direct contracting\b|\bgpdc\b', ' directcontracting '),
    (r'\bdirect contracting\b', ' directcontracting '),
    (r'\bnext gen(?:eration)? aco\b|\bngaco\b', ' nextgenaco '),
    (r'\bmedicare advantage\b|\bpart c\b', ' medicareadvantage '),
    (r'\baccountable care organi[sz]ations?\b|\bacos\b', ' aco '),
    (r'\bcms innovation center\b|\bcenter for medicare and medicaid innovation\b|\binnovation center\b|\bcmmi\b', ' cmmi '),
    (r'\bhierarchical condition categor(?:y|ies)\b|\bhccs\b', ' hcc '),
    (r'\brisk adjustment factor\b|\braf scores?\b', ' raf '),
    (r'\brisk adjustment data validation\b', ' radv '),
    (r'\bstar ratings?\b|\bstars program\b|\b5[- ]star\b', ' starratings '),
    (r'\balternative payment models?\b|\bapms\b', ' apm '),
    (r'\bhealth care payment learning (?:and|&) action network\b|\bhcp[- ]lan\b|\blan\b', ' hcplan '),
    (r'\bper member per month\b', ' pmpm '),
    (r'\bmedical loss ratio\b', ' mlr '),
    (r'\bprimary care physicians?\b|\bprimary care providers?\b|\bpcps\b', ' pcp '),
    (r'\bvalue[- ]based care\b|\bvalue[- ]based payment\b', ' vbc '),
    (r'\bminimum savings rate\b', ' msr '),
    (r'\bminimum loss rate\b', ' mlr2 '),
    (r'\bannual wellness visits?\b', ' awv '),
    (r'\bintegrated practice units?\b', ' ipu '),
    (r'\bhot[- ]?spotting\b|\bhot[- ]?spotters?\b|\bsuper[- ]?utili[sz]ers?\b', ' hotspotting '),
    (r'\bcamden coalition\b', ' camden '),
    (r'\bone medical\b', ' onemedical '),
    (r'\boak street(?: health)?\b', ' oakstreet '),
    (r'\bpearl health\b', ' pearl '),
    (r'\bvillage ?md\b', ' villagemd '),
    (r'\bcenterwell\b', ' centerwell '),
    (r'\bcity ?block(?: health)?\b', ' cityblock '),
    (r'\bsignify(?: health)?\b', ' signify '),
    (r'\bchen ?med\b', ' chenmed '),
    (r'\bagilon(?: health)?\b', ' agilon '),
    (r'\bprivia(?: health)?\b', ' privia '),
    (r'\bmichael porter\b|\bporter\'s\b', ' porter '),
    (r'\bdavid nash\b|\bnash\'s\b', ' nash '),
    (r'\bvoluntary alignment\b', ' voluntaryalignment '),
    (r'\bhigh needs\b', ' highneeds '),
    (r'\bmaking care primary\b', ' makingcareprimary '),
    (r'\bprimary care first\b', ' primarycarefirst '),
    (r'\bcomprehensive primary care plus\b|\bcpc\+', ' cpcplus '),
    (r'\bkidney care choices\b', ' kidneycarechoices '),
    (r'\bmaryland total cost of care\b', ' marylandtcoc '),
    (r'\btotal cost of care\b', ' tcoc '),
]
_STOP = set('a an and are as at be by can could did do does for from has have how i in into is it its me of on or our '
            'should so than that the their them then there these they this to us was we were what when where which who why '
            'will with would you your tell explain describe mean means work works working difference between versus vs compare '
            'please about give overview'.split())


def _canon(text):
    low = ' ' + str(text).lower().replace('’', "'") + ' '
    for pattern, rep in ALIASES:
        low = re.sub(pattern, rep, low)
    return low


def _stem(t):
    if len(t) > 4 and t.endswith('ies'): return t[:-3] + 'y'
    if len(t) > 3 and t.endswith('s') and not t.endswith('ss'): return t[:-1]
    return t


def tokens(text):
    return [_stem(t) for t in re.findall(r"[a-z0-9][a-z0-9+]*", _canon(text)) if t not in _STOP and (len(t) > 1 or t.isdigit())]


def _flat(value):
    if isinstance(value, dict): return ' '.join(f'{k.replace("_", " ")} {_flat(v)}' for k, v in value.items())
    if isinstance(value, list): return ' '.join(_flat(v) for v in value)
    return str(value)


def _doc_fields(e):
    title = e.get('title') or e['id'].replace('-', ' ')
    return {'title': title, 'questions': ' '.join(e['questions']), 'entities': ' '.join(e.get('entities') or []),
            'subtopic': f"{e.get('subtopic', '')} {e['topic'].replace('_', ' ')}", 'summary': e['summary'], 'facts': _flat(e['facts'])}


FIELD_WEIGHTS = {'title': 3.0, 'questions': 2.0, 'entities': 2.0, 'subtopic': 1.0, 'summary': 1.0, 'facts': 0.5}


class Index:
    def __init__(self, entries, encoder=None):
        self.entries = entries
        self.docs = []
        for e in entries:
            c = Counter()
            for field, text in _doc_fields(e).items():
                for t in tokens(text): c[t] += FIELD_WEIGHTS[field]
            self.docs.append(c)
        n = len(self.docs)
        df = Counter(t for d in self.docs for t in d)
        self.idf = {t: math.log(1 + (n - k + 0.5) / (k + 0.5)) for t, k in df.items()}
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lens) / max(1, n)
        self.encoder, self.vectors = encoder, None
        if encoder is not None:
            try:
                self.vectors = self._encode_entries()
            except Exception:  # noqa: BLE001 - an encoder failure means BM25 only, never a broken answer
                self.encoder, self.vectors = None, None

    def _encode_entries(self):
        import numpy as np
        texts = []
        for e in self.entries:
            title = e.get('title') or e['id']
            texts.append((title + '. ' + ' '.join(e['questions']))[:900])
            texts.append((title + '. ' + ' '.join(e['summary'].split()[:110]))[:900])
        v = np.asarray(self.encoder.encode(texts, batch_size=16), dtype='float32')
        return v.reshape(len(self.entries), 2, -1)

    def bm25(self, question, k1=1.2, b=0.75):
        q = tokens(question)
        scores = []
        for d, ln in zip(self.docs, self.lens):
            s = 0.0
            for t in q:
                f = d.get(t)
                if f: s += self.idf[t] * f * (k1 + 1) / (f + k1 * (1 - b + b * ln / self.avg))
            scores.append(s)
        return scores

    def dense(self, question):
        if self.vectors is None: return None
        import numpy as np
        qv = np.asarray(self.encoder.encode([question[:900]])[0], dtype='float32')
        return (self.vectors @ qv).max(axis=1).tolist()

    def search(self, question, k=DEFAULT_TOP, topic=None):
        lex = self.bm25(question)
        top_lex = max(lex) if lex and max(lex) > 0 else 1.0
        # squash so that one very lexical hit does not make every other score look tiny
        lexn = [s / top_lex * min(1.0, top_lex / 8.0) for s in lex]
        den = self.dense(question)
        if den is not None:
            fused = [0.45 * l + 0.55 * max(0.0, d) for l, d in zip(lexn, den)]
            method = 'bm25+encoder'
        else:
            fused, method = lexn, 'bm25'
        order = sorted(range(len(fused)), key=lambda i: (-fused[i], self.entries[i]['id']))
        if topic and topic != 'any':
            order = [i for i in order if self.entries[i]['topic'] == topic]
        q = list(dict.fromkeys(tokens(question)))
        unseen = math.log(1 + (len(self.docs) + 0.5) / 0.5)
        mass = sum(self.idf.get(t, unseen) for t in q) or 1.0
        hits = [{'entry': self.entries[i], 'score': round(fused[i], 4), 'bm25': round(lex[i], 3),
                 'dense': None if den is None else round(den[i], 4),
                 # share of the question's informative words (idf mass) found in this entry: off-topic questions score low
                 'coverage': round(sum(self.idf.get(t, unseen) for t in q if t in self.docs[i]) / mass, 3)} for i in order[:k]]
        return hits, method


_INDEX = {}
_LOCK = threading.Lock()


def private_encoder(encoder):
    """Share a loaded LocalEncoder's model/tokenizer but write vectors to this module's own cache (never the stack's)."""
    if encoder is None: return None
    try:
        enc = copy.copy(encoder)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        enc.db = sqlite3.connect(CACHE, check_same_thread=False)
        enc.db.execute('CREATE TABLE IF NOT EXISTS encoder_vectors (key TEXT PRIMARY KEY, dimensions INTEGER NOT NULL, vector BLOB NOT NULL)')
        enc.db.commit()
        enc.stats = {'cache_hits': 0, 'texts_encoded': 0, 'input_tokens': 0, 'inference_seconds': 0.0}
        return enc
    except Exception:  # noqa: BLE001 - not a LocalEncoder (e.g. a test double): use it as given
        return encoder


def default_encoder():
    """The pinned local MiniLM when installed (AGERE_KNOWLEDGE_ENCODER=off disables); None means BM25 only."""
    if os.environ.get('AGERE_KNOWLEDGE_ENCODER', 'on') == 'off': return None
    path = Path(os.environ.get('AGERE_KNOWLEDGE_ENCODER_PATH') or ROOT / 'models' / 'minilm-l6-v2')
    if not (path / 'agere-model.json').exists(): return None
    key = ('default', str(path))
    with _LOCK:
        if key not in _INDEX:
            try:
                from ...local_encoders import LocalEncoder
                CACHE.parent.mkdir(parents=True, exist_ok=True)
                enc = LocalEncoder(path, pooling='mean', cache=CACHE, threads=2)
                enc.db.close(); enc.db = sqlite3.connect(CACHE, check_same_thread=False)
                _INDEX[key] = enc
            except Exception:  # noqa: BLE001
                _INDEX[key] = None
        return _INDEX[key]


def index(encoder=None, directory=None):
    digest = corpus_digest(directory)
    key = (digest, getattr(encoder, 'identity', None) if encoder is not None else None, str(directory or ''))
    with _LOCK:
        if key not in _INDEX:
            _INDEX[key] = Index(load(directory), private_encoder(encoder))
        return _INDEX[key]


# ---------------------------------------------------------------- answers --------------------------------------------

def _first_sentences(text, n=2):
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z0-9])', text.strip())
    return ' '.join(parts[:n])


def compose(question, hits):
    """Extractive answer: the best entry's summary, short leads from close runners-up, contradictions, numbered sources."""
    if not hits: return '', []
    sources, cite = [], {}

    def ref(e):
        nums = []
        for c in e['citations'][:3]:
            if c['url'] not in cite:
                cite[c['url']] = len(sources) + 1
                sources.append({'n': cite[c['url']], 'url': c['url'], 'title': c['title'], 'publisher': c['publisher'],
                                'date': c.get('date', ''), 'source_type': c['source_type'], 'entry': e['id']})
            nums.append(cite[c['url']])
        return '[' + ','.join(str(n) for n in nums) + ']'

    best = hits[0]
    text = f"{best['entry']['summary'].strip()} {ref(best['entry'])}"
    related = [h for h in hits[1:] if h['score'] >= RELATED_RATIO * best['score']][:3]
    if related:
        text += '\n\nRelated: ' + ' '.join(f"{h['entry'].get('title', h['entry']['id'])}: {_first_sentences(h['entry']['summary'], 1)} {ref(h['entry'])}"
                                            for h in related)
    disagreements = [c for h in [best, *related] for c in (h['entry'].get('contradictions') or [])][:2]
    if disagreements:
        text += '\n\nSources disagree: ' + ' '.join(f"{c['claim'].rstrip('.')}: {c['note'].rstrip('.')}." for c in disagreements)
    return text, sources


def validate(params):
    if not isinstance(params, dict): raise ValueError('knowledge parameters must be an object')
    extra = set(params) - {'query', 'top', 'topic'}
    if extra: raise ValueError(f'Unknown knowledge parameters: {sorted(extra)}')
    q = params.get('query')
    if not isinstance(q, str) or not 3 <= len(q.strip()) <= 1500: raise ValueError('knowledge needs the question text in query (3-1500 chars)')
    top = params.get('top', DEFAULT_TOP)
    if not isinstance(top, int) or isinstance(top, bool) or not 1 <= top <= 10: raise ValueError('top must be an integer 1-10')
    topic = params.get('topic', 'any') or 'any'
    if topic not in (*TOPICS, 'any'): raise ValueError(f'topic must be one of {", ".join(TOPICS)} or any')
    return {'query': q.strip(), 'top': top, 'topic': topic}


def synthesis_enabled():
    return os.environ.get('AGERE_KNOWLEDGE_SYNTHESIS', 'off').lower() in ('on', '1', 'true')


def synthesize(question, hits, *, allow_live=False, ledger=None, out=None, model='claude-haiku-5-5'):
    """Optional hosted-model synthesis over the retrieved entries only. Off unless AGERE_KNOWLEDGE_SYNTHESIS=on and the
    caller allows live calls; returns None when not run. The model sees only the cited summaries and must cite [n]."""
    if not (synthesis_enabled() and allow_live and ledger is not None and out is not None): return None
    from .context_student import send
    Path(out).mkdir(parents=True, exist_ok=True)
    passages = '\n\n'.join(f"[{i + 1}] {h['entry'].get('title', h['entry']['id'])}: {h['entry']['summary']}" for i, h in enumerate(hits))
    body = {'model': model, 'max_tokens': 700,
            'system': 'Answer the question using ONLY the numbered passages. Cite passages as [n]. If they do not answer it, say so. '
                      'No facts beyond the passages.',
            'messages': [{'role': 'user', 'content': f'Question: {question}\n\nPassages:\n{passages}'[:18000]}]}
    text, receipt = send(body, 'claude', ledger, Path(out), parse=lambda raw: ''.join(b.get('text', '') for b in raw.get('content', []) if b.get('type') == 'text'))
    return {'text': text, 'model': model, 'cache_hit': bool(receipt.get('cache_hit')),
            'new_paid_api_calls': 0 if receipt.get('cache_hit') else 1}


def execute(params, *, encoder=None, directory=None, allow_live=False, ledger=None, out=None):
    """Retrieve and compose. Report: hits (entries with scores), composed answer, sources, retrieval method."""
    params = validate(params)
    idx = index(encoder, directory)
    hits, method = idx.search(params['query'], params['top'], params['topic'])
    on_topic = bool(hits) and max(h['coverage'] for h in hits) >= MIN_COVERAGE
    found = [h for h in hits if h['score'] >= MIN_SCORE] if on_topic else []
    text, sources = compose(params['query'], found)
    report = {'version': VERSION, 'query': params['query'], 'retrieval': method, 'answered': bool(found),
              'corpus': {'entries': len(idx.entries), 'digest': corpus_digest(directory)[:16]},
              'answer': text, 'sources': sources, 'new_paid_api_calls': 0, 'synthesis': None,
              'hits': [{'id': h['entry']['id'], 'topic': h['entry']['topic'], 'title': h['entry'].get('title', h['entry']['id']),
                        'score': h['score'], 'bm25': h['bm25'], 'dense': h['dense'], 'summary': h['entry']['summary'],
                        'facts': h['entry']['facts'], 'citations': h['entry']['citations'], 'confidence': h['entry']['confidence'],
                        'last_verified': h['entry']['last_verified'], 'contradictions': h['entry'].get('contradictions') or [],
                        'coverage': h['coverage'], 'relevant': on_topic and h['score'] >= MIN_SCORE} for h in hits],
              'method': ('Retrieved cited knowledge entries (' + method + ') and composed the answer extractively from their '
                         'summaries; no model wrote the text.')}
    if found:
        s = synthesize(params['query'], found, allow_live=allow_live, ledger=ledger, out=out)
        if s:
            report['synthesis'] = s
            report['new_paid_api_calls'] = s['new_paid_api_calls']
            report['method'] = f"Retrieved cited knowledge entries ({method}); answer synthesised by {s['model']} from those entries only."
    return report


def summary(report):
    if not report['answered']:
        return ('No knowledge entry covers this question. The knowledge base holds cited explanations of VBC programs, '
                'attribution, risk adjustment, quality, CMS/CMMI models, market players, frameworks and evidence.')
    if report.get('synthesis') and report['synthesis'].get('text'): return report['synthesis']['text']
    return report['answer']


def answer(question, k=DEFAULT_TOP, *, encoder=None, topic='any', directory=None):
    """Top entries with citations for a question (the library entry point)."""
    if encoder is None: encoder = default_encoder()
    return execute({'query': question, 'top': k, 'topic': topic}, encoder=encoder, directory=directory)


def context_for(question, k=2, *, encoder=None, min_score=0.35):
    """Background entries for a DATA answer (e.g. 'your PMPM vs typical MSSP ACOs'): only confident matches, no text."""
    try:
        r = answer(question, k, encoder=encoder)
    except (ValueError, OSError):
        return []
    return [{'id': h['id'], 'title': h['title'], 'citations': h['citations'][:2]} for h in r['hits'] if h['score'] >= min_score]


if __name__ == '__main__':
    files = sys.argv[1:] or [str(p) for p in corpus_files()]
    count, problems = check_files(files)
    print('\n'.join(problems))
    print(f'{count} entries, {len(problems)} with problems')
    sys.exit(1 if problems else 0)
