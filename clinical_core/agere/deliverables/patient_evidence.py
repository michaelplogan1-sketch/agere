"""Carry already executed patient evidence into every saved report format.

These are attributed observations, never new medication/reason adjudications.
Keep the answer intact; the complete native context remains the download.
"""
import json,re

RECOMMENDATION_FIELD_PATTERN=r'^(?!.*(?:_status|_count|_type|_number|_n)$).*(?:recommend|next_(?:step|action)|action_plan|proposed_(?:[a-z]+_)*(?:action|step)|review_(?:step|plan))'

def recommendation_field(name):
    return bool(re.search(RECOMMENDATION_FIELD_PATTERN,name,re.I))


def projection(context):
    from ...contracts import digest
    investigation = context.get('investigation') or {}
    notes = {}
    replacements={}
    for review in investigation.get('document_reviews')or[]:
        recheck=review.get('candidate_observation_review')or{}
        if recheck.get('source_rows_digest'):
            replacements.setdefault(recheck['source_rows_digest'],set()).update(recheck.get('checked_fields')or[])
    for review in investigation.get('document_reviews') or []:
        questions = review.get('questions') or {}
        audit_fields=sorted(replacements.get(digest(review.get('rows')or[]),set()))
        for row in review.get('rows') or []:
            pid = row.get('patient_id')
            if not pid:
                continue
            raw = row.get('review_json')
            try:
                found = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError):
                found = None
            raw_roles=row.get('source_clinicians_json')or'[]'
            try:roles=json.loads(raw_roles)if isinstance(raw_roles,str)else raw_roles
            except (TypeError,ValueError):roles=None
            try:
                scope=row.get('evidence_scope_json')
                scope=json.loads(scope)if isinstance(scope,str)else scope
                scope={k:scope[k]for k in ('inventory_documents','selected_documents','field_documents','field_applicability',
                    'complete_chart_review','absence_established','expansion')if k in scope}if isinstance(scope,dict)else None
                pending=row.get('review_unresolved_json')
                pending=json.loads(pending)if isinstance(pending,str)else pending
                unresolved=len(pending)if isinstance(pending,list)else None
            except (TypeError,ValueError):scope=None;unresolved=None
            notes.setdefault(pid, []).append({
                'source_clinicians':roles,
                'source_scope':review.get('source_scope'),
                'evidence_scope':scope,'unresolved_source_fields':unresolved,
                'questions': questions, 'findings': found,
                'audit_only_fields':audit_fields,
                'candidate_review_scope':review.get('candidate_observation_review'),
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
