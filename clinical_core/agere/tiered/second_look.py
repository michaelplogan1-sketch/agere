"""ED Second Look and Facility Line: two prototypes on synthetic records. Neither contacts anyone or changes a record.

ED Second Look. When a patient discharged in the last 30 days arrives in the emergency department, the physician sees one
screen: the last stay, what has happened since, and four things that would need to be true to send the patient home, each
marked as on record or not. The decision to admit or discharge is the physician's; the screen offers, it never decides.
Facility Line. A facility nurse calls before sending a patient back. Four answers lead to one of four outcomes by fixed rules;
an unstable patient is always an emergency transfer.

The lists come from the stack ('ed_return_arrivals', 'facility_census'); the rules are code; no model is called.

    python -m clinical_core.agere.tiered.second_look arrivals 2029-01-23
    python -m clinical_core.agere.tiered.second_look replay 2029 [out.json]
"""
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from . import bottles as bottle_store, readmission, router, store

NEEDS = ('a visit within 48 hours', 'medicines in hand', 'someone at home', 'a plan if it worsens')
FACILITY_QUESTIONS = (('unstable', 'Is the patient unstable right now: struggling to breathe, unresponsive, chest pain, or bleeding?'),
                      ('change', 'Is this a change from how the patient was on arrival at the facility?'),
                      ('seen', "Has the facility's own clinician assessed the patient today?"),
                      ('can_treat', 'Can the facility give what is likely needed tonight: oxygen, IV or injected medicines, labs by morning?'))


def _ask(question, bottles=None):
    outcome = router.answer(question, tiers=('bottles', 'recipes'), bottles=bottles or bottle_store.Bottles())
    return outcome.get('columns') or [], (outcome.get('rows') or []) if outcome['status'] == 'answered' else []


def arrivals(day, bottles=None):
    columns, rows = _ask(f'Who came back to the emergency department on {day}?', bottles)
    return [dict(zip(columns, r)) for r in rows if len(r) > 1 and r[1]]


def census(day, bottles=None):
    columns, rows = _ask(f'Which patients are in a facility after discharge on {day}?', bottles)
    return [dict(zip(columns, r)) for r in rows if len(r) > 1 and r[1]]


def screen(arrival, day, db):
    """What the emergency physician sees for one arrival, from the record as it stood that day.
    -> {'patient','arrival','discharge','days','went','reason','instruction','markers','needs': {need: (state, words)}, 'offer': [...]}."""
    pid, dis = arrival['patient_id'], arrival['discharge_date']
    admit = (db.execute("SELECT MAX(admit_date) FROM stay WHERE patient_id = ? AND type = 'inpatient' AND admit_date <= ?", (pid, dis)).fetchone() or [dis])[0] or dis
    ids = [r[0] for r in db.execute("SELECT record_id FROM document WHERE patient_id = ? AND event_date >= ? AND event_date < ? AND lower(document_type) LIKE '%discharge%' ORDER BY event_date", (pid, admit, day))]
    summaries = [' '.join(t['text'].split()) for t in store.texts(ids, db).values()]
    reason = next((got[1] for got in map(readmission.stated_reason, summaries) if got), None)
    instruction = next((got for got in map(readmission.follow_up_instruction, summaries) if got), None)
    markers = {d: [] for d in readmission.DRIVERS if d != 'emergency department threshold'}
    readmission.person_markers(db, pid, day, admit, markers)
    went = readmission.destination(db, pid, dis, summaries)
    facility = bool(readmission.FACILITY.search(went))
    if facility: markers['sent back from a facility'].append('discharged to a ' + went)
    elif readmission.HOME_HEALTH.search(went): markers['self-care at home'].append('discharged with home health')
    nxt, fills = arrival['next_appointment'], int(arrival['fills_since_discharge'] or 0)
    soon = nxt != 'none on record' and date.fromisoformat(nxt) <= date.fromisoformat(day) + timedelta(days=2)
    needs = {'a visit within 48 hours': ('on record', f'appointment on {nxt}') if soon else ('not on record', 'no appointment in the next 2 days' + (f'; the next is {nxt}' if nxt != 'none on record' else '')),
             'medicines in hand': ('on record', f'{fills} pharmacy fill{"s" if fills != 1 else ""} since discharge') if fills else ('not on record', 'no pharmacy fill since discharge'),
             'someone at home': ('facility', 'returns to the ' + went + ': hand over to the charge nurse') if facility else
                                ('ask', '; '.join(markers['self-care at home'])) if markers['self-care at home'] else ('not flagged', 'no self-care marker on record'),
             'a plan if it worsens': ('ask', 'advanced illness on record and no goals-of-care note: ' + '; '.join(markers['advanced illness'])) if markers['advanced illness'] else ('not flagged', 'no advanced-illness marker on record')}
    offer = []
    if not soon: offer.append('book a clinic visit for tomorrow morning')
    if not fills: offer.append('send the discharge medication list to the pharmacist tonight')
    if needs['someone at home'][0] == 'ask': offer.append('confirm with the patient who is at home tonight, and ask care management to call in the morning')
    if facility: offer.append("call the facility's charge nurse with tonight's findings and orders")
    if needs['a plan if it worsens'][0] == 'ask': offer.append("flag the patient's own clinician that a goals-of-care conversation is due")
    offer.append('place a call to the patient tomorrow morning and record that a documented plan was in place')
    return {'patient': pid, 'arrival': day, 'discharge': dis, 'days': arrival['days_since_discharge'], 'went': went, 'contacts': arrival['contacts_since_discharge'], 'reason': reason,
            'instruction': instruction, 'markers': {k: v for k, v in markers.items() if v}, 'needs': needs, 'offer': offer}


