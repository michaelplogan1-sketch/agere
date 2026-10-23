"""Note frames: typed readings of clinical text (spec: docs/agere/QUESTION_SPEC.md, Addition 1; coverage: docs/agere/NOTE_FRAMES.md).

A question about what a chart SAYS ("was metformin stopped because of side effects", "any documented fall", "is the code status
recorded") becomes a `note` predicate: one of twelve fixed FRAMES, a concept, the shared assertion STATUSES, optional frame
DETAIL fields (closed option lists), a window and a COMBINE rule. System One (Jev, then Perplexity when unsure) reads each
retrieved passage with typed questions whose options are exactly the lists below; code reduces the passage readings to one
patient value with COMBINE and never turns an undecided patient into "no": the result is status 'unknown', and a count over
patients becomes a range.

  FRAMES              frame -> {label, stem, concept_kind, details{field: {kind, options, multi, ask, ...}}, default_combine}
  STATUSES            the seven assertion options, one precise definition each (identical in every frame)
  COMBINE             any | latest | first | trend | conflict | all
  validate_predicate  raises ValueError (messages say what to fix); returns the normalized predicate
  evaluate()          {patient_id: {status, detail, evidence, p, ...}} for many patients (executor, calls System One)
  answer_patient()    the same shape for one patient (every frame detail field is read, not only the constrained ones)

Passage selection is in note_evidence.py, the System One questions and the combine code in note_questions.py, the per-frame
acceptance thresholds and log-odds shifts in config/agere/note_frames_calibration.json.
"""
from __future__ import annotations

import re
from datetime import date

VERSION = 'note-frames/1'

STATUSES = {
    'documented': 'The passage states that the PATIENT has, had in this encounter, did, or received the thing now: an affirmative '
                  'statement about the patient that is current as of the note date (or an event the note reports as having occurred '
                  'at or just before the note date).',
    'denied': 'The passage explicitly states the thing is absent or did not happen for the patient: "denies", "no", "not taking", '
              '"negative for", "does not recognize this as a current medicine", "NKDA". Silence is not denial.',
    'possible': 'The passage is uncertain whether the patient has or did it: suspected, probable, rule-out pending, differential, '
                '"concern for", "may have", "cannot exclude", a question that remains open.',
    'planned_not_done': 'The passage recommends, orders, schedules, offers, requests or intends the thing for the patient but does '
                        'not show it carried out, including offered-and-declined, due, overdue, or deferred.',
    'historical': 'The passage places the thing in the patient\'s past and not as current at the note date: "history of", '
                  '"resolved", "previously", "in 2027", "was on X until March", a closed episode.',
    'other_person': 'The passage is about someone other than the patient (family member, relative, caregiver, household member).',
    'not_mentioned': 'The passage says nothing about this concept for the patient: a retrieval miss, or only a hypothetical or '
                     'conditional mention ("return if fever", "evaluate for X", risk teaching) that does not state the patient has it.',
}
RESULT_STATUSES = tuple(STATUSES) + ('unknown',)  # 'unknown' is a result only: undecided, never silently "no"
POSITIVE = ('documented', 'possible', 'planned_not_done', 'historical')  # statuses that carry detail fields
# Precedence when one patient value must be named from several passage statuses (any/latest tie-breaks, reporting).
PRECEDENCE = ('documented', 'possible', 'planned_not_done', 'historical', 'denied', 'other_person', 'not_mentioned')

COMBINE = {
    'any': 'The predicate holds when ANY retrieved passage reads with a status in `statuses` (and matching detail).',
    'latest': 'The most recent dated passage that addresses the concept decides the patient value.',
    'first': 'The earliest dated passage that addresses the concept decides the patient value.',
    'trend': 'Numeric values in dated passages are ordered by date; the patient value is the direction of change '
             '(increased | decreased | stable) with first and last values. Needs two dated values.',
    'conflict': 'Holds when passages address the concept with contradictory readings (affirmed in one, denied in another, or '
                'different values); the patient value is documented when a conflict exists.',
    'all': 'Holds only when EVERY passage that addresses the concept has a status in `statuses` (at least one must).',
}
SOURCES = ('clinical_note', 'scanned_document_text', 'hie_document', 'other_text')

