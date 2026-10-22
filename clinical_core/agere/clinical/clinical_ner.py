"""Local clinical NER (OpenMed token-classification, DeBERTa-v3 184M disease + 141M chemical) feeding note_facts.

Zero network, CPU, small models (models/openmed-ner-*). The NER finds DISEASE / CHEM spans; this module maps them onto the
closed note-fact vocabulary and returns candidate facts in the same dict shape as note_facts.extract():
  * symptom facts: a DISEASE span whose normalised text is in SYMPTOM_LEXICON (richer synonyms than the rule regexes), with
    negation (NegEx-style window), hypothetical (note_facts.HYPOTHETICAL) and historical cues handled by the SAME rules the
    extractor uses;
  * medication qualifiers: a CHEM span in the sentence of a medication_* fact that has no qualifier fills it (lowercase first word).
Nothing NER-derived enters the note_fact table on faith: a fact type is used only if clinical_ner_admission.json lists it, and that file
is written by tools/notes_extraction/ner_evaluate.py from the measured precision of NER-added facts against the Sonnet labels
(precision >= the extractor's bar with a Wilson lower bound and a recall gain). AGERE_NOTE_NER=off disables.
"""
import json
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MODELS = {'disease': ROOT / 'models' / 'openmed-ner-disease-184m', 'chem': ROOT / 'models' / 'openmed-ner-pharma-141m'}
ADMISSION = HERE / 'clinical_ner_admission.json'
VERSION = 'clinical-ner/1'
MIN_SCORE = 0.80
SYMPTOM_LEXICON = {
    'chest_pain': ('chest pain', 'chest discomfort', 'chest tightness', 'angina', 'chest pressure'),
    'syncope': ('syncope', 'fainting', 'passed out', 'blackout', 'loss of consciousness', 'presyncope'),
    'palpitations': ('palpitations', 'palpitation', 'racing heart', 'heart racing', 'irregular heartbeat'),
    'bleeding': ('bleeding', 'hemorrhage', 'haemorrhage', 'melena', 'hematuria', 'hematochezia', 'epistaxis', 'blood in stool'),
    'cough': ('cough', 'coughing'),
    'fever': ('fever', 'fevers', 'febrile', 'chills'),
    'nausea_vomiting': ('nausea', 'vomiting', 'emesis', 'nausea and vomiting', 'nauseated'),
    'diarrhea': ('diarrhea', 'diarrhoea', 'loose stools'),
    'constipation': ('constipation',),
    'dizziness': ('dizziness', 'lightheadedness', 'light-headedness', 'vertigo', 'dizzy', 'lightheaded'),
    'wheeze': ('wheeze', 'wheezing'),
    'fatigue': ('fatigue', 'tiredness', 'exhaustion', 'malaise', 'lethargy'),
    'pain': ('pain', 'back pain', 'abdominal pain', 'joint pain', 'headache', 'leg pain', 'knee pain', 'hip pain'),
    'reduced_oral_intake': ('poor appetite', 'decreased appetite', 'reduced appetite', 'anorexia', 'poor oral intake'),
    'muscle_symptoms': ('myalgia', 'muscle pain', 'muscle aches', 'muscle weakness', 'cramps', 'muscle cramps'),
}
_LEX = {phrase: val for val, ps in SYMPTOM_LEXICON.items() for phrase in ps}
_NEG = re.compile(r"\b(?:no|denies|denied|without|negative for|not|never|absence of|free of|nor)\b[^.;]{0,40}$", re.I)
_CACHE = {}
_PIPES = {}


def enabled():
    return os.environ.get('AGERE_NOTE_NER', 'on') != 'off'


def admitted():
    """Fact types validated against labels (clinical_ner_admission.json); empty until the evaluator has admitted any."""
    try:
        return set(json.loads(ADMISSION.read_text()).get('admitted', []))
    except (OSError, ValueError):
        return set()


def _pipe(kind):
    if kind not in _PIPES:
        from transformers import pipeline
        _PIPES[kind] = pipeline('token-classification', model=str(MODELS[kind]), aggregation_strategy='simple', device='cpu')
    return _PIPES[kind]


