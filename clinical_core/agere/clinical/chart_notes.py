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
from . import note_open as NO

VERSION = 'chart-notes/2'
STRONG = re.compile(
    r"\b(?:in (?:the |his |her |their )?notes?|the notes?|notes? (?:say|show|record|mention|document|list|stated)|(?:is|are) there (?:a|an|any) (?:\w+ )?(?:notes?|documentation|mention)|"
    r"(?:a|an|any) notes? (?:saying|stating|documenting|mentioning|showing|recording)|chart (?:say|show|mention)s?|later notes?|"
    r"discussed|discussion|conversation|goals[- ]of[- ]care|code status|dnr|dni|resuscitat\w*|chest compressions|breathing tube|intubat\w*|"
    r"advance directive|polst|molst|living will|health ?care proxy|why was|why did|why is|reason (?:for|why|\w+ (?:was|were))|accept(?:ed)?|declin\w+|refus\w+|"
    r"(?:report|reports|reported)\s+(?:taking|using|having)|discharge summary|impression|x-?ray|radiolog\w+|ruled out|rule[- ]out|stress test|"
    r"memory concern|intoleran\w+|outside (?:report|record|chest)|scanned|did the patient|does the patient|patient (?:choose|chose|want|wants|prefer)|"
    r"counsel(?:ed|ing|led|ling)|(?:do|does|did|are|is)\s+(?:the\s+)?(?:later|subsequent)\s+(?:notes?|records?|visits?))\b", re.I)
# Real wording points at a DOCUMENT rather than saying "the notes": "the June 2028 annual physical", "the orthopedics letter", "the MRI report scanned in",
# "per the callback", "the d/c summary", "the H&P", "the progress note", "outside records". A document kind (with a date, "the", or "per/at/in") is a
# note cue; a bare "visit" is one only when the question asks what was said or found (VISIT_REF + CONTENT_ASK).
MODALITY = (r"holter|\bmri\b|\bct\b|cat scan|ultrasound|sonogram|echocardiogra\w*|\becho\b|x-?ray|\bekg\b|\becg\b|stress test|colonoscop\w*|endoscop\w*|"
            r"biops\w*|patholog\w*|\beeg\b|\bemg\b|doppler|angiogra\w*|cardiac cath\w*|sleep study|spirometry|\bpfts?\b|\bpet\b|nuclear|carotid|fluoroscop\w*")
DOC_KIND = re.compile(
    r"\b(?:annual|yearly|routine|preventive|prevention|wellness)\s+(?:physical|exam|visit|review|check-?up|appointment)s?\b|\bpreventive (?:care )?(?:review|visit)\b|"
    r"\b(?:\w+\s+){0,2}(?:letter|callback|call-?back)s?\b|\b(?:medication history|phone|telephone|nurse|follow-?up|triage|care management|outreach)\s+(?:call|message|note|review|encounter)s?\b|"
    r"\b(?:d/c|dc|discharge|hospital|transition(?:-of-care)?|transfer)\s+(?:summary|summaries|paperwork|note|report)s?\b|\bh\s?(?:&|and)\s?p\b|\bhistory and physical\b|"
    r"\bprogress notes?\b|\b(?:office|clinic|visit|consult\w*|specialist|specialty|operative|op|nursing|admission|triage|intake|telehealth) notes?\b|"
    r"\b(?:" + MODALITY + r"|imaging|radiolog\w*|outside|specialist|scanned|surgical|lab(?:oratory)?|procedure|operative|ed|er|pathology)\s+(?:report|result|read|study|film|findings?)s?\b|"
    r"\b(?:outside|prior|old|external|received|faxed|scanned|transferred|hospital)\s+(?:records?|documents?|charts?|paperwork)\b|"
    r"\b(?:the|that|this)\s+(?:[a-z0-9/-]+\s+){0,2}(?:physical|check-?up)\b|\b(?:orthopedic|cardiology|specialist|referral|consult\w*)\s+(?:visit|appointment|evaluation|clinic)\b|"
    r"\b(?:per|according to|based on|from)\s+the\s+(?:[a-z0-9-]+\s+){0,3}(?:visit|review|call|letter|report|summary|note|physical|callback|appointment)\b|"
    r"\b(?:it|that|this)\s+(?:should|would|might)\s+be\s+(?:in|on)\s+the\b", re.I)
ROLE_ASK = re.compile(r"\b(?:the\s+)?(?:nurse|physician|doctor|provider|clinician|surgeon|cardiologist|specialist|therapist|pharmacist|dietitian|social worker|\bMA\b|resident|hospitalist|PCP)\s+(?:record\w*|document\w*|note[ds]?|write|wrote|writes|chart\w*|mention\w*|say|said|says|advis\w+|recommend\w*|suggest\w*|tell|told|find|found|ask\w*|report\w*)\b", re.I)
VISIT_REF = re.compile(r"\b(?:(?:acute|sick|urgent|new[- ]patient|post-?op\w*|follow-?up|office|clinic|telehealth|video|annual|same-day|walk-in)\s+)?(?:visit|appointment|encounter)\b", re.I)
CONTENT_ASK = re.compile(r"\b(?:who|why|what|which|how (?:big|large|small|high|low|much|long))\b[^?]{0,90}\b(?:say|said|says|show\w*|record\w*|document\w*|note[ds]?|push\w*|urg\w+|encourag\w+|"
                         r"insist\w*|convinc\w+|recommend\w*|suggest\w*|advis\w+|report\w*|mention\w*|complain\w*|describ\w+|find|found|reveal\w*|conclude\w*|"
                         r"bump\w*|increas\w+|decreas\w+|start\w*|stop\w*|prescrib\w+|order\w*|tell|told|ask\w*|brought|prompt\w*|come back|came back)\b", re.I)
TEST_ASK = re.compile(r"\b(?:" + MODALITY + r")\b[^?]{0,60}\b(?:show\w*|say|said|find|found|reveal\w*|conclude\w*|impression|result\w*|read|report\w*|size)\b|"
                      r"\b(?:show\w*|say|said|find|found|reveal\w*|conclude\w*|impression|result\w*|read|report\w*)\b[^?]{0,40}\b(?:" + MODALITY + r")\b|"
                      r"\b(?:how (?:big|large)|what size|size of)\b[^?]{0,40}\b(?:nodule|mass|lesion|cyst|polyp|tumor|stone|aneurysm)\b|\b(?:nodule|mass|lesion|cyst|polyp)s?\b[^?]{0,40}\b(?:size|mm|cm)\b", re.I)
BACKREF = re.compile(r"\b(?:that|the same|this|the)\s+(?:day|visit|appointment|encounter|note|call|report|letter|review|physical|check-?up|admission|stay|time)\b|\bat that time\b|"
                     r"\bthen\b\s*\??$|\bon that date\b|\bsame (?:day|visit)\b", re.I)
DUE_FOR = re.compile(r"\b(?:due|overdue|need(?:s|ed)?|schedul\w+|book\w*|eligible|time) (?:for|to|a|an)\b[^?]{0,30}\b(?:physical|annual|wellness|visit|check-?up|exam)\b|"
                     r"\b(?:when|how long)\b[^?]{0,30}\b(?:last|next)\b[^?]{0,20}\b(?:physical|annual|wellness|check-?up|exam)\b", re.I)  # a care-gap / scheduling question
WEAK = re.compile(r"\b(?:falls?|fell|raised|trouble|barriers?|symptoms?|complaints?|recommended|mentioned|anywhere|documented|noted)\b", re.I)
STRUCTURED_FIRST = re.compile(r"\b(?:ed visits?|admissions?|claims?|fills?|pick(?:ed)? up|prescribed|prescriptions?|mammogra\w+|flu shot|vaccin\w+|a1c|egfr|ldl|"
                              r"blood pressure|practice|attributed|allergies|medication list|active medications?|appointments?|no-shows?)\b", re.I)
