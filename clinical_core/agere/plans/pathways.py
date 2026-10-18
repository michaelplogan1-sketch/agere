"""Follow-on pathways after PharmD GDMT enrollment: GDMT titration and remote monitoring + home labs.

    snap = snapshot(semantic_path, patient_ids, as_of, cat)               # latest labs, vitals and current GDMT doses
    titration, monitoring = run_follow_ons(parent_action, row, cat, workspace=..., snap=snap.get(pid), through=...)

Programs are catalog data (programs.json, role 'follow_on', listed in the parent program's `follow_on` block with the
milestone that starts them). plans/backbone.py calls run() after each pharmacy_gdmt item and persists each follow-on as its
own work item in lane '<parent lane>:next:<kind>' (e.g. plan:pharmacy_gdmt:next:gdmt_titration), with follow-up work in
'<that lane>:followup:<type>'. The enrollment workflow of every existing program is unchanged.

Remote monitoring + home labs (starts at PharmD enrollment): RPM consent -> device shipment -> device activation (one retry)
-> home BMP draw -> 30 days of BP / heart-rate / weight readings checked against the program thresholds -> escalation to
the care team on a breach -> monthly review. Escalations feed the titration pathway (the next dose step is held).

GDMT titration (starts at the attended PharmD visit): up to `cycles` cycles, each: safety check of the latest potassium,
eGFR, systolic BP and heart rate against the program's safety_holds (holds are per pillar) -> the next dose step on the
configured ladder (start a missing pillar, or increase an on-board one) proposed to the PharmD -> e-prescription ->
patient instructions -> follow-up BMP after a RAS-inhibitor / MRA / SGLT2i change, whose result becomes the next cycle's
labs. A hold with no other candidate pillar, out-of-range labs, a rejected prescription or a monitoring escalation stops
the pathway with follow-up work for a person. Every step is a receipt from a simulated counterparty (plans/counterparty.py);
thresholds and ladders are planning data, not clinical guidance, and nothing is sent anywhere.
"""
from __future__ import annotations

from datetime import date, timedelta
import re
import sqlite3
from pathlib import Path

from .act import _Run, _value, barriers_of

SEP = ':next:'
MEASURES = ('potassium', 'egfr', 'systolic_bp', 'heart_rate', 'weight')


def follow_on_programs(cat, program):
    """[(follow-on program, spec)] for a parent program (its `follow_on` block)."""
    progs = {p['id']: p for p in cat['programs']}
    return [(progs[f['program_id']], f) for f in program.get('follow_on') or [] if f.get('program_id') in progs]


def lane_for(parent_lane, kind):
    return f'{parent_lane}{SEP}{kind}'


# --- workspace data ---------------------------------------------------------------------------------------------------

def _has(db, table, *cols):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return False
    have = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
    return set(cols) <= have


def _mg(name):
    m = re.search(r'(\d+(?:\.\d+)?)\s*MG', str(name or ''), re.I)
    return float(m.group(1)) if m else None


def snapshot(semantic_path, ids, as_of, cat):
    """{patient_id: {'measures': {concept: {value, date, record_id}}, 'meds': {pillar: {name, agent, mg, date}}}} from the
    workspace semantic layer: the latest measurement of each concept on or before as_of, and the latest fill per pillar class
    in the 180 days before as_of. Missing tables give empty snapshots (the pathway then asks for labs)."""
    out = {pid: {'measures': {}, 'meds': {}} for pid in ids or []}
    if not ids or semantic_path is None or not Path(semantic_path).is_file():
        return out
    pillars = ((cat.get('gaps') or {}).get('gdmt_pillar_missing') or {}).get('pillars') or {}
    cls_pillar = {c: name for name, p in pillars.items() for c in p.get('fill_classes') or []}
    lo = (date.fromisoformat(as_of) - timedelta(days=180)).isoformat()
    db = sqlite3.connect(f'file:{Path(semantic_path)}?mode=ro', uri=True)
    try:
        ids = list(ids)
        for i in range(0, len(ids), 500):
            ch = ids[i:i + 500]
            ph = ','.join('?' * len(ch))
            if _has(db, 'measurement', 'patient_id', 'concept', 'event_date', 'value_canonical'):
                rid = 'record_id' if _has(db, 'measurement', 'record_id') else 'NULL'
                rows = db.execute(f"SELECT patient_id, concept, event_date, value_canonical, {rid} FROM measurement "
                                  f"WHERE concept IN ({','.join('?' * len(MEASURES))}) AND event_date <= ? AND value_canonical IS NOT NULL "
                                  f"AND patient_id IN ({ph}) ORDER BY event_date", list(MEASURES) + [as_of] + ch)
                for pid, concept, d, v, r in rows:
                    try:
                        out[pid]['measures'][concept] = {'value': round(float(v), 2), 'date': str(d)[:10], 'record_id': r}
                    except (TypeError, ValueError):
                        continue
            if _has(db, 'fill', 'patient_id', 'fill_date', 'class_key', 'drug_name') and cls_pillar:
                rows = db.execute(f"SELECT patient_id, fill_date, class_key, drug_name FROM fill WHERE fill_date BETWEEN ? AND ? "
                                  f"AND class_key IN ({','.join('?' * len(cls_pillar))}) AND patient_id IN ({ph}) ORDER BY fill_date",
                                  [lo, as_of] + list(cls_pillar) + ch)
                for pid, d, cls, name in rows:
                    out[pid]['meds'][cls_pillar[cls]] = {'name': name, 'class': cls, 'mg': _mg(name), 'date': str(d)[:10]}
    finally:
        db.close()
    return out


