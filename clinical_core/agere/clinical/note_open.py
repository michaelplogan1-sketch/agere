"""Open-concept note questions and source cues (docs/agere/NOTE_FRAMES.md, "Open concepts and source cues").

An OPEN question names a class instead of a thing: "What drug intolerance do the notes record?", "Anything in the notes about social or
financial barriers?", "What symptoms has the patient reported?". The reader must not use the class word ("intolerance") as if it were the
concept: it is not what the notes say. For an open concept this module supplies

  * the retrieval lexicon of the FRAME (cue words of adverse events, of each Gravity social domain, of symptoms, of a documented response),
    so passages are found by what they are about, not by the class word;
  * the class members the concept library knows (drugs and brands, social domains, symptoms), found by code in each passage, so every
    finding is NAMED from a closed list; a passage with a cue and no known member is named by its quoted sentence;
  * the detail fields worth reading for each finding.

System One still decides whether a finding is true: note_questions asks the usual typed status question once per named finding, and a
finding below the calibrated threshold is listed as undecided. Nothing here reads a note by itself or says a thing is present.

SOURCE cues ("the outside report", "the scanned ...", "HIE", "discharge summary", "the ED note", "radiology report / impression", "the
annual wellness visit") restrict the sources and note types a predicate reads (`source_cues`), so the typed chart request carries them.
"""
from __future__ import annotations

import re

VERSION = 'note-open/1'

# -- social domains: the Gravity SDOH domains as cue words (the keys are exactly the social_need `domain` options) ------------------
DOMAIN_CUES = {
    'food_insecurity': r"\bfood\b|grocer\w*|hungr\w*|hunger|pantry|\bsnap\b|\bmeals?\b|ran out of food",
    'housing_instability': r"housing|evict\w*|behind on rent|\brent\b|mortgage|couch[- ]surf\w*|doubled up",
    'homelessness': r"homeless\w*|unhoused|\bshelter\b|sleeping (?:in|on) (?:a |the )?(?:car|street)",
    'inadequate_housing': r"\bmold\b|pests?\b|infestation|no heat|unsafe housing|overcrowd\w*|substandard housing",
    'transportation_insecurity': r"transport\w*|rides? to|getting to (?:appointments|visits|clinic|the clinic)|no car|bus pass|missed (?:appointments|visits) because",
    'utility_insecurity': r"utilit(?:y|ies)|shut[- ]?off|electric(?:ity)? bill|heating bill|gas bill|no (?:electricity|running water)",
    'financial_strain': r"financ\w*|afford\w*|trouble paying|cannot pay|can't pay|unable to pay|\bmoney\b|income|unemploy\w*|\bdebt\b|\bbills\b",
    'medical_cost_burden': r"co-?pays?|cost of (?:medic\w*|care|prescriptions?|treatment)|out[- ]of[- ]pocket|too expensive|expensive|can(?:no|')t afford (?:his|her|their|the)? ?(?:medic\w*|prescriptions?)",
    'material_hardship': r"clothing|diapers|hygiene supplies|basic needs|material hardship",
    'employment': r"unemploy\w*|laid off|lost (?:his|her|their) job|job loss|out of work",
    'education_literacy': r"health literacy|low literacy|cannot read|trouble reading|limited schooling|illiterate",
    'social_isolation': r"isolat\w*|lonel\w*|lives alone|no (?:family|friends) nearby|no social support|alone at home",
    'stress': r"\bstress(?:ed|es|ors?)?\b|overwhelmed|financial stress",
    'interpersonal_violence': r"violence|\babus(?:e|ed|ive)\b|unsafe at home|afraid of (?:his|her|their) (?:partner|spouse)|domestic",
    'elder_abuse': r"elder abuse|neglect\w*|exploit\w*",
    'digital_access': r"no internet|internet access|no (?:smart)?phone|broadband|portal access|telehealth access",
    'language_barrier': r"interpreter|language barrier|limited english|primary language|speaks (?!english)\w+",
    'legal': r"\blegal\b|immigration|\bcourt\b|lawyer|custody",
    'child_care': r"child ?care|day ?care|babysit\w*",
    'caregiver_strain': r"caregiver (?:strain|burnout|stress)|burn(?:ed|t)[- ]?out",
    'incarceration': r"incarcerat\w*|\bjail\b|\bprison\b",
    'health_insurance': r"uninsured|no insurance|insurance (?:lapse|denied|denial|coverage)|coverage (?:lapse|denied)|lost (?:his|her|their) coverage",
    'discrimination': r"discriminat\w*|racis\w*",
}
# Domains the frame defines but a "barrier / need" question does not mean (they are history, not a barrier): reachable by naming them.
NOT_BARRIERS = ('caregiver_role', 'veteran_status', 'substance_use', 'other')
DOMAIN_LABELS = {k: k.replace('_', ' ') for k in DOMAIN_CUES}

