"""Meaning-level reuse of verified plans: a paraphrase of a previously VERIFIED question reuses its verified query.

Reuse needs all of:
  * same verification context (learning.context: data release digest | semantic layer version);
  * neither question corrected by a user (learning.corrections), nor a paraphrase of a corrected question;
  * neither question a benchmark question (bench/questions, bench/acceptance) - benchmarks always run the real ladder;
  * an identical slot signature: the ordered sequence of numbers, years, months, comparators, negations, and/or,
    named concepts (conditions, labs, encounter types, drug classes, social needs, payers), identifiers and group-bys;
    the same requested answer shape (analysis_graph.requested_shape); and the same bag of remaining content words
    (units such as patients vs visits, statuses, sources...). Only filler words, case, punctuation, plurals and the
    compiler's own synonym lists may differ;
  * cosine similarity of the installed sentence encoder's embeddings >= SIMILARITY.
The slot signature does the safety work; the embedding is a second, independent agreement check.
"""
import json, os, re, sqlite3
from functools import lru_cache
from pathlib import Path

import numpy as np

VERSION = 'semantic-cache/1'
SIMILARITY = 0.85  # hand-set fallback (semantic_cache_eval.py: 0 false reuses); a certified LTT value replaces it
ROOT = Path(__file__).resolve().parents[3]

NUMBER_WORDS = {'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
                'ten': 10, 'eleven': 11, 'twelve': 12, 'fifteen': 15, 'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
                'sixty': 60, 'ninety': 90, 'hundred': 100, 'single': 1, 'once': 1, 'twice': 2, 'half': 0.5, 'dozen': 12}
MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december']
MONTH_ABBR = {m[:3]: m for m in MONTHS} | {'sept': 'september'}
# Words that never change what is asked (removed before comparing). Everything else must match exactly.
FILLER = set('''a an the of in on at to for from with is are was were be been being do does did have has had having
what whats what's how much show me give tell please can could would you i we us our my your their there this that these those
it its list display find get pull report return provide want need know let see look about please kindly just also
which who whom whose here up out into as during overall w currently current total all received observed data warehouse
patients patient people persons person individuals individual pts pt'''.split()) - {'how', 'much', 'all', 'total', 'current', 'currently'}
# Units of analysis and population nouns are NOT filler (patients vs visits vs members must differ) - restore them.
FILLER -= {'patients', 'patient', 'people', 'persons', 'person', 'individuals', 'individual', 'pts', 'pt'}
UNIT_SYNONYMS = {'pts': 'patient', 'pt': 'patient', 'people': 'patient', 'persons': 'patient', 'person': 'patient',
                 'individuals': 'patient', 'individual': 'patient', 'encounters': 'visit', 'encounter': 'visit'}
AGGREGATES = [(r'\bhow many\b|\bnumber of\b|\bcount(?:s|ed)? of\b|\bcount\b|\btally\b', '<count>'), (r'\baverage\b|\bmean\b|\bavg\b', '<mean>'),
              (r'\bmedian\b', '<median>'), (r'\bpercent(?:age)?\b|%|\bshare\b|\bproportion\b|\bfraction\b', '<percent>')]
LOGIC = {'and': '<and>', 'or': '<or>', 'nor': '<nor>', 'not': '<not>', 'no': '<not>', "don't": '<not>', "didn't": '<not>', "doesn't": '<not>',
         'never': '<never>', 'without': '<without>', 'except': '<except>', 'excluding': '<except>', 'exclude': '<except>', 'but': '<but>',
         'only': '<only>', 'either': '<either>', 'neither': '<neither>', 'both': '<both>', 'versus': '<vs>', 'vs': '<vs>', 'any': '<any>',
         'every': '<every>', 'unless': '<unless>', 'other': '<other>'}
CRITICAL = re.compile(r'^(?:<num:.*>|<month:.*>|<quarter:.*>|<cmp:.*>|<concept:.*>|<payer:.*>|<group:.*>|<id:.*>|<and>|<or>|<nor>|<not>|<never>|'
                      r'<without>|<except>|<but>|<only>|<either>|<neither>|<both>|<vs>|<unless>|<other>)$')


