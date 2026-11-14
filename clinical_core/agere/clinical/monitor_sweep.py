"""Saved-answer monitors recomputed on new warehouse snapshots (no manual click per monitor).

A monitor is a 'monitor' step of a saved answer's workflow (deliverables/workflow.py): it keeps its scope (the original
query scope, or the frozen result membership), its computed outputs and a baseline observation stamped with the warehouse
source identity. deliverables.workflow.refresh re-executes the saved contract only when the source identity changed, and
records the new observation and whether the computed answer changed.

This module makes that recomputation continuous:
  status(path)         every saved monitor with its scope and whether the current snapshot is newer than its last observation
  sweep(stack)         refresh every due monitor (new snapshot); a monitor that cannot recompute records its reason and the
                       sweep continues (never blocks the others)
  schedule(stack_dir)  release activation marks a sweep due (warehouse_activation.activate calls it); sweep_if_due(stack)
                       runs it once the stack is opened, and the command line (python -m ...monitor_sweep --stack DIR) runs it
                       from a scheduler
and puts it on the planner menu as the 'saved_monitors' engine ("did any of my monitors change with the new data?").
Recomputation is on new snapshots only; same snapshot = no new observation. No causal benefit is inferred from a change.
"""
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

VERSION = 'monitor-sweep/1'
OP = 'saved_monitors'
NEEDS_STACK = True
ACTIONS = ('recompute', 'status')
DUE_FILE = 'monitor-sweep-due.json'
LOG_FILE = 'monitor-sweep-log.jsonl'

PRODUCT = {'population': 'none', 'view': 'table',
           'use': 'SAVED MONITORS: recompute every saved answer monitor on the new warehouse snapshot (original query scope or frozen '
                  'patients) and report which monitored answers changed, stayed the same or could not be recomputed.'}
CONTRACT = {'inputs': ['none'], 'parameters': {'action': 'recompute|status', 'only_changed': False},
            'meaning': 'Saved answer monitors (workflow monitor steps with explicit scope, computed outputs and a stamped baseline): recompute '
                       'each due monitor against the current snapshot via the saved request contract (no new interpretation), record the '
                       'observation and whether the computed answer changed; status lists monitors and whether a newer snapshot is pending. '
                       'Same snapshot = no new observation; no causal outcome is inferred.'}
NATIVE = ('count',)


def schema(obj, string, array):
    return obj({'action': string(ACTIONS, 'recompute: refresh every monitor whose last observation predates this snapshot; status: read-only list'),
                'only_changed': {'type': 'boolean', 'description': 'List only monitors whose computed answer changed'}})


def validate(p):
    if not isinstance(p, dict) or set(p) - {'action', 'only_changed'}: raise ValueError('saved_monitors parameters: action, only_changed')
    out = {'action': p.get('action', 'recompute'), 'only_changed': p.get('only_changed', False)}
    if out['action'] not in ACTIONS: raise ValueError(f'action: {ACTIONS}')
    if type(out['only_changed']) is not bool: raise ValueError('only_changed: true or false')
    return out


def _now(): return datetime.now(timezone.utc).isoformat()


def monitors(path):
    """[(context_id, step index, step, question)] for every saved monitor in this stack's answer workspaces."""
    from ..deliverables import answer_context as A
    out = []
    root = A.root(path)
    if not root.is_dir(): return out
    for f in sorted(root.glob('ANS-*/workflow.json')):
        try:
            record = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if record.get('program_links'): continue  # links are refreshed in their producing workspace
        question = None
        try: question = A.read_snapshot(f.parent / 'answer.json').get('question')
        except (OSError, ValueError): pass
        for i, s in enumerate(record.get('steps') or []):
            if s.get('kind') == 'monitor': out.append((f.parent.name, i, s, question))
    return out


def _same(a, b):
    keys = ('source_digest', 'as_of', 'semantic_version', 'size', 'mtime_ns')
    return isinstance(a, dict) and isinstance(b, dict) and all(a.get(k) == b.get(k) for k in keys)


def _row(key, i, s, question, current):
    obs = s.get('observations') or []
    last = obs[-1] if obs else None
    due = bool(last) and s.get('status') == 'watching' and not _same(last.get('source'), current)
    return {'context_id': key, 'index': i, 'question': question, 'mode': s.get('mode'), 'scope': s.get('scope_label') or s.get('scope'),
            'status': s.get('status'), 'observations': len(obs), 'last_observed': last.get('at') if last else None,
            'last_as_of': (last.get('source') or {}).get('as_of') if last else None, 'due': due,
            'changed_last': bool(last and last.get('changed')), 'reason': s.get('reason')}