def what_happened(pid, day, db):
    """Hindsight, never shown to the agent: whether a hospital stay began on the arrival day or the next, and what the emergency note said."""
    began = db.execute("SELECT MIN(admit_date) FROM stay WHERE patient_id = ? AND type = 'inpatient' AND admit_date >= ? AND admit_date <= date(?, '+1 day')", (pid, day, day)).fetchone()[0]
    ids = [r[0] for r in db.execute("SELECT record_id FROM document WHERE patient_id = ? AND event_date >= ? AND event_date <= date(?, '+2 day')", (pid, day, day))]
    said = any(readmission.ED_LOW_YIELD.search(' '.join(t['text'].split())) for t in store.texts(ids, db).values())
    return {'admitted': bool(began), 'note_unremarkable_admitted': bool(began) and said}


def facility_outcome(answers):
    """Rules in code for a call from a facility nurse. answers: {key: 'yes' | 'no'}. -> {'do','why','urgent'} or None until enough is known."""
    a = answers
    if a.get('unstable') == 'yes': return {'do': 'Transfer now by emergency ambulance', 'why': 'unstable; the line never delays an emergency', 'urgent': True}
    if a.get('unstable') != 'no' or 'change' not in a: return None
    if a['change'] == 'no': return {'do': 'Stay at the facility; recheck at the next shift', 'why': 'no change from arrival; the hospital clinician reviews the discharge plan with the nurse', 'urgent': False}
    if 'seen' not in a or 'can_treat' not in a: return None
    if a['can_treat'] == 'yes':
        return {'do': 'Treat in place with orders from the hospital clinician; recheck in 4 hours' + ('' if a['seen'] == 'yes' else "; the facility's clinician to assess today"),
                'why': 'a change the facility can treat tonight', 'urgent': False}
    return {'do': 'Transfer to the emergency department with the reason and tonight\'s findings written down', 'why': 'a change the facility cannot treat tonight', 'urgent': False}


def replay(year):
    """Every day of one year, both prototypes, from the record alone."""
    bottles, db = bottle_store.Bottles(), store.connect()
    day, end, seen, stays, questions, started = date(int(year), 1, 1), date(int(year), 12, 31), [], {}, 0, time.monotonic()
    while day <= end:
        for a in arrivals(str(day), bottles):
            s = screen(a, str(day), db)
            seen.append({**s, 'needs': {k: list(v) for k, v in s['needs'].items()}, **what_happened(a['patient_id'], str(day), db)})
        for c in census(str(day), bottles):
            stays.setdefault((c['patient_id'], c['discharge_date']), {'p': c['patient_id'], 'dis': c['discharge_date'], 'facility': c['facility'], 'days': 0, 'prior': c['admissions_in_prior_12_months']})['days'] += 1
        questions += 2
        day += timedelta(days=1)
    for f in stays.values():
        back = db.execute("SELECT MIN(admit_date) FROM stay WHERE patient_id = ? AND type = 'inpatient' AND admit_date > ? AND admit_date <= date(?, '+30 day') AND admit_date <> discharge_date", (f['p'], f['dis'], f['dis'])).fetchone()[0]
        f['back'] = back or ''
        ids = [r[0] for r in db.execute("SELECT record_id FROM document WHERE patient_id = ? AND event_date <= date(?, '+3 day') AND event_date >= date(?, '-30 day') AND lower(document_type) LIKE '%discharge%'", (f['p'], f['dis'], f['dis']))]
        texts = [' '.join(t['text'].split()) for t in store.texts(ids, db).values()]
        f['reason'] = next((g[1] for g in map(readmission.stated_reason, texts) if g), '') or ''
    db.close()
    n, adm = len(seen), [s for s in seen if s['admitted']]
    gap = lambda k, rows: sum(1 for s in rows if s['needs'][k][0] in ('not on record', 'ask'))
    summary = {'year': year, 'stack_questions': questions, 'seconds': round(time.monotonic() - started, 1),
               'ed_arrivals_within_30_days_of_discharge': n, 'patients': len({s['patient'] for s in seen}), 'admitted': len(adm), 'sent_home': n - len(adm),
               'admitted_with_note_unremarkable': sum(s['note_unremarkable_admitted'] for s in seen), 'from_a_facility': sum('sent back from a facility' in s['markers'] for s in seen),
               'no_visit_within_48_hours_on_record': gap('a visit within 48 hours', seen), 'no_fill_since_discharge': gap('medicines in hand', seen),
               'self_care_to_ask': gap('someone at home', seen), 'plan_to_ask': gap('a plan if it worsens', seen),
               'facility_stays': len(stays), 'facility_patient_days': sum(f['days'] for f in stays.values()), 'facility_stays_back_in_hospital_within_30_days': sum(bool(f['back']) for f in stays.values())}
    return {'arrivals': seen, 'facility': list(stays.values()), 'summary': summary}


def main(argv):
    if argv[:1] == ['arrivals']:
        db = store.connect()
        for a in arrivals(argv[1]):
            s = screen(a, argv[1], db)
            print(f"{s['patient']}  day {s['days']} after discharge {s['discharge']} to {s['went']}  ({s['reason'] or 'no stated reason'})")
            for need, (state, words) in s['needs'].items(): print(f'   {need:26} {state:14} {words}')
            for o in s['offer']: print('   offer:', o)
        return
    if argv[:1] == ['replay']:
        out = replay(argv[1])
        if len(argv) > 2: Path(argv[2]).write_text(json.dumps(out))
        print(json.dumps(out['summary'], indent=1))
        return
    print(__doc__)


if __name__ == '__main__':
    main(sys.argv[1:])