# -- symptoms: a vocabulary (label -> cue) -----------------------------------------------------------------------------------------
SYMPTOMS = {
    'chest pain': r"chest (?:pain|pressure|tightness|discomfort)",
    'abdominal pain': r"abdominal (?:pain|discomfort)|epigastric (?:pain|discomfort)|stomach (?:pain|ache)",
    'back pain': r"back pain",
    'headache': r"headaches?",
    'joint pain': r"joint (?:pain|aches?)|arthralgias?|knee pain|hip pain|shoulder pain",
    'pain': r"(?<!chest )(?<!abdominal )(?<!back )(?<!joint )\bpain\b",
    'shortness of breath': r"shortness of breath|dyspnea|\bsob\b|breathless\w*|winded|short of breath",
    'cough': r"\bcough(?:ing)?\b",
    'fatigue': r"fatigue|tired(?:ness)?|low energy|exhaust\w*",
    'dizziness': r"dizz\w+|light[- ]?headed\w*|vertigo",
    'nausea': r"nausea|nauseous",
    'vomiting': r"vomit\w*|emesis",
    'diarrhea': r"diarrh\w+|loose stools?",
    'constipation': r"constipat\w+",
    'fever': r"\bfever\b|febrile|chills",
    'leg swelling': r"(?:leg|ankle|foot|pedal) (?:swelling|edema)|swelling (?:in|of) (?:the )?(?:legs?|ankles?|feet)|\bedema\b",
    'palpitations': r"palpitations?|racing heart|heart racing",
    'weakness': r"weakness|\bweak\b",
    'weight loss': r"weight loss|losing weight|unintentional weight",
    'weight gain': r"weight gain|gaining weight",
    'insomnia': r"insomnia|trouble sleeping|difficulty sleeping|poor sleep",
    'anxiety': r"\banxi\w+|\bpanic\b",
    'low mood': r"depressed mood|low mood|feeling (?:sad|down|depressed)|anhedonia",
    'rash': r"\brash\b|hives|itching|pruritus",
    'wheezing': r"wheez\w+",
    'syncope': r"syncop\w+|fainting|passed out|blackout",
    'numbness or tingling': r"numbness|tingling|paresthesias?",
    'vision change': r"blurred vision|blurry vision|vision (?:change|loss)",
    'urinary symptoms': r"dysuria|urinary (?:frequency|urgency|incontinence)|burning with urination|polyuria",
    'bleeding': r"\bbleeding\b|blood in (?:stool|urine)|hematuria|melena",
    'heartburn': r"heartburn|reflux|dyspepsia",
    'loss of appetite': r"loss of appetite|poor appetite|decreased appetite",
    'memory problems': r"memory (?:loss|problems?|concern)|forgetful\w*",
    'cramps': r"cramps?|myalgias?|muscle (?:pain|aches?)",
}
SYMPTOM_FRAME_CUE = r"complain(?:s|ed|ing|t)? of|\bc/o\b|presents? with|symptoms? of"

# -- adverse events ----------------------------------------------------------------------------------------------------------------------------
ADVERSE_CUES = (r"intoleran\w*", r"allerg\w*", r"side[- ]effects?", r"adverse (?:reactions?|events?|effects?|drug)", r"\breactions? to\b",
                r"\bhives\b|angioedema|anaphyla\w*", r"(?:not|poorly|unable to|did not|didn't|cannot|can't) tolerat\w*|\btolerat\w+",
                r"\bcaus(?:ed|es|ing)\b", r"(?:stopped|discontinued|held|d/c'?d) (?:due to|because|for|secondary to)",
                r"(?:due to|secondary to) (?:cough|rash|hives|angioedema|dizziness|hyperkal\w*|myalgia|nausea|swelling)")