# --- titration state ----------------------------------------------------------------------------------------------------

def _ladder(program, pillar, med=None, excluded=()):
    """(ladder, step index or None) for the pillar: the ladder matching the current drug, else the first agent not excluded."""
    ladders = (program.get('dose_ladders') or {}).get(pillar) or []
    if med:
        name = str(med.get('name') or '').lower()
        for lad in ladders:
            if lad['agent'].split()[0] in name:
                mg = med.get('mg')
                idx = 0
                for n, v in enumerate(lad.get('mg') or []):
                    if mg is not None and mg + 1e-9 >= v:
                        idx = n
                return lad, idx
        return None, None
    for lad in ladders:
        if not any(lad['agent'].split()[0] in x for x in excluded):
            return lad, None
    return None, None


def dose_state(program, row, snap):
    """{pillar: {status: missing|on_board|at_target|outside_ladder, agent, step, steps}} for the four pillars."""
    gap = row.get('gap') or {}
    missing = set(_value(gap.get('missing_pillars')) or [])
    cur, _ = barriers_of(gap)
    excluded = {str(b.get('drug_key') or b.get('drug_class') or '').lower() for b in cur}
    state = {}
    for pillar in (program.get('workflow') or {}).get('pillar_order') or ['sglt2i', 'mra', 'beta_blocker', 'rasi']:
        med = None if pillar in missing else (snap.get('meds') or {}).get(pillar)
        if pillar in missing or not med:
            lad, _ = _ladder(program, pillar, None, excluded)
            state[pillar] = {'status': 'missing', 'agent': lad['agent'] if lad else None, 'step': None,
                             'steps': lad['steps'] if lad else [], 'current': None}
            continue
        lad, idx = _ladder(program, pillar, med)
        if lad is None:
            state[pillar] = {'status': 'outside_ladder', 'agent': None, 'step': None, 'steps': [], 'current': med.get('name')}
            continue
        state[pillar] = {'status': 'at_target' if idx >= len(lad['steps']) - 1 else 'on_board', 'agent': lad['agent'], 'step': idx,
                         'steps': lad['steps'], 'current': med.get('name')}
    return state


def holds_for(program, measures):
    """Per-pillar holds from the latest values: [{pillar, measure, value, threshold, reason, date}]."""
    out = []
    for h in program.get('safety_holds') or []:
        m = measures.get(h['measure'])
        if not m or m.get('value') is None:
            continue
        v = float(m['value'])
        if ('lt' in h and v < h['lt']) or ('gt' in h and v > h['gt']):
            out.append({'pillar': h['pillar'], 'measure': h['measure'], 'value': v, 'threshold': {k: h[k] for k in ('lt', 'gt') if k in h},
                        'reason': h['reason'], 'date': m.get('date'), 'record_id': m.get('record_id')})
    return out


def _fresh(m, today, days):
    return bool(m) and m.get('date') and (today - date.fromisoformat(m['date'][:10])).days <= days


def _proposal(pillar, st):
    if st['status'] == 'missing' and st['agent']:
        return {'pillar': pillar, 'action': 'start', 'agent': st['agent'], 'from': None, 'to': st['steps'][0]}
    if st['status'] == 'on_board':
        return {'pillar': pillar, 'action': 'increase', 'agent': st['agent'], 'from': st['steps'][st['step']], 'to': st['steps'][st['step'] + 1]}
    return None