def status(path):
    from ..deliverables import answer_context as A
    current = A.source_identity(path)
    return [_row(k, i, s, q, current) for k, i, s, q in monitors(path)], current


def sweep(stack, *, only_due=True):
    """Refresh due monitors, preserving failed work. A provider limit is recorded and raised immediately after persistence."""
    from ..deliverables import workflow, answer_context as A
    from ..gateway import ProviderUnavailable
    rows, current = status(stack.path)
    marker=Path(stack.path)/DUE_FILE
    try:pending=json.loads(marker.read_text()) if marker.exists() else {}
    except (OSError,ValueError):pending={}
    blocked={(x['context_id'],x['index']):x for x in pending.get('blocked',[]) if pending.get('source')==current}
    stop_error=None
    for r in rows:
        if stop_error is not None:
            r.update(outcome='not_recomputed',reason='Not checked after provider limit; explicit continuation required',retryable=False,
                     new_paid_api_calls=0,known_api_estimate_usd=0,api_cost_known=True)
            continue
        if r['mode'] == 'work' and r['status'] == 'watching':
            r['due'] = True  # work monitors follow saved simulated work, not the snapshot
        if not r['due'] and only_due:
            r['outcome'] = 'not_due' if r['status'] == 'watching' else 'not_watching'
            continue
        prior=blocked.get((r['context_id'],r['index']))
        if prior and only_due:
            r.update(outcome='not_recomputed',reason=prior['reason'],retryable=False,
                     new_paid_api_calls=0,known_api_estimate_usd=0,api_cost_known=True)
            continue
        try:
            record = workflow.refresh(stack, {'context_id': r['context_id'], 'index': r['index']})
            step = record['steps'][r['index']]
            last = (step.get('observations') or [{}])[-1]
            r.update(outcome='changed' if last.get('changed') else 'unchanged', reason=step.get('reason'),
                     observations=len(step.get('observations') or []), last_as_of=(last.get('source') or {}).get('as_of'),
                     new_paid_api_calls=record.get('new_paid_api_calls',0),
                     known_api_estimate_usd=record.get('known_api_estimate_usd',0),api_cost_known=record.get('api_cost_known',True))
        except (ValueError, OSError, KeyError,sqlite3.Error,ProviderUnavailable) as exc:
            # Retry only a transient local read. Provider/definition failures stay
            # visible and require action or a changed release, not a blind send.
            retryable=isinstance(exc,OSError) and not isinstance(exc,(TimeoutError,ConnectionError)) and not getattr(exc,'kind',None)
            r.update(outcome='not_recomputed', reason=str(exc),retryable=retryable,
                     new_paid_api_calls=0,new_paid_api_calls_known=False,known_api_estimate_usd=0,api_cost_known=False)
            if getattr(exc,'kind',None)=='rate_limit' or re.search(r'subscription.*limit|weekly.*limit',str(exc),re.I):
                stop_error=exc
    entry = {'at': _now(), 'source': current, 'checked': sum(1 for r in rows if r.get('outcome') not in ('not_due', 'not_watching')),
             'changed': sum(1 for r in rows if r.get('outcome') == 'changed')}
    try:
        with open(Path(stack.path) / LOG_FILE, 'a') as f: f.write(json.dumps(entry) + '\n')
        failed=[r for r in rows if r.get('outcome')=='not_recomputed']
        if failed:
            marker.write_text(json.dumps({'due':True,'at':_now(),'source':current,
                'state':'pending' if any(r.get('retryable') for r in failed) else 'requires_attention',
                'blocked':[{'context_id':r['context_id'],'index':r['index'],'reason':r['reason']} for r in failed if not r.get('retryable')],
                'pending':[{'context_id':r['context_id'],'index':r['index'],'reason':r['reason']} for r in failed if r.get('retryable')]}))
        else:marker.unlink(missing_ok=True)
    except OSError:
        pass
    if stop_error is not None:raise stop_error
    return rows, current


def schedule(stack_dir, reason='release activated'):
    """Mark a sweep due for this stack (called by release activation; cheap, no model or stack load)."""
    try:
        (Path(stack_dir) / DUE_FILE).write_text(json.dumps({'due': True, 'at': _now(), 'reason': reason}) + '\n')
        return True
    except OSError:
        return False


def sweep_if_due(stack):
    marker=Path(stack.path)/DUE_FILE
    if not marker.exists():return None
    try:pending=json.loads(marker.read_text())
    except (OSError,ValueError):return None
    if pending.get('state')=='requires_attention':
        from ..deliverables import answer_context as A
        if pending.get('source')==A.source_identity(stack.path):return None
    return sweep(stack)