def entities(text, kinds=('disease', 'chem')):
    """-> [{'kind', 'text', 'start', 'end', 'score'}] spans into `text` (cached per text)."""
    key = (text, kinds)
    if key in _CACHE: return _CACHE[key]
    out = []
    for kind in kinds:
        if not MODELS[kind].exists(): continue
        for e in _pipe(kind)(text):
            if float(e['score']) < MIN_SCORE: continue
            a, b = int(e['start']), int(e['end'])
            while a > 0 and text[a - 1].isalnum(): a -= 1          # sub-word pieces ('mide') expand to the whole word ('furosemide')
            while b < len(text) and text[b].isalnum(): b += 1
            out.append({'kind': kind, 'text': text[a:b], 'start': a, 'end': b, 'score': round(float(e['score']), 3)})
    if len(_CACHE) > 20000: _CACHE.clear()
    _CACHE[key] = out
    return out


def normal(span):
    return re.sub(r'[^a-z\- ]', '', span.lower()).strip()


def candidates(text, stream=None, field=None, ents=None):
    """NER-derived facts (note_facts shape) for one text unit, before any admission filter. Pure of the rule extractor."""
    from . import note_facts as nf
    ents = entities(text) if ents is None else ents
    facts = []
    for s0, s1, sent, section, major in nf._sentences(text, majors=True):
        hypo = bool(nf.HYPOTHETICAL.search(sent))
        hist = bool(nf.HISTORICAL.search(sent)) or section in ('history on file', 'prior evidence')
        if hypo: continue
        if stream in nf.IMAGING_STREAMS or major in ('allergies', 'medications', 'current medications', 'discharge medications') \
                or re.match(r'\s*allerg', sent, re.I):
            continue
        for e in ents:
            if e['kind'] != 'disease' or not (s0 <= e['start'] < s1): continue
            val = _LEX.get(normal(e['text']))
            if val is None: continue
            idx = e['start'] - s0
            if re.search(r'(?:chronic|history of|intractable)[a-z ]{0,12}$', sent[:idx], re.I): continue
            neg = bool(_NEG.search(sent[max(0, idx - 60):idx])) or nf._negated_before(sent, idx)
            f = nf._fact('symptom', val, e['start'], e['end'], text, negated=neg, confidence=min(0.75, e['score']),
                         temporality='historical' if hist else 'current')
            f.update(section=major, label=section, context=text[s0:s1].strip(), source='ner')
            facts.append(f)
    return facts


def qualifiers(text, facts, ents=None):
    """Fill missing medication qualifiers from CHEM spans in the same sentence -> number filled."""
    n = 0
    todo = [f for f in facts if f['fact_type'].startswith('medication') and not f.get('qualifier')]
    if not todo: return 0
    chems = [e for e in (entities(text) if ents is None else ents) if e['kind'] == 'chem']
    for f in todo:
        near = [e for e in chems if abs(e['start'] - f['span_start']) <= 120 and re.search(r'[a-z]{4,}', e['text'], re.I)]
        if near:
            best = min(near, key=lambda e: abs(e['start'] - f['span_start']))
            f['qualifier'] = re.findall(r'[a-z]+', best['text'].lower())[0]; f['qualifier_source'] = 'ner'; n += 1
    return n


def augment(text, facts, stream=None, field=None, allowed=None):
    """Rule facts + admitted NER facts (non-duplicates). `allowed` overrides the admission file (evaluation uses it)."""
    allowed = admitted() if allowed is None else set(allowed)
    if not enabled() or not allowed or not text or len(text) < 8: return facts
    need_q = 'medication_qualifier' in allowed and any(f['fact_type'].startswith('medication') and not f.get('qualifier') for f in facts)
    if 'symptom' not in allowed and not need_q: return facts   # NER only runs where an admitted use needs it
    ents = entities(text)
    out = list(facts)
    if 'symptom' in allowed:
        have = {(f['fact_type'], f['value'], f['negated']) for f in facts}
        for f in candidates(text, stream, field, ents):
            k = (f['fact_type'], f['value'], f['negated'])
            if k not in have: have.add(k); out.append(f)
    if need_q: qualifiers(text, out, ents)
    return out