# --- the pathways -------------------------------------------------------------------------------------------------------

def _finish(run, program, row, state, extra):
    wf = program.get('workflow') or {}
    return {'action_id': run.action_id, 'patient_id': row['patient_id'], 'route': 'follow_on', 'program_id': program['id'],
            'program': program.get('name'), 'agent': wf.get('agent'), 'vendor_slot': (run.slot or {}).get('slot_id'),
            'vendor': (run.slot or {}).get('display'), 'counterparty_id': run.cp.id, 'seed': run.cp.seed, 'state': state,
            'milestones': dict(run.milestones), 'steps': run.steps, 'followups': run.followups, 'closed_loop': None,
            'barriers': [], 'stale_barriers': [], 'facts': {k: v for k, v in run.cp.facts.items() if v not in (None, False, [], '')},
            'through': run.through.isoformat() if run.through else None, 'simulated_counterparty': True,
            'start': run.as_of.isoformat(), 'pathway': extra}


def _follow_once(run, trigger, day, rec=None, **kw):
    if any(f['trigger'] == trigger for f in run.followups):
        return None
    return run.follow(trigger, day, rec, **kw)


def monitoring(row, program, *, workspace, start, cat, facts, snap, through=None, ledger=None):
    slot = (program.get('vendor_slots') or [None])[0]
    run = _Run(row, program, slot, workspace=workspace, as_of=start, cat=cat, facts=facts, through=through, ledger=ledger, scenario=None)
    m, wf, th = run.milestones, program.get('workflow') or {}, program.get('thresholds') or {}
    base = {k: v['value'] for k, v in (snap.get('measures') or {}).items()}
    extra = {'baseline': snap.get('measures') or {}, 'thresholds': th, 'escalations': [], 'readings': None, 'home_labs': None}

    def done(state):
        return _finish(run, program, row, state, extra)
    rec = run.send('consent', 1)
    if rec is None:
        return done('proposed')
    if rec['outcome'] == 'declined':
        m['declined'] += 1
        run.follow('rpm_declined', 1, rec)
        return done('declined')
    m['consented'] += 1
    rec = run.send('device_shipment', 3)
    if rec is None:
        return done('consented')
    if rec['outcome'] != 'delivered':
        run.follow('device_not_activated', 3, rec)
        return done('device_issue')
    m['device_delivered'] += 1
    rec = run.send('device_activation', 5)
    if rec is not None and rec['outcome'] == 'not_activated':
        rec = run.send('device_activation', 8, attempt=2)
        if rec is not None and rec['outcome'] == 'not_activated':
            run.follow('device_not_activated', 8, rec)
            return done('device_not_activated')
    if rec is None:
        return done('device_delivered')
    m['activated'] += 1
    lab_day = int(wf.get('lab_day') or 10)
    rec = run.send('home_lab_draw', lab_day, baseline=base)
    if rec is None:
        return done('monitoring_active')
    if rec['outcome'] == 'missed':
        run.follow('home_lab_missed', lab_day, rec)
    else:
        m['home_lab_collected'] += 1
        extra['home_labs'] = {**(rec.get('detail') or {}), 'date': rec['at'][:10], 'receipt_id': rec['receipt_id']}
        if rec['outcome'] == 'collected_out_of_range':
            esc = run.send('escalation', lab_day, reason=rec['reason'])
            run.follow('rpm_threshold_breach', lab_day, rec)
            m['escalated'] += 1
            extra['escalations'].append({'date': rec['at'][:10], 'reason': rec['reason'], 'receipt_id': rec['receipt_id'],
                                         'notified': (esc or {}).get('receipt_id')})
    days = int(wf.get('reading_days') or 30)
    rec = run.send('readings_window', days, baseline=base, thresholds=th, days=days)
    if rec is None:
        return done('escalated' if m['escalated'] else 'monitoring_active')
    extra['readings'] = rec.get('detail')
    if rec['outcome'] == 'threshold_breach':
        esc = run.send('escalation', days, attempt=2, reason=rec['reason'])
        run.follow('rpm_threshold_breach', days, rec)
        m['escalated'] += 1
        extra['escalations'].append({'date': rec['at'][:10], 'reason': rec['reason'], 'receipt_id': rec['receipt_id'],
                                     'notified': (esc or {}).get('receipt_id')})
    elif rec['outcome'] == 'low_adherence':
        run.follow('rpm_low_adherence', days, rec)
        m['low_adherence'] += 1
    else:
        m['in_range'] += 1
    rec = run.send('monitoring_review', int(wf.get('review_day') or days) + 1)
    if rec is not None:
        m['reviewed'] += 1
    return done('escalated' if m['escalated'] else ('low_adherence' if m['low_adherence'] else
                                                     ('month_reviewed' if rec is not None else 'monitoring_active')))