FRAME_CUES = [  # (frame, pattern); evaluated on the asking sentence; every match is kept (at most two are used)
    ('care_process', r"goals[- ]of[- ]care|code status|\bdnr\b|\bdni\b|resuscitat\w*|chest compressions|breathing tube|intubat\w*|advance directive|polst|molst|living will|health ?care proxy|counsel\w*|referr\w+|hospice|palliative|lung (?:cancer )?screening|screening criteria|meets? (?:the )?(?:screening )?criteria|eligib\w+"),
    ('preference_refusal', r"\baccept\w*|\bdeclin\w*|\brefus\w*|\bprefer\w*|\bwants?\b|\bchoose\b|\bchose\b|\bdecid\w*|\bagree\w*"),
    ('medication_event', r"\bstopped\b|\bdiscontinu\w*|\bheld\b|\bswitch\w*|\bstarted\b|\bbeing started\b|\bstarting\b|\bstart\b|\bstop\b|\badd(?:ed)?\b|\bdose\b|\btaking\b|\btakes?\b|\bused?\b|\busing\b|\bbump\w*|\bup-?titrat\w*|\bincreas\w+|\bdecreas\w+|\bsuggest\w* (?:starting|stopping|adding)|\bstill (?:list|show|taking|on)\w*|\blist\w* (?:it|them)\b|\brestart\w*|\bmedications?\b"),
    ('adverse_event', r"intoleran\w+|allerg\w+|side effects?|adverse|reaction|angioedema"),
    ('function_cognition', r"memory|cognit\w*|mobility|gait|balance|\badl\b|vision|hearing|home safety|dementia|\bfalls?\b|\bfell\b"),
    ('social_need', r"social|financial|barriers?|transport\w*|getting to|food|housing|afford\w*|isolat\w*|utilit\w*|trouble"),
    ('procedure_test', r"x-?ray|imaging|impression|radiolog\w*|\bstudy\b|echo\w*|" + MODALITY),
    ('plan_followup', r"follow[- ]?up|recommend\w*|suggest\w*|advis\w+|\breturn\b|disposition|discharged to|\bwhere\b[^?]{0,40}\bdischarg\w+|\bdischarg\w+ (?:home|destination|location)|\bgo(?:ne)? (?:home|after)\b|\bsent (?:home|to)\b|\bplan\b"),
    ('preference_refusal', r"\bwho (?:pushed|urged|encouraged|convinced|insisted|talked|made|told|prompted|asked|brought|drove)\b|\bpushed\b|\burged\b|\bencouraged\b|\binsisted\b|\bprompted\b"),
    ('social_need', r"smoking|tobacco|alcohol|drinks?\b|drinking|\betoh\b|substance|cannabis|marijuana"),
    ('finding_value', r"\bscore\b|\bvalue\b|\breading\b|\ba1c\b|\bhba1c\b|\bphq-?\d\b|\bgad-?\d\b|\bbmi\b|\bldl\b|\begfr\b|come back as|came back as|how (?:big|large|small|high|low)\b|what size|\bsize of\b|\bin (?:mm|cm)\b|\bnodule\b"),
    ('condition', r"ruled out|rule[- ]out|\bexcluded\b|diagnos\w+|history of|syndrome"),
    ('symptom', r"symptom|complain\w*|reports? (?:pain|dyspnea|nausea)|\bpain\b"),
]
CONCEPT_PHRASES = [  # (cue pattern, canonical concept term, frames it applies to; () = any); not a drug
    (r"goals[- ]of[- ]care", 'goals of care', ('care_process',)), (r"goals[- ]of[- ]care", 'goals of care', ('preference_refusal',)),
    (r"code status|\bdnr\b|\bdni\b", 'code status', ('care_process',)),
    (r"code status|\bdnr\b|\bdni\b|resuscitat\w*|chest compressions|breathing tube|intubat\w*", 'chest compressions', ('preference_refusal',)),
    (r"resuscitat\w*|chest compressions|breathing tube|intubat\w*", 'code status', ('care_process',)),
    (r"advance directive|polst|molst|living will|health ?care proxy", 'advance directive', ('care_process', 'preference_refusal')),
    (r"social or financial|financial barriers?|social barriers?|financial|afford\w*", 'financial', ('social_need',)),
    (r"trouble getting to appointments|transport\w*|getting to appointments", 'transportation', ('social_need',)),
    (r"\bfalls?\b|\bfell\b", 'falls', ('function_cognition', 'adverse_event')), (r"memory (?:concern|problem|loss)|cognit\w*", 'memory concern', ('function_cognition',)),
    (r"smok\w*|tobacco|cigarette", 'smoking', ('social_need', 'finding_value', 'condition')), (r"drug intolerance|intoleran\w+", 'intolerance', ('adverse_event',)),
    (r"chest x-?ray|\bcxr\b", 'chest x-ray', ('procedure_test',)), (r"stress test", 'stress test', ('procedure_test',)),
    (r"acute coronary syndrome|\bacs\b", 'acute coronary syndrome', ('condition',)),
    (r"new disease-directed medications?|disease-directed", 'started', ('medication_event',)),
    (r"discharge summary|follow[- ]?up", 'follow up', ('plan_followup',)),
    (r"\bdischarg\w+|go(?:ne)? home|sent (?:home|to)", 'discharge disposition', ('plan_followup',)), (r"discharged to|disposition", 'discharge disposition', ('plan_followup',)),
    (r"alcohol|\bdrinks?\b|drinking|\betoh\b", 'alcohol', ('social_need', 'finding_value', 'condition')),
    (r"housing|homeless\w*|evict\w*", 'housing', ('social_need',)), (r"\bfood\b|hungry|grocer\w*", 'food', ('social_need',)), (r"utilit\w+", 'utilities', ('social_need',)),
    (r"isolat\w+|lonel\w+", 'social isolation', ('social_need',)),
    (r"hba1c|\ba1c\b|hemoglobin a1c", 'HbA1c', ('finding_value',)), (r"phq-?9", 'PHQ-9', ('finding_value',)), (r"phq-?2", 'PHQ-2', ('finding_value',)),
    (r"gad-?7", 'GAD-7', ('finding_value',)), (r"pain (?:score|level|rating|scale)", 'pain score', ('finding_value',)),
    (r"lung (?:cancer )?screening|screening criteria", 'lung cancer screening', ('care_process',)),
    (r"anti-?inflammator\w*|\bnsaids?\b", 'anti-inflammatory medication', ('medication_event', 'plan_followup')),
]
# A thing named only by its kind: the imaging / test word of the question ("MRI", "Holter monitor", "surgical pathology"), the measure being sized
# ("how big was the lung nodule"), or "a medication" when the question asks WHICH drug was started / stopped / recommended.
MODALITY_TERM = re.compile(r"\b((?:surgical\s+|lung\s+|cardiac\s+|carotid\s+)?(?:" + MODALITY + r")(?:\s+monitor)?)", re.I)
MEASURE_OF = re.compile(r"\b(?:how (?:big|large|small)\s+(?:was|is|were|are)|(?:what size|size|measurement|dimensions?) (?:was|is|were|of))\s+(?:the\s+|a\s+|that\s+)?([a-z][a-z -]{2,30}?)\s*(?:\bon\b|\bin\b|\bat\b|\bfrom\b|\bper\b|[?,.]|$)", re.I)
DECIDED_OBJECT = re.compile(r"\b(?:refus\w+|declin\w+|accept\w*|agree\w*|want\w*|chose|choose|prefer\w*|turned down|opted (?:for|out of))\s+(?:the\s+|a\s+|an\s+|her\s+|his\s+|their\s+|any\s+)?([a-z][a-z -]{2,30}?)(?:\s+(?:in|at|on|for|because|due|during|from|per|according)\b|[?.,]|$)", re.I)
WHO_URGED = re.compile(r"\bwho\b[^?]{0,40}\b(?:push\w*|urg\w+|encourag\w+|convinc\w+|insist\w*|prompt\w*|talked|made|told|asked|brought)\b|\b(?:pushed|urged|encouraged|insisted|prompted)\b", re.I)
WHICH_DRUG = re.compile(r"\b(?:what|which)\b[^?]{0,70}\b(?:start\w*|stop\w*|prescrib\w*|recommend\w*|suggest\w*|add\w*|chang\w+|switch\w*|advis\w+|new)\b", re.I)

