"""Patient references in question text: recognized from the warehouse's own id formats, matched exactly, never guessed.

The warehouse (semantic.sqlite3, table `patient`) holds three id formats:
  SYN-XXXXXXXX-XXX     synthetic cohort ids (8 + 3 hex digits)
  WHP-XXXXXXXXXXXX     warehouse ids (12 hex digits)
  P-XXXX               development patients (P-1001, P-HF11, P-RH01, ...)
and enrollment_span.member_id (and previous_member_id) holds payer member ids. The warehouse has no MRN column, so none is
recognized. A reference is accepted only when its normalized form (upper case, letters and digits only) equals one id in the
patient table: "syn-67436663-a0f", "SYN67436663A0F" and "SYN_67436663_A0F" are all the same id. A text that looks like an id but is
not in the table is reported as unknown, never replaced by a nearest id; a text that is the start of one or more real ids is
reported as ambiguous with the candidates (the caller asks, it never picks).

Follow-ups: "her A1c", "is he on a statin?", "his last fill" bind the previous turn's subject. The conversation keeps the subject
(and the frame of the question it answered) in answer_summary['subject']; evaluation mode stores no turn, so the subject is also kept in a bounded
in-process memory keyed by conversation id. Two hooks in conversation.py (documented in docs/agere/CHART_READER.md):
  conversation.handle_compose -> patient_ids.rebind(path, conversation_id, question)   (rewrites pronouns, sets current_prior())
  conversation.summaries / record -> answer_summary['subject'] and patient_ids.remember(...)
"""
from __future__ import annotations

import contextvars
import re
import sqlite3
import threading
from collections import OrderedDict
from pathlib import Path

VERSION = 'patient-ids/1'

# The formats seen in the patient table; used to tell "an id that is not in the warehouse" from an ordinary word.
ID_FORMATS = (
    ('SYN', re.compile(r'SYN-[0-9A-F]{8}-[0-9A-F]{3}')),
    ('WHP', re.compile(r'WHP-[0-9A-F]{12}')),
    ('P', re.compile(r'P-(?=[A-Z0-9]*[0-9])[A-Z0-9]{2,8}')),  # always holds a digit, so "P-ractice" is a word, not an id
)
_DASHES = dict.fromkeys(map(ord, '‐‑‒–—−'), '-')
# A candidate id token: a known prefix, then letters/digits with optional - or _ between groups (a space is not allowed, so the
# token never swallows the next word). The token is only a candidate until it matches the index.
TOKEN = re.compile(r'(?<![A-Za-z0-9])(?:SYN|WHP|P)[-_]?[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+){0,2}', re.I)
MEMBER_TOKEN = re.compile(r'(?<![A-Za-z0-9])[A-Za-z0-9]{8,12}(?![A-Za-z0-9])')
# The same pattern, used with no database, to know that a question names a patient (decision_context: a named patient is never
# excluded for being deceased).
NAMED_FORMAT = re.compile(r'(?<![A-Za-z0-9])(?:SYN[-_]?[0-9A-F]{8}[-_]?[0-9A-F]{3}|WHP[-_]?[0-9A-F]{12}|P[-_](?=[A-Z0-9]*[0-9])[A-Z0-9]{2,8})(?![A-Za-z0-9])', re.I)

PRONOUN = re.compile(r"\b(?:he|she|him|his|hers|himself|herself|her|this patient|that patient|the patient|same patient|"
                     r"this (?:person|member)|that (?:person|member))\b", re.I)
GROUP_PRONOUN = re.compile(r"\b(?:they|them|their|theirs)\b", re.I)
POPULATION_WORDS = re.compile(r"\b(?:patients|members|people|persons|everyone|all|cohort|population|how many patients|which patients)\b", re.I)
_OBJECT_NEXT = frozenset('to for a an the on in at about of is was has had did does and or if when by with from as that this not never'.split())


def normalize(text):
    return re.sub(r'[^A-Z0-9]', '', str(text or '').upper().translate(_DASHES))


def semantic_file(target):
    """target: a stack object (has .path), a directory, a semantic.sqlite3 path or an open connection."""
    if isinstance(target, sqlite3.Connection):
        return target
    path = Path(getattr(target, 'path', target))
    return path / 'semantic.sqlite3' if path.is_dir() else path


_INDEX, _INDEX_LOCK = {}, threading.Lock()


def _index(target):
    """{'patients': {normalized: [patient_id]}, 'members': {normalized: {patient_id}}} cached per file and modification time."""
    src = semantic_file(target)
    own = not isinstance(src, sqlite3.Connection)
    if own:
        try:
            key = (str(src), src.stat().st_mtime_ns)
        except OSError:
            return {'patients': {}, 'members': {}}
        with _INDEX_LOCK:
            if key in _INDEX:
                return _INDEX[key]
        db = sqlite3.connect(f'file:{src}?mode=ro', uri=True)
    else:
        db, key = src, None
    try:
        patients, members = {}, {}
        for (pid,) in db.execute('SELECT patient_id FROM patient'):
            patients.setdefault(normalize(pid), []).append(pid)
        try:
            for pid, m1, m2 in db.execute('SELECT patient_id, member_id, previous_member_id FROM enrollment_span'):
                for m in (m1, m2):
                    if m:
                        members.setdefault(normalize(m), set()).add(pid)
        except sqlite3.Error:
            pass
    finally:
        if own:
            db.close()
    out = {'patients': patients, 'members': members}
    if key:
        with _INDEX_LOCK:
            _INDEX[key] = out
    return out