@lru_cache(maxsize=1)
def _vocabulary():
    """(regex, token) pairs from the compiler's own alias tables, longest phrases first."""
    from . import local_compiler as lc
    pairs = []
    for table in (lc.CONDITION_ALIASES, lc.MEASUREMENT_ALIASES, lc.ENCOUNTER_ALIASES):
        for concept, aliases in table.items():
            pairs += [(r'\b' + re.escape(a) + r's?\b', f'<concept:{concept}>') for a in aliases]
    for concept, pattern in {**lc.DRUG_ALIASES, **lc.DRUG_GROUP_ALIASES}.items():
        pairs.append((r'\b(?:' + pattern + r')\b', f'<concept:{concept}>'))
    for phrase, (_, field) in lc.SOCIAL_FLAGS.items():
        pairs.append((r'\b' + re.escape(phrase) + r'\b', f'<concept:{field}>'))
    for pattern, (_, field) in lc.SOCIAL_PHRASES:
        pairs.append((pattern, f'<concept:{field}>'))
    for phrase, payer in lc.PAYERS.items():
        pairs.append((r'\b' + re.escape(phrase) + r'\b', f'<payer:{payer}>'))
    pairs.append((r'\b(?:medicare advantage|ma plans?)\b', '<payer:medicare_advantage>'))
    pairs.append((r'\bself[- ]pay\b', '<payer:self_pay>'))
    def length(p):  # longest literal alias first so 'type 2 diabetes' wins over 'diabetes'
        return -len(re.sub(r'\\.|[()?:|+*\[\]]', '', p[0]))
    pairs.sort(key=length)
    groups = [(pattern, f'<group:{name}>') for pattern, name in lc.GROUPS]
    return groups, pairs, lc.COMPARATORS


def canonical(question):
    """Question -> token list: synonyms, numbers, comparators, months and group-bys replaced by canonical tokens."""
    q = ' ' + ' '.join(str(question).lower().replace('’', "'").split()) + ' '
    groups, pairs, comparators = _vocabulary()
    protected = []
    def protect(token):
        protected.append(token)
        n, tag = len(protected) - 1, ''
        while True:  # letters only: a digit inside the placeholder would be read as a number later
            n, r = divmod(n, 26); tag = chr(65 + r) + tag
            if not n: break
        return f' \x00{tag}\x00 '
    q = re.sub(r'\b([a-z]{2,6})[-_]?(\d{2,})\b', lambda m: protect(f'<id:{m.group(1)}{m.group(2)}>') if m.group(1) not in ('q', 'fy', 'cy', 'ytd') else m.group(0), q)
    q = re.sub(r'\bq([1-4])\b|\b(first|second|third|fourth) quarter\b',
               lambda m: protect(f'<quarter:{m.group(1) or {"first": 1, "second": 2, "third": 3, "fourth": 4}[m.group(2)]}>'), q)
    for pattern, token in groups: q = re.sub(pattern, lambda m, t=token: protect(t), q)
    for pattern, token in pairs: q = re.sub(pattern, lambda m, t=token: protect(t), q)
    for pattern, token in AGGREGATES: q = re.sub(pattern, lambda m, t=token: protect(t), q)
    q = re.sub(r'\bor (?:more|higher|greater|above|over|older|later|longer)\b|\+(?!\w)', lambda m: protect('<cmp:ge_suffix>'), q)
    q = re.sub(r'\bor (?:less|lower|fewer|below|under|younger|earlier|shorter)\b', lambda m: protect('<cmp:le_suffix>'), q)
    for pattern, op in comparators: q = re.sub(r'(?<![\w<>=])' + pattern + r'(?![\w=])', lambda m, o=op: protect(f'<cmp:{o}>'), q)
    q = re.sub(r'\$\s*', lambda m: protect('<usd>'), q)
    def number(m):
        value = float(m.group(0).replace(',', ''))
        return protect(f'<num:{int(value) if value == int(value) else value}>')
    q = re.sub(r'(?<![\w.])\d[\d,]*(?:\.\d+)?', number, q)
    out = []
    for raw in re.findall(r"\x00[A-Z]+\x00|[a-z][a-z'.-]*", q):
        if raw.startswith('\x00'):
            index = 0
            for ch in raw.strip('\x00'): index = index * 26 + ord(ch) - 65
            out.append(protected[index]); continue
        word = raw.strip(".'-")
        for part in word.split('-') if '-' in word and word not in ("don't",) else [word]:
            if not part: continue
            if part in NUMBER_WORDS: out.append(f'<num:{NUMBER_WORDS[part]}>'); continue
            if part in MONTHS and part != 'may' or part in MONTH_ABBR and part != 'may':  # 'may' stays a plain word
                out.append(f'<month:{MONTH_ABBR.get(part, part)}>'); continue
            if part in LOGIC: out.append(LOGIC[part]); continue
            part = UNIT_SYNONYMS.get(part, part)
            if part in FILLER: continue
            if len(part) > 3 and part.endswith('ies'): part = part[:-3] + 'y'
            elif len(part) > 3 and part.endswith('s') and not part.endswith('ss'): part = part[:-1]
            out.append(part)
    return out


def signature(question):
    """Hashable slot signature; two questions may share a plan only when their signatures are equal."""
    from .analysis_graph import requested_shape
    tokens = canonical(question)
    critical = tuple(t for t in tokens if CRITICAL.match(t))
    rest = tuple(sorted(t for t in tokens if not CRITICAL.match(t)))
    return (requested_shape(question), critical, rest)


