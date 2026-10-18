"""Agere acts: the workflow executor for routed plans. A step advances only on a receipt from a counterparty.

    out = act(plan, workspace='pilot')        # {'actions': [...], 'summary': {...}}  (in memory; no store)

The durable path is plans/backbone.py, which runs the same executor (run_patient) per work item and persists items,
receipts and follow-up work in the workspace action store. act() is the stateless preview of exactly that logic.

Per routed patient the program's workflow template (programs.json `workflow` + `counterparty`) is walked step by step:
outreach attempts -> consent -> counterparty eligibility / benefit check -> prior authorization (before enrollment for
plan-benefit PT, after the pharmacist visit for GDMT medication starts) with an appeal path -> enrollment -> PCP notice ->
first-visit scheduling -> first visit (attended, rescheduled, no-show -> rebook) -> completion report -> the closed-loop
check (left `pending_evaluation` for plans/completion.py). PharmD barrier review starts with a pharmacist chart review.
Coverage-check patients go to a simulated eligibility clearinghouse; care-team-review patients get a manual task with no
counterparty (it stays `proposed` until a person records the outcome).

Every request goes to the patient's simulated counterparty (plans/counterparty.py) and every step records its receipt
(counterparty id, timestamp from as_of, outcome code, reason, probability basis). Nothing is marked done without one.
Unreachable, declined, denied, upheld-on-appeal, dropped and not-scheduled outcomes create follow-up work (PCP notice,
alternate program, appeal, recontact, records request, reroute) instead of ending silently. `through` (an ISO date)
withholds receipts dated after it, leaving the step `awaiting_counterparty`: re-running later reveals them.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import hashlib

from . import catalog as catmod
from .counterparty import SimulatedCounterparty

POSITIVE = {'reached', 'consented', 'reviewed', 'eligible', 'approved', 'overturned', 'enrolled', 'scheduled', 'attended',
            'completed', 'coverage_found', 'delivered'}
TERMINAL = {'unreachable', 'declined', 'records_needed', 'eligibility_denied', 'coverage_denied', 'prior_auth_denied',
            'not_scheduled', 'dropped_after_no_show', 'dropped_out', 'completed', 'coverage_found', 'coverage_not_found'}
OUTCOME_ORDER = ('unreachable', 'declined', 'records_needed', 'eligibility_denied', 'coverage_denied', 'prior_auth_denied',
                 'not_scheduled', 'dropped_after_no_show', 'dropped_out', 'completed', 'coverage_found', 'coverage_not_found',
                 'awaiting_care_team')


def _value(field):
    return field.get('value') if isinstance(field, dict) else field


def _h(*parts, n=12):
    return hashlib.sha256('|'.join(str(p) for p in parts).encode()).hexdigest()[:n].upper()


def barriers_of(gap):
    """Current barriers on the patient's missing pillars (with the barrier-specific action), and stale history."""
    cur, stale = [], []
    gap = gap or {}
    for name, p in (gap.get('pillars') or {}).items():
        a = p.get('barrier_assessment') or {}
        for b in _value(p.get('barriers')) or []:
            cur.append({'pillar': name, 'pillar_status': p.get('status'), 'suggested_action': a.get('action'),
                        'usable_agents': a.get('usable_agents'), 'flags': [f.get('flag') for f in a.get('flags') or []], **b})
        for b in _value(p.get('stale_barriers')) or []:
            stale.append({'pillar': name, **b})
    return cur, stale


def target_pillars(gap):
    gap = gap or {}
    return sorted(set(_value(gap.get('missing_pillars')) or []) | set(_value(gap.get('barrier_pillars')) or [])
                  | set(_value(gap.get('barrier_documented_pillars')) or []))