# Detail kinds: choice (closed list, one), multi (closed list, several), number (value picked from numbers in the passage),
# time (HH:MM picked from clock times in the passage), date (picked from dates in the passage or the note date), count (integer picked from the passage), quote (the exact sentence).
_SEV = ['mild', 'moderate', 'severe', 'unspecified']
_COURSE = ['new', 'in_remission', 'persistent', 'worsening', 'improving', 'stable', 'resolved', 'intermittent', 'unspecified']
_TIME = 'The clock time written for it (for example 14:35), if the passage gives one.'
_REASON_NOT = ['patient_refusal', 'medical_contraindication', 'system_or_access_reason', 'not_indicated', 'clinician_decision', 'other_with_quote']


def _d(kind, options=None, ask='', **extra):
    return {'kind': kind, 'options': list(options or []), 'ask': ask, **extra}


FRAMES = {
    'condition': {
        'label': 'Condition or diagnosis',
        'stem': 'Does the passage say the patient has (or had) the condition',
        'concept_kind': 'condition',
        'default_combine': 'any',
        'details': {
            'acuity': _d('choice', ['acute', 'chronic', 'unspecified'], 'Is the condition described as acute, chronic, or neither?'),
            'severity': _d('choice', _SEV, 'How severe is the condition described to be?'),
            'course': _d('choice', _COURSE, 'How is the condition described to be changing?'),
            'assessment': _d('multi', ['monitored', 'evaluated', 'assessed', 'treated'],
                             'Which of these does the passage show was done about the condition (MEAT: monitor, evaluate, assess, treat)?'),
            'stage_or_type': _d('quote', [], 'The words giving the type, stage or class of the condition.'),
            'event_time': _d('time', [], _TIME),
        }},
    'symptom': {
        'label': 'Symptom or complaint',
        'stem': 'Does the passage say the patient reports or has the symptom',
        'concept_kind': 'symptom',
        'default_combine': 'any',
        'details': {
            'severity': _d('choice', _SEV, 'How severe is the symptom described to be?'),
            'course': _d('choice', _COURSE, 'How is the symptom described to be changing?'),
            'trigger': _d('choice', ['exertion', 'rest', 'positional', 'meal_related', 'medication_related', 'none_described'],
                          'What brings the symptom on, if the passage says?'),
            'duration': _d('quote', [], 'The words giving how long the symptom has lasted.'),
            'event_time': _d('time', [], _TIME),
        }},
    'medication_event': {
        'label': 'Medication event',
        'stem': 'Does the passage describe a medication event (start, stop, dose change, hold, switch, continuation, taking)',
        'concept_kind': 'medication',
        'default_combine': 'latest',
        'details': {
            'event': _d('choice', ['started', 'stopped', 'changed_dose', 'held', 'switched', 'continued'],
                        'Which medication event does the passage describe for this medicine?'),
            'reason': _d('multi', ['side_effect', 'cost', 'ineffective', 'patient_choice', 'contraindication', 'course_completed',
                                   'clinician_decision', 'no_longer_indicated', 'other_with_quote'],
                         'What reason does the passage give for the event? Choose every reason stated.'),
            'direction': _d('choice', ['increased', 'decreased', 'unspecified'], 'For a dose change, was the dose raised or lowered?'),
            'adherence': _d('choice', ['taking_as_directed', 'taking_differently', 'not_taking', 'not_stated'],
                            'What does the passage say about whether the patient actually takes it?'),
            'initiated_by': _d('choice', ['clinician', 'patient', 'pharmacy_or_plan', 'unspecified'], 'Who made the change?'),
            'reason_not_done': _d('choice', _REASON_NOT, 'If it was not given or prescribed, why not?'),
            'amount': _d('number', [], 'Which number is the dose or amount given or ordered?'),
            'sig': _d('quote', [], 'The dose, route and frequency as written (the signature line).'),
            'event_time': _d('time', [], _TIME),
        }},
    'procedure_test': {
        'label': 'Procedure, test or study',
        'stem': 'Does the passage describe the procedure, test or study being ordered, done or reported',
        'concept_kind': 'procedure',
        'default_combine': 'latest',
        'details': {
            'category': _d('choice', ['imaging', 'laboratory', 'pathology_biopsy', 'endoscopy', 'cardiac_test', 'surgery_procedure',
                                      'screening', 'other'], 'What kind of procedure or test is it?'),
            'result': _d('choice', ['normal', 'abnormal', 'indeterminate', 'pending', 'not_reported'],
                         'What result or interpretation does the passage report?'),
            'result_text': _d('quote', [], 'The words giving the result or impression.'),
            'performed_on': _d('date', [], 'On what date was it performed?'),
            'reason_not_done': _d('choice', _REASON_NOT, 'If it was not done, why not?'),
            'event_time': _d('time', [], _TIME),
        }},
    'finding_value': {
        'label': 'Measured value or score',
        'stem': 'Does the passage report a measured value, score or finding for the patient',
        'concept_kind': 'measurement',
        'default_combine': 'latest',
        'details': {
            'value': _d('number', [], 'Which number is the value of this measurement?'),
            'unit': _d('quote', [], 'The unit written next to the value, if any.'),
            'date': _d('date', [], 'On what date was the value measured?'),
            'kind': _d('choice', ['vital_sign', 'laboratory', 'screening_score', 'imaging_measure', 'anthropometric', 'other'],
                       'What kind of measurement is it?'),
            'interpretation': _d('choice', ['normal', 'abnormal', 'high', 'low', 'at_goal', 'not_at_goal', 'not_stated'],
                                 'How does the passage interpret the value?'),
            'event_time': _d('time', [], _TIME),
        }},
    'adverse_event': {
        'label': 'Adverse event, allergy or intolerance',
        'stem': 'Does the passage describe the adverse event, allergy, intolerance or injury for the patient',
        'concept_kind': 'adverse_event',
        'default_combine': 'any',
        'details': {
            'kind': _d('choice', ['allergy', 'intolerance', 'side_effect', 'procedural_complication', 'fall', 'medication_error',
                                  'hospital_acquired', 'other'], 'What kind of adverse event is it?'),
            'agent': _d('quote', [], 'The medicine, food or cause named.'),
            'reaction': _d('quote', [], 'The reaction described.'),
            'severity': _d('choice', ['mild', 'moderate', 'severe', 'life_threatening', 'unspecified'], 'How severe was it?'),
            'outcome': _d('choice', ['resolved', 'ongoing', 'hospitalization', 'death', 'unknown'], 'What was the outcome?'),
            'count': _d('count', [], 'How many times did it happen in the period the passage covers (a whole number)?'),
        }},
    'care_process': {
        'label': 'Care process or service',
        'stem': 'Does the passage document the care process (referral, counseling, goals-of-care talk, directive, coordination, '
                'discharge teaching, screening, immunization, rehabilitation)',
        'concept_kind': 'care_process',
        'default_combine': 'any',
        'details': {
            'kind': _d('choice', ['referral', 'education_counseling', 'goals_of_care_discussion', 'advance_directive', 'code_status',
                                  'care_plan', 'care_coordination', 'transition_of_care', 'medication_reconciliation',
                                  'discharge_instruction', 'follow_up_visit', 'palliative_hospice', 'home_health', 'rehabilitation',
                                  'screening_performed', 'immunization_given', 'case_management', 'interpreter_used', 'other'],
                       'Which kind of care process is it?'),
            'code_status': _d('choice', ['full_code', 'dnr', 'dni', 'dnr_dni', 'comfort_measures_only', 'limited_interventions',
                                         'not_specified'], 'What code status or resuscitation preference does the passage record?'),
            'directive': _d('choice', ['living_will', 'polst_molst', 'healthcare_proxy', 'dnr_order', 'none_on_file'],
                            'Which advance-care document does the passage name?'),
            'outcome': _d('choice', ['completed', 'ordered_or_referred', 'declined', 'unable', 'deferred'],
                          'What happened with it?'),
            'done_on': _d('date', [], 'On what date was it done?'),
            'topics': _d('multi', ['risk_factors', 'warning_signs', 'emergency_activation', 'follow_up', 'medications', 'diet',
                                   'activity', 'smoking_cessation', 'self_monitoring', 'other'],
                         'For teaching or counseling: which topics does the passage say were covered?'),
            'event_time': _d('time', [], _TIME),
            'reason_not_done': _d('choice', _REASON_NOT, 'If it was not done, why not?'),
        }},
    'social_need': {
        'label': 'Social need or barrier',
        'stem': 'Does the passage say the patient has the social need or barrier',
        'concept_kind': 'social_need',
        'default_combine': 'any',
        'details': {
            'domain': _d('multi', ['food_insecurity', 'housing_instability', 'homelessness', 'inadequate_housing',
                                   'transportation_insecurity', 'utility_insecurity', 'financial_strain', 'medical_cost_burden',
                                   'material_hardship', 'employment', 'education_literacy', 'social_isolation', 'stress',
                                   'interpersonal_violence', 'elder_abuse', 'digital_access', 'language_barrier', 'legal',
                                   'child_care', 'caregiver_strain', 'caregiver_role', 'incarceration', 'veteran_status',
                                   'substance_use', 'health_insurance', 'discrimination', 'other'],
                         'Which social-need domains does the passage describe? Choose every one described.'),
            'response': _d('choice', ['referral_made', 'resources_provided', 'counseling', 'screened_only', 'patient_declined',
                                      'need_resolved', 'none_documented'], 'What was done about the need?'),
            'severity': _d('choice', _SEV, 'How serious does the passage say the need is?'),
        }},
    'function_cognition': {
        'label': 'Function, cognition or safety',
        'stem': 'Does the passage describe a problem or status in the patient\'s function, mobility, cognition or home safety',
        'concept_kind': 'function',
        'default_combine': 'any',
        'details': {
            'domain': _d('choice', ['mobility_gait_balance', 'self_care_adl', 'instrumental_adl', 'falls', 'memory_cognition',
                                    'vision', 'hearing', 'swallowing_speech', 'continence', 'frailty', 'home_safety', 'mood',
                                    'caregiver_support', 'other'], 'Which function domain is it?'),
            'level': _d('choice', ['independent', 'needs_some_help', 'dependent', 'impaired', 'unspecified'],
                        'What level of function does the passage describe?'),
            'device': _d('choice', ['cane', 'walker', 'wheelchair', 'none', 'other', 'not_stated'], 'What aid does the patient use?'),
            'raised_by': _d('choice', ['patient', 'family_or_caregiver', 'clinician', 'screening_tool', 'unspecified'],
                            'Who raised the concern?'),
            'count': _d('count', [], 'How many events (for example falls) does the passage report (a whole number)?'),
        }},
    'preference_refusal': {
        'label': 'Preference, refusal or decision',
        'stem': 'Does the passage record the patient\'s decision, preference or refusal about the thing',
        'concept_kind': 'decision',
        'default_combine': 'latest',
        'details': {
            'decision': _d('choice', ['accepted', 'declined', 'deferred', 'undecided', 'requested'],
                           'What did the patient decide?'),
            'reason': _d('multi', ['cost', 'side_effect_concern', 'belief_or_values', 'logistics_transport', 'prefers_alternative',
                                   'not_ready', 'family_decision', 'comfort_focused_goals', 'other_with_quote'],
                         'What reason does the passage give? Choose every reason stated.'),
            'target': _d('choice', ['medication', 'procedure', 'test', 'vaccine', 'service_referral', 'lifestyle', 'life_sustaining',
                                    'other'], 'What was the decision about?'),
        }},
    'plan_followup': {
        'label': 'Plan, follow-up or disposition',
        'stem': 'Does the passage state a plan, follow-up, return instruction or disposition for the patient',
        'concept_kind': 'plan',
        'default_combine': 'latest',
        'details': {
            'item': _d('choice', ['follow_up_visit', 'referral', 'test_order', 'medication_plan', 'monitoring', 'call_back',
                                  'return_precautions', 'education', 'disposition', 'goal'], 'Which kind of plan item is it?'),
            'timeframe': _d('quote', [], 'The words giving when (a date or interval).'),
            'due_on': _d('date', [], 'By what date is it due?'),
            'disposition': _d('choice', ['home', 'home_health', 'skilled_nursing', 'inpatient_rehab', 'hospice', 'admitted',
                                         'transferred', 'left_against_advice', 'expired', 'other', 'not_stated'],
                             'Where was the patient discharged or sent?'),
            'responsible': _d('choice', ['patient', 'clinician', 'care_team', 'unspecified'], 'Who is to act?'),
        }},
    'note_contradiction': {
        'label': 'Contradiction between notes',
        'stem': 'Does the passage state something about the proposition',
        'concept_kind': 'proposition',
        'default_combine': 'conflict',
        'details': {
            'base_frame': _d('choice', [f for f in ('condition', 'symptom', 'medication_event', 'procedure_test', 'finding_value',
                                                    'adverse_event', 'care_process', 'social_need', 'function_cognition',
                                                    'preference_refusal', 'plan_followup'
                                                    )], 'The frame whose readings are compared across notes.', required=True),
            'axis': _d('choice', ['status', 'value', 'date', 'any'], 'What must differ: the status, the value or the date.'),
        }},
}

