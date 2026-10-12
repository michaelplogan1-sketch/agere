"""Factorized clinical text reader: four axes, exact sources, explicit abstention.

This learns text annotations, not clinical eligibility. Receipt hashes bind the
runtime to its training weights. Missing evidence never implies missing care.
"""
from pathlib import Path
from collections import Counter
import hashlib
import json
import re
import sqlite3

from ...contracts import digest
from ..narrative_sources import text_fields
from .facts import TEXT_CONCEPTS, DRUGS
from .relation_learning import RELATIONS, source_window, feature

AXES = {
    'assertion': {
        'affirmed': 'The targeted statement is asserted. Historical statements and statements about other people can still be affirmed.',
        'negated': 'The targeted statement is explicitly denied; cannot afford affirms a barrier, while difficulty reported false negates it.',
        'uncertain': 'The targeted proposition is conditional, possible, conflicting or cannot be resolved.',
        'retracted': 'The targeted statement is explicitly withdrawn or corrected as entered in error.',
        'not_applicable': 'Target is a signature, heading, administrative instruction or other non-clinical text, not an assertion about care.'},
    'subject': {
        'patient': 'Target refers to this patient. Normal clinical prose assumes the chart patient unless context indicates otherwise.',
        'other_person': 'Target refers to a relative or another person, not this patient.',
        'nonclinical': 'Target is a signature/heading/instruction, not a clinical subject.',
        'unknown': 'Excerpt explicitly leaves who the target concerns unresolved.'},
    'time': {
        'current': 'Explicitly current/recent account at the time of the source; not necessarily current today.',
        'historical': 'Explicitly prior-only or copied from a past account, without current reconfirmation.',
        'planned': 'Targeted event is prospective, proposed, scheduled or conditional in the future.',
        'unspecified': 'No explicit usable time for the targeted occurrence, including lists without a time qualifier and metadata.',
        'unknown': 'Conflicting or explicitly unknown chronology.'},
    'relation': {**RELATIONS,
        'clinical_observation': 'A diagnosis, symptom, measurement, preference or social/financial observation; assertion/subject/time are separate.',
        'document_metadata': 'Signature, author specialty, heading or administrative text; not evidence that care occurred.'},
}
VERSION = 'factorized-evidence-reader/2'


def validate_input(x):
    if not isinstance(x, dict) or not isinstance(x.get('text'), str) or not isinstance(x.get('concept'), str):
        raise ValueError('Invalid reader input')
    a, b = x.get('target_start'), x.get('target_end')
    if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(x['text']):
        raise ValueError('Invalid target offsets')
    if x['text'][a:b] != x.get('target_quote'):
        raise ValueError('Target is not source bound')
    if len(x['text']) > 6000:
        raise ValueError('Reader requires bounded explicit windows')
    return x


def input_feature(x):
    validate_input(x)
    return feature({'input': x})


def interpretation(axes):
    """Explain proposal constraints without converting confidence into authority."""
    labels = {k: v['label'] for k, v in axes.items()}
    limitations = ['Machine annotation requires review; no clinical action authorized']
    if labels['subject'] != 'patient':
        limitations.append('Does not establish a fact about this patient')
    if labels['time'] != 'current':
        limitations.append('Does not establish current clinical state')
    if labels['assertion'] != 'affirmed':
        limitations.append('Does not affirm the target proposition')
    if labels['relation'] in {'prescription_order', 'dispense_record', 'listed_mention'}:
        limitations.append('Does not establish that the patient takes the medication')
    if labels['relation'] in {'record_request', 'care_order_plan'}:
        limitations.append('Does not establish that the visit or service occurred')
    if labels['relation'] == 'source_coverage':
        limitations.append('Missing documentation does not establish missing care')
    if labels['relation'] == 'document_metadata':
        limitations.append('Document metadata is not a clinical care event')
    return limitations


