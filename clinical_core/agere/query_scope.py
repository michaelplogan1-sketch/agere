"""Conservative scope disclosure for the bounded research query interface.

This is not a semantic compiler. Only named screening requests are fully routed;
other language receives related evidence explicitly marked as a partial answer.
"""
import re

SCREENS = {
    'kidney_specialist_review': ('kidney specialist review', 'Persistent low eGFR with latest value below 30; referral context still needs review.'),
    'declining_egfr': ('declining egfr', 'Descriptive eGFR slopes with an interval below zero; not an acute kidney injury prediction.'),
    'reduced_ef_documentation': ('reduced ef documentation', 'Low-EF text candidates; current phenotype, negation and medication tolerance are not adjudicated.'),
    'financial_assistance_screen': ('financial assistance screen', 'Explicit patient-reported difficulty paying; not program eligibility.'),
    'goals_of_care_context': ('goals of care context', 'Context mentions, not a determination of palliative eligibility or treatment futility.'),
}
PATTERNS = {
    'kidney_specialist_review': r'\b(?:kidney|ckd|nephrolog\w*)\b',
    'declining_egfr': r'\b(?:egfr|renal trend)\b',
    'reduced_ef_documentation': r'\b(?:heart failure|hfref|gdmt|reduced ef)\b',
    'financial_assistance_screen': r'\b(?:financial|afford\w*|assistance)\b',
    'goals_of_care_context': r'\b(?:palliative|goals of care|futil\w*)\b',
}
REQUIRES = [
    (r'\b(?:creatinine|rising cr)\b', 'creatinine_risk'),
    (r'\b(?:snfs?|pcps?|drgs?|best provider)\b', 'snf_pcp_outcomes'),
    (r'\bsepsis\b', 'sepsis_outcomes'),
    (r'\b(?:caus\w*|why|prevent\w*|reduce|reducing|readmi\w*)\b', 'causal_intervention'),
    (r'\b(?:contract\w*|financial performance|shared savings|upside|downside)\b', 'contract_performance'),
    (r'\b(?:network|leakage)\b', 'network_leakage'),
    (r'\b(?:qualif\w*|eligib\w*|cms|medicaid|access program|asm|cjr\w*)\b', 'program_eligibility'),
    (r'\bfutil\w*\b', 'futility'),
]

def scope_query(question, cards, requirements):
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        raise ValueError('A nonempty question of at most 2000 characters is required')
    q = re.sub(r'\s+', ' ', question.lower()).strip(' .?!')
    matches = {kind for kind, pattern in PATTERNS.items() if re.search(pattern, q)}
    missing = {key: requirements[key] for pattern, key in REQUIRES if re.search(pattern, q) and key in requirements}
    if re.search(r'\b(?:gdmt|tolerat\w*|titra\w*|dose|untreated|not on)\b', q):
        missing['medication_appropriateness'] = 'Needs current phenotype, class-specific use, doses, titration, contraindications and patient tolerance; orders or missing claims alone do not establish a treatment gap.'
    if re.search(r'\b(?:palliative|futil\w*)\b', q):
        missing['goals_and_preferences'] = 'Needs current symptoms, prognosis, patient preferences and clinician assessment; keyword mentions are only review context.'
    selected = [c for c in cards if c['id'] in matches]
    exact = next((kind for kind, (name, _) in SCREENS.items()
                  if q in {name, 'show '+name, 'show the '+name, 'find '+name}), None)
    if exact:
        selected = [c for c in cards if c['id'] == exact]
    complete = bool(exact) and not missing
    status = 'supported_screening_results' if complete else 'partial_screening_results' if selected else 'requires_explicit_operator_and_inputs'
    return {'status': status, 'question_fully_answered': complete, 'cards': selected,
            'missing_inputs': missing or (requirements if not selected and not complete else {}),
            'answer_scope': [SCREENS[c['id']][1] for c in selected],
            'supported_screen_names': [name for name, _ in SCREENS.values()],
            'limitation': 'Named screens only. Compound requests, exclusions, comparisons and arbitrary filters are not executed; related cards are not a complete answer.',
            'clinical_action_authorized': False}