def names_a_patient(text):
    """Does the text contain something shaped like a patient id? (format only, no database)"""
    return bool(NAMED_FORMAT.search(str(text or '').translate(_DASHES)))


def find(target, text):
    """Every patient reference in the text, in order: [{'kind': 'patient_id'|'member_id', 'status': 'found'|'ambiguous'|'unknown'|'partial',
    'patient_id', 'candidates', 'quote', 'message'}]. Exact matches only."""
    idx = _index(target)
    text = str(text or '').translate(_DASHES)
    out, seen = [], set()
    for m in TOKEN.finditer(text):
        quote, norm = m.group(), normalize(m.group())
        if norm in seen:
            continue
        hits = idx['patients'].get(norm)
        if hits and len(hits) > 1:  # several ids share these letters and digits: an exact spelling of one of them still names it
            exact = [h for h in hits if h.upper() == quote.upper()]
            hits = exact if len(exact) == 1 else hits
        if hits:
            seen.add(norm)
            if len(hits) == 1:
                out.append({'kind': 'patient_id', 'status': 'found', 'patient_id': hits[0], 'candidates': hits, 'quote': quote})
            else:
                out.append({'kind': 'patient_id', 'status': 'ambiguous', 'patient_id': None, 'candidates': sorted(hits), 'quote': quote,
                            'message': f'"{quote}" matches {len(hits)} patients ({", ".join(sorted(hits)[:5])}); which one is meant?'})
            continue
        shaped = any(rx.fullmatch(quote.upper().replace('_', '-')) or rx.fullmatch(_dashed(norm)) for _, rx in ID_FORMATS)
        if len(norm) >= 5:
            starts = sorted(i for k, v in idx['patients'].items() if k.startswith(norm) for i in v)
            if starts:
                seen.add(norm)
                out.append({'kind': 'patient_id', 'status': 'partial', 'patient_id': None, 'candidates': starts[:8], 'quote': quote,
                            'message': f'"{quote}" is the start of {len(starts)} patient id(s) ({", ".join(starts[:5])}); '
                                       'give the full id so no patient is guessed.'})
                continue
        if shaped:
            seen.add(norm)
            out.append({'kind': 'patient_id', 'status': 'unknown', 'patient_id': None, 'candidates': [], 'quote': quote,
                        'message': f'"{quote}" looks like a patient id but is not in this warehouse.'})
    if not out:  # member ids only when no patient id was named
        for m in MEMBER_TOKEN.finditer(text):
            who = idx['members'].get(normalize(m.group()))
            if not who:
                continue
            quote = m.group()
            if len(who) == 1:
                (pid,) = who
                out.append({'kind': 'member_id', 'status': 'found', 'patient_id': pid, 'candidates': [pid], 'quote': quote})
            else:
                out.append({'kind': 'member_id', 'status': 'ambiguous', 'patient_id': None, 'candidates': sorted(who), 'quote': quote,
                            'message': f'member id "{quote}" belongs to {len(who)} patients ({", ".join(sorted(who)[:5])}); which one is meant?'})
    return out


def _dashed(norm):
    """SYN67436663A0F -> SYN-67436663-A0F; WHP...; P1001 -> P-1001 (to test a hyphen-less spelling against the formats)."""
    if norm.startswith('SYN') and len(norm) == 14:
        return f'SYN-{norm[3:11]}-{norm[11:]}'
    if norm.startswith('WHP') and len(norm) == 15:
        return f'WHP-{norm[3:]}'
    if norm.startswith('P') and len(norm) > 2:
        return f'P-{norm[1:]}'
    return norm


# -- resolution ---------------------------------------------------------------------------------------------------------
def resolve(target, text, prior=None):
    """The one patient a question is about.
    -> {'status': 'found'|'none'|'ambiguous'|'unknown'|'multiple', 'patient_id', 'quote', 'how', 'message', 'references'}.
    'found' how = 'patient_id' | 'member_id' | 'pronoun' (bound to prior['patient_id']). Never guesses: an unmatched, partial or
    ambiguous reference gives a clarification message and no patient_id."""
    refs = find(target, text)
    found = [r for r in refs if r['status'] == 'found']
    ids = list(dict.fromkeys(r['patient_id'] for r in found))
    problems = [r for r in refs if r['status'] != 'found']
    if len(ids) > 1:
        return {'status': 'multiple', 'patient_id': None, 'quote': None, 'how': None, 'references': refs,
                'message': f'The question names {len(ids)} patients ({", ".join(ids[:5])}); a chart question is about one patient.'}
    if ids:
        r = found[0]
        return {'status': 'found', 'patient_id': ids[0], 'quote': r['quote'], 'how': r['kind'], 'references': refs, 'message': None}
    if problems:
        r = problems[0]
        return {'status': 'ambiguous' if r['status'] in ('ambiguous', 'partial') else 'unknown', 'patient_id': None, 'quote': r['quote'],
                'how': None, 'references': refs, 'message': r['message']}
    if prior and prior.get('patient_id') and has_pronoun(text):
        m = PRONOUN.search(text) or GROUP_PRONOUN.search(text)
        return {'status': 'found', 'patient_id': prior['patient_id'], 'quote': m.group() if m else None, 'how': 'pronoun',
                'references': refs, 'message': None}
    return {'status': 'none', 'patient_id': None, 'quote': None, 'how': None, 'references': refs, 'message': None}


