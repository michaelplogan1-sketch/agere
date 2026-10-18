"""Product naming, design tokens and demo content for the hosted pages, in one place.

PRODUCT_NAME is what every page shows (override with AGERE_PRODUCT_NAME). TOKENS mirror the marketing site's design
system (paper / ink / forest / orange / line, DM Sans for UI, IBM Plex Mono for labels, numbers and code).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

PRODUCT_NAME = os.environ.get('AGERE_PRODUCT_NAME') or 'Agere'
TAGLINE = 'From data to wisdom'
ASK_TITLE = f'Ask {PRODUCT_NAME}'

TOKENS = {'paper': '#eeeee8', 'paper-2': '#e5e6de', 'card': '#f6f6f1', 'ink': '#172b23', 'muted': '#55655c', 'forest': '#102b21',
          'forest-2': '#183a2d', 'orange': '#e8693d', 'line': '#cbd0c5', 'ok': '#2f7a4f', 'bad': '#b4432a'}
DARK = {'paper': '#0d241b', 'paper-2': '#123024', 'card': '#14342a', 'ink': '#e9ebe2', 'muted': '#a6b4aa', 'forest': '#081a13',
        'forest-2': '#0f2a1f', 'orange': '#f07a4f', 'line': '#2c4a3d', 'ok': '#6cc493', 'bad': '#f08a6c'}
FONTS_URL = 'https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,700&family=IBM+Plex+Mono:wght@400;500&display=swap'
FONT_ORIGINS = ('https://fonts.googleapis.com', 'https://fonts.gstatic.com')

GOLD = Path(__file__).resolve().parents[3] / 'docs' / 'agere' / 'gold10' / 'questions.jsonl'
EXTRA_QUESTIONS = [
    {'id': 'POP-HF', 'category': 'Population', 'question': 'How many patients have heart failure with reduced ejection fraction?'},
    {'id': 'POP-EGFR', 'category': 'Forecast', 'question': 'Which eGFR series are projected below 30 at the one-year horizon?'},
    {'id': 'FIN-IP', 'category': 'Finance', 'question': 'What was the total paid amount for inpatient claims in the last 12 months?'},
    {'id': 'FIN-ED', 'category': 'Finance', 'question': 'What is the ED cost per member per month by payer product?'},
    {'id': 'FIN-PMPM', 'category': 'Finance', 'question': 'What is total cost of care per member per month for heart failure patients?'},
]

PROGRAMS = [
    {'kind': 'pharmacy_gdmt', 'label': 'Heart failure GDMT', 'detail': 'Find HFrEF patients missing GDMT pillars, enroll them with a PharmD program, '
                                                                      'then titrate and monitor at home.'},
    {'kind': 'virtual_pt', 'label': 'ED low back pain to PT', 'detail': 'Patients discharged from the ED with low back pain and no physical therapy, '
                                                                       'routed to virtual PT (ACCESS or plan benefit).'},
    {'kind': 'coverage_check', 'label': 'Coverage verification', 'detail': 'Patients with no active coverage: a clearinghouse check and enrollment help.'},
]

DELIVERABLE_TEMPLATES = [
    {'template': 'war_room', 'label': 'HF war room'},
    {'template': 'pharmacist_cards', 'label': 'Pharmacist worklist cards'},
    {'template': 'self_running_program', 'label': 'Self-running program view'},
]


def example_questions():
    """Gold questions (docs/agere/gold10/questions.jsonl) plus population and finance examples."""
    out = []
    try:
        for line in GOLD.read_text().splitlines():
            if line.strip():
                q = json.loads(line)
                out.append({'id': q.get('id'), 'category': 'Quality' if q.get('answer_type') == 'patient_set' else 'Population',
                            'question': q['question'], 'definition': q.get('definition')})
    except (OSError, ValueError):
        pass
    return out + EXTRA_QUESTIONS


def css_tokens():
    light = ';'.join(f'--{k}:{v}' for k, v in TOKENS.items())
    dark = ';'.join(f'--{k}:{v}' for k, v in DARK.items())
    return (f':root{{{light};color-scheme:light}}'
            f'@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{{dark};color-scheme:dark}}}}'
            f':root[data-theme="dark"]{{{dark};color-scheme:dark}}')