def titration(row, program, *, workspace, start, cat, facts, snap, through=None, ledger=None, escalations=()):
    slot = (program.get('vendor_slots') or [None])[0]
    run = _Run(row, program, slot, workspace=workspace, as_of=start, cat=cat, facts=facts, through=through, ledger=ledger, scenario=None)
    m, wf = run.milestones, program.get('workflow') or {}
    measures = {k: dict(v) for k, v in (snap.get('measures') or {}).items()}
    doses = dose_state(program, row, snap)
    extra = {'initial_doses': {k: dict(v) for k, v in doses.items()}, 'cycles': [], 'baseline': snap.get('measures') or {}}
    esc = sorted(escalations or [], key=lambda e: e['date'])

    def done(state):
        extra['doses'] = doses
        return _finish(run, program, row, state, extra)
    need_labs = set(wf.get('labs_required_for') or [])
    for c in range(1, int(wf.get('cycles') or 3) + 1):
        day = (c - 1) * int(wf.get('cycle_days') or 14)
        today = run.as_of + timedelta(days=day)
        hit = next((e for e in esc if e['date'] <= today.isoformat()), None)
        holds = holds_for(program, measures)
        if hit:
            holds = holds + [{'pillar': p, 'measure': 'remote_monitoring', 'value': None, 'threshold': None,
                              'reason': f"remote-monitoring escalation on {hit['date']}: {hit['reason']}", 'date': hit['date'],
                              'record_id': hit.get('receipt_id')} for p in doses]
        labs_ok = all(_fresh(measures.get(k), today, int(wf.get('labs_max_age_days') or 365)) for k in ('potassium', 'egfr'))
        held = {h['pillar'] for h in holds}
        props = [(_proposal(p, st)) for p, st in doses.items()]
        props = [x for x in props if x]
        missing_labs = [x['pillar'] for x in props if x['pillar'] in need_labs and not labs_ok]
        candidates = [x for x in props if x['pillar'] not in held and x['pillar'] not in missing_labs]
        cyc = {'cycle': c, 'date': today.isoformat(), 'holds': holds, 'labs_missing': missing_labs, 'proposal': None, 'outcome': None}
        extra['cycles'].append(cyc)
        if not props:
            m['target_reached'] += 1
            cyc['outcome'] = 'all pillars on board at the ladder target'
            return done('target_reached')
        rec = run.send('safety_check', day, attempt=c, holds=holds, labs_missing=missing_labs,
                       candidates=[x['pillar'] for x in candidates],
                       measures={k: measures[k] for k in MEASURES if k in measures})
        if rec is None:
            return done('titrating' if c > 1 else 'proposed')
        if rec['outcome'] == 'hold':
            m['held'] += 1
            cyc['outcome'] = rec['reason']
            if missing_labs and not holds:
                run.follow('titration_labs_missing', day, rec, pillars=missing_labs)
                return done('labs_needed')
            run.follow('titration_hold', day, rec, holds=holds)
            return done('held')
        prop = candidates[0]
        cyc['proposal'] = prop
        m['proposed'] += 1
        rec = run.send('dose_step_proposal', day, attempt=c, proposal=prop)
        if rec is None:
            return done('awaiting_pharmd')
        cyc['outcome'] = rec['outcome']
        if rec['outcome'] == 'deferred':
            m['deferred'] += 1
            _follow_once(run, 'pharmd_deferred', day, rec)
            continue
        if rec['outcome'] == 'modified':
            m['modified'] += 1
            continue  # approved to stay one more cycle at the current dose: no prescription change
        m['approved'] += 1
        rec = run.send('prescription', day + 1, attempt=c, dose=f"{prop['agent']} {prop['to']}")
        if rec is None:
            return done('approved')
        if rec['outcome'] == 'rejected':
            run.follow('rx_rejected', day + 1, rec)
            return done('rx_issue')
        m['rx_sent'] += 1
        st = doses[prop['pillar']]
        st.update(status='on_board', step=0 if st['step'] is None else st['step'] + 1, current=f"{prop['agent']} {prop['to']}")
        if st['step'] >= len(st['steps']) - 1:
            st['status'] = 'at_target'
        rec = run.send('patient_instructions', day + 1, attempt=c)
        if rec is not None and rec['outcome'] == 'unreached':
            _follow_once(run, 'instructions_unreached', day + 1, rec)
        if prop['pillar'] in ('rasi', 'mra', 'sglt2i'):
            lab_day = day + int(wf.get('lab_after_days') or 7)
            rec = run.send('followup_labs', lab_day, attempt=c, baseline={k: (measures.get(k) or {}).get('value') for k in ('potassium', 'egfr')})
            if rec is None:
                return done('titrating')
            if rec['outcome'] == 'lab_not_drawn':
                _follow_once(run, 'lab_not_drawn', lab_day, rec)
            else:
                det = rec.get('detail') or {}
                for k in ('potassium', 'egfr'):
                    if det.get(k) is not None:
                        measures[k] = {'value': det[k], 'date': rec['at'][:10], 'record_id': rec['receipt_id']}
                if rec['outcome'] == 'out_of_range':
                    m['lab_out_of_range'] += 1
                    run.follow('lab_out_of_range', lab_day, rec)
                    return done('paused_for_clinician')
    m['cycles_completed'] += 1
    return done('target_reached' if not any(_proposal(p, st) for p, st in doses.items()) else 'cycles_completed')


