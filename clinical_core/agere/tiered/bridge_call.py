"""Bridge Call: a call two days after a hospital discharge. A prototype on synthetic records.

Nothing in this module contacts anyone, books anything or writes to a clinical system. It answers four questions with the
tiered stack and fixed rules, and keeps a local log:
  who is due a call today          -> the 'bridge_call_queue' recipe
  what the caller needs to know    -> the 'bridge_call_sheet' recipe, the discharge summary quoted by code, and the markers
                                      on record (advanced illness, social need, a facility discharge, self-care at home)
  who to call and what to ask      -> route(): rules in code, no model
  what to do with the answers      -> triage(): rules in code, no model
Every figure shown is what was on record by the call date; later information is not used.

    python -m clinical_core.agere.tiered.bridge_call queue 2029-08-16
    python -m clinical_core.agere.tiered.bridge_call replay 2029 [out.json]
"""
import json
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from . import bottles as bottle_store, readmission, router, store

LOG = Path('artifacts/agere-bridge-call/log.sqlite3')
ATTEMPTS = 3
QUESTIONS = (('worse', 'Since leaving hospital, is the breathing, swelling, pain or fever worse than on the day of discharge?'),
             ('medicines', 'Is every medicine on the discharge list in the home now?'),
             ('appointment', 'Is the date of the next appointment known?'))
EXTRA = {'advanced illness': ('plan', 'If this gets worse tonight, is there a plan and a number to call before going to the emergency department?'),
         'self-care at home': ('help', 'Is someone helping with medicines and meals today?'),
         'social need': ('need', 'Is anything about food, transport, housing or cost getting in the way of the discharge plan?')}
FACILITY_QUESTIONS = (('worse', 'Has there been a change in condition since arrival?'),
                      ('seen', "Has the facility's own clinician seen the patient since arrival?"),
                      ('medicines', 'Did the discharge medication list arrive, and does it match what is being given?'),
                      ('plan', 'Is there an order for who to call before a transfer back to the emergency department?'))


def _ask(question, bottles=None):
    outcome = router.answer(question, tiers=('bottles', 'recipes'), bottles=bottles or bottle_store.Bottles())
    return outcome.get('columns') or [], (outcome.get('rows') or []) if outcome['status'] == 'answered' else []


def queue(day, bottles=None):
    """The stack's answer to who is due a call on a date. -> [{patient, admit, discharge, ...}] (empty when nobody is due)."""
    columns, rows = _ask(f'Who is due a bridge call on {day}?', bottles)
    return [dict(zip(columns, r)) for r in rows if len(r) > 2 and r[2]]


def sheet(patient, discharge, bottles=None, db=None):
    """What a caller needs for one discharge: the stack's call sheet, the discharge summary quoted by code, and the markers.
    -> {'patient','discharge','facts': {item: value}, 'reason', 'instruction', 'went', 'markers': {driver: [why]}}."""
    own = db or store.connect()
    try:
        _, rows = _ask(f'What is on the call sheet for patient {patient} discharged on {discharge}?', bottles)
        facts = {r[0]: r[1] for r in rows}
        admit = (facts.get('stay') or '').split(' to ')[0] if ' to ' in (facts.get('stay') or '') else discharge
        by = str(date.fromisoformat(discharge) + timedelta(days=2))
        ids = [r[0] for r in own.execute("SELECT record_id FROM document WHERE patient_id = ? AND event_date >= ? AND event_date <= ? AND lower(document_type) LIKE '%discharge%' ORDER BY event_date",
                                         (patient, admit, by))]
        summaries = [' '.join(t['text'].split()) for t in store.texts(ids, own).values()]
        reason = next((got[1] for got in map(readmission.stated_reason, summaries) if got), None)
        instruction = next((got for got in map(readmission.follow_up_instruction, summaries) if got), None)
        markers = {d: [] for d in readmission.DRIVERS if d != 'emergency department threshold'}
        readmission.person_markers(own, patient, by, admit, markers)
        went = readmission.destination(own, patient, discharge, summaries)
        if readmission.FACILITY.search(went): markers['sent back from a facility'].append('discharged to a ' + went)
        elif readmission.HOME_HEALTH.search(went): markers['self-care at home'].append('discharged with home health')
    finally:
        if db is None: own.close()
    return {'patient': patient, 'admit': admit, 'discharge': discharge, 'call_date': by, 'facts': facts, 'reason': reason, 'instruction': instruction, 'went': went, 'markers': markers}