# An elliptical follow-up names neither a patient nor a pronoun but continues the last turn: "and the one before that?",
# "what about the LDL?", "same for last year", "then?" (short, and opening with a continuation word).
ELLIPTICAL = re.compile(r"^\W*(?:and|also|plus|then|ok(?:ay)?,?\s+and|what about|how about|and what about|same (?:for|with)|the one|that one|"
                        r"the (?:previous|prior|next|last|first) one|before that|after that)\b", re.I)


def is_elliptical(text):
    t = str(text or '').strip()
    return bool(ELLIPTICAL.match(t)) and len(t.split()) <= 12 and not POPULATION_WORDS.search(t)


def has_pronoun(text):
    t = str(text or '')
    if PRONOUN.search(t):
        return True
    return bool(GROUP_PRONOUN.search(t)) and not POPULATION_WORDS.search(t)


def rewrite_pronouns(text, patient_id):
    """'is he on a statin?' -> 'is <id> on a statin?'; 'her A1c' -> '<id>'s A1c'. Only called when the text names no patient."""
    def possessive_her(m):
        nxt = re.match(r"\s*([A-Za-z0-9][\w'-]*)", text[m.end():])
        return f"{patient_id}'s" if nxt and nxt.group(1).lower() not in _OBJECT_NEXT else patient_id
    out = text
    out = re.sub(r'\b(?:his|hers|their|theirs)\b', f"{patient_id}'s", out, flags=re.I)
    out = re.sub(r'\bher\b', possessive_her, out, flags=re.I) if re.search(r'\bher\b', out, re.I) else out
    out = re.sub(r"\b(?:he|she|him|himself|herself|they|them|this patient|that patient|the patient|same patient|"
                 r"this (?:person|member)|that (?:person|member))\b", patient_id, out, flags=re.I)
    return out


# -- the previous turn's subject -----------------------------------------------------------------------------------------
_MEMORY, _MEMORY_LOCK, MEMORY_SIZE = OrderedDict(), threading.Lock(), 512
_PRIOR = contextvars.ContextVar('agere_chart_prior', default=None)


def make_subject(patient_id, quote=None, frame=None):
    """The record stored with a turn: {'kind': 'patient', 'patient_id', 'quote', 'frame'}; frame is the typed intent that was answered
    (chart_reader.Intent as a dict) so an elliptical follow-up can inherit it."""
    return {'kind': 'patient', 'patient_id': patient_id, 'quote': quote or patient_id, **({'frame': frame} if frame else {})}


def remember(conversation_id, subject):
    if not conversation_id or not isinstance(subject, dict) or not subject.get('patient_id'):
        return
    with _MEMORY_LOCK:
        _MEMORY[conversation_id] = subject
        _MEMORY.move_to_end(conversation_id)
        while len(_MEMORY) > MEMORY_SIZE:
            _MEMORY.popitem(last=False)


def recall(path, conversation_id):
    """The latest single-patient subject of a conversation (in-process memory first, then the stored turns), or None."""
    if not conversation_id:
        return None
    with _MEMORY_LOCK:
        if conversation_id in _MEMORY:
            return _MEMORY[conversation_id]
    try:
        from . import conversation
        for turn in reversed(conversation.turns(path, conversation_id)):
            subject = (turn.get('answer_summary') or {}).get('subject')
            if isinstance(subject, dict) and subject.get('patient_id'):
                return subject
    except Exception:  # noqa: BLE001 - a missing store is "no prior subject"
        return None
    return None


def current_prior():
    """The prior subject of the request being composed (set by rebind), or None."""
    return _PRIOR.get()


def rebind(path, conversation_id, question):
    """Hook for conversation.handle_compose. Sets current_prior() for this request and returns the question with its pronouns replaced
    by the prior turn's patient id when the question names no patient. Any other question is returned unchanged."""
    prior = recall(path, conversation_id) if conversation_id else None
    _PRIOR.set(prior)
    if prior and not names_a_patient(question) and has_pronoun(question):
        return rewrite_pronouns(question, prior['patient_id'])
    if prior and not names_a_patient(question) and is_elliptical(question):
        return f"{question.rstrip().rstrip('?')} for {prior['patient_id']}?"  # the continuation keeps the last turn's patient
    return question
