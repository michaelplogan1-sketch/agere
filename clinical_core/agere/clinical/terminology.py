"""Clinical terminology mapping: messy labels -> the codes and concept ids the compiler queries (retrieve, then classify).

    map_label(label, kind=None, ...)   -> {'label', 'kind', 'concept', 'classes', 'codes', 'method', 'candidates', ...}
    grammar_terms(question, store)     -> {'condition': {span: concept}, 'lab': {span: concept}, 'medication': {span: [classes]},
                                           'used': [...]}   free: deterministic + saved mappings only (request_language.Parser)
    tier(stack, catalog, question, population, ledger)      cascade proposer: adjudicate the unconsumed clinical term, save the
                                                            mapping, recompile the ORIGINAL question with the grammar

Order for one label (cheapest first; the first that decides wins):
  1. saved mapping (terminology-map.sqlite3 beside the semantic layer): every accepted adjudication is saved, so the next
     question with the same term is free and deterministic;
  2. deterministic exact match: the concept library (concepts.py code/label patterns, the whole span; a few clinical
     modifiers such as 'congestive' or 'chronic' may precede a condition), the reviewed normalization dictionaries
     (config/agere/normalization, entries at confidence >= 0.8 with an Agere concept), curated lay synonyms
     (config/agere/normalization/lay_terms.json), RxNorm brand -> ingredient naming (drug_brands.json) and ingredient words;
  3. retrieval: lexical (word stems + character trigrams) over every concept's documents (id, library label, pattern
     literals, dictionary labels, brands), plus the pinned terminology encoder when it is loaded;
  4. System One (system_one.decide: Jev first, Perplexity fallback; typed Choice among the top-k retrieved candidates plus
     'none'; never Claude). Accepted only at calibrated p >= ACCEPT_P and a label other than none/unknown; otherwise the
     term stays unmapped (abstain) and the question declines as before. Cost goes to the cascade usage.
Codes: conditions -> ICD-10-CM prefixes (+ SNOMED where the received data carries it), labs -> LOINC, medications -> Agere
drug classes with RxNorm ingredient naming. Synthetic data only.
"""
import functools
import json
import re
import sqlite3
import time
from pathlib import Path

from . import concepts

VERSION = 'terminology-map/1'
ROOT = Path(__file__).resolve().parents[3]
NORMALIZATION = ROOT / 'config/agere/normalization'
STORE_NAME = 'terminology-map.sqlite3'
KINDS = ('condition', 'lab', 'medication')
ACCEPT_P = 0.8
DICTIONARY_CONFIDENCE = 0.8
MAX_OPTIONS = 8
RETRIEVAL_FLOOR = 0.25   # below this no candidate is worth a System One call
MAX_ADJUDICATIONS = 3    # per question
MAX_SPAN_WORDS = 5
MODIFIERS = re.compile(r'^(?:(?:congestive|chronic|acute|essential|primary|uncontrolled|known|diagnosed|active)\s+)+', re.I)
STOP = set('''a an the and or of in on at to for from by with without within into is are was were be been have has had
having do does did how many much what which who whose patients patient members member people their our any all who
taking take takes prescribed filled on test tests result results level levels value values ever recorded had'''.split())


def _norm(text):
    return ' '.join(re.sub(r'[^\w/+\-. ]+', ' ', str(text or '').lower()).replace('_', ' ').split()).strip(' .-')


# ------------------------------------------------------------------------------------------------ dictionaries
@functools.lru_cache(maxsize=None)
def _dictionary(domain):
    try:
        return json.loads((NORMALIZATION / f'{domain}.json').read_text()).get('entries', {})
    except (OSError, ValueError):
        return {}


@functools.lru_cache(maxsize=None)
def lay_terms():
    try:
        data = json.loads((NORMALIZATION / 'lay_terms.json').read_text())
    except (OSError, ValueError):
        return {}
    return {kind: {_norm(k): v for k, v in (data.get(kind) or {}).items()} for kind in KINDS}


def _entry(value):
    """A dictionary entry, or its first unit variant (labs keyed by unit)."""
    if isinstance(value, dict) and 'variants_by_unit' in value:
        return next(iter(value['variants_by_unit'].values()), {})
    return value if isinstance(value, dict) else {}