ALLERGENS = {  # agents that are not in the drug-class library but are common allergy / intolerance entries
    'penicillin': r"penicillin\w*", 'amoxicillin': r"amoxicillin", 'sulfa': r"sulfa\b|sulfonamide\w*|bactrim|trimethoprim",
    'cephalosporin': r"cephalosporin\w*|cephalexin|ceftriaxone", 'latex': r"\blatex\b", 'peanut': r"\bpeanuts?\b",
    'shellfish': r"shellfish", 'iodine or contrast': r"\biodine\b|contrast dye|contrast media", 'NSAIDs': r"\bnsaids?\b|ibuprofen|naproxen",
    'erythromycin': r"erythromycin", 'doxycycline': r"doxycycline|tetracycline", 'egg': r"\beggs?\b",
}
# -- documented response (what was done about a finding) -----------------------------------------------------------------------
RESPONSE_CUES = (r"referr\w+", r"resources?\b", r"counsel\w*", r"\bprovided\b", r"connect(?:ed|ion)? (?:to|with)", r"arrang\w+", r"assist\w*",
                 r"\benroll\w*", r"follow[- ]?up", r"schedul\w+", r"educat\w+", r"social work\w*", r"case manage\w*", r"community (?:health|resources?|program)",
                 r"\baddressed\b", r"prescrib\w+", r"\b(?:started|stopped|changed|switched|increased|decreased|held|discontinued)\b",
                 r"recommend\w+")
RESPONSE_PART = re.compile(r"\bwhat (?:was|were|has been|had been) done\b|\bdone about\b|\bwhat (?:action|steps?|measures?)\b|\bhow (?:was|were|has) (?:it|that|this|they|the \w+) (?:addressed|handled|managed|treated|dealt with)\b|"
                           r"\bwhat (?:was|were) the (?:response|plan|follow[- ]?up)\b|\bwas anything done\b|\bwhat did (?:the \w+|they|we|\w+) do about\b|\bwhat happened (?:next|after)\b|"
                           r"\banything (?:done|arranged|ordered|provided)\b|\bresponse\b", re.I)
OPEN_DETAILS = {  # fields read per finding (choice fields; cheap, one call each)
    'adverse_event': ['kind', 'severity', 'outcome', 'reaction'],
    'symptom': ['severity', 'course', 'trigger'],
    'social_need': ['response', 'severity'],
    'care_process': ['kind', 'outcome'],
    'plan_followup': ['item', 'timeframe'],
    'condition': ['course', 'severity'],
}

# -- which question phrasings are open -------------------------------------------------------------------------------------------
# (frame, class, pattern on the asking sentence). A specific agent ("allergic to penicillin", "intolerance to metformin") is NOT open.
SPECIFIC_AGENT = re.compile(r"(?:allerg\w+|intoleran\w+|reaction|sensitiv\w+|side effects?|adverse \w+)\s+(?:to|from|of|with|after|on)\s+(?!any\b|a\b|the notes\b)", re.I)
OPEN_PHRASES = [
    ('adverse_event', 'adverse', r"\b(?:drug|medication|medicine|food|any)?\s*(?:intoleran\w+|allerg(?:y|ies|ic)|side effects?|adverse (?:drug )?(?:reactions?|events?|effects?)|reactions?)\b"),
    ('social_need', 'social', r"\bsocial (?:or|and|/) (?:financial|economic)\b|\bfinancial (?:or|and) social\b|\bsocial (?:needs?|barriers?|issues?|concerns?|determinants?|risk factors?|history)\b|"
                             r"\b(?:financial|economic) (?:barriers?|strain|needs?|hardships?|problems?|concerns?)\b|\bbarriers? to (?:care|access)\b|\b(?:what|any) barriers?\b|\bbarriers?\b"),
    ('symptom', 'symptom', r"\bsymptoms?\b|\bcomplaints?\b|\bcomplain\w*\b"),
]
GENERIC_WORDS = {'intolerance', 'intolerances', 'allergy', 'allergies', 'reaction', 'reactions', 'side effect', 'side effects', 'adverse', 'symptom',
                 'symptoms', 'complaint', 'complaints', 'barrier', 'barriers', 'financial', 'social', 'any', 'anything'}


def open_class(frame, ask):
    """-> (class, phrase) when the asking sentence names a CLASS for this frame instead of a thing, else None. The phrase is the user's own words."""
    for fr, cls, pat in OPEN_PHRASES:
        if fr != frame:
            continue
        m = re.search(pat, ask or '', re.I)
        if not m:
            continue
        if fr == 'adverse_event' and SPECIFIC_AGENT.search(ask):
            return None
        phrase = re.sub(r'\s+', ' ', m.group(0)).strip()
        if fr == 'symptom':
            phrase = 'symptoms'
        if fr == 'social_need':
            # the widest wording the user used ("social or financial barriers")
            wide = re.search(r"(?:social|financial|economic)(?: (?:or|and|/) (?:social|financial|economic))? (?:barriers?|needs?|issues?|concerns?|determinants?|strain|hardships?|problems?)", ask, re.I)
            phrase = wide.group(0) if wide else phrase
        return cls, phrase
    return None