CONDITION_OF = re.compile(r"(?:was|were|is|are|has|had|did)\s+(?:the\s+|a\s+|an\s+)?([a-z][a-z -]{3,40}?)\s+(?:ruled out|excluded|present|documented|diagnosed|confirmed)", re.I)
RETURN_CUES = [  # (cue, frame, detail field to show)
    (r"\bwhy\b|\breason", 'medication_event', 'reason'), (r"\bwhy\b|\breason", 'preference_refusal', 'reason'),
    (r"what was done|done about|response", 'social_need', 'response'),
    (r"what did the patient (?:choose|decide)|\bchoose\b|\bchose\b", 'preference_refusal', 'decision'),
    (r"\bdecision\b|accept|declin", 'preference_refusal', 'decision'), (r"how many|\bcount\b|number of", 'function_cognition', 'count'),
    (r"how many|number of", 'adverse_event', 'count'), (r"impression|result|show", 'procedure_test', 'result_text'), (r"impression|result|show", 'procedure_test', 'result'),
    (r"discharged to|disposition|where", 'plan_followup', 'disposition'), (r"follow[- ]?up|recommend", 'plan_followup', 'timeframe'),
    (r"follow[- ]?up|recommend", 'plan_followup', 'item'),
    (r"code status|chest compressions|breathing tube|dnr|resuscitat", 'care_process', 'code_status'),
]
MONTHS = ('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december')
MONTH_YEAR = re.compile(r"\b(" + '|'.join(MONTHS) + r")\s+((?:19|20)\d\d)\b", re.I)
ISO = re.compile(r'\b((?:19|20)\d\d-\d\d-\d\d)\b')
LATER = re.compile(r'\b(?:later|after|afterwards?|since|subsequent(?:ly)?|thereafter)\b', re.I)
PERSIST = re.compile(r"\bstill\b|\bremain(?:s|ed)?\b|\bcontinu(?:e|es|ed|ing)\b|\bany ?more\b|\bany longer\b|\bno longer\b", re.I)
INACTIVE_EVENTS = ('stopped', 'held', 'switched')  # a medication event that ends the drug: not "still listed"
STOP_WORDS = re.compile(r"\bstopp\w+|\bdiscontinu\w+|\bheld\b|\bhold\w*|\bcancel\w+|\bended\b", re.I)
POSITIVE = ('documented', 'possible', 'planned_not_done', 'historical')
PATIENT_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:SYN[-_]?[0-9A-F]{8}[-_]?[0-9A-F]{3}|WHP[-_]?[0-9A-F]{12}|P[-_](?=[A-Z0-9]*[0-9])[A-Z0-9]{2,8})(?:'s)?(?![A-Za-z0-9])", re.I)


def is_note_question(question, intent):
    q = question or ''
    if intent.get('domain') == 'notes':
        return True
    strong = STRONG.search(q) or DOC_KIND.search(q) or TEST_ASK.search(q) or ROLE_ASK.search(q) or (
        VISIT_REF.search(q) if (CONTENT_ASK.search(q) and (note_dates(q) or MONTH_YEAR.search(q))) else None)
    if strong and not STRONG.search(q) and (DUE_FOR.search(q) or intent.get('domain') in ('care_gap', 'appointment')):
        strong = None  # only a document-kind word, and the question is about being due / scheduling: the structured reader answers it
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
        re.search(r'\b(?:falls?|raised|trouble|barriers?|symptoms?|complaints?|mentioned|anywhere)\b', q, re.I))


def sentences(question):
    parts = [s.strip() for s in re.split(r'(?<=[.?!])\s+(?=[A-Z0-9])', question or '') if s.strip()]
    return parts or [question or '']


def asking_and_context(question):
    parts = sentences(question)
    ask = next((s for s in reversed(parts) if s.endswith('?')), parts[-1])
    context = ' '.join(s for s in parts if s is not ask)
    return ask, context


def split_parts(question):
    """-> (ask, context, response_asked). A multi-part question ("Anything about X? What was done about it?") asks its FIRST question and
    then, in a later question that only points back at the finding, for the documented response. The later part is not the frame cue."""
    parts = sentences(question)
    qs = [s for s in parts if s.endswith('?')]
    if len(qs) > 1 and all(NO.RESPONSE_PART.search(s) for s in qs[1:]) and not NO.RESPONSE_PART.search(qs[0]):
        ask = qs[0]
        context = ' '.join(s for s in parts if s is not ask and s not in qs[1:])
        return ask, context, True
    ask, context = asking_and_context(question)
    # one sentence that asks the finding and then, after "and", what was done about it ("... barriers, and what was done to address them?")
    m = NO.RESPONSE_PART.search(ask)
    if m and m.start() > 12:
        head = re.sub(r"[,;:\s]*(?:(?:and|then|also)\s*)*(?:(?:tell me|let me know|say|state|show me|explain|describe)\s*)?$", '', ask[:m.start()]).strip()
        if len(head) > 12 and not NO.RESPONSE_PART.search(head):
            return head + ('?' if ask.rstrip().endswith('?') else ''), context, True
    return ask, context, False


US_DATE = re.compile(r'\b(\d{1,2})/(\d{1,2})/((?:19|20)\d\d)\b')
NOTE_NOUN = r'(?:note|report|summary|visit|admission|document|study|film|letter|encounter|record|discharge|consult\w*|x-?ray|imaging|scan|message|call)'


def note_dates(text):
    """ISO dates a question gives for a note ("the 2029-08-05 note", "the 08/05/2029 ED note", "the ED note from August 5, 2029")."""
    out = [m.group(1) for m in ISO.finditer(text or '')]
    for m in US_DATE.finditer(text or ''):
        try:
            out.append(date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat())
        except ValueError:
            pass
    for m in re.finditer(r'\b(' + '|'.join(MONTHS) + r')\s+(\d{1,2}),?\s+((?:19|20)\d\d)\b', text or '', re.I):
        try:
            out.append(date(int(m.group(3)), MONTHS.index(m.group(1).lower()) + 1, int(m.group(2))).isoformat())
        except ValueError:
            pass
    return list(dict.fromkeys(out))


def window_for(ask, context, as_of):
    """The note window the question implies: its own words, a month and year, 'later' than a dated note, or the exact day of a dated note."""
    q = f'{context} {ask}'.strip()
    w = CI.window_of(q, as_of)
    if w and str(w.get('kind', '')).startswith('last_') and (MONTH_YEAR.search(q) or note_dates(q)):
        w = None  # "how many of the LAST 14 DAYS ... per the May 2027 callback": the span the note reports, not when the note was written
    if w:
        return w
    m = MONTH_YEAR.search(q)
    if m:
        month = MONTHS.index(m.group(1).lower()) + 1
        year = int(m.group(2))
        start = date(year, month, 1)
        end = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))
        return {'kind': 'month', 'start': start.isoformat(), 'end': end.isoformat(), 'start_exclusive': False, 'phrase': m.group()}
    dates = note_dates(q)
    if dates:
        d = dates[0]
        if LATER.search(ask):
            start = (date.fromisoformat(d) + timedelta(days=1)).isoformat()
            return {'kind': 'after_date', 'start': start, 'end': str(as_of)[:10], 'start_exclusive': False, 'phrase': f'after {d}'}
        if dated_note_reference(q):
            return {'kind': 'day', 'start': d, 'end': d, 'start_exclusive': False, 'phrase': d}
    return None


ON_DATE = re.compile(r"\b(?:on|at|during|from|dated|of)\s+(?:the\s+)?(?:[a-z-]+\s+){0,2}?(?:" + r"(?:\b(?:19|20)\d\d-\d\d-\d\d\b|\b\d{1,2}/\d{1,2}/(?:19|20)\d\d\b|\b(?:" + '|'.join(MONTHS) + r")\s+\d{1,2},?\s+(?:19|20)\d\d\b))", re.I)
AFTER_EVENT_IN_MONTH = re.compile(r"\b(?:after|following|since|post)\b[^?]{2,60}?\b(?:in|during|on|from)\s+(?:" + '|'.join(MONTHS) + r")\s+(?:19|20)\d\d\b", re.I)
DATE_TEXT = (r"(?:\b(?:19|20)\d\d-\d\d-\d\d\b|\b\d{1,2}/\d{1,2}/(?:19|20)\d\d\b|\b(?:" + '|'.join(MONTHS) + r")\s+\d{1,2},?\s+(?:19|20)\d\d\b)")


def dated_note_reference(q):
    """Does the wording point at ONE dated note ("the 2029-08-05 note", "the 2027-10-28 discharge summary", "the ED note from 2029-08-05",
    "the note dated 08/05/2029")? A date that only dates an event ("stopped on 2029-08-05") does not bind a note."""
    noun = NOTE_NOUN
    return bool(re.search(r"\b(?:the|that|this|a|an)\s+" + DATE_TEXT + r"|" + DATE_TEXT + r"\s+(?:\w+\s+){0,3}?" + noun + r"\b|\b" + noun +
                          r"s?\s+(?:from|of|dated|on|written|done|taken)\s+" + DATE_TEXT, q or '', re.I))


FRONTED_CLAUSE = re.compile(r"^\W*(?:after|following|since|once|when|given|before)\b[^?]{3,220}?[,)]\s*((?:and\s+)?(?:is|are|was|were|does|do|did|has|have|had|can|could|will|would)\b[^?]*\??)\s*$", re.I)


