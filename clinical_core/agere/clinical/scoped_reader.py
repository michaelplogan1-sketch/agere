"""Target-local representations with retained section/context evidence.

Scope boundaries are a deterministic approximation, not syntactic entailment.
The full input remains available and outputs cannot authorize clinical actions.
"""
from pathlib import Path
from collections import Counter
import hashlib
import json
import re
import sqlite3

from sklearn.base import BaseEstimator, TransformerMixin
from ...contracts import digest
from .evidence_reader import AXES, validate_input, interpretation

VERSION = 'scoped-evidence-reader/3'
BOUNDARY = re.compile(r'\n+|;|\s+/\s+|[.!?](?=\s|$)')
ABBREVIATIONS = {'dr', 'mr', 'mrs', 'ms', 'vs', 'approx', 'pt', 'e.g', 'i.e'}


def scoped_view(x):
    validate_input(x)
    text = x['text']; a, b = x['target_start'], x['target_end']
    spans = []
    for m in BOUNDARY.finditer(text):
        if m.start() < b and m.end() > a:
            continue  # Never split the supplied target occurrence.
        if m[0] == '.':
            token = re.search(r'([\w.]+)$', text[:m.start()])
            if token and token[1].lower() in ABBREVIATIONS:
                continue
        spans.append(m.span())
    lo = max((end for start, end in spans if end <= a), default=0)
    hi = min((start for start, end in spans if start >= b), default=len(text))
    base_hi = hi
    # Keep a narrowly signaled trailing qualifier. Do not attach an arbitrary
    # neighboring finding just because it shares the patient or note.
    next_start = next((end for start, end in spans if start == hi), len(text))
    next_end = min((start for start, end in spans if start >= next_start), default=len(text))
    following = text[next_start:next_end].strip()
    medication = re.search(r'\b(?:medication|drug|metoprolol|carvedilol|bisoprolol|losartan|insulin|tiotropium|apixaban|amoxicillin|aspirin|methotrexate)\b', x['concept']+' '+x['target_quote'], re.I)
    linked = re.match(r'(?i)^(?:it\b|this\s+(?:was|is|has|had|remains|medication|finding|diagnosis|condition)\b)', following)
    med_linked = medication and re.match(r'(?i)^(?:current use|use|taking|adherence|not taking|not confirmed|unconfirmed|current taking)\b', following)
    if following and (linked or med_linked):
        hi = next_end
    while lo < a and text[lo].isspace():lo += 1
    while hi > b and text[hi-1].isspace():hi -= 1
    focus = text[lo:hi]
    prefix = text[:lo]
    headings = []
    for m in re.finditer(r'(?m)^\s*([^\n:]{1,90}):\s*$', prefix):
        headings.append({'start': m.start(1), 'end': m.end(1), 'text': m[1]})
    # Inline copy-forward headers are common in flattened note exports.
    for m in re.finditer(r'(?i)\b(?:prior note|copied (?:from|note|history)|previous encounter|family history)[^\n:]{0,65}:', prefix):
        headings.append({'start': m.start(), 'end': m.end(), 'text': m[0]})
    headings = sorted({(h['start'], h['end']): h for h in headings}.values(), key=lambda h: h['start'])[-3:]
    section = ' '.join(h['text'] for h in headings) or 'no explicit section'
    left, right = text[lo:a], text[b:hi]
    flags = {
        'prefix_omitted': float(bool(x.get('prefix_omitted'))),
        'suffix_omitted': float(bool(x.get('suffix_omitted'))),
        'attached_qualifier': float(hi > base_hi),
        'target_in_short_heading': float(len(focus) < 100 and focus.rstrip().endswith(':')),
        'signature_format': float(bool(re.search(r'\b(?:MD|DO|NP|RN|PharmD)\s*\|', focus))),
    }
    return {'focus': left+' TARGET_ENTITY '+right,
            'target': x['concept']+' '+x['target_quote'],
            'section': section, 'context': text,
            'left': left or 'no left words', 'right': right or 'no right words',
            'flags': flags,
            'trace': {'focus_start': lo, 'focus_end': hi, 'focus_text': focus,
                      'heading_spans': headings, 'trailing_qualifier_attached': hi > base_hi,
                      'scope_verified': False, 'limitation': 'Heuristic scope; cross-sentence/coreference and omitted headers may remain unresolved.'}}


class MakeViews(BaseEstimator, TransformerMixin):
    def fit(self, x, y=None):return self
    def transform(self, x):return [scoped_view(row) for row in x]


class SelectView(BaseEstimator, TransformerMixin):
    def __init__(self, view):self.view = view
    def fit(self, x, y=None):return self
    def transform(self, x):return [row[self.view] for row in x]