ASK_CLASS = {  # how the class is put to the reader when no library member names the finding (the passage is read for ANY member of the class)
    'adverse': '{} (any drug, food or other agent the patient is allergic or intolerant to, or reacts badly to, even if the name is misspelled)',
    'social': '{} (any social or financial need or barrier the patient has)',
    'symptom': '{} (any symptom the patient has or reports)',
    'response': '{} (anything the care team did, arranged, ordered or advised in response)',
}


# -- retrieval patterns ------------------------------------------------------------------------------------------------------------------
def patterns(frame, cls, *, only_domains=None):
    """Regex strings that retrieve passages for an open concept of this frame (the frame's cue lexicon)."""
    if cls == 'adverse':
        return list(ADVERSE_CUES)
    if cls == 'social':
        keep = only_domains or [d for d in DOMAIN_CUES if d not in NOT_BARRIERS]
        return [DOMAIN_CUES[d] for d in keep]
    if cls == 'symptom':
        return [SYMPTOM_FRAME_CUE] + list(SYMPTOMS.values())
    if cls == 'response':
        return list(RESPONSE_CUES)
    return []


# -- class members found in a passage ------------------------------------------------------------------------------------------------
def _sentence_of(text, pos):
    """The line / sentence around a character position (the same splitter the passages use)."""
    from . import note_evidence
    for start, end, sent in note_evidence.sentences(text):
        if start <= pos < end:
            return sent
    return text[max(0, pos - 80):pos + 160].strip()


def _cue_positions(text, cue_patterns):
    out = []
    for p in cue_patterns:
        for m in re.finditer(p, text, re.I):
            out.append(m.start())
    return out


def drug_members(text):
    """Drugs (generic ingredients and brands) named in `text`: [(label, position)] in order of appearance."""
    out, seen = [], set()
    try:
        from . import drug_classes as DCL
        for w in DCL.ingredients(text):
            m = re.search(r'\b' + re.escape(w) + r'\w*', text, re.I)
            if m and w not in seen:
                seen.add(w)
                out.append((w, m.start()))
        for b in DCL.brand_lookup(text):
            m = re.search(re.escape(b['brand']), text, re.I)
            generic = (b.get('ingredients') or [None])[0]
            label = (generic or b['brand']).lower()
            if m and label not in seen and b['brand'].lower() not in seen:
                seen.add(label)
                out.append((label, m.start()))
    except Exception:  # noqa: BLE001 - the drug library is an enrichment; allergens and quotes still work
        pass
    for label, rx in ALLERGENS.items():
        m = re.search(rx, text, re.I)
        if m and label.lower() not in seen:
            seen.add(label.lower())
            out.append((label, m.start()))
    return sorted(out, key=lambda t: t[1])


def members(frame, cls, text, *, limit=5):
    """Class members present in a passage, nearest to a cue word first: [{'label','source','quote'}]. `source` is 'library' (a closed list
    the concept library owns) and `quote` the sentence that names it. Empty when the passage has a cue but no known member (then the quoted
    cue sentence names the finding)."""
    found = []
    cues = _cue_positions(text, patterns(frame, cls))
    if cls == 'adverse':
        raw = drug_members(text)
    elif cls == 'social':
        raw = []
        for d, rx in DOMAIN_CUES.items():
            if d in NOT_BARRIERS:
                continue
            m = re.search(rx, text, re.I)
            if m:
                raw.append((DOMAIN_LABELS[d], m.start()))
    elif cls == 'symptom':
        raw = []
        for label, rx in SYMPTOMS.items():
            m = re.search(rx, text, re.I)
            if m:
                raw.append((label, m.start()))
        # "chest pain" also contains "pain": the generic label only counts when no specific pain label matched
        if any(l.endswith('pain') and l != 'pain' for l, _ in raw):
            raw = [(l, p) for l, p in raw if l != 'pain']
    else:
        raw = []
    for label, pos in raw:
        dist = min([abs(pos - c) for c in cues] or [0])
        found.append((dist, pos, label))
    found.sort()
    return [{'label': label, 'source': 'library', 'quote': _sentence_of(text, pos)[:300], 'position': pos} for _, pos, label in found[:limit]]


def cue_sentence(frame, cls, text):
    """The quoted span that names a finding no library member names: the first sentence with a cue word."""
    from . import note_evidence
    sents = note_evidence.sentences(text)
    for p in patterns(frame, cls):
        m = re.search(p, text, re.I)
        if m:
            for i, (start, end, sent) in enumerate(sents):
                if start <= m.start() < end:
                    # a bare header line ("ALLERGIES") is named by the line under it
                    if len(sent) < 25 and not re.search(r'[.;]$', sent) and i + 1 < len(sents):
                        return (sent + ': ' + sents[i + 1][2])[:300]
                    return sent[:300]
            return _sentence_of(text, m.start())[:300]
    return text.strip().split('\n')[0][:300]