def execute(stack, patient_ids, params, as_of):
    p = validate(params)
    if p['action'] == 'status':
        rows, current = status(stack.path)
        for r in rows: r['outcome'] = 'pending_new_snapshot' if r['due'] else 'current'
    else:
        rows, current = sweep(stack)
    table = [r for r in rows if not p['only_changed'] or r.get('outcome') == 'changed' or r.get('changed_last')]
    known=sum(r.get('known_api_estimate_usd') or 0 for r in rows)
    complete=all(r.get('api_cost_known',True) for r in rows)
    return {'schema': VERSION, 'as_of': str(as_of)[:10], 'parameters': p, 'source': current, 'monitors': rows, 'table': table,
            'new_paid_api_calls':sum(r.get('new_paid_api_calls') or 0 for r in rows),
            'planning_economics':{'known_api_estimate_usd':known,'new_api_estimate_usd':known if complete else None,'api_cost_known':complete},
            'counts': {k: sum(1 for r in rows if r.get('outcome') == k) for k in
                       ('changed', 'unchanged', 'not_recomputed', 'not_due', 'not_watching', 'pending_new_snapshot', 'current')},
            'scheduled': (Path(stack.path) / DUE_FILE).exists()}


def headline(r):
    c, n = r['counts'], len(r['monitors'])
    if not n: return 'No saved monitors in this warehouse yet (add a monitor step to a saved answer to follow it across snapshots)'
    if r['parameters']['action'] == 'status':
        return (f"{n} saved monitors: {c['pending_new_snapshot']} have a newer snapshot to recompute, {c['current']} are current "
                f"(snapshot as of {r['source'].get('as_of')})")
    return (f"{n} saved monitors checked against the snapshot as of {r['source'].get('as_of')}: {c['changed']} changed, {c['unchanged']} "
            f"unchanged after recompute, {c['not_due']} already current (same snapshot, no new observation), {c['not_recomputed']} could "
            f"not be recomputed, {c['not_watching']} not yet defined")


def answer(report, plan, question):
    return {'status': 'answered', 'answer_type': 'table', 'table': report['table'], 'count': len(report['table']), 'patient_ids': [],
            'value': headline(report) + '.',
            'method': 'Each monitor re-executes its saved request contract with its saved scope (original query scope or frozen patients) '
                      'when the warehouse source identity differs from its last observation; the new computed outputs are compared with the '
                      'previous observation (deliverables.workflow.refresh).',
            'limitations': ['Recomputation happens on new snapshots only; the same snapshot never produces a new observation.',
                            'A changed monitored answer is an observed difference, not a causal outcome.',
                            'Synthetic warehouse data.'],
            'membership_role': 'evaluated'}


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not re.search(r'\b(?:my|our|saved|the) (?:saved )?monitors?\b|\bmonitored (?:answers?|questions?|metrics?)\b|\bwatch ?list\b|\bthings? (?:i|we)(?:\'m| am|\'re| are)? (?:watching|tracking|monitoring)\b', low):
        return None
    if re.search(r'\bout of control\b|\bcontrol charts?\b|\bfeeds?\b|\bspc\b|\bcusum\b', low): return None  # SPC / feed health stays monitor
    params = {'action': 'status'} if re.search(r'\bpending\b|\bwhich monitors\b|\blist\b|\bshow\b(?!.*\bchang)', low) else {}
    if re.search(r'\bchang\w*\b|\bmoved\b|\bdifferent\b', low): params['only_changed'] = True
    return plan(OP, params, 'Recompute saved answer monitors on the current snapshot', question, [f"action {params.get('action', 'recompute')}"])


TEMPLATES = ['Did any of my saved monitors change with the new data?', 'recheck our monitors against the latest snapshot',
             'which of the things we are tracking moved since last refresh', 'show my saved monitors and whether they are current',
             'rerun every monitored answer on the new warehouse', 'list our monitors pending a new snapshot']


def main(argv=None):
    import argparse
    from .stack_runtime import Stack
    a = argparse.ArgumentParser(description='Recompute saved answer monitors on the current snapshot (scheduler entry point)')
    a.add_argument('--stack', required=True); a.add_argument('--if-due', action='store_true')
    args = a.parse_args(argv)
    stack = Stack(Path(args.stack))
    try:
        out = sweep_if_due(stack) if args.if_due else sweep(stack)
        rows = out[0] if out else []
        print(json.dumps({'checked': len(rows), 'changed': sum(1 for r in rows if r.get('outcome') == 'changed')}))
    finally:
        stack.close()


if __name__ == '__main__':
    main()
