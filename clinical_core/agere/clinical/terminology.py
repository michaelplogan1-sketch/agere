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
  5. granularity (conditions only): a term that is not the concept's own name but maps to a concept covering several ICD-10-CM
     categories ("opioid use disorder" -> substance_use_disorder, F10-F19) is narrowed by System One to the categories it names
     (narrow(); typed choice labelled "F11 - <title from the warehouse's own received descriptions>"). Confident (p >= NARROW_P):
     the narrowed codes travel in the plan (scoped concept name 'substance_use_disorder@F11'); otherwise the concept is kept and
     the mapping carries granularity 'unverified', which the answer states as a limitation. Saved with the mapping.
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
NARROW_P = 0.8           # a wrong narrowing silently drops patients, so it needs the mapping's own bar; below it the broad concept stays, disclosed
DECIDED = ('narrowed', 'whole')  # granularity values that are saved; 'unverified' / 'concept' are recomputed each time
OWN_NAME_METHODS = ('alias', 'dictionary', 'lay_synonym')  # the term IS the concept (or a curated synonym): nothing to narrow
DICTIONARY_CONFIDENCE = 0.8
MAX_OPTIONS = 8
RETRIEVAL_FLOOR = 0.25   # below this no candidate is worth a System One call
MAX_ADJUDICATIONS = 3    # per question
MAX_SPAN_WORDS = 5
RECEIPT_KEYS = ('label', 'kind', 'concept', 'classes', 'codes', 'method', 'system_one', 'granularity', 'narrowing')  # the tier's terminology receipt
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
    if 'narrowing' not in {r[1] for r in db.execute('PRAGMA table_info(mapping)')}:
        db.execute('ALTER TABLE mapping ADD COLUMN narrowing TEXT')  # JSON: the saved granularity decision (narrow())
    return db


def saved(store, label=None):
    """Saved mappings -> {label: row} (all) or the row for one normalized label (any kind). A condition row carries 'narrowing'
    (the saved granularity decision; None when it was never assessed)."""
    path = store_path(store) if store else None
    if not path or not path.exists(): return {} if label is None else None
    try:
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        has = 'narrowing' in {r[1] for r in db.execute('PRAGMA table_info(mapping)')}
        rows = db.execute('SELECT label, kind, concept, classes, method, provider, probability, ' + ('narrowing' if has else 'NULL')
                          + ' FROM mapping').fetchall()
        db.close()
    except sqlite3.Error:
        return {} if label is None else None
    out = {r[0]: {'kind': r[1], 'concept': r[2], 'classes': json.loads(r[3]) if r[3] else None, 'method': 'saved_' + r[4],
                  'provider': r[5], 'probability': r[6], 'narrowing': json.loads(r[7]) if r[7] else None} for r in rows}
    return out if label is None else out.get(_norm(label))


def save(store, label, result, question=None):
    path = store_path(store)
    db = _db(path)
    try:
        s1 = result.get('system_one') or {}
        n = result.get('narrowing')
        db.execute('INSERT OR REPLACE INTO mapping(label, kind, concept, classes, method, provider, probability, cost_usd, request_hash, '
                   'question, candidates, created, version, narrowing) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (_norm(label), result['kind'], result['concept'], json.dumps(result.get('classes')) if result.get('classes') else None,
                    result['method'], s1.get('decided_by'), s1.get('probability'), result.get('cost_usd') or 0.0, s1.get('request_hash'),
                    question, json.dumps(result.get('candidates') or [])[:4000], time.strftime('%Y-%m-%dT%H:%M:%S'), VERSION,
                    json.dumps(n) if n and n.get('granularity') in DECIDED else None))
        db.commit()
    finally:
        db.close()


def save_narrowing(store, label, kind, narrowing):
    """Record a granularity decision on an already saved mapping (one saved before narrowing existed)."""
    db = _db(store_path(store))
    try:
        db.execute('UPDATE mapping SET narrowing = ? WHERE label = ? AND kind = ?', (json.dumps(narrowing), _norm(label), kind))
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