# -- source cues -----------------------------------------------------------------------------------------------------------------------------
NOTE_TYPES = {  # closed names a predicate may carry in `note_types` -> pattern over "note_type stream title"
    'discharge_summary': r"discharge[ _]summar|received_hospital_summaries|home_health_case_summaries|transition_of_care",
    'ed_note': r"\bed[ _](?:summary|note|visit)|emergency|urgent[ _]care",
    'radiology_report': r"radiolog|imaging|x-?ray|diagnostic imaging|received_diagnostic_reports|diagnostic_test_reports",
    'pathology_report': r"patholog|biops",
    'procedure_report': r"procedure report|endoscopy|surgery|operative|inpatient_cardiac_reports|received_procedure_reports",
    'consult_note': r"consult|specialist letter|specialty_record_reviews",
    'outside_report': r"outside|facility records|received_|imported_documents|correspondence|specialist letter",
    'annual_wellness_visit': r"annual|wellness|preventive",
    'telephone_message': r"telephone|phone|callback|contacts?\b",
    'admission_note': r"admission|h&p|history_and_physical|inpatient_progress",
    'nursing_note': r"nursing",
    'lab_report': r"laborator|lab results|lab_",
}
SOURCE_CUE_TABLE = [  # (question pattern, sources or None, note type names)
    (r"\boutside (?:\w+ ){0,3}(?:report|records?|document|study|film|imaging|x-?ray|facility|hospital|lab\w*)\b|\boutside\b|\bexternal (?:report|record)s?\b|\bfaxed?\b|\bsent over\b|\breceived from\b",
     ['scanned_document_text', 'hie_document', 'other_text'], []),
    (r"\bscanned\b|\bocr\b|\bpdf\b|\bfax(?:ed)?\b|\bphoto(?:graph)?\b", ['scanned_document_text'], []),
    (r"\bhie\b|health information exchange|care ?everywhere|\bccd\b|\bc-?cda\b|exchange document", ['hie_document'], []),
    (r"\bdischarge summar(?:y|ies)\b|\bdischarge (?:paperwork|document)s?\b", None, ['discharge_summary']),
    (r"\bED (?:note|summary|visit note|record|report)s?\b|\bemergency (?:department|room) (?:note|summary|record|report)s?\b|\bER (?:note|summary)\b", None, ['ed_note']),
    (r"\bradiology (?:report|impression|read)s?\b|\bx-?ray (?:report|impression|film)s?\b|\bimaging report\b|\b(?:ct|mri|ultrasound) report\b", None, ['radiology_report']),
    (r"\bpathology (?:report|result)s?\b|\bbiopsy report\b", None, ['pathology_report']),
    (r"\b(?:procedure|operative|endoscopy|colonoscopy) report\b", None, ['procedure_report']),
    (r"\bconsult(?:ation)? (?:note|letter|report)s?\b|\bspecialist (?:letter|note|report)s?\b", None, ['consult_note']),
    (r"\bannual (?:wellness|preventive|physical|exam)\b|\bwellness visit\b|\bAWV\b", None, ['annual_wellness_visit']),
    (r"\b(?:telephone|phone|call) (?:note|message|encounter|call)s?\b|\bcall(?:back)? note\b", None, ['telephone_message']),
    (r"\badmission (?:note|h&p)|\bhistory and physical\b|\bH&P\b", None, ['admission_note']),
    (r"\bnursing notes?\b", None, ['nursing_note']),
    (r"\blab(?:oratory)? (?:report|result)s?\b", None, ['lab_report']),
]
SOURCE_ORDER = ('clinical_note', 'scanned_document_text', 'hie_document', 'other_text')


def source_cues(text):
    """Source and note-type restrictions the wording implies: {'sources': [...] | None, 'note_types': [names]}. Several cues combine: sources
    intersect when more than one names them, note types accumulate."""
    sources, types = None, []
    for pat, srcs, nts in SOURCE_CUE_TABLE:
        if re.search(pat, text or '', re.I):
            if srcs:
                sources = [s for s in SOURCE_ORDER if s in srcs and (sources is None or s in sources)] or sources
            types += [t for t in nts if t not in types]
    return {'sources': sources, 'note_types': types}


def type_regex(names):
    """One compiled alternation over the note-type names (or None)."""
    pats = [NOTE_TYPES[n] for n in names or [] if n in NOTE_TYPES]
    return re.compile('|'.join(f'(?:{p})' for p in pats), re.I) if pats else None
