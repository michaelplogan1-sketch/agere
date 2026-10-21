"""Honest 'not available': questions about data the warehouse was never sent.

A question names a data domain (patient-experience surveys, prior authorization, Star ratings, ...). When no received
table, column or stream field could hold that domain, the answer says so and names what was checked, instead of a
generic 'not understood'. Anything that might exist falls through to normal planning. A period that starts after the
snapshot is reported the same way. Absence is checked against the catalog, never assumed from wording alone.
"""
import re
from datetime import date

DOMAINS = [  # (label, question pattern, catalog name hints that would hold it)
    ('patient-experience survey (CAHPS / satisfaction) results',
     r'\b(?:h?cahps|patient satisfaction|satisfaction scores?|patient experience surveys?|press ganey|nps score)\b',
     ('survey', 'cahps', 'satisfaction', 'experience_score')),
    ('prior authorization requests and decisions', r'\bprior[- ]?auth(?:orization)?s?\b|\bpre-?auth(?:orization)?s?\b',
     ('prior_auth', 'preauth', 'authorization_request', 'auth_decision')),
    ('CMS Star ratings', r'\bstar ratings?\b|\bstars? (?:measure )?ratings?\b|\bpart [cd] stars?\b', ('star_rating', 'stars_rating', 'star_measure')),
    ('clinician compensation and RVUs', r'\b(?:w?rvus?|relative value units?|salar(?:y|ies)|physician compensation|provider pay)\b',
     ('rvu', 'salary', 'compensation', 'payroll')),
    ('genetic or genomic test results', r'\b(?:brca[12]?|genetic|genomic|germline|gene mutation|variant of uncertain)\b',
     ('genetic', 'genomic', 'variant', 'gene')),
    ('ED throughput timestamps', r'\bdoor[- ]to[- ](?:provider|doctor|doc|needle|balloon)\b|\b(?:ed|er) wait times?\b|\blength of (?:ed|er) wait\b',
     ('triage_time', 'provider_seen', 'door_to', 'arrival_time', 'wait_minutes')),
    ('cost-accounting (internal cost) data', r'\b(?:cost accounting|cost to serve|direct costs?|cost per case|activity[- ]based cost)\b',
     ('cost_accounting', 'direct_cost', 'cost_per_case')),
]
MONTHS = {m: i for i, m in enumerate('january february march april may june july august september october november december'.split(), 1)}


def _names(catalog):
    names = set(catalog.tables)
    for cols in catalog.tables.values(): names.update(cols)
    for stream, fields in (catalog.stream_fields or {}).items():
        names.add(stream); names.update(fields if isinstance(fields, (list, tuple, set, dict)) else [])
    return {str(n).lower() for n in names}


def check(catalog, question):
    """(label, reason) when the question needs data the warehouse does not hold, else None."""
    text = question.lower()
    names = None
    for label, pattern, hints in DOMAINS:
        if not re.search(pattern, text, re.I): continue
        names = names if names is not None else _names(catalog)
        if any(h in n for h in hints for n in names): return None  # something could hold it: plan normally
        return label, (f'The warehouse has no {label}: no received table, column or feed field matches '
                       f'({", ".join(hints)}).')
    as_of = catalog.identity.get('as_of')
    if as_of:
        end = date.fromisoformat(as_of[:10])
        periods = [(int(y), MONTHS.get((m or '').lower(), 1)) for m, y in re.findall(
            r'\b(?:(' + '|'.join(MONTHS) + r')\s+)?(20\d\d)\b', text)]
        if periods and all((y, mo) > (end.year, end.month) for y, mo in periods):
            return 'data for that period', f'The warehouse data runs through {as_of[:10]}; the requested period is after it.'
    return None


def answer(question, label, reason):
    return {'schema': 'agere-data-absent/1', 'synthetic': True, 'question': question, 'route': 'shared_planner',
            'answer': {'status': 'not_available', 'answer_type': 'data_absent', 'answerable': False,
                       'data_absence_established': True, 'headline': 'Not available: ' + reason,
                       'missing': [reason], 'patient_ids': [], 'actionable_cohort': False,
                       'limitations': ['Checked against the received catalog; no query was run and no model was called.']},
            'planner_receipt': {'route': 'shared_planner', 'interpretation': {'interpreter': 'data_domain_check', 'domain': label},
                                'new_paid_api_calls': 0},
            'new_paid_api_calls': 0, 'planning_economics': {'known_api_estimate_usd': 0, 'api_cost_known': True}}