def start_date(parent_action, spec):
    """The day a follow-on starts: the parent's enrollment receipt, or its attended first visit."""
    want = 'first_visit' if spec.get('start') == 'first_visit' else 'enrollment'
    for s in parent_action.get('steps') or []:
        r = s.get('receipt')
        if s['step'] == want and r and (want != 'first_visit' or r.get('outcome') == 'attended'):
            return r['at'][:10]
    return None


def triggered(parent_action, spec):
    return bool((parent_action.get('milestones') or {}).get(spec.get('after_milestone') or 'enrolled')) and start_date(parent_action, spec) is not None


def run_follow_ons(parent_action, row, parent_program, cat, *, workspace, snap, facts, through=None, ledgers=None):
    """-> [(follow-on program, action or None when not triggered)] for one parent action; monitoring runs first so its
    escalations hold titration steps."""
    ledgers = ledgers or {}
    snap = snap or {'measures': {}, 'meds': {}}
    k = (snap.get('measures') or {}).get('potassium') or {}
    f = {**(facts or {}), 'borderline_potassium': k.get('value') is not None and 4.8 <= float(k['value']) <= 5.0}
    out, escalations = [], []
    specs = follow_on_programs(cat, parent_program)
    specs.sort(key=lambda ps: 0 if ps[0]['kind'] == 'hf_remote_monitoring' else 1)
    for prog, spec in specs:
        if not triggered(parent_action, spec):
            out.append((prog, None))
            continue
        start = start_date(parent_action, spec)
        if prog['kind'] == 'hf_remote_monitoring':
            a = monitoring(row, prog, workspace=workspace, start=start, cat=cat, facts=f, snap=snap, through=through,
                           ledger=ledgers.get(prog['id']))
            escalations = (a.get('pathway') or {}).get('escalations') or []
        else:
            a = titration(row, prog, workspace=workspace, start=start, cat=cat, facts=f, snap=snap, through=through,
                          ledger=ledgers.get(prog['id']), escalations=escalations)
        a['parent_action'] = parent_action['action_id']
        out.append((prog, a))
    return out


def summarize(actions):
    """Counts for a set of follow-on actions: by program and final state, milestones, open follow-ups."""
    from collections import Counter, defaultdict
    by = defaultdict(lambda: {'actions': 0, 'states': Counter(), 'milestones': Counter()})
    for a in actions:
        b = by[a['program_id']]
        b['actions'] += 1
        b['states'][a['state']] += 1
        for k, v in (a.get('milestones') or {}).items():
            if v:
                b['milestones'][k] += 1
    return {'by_program': {k: {'actions': v['actions'], 'states': dict(v['states']), 'milestones': dict(v['milestones'])}
                           for k, v in sorted(by.items())},
            'actions': len(actions), 'followups_open': sum(1 for a in actions for f in a['followups'] if f['state'] == 'open'),
            'receipts': sum(1 for a in actions for s in a['steps'] if s.get('receipt'))}
