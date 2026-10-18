"""Agere acts: enrollment agents carry a routed plan through to enrollment.

Each patient the router sent to a program gets an agent run: verify eligibility, reach the patient, capture consent,
submit the enrollment to the program (PharmD GDMT service, ACCESS MSK virtual PT vendor, plan-benefit PT), schedule the
first visit, notify the attributed PCP, and set the closed-loop check that looks for the first fill change or PT claim.
Coverage-check patients get a coverage agent that confirms coverage and re-routes. Every step is dated from as_of and
every confirmation id is a stable hash, so the same plan always produces the same actions.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
import hashlib

AGENTS = {
    'pharmacy_gdmt': {'agent': 'Agere PharmD enrollment agent', 'program_label': 'PharmD GDMT optimization',
                      'first_visit': 'pharmacist medication review', 'first_visit_days': 5,
                      'loop_check': 'fill for each missing pillar or a documented contraindication', 'loop_days': 45},
    'virtual_pt': {'agent': 'Agere MSK enrollment agent', 'program_label': 'Virtual PT (ACCESS MSK)',
                   'first_visit': 'virtual PT evaluation (CPT 97161-97163)', 'first_visit_days': 4,
                   'loop_check': 'PT evaluation claim and 4+ treatment visits', 'loop_days': 30},
    'coverage_check': {'agent': 'Agere coverage agent', 'program_label': 'Coverage verification',
                       'first_visit': None, 'first_visit_days': None,
                       'loop_check': 'coverage confirmed and patient re-routed to the program', 'loop_days': 7},
}
TIMELINE = (('eligibility_verified', 0), ('patient_reached', 1), ('consent_captured', 1), ('enrollment_submitted', 1),
            ('enrollment_confirmed', 2))


def _cid(*parts):
    return hashlib.sha1('|'.join(str(p) for p in parts).encode()).hexdigest()[:10].upper()


def _value(field):
    return field.get('value') if isinstance(field, dict) else field


def act(plan):
    """-> {'actions': [...], 'summary': {...}} for a routed plan from router.route."""
    as_of = date.fromisoformat(plan['as_of'])
    kind = plan['program_kind']
    programs = {p['id']: p for p in plan.get('programs') or []}
    actions = []
    for r in plan['patients']:
        if r['route'] not in ('enroll', 'coverage_check'):
            continue
        agent_kind = kind if r['route'] == 'enroll' else 'coverage_check'
        spec = AGENTS.get(agent_kind) or AGENTS['coverage_check']
        prog = programs.get(r.get('program_id')) or {}
        slot = (r.get('vendor_slot') or {})
        pid = r['patient_id']
        conf = f"{'ENR' if r['route'] == 'enroll' else 'COV'}-{_cid(pid, r.get('program_id'), plan['as_of'])}"
        steps = [{'step': s, 'date': (as_of + timedelta(days=d)).isoformat(), 'status': 'done'} for s, d in TIMELINE]
        if r['route'] == 'coverage_check':
            steps = [dict(s, step=s['step'].replace('enrollment', 'coverage')) for s in steps]
        first = None
        if spec['first_visit']:
            first = {'visit': spec['first_visit'], 'date': (as_of + timedelta(days=spec['first_visit_days'])).isoformat(),
                     'with': slot.get('display') or prog.get('name')}
            steps.append({'step': 'first_visit_scheduled', 'date': steps[-1]['date'], 'status': 'done', 'detail': first})
        pcp = _value((r.get('attribution') or {}).get('npi')) or _value((r.get('attribution') or {}).get('practice_id'))
        steps.append({'step': 'pcp_notified', 'date': steps[-1]['date'], 'status': 'done', 'to': pcp})
        loop = {'check': spec['loop_check'], 'due': (as_of + timedelta(days=spec['loop_days'])).isoformat(),
                'status': 'scheduled'}
        steps.append({'step': 'closed_loop_check', 'date': loop['due'], 'status': 'scheduled', 'detail': loop})
        actions.append({'action_id': f"ACT-{_cid(conf)}", 'patient_id': pid, 'agent': spec['agent'],
                        'program': prog.get('name') or spec['program_label'], 'program_id': r.get('program_id'),
                        'vendor': slot.get('display'), 'status': 'enrolled' if r['route'] == 'enroll' else 'coverage_verified',
                        'confirmation_id': conf, 'first_visit': first, 'closed_loop': loop, 'steps': steps,
                        'gap': r.get('gap')})
    by_status = Counter(a['status'] for a in actions)
    return {'actions': actions,
            'summary': {'agents_run': len(actions), 'enrolled': by_status.get('enrolled', 0),
                        'coverage_verified': by_status.get('coverage_verified', 0),
                        'first_visits_scheduled': sum(1 for a in actions if a['first_visit']),
                        'pcps_notified': len({s['to'] for a in actions for s in a['steps'] if s['step'] == 'pcp_notified' and s['to']}),
                        'by_program': dict(Counter(a['program'] for a in actions)),
                        'by_vendor': dict(Counter(a['vendor'] for a in actions if a['vendor'])),
                        'closed_loop_checks_due': sorted({a['closed_loop']['due'] for a in actions})[:1]}}