# Every detail field has a stable task key: calibration and thresholds are fit per (frame, field).
ASSERTION_FRAMES = tuple(f for f in FRAMES if f != 'note_contradiction')
DEFAULT_THRESHOLD = 0.8
WINDOW_KEYS = {'start', 'end', 'quote'}
CONCEPT_KEYS = {'term', 'codes', 'quote', 'open', 'class'}
OPEN_CLASSES = ('adverse', 'social', 'symptom', 'response')  # note_open: the classes an open concept may name
PREDICATE_KEYS = {'op', 'frame', 'concept', 'statuses', 'detail', 'window', 'combine', 'sources', 'quote', 'return_detail', 'min_p', 'note_types', 'near_records'}


def frame_catalog():
    """A plain-data catalog for prompt writers (spec filling): frame -> label, concept_kind, details -> options."""
    return {'version': VERSION, 'statuses': dict(STATUSES), 'combine': dict(COMBINE),
            'frames': {name: {'label': f['label'], 'concept_kind': f['concept_kind'], 'default_combine': f['default_combine'],
                              'details': {k: {'kind': d['kind'], 'options': d['options'], 'ask': d['ask']}
                                          for k, d in f['details'].items()}} for name, f in FRAMES.items()}}


def _iso(value, field):
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        raise ValueError(f'{field} must be an ISO date (YYYY-MM-DD), got {value!r}') from None