def main_clause(ask):
    m = FRONTED_CLAUSE.match(ask or '')
    return m.group(1) if m and frames_of(m.group(1)) else ask


def _lab_term(text):
    """The measurement the wording names, in the user's words (HbA1c, LDL, PHQ-9, blood pressure, BMI ...): chart_intents' lab lexicon, then its unmeasured vitals."""
    for pat, _ in CI.LAB_TERMS:
        m = re.search(pat, text or '', re.I)
        if m:
            return m.group().strip()
    m = CI.UNMEASURED.search(text or '')
    return m.group().strip() if m else None


def _condition_term(text):
    """A diagnosis the wording names, in the user's words, when the concept library knows it (heart failure, stroke, COPD ...)."""
    try:
        from . import concepts
    except ImportError:  # noqa: PERF203
        return None
    best = None
    for d in concepts.CONDITIONS.values():
        try:
            if d.get('exclude') and re.search(d['exclude'], text or '', re.I):
                continue
            m = re.search(d['text'], text or '', re.I)
        except re.error:
            continue
        if m and (best is None or len(m.group()) > len(best)):
            best = m.group()
    return best.strip() if best and len(best.strip()) > 2 else None


DOC_STRIP = re.compile(r"\b(?:medication history|phone|telephone|nurse|follow-?up|triage|care management|outreach)\s+(?:call|message|note|review|encounter|visit)s?\b|"
                       r"\b(?:d/c|dc|discharge|hospital|transition(?:-of-care)?|transfer)\s+(?:summary|summaries|paperwork)\b|\b(?:\w+\s+){0,2}(?:letter|callback|call-?back)s?\b", re.I)


def strip_documents(ask):
    """The asking sentence without the words that name the DOCUMENT ("follow-up call", "discharge summary", "orthopedics letter"): those are where to read, not what is asked."""
    out = DOC_STRIP.sub(' ', ask or '')
    return re.sub(r'\s+', ' ', out).strip()


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
    if not found:  # no cue word: a named measurement is a value, a named diagnosis is a condition (the same lexicons the structured reader uses)
        if _lab_term(ask):
            found.append('finding_value')
        elif _condition_term(ask):
            found.append('condition')
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
    if drug and frame not in ('procedure_test', 'finding_value', 'preference_refusal'):
        return drug['value'].replace('_', ' '), drug.get('term') or drug['value']
    if frame == 'procedure_test':
        for text in (ask, context):
            m = MODALITY_TERM.search(text or '')
            if m:
                return m.group(1).strip(), m.group(1).strip()
    if frame == 'finding_value':
        for text in (ask, context):
            lab = _lab_term(text)
            if lab:
                return lab, lab
            m = MEASURE_OF.search(text or '')
            if m:
                return m.group(1).strip(), m.group(1).strip()
    if frame in ('medication_event', 'plan_followup') and WHICH_DRUG.search(ask or ''):
        return 'medication', 'medication'
    if frame == 'preference_refusal' and WHO_URGED.search(ask or ''):
        return 'urged', 'urged'
    if frame == 'preference_refusal':
        m = DECIDED_OBJECT.search(ask or '')
        if m:
            return m.group(1).strip(), m.group(1).strip()
    if frame == 'condition':
        cond = _condition_term(ask) or _condition_term(context)
        if cond:
            return cond, cond
    if drug:
        return drug['value'].replace('_', ' '), drug.get('term') or drug['value']
    m = re.search(r"\b(?:about|of|for|on|regarding)\s+(?:the\s+|a\s+|an\s+|her\s+|his\s+|their\s+|any\s+)?([a-z][a-z -]{3,40}?)(?:\s+(?:in|for|at|during|when|and|from)\b|[?.,]|$)", ask, re.I)
    return (m.group(1).strip(), m.group(1).strip()) if m else (None, None)


FRAME_LABELS = {'condition': 'Condition or diagnosis', 'symptom': 'Symptom or complaint', 'medication_event': 'Medication event',
                'procedure_test': 'Procedure, test or study', 'finding_value': 'Measured value or score', 'adverse_event': 'Adverse event, allergy or intolerance',
                'care_process': 'Care process or service', 'social_need': 'Social need or barrier', 'function_cognition': 'Function, cognition or safety',
                'preference_refusal': 'Preference, refusal or decision', 'plan_followup': 'Plan, follow-up or disposition'}
NOT_DRUGS = frozenset('after before during from with when while about over under into onto than then for and but nor yet the a an any new it them that this these those his her their patient later earlier notes note chart still again also not never because impression impressions result results report reports finding findings anything nothing something'.split())
MED_WORD = [re.compile(r"\b(?:was|were|is|are)\s+([a-z][a-z-]{3,})\s+(?:stopped|started|held|discontinued|restarted|switched|changed)\b", re.I),
            re.compile(r"\b([a-z][a-z-]{3,})\s+(?:was|were|has been|had been|is|are)\s+(?:stopped|started|held|discontinued|restarted|switched|changed)\b", re.I),
            re.compile(r"\b(?:stopped|started|holding|held|discontinued|restarted|taking|takes?|using|used?|list|lists|listed|show|shows|accept(?:ed)?|declined|refused)\s+(?:the\s+|on\s+)?([a-z][a-z-]{3,})\b", re.I),
            re.compile(r"\breason\s+([a-z][a-z-]{3,})\s+was\b", re.I)]


def _med_word(text):
    """A drug named only by its place in the sentence ("aspirin was stopped", "taking oxycodone"), for drugs the fill lexicon does not know."""
    for rx in MED_WORD:
        m = rx.search(text or '')
        if m and m.group(1).lower() not in NOT_DRUGS and not re.fullmatch(r'(?:therapy|medications?|meds?|medicines?|treatment|drugs?)', m.group(1), re.I):
            return {'kind': 'drug', 'value': m.group(1).lower(), 'classes': [], 'term': m.group(1)}
    return None


DAYS_OF = re.compile(r"\bhow many (?:of )?(?:the )?(?:last |past |previous )?\d* ?(?:days|weeks|doses)\b", re.I)  # a reported rate ("0 of 14 days"): the passage states it, there is no event count to make
COUNT_FRAMES = ('function_cognition', 'adverse_event')  # frames whose reading has a stated `count` detail


def _persistence(pred, question, ask, context):
    """A medication event followed by "do later notes still list it": the later notes are read for the drug as ACTIVE / listed (any later passage
    that lists it without stopping it), anchored on the dated note (or the stopping event) so the answer cites both."""
    p = {k: v for k, v in pred.items() if k != '_ask_detail'}
    p.update({'_persist': True, 'statuses': ['documented'], 'combine': 'any',
              'return_detail': list(dict.fromkeys([*(p.get('return_detail') or []), 'event', 'adherence']))})
    dates = note_dates(question)
    anchor = {'op': 'note', 'frame': 'medication_event', 'concept': dict(p['concept']), 'statuses': ['documented'], 'combine': 'any',
              'quote': (context or question)[:300], '_event': 'stopped' if STOP_WORDS.search(f'{context} {question}') else None}
    if dates:
        anchor['window'] = {'start': dates[0], 'end': dates[0], 'quote': dates[0]}
        anchor['_keep_window'] = True
    p['_anchor'] = anchor
    return p


LONG_FRONTED_YES_NO = re.compile(r"^\W*(?:after|before|when|while|at|during|in|on|following|since|given|if|once|with|from)\b[^?]{3,220}?[,)]\s*(?:and\s+)?"
                                 r"(?:is|are|was|were|does|do|did|has|have|had|can|could|will|would)\b", re.I)


def asks_yes_no(ask):
    """A yes/no question, including one that opens with a long dated clause ("After the 2029-08-05 note that stopped X (reason), do later notes still ...?")."""
    return bool(CI.asks_yes_no(ask) or LONG_FRONTED_YES_NO.match(ask or ''))


ANAPHORIC_ASK = re.compile(r"^\W*(?:and\s+)?(?:what|which|who|how many|how much)\b[^?]{0,30}\b(?:it|they|them|those|that|these|this|ones?)\b[^?]{0,20}\??\W*$", re.I)


DC_SUMMARY = re.compile(r"\b(?:d/c|dc|d-c)\s+(?:summar\w+|paperwork|note|report)s?\b|\bhospital summar(?:y|ies)\b|\btransition(?:-of-care)? summar\w+", re.I)


