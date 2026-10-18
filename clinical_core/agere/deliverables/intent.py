"""Deterministic request parsing: which data a deliverable needs, for whom, and whether a reference example fits.

parse(request) -> {'program_kinds', 'audience', 'example', 'tools': [(tool, args)], 'signals'}. Keyword rules first; the
composer calls a small model to choose tools only when these rules find nothing to fetch.
"""
from __future__ import annotations

import re

AUDIENCES = [('CFO', r'\bcfo\b|finance|financial|chief financial'), ('CMO', r'\bcmo\b|chief medical'),
             ('board', r'\bboard\b|trustees|investor'), ('VP population health', r'population health|\bvp\b|care management lead'),
             ('quality director', r'quality director|quality team|hedis|star rating'), ('payer medical director', r'payer medical director|health plan|payer'),
             ('pharmacy leader', r'pharmac|pharmd'), ('practice manager', r'practice manager|clinic manager|front desk')]

EXAMPLE_RULES = [('war_room', r'war ?room|what[- ]if|slider'), ('pharmacist_cards', r'decision cards?|pharmacist (?:decision|review|cards?)|barrier review'),
                 ('self_running_program', r'self[- ]running|runs? itself|agent timeline')]

PROGRAM_RULES = [('pharmacy_gdmt', r'heart failure|\bhf\b|\bchf\b|gdmt|hfref|pharmd|pharmacist|cardio'),
                 ('virtual_pt', r'back pain|\blbp\b|physical therapy|\bpt\b|\bmsk\b|access model|cms access|musculoskeletal')]

TOOL_RULES = [
    (r'negotiat|nash|game[- ]theor|bargain|walk[- ]away', lambda k: [('contract', {'focus': 'negotiate'})] + ([('what_if', {'program_kind': k})] if k else [])),
    (r'contract|shared savings|settlement|mssp|aco reach|risk track|var\b|value at risk', lambda k: [('settlement', {})] + ([('what_if', {'program_kind': k})] if k else [])),
    (r'what[- ]if|scenario|slider|war ?room|sensitiv', lambda k: [('what_if', {'program_kind': k or 'pharmacy_gdmt'})]),
    (r'simulat|forecast|business case|\broi\b|savings|outcome|impact|launch|story|board', lambda k: [('simulate', {'program_kind': k})] if k else [('settlement', {})]),
    (r'shapley|attribut|who gets paid|who earned|credit|fair split|settles? who', lambda k: [('attribution', {'program_kind': k or 'pharmacy_gdmt', 'by': 'practice'})]),
    (r'\bmap\b|cluster|where|by practice|by clinic|each clinic|each practice|geograph', lambda k: ([('plan', {'program_kind': k})] if k else []) + [('market', {'unit': 'practice'})]),
    (r'market|ticker|terminal|moving|deteriorat|trend|surprise|volatil', lambda k: [('market', {'unit': 'practice'})]),
    (r'barrier|pharmacist|intoleran|alternative agent', lambda k: [('barriers', {'limit': 200})]),
    (r'outreach|message|script|\btext\b|sms|letter|language|what the agent sends|per patient', lambda k: [('outreach', {'program_kind': k or 'virtual_pt', 'limit': 60})]),
    (r'quality|measure|hedis|star|a1c|blood pressure|statin', lambda k: [('quality', {'group_by': 'practice'})]),
    (r'agent|timeline|operations|ops\b|funnel|denial|appeal|no[- ]show|worklist|exception|escalat', lambda k: [('work', {'program_kind': k or 'pharmacy_gdmt', 'view': 'timelines', 'limit': 120})]),
    (r'readmi|admission|inpatient', lambda k: [('market', {'unit': 'practice', 'metrics': ['admit_rate', 'ed_rate']})]),
    (r'\bed\b|emergency', lambda k: [('market', {'unit': 'practice', 'metrics': ['ed_rate']})]),
]


def parse(request):
    t = ' ' + re.sub(r'\s+', ' ', str(request or '').lower()) + ' '
    kinds = [k for k, rx in PROGRAM_RULES if re.search(rx, t)]
    audience = next((a for a, rx in AUDIENCES if re.search(rx, t)), None)
    example = next((e for e, rx in EXAMPLE_RULES if re.search(rx, t)), None)
    tools, signals = [], []
    primary = kinds[0] if kinds else None
    for rx, fn in TOOL_RULES:
        if re.search(rx, t):
            signals.append(rx.split('|')[0].strip('\\b'))
            for k in (kinds or [None]):
                tools += fn(k)
    if kinds and not any(x[0] == 'plan' for x in tools):
        tools = [('plan', {'program_kind': k}) for k in kinds] + tools
    seen, uniq = set(), []
    for name, args in tools:
        key = (name, tuple(sorted((a, str(v)) for a, v in args.items())))
        if key not in seen:
            seen.add(key)
            uniq.append((name, args))
    return {'program_kinds': kinds, 'program_kind': primary, 'audience': audience, 'example': example, 'tools': uniq[:10], 'signals': signals}