def patient_facts(row, extra=None):
    """Facts the counterparty modifiers read: from the routed row, plus extra (e.g. recent_acute_30d from the semantic layer)."""
    gap = row.get('gap') or {}
    cov = row.get('coverage') or {}
    product, lob = _value(cov.get('product')), _value(cov.get('line_of_business'))
    cur, _ = barriers_of(gap)
    types = {b['barrier_type'] for b in cur}
    pf = row.get('patient_facts') or {}
    age = _value(row.get('age'))
    days = _value(gap.get('days_since_ed'))
    f = {'product': product, 'line_of_business': lob, 'no_active_coverage': not _value(cov.get('active')),
         'medicaid': product == 'medicaid' or lob == 'medicaid', 'dual': (lob == 'dual') or str(product or '').startswith('dual'),
         'interpreter_needed': bool(pf.get('interpreter_needed')) or
                               (str(pf.get('preferred_language') or '').strip().lower() not in ('', 'en', 'eng', 'english', 'unknown')),
         'age_80_plus': age is not None and age >= 80, 'target_pillars': target_pillars(gap),
         'missing_3plus': len(_value(gap.get('missing_pillars')) or []) >= 3,
         'barrier_on_target': bool(cur), 'barrier_cost_only': bool(types) and types <= {'cost_access'},
         'barrier_declined': 'patient_declined' in types,
         'ed_within_30d': days is not None and days <= 30, 'ed_over_60d': days is not None and days > 60}
    f.update(extra or {})
    return f


def _owner(kind, row, slot):
    att = row.get('attribution') or {}
    if kind == 'attributed_practice':
        return {'kind': 'practice', 'id': _value(att.get('practice_id')) or _value(att.get('npi')) or 'unattributed'}
    if kind == 'program':
        return {'kind': 'vendor_slot', 'id': (slot or {}).get('slot_id')}
    return {'kind': 'team', 'id': 'care_team'}


def _alternates(cat, program, row):
    product = _value((row.get('coverage') or {}).get('product'))
    out = []
    for p in catmod.programs_of(cat, program.get('kind')):
        if p['id'] == program['id'] or p.get('role') == 'barrier_review':
            continue
        prods = (p.get('eligibility') or {}).get('products')
        if not prods or product in prods:
            out.append(p['id'])
    return out or [f"care_team_review:{program.get('kind')}"]


class _Run:
    def __init__(self, row, program, slot, *, workspace, as_of, cat, facts, through, ledger, scenario):
        self.row, self.program, self.slot, self.cat = row, program, slot or {}, cat
        self.as_of = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of)[:10])
        self.through = date.fromisoformat(through[:10]) if isinstance(through, str) else through
        self.cp = SimulatedCounterparty(program, slot, workspace=workspace, patient_id=row['patient_id'], as_of=self.as_of,
                                        facts=facts, scenario=scenario, ledger=ledger,
                                        hours=tuple((cat.get('simulation') or {}).get('timestamp_hours') or (9, 17)))
        self.action_id = 'ACT-' + _h(self.cp.seed)
        self.steps, self.followups, self.milestones = [], [], Counter()
        self.halted = False  # a receipt is dated after `through`: everything after waits

    def send(self, step, day, attempt=1, **extra):
        """Submit one request; -> the receipt, or None when the receipt is not yet due (through) or the run is halted."""
        if self.halted:
            return None
        rid = f'{self.action_id}:{step}:{attempt}'
        prev = next((s['receipt']['at'] for s in reversed(self.steps) if s.get('receipt')), None)
        rec = self.cp.submit({'request_id': rid, 'step': step, 'attempt': attempt, 'day': day, 'not_before': prev, **extra})
        sent = (self.as_of + timedelta(days=int(day))).isoformat()
        entry = {'step': step, 'attempt': attempt, 'request_id': rid, 'sent_on': sent, 'counterparty_id': rec['counterparty_id']}
        if self.through is not None and date.fromisoformat(rec['at'][:10]) > self.through:
            entry.update(status='awaiting_counterparty', receipt=None)
            self.steps.append(entry)
            self.halted = True
            return None
        entry.update(status='done' if rec['outcome'] in POSITIVE else ('moved' if rec['outcome'] == 'rescheduled' else 'failed'),
                     receipt=rec)
        self.steps.append(entry)
        return rec

    def manual(self, step, day, note):
        self.steps.append({'step': step, 'attempt': 1, 'request_id': None, 'sent_on': None, 'counterparty_id': 'manual',
                           'status': 'proposed', 'receipt': None, 'due': (self.as_of + timedelta(days=int(day))).isoformat(),
                           'note': note})

    def follow(self, trigger, day, receipt=None, **extra):
        spec = ((self.cat.get('simulation') or {}).get('followups') or {}).get(trigger) or {'type': 'care_team_review', 'owner': 'care_team',
                                                                                               'due_days': 7, 'detail': trigger}
        f = {'followup_id': 'FU-' + _h(self.action_id, trigger), 'type': spec['type'], 'trigger': trigger,
             'owner': _owner(spec.get('owner'), self.row, self.slot), 'detail': spec.get('detail'),
             'due': (self.as_of + timedelta(days=int(day) + int(spec.get('due_days') or 7))).isoformat(),
             'state': 'open', 'from_receipt': (receipt or {}).get('receipt_id'), 'reason': (receipt or {}).get('reason')}
        if spec['type'] == 'alternate_program':
            f['alternatives'] = _alternates(self.cat, self.program, self.row)
        f.update(extra)
        self.followups.append(f)
        return f


