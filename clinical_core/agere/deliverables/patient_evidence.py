"""Carry already executed patient evidence into every saved report format.

These are attributed observations, never new medication/reason adjudications.
Keep the answer intact; the complete native context remains the download.
"""
import json


def projection(context):
    investigation = context.get('investigation') or {}
    notes = {}
    for review in investigation.get('document_reviews') or []:
        questions = review.get('questions') or {}
        for row in review.get('rows') or []:
            pid = row.get('patient_id')
            if not pid:
                continue
            raw = row.get('review_json')
            try:
                found = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError):
                found = None
            notes.setdefault(pid, []).append({
                'questions': questions, 'findings': found,
                'review_status': row.get('review_status'),
                'documents_reviewed': row.get('documents_reviewed'),
                'limitations': row.get('review_limitations')})
    # Last executed table with the same schema is the most recent observation
    # of that schema, not a merged or clinically reconciled state. Aggregated
    # final-answer tables and earlier rejected summaries are not substituted.
    tables = {}
    for call in investigation.get('tool_calls') or []:
        out = call.get('result')
        if call.get('name') != 'query_sql' or not isinstance(out, dict) or out.get('error'):
            continue
        columns = out.get('columns') or []
        if 'patient_id' not in columns or not ({'drug_name', 'dose_status', 'assignment_status'} & set(columns)):
            continue
        rows = [dict(zip(columns, r)) if isinstance(r, (list, tuple)) else r
                for r in out.get('rows') or []]
        if any(not isinstance(r, dict) for r in rows):
            continue
        tables[tuple(columns)] = {'result_view': out.get('result_view'), 'columns': columns, 'rows': rows,
            'query': out.get('executed_query') or (call.get('input') or {}).get('sql'),
            'truncated': bool(out.get('truncated'))}
    return {'notes': notes, 'supporting_tables': list(tables.values()),
            'basis': 'Already executed supporting observations; no new clinical interpretation. Historical fills, documented dose statements and referrals retain their original meaning.'}
