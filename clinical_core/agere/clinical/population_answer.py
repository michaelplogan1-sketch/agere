"""What a population answer says about itself: numerator and denominator of a share, and a one-line read-back of how the question was
understood (population, measure, window, the decision-context definitions that were applied).

The read-back is built from the compiled request only -- its own literal fragments of the question and the SQL that ran -- so it states
what was computed, never what was hoped. Used by shared_planner.execute_contract for every typed-request answer.
"""
import re

from . import sql_query

NOUNS = {'patient': 'patients', 'payer_claim': 'counted medical claims', 'claim': 'claims', 'payer_pharmacy_claim': 'pharmacy claims',
         'appointment': 'appointments', 'utilization_event': 'utilization events', 'stay': 'hospital stays', 'visit': 'visits',
         'fill': 'fills', 'returned_fill': 'returned fills', 'member_month': 'member months', 'measurement': 'lab results'}


def parts(stack_path, contract, answer, *, population=None, target=None):
    """Run the contract's numerator / denominator query and attach the counts (ungrouped) or merge them into the rows (grouped)."""
    query = contract.get('parts_query')
    if not query: return None
    try:
        report = sql_query.execute(stack_path, {'query': query, 'returns': 'table'}, patient_ids=population, target=target)
    except Exception:  # noqa: BLE001 - the parts only explain the answer; a failure never withholds it
        return None
    rows = report['rows']
    groups = [g for g in (contract['request'].get('group_by') or [])]
    if not groups:
        if rows:
            answer['numerator'], answer['denominator'] = rows[0]['numerator'], rows[0]['denominator']
        return {'numerator': answer.get('numerator'), 'denominator': answer.get('denominator')}
    index = {tuple(r[g] for g in groups): r for r in rows}
    for row in answer.get('table') or []:
        match = index.get(tuple(row.get(g) for g in groups))
        if match:
            row['numerator'], row['denominator'] = match['numerator'], match['denominator']
    return {'groups': len(rows)}


def criteria(contract, question=''):
    """The literal fragments of the question that became the population and its conditions, in question order, nested fragments merged."""
    quotes = []
    for b in contract.get('bindings', []):
        if b['kind'] not in ('population', 'predicate', 'relationship', 'ordering', 'limit') or not b.get('covers', True): continue
        if b['quote'] not in quotes: quotes.append(b['quote'])
    quotes = [q for q in quotes if not any(q != o and q in o for o in quotes)]
    return sorted(quotes, key=lambda q: question.find(q) if q in question else 10 ** 6)


def read_back(contract, answer, question, as_of):
    req = contract['request']
    table = req['source']['table']
    noun = NOUNS.get(table, table.replace('_', ' '))
    query = contract.get('query', '')
    selects = [e for e in req['select'] if e['kind'] != 'field']
    groups = [e for e in req['select'] if e['kind'] == 'field' and e['alias'] in (req.get('group_by') or [])]
    if selects and selects[0]['kind'] == 'ratio':
        what = f'Percent of {noun}' + (' that have a latest result for the lab and meet the condition' if 'measurement_latest' in query else ' that meet the condition')
        if answer.get('numerator') is not None and answer.get('denominator') is not None:
            what += f" ({answer['numerator']:,} of {answer['denominator']:,})"
    elif selects:
        kind = selects[0]['kind']
        what = {'count_distinct': f'Count of {noun}', 'count': f'Count of {noun}', 'sum': f"Sum of {selects[0].get('field', '').split('.')[-1].replace('_', ' ')}",
                'avg': f"Average of {selects[0].get('field', '').split('.')[-1].replace('_', ' ')}"}.get(kind, f'{kind} of {noun}')
    else:
        what = f'List of {noun}'
    if groups: what += ' by ' + ', '.join(g['alias'].replace('_', ' ') for g in groups)
    given = criteria(contract, question)
    parts_ = [what + (': ' + '; '.join(given) if given else '')]
    defs = []
    if re.search(r'NOT IN \(SELECT patient_id FROM death\)|NOT EXISTS \(SELECT 1 FROM "death"', query): defs.append('living patients only')
    if 'measurement_latest' in query: defs.append('labs read the latest result per patient')
    if 'picked-up fill' in ' '.join(contract.get('notes', [])): defs.append('"on" a medication = a picked-up fill in the 12 months to the as-of date')
    if "'ehr_diagnosis'" in query: defs.append('conditions = EHR diagnoses, problem list, counted claim diagnoses (diabetes and CKD also by lab evidence)')
    if 'within_last' in query or "date('" in query: defs.append(f'windows end at the as-of date {as_of}')
    if defs: parts_.append('definitions: ' + '; '.join(defs))
    return ' | '.join(parts_) + '.'