def source_cues(text):
    """note_open.source_cues plus the spellings real wording uses for the same documents ("d/c summary")."""
    cues = NO.source_cues(text)
    if DC_SUMMARY.search(text or '') and 'discharge_summary' not in cues['note_types']:
        cues = {**cues, 'note_types': [*cues['note_types'], 'discharge_summary']}
    return cues


def build(question, as_of, *, lexicon=None, prior_predicates=None, choose=None):
    """-> [predicate dict]. `choose(question, options, task_key)` -> label | None is the System One closed-list choice (frame, when the cues are silent)."""
    question = PATIENT_TOKEN.sub(' ', question or '').strip()
    ask, context, response_asked = split_parts(question)
    window = window_for(ask, context, as_of)
    cues = source_cues(question)
    drug = CI._drug(ask, lexicon) or CI._drug(context, lexicon) or _med_word(ask) or _med_word(context)
    frames = frames_of(strip_documents(main_clause(ask))) or frames_of(main_clause(ask))
    if not frames and drug:
        frames = ['medication_event']  # a drug and no other cue ("was a statin discussed at the visit"): what the notes say about that medicine
    frames = frames or frames_of(main_clause(ask))  # "After <dated event clause>, do later notes show X?": the frame is what the main clause asks, not the clause that dates it
    if not frames and context:
        # the asking sentence is only a pointer back ("What is it?", "Which ones?"): the sentence that names the finding is the question
        named = next((x for x in sentences(context) if frames_of(x)), None)
        frames = frames_of(named or context)[:1]
        if named and frames and ANAPHORIC_ASK.search(ask):
            ask = named
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
    window = _refine_window(window, ask, context, question, frames, as_of)
    if prior_predicates and BACKREF.search(ask) and not window:
        window = _prior_window(prior_predicates)  # "and her PHQ-9 that day?", "did they bump the dose at that visit?": the earlier question's note window
    preds = []
    for frame in frames[:2]:
        oc = None if (drug and frame != 'social_need') else NO.open_class(frame, ask)
        if oc:  # a CLASS ("drug intolerance", "social or financial barriers", "symptoms"), not a thing: read by the frame's cue lexicon
            term, quote = oc[1], oc[1]
        else:
            term, quote = concept_for(frame, ask, context, drug)
        if not term:
            continue
        statuses = ['denied'] if frame == 'condition' and re.search(r'\bruled out\b|\bexcluded\b|\bnot present\b|\bdenies\b', ask, re.I) else ['documented']
        if term in ('smoking', 'code status') or re.search(r'\bis (?:a |an )?[a-z ]{0,30}(?:documented|recorded|mentioned)\b', ask, re.I):
            statuses = ['documented', 'denied'] + (['historical'] if term == 'smoking' else [])  # "is X documented anywhere": any statement about it counts
        detail = {}
        if frame == 'medication_event':
            if re.search(r'\bstopped\b|\bdiscontinu\w*', ask, re.I):
                detail['event'] = 'stopped'
            elif re.search(r'\bstarted\b|\bbeing started\b|\bstarting\b|\bnew\b', ask, re.I):
                detail['event'] = 'started'
        if frame == 'preference_refusal':
            if re.search(r'\b(?:not\s+(?:agree|accept)\w*|disagree\w*)\b', ask, re.I):
                return []  # not accepting need not mean declining; leave this negated proposition to the broader reader
            elif re.search(r'\b(?:accept|agree)\w*', ask, re.I) and not re.search(r'\bdeclin\w*|\brefus\w*', ask, re.I):
                detail['decision'] = 'accepted'
            elif re.search(r'\bdeclin\w*|\brefus\w*', ask, re.I) and not re.search(r'\baccept\w*', ask, re.I):
                detail['decision'] = 'declined'
        ret = [fld for cue, fr, fld in RETURN_CUES if fr == frame and re.search(cue, ask, re.I)]
        pred = {'op': 'note', 'frame': frame, 'concept': {'term': term, 'codes': [], 'quote': quote}, 'statuses': statuses, 'quote': question[:300]}
        if oc:
            pred['concept'] = {'term': term, 'codes': [], 'quote': quote, 'open': True, 'class': oc[0]}
            pred['statuses'] = ['documented', 'historical']
        if frame in ('medication_event', 'plan_followup', 'care_process') and re.search(r"\b(?:recommend\w*|suggest\w*|advis\w+|propos\w+|plan(?:ned)?)\b", ask, re.I):
            pred['statuses'] = list(dict.fromkeys([*pred['statuses'], 'planned_not_done']))  # what a letter or plan RECOMMENDS is not shown as done
        if cues['sources']:
            pred['sources'] = list(cues['sources'])
        if cues['note_types']:
            pred['note_types'] = list(cues['note_types'])
        elif prior_predicates and BACKREF.search(ask):
            inherited = [t for pp in prior_predicates for t in (pp.get('note_types') or [])]
            if inherited:
                pred['note_types'] = list(dict.fromkeys(inherited))
        if response_asked and not preds:
            pred['_response'] = True  # the later part of the question: what was documented as done about the finding
            if frame == 'social_need':
                ret.append('response')
        if detail:
            pred['_ask_detail'] = detail  # judged here against the fields answer_patient returns; never sent as a constraint
        if ret:
            pred['return_detail'] = list(dict.fromkeys(ret))
        if frame == 'medication_event' and 'reason' in ret and detail.get('event'):
            # A reason belongs to the requested event, not the latest medication
            # mention. A later continuation must not replace a documented stop.
            # ANY matching event retains its own reason and supporting passage.
            pred['detail'] = dict(detail)
            pred['combine'] = 'any'
        if frame == 'procedure_test' and {'result', 'result_text'} & set(ret) and not (window and window.get('kind') == 'day'):
            pred['combine'] = 'any'  # "the impression on the report" with no date: every matching report's result, not only the latest
        if window:
            pred['window'] = {'start': window['start'], 'end': window['end'], 'quote': window.get('phrase') or ''}
        preds.append(pred)
    # a question that asks HOW MANY is answered by one count over its first frame (a second frame would be a second answer)
    if preds and CI.HOW_MANY.search(ask) and not DAYS_OF.search(ask):
        preds = preds[:1]
        preds[0]['_count'] = True
        # Reported counts belong to the dated note, even when its events are
        # historical. An unqualified event-count question keeps its own scope.
        if re.search(r'\breport(?:s|ed|ing)?\b', ask, re.I):
            preds[0]['statuses'] = list(dict.fromkeys([*preds[0]['statuses'], 'historical']))
        preds[0]['return_detail'] = list(dict.fromkeys([*(preds[0].get('return_detail') or []), *(['count'] if preds[0]['frame'] in COUNT_FRAMES else [])]))
        if not preds[0]['concept'].get('open'):
            preds[0]['combine'] = 'any'  # every documented occurrence in the window, not only the latest
    # persistence after a dated event: "a 2029-08-05 note stopped X. Do later notes still list X?", "after <event>, is X still on the list?"
    if (preds and preds[0]['frame'] == 'medication_event' and drug and PERSIST.search(ask) and (LATER.search(ask) or note_dates(question))
            and not re.search(r'\bstarted\b|\bbeing started\b|\bstarting\b|\bnew\b', ask, re.I)):
        preds[0] = _persistence(preds[0], question, ask, context)
        return preds[:1]
    # multi-hop: "later notes" with no date in the question -> run the context event first and use its date
    if preds and LATER.search(ask) and not note_dates(question) and context:
        anchor_frames = frames_of(context)
        aterm, aquote = concept_for(anchor_frames[0], context, '', drug=None) if anchor_frames else (None, None)
        if aterm and anchor_frames and anchor_frames[0] != preds[0]['frame'] or (aterm and aterm != preds[0]['concept']['term']):
            anchor = {'op': 'note', 'frame': anchor_frames[0], 'concept': {'term': aterm, 'codes': [], 'quote': aquote}, 'statuses': ['documented'],
                      'combine': 'latest', 'quote': context[:300]}
            for p in preds:
                p['_anchor'] = anchor
    return preds[:2]


