"""Completion evaluator: did the care actually happen after Agere acted?

    closures = evaluate(semantic_db, actions, catalog=cat)     # {action_id: closure}
    report = rates(actions, closures)

For each enrolled action (one with a closed_loop block) the program's completion criterion (programs.json `completion`) is
checked against the workspace's own data first:
- fill_for_target_pillars (PharmD GDMT, barrier review): a picked-up fill (fill table: paid, not reversed) of each target
  pillar's classes on or after the enrollment date and within the window. A paid pharmacy claim for a target pillar that
  was later reversed (payer_pharmacy_claim.reversed_by_record_id) with no other fill reopens the item.
- pt_claim_after_enrollment (virtual PT): a PT claim line (CPT 97161-97164, 97110, 97112, 97140, 97530) on or after the
  enrollment date within the window. Lines that no longer count (voided / denied, counts_in_metrics = 0) with no
  counting line reopen the item.
The workspace data is authoritative for the part of the window it covers (meta as_of = data_through). When the window is
not covered (the plan's as_of is the data's as_of, so every visit is in the future), the simulated counterparty's
attendance / completion receipt decides, and the closure says so (source = simulated_attendance). A data closure for a
patient the program reported as dropped is still a closure (care happened, possibly elsewhere), flagged.
Status: closed | still_open | reopened. Every closure names the records that set it.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import sqlite3
from pathlib import Path

from . import catalog as catmod


def _d(s):
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def _open(semantic_db):
    if isinstance(semantic_db, sqlite3.Connection):
        return semantic_db, False
    return sqlite3.connect(f'file:{Path(semantic_db)}?mode=ro', uri=True), True


def _cols(db, table):
    return {r[1] for r in db.execute(f'PRAGMA table_info({table})')}


def data_through(db):
    try:
        r = db.execute("SELECT value FROM meta WHERE key = 'as_of'").fetchone()
    except sqlite3.Error:
        return None
    return _d(r[0]) if r else None


def _ingredients(pillar_cfg):
    out = []
    for i in pillar_cfg.get('ingredients') or []:
        out.append(i if isinstance(i, str) else i.get('key'))
    return [x.lower() for x in out if x]


def _gdmt(db, pid, targets, start, end, gap_cfg):
    """-> {pillar: {'state': filled|reversed|none, 'sources': [...]}}"""
    fcols, pcols = _cols(db, 'fill'), _cols(db, 'payer_pharmacy_claim')
    out = {}
    for name in targets:
        p = (gap_cfg.get('pillars') or {}).get(name) or {}
        classes = p.get('fill_classes') or []
        state, src = 'none', []
        if fcols and classes:
            q = (f"SELECT fill_id, fill_date, drug_name, record_ids FROM fill WHERE patient_id = ? AND fill_date >= ? AND fill_date <= ? "
                 f"AND (class_key IN ({','.join('?' * len(classes))}) OR drug_class IN ({','.join('?' * len(classes))})) ORDER BY fill_date")
            hits = db.execute(q, [pid, start.isoformat(), end.isoformat()] + classes * 2).fetchall()
            if hits:
                state = 'filled'
                src = [f"fill:{h[3] or h[0]}" for h in hits[:3]]
                out[name] = {'state': state, 'sources': src, 'first_fill': hits[0][1], 'drug': hits[0][2]}
                continue
        if {'reversed_by_record_id', 'drug_name', 'fill_date'} <= pcols:
            ings = _ingredients(p)
            rows = db.execute("SELECT record_id, fill_date, drug_name, reversed_by_record_id FROM payer_pharmacy_claim WHERE patient_id = ? "
                              "AND fill_date >= ? AND fill_date <= ? AND reversed_by_record_id IS NOT NULL AND reversed_by_record_id != ''",
                              (pid, start.isoformat(), end.isoformat())).fetchall()
            rev = [r for r in rows if any(i in str(r[2] or '').lower() for i in ings)]
            if rev:
                out[name] = {'state': 'reversed', 'sources': [f"payer_pharmacy_claim:{r[0]}" for r in rev[:3]] +
                             [f"payer_pharmacy_claim:{r[3]}" for r in rev[:3]], 'reversed_fill': rev[0][1], 'drug': rev[0][2]}
                continue
        out[name] = {'state': state, 'sources': src}
    return out


def _pt(db, pid, start, end, codes):
    cols = _cols(db, 'payer_claim_line')
    if not cols or not codes:
        return [], []
    live = 'COALESCE(counts_in_metrics, 1)' if 'counts_in_metrics' in cols else '1'
    hc = f" OR hcpcs IN ({','.join('?' * len(codes))})" if 'hcpcs' in cols else ''
    rows = db.execute(f"SELECT record_id, payer_claim_id, service_date, cpt, {live} FROM payer_claim_line WHERE patient_id = ? "
                      f"AND service_date >= ? AND service_date <= ? AND (cpt IN ({','.join('?' * len(codes))}){hc}) ORDER BY service_date",
                      [pid, start.isoformat(), end.isoformat()] + codes + (codes if hc else [])).fetchall()
    counting = [r for r in rows if r[4] != 0]
    voided = [r for r in rows if r[4] == 0]
    return counting, voided


def _observable(db, pid, criterion, since, until):
    """Does the workspace receive the stream that would show completion for this patient? (any pharmacy record / any
    professional claim line between a year before enrollment and the data's as_of). Absent stream = cannot observe, which
    is not the same as 'no fill'."""
    checks = ([('fill', 'fill_date'), ('payer_pharmacy_claim', 'fill_date')] if criterion == 'fill_for_target_pillars' else
              [('payer_claim_line', 'service_date')])
    for table, col in checks:
        if col in _cols(db, table) and db.execute(f'SELECT 1 FROM {table} WHERE patient_id = ? AND {col} BETWEEN ? AND ? LIMIT 1',
                                                  (pid, since.isoformat(), until.isoformat())).fetchone():
            return True
    return False


def evaluate(semantic_db, actions, *, catalog=None):
    """{action_id: closure} for actions with a closed_loop block (enrolled)."""
    cat = catalog or catmod.load()
    db, own = _open(semantic_db)
    try:
        through = data_through(db)
        gdmt_cfg = (cat.get('gaps') or {}).get('gdmt_pillar_missing') or {}
        pt_codes = ((cat.get('gaps') or {}).get('pt_after_ed_low_back_pain') or {}).get('pt_cpt') or []
        out = {}
        for a in actions:
            loop = a.get('closed_loop')
            if not loop:
                continue
            start, due = _d(loop['enrolled_on']), _d(loop['due'])
            covered_to = min(due, through) if through else None
            observed = bool(covered_to and covered_to >= start)
            stream = None
            if observed and not _observable(db, a['patient_id'], loop['criterion'], start - timedelta(days=365), through):
                observed, stream = False, ('no pharmacy claims or fills delivered for this patient' if loop['criterion'] == 'fill_for_target_pillars'
                                           else 'no professional claim lines delivered for this patient')
            fully = bool(observed and through >= due)
            sim_done = a['state'] == 'completed'
            res = {'criterion': loop['criterion'], 'enrolled_on': loop['enrolled_on'], 'due': loop['due'],
                   'data_through': through.isoformat() if through else None, 'window_observed': 'full' if fully else ('partial' if observed else 'none'),
                   'program_state': a['state'], **({'stream_not_delivered': stream} if stream else {})}
            status, source, evidence, detail = None, None, [], None
            if observed and loop['criterion'] == 'fill_for_target_pillars':
                targets = loop.get('target_pillars') or []
                per = _gdmt(db, a['patient_id'], targets, start, covered_to, gdmt_cfg)
                res['pillars'] = per
                filled = [n for n, v in per.items() if v['state'] == 'filled']
                reversed_ = [n for n, v in per.items() if v['state'] == 'reversed']
                evidence = [s for v in per.values() for s in v['sources']]
                if targets and len(filled) == len(targets):
                    status, source, detail = 'closed', 'workspace_data', f"fill for every target pillar ({', '.join(filled)})"
                elif reversed_:
                    status, source, detail = 'reopened', 'workspace_data', f"fill reversed for {', '.join(reversed_)}" + \
                        (f"; filled {', '.join(filled)}" if filled else '')
                elif fully:
                    status, source = 'still_open', 'workspace_data'
                    detail = f"{len(filled)}/{len(targets)} target pillars filled by {covered_to.isoformat()}" + \
                        (' (program reported completion; no fill observed)' if sim_done else '')
                elif filled:
                    detail = f"{len(filled)}/{len(targets)} target pillars filled by {covered_to.isoformat()} (window continues past the data)"
            elif observed and loop['criterion'] == 'pt_claim_after_enrollment':
                counting, voided = _pt(db, a['patient_id'], start, covered_to, pt_codes)
                evidence = [f"payer_claim_line:{r[0] or r[1]}" for r in (counting or voided)[:4]]
                if counting:
                    status, source = 'closed', 'workspace_data'
                    detail = f"{len(counting)} PT claim line(s) from {counting[0][2]} (CPT {', '.join(sorted({r[3] for r in counting if r[3]}))})"
                elif voided:
                    status, source, detail = 'reopened', 'workspace_data', 'PT claim lines voided or denied (counts_in_metrics = 0)'
                elif fully:
                    status, source = 'still_open', 'workspace_data'
                    detail = 'no PT claim in the window' + (' (program reported completion; no claim observed)' if sim_done else '')
            if status is None:  # the window is (partly) beyond the data: the simulated attendance record decides
                source = 'simulated_attendance'
                comp = next((s for s in a['steps'] if s['step'] == 'completion_report' and s.get('receipt')), None)
                if sim_done:
                    status, evidence = 'closed', [f"receipt:{comp['receipt']['receipt_id']}"] if comp else []
                    detail = (detail + '; ' if detail else '') + 'counterparty reported completion (simulated)'
                else:
                    status = 'still_open'
                    detail = (detail + '; ' if detail else '') + f"program state {a['state']}"
                if stream:
                    detail += f'; workspace data cannot observe completion ({stream})'
            if source == 'workspace_data' and status == 'closed' and not sim_done:
                res['flag'] = f"closed by workspace data although the program reported {a['state']} (care may have happened elsewhere)"
            res.update(status=status, source=source, evidence=evidence, detail=detail)
            out[a['action_id']] = res
        return out
    finally:
        if own:
            db.close()


def rates(actions, closures):
    """Closure rates overall and per program / vendor slot."""
    by_prog, by_slot = defaultdict(Counter), defaultdict(Counter)
    total = Counter()
    for a in actions:
        c = closures.get(a['action_id'])
        if not c:
            continue
        for bucket in (total, by_prog[a['program_id']], by_slot[a.get('vendor_slot')]):
            bucket['evaluated'] += 1
            bucket[c['status']] += 1
            bucket['source:' + c['source']] += 1
            if c['status'] != 'closed' and any(v.get('state') == 'filled' for v in (c.get('pillars') or {}).values()):
                bucket['open_with_some_target_filled'] += 1

    def fmt(c):
        n = c.get('evaluated', 0)
        return {**dict(sorted(c.items())), 'closure_rate': round(c.get('closed', 0) / n, 3) if n else None}
    return {**fmt(total), 'by_program': {k: fmt(v) for k, v in sorted(by_prog.items())},
            'by_vendor_slot': {k: fmt(v) for k, v in sorted(by_slot.items(), key=lambda kv: str(kv[0]))}}