def slots(question):
    """Human-readable slots for receipts."""
    shape, critical, rest = signature(question)
    return {'requested_shape': shape, 'ordered_slots': list(critical), 'content_words': list(rest)}


def same_slots(a, b):
    return signature(a) == signature(b)


def _norm(q): return ' '.join(str(q).strip().split()).casefold()


@lru_cache(maxsize=1)
def benchmark_questions():
    found = set()
    for folder in ('questions', 'acceptance'):
        for f in (ROOT / 'bench' / folder).glob('*.json'):
            try: items = json.loads(f.read_text())
            except (OSError, ValueError): continue
            found |= {_norm(i['question']).rstrip('?.') for i in items if isinstance(i, dict) and i.get('question')}
    return frozenset(found)


def is_benchmark(question): return _norm(question).rstrip('?.') in benchmark_questions()


_INDEX = {}


def _verified(path, ctx):
    """Latest verified SQL plan per question for this context, plus corrected questions (read-only)."""
    target = Path(path) / 'learning.sqlite3'
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'verified' not in tables: return [], set()
        cols = [r[1] for r in db.execute('PRAGMA table_info(verified)')]
        if 'context' not in cols: return [], set()
        stamp = db.execute('SELECT count(*), max(created) FROM verified WHERE context = ?', (ctx,)).fetchone()
        corrected = {_norm(r[0]) for r in db.execute('SELECT question FROM corrections')} if 'corrections' in tables else set()
        key = (str(target), ctx, stamp, len(corrected))
        if key not in _INDEX:
            rows = {}
            for q, kind, plan, agreement in db.execute('SELECT question, plan_kind, plan, agreement FROM verified WHERE context = ? '
                                                       'ORDER BY created', (ctx,)):
                rows[_norm(q)] = {'question': q, 'plan_kind': kind, 'plan': json.loads(plan), 'agreement': agreement}
            _INDEX.clear()
            _INDEX[key] = ([{**r, 'signature': signature(r['question'])} for r in rows.values()], corrected)
        return _INDEX[key]
    finally:
        db.close()


def similarity_gate(encoder=None):
    """Learn-then-Test similarity cut for slot-identical paraphrases (calibrated_thresholds 'semantic_cache.similarity',
    bound to the encoder identity it was fitted with), else the hand-set SIMILARITY."""
    from .calibrated_thresholds import get
    return get('semantic_cache.similarity', SIMILARITY, bind=getattr(encoder, 'identity', None))


def lookup(path, question, encoder, *, threshold=None):
    """-> (hit | None, receipt). hit = {plan_kind, plan, agreement, matched_question}."""
    from . import learning
    gate = similarity_gate(encoder) if threshold is None else {'threshold': threshold, 'calibrated': False}
    threshold = gate['threshold']
    receipt = {'cache': VERSION, 'threshold': threshold, 'threshold_calibrated': gate['calibrated']}
    if os.environ.get('AGERE_SEMANTIC_CACHE', 'on') == 'off': return None, {**receipt, 'reason': 'disabled'}
    if encoder is None: return None, {**receipt, 'reason': 'no local encoder'}
    if not (Path(path) / 'learning.sqlite3').exists(): return None, {**receipt, 'reason': 'no verified store'}
    if is_benchmark(question): return None, {**receipt, 'reason': 'benchmark question: never served from a cache'}
    rows, corrected = _verified(path, learning.context(path))
    if _norm(question) in corrected: return None, {**receipt, 'reason': 'question was corrected by a user'}
    sig = signature(question)
    if any(signature(c) == sig for c in corrected):
        return None, {**receipt, 'reason': 'paraphrase of a corrected question'}
    if any(_norm(r['question']) == _norm(question) for r in rows):
        return None, {**receipt, 'reason': 'exact verified question: the learned_verified_sql rung owns it'}
    candidates = [r for r in rows if r['signature'] == sig and r['plan_kind'] == 'sql'
                  and _norm(r['question']) not in corrected and not is_benchmark(r['question'])]
    if not candidates: return None, {**receipt, 'reason': 'no verified question with identical slots', 'candidates': 0}
    vectors = np.asarray(encoder.encode([question] + [r['question'] for r in candidates]), dtype=float)
    norms = np.maximum(np.linalg.norm(vectors, axis=1), 1e-12)
    sims = vectors[1:] @ vectors[0] / (norms[1:] * norms[0])
    best = int(np.argmax(sims))
    receipt.update(candidates=len(candidates), matched_question=candidates[best]['question'], similarity=round(float(sims[best]), 4),
                   slots=slots(question))
    if sims[best] < threshold: return None, {**receipt, 'reason': 'identical slots but similarity below threshold'}
    hit = candidates[best]
    return {'plan_kind': hit['plan_kind'], 'plan': hit['plan'], 'agreement': hit['agreement'], 'matched_question': hit['question']}, \
           {**receipt, 'reason': 'reused'}
