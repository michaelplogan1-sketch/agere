"""Bounded contextual assertions, with exact source spans and review proposals.

This is a transparent rules layer, NOT a generally trained clinical NLP model.
Unrecognized text remains available in retrieval. Supported assertions are not
validated diagnoses. Mention time, record time and physiology time are distinct.
"""
import re
from .narrative_sources import text_fields, coverage

VERSION = 'contextual-narrative/1'
PATTERNS = {
    'ef': r'\b(?:LVEF|EF|(?:left ventricular )?ejection fraction)\s*(?:(?:is|was|of|at|estimated(?: at)?|approximately|about|:|=)\s*)*(?P<value>\d{1,3}(?:\.\d+)?)\s*(?:[-–]\s*(?P<upper>\d{1,3}(?:\.\d+)?)\s*)?%',
    'financial_hardship': r'\b(?:cannot afford|can\x27t afford|unable to (?:afford|pay)|difficulty paying|cost (?:barrier|concern)|too expensive)\b',
    'transport_barrier': r'\b(?:transportation barrier|no (?:ride|transportation)|unable to (?:get to|attend)|lack of transportation)\b',
    'outside_care': r'\b(?:out.of.network|outside (?:lab(?:oratory)?|imaging|specialist|pharmacy|record)s?|external (?:lab|record)s?)\b',
    'goals_of_care': r'\b(?:palliative|hospice|goals of care|comfort.focused|patient preference)\b',
    'treatment_exception': r'\b(?:contraindicat(?:ed|ion)|intoleran(?:t|ce)|declined (?:treatment|medication|therapy)|medication (?:held|stopped)|held (?:due to|because of))\b',
}
COMPILED = {k: re.compile(v, re.I) for k, v in PATTERNS.items()}
BOUNDARY = re.compile(r'[;\n]|(?<=[.!?])\s+|\s/\s|\b(?:but|however|whereas)\b', re.I)
FAMILY = r'\b(?:mother|father|brother|sister|son|daughter|spouse|wife|husband|aunt|uncle|grandmother|grandfather)\b'
PATIENT = r'\b(?:patient|pt)\b|\b(?:his|her|their)\s+(?=(?:current\s+)?(?:EF|LVEF|ejection fraction)\b)'


def _context(text, match, field):
    boundaries = list(BOUNDARY.finditer(text))
    start = max((b.end() for b in boundaries if b.end() <= match.start()), default=0)
    end = min((b.start() for b in boundaries if b.start() >= match.end()), default=len(text))
    # A later measurement cannot donate its negation/retraction to this measurement.
    later = COMPILED['ef'].search(text, match.end(), end)
    if later:
        end = later.start()
    prefix = text[start:match.start()]; suffix = text[match.end():end]
    # Restrict local assertion cues to the last coordinating subject change.
    anchors = [(m.start(), 'other_person') for m in re.finditer(FAMILY, prefix, re.I)]
    anchors += [(m.start(), 'patient') for m in re.finditer(PATIENT, prefix, re.I)]
    subject = max(anchors)[1] if anchors else 'patient_context'
    # Heading carries until a new explicit section. No open-ended pronoun resolution.
    before = text[:match.start()]
    headings = list(re.finditer(r'(?:^|\n)\s*(family history|assessment|plan|hpi|social history|impression)\s*:', before, re.I))
    section = headings[-1][1].lower() if headings else field
    if ('family_history' in field or section == 'family history') and subject == 'patient_context':
        subject = 'other_person'
    if not anchors and re.search(r'\b(?:he|she|they)\b', prefix, re.I):
        subject = 'unresolved_pronoun'
    if anchors:
        prefix = prefix[max(anchors)[0]:]
    # Only forward-looking examples, not conditional words in distant clauses.
    uncertain = re.search(r'\b(?:possible|possibly|suspected|uncertain|unconfirmed|question of|rule out|r/o|cannot exclude|can\x27t exclude)\b', prefix, re.I)
    hypothetical = re.search(r'\b(?:if|hypothetical|example|educational|would consider)\b', prefix, re.I)
    negation_prefix = re.sub(r'\bno (?:ride|transportation)\b', 'transport barrier', prefix, flags=re.I)
    negative = re.search(r'\b(?:no|not|denies|denied|without|negative for)\b(?!\s+(?:change|doubt))', negation_prefix, re.I)
    # Broad error cues elsewhere in a paragraph must not invalidate every assertion.
    retracted = re.search(r'^\s*(?:was |is |has been )?(?:entered |documented |recorded |reported )?(?:in error|incorrect|erroneous|retracted|a typo)\b', suffix, re.I)
    retracted = retracted or re.search(r'\b(?:erroneous|incorrect|retracted)\s*$', prefix, re.I)
    post_negative = re.search(r'^\s*(?:is |was )?(?:not (?:present|observed|confirmed)|ruled out|denied)\b', suffix, re.I)
    boolean_negative = re.search(r'^\s*[^:;\n]{0,60}:\s*(?:false|no|negative)\b', suffix, re.I)
    boolean_unknown = re.search(r'^\s*[^:;\n]{0,60}:\s*(?:unknown|not assessed|not recorded|unavailable)\b', suffix, re.I)
    status = 'retracted' if retracted else 'hypothetical' if hypothetical else 'uncertain' if uncertain or boolean_unknown else 'negated' if negative or post_negative or boolean_negative else 'affirmed'
    historical = re.search(r'\b(?:prior|previous|previously|historical|history of|formerly|used to|last year|in 20\d\d)\b', prefix, re.I)
    resolved = re.search(r'^\s*(?:is |was |has been )?(?:resolved|no longer present)\b', suffix, re.I)
    temporality = 'resolved' if resolved else 'historical' if historical else 'unspecified'
    if re.search(r'\b(?:current|currently|today|now)\b', prefix, re.I):
        temporality = 'current_statement'
    return {'subject': subject, 'assertion': status, 'temporality': temporality,
            'context_start': start, 'context_end': end, 'context': text[start:end],
            'subject_basis': 'local lexical cues; patient_context is an assumption, not coreference proof'}


