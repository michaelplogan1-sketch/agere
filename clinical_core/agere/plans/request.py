"""Recognize plan requests and split them into (cohort question, program kind) -- deterministic text rules, no model.

    parse('Find all HF patients not on full GDMT, then create a plan to sign them up for a pharmacy GDMT program')
    -> {'program_kind': 'pharmacy_gdmt', 'cohort_question': 'Find all HF patients not on full GDMT', ...}
"""
from __future__ import annotations

import re

from . import catalog as catmod

# "create / make / build / draft / generate / develop ... plan", or an explicit routing / enrollment ask about patients.
_PLAN = re.compile(r'\b(create|make|build|draft|generate|develop|put together|write)\b[^.?!]{0,40}?\bplan\b', re.I)
_ACTION = re.compile(r'\b(enroll (?:them|those|these|each|patients?|members?|the patients|the members)|sign (?:them|those|these|each|patients?|members?) up|route (?:them|those|these|each|patients?|members?)'
                     r'|refer (?:them|those|these|each)|outreach (?:to|for) (?:them|those|these|each))\b', re.I)
# where the cohort clause ends and the plan clause begins
_SPLIT = re.compile(r'(?:[,;.]\s*|\s+)(?:and\s+)?(?:then\s+)?(?:please\s+)?(?:(?:create|make|build|draft|generate|develop|put together|write)\b'
                    r'|(?:enroll|route|sign)\b)', re.I)
_LEAD = re.compile(r'^\s*(?:please\s+)?(?:create|make|build|draft|generate|develop|put together|write)\s+(?:an?\s+)?'
                   r'(?:routed\s+|enrollment\s+|outreach\s+|care\s+)*(?:plan|outreach)\b[^.?!]*?\b(?:for|covering)\s+', re.I)


def is_plan_request(question, answer_type=None):
    if answer_type == 'plan':
        return True
    if answer_type not in (None, ''):
        return False  # an explicit count / value / patient_set / table type is never a plan
    q = str(question or '')
    return bool(_PLAN.search(q) or _ACTION.search(q))


def infer_kind(text, cat=None):
    """The program kind whose request keywords appear in text (most specific match wins), or None."""
    cat = cat or catmod.load()
    t = ' ' + re.sub(r'\s+', ' ', str(text).lower()) + ' '
    best, score = None, 0
    for p in cat['programs']:
        hits = sum(len(k) for k in p.get('request_keywords') or [] if k.lower() in t)
        if hits > score:
            best, score = p['kind'], hits
    return best


def _split(question):
    """-> (cohort clause, plan clause) of a plan request; the cohort clause is '' when none is named."""
    q = str(question or '').strip()
    m = _LEAD.match(q)
    if m:  # "Create a plan ... for heart failure patients not on GDMT [with agents who ...]"
        rest = q[m.end():]
        parts = re.split(r'\b(?:with|using|through|via|so that|who can|to sign|to enroll)\b', rest, maxsplit=1, flags=re.I)
        return parts[0].strip(' ,;.-'), q[:m.end()] + (parts[1] if len(parts) > 1 else '')
    m = _SPLIT.search(q)
    if m and m.start() > 0:
        return q[:m.start()].strip(' ,;.-'), q[m.start():]
    return '', q


def cohort_question(question):
    """The cohort part of a plan request as a patient-list question ('' when the request does not name one)."""
    s = _split(question)[0]
    if s and not re.match(r'^(find|list|which|who|show|identify|get|return)\b', s, re.I):
        s = 'List the patients: ' + s
    return s


def parse(question, answer_type=None, *, program_kind=None, cat=None):
    """-> {'question', 'cohort_question', 'program_kind', 'kind_source'} or None when this is not a plan request."""
    if not is_plan_request(question, answer_type) and not program_kind:
        return None
    cat = cat or catmod.load()
    kind, source = program_kind, 'caller'
    if not kind:  # the plan clause names the program; fall back to the whole question
        kind, source = infer_kind(_split(question)[1], cat) or infer_kind(question, cat), 'keywords'
    return {'question': str(question), 'cohort_question': cohort_question(question), 'program_kind': kind,
            'kind_source': source if kind else None}
