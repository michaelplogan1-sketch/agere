"""Questions about what a patient's notes SAY: routing, note predicates, and the rendered answer.

A question is a note question when its wording is about text (notes, "discussed", goals of care, code status, "ruled out", an impression,
"why was it stopped") rather than a structured table. It becomes one or two note predicates (docs/agere/QUESTION_SPEC.md, Addition 1):
frame (a fixed set), concept (a drug, a canonical phrase, or a phrase from the question), statuses, detail constraints, a window and the
detail fields to show. The grammar reads the ASKING sentence (the last sentence of the question) for the frame and the earlier sentences
for context; System One picks the frame from the closed list of frames when the cues are silent. builder A's note_frames reads the
passages (System One) and code reduces them; this module never reads a note itself.

A multi-hop question ("the later notes still show new medications" after a goals-of-care talk with no date given) first runs an anchor
predicate for the earlier event and uses its latest dated passage as the window start.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from . import chart_intents as CI
from . import chart_queries as Q

VERSION = 'chart-notes/1'
STRONG = re.compile(
    r"\b(?:in (?:the |his |her |their )?notes?|the notes?|notes? (?:say|show|record|mention|document|list|stated)|chart (?:say|show|mention)s?|later notes?|"
    r"discussed|discussion|conversation|goals[- ]of[- ]care|code status|dnr|dni|resuscitat\w*|chest compressions|breathing tube|intubat\w*|"
    r"advance directive|polst|molst|living will|health ?care proxy|why was|why did|why is|reason (?:for|why|\w+ (?:was|were))|accept(?:ed)?|declin\w+|refus\w+|"
    r"(?:report|reports|reported)\s+(?:taking|using|having)|discharge summary|impression|x-?ray|radiolog\w+|ruled out|rule[- ]out|stress test|"
    r"memory concern|intoleran\w+|outside (?:report|record|chest)|scanned|did the patient|does the patient|patient (?:choose|chose|want|wants|prefer))\b", re.I)
WEAK = re.compile(r"\b(?:falls?|fell|raised|trouble|barriers?|recommended|mentioned|anywhere|documented|noted)\b", re.I)
STRUCTURED_FIRST = re.compile(r"\b(?:ed visits?|admissions?|claims?|fills?|pick(?:ed)? up|prescribed|prescriptions?|mammogra\w+|flu shot|vaccin\w+|a1c|egfr|ldl|"
                              r"blood pressure|practice|attributed|allergies|medication list|active medications?|appointments?|no-shows?)\b", re.I)
FRAME_CUES = [  # (frame, pattern); evaluated on the asking sentence; every match is kept (at most two are used)
    ('care_process', r"goals[- ]of[- ]care|code status|\bdnr\b|\bdni\b|resuscitat\w*|chest compressions|breathing tube|intubat\w*|advance directive|polst|molst|living will|health ?care proxy|counsel\w*|referr\w+|hospice|palliative"),
    ('preference_refusal', r"\baccept\w*|\bdeclin\w*|\brefus\w*|\bprefer\w*|\bwants?\b|\bchoose\b|\bchose\b|\bdecid\w*|\bagree\w*"),
    ('medication_event', r"\bstopped\b|\bdiscontinu\w*|\bheld\b|\bswitch\w*|\bstarted\b|\bbeing started\b|\bstarting\b|\bdose\b|\btaking\b|\btakes\b|\bstill (?:list|show|taking|on)\w*|\blist\w* (?:it|them)\b|\brestart\w*|\bmedications?\b"),
    ('adverse_event', r"intoleran\w+|allerg\w+|side effects?|adverse|reaction|angioedema"),
    ('function_cognition', r"memory|cognit\w*|mobility|gait|balance|\badl\b|vision|hearing|home safety|dementia|\bfalls?\b|\bfell\b"),
    ('social_need', r"social|financial|barriers?|transport\w*|getting to|food|housing|afford\w*|isolat\w*|utilit\w*|trouble"),
    ('procedure_test', r"x-?ray|imaging|\bct\b|\bmri\b|stress test|biopsy|colonoscop\w*|echo\w*|impression|radiolog\w*|ultrasound|\bekg\b|\becg\b|\bstudy\b"),
    ('plan_followup', r"follow[- ]?up|recommend\w*|\breturn\b|disposition|discharged to|\bplan\b"),
    ('finding_value', r"smoking status|tobacco|\bscore\b|\bvalue\b|\breading\b"),
    ('condition', r"ruled out|rule[- ]out|diagnos\w+|history of|syndrome"),
    ('symptom', r"symptom|complain\w*|reports? (?:pain|dyspnea|nausea)|\bpain\b"),
]
CONCEPT_PHRASES = [  # (cue pattern, canonical concept term, frames it applies to; () = any); not a drug
    (r"goals[- ]of[- ]care", 'goals-of-care conversation', ('care_process',)), (r"goals[- ]of[- ]care", 'goals-of-care decision', ('preference_refusal',)),
    (r"code status|\bdnr\b|\bdni\b", 'code status', ('care_process',)),
    (r"code status|\bdnr\b|\bdni\b|resuscitat\w*|chest compressions|breathing tube|intubat\w*", 'resuscitation preference', ('preference_refusal',)),
    (r"resuscitat\w*|chest compressions|breathing tube|intubat\w*", 'code status', ('care_process',)),
    (r"advance directive|polst|molst|living will|health ?care proxy", 'advance directive', ('care_process', 'preference_refusal')),
    (r"social or financial|financial barriers?|social barriers?|financial|afford\w*", 'social or financial barrier', ('social_need',)),
    (r"trouble getting to appointments|transport\w*|getting to appointments", 'transportation to appointments', ('social_need',)),
    (r"\bfalls?\b|\bfell\b", 'falls', ('function_cognition', 'adverse_event')), (r"memory (?:concern|problem|loss)|cognit\w*", 'memory concern', ('function_cognition',)),
    (r"smoking status|tobacco", 'smoking status', ('finding_value', 'condition')), (r"drug intolerance|intoleran\w+", 'medication intolerance', ('adverse_event',)),
    (r"chest x-?ray", 'chest x-ray', ('procedure_test',)), (r"stress test", 'stress test', ('procedure_test',)),
    (r"acute coronary syndrome|\bacs\b", 'acute coronary syndrome', ('condition',)),
    (r"new disease-directed medications?|disease-directed", 'new disease-directed medication', ('medication_event',)),
    (r"discharge summary|follow[- ]?up", 'discharge follow-up', ('plan_followup',)), (r"discharged to|disposition", 'discharge disposition', ('plan_followup',)),
]
CONDITION_OF = re.compile(r"(?:was|were|is|are|has|had|did)\s+(?:the\s+|a\s+|an\s+)?([a-z][a-z -]{3,40}?)\s+(?:ruled out|excluded|present|documented|diagnosed|confirmed)", re.I)
RETURN_CUES = [  # (cue, frame, detail field to show)
    (r"\bwhy\b|\breason", 'medication_event', 'reason'), (r"\bwhy\b|\breason", 'preference_refusal', 'reason'),
    (r"what was done|done about|response", 'social_need', 'response'),
    (r"what did the patient (?:choose|decide)|\bchoose\b|\bchose\b", 'preference_refusal', 'decision'),
    (r"\bdecision\b|accept|declin", 'preference_refusal', 'decision'), (r"how many|\bcount\b|number of", 'function_cognition', 'count'),
    (r"how many|number of", 'adverse_event', 'count'), (r"impression|result|show", 'procedure_test', 'result_text'),
    (r"discharged to|disposition|where", 'plan_followup', 'disposition'), (r"follow[- ]?up|recommend", 'plan_followup', 'item'),
    (r"code status|chest compressions|breathing tube|dnr|resuscitat", 'care_process', 'code_status'),
]
MONTHS = ('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december')
MONTH_YEAR = re.compile(r"\b(" + '|'.join(MONTHS) + r")\s+((?:19|20)\d\d)\b", re.I)
ISO = re.compile(r'\b((?:19|20)\d\d-\d\d-\d\d)\b')
LATER = re.compile(r'\b(?:later|after|since|subsequent)\b', re.I)
POSITIVE = ('documented', 'possible', 'planned_not_done', 'historical')
PATIENT_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:SYN[-_]?[0-9A-F]{8}[-_]?[0-9A-F]{3}|WHP[-_]?[0-9A-F]{12}|P[-_](?=[A-Z0-9]*[0-9])[A-Z0-9]{2,8})(?:'s)?(?![A-Za-z0-9])", re.I)


def is_note_question(question, intent):
    q = question or ''
    if intent.get('domain') == 'notes':
        return True
    strong = STRONG.search(q)
    if not strong and not WEAK.search(q):
        return False
    if intent.get('domain') is None:
        return bool(strong or WEAK.search(q))
    if strong:
        # a crisp structured request (claims, fills, A1c ...) still wins when the only note-like word is generic
        generic = re.fullmatch(r'(?:accept(?:ed)?|declin\w+|refus\w+|scanned)', strong.group(), re.I)
        if STRUCTURED_FIRST.search(q) and generic and not re.search(r'\bnotes?\b|\bdiscussed\b', q, re.I):
            return False
        return True
    # only weak cues: a note question when no structured domain claims it
    return intent.get('domain') in ('wellness', 'condition', 'social_need', 'allergy', 'smoking') and bool(
        re.search(r'\b(?:falls?|raised|trouble|barriers?|mentioned|anywhere)\b', q, re.I))


def sentences(question):
    parts = [s.strip() for s in re.split(r'(?<=[.?!])\s+(?=[A-Z0-9])', question or '') if s.strip()]
    return parts or [question or '']


def asking_and_context(question):
    parts = sentences(question)
    ask = next((s for s in reversed(parts) if s.endswith('?')), parts[-1])
    context = ' '.join(s for s in parts if s is not ask)
    return ask, context


def window_for(ask, context, as_of):
    """The note window the question implies: its own words, a month and year, 'later' than a dated note, or the exact day of a dated note."""
    q = f'{context} {ask}'.strip()
    w = CI.window_of(q, as_of)
    if w:
        return w
    m = MONTH_YEAR.search(q)
    if m:
        month = MONTHS.index(m.group(1).lower()) + 1
        year = int(m.group(2))
        start = date(year, month, 1)
        end = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))
        return {'kind': 'month', 'start': start.isoformat(), 'end': end.isoformat(), 'start_exclusive': False, 'phrase': m.group()}
    iso = ISO.search(q)
    if iso:
        d = iso.group(1)
        if LATER.search(ask):
            start = (date.fromisoformat(d) + timedelta(days=1)).isoformat()
            return {'kind': 'after_date', 'start': start, 'end': str(as_of)[:10], 'start_exclusive': False, 'phrase': f'after {d}'}
        if re.search(r'\b(?:the|that|this)\s+' + re.escape(d) + r"|\b" + re.escape(d) + r"\s+(?:note|discharge|summary|report|visit|admission|document)", q, re.I):
            return {'kind': 'day', 'start': d, 'end': d, 'start_exclusive': False, 'phrase': d}
    return None


def frames_of(ask):
    found = []
    for frame, pat in FRAME_CUES:
        if re.search(pat, ask, re.I) and frame not in found:
            found.append(frame)
    # a drug that is only the object of "taking / stopped" is a medication question; do not also ask about procedures
    if 'medication_event' in found and 'procedure_test' in found and not re.search(r'x-?ray|imaging|stress test|biopsy|impression|radiolog', ask, re.I):
        found.remove('procedure_test')
    if 'function_cognition' in found and 'adverse_event' in found:
        found.remove('adverse_event')
    return found


def concept_for(frame, ask, context, drug):
    for text in (ask, context):
        for pat, term, frames in CONCEPT_PHRASES:
            if (not frames or frame in frames) and re.search(pat, text, re.I):
                return term, term
        if frame == 'condition':
            m = CONDITION_OF.search(text)
            if m:
                return m.group(1).strip(), m.group(1).strip()
        if frame in ('medication_event', 'preference_refusal', 'adverse_event', 'condition') and text is ask and drug:
            return drug['value'].replace('_', ' '), drug.get('term') or drug['value']
    if drug:
        return drug['value'].replace('_', ' '), drug.get('term') or drug['value']
    m = re.search(r"\b(?:about|of|for|on|regarding)\s+(?:the\s+|a\s+|an\s+)?([a-z][a-z -]{3,40}?)(?:\s+(?:in|for|at|during|when|and|from)\b|[?.,]|$)", ask, re.I)
    return (m.group(1).strip(), m.group(1).strip()) if m else (None, None)


FRAME_LABELS = {'condition': 'Condition or diagnosis', 'symptom': 'Symptom or complaint', 'medication_event': 'Medication event',
                'procedure_test': 'Procedure, test or study', 'finding_value': 'Measured value or score', 'adverse_event': 'Adverse event, allergy or intolerance',
                'care_process': 'Care process or service', 'social_need': 'Social need or barrier', 'function_cognition': 'Function, cognition or safety',
                'preference_refusal': 'Preference, refusal or decision', 'plan_followup': 'Plan, follow-up or disposition'}
NOT_DRUGS = frozenset('the a an any new it them that this these those his her their patient later earlier notes note chart still again also not never because'.split())
MED_WORD = [re.compile(r"\b([a-z][a-z-]{3,})\s+(?:was|were|has been|had been|is|are)\s+(?:stopped|started|held|discontinued|restarted|switched|changed)\b", re.I),
            re.compile(r"\b(?:stopped|started|holding|held|discontinued|restarted|taking|takes|using|list|lists|listed|show|shows|accept(?:ed)?|declined|refused)\s+(?:the\s+|on\s+)?([a-z][a-z-]{3,})\b", re.I),
            re.compile(r"\breason\s+([a-z][a-z-]{3,})\s+was\b", re.I)]


def _med_word(text):
    """A drug named only by its place in the sentence ("aspirin was stopped", "taking oxycodone"), for drugs the fill lexicon does not know."""
    for rx in MED_WORD:
        m = rx.search(text or '')
        if m and m.group(1).lower() not in NOT_DRUGS and not re.fullmatch(r'(?:therapy|medications?|meds?|medicines?|treatment|drugs?)', m.group(1), re.I):
            return {'kind': 'drug', 'value': m.group(1).lower(), 'classes': [], 'term': m.group(1)}
    return None


def build(question, as_of, *, lexicon=None, prior_predicates=None, choose=None):
    """-> [predicate dict]. `choose(question, options, task_key)` -> label | None is the System One closed-list choice (frame, when the cues are silent)."""
    question = PATIENT_TOKEN.sub(' ', question or '').strip()
    ask, context = asking_and_context(question)
    window = window_for(ask, context, as_of)
    drug = CI._drug(ask, lexicon) or CI._drug(context, lexicon) or _med_word(ask) or _med_word(context)
    frames = frames_of(ask)
    if not frames and context:
        frames = frames_of(context)[:1]
    elliptical = bool(prior_predicates) and len(ask.split()) <= 8 and not frames_of(ask)
    if elliptical:
        # "and morphine?": the previous question's predicates with the concept swapped
        out = []
        for p in prior_predicates:
            q = {k: v for k, v in p.items() if k != '_anchor'}
            if drug:
                q['concept'] = {'term': drug['value'].replace('_', ' '), 'codes': [], 'quote': drug.get('term') or drug['value']}
            else:
                m = re.search(r'(?:and|what about|how about)\s+(?:the\s+)?([a-z][a-z -]{2,30}?)\s*\?*$', ask.strip(), re.I)
                if not m:
                    return []
                q['concept'] = {'term': m.group(1).strip(), 'codes': [], 'quote': m.group(1).strip()}
            q['quote'] = question[:300]
            out.append(q)
        return out
    if not frames and choose:
        try:
            from . import note_frames as NF
            options = {k: v['label'] for k, v in NF.FRAMES.items() if k != 'note_contradiction'} or dict(FRAME_LABELS)
        except ImportError:  # builder A's module is not on this branch: the same closed list, written here
            options = dict(FRAME_LABELS)
        label = choose(f'Patient chart question: {question}\nWhich kind of note statement does the question ask about?', options, 'chart_reader_note_frame')
        frames = [label] if label else []
    if not frames:
        return []
    preds = []
    for frame in frames[:2]:
        term, quote = concept_for(frame, ask, context, drug)
        if not term:
            continue
        statuses = ['denied'] if frame == 'condition' and re.search(r'\bruled out\b|\bnot present\b|\bdenies\b', ask, re.I) else ['documented']
        detail = {}
        if frame == 'medication_event':
            if re.search(r'\bstopped\b|\bdiscontinu\w*', ask, re.I):
                detail['event'] = 'stopped'
            elif re.search(r'\bstarted\b|\bbeing started\b|\bstarting\b|\bnew\b', ask, re.I):
                detail['event'] = 'started'
        if frame == 'preference_refusal':
            if re.search(r'\baccept\w*', ask, re.I) and not re.search(r'\bdeclin\w*|\brefus\w*', ask, re.I):
                detail['decision'] = 'accepted'
            elif re.search(r'\bdeclin\w*|\brefus\w*', ask, re.I) and not re.search(r'\baccept\w*', ask, re.I):
                detail['decision'] = 'declined'
        ret = [fld for cue, fr, fld in RETURN_CUES if fr == frame and re.search(cue, ask, re.I)]
        pred = {'op': 'note', 'frame': frame, 'concept': {'term': term, 'codes': [], 'quote': quote}, 'statuses': statuses, 'quote': question[:300]}
        if detail:
            pred['detail'] = detail
        if ret:
            pred['return_detail'] = list(dict.fromkeys(ret))
        if window:
            pred['window'] = {'start': window['start'], 'end': window['end'], 'quote': window.get('phrase') or ''}
        preds.append(pred)
    # multi-hop: "later notes" with no date in the question -> run the context event first and use its date
    if preds and LATER.search(ask) and not ISO.search(question) and context:
        anchor_frames = frames_of(context)
        aterm, aquote = concept_for(anchor_frames[0], context, '', drug=None) if anchor_frames else (None, None)
        if aterm and anchor_frames and anchor_frames[0] != preds[0]['frame'] or (aterm and aterm != preds[0]['concept']['term']):
            anchor = {'op': 'note', 'frame': anchor_frames[0], 'concept': {'term': aterm, 'codes': [], 'quote': aquote}, 'statuses': ['documented'],
                      'combine': 'latest', 'quote': context[:300]}
            for p in preds:
                p['_anchor'] = anchor
    return preds[:2]


def is_elliptical(question):
    """A short follow-up with no frame cue of its own ("and morphine?", "what about aspirin?")."""
    q = (question or '').strip().lower()
    return len(q.split()) <= 8 and bool(re.match(r'^(?:and|what about|how about|also|then)\b', q)) and not frames_of(question)


def detail_matches(wanted, got):
    """Does the patient's reading satisfy the detail constraints of the predicate? (answer_patient returns every field, not only the asked ones.)"""
    for field, want in (wanted or {}).items():
        values = got.get(field)
        if values is None:
            return False
        have = values if isinstance(values, (list, tuple, set)) else [values]
        ws = want if isinstance(want, list) else [want]
        if not any(isinstance(w, dict) or w in have for w in ws):
            return False
    return True


