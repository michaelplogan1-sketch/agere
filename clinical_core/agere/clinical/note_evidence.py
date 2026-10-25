"""Candidate passages for a note predicate: sentence/paragraph chunks of one patient's clinical text, by concept and window.

Text sources (all read-only from the semantic layer, as of the stack's cutoff):
  clinical_note          record.payload $.text of stream `notes` (note type from clinical_note)
  hie_document           stream `hie_documents` (document type from hie_document)
  scanned_document_text  scanned_document_text.text (PDF text layer or front-office OCR; OCR garbles are kept as written)
  other_text             every other clinical text stream (telephone messages, advance-care documents, imaging and pathology
                         reports, imported documents, referral updates, ...), never billing documents

A passage is a matched sentence plus one sentence of context on each side (merged when they overlap), at most MAX_CHARS long,
with record_id, note_type, date, stream and the character span in the full text. Retrieval is deterministic: terms (concept
words, library synonyms, drug-class members, a topic lexicon) -> regex; passages ranked by distinct terms matched, then date
(newest first), record_id, span; bounded per patient (max_passages) and per record. Identical text on the same date (a copied-
forward or re-imported document) is kept once and lists the other record ids in `duplicates`. note_fact (the rules extraction)
is NOT used: it is not truth and would hide the same misses the readers must see.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

VERSION = 'note-evidence/1'
MAX_PASSAGES = 12
PER_RECORD = 3
MAX_CHARS = 700
NOTE_STREAMS = ('notes',)
HIE_STREAMS = ('hie_documents',)
BILLING = {'billing_documents'}
OTHER_STREAMS = (
    'advance_care_documents', 'appointment_scheduling_contacts', 'behavioral_care_contacts', 'care_management_contacts',
    'chart_summaries', 'community_service_outcomes', 'diagnostic_access_contacts', 'diagnostic_test_reports', 'endoscopy_reports',
    'home_health_case_summaries', 'home_health_plan_responses', 'home_health_referral_packets', 'home_medication_questions',
    'imaging_reports', 'imported_documents', 'inpatient_cardiac_reports', 'laboratory_followup_contacts',
    'medication_clarification_contacts', 'outside_followup_contacts', 'pathology_reports', 'pharmacy_information_responses',
    'pharmacy_prescriber_responses', 'prescription_request_responses', 'procedure_access_contacts',
    'procedure_preparation_contacts', 'procedure_result_communications', 'received_diagnostic_reports',
    'received_hospital_summaries', 'received_pharmacy_information', 'received_prescription_requests',
    'received_procedure_reports', 'referral_status_updates', 'regimen_followup_contacts', 'school_function_reports',
    'screening_workup_contacts', 'specialty_record_reviews', 'telephone_messages', 'transition_of_care_contacts',
    'transported_handoffs', 'youth_preventive_contacts')

# Topic lexicon: concept words -> extra regexes. Keys are matched against the concept term (word-boundary, lower case).
SYNONYMS = {
    r'falls?|fell': [r'\bfalls?\b', r'\bfell\b', r'\bfallen\b', r'\bfalling\b'],
    r'goals? of care': [r'goals? of care', r'comfort[- ]focused', r'comfort measures', r'\bhospice\b', r'\bpalliative\b'],
    r'code status|dnr|resuscitat|cpr|polst|molst|advance directive|living will': [
        r'\bdnr\b', r'\bdni\b', r'\bcpr\b', r'code status', r'full code', r'resuscitat', r'chest compressions', r'breathing tube',
        r'intubat', r'\bpolst\b', r'\bmolst\b', r'advance directive', r'living will', r'health ?care proxy', r'power of attorney'],
    r'memory|cogniti|dementia': [r'memory', r'cognit', r'dementia', r'forget', r'confus'],
    r'transport': [r'transport', r'getting to appointments', r'ride to', r'no car', r'bus pass'],
    r'food|hunger|grocer': [r'food', r'grocer', r'hungry', r'\bmeals?\b', r'ran out of food'],
    r'housing|homeless|eviction': [r'housing', r'homeless', r'evict', r'shelter', r'rent\b', r'utilit'],
    r'financ|afford|cost': [r'financ', r'afford', r'\bcost', r'copay', r'cannot pay', r'expensive'],
    r'smok|tobacco|cigarette': [r'smok', r'tobacco', r'cigarette', r'\bpacks?\b/day', r'vap(?:e|ing)', r'nicotine'],
    r'alcohol|drinking': [r'alcohol', r'drinks? per', r'\bdrinking\b', r'\betoh\b'],
    r'allerg|intoleran': [r'allerg', r'intoleran', r'\bhives\b', r'angioedema', r'anaphyla', r'rash\b'],
    r'side effect|adverse': [r'side effect', r'adverse', r'intoleran', r'\bcough\b', r'myalgia', r'hyperkal'],
    r'discharge|disposition': [r'discharg', r'disposition', r'\bhome\b today', r'transferred to', r'skilled nursing'],
    r'chest x-?ray|cxr': [r'chest x-?ray', r'\bcxr\b', r'chest radiograph', r'chest film', r'\bx-?ray\b', r'\bradiograph\w*', r'exam:\s*x-?ray'],
    r'stress test': [r'stress test', r'treadmill', r'stress echo', r'inducible ischemia'],
    r'follow[- ]?up': [r'follow[- ]?up', r'return (?:in|for|to)', r'recheck', r'see (?:the )?patient'],
    r'declin|refus': [r'declin', r'refus', r'does not want', r'not interested', r'would not'],
    r'depress|mood|phq': [r'depress', r'\bmood\b', r'\bphq', r'sad\b', r'anhedonia'],
    r'pain': [r'\bpain', r'\bache', r'\bsore\b'],
    r'shortness of breath|dyspnea|sob': [r'shortness of breath', r'dyspnea', r'\bsob\b', r'breathless', r'winded'],
    r'start|initiat|begin|new medication': [r'\bstart(?:ed|ing)?\b', r'\binitiat\w*', r'\bbegin\b', r'\bbegan\b', r'\bnew prescription'],
    r'stop|discontinu|held|hold': [r'\bstop(?:ped)?\b', r'\bdiscontinu\w*', r'\bheld\b', r'\bon hold\b', r'\bhold\b'],
    r'edema|swelling': [r'edema', r'swelling', r'swollen'],
    # wording real analysts and clinicians use for the same things (a class named by its kind, a measure by its name, who prompted a visit)
    r'inflammator|nsaid': [r'anti-?inflammator\w*', r'\bnsaids?\b', r'meloxicam', r'ibuprofen', r'naproxen', r'diclofenac', r'celecoxib', r'nabumetone',
                           r'etodolac', r'indomethacin', r'ketorolac', r'\bmotrin\b', r'\baleve\b'],
    r'^medications?$|^medicines?$': [r'\bstart(?:ed|ing)?\b', r'\brecommend\w*', r'\bprescri\w+', r'\b\d+(?:\.\d+)?\s?(?:mg|mcg|units?)\b', r'\bsuggest\w*'],
    r'a1c|glycated|glycohemoglobin': [r'a1c', r'hemoglobin a1c', r'glycated h?emoglobin', r'\bhba'],
    r'nodule|lesion|\bmass\b': [r'nodul\w*', r'lesion', r'\bmass\b', r'opacity'],
    r'^urged$': [r'\bdaughter\b', r'\bson\b', r'\bwife\b', r'\bhusband\b', r'\bspouse\b', r'\bfamily\b', r'\bneighbou?r\b', r'\bfriend\b',
                 r'thinks? (?:i|he|she|they) should', r'\burg\w+', r'\bpush\w*', r'\bencourag\w+', r'\binsist\w+', r'told (?:me|him|her|them) to',
                 r'wanted (?:me|him|her|them) to'],
    r'holter|ambulatory (?:rhythm|ecg|ekg)|event monitor': [r'holter', r'ambulatory (?:rhythm|ecg|ekg|monitor)', r'event monitor', r'telemetry'],
    r'patholog': [r'patholog\w*', r'biops\w*', r'specimen', r'margins?\b', r'histolog\w*', r'carcinoma|adenoma|benign|malignan\w*'],
    r'\bmri\b|magnetic resonance': [r'\bmri\b', r'magnetic resonance', r'impression'],
}
STOP = {'the', 'and', 'for', 'with', 'any', 'from', 'that', 'this', 'patient', 'patients', 'documented', 'documentation', 'history',
        'status', 'level', 'test', 'tests', 'use', 'using', 'taking', 'take'}


def _db_path(stack):
    p = getattr(stack, 'path', stack)
    p = Path(p)
    return p / 'semantic.sqlite3' if p.is_dir() else p


def connect(stack):
    """Read-only connection to the semantic layer (a stack, a stack directory or a database path)."""
    c = sqlite3.connect(f'file:{_db_path(stack)}?mode=ro', uri=True, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def _meta(db, key):
    r = db.execute('select value from meta where key=?', (key,)).fetchone()
    return r[0] if r else None


def concept_patterns(concept):
    """-> list of regex strings for a predicate concept {'term','codes'}: the literal words (prefix-tolerant), the concept
    library's condition text pattern when the term or a code maps to one, drug names/brands/classes for medicines, and the
    SYNONYMS topic lexicon. Deterministic and ordered."""
    term = str(concept.get('term') or '').strip().lower()
    out = []
    if term:
        out.append(r'\b' + r'\W+'.join(re.escape(w) for w in re.findall(r'[a-z0-9]+', term)) + r'\w*')
    words = [w for w in re.findall(r'[a-z0-9]+', term) if len(w) > 3 and w not in STOP]
    if len(words) > 2:  # long free-text terms only; short phrases match as a phrase (single words over-match: "heart", "failure")
        out += [r'\b' + re.escape(w) + r'\w*' for w in words]
    for rx, extra in SYNONYMS.items():
        if re.search(rx, term):
            out += extra
    try:
        from . import concepts
        for cid in concepts.conditions_for_label(term)[:2]:
            out.append(concepts.CONDITIONS[cid]['text'])
        for code in concept.get('codes') or []:
            raw = code.split(':', 1)[-1]
            cid = concepts.concept_for_code(raw)
            if cid in concepts.CONDITIONS:
                out.append(concepts.CONDITIONS[cid]['text'])
    except Exception:  # noqa: BLE001 - the concept library is an enrichment; the literal words always work
        pass
    try:
        from . import drug_classes
        for w in words or [term]:
            for ing in (drug_classes.ingredients(w) or []):
                out.append(r'\b' + re.escape(str(ing).lower()) + r'\w*')
    except Exception:  # noqa: BLE001
        pass
    seen, uniq = set(), []
    for rx in out:
        if rx and rx not in seen:
            seen.add(rx)
            uniq.append(rx)
    return uniq


_OCR_L = re.compile(r"(?<=[a-z])(?<!\ba)1(?=[a-z])|\b1(?=[a-z]{3,})", re.I)
_OCR_O = re.compile(r"(?<=[a-z])0(?=[a-z])|\b0(?=[a-z]{3,})", re.I)


def ocr_fold(text):
    """The text with the digit-for-letter swaps of a scan undone ("me1oxicam" -> "meloxicam"), the SAME LENGTH, so a match found in it points at the
    same characters of the verbatim text. Used only to MATCH a pattern; a passage is always quoted as written."""
    return _OCR_O.sub('o', _OCR_L.sub('l', text or ''))


_SENT = re.compile(r'[^.!?;\n]+(?:[.!?;]+|\n|$)')


def sentences(text):
    """[(start, end, sentence)] over the raw text; lines and sentence punctuation both split."""
    return [(m.start(), m.end(), m.group().strip()) for m in _SENT.finditer(text) if m.group().strip()]


def passages_in(text, patterns, *, context=1, max_chars=MAX_CHARS):
    """Passages of one text matching any pattern: [{start, end, text, terms, hits}]. Overlapping context merges."""
    rx = [re.compile(p, re.I) for p in patterns]
    folded = ocr_fold(text)  # a scanned "me1oxicam" still matches "meloxicam"; the passage keeps the verbatim text
    if len(text.strip()) <= max_chars and any(r.search(folded) for r in rx):  # a short document is its own passage
        body = text.strip()
        start = text.index(body)
        return [{'start': start, 'end': start + len(body), 'text': body, 'terms': len({k for k, r in enumerate(rx) if r.search(folded)}),
                 'hits': 1}]
    sents = sentences(text)
    hit_idx = []
    for i, (_, _, s) in enumerate(sents):
        t = [k for k, r in enumerate(rx) if r.search(ocr_fold(s))]
        if t:
            hit_idx.append((i, t))
    spans = []
    for i, t in hit_idx:
        lo, hi = max(0, i - context), min(len(sents) - 1, i + context)
        if spans and lo <= spans[-1][1] + 1:
            spans[-1][1] = max(spans[-1][1], hi)
            spans[-1][2].update(t)
            spans[-1][3] += 1
        else:
            spans.append([lo, hi, set(t), 1])
    out = []
    for lo, hi, terms, hits in spans:
        start, end = sents[lo][0], sents[hi][1]
        body = text[start:end].strip()
        if len(body) > max_chars:  # keep the matched sentences, centered
            first = min(i for i, _ in hit_idx if lo <= i <= hi)
            start = sents[first][0]
            body = text[start:start + max_chars].strip()
            end = start + len(body)
        out.append({'start': start, 'end': start + len(body), 'text': body, 'terms': len(terms), 'hits': hits})
    return out


def _texts(db, patient_id, sources, as_of):
    """[(record_id, source, stream, note_type, date, text)] for a patient, event date <= as_of."""
    out = []
    want = set(sources)
    streams = []
    if 'clinical_note' in want:
        streams += NOTE_STREAMS
    if 'hie_document' in want:
        streams += HIE_STREAMS
    if 'other_text' in want:
        streams += OTHER_STREAMS
    if streams:
        q = ','.join('?' * len(streams))
        rows = db.execute(f"select record_id, stream, event_date, json_extract(payload,'$.text') t, "
                          f"coalesce(json_extract(payload,'$.note_type'), json_extract(payload,'$.document_type')) nt "
                          f"from record where patient_id=? and stream in ({q}) and event_date<=?", (patient_id, *streams, as_of))
        for r in rows:
            if r['t']:
                src = 'clinical_note' if r['stream'] in NOTE_STREAMS else 'hie_document' if r['stream'] in HIE_STREAMS else 'other_text'
                out.append((r['record_id'], src, r['stream'], r['nt'] or r['stream'], r['event_date'], r['t'], ''))
    if 'scanned_document_text' in want:
        cols = {c[1] for c in db.execute('pragma table_info(scanned_document)')}
        title = 'd.document_title' if 'document_title' in cols else "''"
        for r in db.execute(f'select t.record_id, t.text, d.document_type, d.event_date, t.text_source, {title} title from scanned_document_text t '
                            'left join scanned_document d on d.record_id=t.record_id where t.patient_id=? and '
                            'coalesce(d.event_date, ?)<=?', (patient_id, as_of, as_of)):
            if r['text']:
                out.append((r['record_id'], 'scanned_document_text', r['text_source'] or 'scanned_document',
                            r['document_type'] or 'scanned_document', r['event_date'], r['text'], r['title'] or ''))
    return out


class Passages(list):
    """The ranked passages, plus whether the bound dropped candidates: `truncated` is True when more passages matched than are returned
    (the max_passages or per_record cap), `candidates` is how many matched. A reduction over a truncated set cannot claim absence."""
    truncated = False
    candidates = 0


def retrieve(stack_or_db, patient_id, concept, *, window=None, sources=None, as_of, max_passages=MAX_PASSAGES, per_record=PER_RECORD,
             patterns=None, note_types=None, prefer_records=None):
    """Candidate passages for one patient and concept, ranked and bounded. Each: {passage_id, record_id, source, stream,
    note_type, date, start, end, text, terms, duplicates} (a Passages list: `.truncated` says the caps dropped matches). `window` {'start','end'} filters on the note's event date
    (a note dated in the window is where the statement was made; the reading's `historical` status handles the rest).
    `note_types` (names from note_open.NOTE_TYPES) keeps only records whose note type, stream or document title matches one of them
    (a cue such as "the discharge summary" or "the ED note"); `prefer_records` ranks passages of those records first."""
    db = stack_or_db if isinstance(stack_or_db, sqlite3.Connection) else connect(stack_or_db)
    pats = patterns or concept_patterns(concept)
    if not pats:
        return Passages()
    window = window or {}
    lo, hi = window.get('start') or '', min(window.get('end') or as_of, as_of)
    cands, seen = [], {}
    type_rx = None
    if note_types:
        from . import note_open
        type_rx = note_open.type_regex(note_types)
    prefer = set(prefer_records or [])
    for record_id, source, stream, note_type, day, text, title in sorted(_texts(db, patient_id, sources or
                                                                 ['clinical_note', 'scanned_document_text', 'hie_document', 'other_text'],
                                                                 as_of[:10]), key=lambda x: (x[4] or '', x[0])):
        if not day or day < lo or day > hi:
            continue
        if type_rx is not None and not type_rx.search(f'{note_type} {stream} {title}'):
            continue
        for ps in passages_in(text, pats):
            key = (re.sub(r'\W+', ' ', ps['text'].lower()).strip(), day)
            if key in seen:
                seen[key]['duplicates'].append(record_id)
                continue
            c = {'record_id': record_id, 'source': source, 'stream': stream, 'note_type': note_type, 'date': day,
                 'start': ps['start'], 'end': ps['end'], 'text': ps['text'], 'terms': ps['terms'], 'duplicates': []}
            seen[key] = c
            cands.append(c)
    cands.sort(key=lambda c: (c['record_id'] not in prefer, -c['terms'], c['date'] is None, _neg_date(c['date']), c['record_id'], c['start']))
    out, per = Passages(), {}
    for c in cands:
        if per.get(c['record_id'], 0) >= per_record:
            continue
        per[c['record_id']] = per.get(c['record_id'], 0) + 1
        out.append(c)
        if len(out) >= max_passages:
            break
    out.candidates, out.truncated = len(cands), len(out) < len(cands)
    for c in out:
        c['passage_id'] = f"{c['record_id']}@{c['start']}-{c['end']}"
    return out


def _neg_date(d):
    return tuple(-ord(ch) for ch in (d or ''))