def run_patient(row, program, slot, *, workspace, as_of, cat, facts=None, through=None, ledger=None, scenario=None):
    """-> the action for one routed patient (see module doc)."""
    run = _Run(row, program, slot, workspace=workspace, as_of=as_of, cat=cat, facts=facts or patient_facts(row),
               through=through, ledger=ledger, scenario=scenario)
    state = _walk(run, row, program)
    wf = program.get('workflow') or {}
    m = run.milestones
    cur, stale = barriers_of(row.get('gap'))
    enrolled_on = next((s['receipt']['at'][:10] for s in run.steps if s['step'] == 'enrollment' and s.get('receipt')), None)
    loop = None
    if enrolled_on and row.get('route') == 'enroll':
        comp = program.get('completion') or {}
        loop = {'criterion': comp.get('kind'), 'label': wf.get('completion_label'), 'window_days': comp.get('window_days'),
                'enrolled_on': enrolled_on,
                'due': (date.fromisoformat(enrolled_on) + timedelta(days=int(comp.get('window_days') or 90))).isoformat(),
                'status': 'pending_evaluation', 'target_pillars': target_pillars(row.get('gap')) if row.get('gap', {}).get('type') == 'gdmt_pillar_missing' else None}
    return {'action_id': run.action_id, 'patient_id': row['patient_id'], 'route': row['route'], 'program_id': program['id'],
            'program': program.get('name'), 'agent': wf.get('agent'), 'vendor_slot': (slot or {}).get('slot_id'),
            'vendor': (slot or {}).get('display'), 'counterparty_id': run.cp.id, 'seed': run.cp.seed, 'state': state,
            'milestones': dict(m), 'steps': run.steps, 'followups': run.followups, 'closed_loop': loop,
            'barriers': cur, 'stale_barriers': stale, 'facts': {k: v for k, v in (facts or {}).items() if v not in (None, False, [], '')},
            'through': run.through.isoformat() if run.through else None, 'simulated_counterparty': True}