def _refine_window(window, ask, context, question, frames, as_of):
    """Windows the generic wording gives that window_for leaves open: a date after "on / at / during" is the day of the encounter a lookup asks about
    (not for a medication event, where "stopped on DATE" dates the event, not a note); "after the colectomy in June 2028" reads from that month on."""
    q = f'{context} {ask}'.strip()
    m = AFTER_EVENT_IN_MONTH.search(q)
    mm = MONTH_YEAR.search(q) if m else None
    if mm and window and window.get('kind') == 'month':
        return {**window, 'kind': 'after_month', 'end': str(as_of)[:10], 'phrase': f'from {mm.group()}'}
    if window is None and frames and frames[0] != 'medication_event' and not LATER.search(ask):
        dates = note_dates(q)
        if len(dates) == 1 and ON_DATE.search(q):
            return {'kind': 'day', 'start': dates[0], 'end': dates[0], 'start_exclusive': False, 'phrase': dates[0]}
    return window


def _prior_window(prior_predicates):
    for p in prior_predicates or []:
        w = p.get('window')
        if w and w.get('start'):
            return {'kind': 'prior', 'start': w['start'], 'end': w.get('end') or w['start'], 'start_exclusive': False, 'phrase': w.get('quote') or w['start']}
    return None


def back_references(question, prior_note):
    """Does a follow-up point back at the previous note question's encounter ("that visit", "that day", "the same call")?"""
    return bool(prior_note) and bool(BACKREF.search(question or ''))


DANGLING = re.compile(r"\b(?:at|on|during|for|from|in)\s+that\s+(?:visit|appointment|encounter|day|admission|stay|call)\b|\bthat\s+(?:day|visit)\?*\s*$", re.I)


def dangling_reference(question):
    """Wording that points at an encounter an earlier question must have named ("at that visit", "that day")."""
    return bool(DANGLING.search(question or ''))


def is_elliptical(question):
    """A short follow-up with no frame cue of its own ("and morphine?", "what about aspirin?")."""
    q = (question or '').strip().lower()
    return len(q.split()) <= 8 and bool(re.match(r'^(?:and|what about|how about|also|then)\b', q)) and not frames_of(question)


def follows_note_question(intent, prior_note):
    """Is a typed (structured) reading of an elliptical follow-up really the next question of a prior NOTE question? True when the new intent
    names a drug on the medication domain and the prior note question was about a medication event."""
    concept = (intent or {}).get('concept') or {}
    return (intent or {}).get('domain') == 'medication' and concept.get('kind') == 'drug' and any(p.get('frame') == 'medication_event' for p in prior_note or [])


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


RESULT_WORDS = re.compile(r'impression|conclusion|result|finding|assessment|plan|recommend', re.I)


def focus_quote(quote, pred, limit=400):
    """The part of a passage that speaks to the question: a short passage whole; a long one cut to the sentences that carry the concept (and, for
    a result question, the impression / result sentences) instead of its first `limit` characters (a letterhead)."""
    text = str(quote or '').strip()
    if len(text) <= limit:
        return text
    from . import note_evidence as NE
    pats = [re.compile(p, re.I) for p in NE.concept_patterns(pred['concept'])] if not pred['concept'].get('open') else []
    want_result = bool({'result', 'result_text'} & set(pred.get('return_detail') or []))
    keep = [s for _, _, s in NE.sentences(text) if any(p.search(s) for p in pats) or (want_result and RESULT_WORDS.search(s))]
    out = ' '.join(keep) if keep else text
    return out[:limit]


OCR_DIGIT = [(re.compile(r"(?<=[a-z])(?<!\ba)1(?=[a-z])|\b1(?=[a-z]{3,})"), 'l'), (re.compile(r"(?<=[a-z])0(?=[a-z])|\b0(?=[a-z]{3,})"), 'o')]


def ocr_clean(text):
    """A scanned passage with a digit inside a word ("1owerlobe" for "lowerlobe"): the same text with the obvious digit-for-letter swaps undone,
    shown NEXT TO the verbatim quote, never in place of it."""
    out = str(text or '')
    for rx, ch in OCR_DIGIT:
        out = rx.sub(ch, out)
    return out


def _quote(e, n=300):
    return str(e.get('quote') or '').strip().replace('\n', ' ')[:n]


def _where(e):
    return f"{e.get('date') or ''} {e.get('note_type') or ''} {e.get('record_id') or ''}".strip()


def _detail_text(detail):
    shown = {k: v for k, v in (detail or {}).items() if v not in (None, [], '')}
    return ' (' + '; '.join(f'{k}: {_val(v)}' for k, v in shown.items()) + ')' if shown else ''


def render_open(pred, res):
    """Words for an open-concept result: every finding with its status, detail, dates, record ids and quotes; undecided findings as undecided.
    Never a bare count: a finding is stated in words with the quote that supports it."""
    label = pred['concept']['term']
    frame = pred['frame'].replace('_', ' ')
    findings, unsure = res.get('findings') or [], res.get('undecided_findings') or []
    if not findings and not unsure:
        return (f"{frame} ({label}): nothing documented in the notes read ({res.get('passages_read', 0)} passage reading(s), none states it)", [])
    parts = []
    for i, g in enumerate(findings, 1):
        ev = g['evidence']
        span = g['first_date'] if g['first_date'] == g['last_date'] else f"{g['first_date']} to {g['last_date']}"
        ids = ', '.join(dict.fromkeys(e['record_id'] for e in ev))
        n_rec = f"at least {g['n_records']}" if g.get('bounds_partial') else g['n_records']  # a capped read bounds the dates and the count
        span = f'{span} (among the passages read, not first/last overall)' if g.get('bounds_partial') else span
        text = f"({i}) {g['concept']}: {WORDS.get(g['status'], g['status'])}{_detail_text(g.get('detail'))}; {span}; {n_rec} record(s): {ids}"
        shown = []
        for e in ev[:1] + ev[-1:]:
            if e not in shown:
                shown.append(e)
        text += ' | ' + ' | '.join(f'{_where(e)}: "{_quote(e)}"' for e in shown)
        parts.append(text)
    if findings:
        head = f"{frame} ({label}): {len(findings)} finding(s) in the notes"
    else:
        head = f"{frame} ({label}): no finding the readers could settle"
    line = head + ': ' + ' ; '.join(parts) if parts else head
    if unsure:
        line += ' ; undecided (not counted as findings): ' + ' ; '.join(
            f"{u['concept']} [{u['evidence'][0]['record_id']}, {u['evidence'][0]['date']}: \"{_quote(u['evidence'][0], 160)}\"]" for u in unsure[:5])
    sources = [e for g in findings + unsure for e in g['evidence']]
    return line, sources


def response_predicates(pred, res, as_of):
    """The predicates that read what the notes document as DONE about the findings of `pred` (same or later notes): a care process and a
    plan / follow-up, both open, the passages of the finding's own records first."""
    evs = [e for g in (res.get('findings') or []) for e in g['evidence']] or [e for e in (res.get('evidence') or []) if e.get('supports')]
    dates = sorted(e['date'] for e in evs if e.get('date'))
    if not dates:
        return [], None
    names = [g['concept'] for g in (res.get('findings') or [])][:3] or [pred['concept']['term']]
    term = 'action taken about ' + ', '.join(names)
    base = {'op': 'note', 'concept': {'term': term, 'codes': [], 'quote': term, 'open': True, 'class': 'response'}, 'combine': 'any',
            'statuses': ['documented', 'planned_not_done'], 'window': {'start': dates[0][:10], 'end': str(as_of)[:10], 'quote': f'from {dates[0][:10]}'},
            'near_records': list(dict.fromkeys(e['record_id'] for e in evs)), 'quote': 'what was done about it'}
    if pred.get('sources'):
        base['sources'] = list(pred['sources'])
    return [{**base, 'frame': 'care_process'}, {**base, 'frame': 'plan_followup'}], dates[0][:10]


def _whole(x):
    try:
        v = float(str(x).replace(',', ''))
    except (TypeError, ValueError):
        return None
    return int(v) if v == int(v) and v >= 0 else None


def count_events(res, is_open=False):
    """HOW MANY, from one patient's reading: the number a note states (the latest stating note decides), else the number of distinct
    documented occurrences in the window (one per note date). -> {'n', 'basis'} or None when the reading is undecided (never a guessed 0)."""
    if res.get('status') == 'unknown':
        return None
    if is_open:
        n = len(res.get('findings') or [])
        if res.get('truncated') and n:
            return {'n': n, 'basis': f"at least: distinct findings the readers settled; {res.get('partial')}"}
        return {'n': n, 'basis': 'distinct findings the readers settled' if n else 'none documented'}
    evs = [e for e in (res.get('evidence') or []) if e.get('supports')]
    stated = sorted(((e.get('date') or '', _whole((e.get('detail') or {}).get('count')), e) for e in evs if _whole((e.get('detail') or {}).get('count')) is not None),
                    key=lambda t: t[0])
    if stated:
        d, n, e = stated[-1]
        return {'n': n, 'basis': f"stated in the {d or 'undated'} {e.get('note_type') or 'note'} {e.get('record_id') or ''}".strip()}
    days = {e.get('date') or e.get('record_id') for e in evs}
    return {'n': len(days), 'basis': 'distinct documented occurrences, one per note date' if days else 'none documented'}