class EvidenceReader:
    def __init__(self, model_dir, cache_path):
        import joblib
        self.path = Path(model_dir)
        self.card = json.loads((self.path/'model-card.json').read_text())
        if self.card['schema'] != VERSION or self.card['decision_authority'] is not False:
            raise ValueError('Unexpected reader model contract')
        self.models = {}
        for axis in AXES:
            path = self.path/f'{axis}-lexical.joblib'
            if hashlib.sha256(path.read_bytes()).hexdigest() != self.card['weight_hashes'][path.name]:
                raise ValueError('Model weights changed')
            self.models[axis] = joblib.load(path)
        self.identity = digest({'card': self.card, 'runtime': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
        self.db = sqlite3.connect(cache_path)
        self.db.execute('CREATE TABLE IF NOT EXISTS reader_predictions (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        self.stats = Counter()

    def close(self):
        self.db.close()

    def predict(self, inputs):
        keys = [digest([self.identity, validate_input(x)]) for x in inputs]
        found, missing = {}, {}
        for key, x in zip(keys, inputs):
            if key in found or key in missing:
                self.stats['batch_reuses'] += 1
                continue
            cached = self.db.execute('SELECT payload FROM reader_predictions WHERE id=?', (key,)).fetchone()
            if cached:
                found[key] = json.loads(cached[0]); self.stats['persistent_cache_hits'] += 1
            else:
                missing[key] = x
        if missing:
            texts = [input_feature(x) for x in missing.values()]
            per_axis = {}
            for axis, model in self.models.items():
                probs = model.predict_proba(texts)
                per_axis[axis] = [{'label': str(model.classes_[p.argmax()]),
                    'probabilities': dict(zip(model.classes_, map(float, p))), 'clinically_calibrated': False} for p in probs]
            for i, key in enumerate(missing):
                axes = {a: per_axis[a][i] for a in AXES}
                found[key] = {'axes': axes, 'decision': 'abstain', 'decision_authority': False,
                    'care_gap_established': False, 'model_identity': self.identity,
                    'limitations': interpretation(axes)}
            with self.db:
                self.db.executemany('INSERT INTO reader_predictions VALUES (?,?)', [(k, json.dumps(found[k])) for k in missing])
            self.stats['new_unique_predictions'] += len(missing)
        self.stats['bindings'] += len(inputs)
        return [found[k] for k in keys]


PATTERNS = {
    **TEXT_CONCEPTS,
    **{'medication:'+k: r'\b(?:'+v+r')\b' for k, v in DRUGS.items()},
    'financial_hardship': r'\b(?:difficulty paying medical bills|cannot afford|unable to afford|cost barrier)\b',
    'outside_care': r'\b(?:outside records|outside laboratory|outside medication records|records are incomplete)\b',
    'goals_of_care': r'\b(?:palliative|hospice|goals of care)\b',
    'ef': r'\b(?:EF|ejection fraction)\s*(?:of\s*)?\d+(?:\.\d+)?\s*%',
}


def candidates(patient_id, records, as_of):
    """Scan mapped text directly, including occurrences omitted by prior fact rules.

    Candidate vocabulary is explicit and incomplete, never described as universal
    clinical NER. No hidden original/clean document text may be accessed.
    """
    for r in records:
        if r.get('synthetic') is not True or r['patient_id'] != patient_id:
            raise ValueError('Synthetic identity-bound source required')
        if (r['event_date'] is not None and r['event_date'] > as_of) or r['available_date'] > as_of or r.get('identity_status') != 'matched':
            raise ValueError('Reader requires visible matched records')
        for part in text_fields(r):
            text = part['text']
            for concept, pattern in PATTERNS.items():
                for match in re.finditer(pattern, text, re.I):
                    # Preserve complete local line/clause plus surrounding text.
                    a, b = match.span()
                    ca = max(text.rfind('\n', 0, a), text.rfind(';', 0, a))+1
                    ends = [i for i in (text.find('\n', b), text.find(';', b)) if i >= 0]
                    cb = min(ends) if ends else len(text)
                    ca, cb = max(ca, a-200), min(cb, b+200)
                    source = {k: r[k] for k in ('record_id', 'source_table', 'event_date', 'available_date')}
                    source.update(field=part['field'], start=a, end=b, quote=match[0],
                                  context_start=ca, context_end=cb, context=text[ca:cb])
                    window = source_window(text, source)
                    x = {'concept': concept.replace(':', ' ').replace('_', ' '), 'target_quote': match[0],
                         **{k: window[k] for k in ('text', 'target_start', 'target_end', 'prefix_omitted', 'suffix_omitted')}}
                    yield {'patient_id': patient_id, 'synthetic': True, 'input': validate_input(x),
                           'source': source, 'window_start': window['window_start'], 'window_end': window['window_end']}