# ------------------------------------------------------------------------------------------------ granularity (subtype terms)
def category(code):
    """ICD-10-CM 3-character category of a code or prefix ('F11.20' -> 'F11', 'E11.2' -> 'E11')."""
    return re.sub(r'[^0-9A-Za-z]', '', str(code or ''))[:3].upper()


def concept_categories(concept):
    """{category: [the concept's own codes inside it]} for a condition concept, in code order."""
    out = {}
    for c in concepts.CONDITIONS.get(concept, {}).get('icd10', []): out.setdefault(category(c), []).append(c)
    return dict(sorted(out.items()))


def is_own_name(label, concept):
    """The term is the concept's own name or alias (deterministic match to that concept): nothing narrower is meant."""
    if concept not in concepts.CONDITIONS: return True
    if _norm(label) in {_norm(concept), _norm(concepts.CONDITIONS[concept]['label'])}: return True
    found = deterministic(label, 'condition')
    return bool(found and found['concept'] == concept)


def _stem(text):
    t = re.sub(r'\([^)]*\)', ' ', str(text or ''))
    return ' '.join(re.split(r'\s*[,;:]\s*', t, maxsplit=1)[0].split()).strip(' .-')


def _semantic_target(where):
    p = Path(where)
    if p.name == STORE_NAME: p = p.parent
    return p if p.suffix == '.sqlite3' else p / 'semantic.sqlite3'