@functools.lru_cache(maxsize=None)
def _exact_dictionary(kind):
    """normalized label -> concept for confident dictionary entries (conditions / labs)."""
    domain = {'condition': 'conditions', 'lab': 'labs'}[kind]
    out = {}
    for key, value in _dictionary(domain).items():
        e = _entry(value)
        if e.get('agere_concept') and (e.get('confidence') or 0) >= DICTIONARY_CONFIDENCE:
            for label in (key, e.get('normalized_label'), e.get('component')):
                if label: out.setdefault(_norm(label), e['agere_concept'])
    return out


def codes(kind, concept):
    """Codes the compiler queries for a concept."""
    if kind == 'condition':
        return {'system': 'ICD-10-CM', 'codes': list(concepts.CONDITIONS.get(concept, {}).get('icd10', []))}
    if kind == 'lab':
        return {'system': 'LOINC', 'codes': list(concepts.MEASUREMENTS.get(concept, {}).get('loinc', []))}
    from .drug_classes import GROUPS
    return {'system': 'Agere drug class (RxNorm ingredient naming)', 'codes': list(GROUPS.get(concept, [concept]))}


def describe(kind, concept):
    if kind == 'condition': return concepts.CONDITIONS[concept]['label']
    if kind == 'lab': return concepts.MEASUREMENTS[concept]['label']
    return concept.replace('_', ' ') + ' (drug class)'


# ------------------------------------------------------------------------------------------------ deterministic
def _whole_span(pattern, text):
    """The pattern matches from the start and only finishes the last word (stems like 'diabet' + 'es')."""
    m = re.search(pattern, text, re.I)
    return bool(m and m.start() == 0 and re.fullmatch(r'\w*', text[m.end():]))


def _condition(label):
    text = MODIFIERS.sub('', label)
    hits = [c for c in concepts.conditions_for_label(text) if _whole_span(concepts.CONDITIONS[c]['text'], text)]
    return hits[0] if hits else None


def _medication(label):
    from . import drug_classes
    if label in {k.replace('_', ' ') for k in (*drug_classes.CLASSES, *drug_classes.GROUPS)}:
        key = label.replace(' ', '_')
        return list(drug_classes.GROUPS.get(key, [key])), 'class'
    brands = [b for b in drug_classes.brand_lookup(label) if _norm(b['brand']) == label]
    if brands: return list(dict.fromkeys(c for b in brands for c in b['classes'])), 'brand'
    found = drug_classes.ingredients(label)
    rest = label
    for i in found: rest = re.sub(re.escape(i), ' ', rest)
    if found and not re.sub(r'[\s/+\-]|and', '', rest):
        classes = drug_classes.classify(label)
        if classes: return classes, 'ingredient'
    return None, None


def deterministic(label, kind=None):
    """Exact, unambiguous match -> {'kind', 'concept', 'classes', 'method'} or None. kind None tries every kind."""
    text = _norm(label)
    if len(text) < 2 or text in STOP: return None
    for k in ([kind] if kind else KINDS):
        lay = lay_terms().get(k, {}).get(text)
        if lay:
            classes = list(lay) if isinstance(lay, list) else None
            return {'kind': k, 'concept': classes[0] if classes else lay, 'classes': classes, 'method': 'lay_synonym'}
        if k == 'condition':
            c = _exact_dictionary('condition').get(text)
            if c: return {'kind': k, 'concept': c, 'classes': None, 'method': 'dictionary'}
            c = _condition(text)
            if c: return {'kind': k, 'concept': c, 'classes': None, 'method': 'alias'}
        elif k == 'lab':
            c = concepts.concept_for_label(text) or _exact_dictionary('lab').get(text)
            if c: return {'kind': k, 'concept': c, 'classes': None, 'method': 'alias' if concepts.concept_for_label(text) else 'dictionary'}
        elif k == 'medication':
            classes, method = _medication(text)
            if classes: return {'kind': k, 'concept': classes[0], 'classes': classes, 'method': method}
    return None


# ------------------------------------------------------------------------------------------------ retrieval
def _literals(pattern):
    out = []
    for alt in re.split(r'\|', pattern or ''):
        alt = alt.replace(r'\b', '').replace('?', '').strip()
        if alt and re.fullmatch(r'[\w \-/]+', alt): out.append(alt)
    return out