def route(s):
    """Rules in code. Who the call goes to, what is asked, and what the record already flags before anyone answers.
    -> {'to', 'interpreter', 'questions': [(key, words)], 'flags': [words], 'priority'}."""
    f, m = s['facts'], s['markers']
    facility = bool(m['sent back from a facility'])
    if facility:
        to, questions = 'the charge nurse at the ' + s['went'], list(FACILITY_QUESTIONS)
    else:
        to = 'the patient, or the person helping at home' if m['self-care at home'] else 'the patient'
        questions = list(QUESTIONS) + [EXTRA[d] for d in ('advanced illness', 'self-care at home', 'social need') if m[d]]
    flags = []
    if f.get('pharmacy fills since discharge (to day 2)') == '0': flags.append('no pharmacy fill on record since discharge')
    if f.get('next appointment') == 'none on record': flags.append('no appointment on record after discharge' + (f': the summary says "{s["instruction"]}"' if s['instruction'] else ''))
    if s['instruction'] is None: flags.append('the discharge summary on record gives no follow-up instruction')
    for d in m:
        if m[d]: flags.append(f"{d}: {'; '.join(m[d])}")
    prior = int(f.get('inpatient admissions in the prior 12 months') or 0)
    priority = 'first' if facility or prior >= 2 or m['advanced illness'] else 'standard'
    return {'to': to, 'interpreter': f.get('interpreter needed') == 'yes', 'language': f.get('preferred language', 'not recorded'), 'questions': questions, 'flags': flags, 'priority': priority}


def triage(answers, s, reached=True):
    """Rules in code. answers: {key: 'yes' | 'no'}. -> [{'to','when','why'}]; an empty list means no task.
    The agent never advises the patient and never changes an order: each task goes to a named role with the answer attached."""
    m, tasks = s['markers'], []
    add = lambda to, when, why: tasks.append({'to': to, 'when': when, 'why': why})
    if not reached:
        add('transitions nurse', 'today', f'not reached after {ATTEMPTS} attempts on day 2; the call is not closed until a person decides what happens next')
        return tasks
    facility = bool(m['sent back from a facility'])
    if answers.get('worse') == 'yes':
        add("the facility's clinician and the transitions nurse" if facility else 'transitions nurse', 'today, within 2 hours', 'reported worse since discharge')
    if answers.get('medicines') == 'no':
        add('pharmacist', 'today', 'the medication list did not arrive or does not match' if facility else 'not every discharge medicine is in the home')
    elif s['facts'].get('pharmacy fills since discharge (to day 2)') == '0' and not facility:
        add('pharmacist', 'next working day', 'says the medicines are in the home, but no fill is on record since discharge')
    if answers.get('appointment') == 'no' or (not facility and s['facts'].get('next appointment') == 'none on record'):
        add('scheduling', 'next working day', 'no follow-up visit known or on record' + (f'; the summary says "{s["instruction"]}"' if s['instruction'] else ''))
    if facility and answers.get('seen') == 'no':
        add("the facility's medical director", 'today', "the facility's clinician has not seen the patient since arrival")
    if answers.get('plan') == 'no':
        add("the facility's clinician" if facility else 'the patient\'s own clinician', 'this week',
            'no order for who to call before a transfer back' if facility else 'no plan for what to do if the illness worsens; ask whether a goals-of-care conversation is due')
    if answers.get('help') == 'no':
        add('home health or care management', 'today', 'nobody is helping with medicines and meals')
    if answers.get('need') == 'yes':
        add('social work', 'this week', 'a social need is getting in the way: ' + ('; '.join(m['social need']) or 'not on record before the call'))
    return tasks