def validate_predicate(p):
    """Validate and normalize a note predicate. Raises ValueError naming the first thing to fix. Returns a normalized copy
    (statuses de-duplicated in canonical order, defaults filled: combine = the frame's default, sources = all)."""
    if not isinstance(p, dict):
        raise ValueError('note predicate must be an object')
    extra = set(p) - PREDICATE_KEYS
    if extra:
        raise ValueError(f'note predicate has unknown keys {sorted(extra)}; allowed {sorted(PREDICATE_KEYS)}')
    if p.get('op') != 'note':
        raise ValueError("note predicate needs op 'note'")
    frame = p.get('frame')
    if frame not in FRAMES:
        raise ValueError(f'frame must be one of {list(FRAMES)}, got {frame!r}')
    spec = FRAMES[frame]
    concept = p.get('concept')
    if not isinstance(concept, dict) or set(concept) - CONCEPT_KEYS or not str(concept.get('term') or '').strip():
        raise ValueError('concept needs a non-empty "term" (plus optional "codes" list and "quote")')
    is_open = concept.get('open')
    if is_open is not None and not isinstance(is_open, bool):
        raise ValueError('concept.open is true or false')
    if is_open and (frame == 'note_contradiction' or concept.get('class') not in OPEN_CLASSES):
        raise ValueError(f'an open concept needs concept.class from {list(OPEN_CLASSES)} and an assertion frame')
    if concept.get('class') and not is_open:
        raise ValueError('concept.class is only for an open concept (concept.open true)')
    codes = concept.get('codes') or []
    if not isinstance(codes, list) or any(not isinstance(c, str) or not c for c in codes):
        raise ValueError('concept.codes must be a list of strings such as "RxNorm:6809" or "ICD10:E11"')
    statuses = p.get('statuses')
    if not isinstance(statuses, list) or not statuses or any(s not in STATUSES for s in statuses):
        raise ValueError(f'statuses must be a non-empty list drawn from {list(STATUSES)}')
    combine = p.get('combine') or spec['default_combine']
    if combine not in COMBINE:
        raise ValueError(f'combine must be one of {list(COMBINE)}')
    if combine == 'trend' and frame != 'finding_value':
        raise ValueError('combine "trend" reads numbers: it needs frame finding_value')
    detail = p.get('detail') or {}
    if not isinstance(detail, dict):
        raise ValueError('detail must be an object of field -> option(s)')
    norm_detail = {}
    for field, want in detail.items():
        if field == 'trend' and combine == 'trend':  # pseudo-field of the trend rule: which direction must the values take
            wants = want if isinstance(want, list) else [want]
            if any(w not in ('increased', 'decreased', 'stable') for w in wants):
                raise ValueError('detail.trend must be increased, decreased or stable')
            norm_detail[field] = want
            continue
        d = spec['details'].get(field)
        if d is None:
            raise ValueError(f'frame {frame} has no detail field {field!r}; fields: {list(spec["details"])}')
        wants = want if isinstance(want, list) else [want]
        if not wants:
            raise ValueError(f'detail.{field} is empty')
        if d['kind'] in ('choice', 'multi'):
            bad = [w for w in wants if w not in d['options']]
            if bad:
                raise ValueError(f'detail.{field} option(s) {bad} not allowed; options: {d["options"]}')
        elif d['kind'] in ('number', 'count'):
            if any(isinstance(w, bool) or not isinstance(w, (int, float, dict)) for w in wants):
                raise ValueError(f'detail.{field} is numeric: give a number or {{"min":..,"max":..}}')
            for w in wants:
                if isinstance(w, dict) and (set(w) - {'min', 'max'} or not w):
                    raise ValueError(f'detail.{field} range takes only "min" and/or "max"')
        elif d['kind'] == 'date':
            wants = [{k: _iso(v, f'detail.{field}.{k}') for k, v in w.items()} if isinstance(w, dict) else _iso(w, f'detail.{field}')
                     for w in wants]
        elif d['kind'] == 'time':
            for w in wants:
                for t in (w.values() if isinstance(w, dict) else [w]):
                    if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', str(t)):
                        raise ValueError(f'detail.{field} times are HH:MM (24 hour), got {t!r}')
        elif d['kind'] == 'quote':
            if any(not isinstance(w, str) or not w.strip() for w in wants):
                raise ValueError(f'detail.{field} is a phrase the passage must contain: give non-empty text')
        norm_detail[field] = wants if isinstance(want, list) else wants[0]
    if frame == 'note_contradiction' and 'base_frame' not in norm_detail:
        raise ValueError('note_contradiction needs detail.base_frame (the frame whose readings are compared)')
    window = p.get('window')
    if window is not None:
        if not isinstance(window, dict) or set(window) - WINDOW_KEYS:
            raise ValueError('window takes only start, end (ISO dates) and quote')
        window = {**window}
        for k in ('start', 'end'):
            if window.get(k):
                window[k] = _iso(window[k], f'window.{k}')
        if window.get('start') and window.get('end') and window['start'] > window['end']:
            raise ValueError('window.start is after window.end')
    sources = p.get('sources') or list(SOURCES)
    if not isinstance(sources, list) or any(s not in SOURCES for s in sources):
        raise ValueError(f'sources must be drawn from {list(SOURCES)}')
    note_types = p.get('note_types') or []
    if note_types:
        from . import note_open
        if not isinstance(note_types, list) or any(t not in note_open.NOTE_TYPES for t in note_types):
            raise ValueError(f'note_types must be drawn from {list(note_open.NOTE_TYPES)}')
    near = p.get('near_records') or []
    if near and (not isinstance(near, list) or any(not isinstance(r, str) or not r for r in near)):
        raise ValueError('near_records is a list of record ids whose passages are read first')
    rd = p.get('return_detail') or []
    if not isinstance(rd, list) or any(f not in spec['details'] for f in rd):
        raise ValueError(f'return_detail must list fields of frame {frame}: {list(spec["details"])}')
    min_p = p.get('min_p')
    if min_p is not None and (isinstance(min_p, bool) or not isinstance(min_p, (int, float)) or not 0 < min_p <= 1):
        raise ValueError('min_p must be in (0, 1]')
    out = {'op': 'note', 'frame': frame,
           'concept': {'term': str(concept['term']).strip(), 'codes': list(dict.fromkeys(codes)),
                       **({'quote': concept['quote']} if concept.get('quote') else {}),
                       **({'open': True, 'class': concept['class']} if is_open else {})},
           'statuses': [s for s in STATUSES if s in statuses], 'detail': norm_detail, 'combine': combine,
           'sources': [s for s in SOURCES if s in sources]}
    if window:
        out['window'] = window
    if note_types:
        out['note_types'] = list(dict.fromkeys(note_types))
    if near:
        out['near_records'] = list(dict.fromkeys(near))
    if rd:
        out['return_detail'] = list(dict.fromkeys(rd))
    if min_p is not None:
        out['min_p'] = float(min_p)
    if p.get('quote'):
        out['quote'] = p['quote']
    return out