def persistence_verdict(res):
    """"Do later notes still list X?": the later passages that list the drug as ACTIVE (a documented medication event that does not stop, hold
    or switch it and is not 'not taking') versus those that end it. -> (status, holds, active, ended)."""
    docs = [e for e in (res.get('evidence') or []) if e.get('status') == 'documented']
    docs.sort(key=lambda e: (e.get('date') or '', e.get('record_id') or ''))
    active = [e for e in docs if (e.get('detail') or {}).get('event') not in INACTIVE_EVENTS and (e.get('detail') or {}).get('adherence') != 'not_taking']
    ended = [e for e in docs if e not in active]
    if active:
        return 'documented', True, active, ended
    if res.get('status') == 'unknown':
        return 'unknown', None, [], ended
    return 'denied', False, [], ended


def render_persistence(pred, status, active, ended):
    term, frm = pred['concept']['term'], (pred.get('window') or {}).get('start') or 'the anchor'

    def show(es):
        return ' | '.join(f"{_where(e)}{_detail_text({k: v for k, v in (e.get('detail') or {}).items() if k in ('event', 'adherence')})}: \"{focus_quote(e.get('quote'), pred)}\"" for e in es[:4])
    if active:
        line = f"medication event '{term}': still listed in later notes (from {frm}): {len(active)} later record(s) list it as active, latest {active[-1].get('date')} | {show(active)}"
    elif status == 'unknown':
        line = f"medication event '{term}': undecided whether later notes (from {frm}) still list it: the evidence does not settle it"
    elif ended:
        line = f"medication event '{term}': not listed as active in later notes (from {frm}); later records that end or hold it | {show(ended)}"
    else:
        line = f"medication event '{term}': no later note (from {frm}) lists it"
    sources = [Q.src(e.get('note_type') or 'clinical_note', e.get('record_id'), e.get('date'), quote=e.get('quote')) for e in [*active, *ended]]
    return line, sources


def render(stack, predicates, patient_id, as_of, *, ledger=None, nf=None, yes_no=False, allow_live=None):
    """Run each predicate through note_frames.answer_patient and merge into one core answer."""
    if nf is None:
        from . import note_frames as nf  # builder A's module (spec: answer_patient)
    lines, sources, rows, statuses, matches, counted, neg_asked = [], [], [], [], [], [], []
    for pred in predicates:
        anchor_note = unresolved = None
        pred = dict(pred)
        anchor = pred.pop('_anchor', None)
        ask_detail = pred.pop('_ask_detail', None)
        want_response = pred.pop('_response', False)
        persist = pred.pop('_persist', False)
        count_asked = pred.pop('_count', False)
        if anchor:
            anchor = dict(anchor)
            keep_window, wanted_event = anchor.pop('_keep_window', False), anchor.pop('_event', None)
            a = _read(nf, stack, _valid(nf, anchor), patient_id, as_of, ledger, allow_live)
            aev = [e for e in (a.get('evidence') or []) if e.get('date')]
            stopped = []
            if wanted_event:  # the stopping note itself, when the readings say which passages stopped it
                stopped = [e for e in aev if (e.get('detail') or {}).get('event') == wanted_event and e.get('status') == 'documented']
                aev = stopped or aev
            dates = sorted(e['date'] for e in aev)
            if keep_window:  # the question dated the note: later notes are read from the day after it, and the anchor note itself is cited
                said = stopped if wanted_event else (aev if a.get('status') in POSITIVE else [])
                day, from_day = anchor['window']['start'], (pred.get('window') or {}).get('start')
                anchor_note = (f"anchor: {anchor['concept']['term']} {wanted_event or 'documented'} in the {day} note ({', '.join(dict.fromkeys(e['record_id'] for e in said))}); later notes read from {from_day}"
                               if said else f"anchor: the {day} note was read for {anchor['concept']['term']} ({a.get('status')}); later notes read from {from_day}")
            elif a.get('status') in POSITIVE and dates:
                start = (date.fromisoformat(dates[-1][:10]) + timedelta(days=1)).isoformat()
                pred['window'] = {'start': start, 'end': str(as_of)[:10], 'quote': f"after {anchor['concept']['term']} on {dates[-1][:10]}"}
                anchor_note = f"anchor: {anchor['concept']['term']} documented {dates[-1][:10]}; later notes read from {start}"
                if a.get('truncated'):  # capped anchor search: the latest date seen need not be the latest documented
                    anchor_note += (f" (partial anchor: {a.get('partial')}; {dates[-1][:10]} is the latest date among the passages read, "
                                    'a later one may exist, so the window and the answer are partial)')
            else:
                # no date to start from: reading every note would answer a different question ("ever", not "after"), so nothing is read
                unresolved = (f"anchor: {anchor['concept']['term']} has no dated documentation ({a.get('status')}); "
                              'the later notes were not read because there is no date to read them from')
                anchor_note = unresolved
            sources.extend(Q.src(e.get('note_type') or 'clinical_note', e.get('record_id'), e.get('date'), quote=e.get('quote'), anchor=True) for e in ([e for e in (a.get('evidence') or []) if e.get('supports')] or (a.get('evidence') or []))[:2])
        if unresolved:
            res = {'status': 'unknown', 'holds': None, 'detail': {}, 'evidence': [], 'p': None}
        else:
            res = _read(nf, stack, _valid(nf, pred), patient_id, as_of, ledger, allow_live)
        status = res.get('status', 'unknown')
        asks_denied = pred.get('statuses') == ['denied']  # "was X ruled out?": a `denied` reading is the YES here
        neg_asked.append(asks_denied)
        detail = res.get('detail') or {}
        ok = bool(res['holds']) if res.get('holds') is not None else status in POSITIVE or (asks_denied and status == 'denied')
        is_open = bool(pred['concept'].get('open'))
        if ask_detail and not persist:
            # the question names an event ("why was X stopped"): the passage that HAS that event answers it, whenever it was written,
            # not only the latest passage read about the drug (a later list that still shows the drug does not undo the stop)
            res, status, detail = _select_asked_event(res, status, detail, ask_detail)
        if ok and ask_detail:
            ok = detail_matches(ask_detail, detail)
        elif ask_detail and not ok and status in POSITIVE and detail_matches(ask_detail, detail):
            ok = True
        active = ended = None
        if persist and not is_open:
            status, ok, active, ended = persistence_verdict(res)
        counted.append(count_events(res, is_open) if count_asked else None)
        statuses.append(status)
        matches.append(ok)
        ev = res.get('evidence') or []
        if persist and not is_open:
            line, psources = render_persistence(pred, status, active, ended)
            if anchor_note:
                line += f' | {anchor_note}'
            lines.append(line)
            rows.append({'frame': pred['frame'], 'concept': pred['concept']['term'], 'status': status, 'detail': detail, 'p': res.get('p'), 'matches_question': ok,
                         'records': [e['record_id'] for e in active + ended]})
            sources.extend(psources)
        elif is_open:
            line, open_sources = render_open(pred, res)
            if pred.get('sources') or pred.get('note_types'):
                line += ' | read only: ' + ', '.join([*(pred.get('note_types') or []), *(pred.get('sources') or [])])
            if anchor_note:
                line += f' | {anchor_note}'
            lines.append(line)
            for g in (res.get('findings') or []) + (res.get('undecided_findings') or []):
                rows.append({'frame': pred['frame'], 'concept': g['concept'], 'status': g['status'], 'detail': g.get('detail') or {}, 'p': g.get('p'),
                             'first_date': g.get('first_date'), 'last_date': g.get('last_date'), 'records': [e['record_id'] for e in g['evidence']],
                             'matches_question': g['status'] != 'undecided'})
            if not (res.get('findings') or res.get('undecided_findings')):
                rows.append({'frame': pred['frame'], 'concept': pred['concept']['term'], 'status': status, 'detail': {}, 'p': None, 'matches_question': False})
            sources.extend(Q.src(e.get('note_type') or 'clinical_note', e.get('record_id'), e.get('date'), quote=e.get('quote')) for e in open_sources)
        else:
            shown = {k: v for k, v in detail.items() if (not pred.get('return_detail') or k in pred['return_detail']) and v not in (None, [], '')} or {
                k: v for k, v in detail.items() if v not in (None, [], '')}
            line = f"{pred['frame'].replace('_', ' ')} '{pred['concept']['term']}': {WORDS.get(status, status)}"
            if status in POSITIVE and ask_detail and not ok:
                line += ' but not as asked (' + '; '.join(f'{k} {_val(v)}' for k, v in ask_detail.items()) + ' not matched)'
            if shown:
                line += ' (' + '; '.join(f'{k}: {_val(v)}' for k, v in shown.items()) + ')'
            if pred.get('sources') or pred.get('note_types'):
                line += ' [read only: ' + ', '.join([*(pred.get('note_types') or []), *(pred.get('sources') or [])]) + ']'
            cited = [e for e in ev if e.get('supports')] or ev  # a positive answer cites the passages that produced it, not every record read
            top = sorted(cited, key=lambda e: not e.get('supports'))[:6 if pred.get('combine') == 'any' else 4]
            multi = pred.get('combine') == 'any' and len([e for e in top if e.get('supports')]) > 1
            separate_reports = (multi and pred['frame'] == 'procedure_test'
                                and len({(e.get('record_id'), e.get('date')) for e in top if e.get('supports')}) > 1)
            if separate_reports:
                line = f"Multiple matching '{pred['concept']['term']}' reports; each dated result is listed separately"
                if pred.get('sources') or pred.get('note_types'):
                    line += ' [read only: ' + ', '.join([*(pred.get('note_types') or []), *(pred.get('sources') or [])]) + ']'
            for e in top:
                own = ''
                if multi:  # several reports: each one's own reading, not the best one's
                    own = _detail_text({k: v for k, v in (e.get('detail') or {}).items() if k in (pred.get('return_detail') or [])})
                shown_quote = focus_quote(e.get('quote'), pred)
                line += f" | {e.get('date') or ''} {e.get('note_type') or ''} {e.get('record_id') or ''}{own}: \"{shown_quote}\""
                if ocr_clean(shown_quote) != shown_quote:
                    line += f' [OCR-corrected reading: "{ocr_clean(shown_quote)}"]'
            if anchor_note:
                line += f' | {anchor_note}'
            if counted[-1] is not None:
                line = f"{counted[-1]['n']} {pred['concept']['term']} documented in the notes ({counted[-1]['basis']}) | {line}"
            lines.append(line)
            if separate_reports:
                rows.extend({'frame': pred['frame'], 'concept': pred['concept']['term'],
                             'status': e.get('status') or status, 'detail': e.get('detail') or {},
                             'date': e.get('date'), 'record_id': e.get('record_id'), 'p': e.get('p'),
                             'matches_question': bool(e.get('supports'))} for e in top)
            else:
                rows.append({'frame': pred['frame'], 'concept': pred['concept']['term'], 'status': status, 'detail': detail, 'p': res.get('p'), 'matches_question': ok,
                             **({'count': counted[-1]['n']} if counted[-1] is not None else {})})
            sources.extend(Q.src(e.get('note_type') or 'clinical_note', e.get('record_id'), e.get('date'), quote=e.get('quote')) for e in cited)
        if res.get('truncated'):  # retrieval caps: absence, earliest, latest and "no contradiction" are not established from part of the notes
            lines[-1] += f" | partial: {res.get('partial')}; the rest were not read, so a negative or earliest/latest answer is undecided"
        if want_response:
            rline, rrows, rsources, rstatuses = _response_part(nf, stack, pred, res, patient_id, as_of, ledger, allow_live)
            lines.append(rline)
            rows.extend(rrows)
            sources.extend(rsources)
    positive = [(s in POSITIVE or (n and s == 'denied')) and m for s, m, n in zip(statuses, matches, neg_asked)]
    if yes_no and len(statuses) >= 1:
        if any(positive):
            lead = 'Yes'
        elif any(s == 'unknown' for s in statuses):
            lead = 'Undecided'
        elif any(s == 'not_mentioned' for s in statuses) and not any(s in ('denied', *POSITIVE) for s in statuses):
            lead = 'Not documented'
        else:
            lead = 'No'
    else:
        lead = None
    headline = (f'{lead}: ' if lead else '') + ' || '.join(lines) if lines else 'No note predicate could be built from the question'
    if len(counted) == 1 and counted[0] is not None:  # the question asked HOW MANY: a count, with the value the reader computed
        n = counted[0]['n']
        return Q.core('count', headline, value=n, count=n, table=rows, sources=sources, facts={'statuses': statuses, 'matches': matches, 'lead': None, 'count_basis': counted[0]['basis']},
                      notes=['note frames: System One reads retrieved passages; the count is a number the note states, else the distinct documented occurrences in the window'])
    return Q.core('note', headline, value=headline, table=rows, sources=sources, facts={'statuses': statuses, 'matches': matches, 'lead': lead},
                  notes=['note frames: System One reads retrieved passages; code reduces them to one patient value; an undecided patient is never read as "no"'])