@functools.lru_cache(maxsize=None)
def documents():
    """[(kind, concept, normalized document text)] for every concept the compiler can query."""
    from . import drug_classes
    docs = []
    for c, d in concepts.CONDITIONS.items():
        docs += [('condition', c, _norm(t)) for t in (c, d['label'], *_literals(d['text']))]
    for key, value in _dictionary('conditions').items():
        e = _entry(value)
        if e.get('agere_concept') in concepts.CONDITIONS:
            docs += [('condition', e['agere_concept'], _norm(t)) for t in (key, e.get('normalized_label')) if t]
    for c, d in concepts.MEASUREMENTS.items():
        docs += [('lab', c, _norm(t)) for t in (c, d['label'], *_literals(d['names']))]
    for key, value in _dictionary('labs').items():
        e = _entry(value)
        if e.get('agere_concept') in concepts.MEASUREMENTS:
            docs += [('lab', e['agere_concept'], _norm(t)) for t in (key, e.get('component')) if t]
    for c, rx in drug_classes.CLASSES.items():
        docs += [('medication', c, _norm(t)) for t in (c, *[w for w in re.findall(r'[a-z]{5,}', rx)])]
    for brand, b in drug_classes.brands().items():
        docs += [('medication', c, _norm(brand)) for c in b.get('classes', [])]
    for kind, table in lay_terms().items():
        for term, target in table.items():
            for c in (target if isinstance(target, list) else [target]):
                docs.append((kind, c, term))
    return [d for d in dict.fromkeys(docs) if d[2]]


def _trigrams(text):
    t = f'  {text} '
    return {t[i:i + 3] for i in range(len(t) - 2)}


def _stems(text):
    return {w[:5] for w in re.findall(r'\w+', text) if w not in STOP}


def similarity(a, b):
    ta, tb = _trigrams(a), _trigrams(b)
    dice = 2 * len(ta & tb) / (len(ta) + len(tb)) if ta and tb else 0.0
    sa, sb = _stems(a), _stems(b)
    jac = len(sa & sb) / len(sa | sb) if sa and sb else 0.0
    return max(dice, jac)


def retrieve(label, kind=None, *, top=MAX_OPTIONS, encoder=None):
    """Top candidate concepts -> [{'kind', 'concept', 'score', 'doc', 'method'}], best first."""
    text = _norm(label)
    best = {}
    for k, c, doc in documents():
        if kind and k != kind: continue
        s = similarity(text, doc)
        if s > best.get((k, c), {'score': -1})['score']:
            best[(k, c)] = {'kind': k, 'concept': c, 'score': round(s, 4), 'doc': doc, 'method': 'lexical'}
    if encoder is not None:
        for k in ([kind] if kind else ('condition', 'lab')):
            if k not in concepts.TERMINOLOGY_KINDS: continue
            try:
                for c, s in concepts.encoder_candidates([text], k, encoder, top=top)[0]:
                    if s > best.get((k, c), {'score': -1})['score']:
                        best[(k, c)] = {'kind': k, 'concept': c, 'score': round(s, 4), 'doc': None, 'method': 'encoder'}
            except Exception:  # noqa: BLE001 - the encoder is optional
                pass
    return sorted(best.values(), key=lambda r: (-r['score'], r['kind'], r['concept']))[:top]


# ------------------------------------------------------------------------------------------------ saved mappings
def store_path(where):
    """The mapping store beside a semantic layer / stack directory (a .sqlite3 path passes through)."""
    p = Path(where)
    if p.suffix == '.sqlite3' and p.name != STORE_NAME: p = p.parent
    return p if p.name == STORE_NAME else p / STORE_NAME


def _db(path):
    db = sqlite3.connect(path, timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS mapping(label TEXT, kind TEXT, concept TEXT, classes TEXT, method TEXT, provider TEXT, '
               'probability REAL, cost_usd REAL, request_hash TEXT, question TEXT, candidates TEXT, created TEXT, version TEXT, '
               'PRIMARY KEY(label, kind))')
    return db


def saved(store, label=None):
    """Saved mappings -> {label: row} (all) or the row for one normalized label (any kind)."""
    path = store_path(store) if store else None
    if not path or not path.exists(): return {} if label is None else None
    try:
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        rows = db.execute('SELECT label, kind, concept, classes, method, provider, probability FROM mapping').fetchall()
        db.close()
    except sqlite3.Error:
        return {} if label is None else None
    out = {r[0]: {'kind': r[1], 'concept': r[2], 'classes': json.loads(r[3]) if r[3] else None, 'method': 'saved_' + r[4],
                  'provider': r[5], 'probability': r[6]} for r in rows}
    return out if label is None else out.get(_norm(label))


def save(store, label, result, question=None):
    path = store_path(store)
    db = _db(path)
    try:
        s1 = result.get('system_one') or {}
        db.execute('INSERT OR REPLACE INTO mapping VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (_norm(label), result['kind'], result['concept'], json.dumps(result.get('classes')) if result.get('classes') else None,
                    result['method'], s1.get('decided_by'), s1.get('probability'), result.get('cost_usd') or 0.0, s1.get('request_hash'),
                    question, json.dumps(result.get('candidates') or [])[:4000], time.strftime('%Y-%m-%dT%H:%M:%S'), VERSION))
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------------------------------------ System One adjudication
def _option_key(c, kinds):
    return c['concept'] if len(kinds) == 1 else f"{c['kind']}__{c['concept']}"