def card(s, answers, tasks, reached=True):
    """The line left in the record for whoever sees the patient next, an emergency physician included."""
    if not reached: return f"Day-2 call {s['call_date']}: not reached after {ATTEMPTS} attempts. Passed to the transitions nurse."
    said = '; '.join(f"{k} {v}" for k, v in answers.items())
    return (f"Day-2 call {s['call_date']} after discharge {s['discharge']}" + (f" ({s['reason']})" if s['reason'] else '') + f": {said}. " +
            ('Open: ' + '; '.join(f"{t['to']} ({t['when']})" for t in tasks) + '.' if tasks else 'Nothing open.') +
            (f" Next appointment {s['facts']['next appointment']}." if s['facts'].get('next appointment') not in (None, 'none on record') else ' No appointment on record.'))


def log(s, r, answers, tasks, reached=True, path=LOG):
    """Local record of one call worked through the prototype. Nothing leaves this machine."""
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE IF NOT EXISTS call (at TEXT, patient_id TEXT, discharge_date TEXT, call_date TEXT, sent_to TEXT, reached INTEGER, answers TEXT, tasks TEXT, card TEXT)')
    db.execute('INSERT INTO call VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), s['patient'], s['discharge'], s['call_date'], r['to'], int(reached),
                                                                    json.dumps(answers), json.dumps(tasks), card(s, answers, tasks, reached)))
    db.commit(); db.close()


def replay(year):
    """Every day of one year: the queue, and each call's sheet and route. No answers exist for these patients, so only what the
    record shows before the call is counted. -> {'calls': [...], 'summary': {...}}."""
    bottles, db = bottle_store.Bottles(), store.connect()
    day, end, calls, questions, started = date(int(year), 1, 1), date(int(year), 12, 31), [], 0, time.monotonic()
    while day <= end:
        for due in queue(str(day), bottles):
            s = sheet(due['patient_id'], due['discharge_date'], bottles, db)
            r = route(s)
            calls.append({'p': s['patient'], 'admit': s['admit'], 'dis': s['discharge'], 'call': s['call_date'], 'facts': s['facts'], 'reason': s['reason'], 'instr': s['instruction'],
                          'went': s['went'], 'markers': {k: v for k, v in s['markers'].items() if v}, 'to': r['to'], 'interp': r['interpreter'], 'lang': r['language'],
                          'flags': r['flags'], 'priority': r['priority'], 'keys': [k for k, _ in r['questions']]})
            questions += 1
        questions += 1
        day += timedelta(days=1)
    db.close()
    n = len(calls)
    count = lambda test: sum(1 for c in calls if test(c))
    summary = {'year': year, 'calls_due': n, 'days_with_a_call': len({c['call'] for c in calls}), 'stack_questions': questions, 'seconds': round(time.monotonic() - started, 1),
               'to_a_facility_nurse': count(lambda c: 'facility' in c['to'] or 'charge nurse' in c['to']), 'to_patient_or_helper': count(lambda c: 'helping' in c['to']),
               'interpreter': count(lambda c: c['interp']), 'called_first': count(lambda c: c['priority'] == 'first'),
               'no_fill_by_day_2': count(lambda c: c['facts'].get('pharmacy fills since discharge (to day 2)') == '0'),
               'no_appointment_on_record': count(lambda c: c['facts'].get('next appointment') == 'none on record'),
               'no_instruction_in_summary': count(lambda c: c['instr'] is None),
               **{d: count(lambda c, d=d: d in c['markers']) for d in readmission.DRIVERS if d != 'emergency department threshold'}}
    return {'calls': calls, 'summary': summary}


def main(argv):
    if argv[:1] == ['queue']:
        for due in queue(argv[1]):
            s = sheet(due['patient_id'], due['discharge_date']); r = route(s)
            print(f"{s['patient']}  discharged {s['discharge']}  call {r['to']}  ({r['priority']})")
            for flag in r['flags']: print('   flag:', flag)
            for _, words in r['questions']: print('   ask: ', words)
        return
    if argv[:1] == ['replay']:
        out = replay(argv[1])
        if len(argv) > 2: Path(argv[2]).write_text(json.dumps(out))
        print(json.dumps(out['summary'], indent=1))
        return
    print(__doc__)


if __name__ == '__main__':
    main(sys.argv[1:])