def detail_matches(frame, want, got):
    """Whether read detail values `got` ({field: value | [values]}) satisfy the predicate's `want` detail. A list in `want`
    is any-of; a multi field matches when it shares any wanted option; number/count/date accept ranges {min,max}; a quote
    field matches when the read phrase contains the wanted text (case-insensitive). Missing read detail never matches."""
    spec = FRAMES[frame]['details']
    for field, wanted in (want or {}).items():
        have = (got or {}).get(field)
        if have is None:
            return False
        haves = have if isinstance(have, list) else [have]
        wants = wanted if isinstance(wanted, list) else [wanted]
        kind = spec[field]['kind']
        ok = False
        for w in wants:
            for h in haves:
                if kind in ('choice', 'multi'):
                    ok = ok or h == w
                elif kind in ('number', 'count'):
                    try:
                        x = float(h)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(w, dict):
                        ok = ok or ((w.get('min') is None or x >= w['min']) and (w.get('max') is None or x <= w['max']))
                    else:
                        ok = ok or x == float(w)
                elif kind == 'date':
                    hs = str(h)[:10]
                    if isinstance(w, dict):
                        ok = ok or ((not w.get('min') or hs >= w['min']) and (not w.get('max') or hs <= w['max']))
                    else:
                        ok = ok or hs == str(w)[:10]
                elif kind == 'time':
                    hs = str(h)[:5]
                    if isinstance(w, dict):
                        ok = ok or ((not w.get('min') or hs >= w['min']) and (not w.get('max') or hs <= w['max']))
                    else:
                        ok = ok or hs == str(w)[:5]
                elif kind == 'quote':
                    ok = ok or str(w).lower() in str(h).lower()
        if not ok:
            return False
    return True