def adjudicate(label, candidates, *, question=None, as_of=None, ledger=None, allow_live=False, decider=None):
    """System One picks one retrieved candidate or 'none' -> {'accepted', 'kind', 'concept', 'label', 'probability',
    'decided_by', 'cost_usd', 'calls', 'request_hash', 'status'}."""
    if not candidates: return {'accepted': False, 'status': 'no_candidates', 'cost_usd': 0.0, 'calls': 0}
    kinds = {c['kind'] for c in candidates}
    options = {_option_key(c, kinds): describe(c['kind'], c['concept']) for c in candidates}
    options['none'] = 'None of these: the term means something else or is not a clinical concept listed here'
    prompt = (f"Which clinical concept does the term '{label}' refer to"
              + (f" in the analyst question: {question}" if question else '') + '? Choose the concept it means, or none.')
    evidence = [{'id': 'term', 'text': f"Term as written: '{label}'." + (f' Question: {question}' if question else '')}]
    if decider is None:
        from .system_one import decide as decider
    from datetime import date
    r = decider(prompt, options, evidence, as_of=str(as_of or date.today())[:10], threshold=ACCEPT_P, task_key='terminology_map',
                ledger=ledger, allow_live=allow_live)
    calls = sum(1 for s in r.get('steps') or [] if s.get('status') == 'answered' and not s.get('cache_hit'))
    label_out = r.get('label')
    p = (r.get('calibrated_probabilities') or r.get('probabilities') or {}).get(label_out)
    pick = next((c for c in candidates if _option_key(c, kinds) == label_out), None)
    accepted = bool(r.get('decided_by') and pick and p is not None and p >= ACCEPT_P)
    return {'accepted': accepted, 'kind': pick['kind'] if pick else None, 'concept': pick['concept'] if pick else None,
            'label': label_out, 'probability': p, 'decided_by': r.get('decided_by'), 'cost_usd': r.get('cost_usd') or 0.0,
            'calls': calls, 'request_hash': r.get('request_hash'), 'status': r.get('status', 'answered'), 'steps': r.get('steps')}


def map_label(label, kind=None, *, store=None, question=None, as_of=None, ledger=None, allow_live=False, adjudicate_with=True,
              encoder=None, decider=None, save_accepted=True):
    """One messy label -> mapping. method: saved_* | lay_synonym | dictionary | alias | brand | ingredient | class |
    system_one | abstained | no_candidates. concept None unless decided."""
    out = {'label': label, 'kind': kind, 'concept': None, 'classes': None, 'codes': None, 'method': None, 'candidates': [],
           'cost_usd': 0.0, 'calls': 0, 'version': VERSION}
    hit = saved(store, label) if store else None
    if hit and (kind is None or hit['kind'] == kind):
        out.update(hit)
    else:
        found = deterministic(label, kind)
        if found:
            out.update(found)
        else:
            cands = retrieve(label, kind, encoder=encoder)
            out['candidates'] = cands
            if not cands or cands[0]['score'] < RETRIEVAL_FLOOR:
                out['method'] = 'no_candidates'
                return out
            if not adjudicate_with:
                out['method'] = 'not_adjudicated'
                return out
            s1 = adjudicate(label, cands, question=question, as_of=as_of, ledger=ledger, allow_live=allow_live, decider=decider)
            out.update(system_one=s1, cost_usd=s1['cost_usd'], calls=s1['calls'])
            if not s1['accepted']:
                out['method'] = 'abstained'
                return out
            from .drug_classes import GROUPS
            classes = list(GROUPS.get(s1['concept'], [s1['concept']])) if s1['kind'] == 'medication' else None
            out.update(kind=s1['kind'], concept=s1['concept'], classes=classes, method='system_one')
            if store and save_accepted: save(store, label, out, question)
    out['codes'] = codes(out['kind'], out['concept'])
    return out


