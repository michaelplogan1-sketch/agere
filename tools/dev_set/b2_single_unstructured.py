"""Bucket 2: single-patient questions answerable only from note / document text.

The keys are READ, not computed: labels_unstructured.json holds, per item, the answer, the supporting record ids and exact
quotes, and both labeling passes (see README). This module re-verifies at build time that every quote is an exact substring of
the stored text, so a changed warehouse fails loudly instead of silently shipping a stale key.

Text lives in: record.payload ($.text) for stream notes / hie_documents (clinical_note and hie_document are metadata only)
and scanned_document_text.text for scanned / OCR documents.
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.dev_set.keys import Q

B = 'single_patient_unstructured'
LABELS = Path(__file__).with_name('labels_unstructured.json')


def record_text(ctx, record_id):
    row = ctx.rows("SELECT text FROM scanned_document_text WHERE record_id = ?", (record_id,))
    if row:
        return row[0][0]
    row = ctx.rows("SELECT payload FROM record WHERE record_id = ?", (record_id,))
    if not row:
        raise KeyError(record_id)
    return json.loads(row[0][0]).get('text') or ''


def load():
    return json.loads(LABELS.read_text())


def build(ctx):
    data = load()
    out = []
    for iid, it in data['items'].items():
        for ev in it['evidence']:
            text = record_text(ctx, ev['record_id'])
            if ev['quote'] not in text:
                raise ValueError(f"{iid}: quote not found in {ev['record_id']}: {ev['quote'][:60]!r}")
        for sc in it['scope']:
            record_text(ctx, sc['record_id'])
        parent = it['follow_up_of']
        common = dict(follow_up_of=f'note_{parent}' if parent else None, evidence=it['evidence'])
        if it['kind'] == 'absent':
            out.append(Q(B, f'note_{iid}', it['question'], 'absent', reason=it['reason'], patient_id=it['patient_id'],
                         notes='the warehouse holds no such content (text-searched all notes, HIE and scanned documents)', **common))
            continue
        extra = dict(kind=it['kind'], key_groups=it.get('key_groups', []), patient_id=it['patient_id'],
                     scope_records=[s['record_id'] for s in it['scope']], pass2_answer=(it['pass2'] or {}).get('answer'))
        if it['standalone']:
            extra['standalone'] = it['standalone']
        notes = {'not_documented': 'correct answer is "not documented": the whole chart scope was read and never addresses it',
                 'yes_no': 'answer yes / no; cite a supporting record id',
                 'value': 'numeric answer; cite the record id',
                 'short_text': 'every key group must be present in the answer; cite a supporting record id'}[it['kind']]
        out.append(Q(B, f'note_{iid}', it['question'], 'note', answer=it['answer'], notes=notes, **common, **extra))
    return out