WORDS = {'documented': 'documented', 'denied': 'denied in the notes', 'possible': 'possible (not confirmed)', 'planned_not_done': 'planned, not shown as done',
         'historical': 'documented as history', 'other_person': 'about another person', 'not_mentioned': 'not mentioned in the notes',
         'unknown': 'undecided: the evidence does not settle it'}


def _val(v):
    return ', '.join(map(str, v)) if isinstance(v, (list, tuple, set)) else str(v)


def render(stack, predicates, patient_id, as_of, *, ledger=None, nf=None, yes_no=False):
    """Run each predicate through note_frames.answer_patient and merge into one core answer."""
    if nf is None:
        from . import note_frames as nf  # builder A's module (spec: answer_patient)
    lines, sources, rows, statuses, matches = [], [], [], [], []
    for pred in predicates:
        anchor_note = None
        pred = dict(pred)
        anchor = pred.pop('_anchor', None)
        if anchor:
            a = nf.answer_patient(stack, _valid(nf, anchor), patient_id, as_of=as_of, ledger=ledger)
            dates = sorted(e.get('date') for e in (a.get('evidence') or []) if e.get('date'))
            if a.get('status') in POSITIVE and dates:
                start = (date.fromisoformat(dates[-1][:10]) + timedelta(days=1)).isoformat()
                pred['window'] = {'start': start, 'end': str(as_of)[:10], 'quote': f"after {anchor['concept']['term']} on {dates[-1][:10]}"}
                anchor_note = f"anchor: {anchor['concept']['term']} documented {dates[-1][:10]}; later notes read from {start}"
            else:
                anchor_note = f"anchor: {anchor['concept']['term']} has no dated documentation ({a.get('status')}); all notes were read"
            sources.extend(Q.src(e.get('note_type') or 'clinical_note', e.get('record_id'), e.get('date'), quote=e.get('quote'), anchor=True) for e in (a.get('evidence') or [])[:2])
        res = nf.answer_patient(stack, _valid(nf, pred), patient_id, as_of=as_of, ledger=ledger)
        status = res.get('status', 'unknown')
        detail = res.get('detail') or {}
        ok = detail_matches(pred.get('detail'), detail) if pred.get('detail') else True
        statuses.append(status)
        matches.append(ok)
        shown = {k: v for k, v in detail.items() if (not pred.get('return_detail') or k in pred['return_detail']) and v not in (None, [], '')} or {
            k: v for k, v in detail.items() if v not in (None, [], '')}
        ev = res.get('evidence') or []
        line = f"{pred['frame'].replace('_', ' ')} '{pred['concept']['term']}': {WORDS.get(status, status)}"
        if status in POSITIVE and pred.get('detail') and not ok:
            line += ' but not as asked (' + '; '.join(f'{k} {_val(v)}' for k, v in pred['detail'].items()) + ' not matched)'
        if shown:
            line += ' (' + '; '.join(f'{k}: {_val(v)}' for k, v in shown.items()) + ')'
        for e in ev[:3]:
            line += f" | {e.get('date') or ''} {e.get('note_type') or ''} {e.get('record_id') or ''}: \"{str(e.get('quote') or '')[:220]}\""
        if anchor_note:
            line += f' | {anchor_note}'
        lines.append(line)
        rows.append({'frame': pred['frame'], 'concept': pred['concept']['term'], 'status': status, 'detail': detail, 'p': res.get('p'), 'matches_question': ok})
        sources.extend(Q.src(e.get('note_type') or 'clinical_note', e.get('record_id'), e.get('date'), quote=e.get('quote')) for e in ev)
    positive = [s in POSITIVE and m for s, m in zip(statuses, matches)]
    if yes_no and len(statuses) >= 1:
        if all(s == 'unknown' for s in statuses):
            lead = 'Undecided'
        elif any(positive):
            lead = 'Yes'
        elif any(s == 'not_mentioned' for s in statuses) and not any(s in ('denied', *POSITIVE) for s in statuses):
            lead = 'Not documented'
        else:
            lead = 'No'
    else:
        lead = None
    headline = (f'{lead}: ' if lead else '') + ' || '.join(lines) if lines else 'No note predicate could be built from the question'
    return Q.core('note', headline, value=headline, table=rows, sources=sources, facts={'statuses': statuses, 'matches': matches, 'lead': lead},
                  notes=['note frames: System One reads retrieved passages; code reduces them to one patient value; an undecided patient is never read as "no"'])


def _valid(nf, pred):
    try:
        return nf.validate_predicate(pred) if hasattr(nf, 'validate_predicate') else pred
    except ValueError:
        slim = {k: v for k, v in pred.items() if k not in ('detail', 'return_detail')}
        try:
            return nf.validate_predicate(slim)
        except ValueError:
            return slim