def _walk(run, row, program):
    m = run.milestones
    route = row['route']
    cfg = program.get('counterparty') or {}
    if route == 'care_team_review':
        run.manual('care_team_review', 7, 'no program accepts this coverage; a care-team member records the decision')
        return 'awaiting_care_team'
    if route == 'coverage_check':
        rec = run.send('coverage_inquiry', 1)
        if rec is None:
            return 'proposed'
        m[rec['outcome']] += 1
        if rec['outcome'] == 'coverage_found':
            run.follow('coverage_found', 1, rec)
        else:
            run.follow('coverage_not_found', 1, rec)
            run.send('pcp_notification', 1, to=_value((row.get('attribution') or {}).get('npi')))
        return rec['outcome']
    day = 0
    if cfg.get('chart_review') is not None:
        rec = run.send('chart_review', 1)
        if rec is None:
            return 'proposed'
        day = 1
        if rec['outcome'] == 'records_needed':
            m['records_needed'] += 1
            run.follow('records_needed', day, rec)
            return 'records_needed'
        m['chart_reviewed'] += 1
    out = cfg.get('outreach') or {}
    days = list(out.get('attempt_days') or [1, 3, 6])
    reached = None
    for i in range(int(out.get('attempts') or 3)):
        rec = run.send('patient_outreach', day + (days[i] if i < len(days) else days[-1] + 2 * i), attempt=i + 1)
        if rec is None:
            return 'outreach_in_progress' if m['outreach_attempted'] else 'proposed'
        m['outreach_attempted'] = 1
        if rec['outcome'] == 'reached':
            reached = rec
            break
    if reached is None:
        m['unreachable'] += 1
        last = run.steps[-1]
        run.follow('unreachable', int(_day(run, last)), last['receipt'])
        run.send('pcp_notification', _day(run, last), attempt=1, to=_value((row.get('attribution') or {}).get('npi')))
        return 'unreachable'
    m['reached'] += 1
    day = _day(run, run.steps[-1])
    rec = run.send('consent', day)
    if rec is None:
        return 'reached'
    if rec['outcome'] == 'declined':
        m['declined'] += 1
        run.follow('declined', day, rec)
        return 'declined'
    rec = run.send('eligibility_check', day + 1)
    if rec is None:
        return 'consented'
    day += 1
    if rec['outcome'] in ('eligibility_denied', 'coverage_denied'):
        m[rec['outcome']] += 1
        run.follow(rec['outcome'], day, rec)
        run.send('pcp_notification', day, to=_value((row.get('attribution') or {}).get('npi')))
        return rec['outcome']
    m['eligible'] += 1
    pa_required = bool((rec.get('detail') or {}).get('prior_auth_required'))
    pa_when = (cfg.get('prior_auth') or {}).get('when', 'before_enrollment')
    if pa_required and pa_when == 'before_enrollment':
        ok, day = _prior_auth(run, row, day + 2)
        if ok is None:
            return 'eligible'
        if not ok:
            return 'prior_auth_denied'
    rec = run.send('enrollment', day + 1)
    if rec is None:
        return 'eligible'
    day += 1
    m['enrolled'] += 1
    run.send('pcp_notification', day, to=_value((row.get('attribution') or {}).get('npi')))
    wf = program.get('workflow') or {}
    if wf.get('first_visit'):
        visit_day = day + int(wf.get('first_visit_days') or 5)
        rec = run.send('schedule_first_visit', day, visit_date=(run.as_of + timedelta(days=visit_day)).isoformat())
        if rec is None:
            return 'enrolled'
        if rec['outcome'] == 'not_scheduled':
            m['not_scheduled'] += 1
            run.follow('scheduling_failed', day, rec)
            return 'not_scheduled'
        m['scheduled'] += 1
        attempt, after = 1, None
        while True:
            rec = run.send('first_visit', visit_day, attempt=attempt, after=after)
            if rec is None:
                return 'scheduled'
            if rec['outcome'] == 'attended':
                m['first_visit_attended'] += 1
                break
            if attempt >= 3:
                m['dropped_after_no_show'] += 1
                run.follow('dropped_after_no_show', visit_day, rec)
                return 'dropped_after_no_show'
            if rec['outcome'] == 'rescheduled':
                m['rescheduled'] += 1
                visit_day += 7
                attempt, after = attempt + 1, 'rescheduled'
                continue
            m['no_show'] += 1
            if after == 'no_show':
                m['dropped_after_no_show'] += 1
                run.follow('dropped_after_no_show', visit_day, rec)
                return 'dropped_after_no_show'
            rr = run.send('reschedule', visit_day + 2)
            if rr is None:
                return 'no_show'
            if rr['outcome'] == 'dropped':
                m['dropped_after_no_show'] += 1
                run.follow('dropped_after_no_show', visit_day + 2, rr)
                return 'dropped_after_no_show'
            m['rescheduled'] += 1
            visit_day += 9
            attempt, after = attempt + 1, 'no_show'
        day = visit_day
    if pa_required and pa_when == 'after_first_visit':
        ok, day = _prior_auth(run, row, day + 2)
        if ok is None:
            return 'first_visit_attended'
        if not ok:
            return 'prior_auth_denied'
    comp_day = max(day + 7, int(wf.get('completion_days') or 30))
    rec = run.send('completion_report', comp_day)
    if rec is None:
        return 'first_visit_attended' if m['first_visit_attended'] else 'enrolled'
    if rec['outcome'] == 'completed':
        m['completed'] += 1
        return 'completed'
    m['dropped_out'] += 1
    run.follow('dropped_out', comp_day, rec)
    return 'dropped_out'


