"""Simulated counterparties: the in-process adapters a plan's steps are sent to (no external system is contacted).

    cp = SimulatedCounterparty(program, slot, workspace='pilot', patient_id='P1', as_of='2030-05-27', facts={...})
    receipt = cp.submit({'request_id': ..., 'step': 'patient_outreach', 'attempt': 1, 'day': 1})

Each program / vendor slot in programs.json carries a `counterparty` block: the stage probabilities of a realistic
enrollment funnel (reach per outreach attempt, decline, eligibility / coverage denial, prior authorization required and
denied, appeal overturned, scheduling failure, reschedule, no-show, completion) and modifiers that multiply a stage
probability when a patient fact holds (a configured 0 or 1 is a forced case: never / always) (recent ED visit -> fewer declines; documented barrier on the drug -> more declines;
interpreter needed -> harder to reach; no active coverage -> the clearinghouse finds none most of the time). A slot may
override any rate (`slot_overrides`).

Determinism: every disposition is one uniform draw u = sha256(seed | step | attempt) with seed = workspace | patient |
program | plan as_of (| scenario). The same plan therefore always gives the same outcomes, each stage draw is independent
of the others (changing one rate never reshuffles another stage), and a duplicate submission with the same request_id
returns the original receipt (the adapter keeps a request ledger; the backbone persists it in work_requests).

Receipts: {receipt_id, request_id, counterparty_id ('sim:<slot_id>'), counterparty, step, attempt, outcome, reason, at
(timestamp from as_of + the step's day offset), detail, basis {probability, draw, modifiers}, simulated: True}.
These are synthetic planning assumptions for engineering tests, not measured performance of any vendor or payer.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib

VERSION = 'simulated-counterparty/1'
CLIP = (0.01, 0.97)


def uniform(*parts):
    h = hashlib.sha256('|'.join(str(p) for p in parts).encode()).hexdigest()
    return int(h[:15], 16) / float(16 ** 15)


def _pick(options, *parts):
    options = list(options or [])
    if not options:
        return None
    return options[int(uniform(*parts, 'pick') * len(options)) % len(options)]


class SimulatedCounterparty:
    """One adapter per (program, vendor slot, patient plan cycle). submit() is the only entry point."""

    def __init__(self, program, slot, *, workspace, patient_id, as_of, facts=None, scenario=None, ledger=None, hours=(9, 17)):
        self.program, self.slot = program, slot or {}
        self.cfg = dict(program.get('counterparty') or {})
        over = (self.cfg.get('slot_overrides') or {}).get(self.slot.get('slot_id')) or {}
        self.rates = {**{k: v for k, v in self.cfg.items() if k.startswith('p_')}, **over}
        self.outreach = {**(self.cfg.get('outreach') or {}), **({'p_reach': over['p_reach']} if 'p_reach' in over else {})}
        self.facts = facts or {}
        self.as_of = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of)[:10])
        self.seed = '|'.join(str(x) for x in (workspace, patient_id, program['id'], self.as_of.isoformat()) + ((scenario,) if scenario else ()))
        self.id = f"sim:{self.slot.get('slot_id') or program['id']}"
        self.name = self.slot.get('display') or program.get('name')
        self.ledger = ledger if ledger is not None else {}
        self.hours = hours

    # --- probabilities ---------------------------------------------------------------------------------------------------

    def p(self, stage, base):
        """Stage probability after modifiers, clipped to CLIP. A configured 0 or 1 is a forced case (never / always) and
        is not modified."""
        mods = []
        val = float(base)
        if val <= 0 or val >= 1:
            return (0.0 if val <= 0 else 1.0), mods
        for m in self.cfg.get('modifiers') or []:
            if m.get('stage') == stage and self.facts.get(m.get('fact')):
                val *= float(m['multiply'])
                mods.append({'fact': m['fact'], 'multiply': m['multiply'], 'why': m.get('why')})
        return min(max(val, CLIP[0]), CLIP[1]), mods

    def at(self, day, step, attempt):
        lo, hi = self.hours
        hour = lo + int(uniform(self.seed, step, attempt, 'hour') * max(1, hi - lo))
        minute = int(uniform(self.seed, step, attempt, 'minute') * 60)
        d = self.as_of + timedelta(days=int(day))
        return datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')

    def _after(self, at, not_before, step, attempt):
        """Receipts of one patient's chain are ordered: never earlier than the previous receipt."""
        if not not_before or at > not_before:
            return at
        t = datetime.fromisoformat(not_before.replace('Z', '+00:00')) + timedelta(minutes=5 + int(uniform(self.seed, step, attempt, 'gap') * 50))
        return t.isoformat().replace('+00:00', 'Z')

    # --- the adapter -----------------------------------------------------------------------------------------------------

    def submit(self, request):
        """request: {request_id, step, attempt, day, ...} -> receipt (idempotent on request_id)."""
        rid = request['request_id']
        if rid in self.ledger:
            return dict(self.ledger[rid], duplicate_submission=True)
        step, attempt = request['step'], int(request.get('attempt') or 1)
        u = uniform(self.seed, step, attempt)
        outcome, reason, prob, mods, detail = self._dispose(step, attempt, u, request)
        receipt = {'receipt_id': 'RCPT-' + hashlib.sha256(f'{rid}|{outcome}'.encode()).hexdigest()[:12].upper(),
                   'request_id': rid, 'counterparty_id': f"sim:practice_inbox:{request.get('to') or 'unattributed'}" if step == 'pcp_notification' else self.id,
                   'counterparty': 'attributed practice inbox' if step == 'pcp_notification' else self.name, 'step': step, 'attempt': attempt,
                   'outcome': outcome, 'reason': reason, 'at': self._after(self.at(request.get('day') or 0, step, attempt),
                                                                           request.get('not_before'), step, attempt),
                   'detail': detail, 'basis': {'probability': None if prob is None else round(prob, 4), 'draw': round(u, 4),
                                                'modifiers': mods},
                   'simulated': True, 'adapter': VERSION}
        self.ledger[rid] = receipt
        return receipt

    def _dispose(self, step, attempt, u, request):
        r = self.rates
        if step == 'patient_outreach':
            p, mods = self.p('reach', self.outreach.get('p_reach', 0.5))
            return ('reached', 'patient answered and spoke with the program', p, mods, None) if u < p else \
                ('no_answer', 'no answer; voicemail / SMS left', p, mods, None)
        if step == 'consent':
            p, mods = self.p('decline', r.get('p_decline', 0.2))
            if u < p:
                return 'declined', _pick(self.cfg.get('decline_reasons') or ['not interested', 'prefers to discuss with own doctor first',
                                                                             'concerned about cost or side effects', 'too busy right now'],
                                         self.seed, step), p, mods, None
            return 'consented', 'verbal consent to enroll recorded by the program', p, mods, None
        if step == 'chart_review':
            p, mods = self.p('records', (self.cfg.get('chart_review') or {}).get('p_records_needed', 0.05))
            return ('records_needed', 'chart review could not verify the documented barrier; outside records needed', p, mods, None) \
                if u < p else ('reviewed', 'pharmacist reviewed the documented barriers', p, mods, None)
        if step == 'eligibility_check':
            if self.facts.get('no_active_coverage'):
                return 'coverage_denied', 'no active coverage at the counterparty check', 1.0, [{'fact': 'no_active_coverage'}], None
            pe, mods = self.p('eligibility', r.get('p_eligibility_denied', 0.0))
            if u < pe:
                return 'eligibility_denied', _pick(self.cfg.get('eligibility_reasons') or ['not eligible for this program'],
                                                   self.seed, step), pe, mods, None
            pc, mods2 = self.p('coverage', r.get('p_coverage_denied', 0.0))
            u2 = uniform(self.seed, step, attempt, 'coverage')
            if u2 < pc:
                return 'coverage_denied', _pick(self.cfg.get('coverage_reasons') or ['benefit not covered'], self.seed, step, 'c'), \
                    pc, mods2, None
            pa = self._pa_required()
            return 'eligible', 'eligibility and benefit confirmed by the counterparty', pe, mods, {'prior_auth_required': pa}
        if step == 'prior_auth':
            cfg = self.cfg.get('prior_auth') or {}
            p, mods = self.p('prior_auth', cfg.get('p_denied', 0.2))
            if u < p:
                return 'denied', _pick(cfg.get('denial_reasons') or ['not medically necessary'], self.seed, step), p, mods, \
                    {'appeal_allowed': bool(cfg.get('appeal'))}
            return 'approved', 'prior authorization approved', p, mods, None
        if step == 'appeal':
            cfg = (self.cfg.get('prior_auth') or {}).get('appeal') or {}
            p, mods = self.p('appeal', cfg.get('p_overturned', 0.5))
            return ('overturned', 'appeal approved on review of the submitted documentation', p, mods, None) if u < p else \
                ('upheld', 'denial upheld on appeal', p, mods, None)
        if step == 'enrollment':
            return 'enrolled', 'enrollment accepted', 1.0, [], {'confirmation_id': 'CP-' + hashlib.sha256(
                (self.seed + '|enrollment').encode()).hexdigest()[:10].upper()}
        if step == 'schedule_first_visit':
            p, mods = self.p('scheduling', r.get('p_scheduling_failed', 0.03))
            if u < p:
                return 'not_scheduled', 'no first-visit slot accepted by the patient', p, mods, None
            return 'scheduled', 'first visit booked', p, mods, {'visit_date': request.get('visit_date')}
        if step == 'first_visit':
            if attempt == 1:
                pr, m1 = self.p('rescheduled', r.get('p_rescheduled', 0.1))
                pn, m2 = self.p('no_show', r.get('p_no_show', 0.15))
                if u < pr:
                    return 'rescheduled', 'patient moved the visit before it happened', pr, m1, None
                if u < pr + pn:
                    return 'no_show', 'patient did not attend the first visit', pn, m2, None
                return 'attended', 'first visit attended', 1 - pr - pn, m1 + m2, None
            p, mods = self.p('no_show', r.get('p_second_no_show', 0.25) if request.get('after') == 'no_show' else r.get('p_no_show', 0.15))
            return ('no_show', 'patient did not attend the rebooked visit', p, mods, None) if u < p else \
                ('attended', 'rebooked visit attended', 1 - p, mods, None)
        if step == 'reschedule':
            p, mods = self.p('reschedule', r.get('p_reschedule_after_no_show', 0.5))
            return ('rescheduled', 'visit rebooked after the no-show', p, mods, None) if u < p else \
                ('dropped', 'could not rebook after the no-show', p, mods, None)
        if step == 'completion_report':
            p, mods = self.p('completed', r.get('p_completed', 0.6))
            return ('completed', self.program.get('workflow', {}).get('completion_label') or 'program completed', p, mods, None) \
                if u < p else ('dropped_out', 'patient stopped before completing the program', p, mods, None)
        if step == 'coverage_inquiry':
            p, mods = self.p('coverage_found', r.get('p_coverage_found', 0.25))
            return ('coverage_found', _pick(self.cfg.get('found_reasons') or ['active coverage found'], self.seed, step), p, mods, None) \
                if u < p else ('coverage_not_found', _pick(self.cfg.get('not_found_reasons') or ['no active coverage found'],
                                                            self.seed, step), p, mods, None)
        if step == 'pcp_notification':
            return 'delivered', 'notice delivered to the attributed practice inbox', 1.0, [], {'to': request.get('to')}
        raise ValueError(f'Unknown counterparty step {step!r}')

    def _pa_required(self):
        cfg = self.cfg.get('prior_auth') or {}
        if not cfg or self.facts.get('product') not in (cfg.get('products') or []):
            return False
        applies = cfg.get('applies_to_pillars')
        if applies is not None and not set(applies) & set(self.facts.get('target_pillars') or []):
            return False
        p, _ = self.p('pa_required', cfg.get('p_required', 0.5))
        return uniform(self.seed, 'prior_auth_required') < p