@functools.lru_cache(maxsize=8)
def _titles(path, stamp):
    out = {}
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        votes = {}  # category -> {description column: {stem.lower(): [count, {variant: count}]}}
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
            cols = {r[1].lower(): r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
            describe_by = [c for c in ('normalized_label', 'label', 'description') if c in cols]
            if 'icd10' not in cols or not describe_by: continue
            for column in describe_by:
                try:
                    rows = db.execute(f"SELECT substr(replace(\"{cols['icd10']}\", '.', ''), 1, 3), \"{cols[column]}\", count(*) FROM \"{table}\" "
                                      f"WHERE \"{cols['icd10']}\" IS NOT NULL AND \"{cols[column]}\" IS NOT NULL GROUP BY 1, 2").fetchall()
                except sqlite3.Error:
                    continue
                for cat, text, n in rows:
                    stem = _stem(text)
                    if not re.fullmatch(r'[A-Za-z][0-9A-Za-z]{2}', cat or '') or len(stem) < 3: continue
                    slot = votes.setdefault(cat.upper(), {}).setdefault(column, {}).setdefault(stem.lower(), [0, {}])
                    slot[0] += n
                    slot[1][stem] = slot[1].get(stem, 0) + n
        for cat, by_column in votes.items():
            for column in ('normalized_label', 'label', 'description'):  # the dictionary's normalized wording first, then what was received
                if column in by_column:
                    _, (_, variants) = min(by_column[column].items(), key=lambda kv: (-kv[1][0], len(kv[0]), kv[0]))
                    title = max(variants.items(), key=lambda kv: (kv[1], kv[0]))[0]
                    out[cat] = (title[:1].upper() + title[1:])[:60]
                    break
    finally:
        db.close()
    return out


def category_titles(where):
    """{ICD-10-CM category: short title} derived from the warehouse's own received descriptions (no code-set download). Every
    table with an icd10 column next to a description column (condition.normalized_label / label, problem_list_entry.label,
    incident_diagnosis.label, ...) votes; a category's title is the most common description stem (text before the first comma,
    parentheticals dropped), the dictionary's normalized wording ahead of the received label. Categories no table describes
    (payer-claim-only codes) have no title. Computed read-only from the semantic layer next to the mapping store and memoized
    per database file version."""
    target = _semantic_target(where)
    try:
        st = target.stat()
        return dict(_titles(str(target), (st.st_size, st.st_mtime_ns)))
    except (OSError, sqlite3.Error):
        return {}


def _granularity_view(n):
    return {k: n.get(k) for k in ('granularity', 'codes', 'categories', 'probability', 'decided_by', 'request_hash', 'titles', 'version',
                                  'reason') if n.get(k) is not None}


def narrow(label, concept, *, titles=None, question=None, as_of=None, ledger=None, allow_live=False, decider=None):
    """Which of the concept's ICD-10-CM categories does the term name? System One typed choice, options labelled
    'F11 — <title from the data>' plus 'all' (the whole group) and 'several' (more than one, then one yes/no per category).
    Accepted only at calibrated p >= NARROW_P; anything else keeps the concept and says so.
    -> {'granularity': 'narrowed' | 'whole' | 'unverified', 'codes' (narrowed only), 'categories', 'probability', 'decided_by',
        'request_hash', 'titles', 'cost_usd', 'calls', 'reason'}"""
    cats = concept_categories(concept)
    titles = {c: (titles or {}).get(c) for c in cats}
    out = {'granularity': 'unverified', 'categories': list(cats), 'titles': {c: t for c, t in titles.items() if t}, 'probability': None,
           'decided_by': None, 'request_hash': None, 'cost_usd': 0.0, 'calls': 0, 'version': VERSION}
    if len(cats) < 2: return {**out, 'granularity': 'whole', 'reason': 'single category'}
    group = concepts.CONDITIONS[concept]['label']
    label_of = lambda c: f'{c} — {titles[c]}' if titles.get(c) else c
    asked = f"The term '{label}'" + (f" in the analyst question: {question}" if question else '')
    evidence = [{'id': 'term', 'text': f"Term as written: '{label}'." + (f' Question: {question}' if question else '')
                 + f" It was matched to the clinical group '{group}'."}]
    if decider is None:
        from .system_one import decide as decider
    from datetime import date
    as_of = str(as_of or date.today())[:10]
    hashes = []

    def ask(prompt, options):
        r = decider(prompt, options, evidence, as_of=as_of, threshold=NARROW_P, task_key='terminology_narrow', ledger=ledger, allow_live=allow_live)
        out['calls'] += sum(1 for s in r.get('steps') or [] if s.get('status') == 'answered' and not s.get('cache_hit'))
        out['cost_usd'] += r.get('cost_usd') or 0.0
        hashes.append(r.get('request_hash'))
        key = r.get('label')
        p = (r.get('calibrated_probabilities') or r.get('probabilities') or {}).get(key)
        ok = bool(r.get('decided_by') and key and key != 'unknown' and p is not None and p >= NARROW_P)
        if ok: out['decided_by'] = out['decided_by'] or r.get('decided_by')
        return (key if ok else None), p, r.get('status')

    options = {c: label_of(c) for c in cats}
    options['all'] = f"All of them: the term means the whole group ({group}), not part of it"
    options['several'] = 'More than one of these categories, but not all of them'
    key, p, status = ask(asked + f" was matched to the clinical group '{group}', which covers these ICD-10-CM categories. Which does the term name? "
                         "Choose the one category it names, 'all' if it names the whole group, or 'several' if it names more than one but not all of them.",
                         options)
    out['request_hash'] = hashes[0]
    if key is None:
        return {**out, 'reason': 'not asked (no live provider and no cached answer)' if status == 'prepared' else 'System One was not confident'}
    if key == 'all':
        return {**out, 'granularity': 'whole', 'probability': p}
    if key in cats:
        return {**out, 'granularity': 'narrowed', 'codes': list(cats[key]), 'categories': [key], 'probability': p}
    if key != 'several':
        return {**out, 'reason': 'unrecognized choice'}
    chosen, lowest = [], p
    for c in cats:  # more than one category: one yes/no each; any unconfident answer keeps the concept
        yes, q, _ = ask(asked + f" was matched to the clinical group '{group}'. Does the term name ICD-10-CM category {label_of(c)}?",
                        {'yes': f'Yes: the term names {label_of(c)}', 'no': f'No: the term does not name {label_of(c)}'})
        if yes is None: return {**out, 'reason': f'System One was not confident about {c}'}
        lowest = min(lowest, q)
        if yes == 'yes': chosen.append(c)
    if not chosen: return {**out, 'reason': 'no category chosen'}
    if len(chosen) == len(cats): return {**out, 'granularity': 'whole', 'probability': lowest}
    return {**out, 'granularity': 'narrowed', 'codes': [x for c in chosen for x in cats[c]], 'categories': chosen, 'probability': lowest}


def _granularity(out, label, *, assess, store=None, question=None, as_of=None, ledger=None, allow_live=False, decider=None):
    """Set out['granularity'] / out['narrowing'] for a mapped condition: concept (the term is the concept's name, or the concept has
    one category), narrowed / whole (saved or just decided), unverified (kept broad; disclosed). assess=False never calls System One.
    -> True when a new decision was made (the caller saves it)."""
    concept = out['concept']
    if len(concept_categories(concept)) < 2 or out.get('method') in OWN_NAME_METHODS or is_own_name(label, concept):
        out['granularity'], out['narrowing'] = 'concept', None
        return False
    n = out.get('narrowing')
    if n and n.get('granularity') in DECIDED:
        out['granularity'] = n['granularity']
        return False
    if not assess:
        out['granularity'], out['narrowing'] = 'unverified', {'granularity': 'unverified', 'reason': 'granularity not assessed'}
        return False
    titles = category_titles(store) if store else {}
    n = narrow(label, concept, titles=titles, question=question, as_of=as_of, ledger=ledger, allow_live=allow_live, decider=decider)
    out['cost_usd'] += n.pop('cost_usd'); out['calls'] += n.pop('calls')
    out['narrowing'], out['granularity'] = _granularity_view(n), n['granularity']
    return n['granularity'] in DECIDED


def granularity_note(entry):
    """The answer's limitation for a term kept at its broader concept (granularity 'unverified'), else None."""
    if entry.get('kind') != 'condition' or entry.get('granularity') != 'unverified' or entry.get('concept') not in concepts.CONDITIONS: return None
    name = entry.get('span') or entry.get('label')
    return (f"'{name}' was read as {concepts.CONDITIONS[entry['concept']]['label']} (ICD-10-CM {', '.join(concept_categories(entry['concept']))}); "
            'whether the question means only some of those categories could not be confirmed, so the count may include conditions it did not name.')


def _codes_of(entry):
    c = entry.get('codes')
    return list(c.get('codes') or []) if isinstance(c, dict) else list(c or [])


def stale(store, entries):
    """True when a saved plan's terminology entries no longer match the saved mappings (a granularity was decided since)."""
    for e in entries or []:
        if e.get('kind') != 'condition': continue
        name = e.get('span') or e.get('label')
        now = {x['span']: x for x in grammar_terms(name or '', store)['used']}.get(name)
        if now and now.get('granularity') != e.get('granularity') or now and _codes_of(now) != _codes_of(e):
            return True
    return False


def map_label(label, kind=None, *, store=None, question=None, as_of=None, ledger=None, allow_live=False, adjudicate_with=True,
              encoder=None, decider=None, save_accepted=True):
    """One messy label -> mapping. method: saved_* | lay_synonym | dictionary | alias | brand | ingredient | class |
    system_one | abstained | no_candidates. concept None unless decided."""
    out = {'label': label, 'kind': kind, 'concept': None, 'classes': None, 'codes': None, 'method': None, 'candidates': [],
           'cost_usd': 0.0, 'calls': 0, 'version': VERSION}
    hit = saved(store, label) if store else None
    ask = dict(assess=adjudicate_with, store=store, question=question, as_of=as_of, ledger=ledger, allow_live=allow_live, decider=decider)
    if hit and (kind is None or hit['kind'] == kind):
        out.update(hit)
        if out['kind'] == 'condition' and out['concept'] and _granularity(out, label, **ask) and save_accepted:
            save_narrowing(store, label, 'condition', out['narrowing'])  # a mapping saved before granularity existed gets its decision
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
            if out['kind'] == 'condition': _granularity(out, label, **ask)
            if store and save_accepted: save(store, label, out, question)
    if out['kind'] == 'condition' and out['concept'] and 'granularity' not in out:  # a deterministic match: the term is the concept's name
        _granularity(out, label, **ask)
    out['codes'] = codes(out['kind'], out['concept'])
    if out.get('granularity') == 'narrowed':
        out['codes'] = {**out['codes'], 'codes': list(out['narrowing']['codes']), 'concept_codes': out['codes']['codes']}
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
        entry = {'span': span, 'kind': found['kind'], 'concept': found['concept'], 'classes': found.get('classes'),
                 'method': found['method'], 'codes': codes(found['kind'], found['concept'])}
        if found['kind'] == 'condition':  # granularity: free here (saved decision or disclosure), the System One call is narrow()
            probe = {**found, 'narrowing': found.get('narrowing')}
            _granularity(probe, text, assess=False)
            entry['granularity'] = probe['granularity']
            if probe['granularity'] == 'narrowed':
                value = concepts.scoped_name(found['concept'], probe['narrowing']['codes'])
                entry['codes'] = {**entry['codes'], 'codes': list(probe['narrowing']['codes']), 'concept_codes': entry['codes']['codes']}
            if probe['narrowing']: entry['narrowing'] = probe['narrowing']
        out[found['kind']][span] = value
        out['used'].append(entry)
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
    store = store_path(Path(catalog.target).parent)
    live = bool(getattr(stack, 'allow_live', False))
    as_of = None
    try: as_of = stack.retriever.meta['snapshot']['as_of']
    except (AttributeError, KeyError, TypeError): pass
    receipts, spent, calls = [], 0.0, 0
    try:
        interpret(catalog, question, population=population)
        tail = None  # the grammar already reads it (its exact tier answers first)
    except QuestionNotUnderstood as exc:
        tail = unconsumed(str(exc))
        if not tail: return None
    if tail is None:
        # Read through saved mappings. The one thing left to do: a term kept at its broader concept because its granularity was never
        # assessed (a mapping saved before narrowing existed) is narrowed now; decided -> recompile, else the grammar's reading stands.
        for e in grammar_terms(question, store)['used']:
            if e['kind'] != 'condition' or e.get('granularity') != 'unverified' or (e.get('narrowing') or {}).get('reason') != 'granularity not assessed':
                continue
            r = map_label(e['span'], 'condition', store=store, question=question, as_of=as_of, ledger=ledger, allow_live=live, decider=decider)
            spent += r['cost_usd']; calls += r['calls']
            receipts.append({k: r.get(k) for k in RECEIPT_KEYS})
        if not any(r.get('granularity') in DECIDED for r in receipts): return None
        contract, interpretation = interpret(catalog, question, population=population)
        return contract, {**interpretation, 'interpreter': 'terminology_mapping', 'terminology': receipts,
                          'terminology_usage': {'new_paid_api_calls': calls, 'known_api_estimate_usd': round(spent, 6)},
                          'review': 'terminology_granularity_then_fully_consumed_grammar'}
    for span in candidate_spans(tail):
        if len([r for r in receipts if r.get('system_one')]) >= MAX_ADJUDICATIONS: break
        r = map_label(span, store=store, question=question, as_of=as_of, ledger=ledger, allow_live=live, decider=decider)
        spent += r['cost_usd']; calls += r['calls']
        receipts.append({k: r.get(k) for k in RECEIPT_KEYS})
        if r['concept']: break
    usage = {'new_paid_api_calls': calls, 'known_api_estimate_usd': round(spent, 6)}
    if not any(r['concept'] for r in receipts):
        err = QuestionNotUnderstood(f"No terminology mapping for '{tail}': " + '; '.join(f"{r['label']}: {r['method']}" for r in receipts))
        err.terminology_usage = usage
        raise err
    contract, interpretation = interpret(catalog, question, population=population)
    return contract, {**interpretation, 'interpreter': 'terminology_mapping', 'terminology': receipts, 'terminology_usage': usage,
                      'review': 'terminology_mapping_then_fully_consumed_grammar'}