def task_key(frame, field=None):
    """Calibration / threshold key: 'note:<frame>:status' or 'note:<frame>:<field>'."""
    return f'note:{frame}:{field or "status"}'


def _terms(text):
    return [w for w in re.findall(r'[a-z0-9]+', str(text).lower()) if len(w) > 2]


# -- interfaces (spec: Order of work; builders B and C call these) ------------------------------------------------------
def evaluate(stack, predicate, patient_ids, *, as_of, ledger=None, workers=4, allow_live=True):
    """Read the chart text of each patient for `predicate` and reduce it. `stack` is anything with a `.path` to the semantic
    store (or the path itself). Returns {patient_id: {"status": one of RESULT_STATUSES, "detail": {...}, "evidence":
    [{record_id, date, note_type, quote, status, p, ...}], "p": float | None, "holds": bool | None, "undecided": int}}.
    `holds` is True/False when the patient is decided and None when undecided (status 'unknown'); a count over patients is a
    range [holds True, holds True + holds None]."""
    from . import note_questions
    return note_questions.evaluate(stack, validate_predicate(predicate), list(patient_ids), as_of=as_of, ledger=ledger,
                                   workers=workers, allow_live=allow_live)


def answer_patient(stack, predicate, patient_id, *, as_of, ledger=None, workers=4, allow_live=True):
    """evaluate() for one patient, with every frame detail field read (not only the constrained ones), so the answer carries
    the status, the detail values and each supporting passage's record_id, date, note type and exact quote."""
    from . import note_questions
    return note_questions.evaluate(stack, validate_predicate(predicate), [patient_id], as_of=as_of, ledger=ledger, workers=workers,
                                   allow_live=allow_live, all_details=True)[patient_id]