def pipeline():
    from sklearn.pipeline import Pipeline, FeatureUnion
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.linear_model import LogisticRegression
    views = []
    for view in ('focus', 'target', 'section', 'context', 'left', 'right'):
        views.append((view, Pipeline([('select', SelectView(view)), ('tfidf', TfidfVectorizer(
            analyzer='char_wb', ngram_range=(3, 5), max_features=25000, sublinear_tf=True))])))
    views.append(('flags', Pipeline([('select', SelectView('flags')), ('vectorizer', DictVectorizer())])))
    return Pipeline([('views', MakeViews()), ('features', FeatureUnion(views, transformer_weights={
        'focus': 2., 'target': .5, 'section': 1., 'context': .2, 'left': .75, 'right': .75, 'flags': .5})),
        ('classifier', LogisticRegression(C=4., class_weight='balanced', max_iter=2000, random_state=812))])


def noise_variants(x):
    """Train-only unrelated topic additions. Target offsets remain exact."""
    validate_input(x)
    result = [dict(x)]
    for prefix, suffix in [
        ('Unrelated ear complaint: the patient denies tinnitus.\n\n', ''),
        ('Separate administrative item: no prior fax number was recorded.\n\n', '\n\nUnrelated hearing screen: no difficulty was reported today.')]:
        value = {**x, 'text': prefix+x['text']+suffix,
                 'target_start': x['target_start']+len(prefix), 'target_end': x['target_end']+len(prefix)}
        result.append(validate_input(value))
    return result


def consistency_issues(axes):
    labels = {a: v['label'] for a, v in axes.items()}
    metadata = labels['relation'] == 'document_metadata'
    na = labels['assertion'] == 'not_applicable'
    nonclinical = labels['subject'] == 'nonclinical'
    return ['Independent heads disagree about whether this is clinical content or document metadata'] if len({metadata, na, nonclinical}) > 1 else []


class ScopedReader:
    def __init__(self, model_dir, cache_path):
        import joblib
        self.path = Path(model_dir); self.card = json.loads((self.path/'model-card.json').read_text())
        if self.card['schema'] != VERSION or self.card['decision_authority'] is not False:
            raise ValueError('Unexpected scoped-reader contract')
        runtime_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        if runtime_hash != self.card['runtime_hash']:
            raise ValueError('Scoped representation changed since training')
        self.models = {}; selected = {}
        for axis in AXES:
            name = f'{axis}-scoped_augmented.joblib'; path = self.path/name
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            if checksum != self.card['weight_hashes'][name]:raise ValueError('Changed scoped weights')
            self.models[axis] = joblib.load(path); selected[name] = checksum
        self.identity = digest({'schema': VERSION, 'runtime_hash': runtime_hash, 'weights': selected, 'axes': AXES})
        self.db = sqlite3.connect(cache_path)
        self.db.execute('CREATE TABLE IF NOT EXISTS predictions (id TEXT PRIMARY KEY,payload TEXT)')
        self.stats = Counter()

    def close(self):self.db.close()

    def predict(self, inputs):
        keys = [digest([self.identity, validate_input(x)]) for x in inputs]
        found, pending = {}, {}
        for k, x in zip(keys, inputs):
            if k in found or k in pending:self.stats['batch_reuses'] += 1;continue
            old = self.db.execute('SELECT payload FROM predictions WHERE id=?', (k,)).fetchone()
            if old:found[k] = json.loads(old[0]); self.stats['persistent_cache_hits'] += 1
            else:pending[k] = x
        if pending:
            rows = list(pending.values()); predictions = {}
            for a, model in self.models.items():
                probs = model.predict_proba(rows)
                predictions[a] = [{'label': str(model.classes_[p.argmax()]), 'probabilities': dict(zip(model.classes_, map(float, p))),
                                   'clinically_calibrated': False} for p in probs]
            with self.db:
                for i, (k, x) in enumerate(pending.items()):
                    axes = {a: predictions[a][i] for a in AXES}
                    issues = consistency_issues(axes)
                    found[k] = {'axes': axes, 'decision': 'abstain', 'decision_authority': False,
                        'care_gap_established': False, 'model_identity': self.identity, 'scope': scoped_view(x)['trace'],
                        'consistency_issues': issues, 'limitations': interpretation(axes)+issues}
                    self.db.execute('INSERT INTO predictions VALUES (?,?)', (k, json.dumps(found[k])))
            self.stats['new_unique_predictions'] += len(pending)
        self.stats['bindings'] += len(inputs)
        return [found[k] for k in keys]