def candidate(mention):
    return (mention['assertion'] == 'affirmed' and mention['subject'] in {'patient', 'patient_context'}
            and mention['temporality'] != 'resolved')


def read(records):
    groups = {}
    for r in records:
        for part in text_fields(r):
            text = part['text']
            for concept, pattern in COMPILED.items():
                for match in pattern.finditer(text):
                    value = float(match['value']) if concept == 'ef' else None
                    upper = float(match['upper']) if concept == 'ef' and match['upper'] else value
                    if concept == 'ef' and not 0 < value <= upper <= 100:
                        continue
                    context = _context(text, match, part['field'])
                    ev = {'record_id': r['record_id'], 'source_table': r['source_table'],
                          'event_date': r['event_date'], 'available_date': r['available_date'],
                          'field': part['field'], 'start': match.start(), 'end': match.end(),
                          'quote': match[0], **context}
                    # Dedupe the assertion + context, never combine unrelated evidence as independent votes.
                    key = (concept, re.sub(r'\s+', ' ', context['context'].strip().lower()),
                           value, upper, context['subject'], context['assertion'], context['temporality'])
                    if key in groups:
                        groups[key]['occurrences'].append(ev)
                        continue
                    groups[key] = {'concept': concept, 'value': value, 'upper_value': upper,
                                   'evidence': {k: ev[k] for k in ('record_id','source_table','event_date','available_date','field')},
                                   'quote': match[0], 'start': match.start(), 'end': match.end(),
                                   **context, 'status': 'contextual_text_candidate', 'occurrences': [ev]}
    mentions = list(groups.values())
    for m in mentions:
        m['candidate_for_review'] = candidate(m)
        m['repeat_count'] = len(m['occurrences'])
        m['independent_confirmation_count'] = None
    return {'version': VERSION, 'method': 'bounded source-span rules; arbitrary text retained for specialist retrieval',
            'mentions': mentions, 'coverage': coverage(records),
            'understanding_complete': False,
            'care_gap_from_absence': False}


def review_actions(narrative):
    actions = []
    action_map = {
        'financial_hardship': ('verify_financial_support_need', 'Reconcile the reported affordability barrier and assess assistance eligibility.'),
        'transport_barrier': ('verify_transport_access', 'Verify the reported transport barrier and available access options.'),
        'outside_care': ('reconcile_outside_evidence', 'Locate and reconcile the relevant outside record before classifying a care gap.'),
        'goals_of_care': ('review_goals_context', 'Review documented preferences and clinical context; a mention does not establish palliative eligibility.'),
        'treatment_exception': ('reconcile_treatment_exception', 'Verify the medication or treatment, reason, timing and continuing relevance of the reported exception.'),
    }
    for concept, (kind, description) in action_map.items():
        evidence = [m for m in narrative['mentions'] if m['concept'] == concept and candidate(m)]
        if evidence:
            actions.append({'kind': kind, 'status': 'evidence_review_proposal', 'description': description,
                            'evidence': evidence,
                            'contrary_mentions': [m for m in narrative['mentions'] if m['concept']==concept and m['assertion']=='negated' and m['subject'] in {'patient','patient_context'}],
                            'execution_authorized': False,
                            'current_need_established': False})
    return actions