def _day(run, step):
    rec = step.get('receipt')
    return (date.fromisoformat(rec['at'][:10]) - run.as_of).days if rec else 0


def _prior_auth(run, row, day):
    """-> (approved: True/False/None when pending, day)."""
    m = run.milestones
    rec = run.send('prior_auth', day)
    if rec is None:
        return None, day
    m['prior_auth_requested'] += 1
    if rec['outcome'] == 'approved':
        m['prior_auth_approved'] += 1
        return True, day
    m['prior_auth_denied'] += 1
    appeal_days = int(((run.program.get('counterparty') or {}).get('prior_auth') or {}).get('appeal', {}).get('days') or 10)
    fu = run.follow('prior_auth_denied', day, rec)
    ap = run.send('appeal', day + appeal_days)
    if ap is None:
        fu['state'] = 'appeal_submitted'
        return None, day
    fu.update(state='resolved', resolution=ap['outcome'], resolution_receipt=ap['receipt_id'])
    if ap['outcome'] == 'overturned':
        m['appeal_overturned'] += 1
        return True, day + appeal_days
    m['appeal_upheld'] += 1
    run.follow('appeal_upheld', day + appeal_days, ap)
    run.send('pcp_notification', day + appeal_days, attempt=2, to=_value((row.get('attribution') or {}).get('npi')))
    return False, day + appeal_days


def _programs(cat, plan):
    return {p['id']: p for p in cat['programs']}


def actionable(row):
    return row.get('route') in ('enroll', 'coverage_check', 'care_team_review')


def program_for(cat, plan, row):
    """(program, slot) the row's work runs through."""
    progs = _programs(cat, plan)
    if row['route'] == 'care_team_review':
        kind = plan.get('program_kind')
        base = next((p for p in catmod.programs_of(cat, kind) if p.get('role') != 'barrier_review'), catmod.coverage_program(cat))
        return {**base, 'id': f'care_team_review:{kind}', 'name': f'Care-team review ({kind})', 'counterparty': {},
                'workflow': {'agent': 'Care team (manual)'}}, None
    prog = progs.get(row.get('program_id')) or catmod.coverage_program(cat)
    return prog, row.get('vendor_slot') or (prog.get('vendor_slots') or [None])[0]


def act(plan, *, workspace='adhoc', catalog=None, facts=None, through=None, scenario=None):
    """-> {'actions', 'summary'} for a routed plan (router.route), in memory. facts: optional {patient_id: {...}} extra facts."""
    cat = catalog or catmod.load()
    actions = []
    for r in plan['patients']:
        if not actionable(r):
            continue
        prog, slot = program_for(cat, plan, r)
        actions.append(run_patient(r, prog, slot, workspace=workspace, as_of=plan['as_of'], cat=cat,
                                   facts=patient_facts(r, (facts or {}).get(r['patient_id'])), through=through, scenario=scenario))
    return {'actions': actions, 'summary': summarize(actions)}