# ------------------------------------------------------------------------------------------------ the compiler's view
def _spans(question, max_words=MAX_SPAN_WORDS):
    words = list(re.finditer(r"[\w][\w/+\-.']*", question))
    for i in range(len(words)):
        for j in range(min(len(words), i + max_words), i, -1):
            yield question[words[i].start():words[j - 1].end()].rstrip('.')


def grammar_terms(question, store=None, *, known=()):
    """Free vocabulary for one question (deterministic + saved mappings; never a provider call): every span of up to five
    words that maps to a concept the grammar does not already know. known: phrases the grammar already holds."""
    have = {_norm(k) for k in known}
    table = saved(store) if store else {}
    out = {'condition': {}, 'lab': {}, 'medication': {}, 'used': []}
    for span in _spans(question):
        text = _norm(span)
        if not text or text in have or text in STOP or all(w in STOP for w in text.split()): continue
        found = table.get(text) or deterministic(text)
        if not found or found['kind'] not in KINDS: continue
        value = found.get('classes') or [found['concept']] if found['kind'] == 'medication' else found['concept']
        if span in out[found['kind']]: continue
        out[found['kind']][span] = value
        out['used'].append({'span': span, 'kind': found['kind'], 'concept': found['concept'], 'classes': found.get('classes'),
                            'method': found['method'], 'codes': codes(found['kind'], found['concept'])})
    return out


def unconsumed(message):
    m = re.search(r'Unconsumed [\w ]*?:\s*(.+?)(?:\. No language model was called|$)', str(message or ''), re.S)
    return m.group(1).strip() if m else None


def candidate_spans(tail):
    """Spans at the head of the grammar's unconsumed tail (after leading grammatical words), longest first."""
    words = re.findall(r"[\w][\w/+\-.']*", tail or '')
    while words and words[0].lower() in STOP: words = words[1:]
    return [' '.join(words[:n]) for n in range(min(len(words), MAX_SPAN_WORDS), 0, -1)]


def tier(stack, catalog, question, population=None, ledger=None, *, decider=None, interpret=None):
    """Cascade proposer: the grammar declined on an unconsumed clinical term -> System One maps it (saved) -> the grammar
    recompiles the ORIGINAL question. -> (contract, interpretation) or None."""
    from .question_input import QuestionNotUnderstood
    if interpret is None:
        from .shared_planner import _interpret as interpret
    try:
        interpret(catalog, question, population=population)
        return None  # the grammar already reads it (its exact tier answers first)
    except QuestionNotUnderstood as exc:
        tail = unconsumed(str(exc))
    if not tail: return None
    store = store_path(Path(catalog.target).parent)
    live = bool(getattr(stack, 'allow_live', False))
    as_of = None
    try: as_of = stack.retriever.meta['snapshot']['as_of']
    except (AttributeError, KeyError, TypeError): pass
    receipts, spent, calls = [], 0.0, 0
    for span in candidate_spans(tail):
        if len([r for r in receipts if r.get('system_one')]) >= MAX_ADJUDICATIONS: break
        r = map_label(span, store=store, question=question, as_of=as_of, ledger=ledger, allow_live=live, decider=decider)
        spent += r['cost_usd']; calls += r['calls']
        receipts.append({k: r.get(k) for k in ('label', 'kind', 'concept', 'classes', 'codes', 'method', 'system_one')})
        if r['concept']: break
    usage = {'new_paid_api_calls': calls, 'known_api_estimate_usd': round(spent, 6)}
    if not any(r['concept'] for r in receipts):
        err = QuestionNotUnderstood(f"No terminology mapping for '{tail}': " + '; '.join(f"{r['label']}: {r['method']}" for r in receipts))
        err.terminology_usage = usage
        raise err
    contract, interpretation = interpret(catalog, question, population=population)
    return contract, {**interpretation, 'interpreter': 'terminology_mapping', 'terminology': receipts, 'terminology_usage': usage,
                      'review': 'terminology_mapping_then_fully_consumed_grammar'}
