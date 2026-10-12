"""Read views across frozen and registered work engines."""
from collections import Counter
from datetime import date
from .program_work_v1 import database,STATES,TERMINAL,LIMITS
import json
VERSION='program-work-view/1'
def definition(program):
    from .program_work import definition as resolve
    return resolve(program)
def registered_programs():
    from .program_work import registered_programs as catalog
    return catalog()

def list_items(path, *, program=None, state=None, offset=0, limit=25):
    if program is not None: definition(program)
    if state is not None and state not in STATES: raise ValueError('Unknown work state')
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Bounded worklist page required')
    db = database(path)
    try:
        items = [json.loads(r[0]) for r in db.execute('SELECT payload FROM work_items'+(' WHERE program=?' if program else '')+' ORDER BY patient,program', (program,) if program else ())]
        counts = Counter(i['state'] for i in items)
        selected = [i for i in items if state is None or i['state'] == state]
        # Operational milestones are cumulative people, not clinical-effect measures.
        ever = {k:set() for k in ('offer','enroll','complete_program')}; relevant = {i['id'] for i in items}
        for ident, raw in db.execute('SELECT item,payload FROM work_events'):
            if ident in relevant:
                event = json.loads(raw)
                if event['action'] in ever: ever[event['action']].add(ident)
        return {'schema': VERSION, 'synthetic': True, 'programs': registered_programs(),
                'states': STATES, 'items': selected[offset:offset+limit], 'matched': len(selected), 'offset': offset, 'limit': limit,
                'summary': {'work_items': len(items), 'state_counts': dict(counts),
                            'unassigned': sum(not i['owner'] for i in items),
                            'evidence_changes_pending': sum(i['reassessment_required'] for i in items),
                            'overdue_open_work': sum(i['state'] not in TERMINAL and bool(i['due_date']) and i['due_date'] < date.today().isoformat() for i in items),
                            'ever_offered': len(ever['offer']), 'ever_enrolled': len(ever['enroll']),
                            'staff_reported_program_completions': len(ever['complete_program']),
                            'verified_clinical_goal_attainment': None,
                            'denominator': 'Persisted patient-program work items in the selected program, not all clinically eligible patients.'},
                'new_paid_api_calls': 0, 'external_actions_executed': 0, 'limits': LIMITS}
    finally:
        db.close()