FUNNEL = (('routed', lambda m, a: True), ('outreach_attempted', lambda m, a: m.get('outreach_attempted')),
          ('reached', lambda m, a: m.get('reached')), ('consented', lambda m, a: m.get('reached') and not m.get('declined')),
          ('eligible', lambda m, a: m.get('eligible')), ('enrolled', lambda m, a: m.get('enrolled')),
          ('first_visit_scheduled', lambda m, a: m.get('scheduled')), ('first_visit_attended', lambda m, a: m.get('first_visit_attended')),
          ('completed', lambda m, a: m.get('completed')))


def _counts(actions):
    c = Counter()
    for a in actions:
        m = a['milestones']
        for k in ('reached', 'unreachable', 'declined', 'eligibility_denied', 'coverage_denied', 'prior_auth_requested',
                  'prior_auth_denied', 'appeal_overturned', 'appeal_upheld', 'enrolled', 'scheduled', 'first_visit_attended',
                  'completed', 'dropped_out', 'dropped_after_no_show', 'not_scheduled', 'records_needed', 'coverage_found',
                  'coverage_not_found'):
            if m.get(k):
                c[k] += 1
        if m.get('no_show'):
            c['no_show_ever'] += 1
        if m.get('rescheduled'):
            c['rescheduled_ever'] += 1
        if m.get('eligibility_denied') or m.get('coverage_denied') or (m.get('prior_auth_denied') and m.get('appeal_upheld')):
            c['denied'] += 1
        if a['state'] == 'awaiting_care_team':
            c['awaiting_care_team'] += 1
    return dict(sorted(c.items()))


def summarize(actions):
    """Per-plan counts by outcome, funnel, by program and vendor slot, follow-ups and barriers."""
    funnel_rows = [a for a in actions if a['route'] == 'enroll']
    funnel = [{'stage': name, 'patients': sum(1 for a in funnel_rows if fn(a['milestones'], a))} for name, fn in FUNNEL]
    by_prog, by_slot = defaultdict(list), defaultdict(list)
    for a in actions:
        by_prog[a['program_id']].append(a)
        if a.get('vendor_slot'):
            by_slot[a['vendor_slot']].append(a)
    return {'actions': len(actions), 'by_route': dict(Counter(a['route'] for a in actions)),
            'final_state': {k: v for k, v in sorted(Counter(a['state'] for a in actions).items(),
                                                     key=lambda kv: (OUTCOME_ORDER.index(kv[0]) if kv[0] in OUTCOME_ORDER else 99, kv[0]))},
            'outcomes': _counts(actions), 'funnel': funnel,
            'by_program': {k: {'actions': len(v), 'outcomes': _counts(v)} for k, v in sorted(by_prog.items())},
            'by_vendor_slot': {k: {'actions': len(v), 'outcomes': _counts(v)} for k, v in sorted(by_slot.items())},
            'followups': dict(Counter(f['type'] for a in actions for f in a['followups'])),
            'followups_open': sum(1 for a in actions for f in a['followups'] if f['state'] == 'open'),
            'barrier_reviews': sum(1 for a in actions if a['program_id'] == 'pharmd_barrier_review'),
            'actions_with_barriers': sum(1 for a in actions if a['barriers']),
            'barriers_by_type': dict(Counter(b['barrier_type'] for a in actions for b in a['barriers'])),
            'awaiting_counterparty': sum(1 for a in actions if any(s['status'] == 'awaiting_counterparty' for s in a['steps'])),
            'receipts': sum(1 for a in actions for s in a['steps'] if s.get('receipt')),
            'steps_done_without_receipt': sum(1 for a in actions for s in a['steps'] if s['status'] == 'done' and not s.get('receipt'))}