def _select_asked_event(res, status, detail, ask_detail):
    """-> (res, status, detail). When no reading of the combined value matches the asked detail but individual passages do, the value is the
    LATEST matching passage and the cited evidence is the matching passages (supports=True), so the answer states that passage's own detail."""
    if status in POSITIVE and detail_matches(ask_detail, detail):
        return res, status, detail
    hit = [e for e in (res.get('evidence') or []) if e.get('status') in POSITIVE and detail_matches(ask_detail, e.get('detail') or {})]
    if not hit:
        return res, status, detail
    hit.sort(key=lambda e: (e.get('date') or '', e.get('record_id') or ''))
    best = hit[-1]
    rest = [e for e in (res.get('evidence') or []) if e not in hit]
    ev = [{**e, 'supports': True} for e in reversed(hit)] + [{**e, 'supports': False} for e in rest]
    return {**res, 'status': best['status'], 'holds': True, 'detail': best.get('detail') or {}, 'p': best.get('p'), 'evidence': ev}, best['status'], best.get('detail') or {}


def _response_part(nf, stack, pred, res, patient_id, as_of, ledger, allow_live):
    """"What was done about it?": read the same and later notes for a documented response to the findings and state each in words."""
    rps, since = response_predicates(pred, res, as_of)
    if not rps:
        return 'documented response: none to read, because no finding was established', [], [], []
    found, unsure, statuses = {}, 0, []
    for rp in rps:
        r = _read(nf, stack, _valid(nf, rp), patient_id, as_of, ledger, allow_live)
        statuses.append(r.get('status'))
        unsure += len(r.get('undecided_findings') or [])
        for g in r.get('findings') or []:
            for e in g['evidence']:
                key = re.sub(r'\W+', ' ', str(e.get('quote')).lower()).strip()
                found.setdefault(key, {'frame': rp['frame'], 'status': g['status'], 'detail': g.get('detail') or {}, 'e': e})
    if not found:
        tail = f' ({unsure} reading(s) undecided)' if unsure else ''
        return f'documented response (notes from {since} on): none documented in the notes read{tail}', [{'frame': 'response', 'status': 'not_mentioned'}], [], statuses
    parts, rows, sources = [], [], []
    for v in sorted(found.values(), key=lambda v: (v['e'].get('date') or '', v['e'].get('record_id') or ''))[:6]:
        e = v['e']
        parts.append(f"{v['frame'].replace('_', ' ')}: {WORDS.get(v['status'], v['status'])}{_detail_text(v['detail'])} | {_where(e)}: \"{_quote(e)}\"")
        rows.append({'frame': v['frame'], 'concept': 'response', 'status': v['status'], 'detail': v['detail'], 'records': [e.get('record_id')], 'matches_question': True})
        sources.append(Q.src(e.get('note_type') or 'clinical_note', e.get('record_id'), e.get('date'), quote=e.get('quote')))
    return f'documented response (notes from {since} on): ' + ' ; '.join(parts), rows, sources, statuses


def _read(nf, stack, pred, patient_id, as_of, ledger, allow_live):
    kw = {'as_of': as_of, 'ledger': ledger}
    if allow_live is not None:
        kw['allow_live'] = allow_live
    return nf.answer_patient(stack, pred, patient_id, **kw)


def _valid(nf, pred):
    try:
        return nf.validate_predicate(pred) if hasattr(nf, 'validate_predicate') else pred
    except ValueError:
        slim = {k: v for k, v in pred.items() if k not in ('detail', 'return_detail')}
        try:
            return nf.validate_predicate(slim)
        except ValueError:
            return slim
