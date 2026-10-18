"""The stack as tools: every fact or computed number a deliverable shows comes from one of these, with a receipt.

Each tool runs a cheap layer (deterministic code, local engines, the workspace answer path) and returns compact data; the
composer adds it to the deliverable's Bundle under a receipt id (receipts.py). Models never write SQL here and never
compute: they choose tools and arguments.

    ctx = ToolContext(ws)
    rid = ctx.call(bundle, 'plan', {'program_kind': 'pharmacy_gdmt'})

Results are cached per workspace (<workspace>/deliverables/tool-cache/<key>.json) keyed by tool, arguments, tool version
and the data fingerprint (semantic layer size, mtime and source digest), so revisions and repeated requests reuse them.
Tools that write (plan: the action backbone; record decisions) are idempotent replays on the same inputs.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import sqlite3
import time

import numpy as np

from . import finance
from .receipts import clean, fingerprint

TOOL_VERSION = 'agere-deliverable-tools/2'
MAX_IDS = 5000


class ToolError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------------------- the context
class ToolContext:
    def __init__(self, ws, *, cache=True, send_factory=None, allow_model_ask=False, on_usage=None):
        self.ws = ws
        self.cache = cache
        self.send_factory = send_factory
        self.allow_model_ask = allow_model_ask
        self.on_usage = on_usage
        self.dir = Path(ws.dir) / 'deliverables'
        self.cache_dir = self.dir / 'tool-cache'
        self.calls = []
        self._mem = {}

    @property
    def semantic(self):
        return Path(self.ws.semantic_path)

    @property
    def as_of(self):
        a = self.ws.state.get('as_of')
        if a:
            return a
        db = self.ro()
        try:
            return dict(db.execute('SELECT key, value FROM meta').fetchall()).get('as_of')
        finally:
            db.close()

    def ro(self):
        if not self.semantic.is_file():
            raise ToolError(f'Workspace {self.ws.slug} has no semantic layer')
        return sqlite3.connect(f'file:{self.semantic}?mode=ro', uri=True)

    def data_fp(self):
        st = self.semantic.stat()
        db = self.ro()
        try:
            meta = dict(db.execute('SELECT key, value FROM meta').fetchall())
        finally:
            db.close()
        return f"semantic:{st.st_size}:{int(st.st_mtime)}:{(meta.get('source_digest') or '')[:16]}"

    def engine_dir(self):
        """The engines (market, vbc, contract_strategy) read a stack directory (run.json + semantic.sqlite3); a workspace
        layer is exposed to them through a shim directory whose run.json carries the layer's own source digest, so
        semantic_layer.ensure() finds it current and never rebuilds."""
        d = self.dir / 'engine-stack'
        d.mkdir(parents=True, exist_ok=True)
        link = d / 'semantic.sqlite3'
        if not link.exists():
            try:
                link.symlink_to(self.semantic.resolve())
            except OSError as exc:  # pragma: no cover - filesystems without symlinks
                raise ToolError(f'cannot expose the semantic layer to the engines: {exc}')
        db = self.ro()
        try:
            meta = dict(db.execute('SELECT key, value FROM meta').fetchall())
        finally:
            db.close()
        from ..clinical import semantic_layer
        digest = meta.get('source_digest')
        if meta.get('version') != semantic_layer.VERSION or not digest:
            # semantic_layer.ensure() would try to rebuild a layer it does not recognise; never let a tool write the layer
            raise ToolError(f"the engines need a {semantic_layer.VERSION} layer with a source digest (this workspace has "
                            f"{meta.get('version') or 'no version'}); use plan / work / simulate / barriers")
        (d / 'run.json').write_text(json.dumps({'source_digest': digest, 'note': 'deliverables engine shim'}))
        return d

    # --- calling ----------------------------------------------------------------------------------------------------
    def call(self, bundle, tool, args=None):
        """Run (or reuse) a tool and add its result to the bundle. -> receipt id."""
        if tool not in TOOLS:
            raise ToolError(f'Unknown tool {tool!r}; tools: {sorted(TOOLS)}')
        spec = TOOLS[tool]
        args = _norm_args(spec, args or {})
        fp = self.data_fp()
        key = fingerprint([TOOL_VERSION, tool, args, fp])[:24]
        f = self.cache_dir / f'{key}.json'
        t0 = time.time()
        cached = False
        if key in self._mem:
            out, cached = self._mem[key], True
        elif self.cache and f.is_file():
            out, cached = json.loads(f.read_text()), True
        else:
            res = spec['fn'](self, **args)
            out = clean({'data': res.get('data'), 'definition': res.get('definition'), 'sql': res.get('sql'),
                         'source': res.get('source'), 'model_cost_usd': res.get('model_cost_usd')})
            if self.cache and not res.get('no_cache'):
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                tmp = f.with_suffix('.tmp')
                tmp.write_text(json.dumps(out, default=str))
                tmp.replace(f)
        self._mem[key] = out
        rid = bundle.add(tool, args, out['data'], definition=out.get('definition'), sql=out.get('sql'), as_of=self.as_of, data_fp=fp,
                         source=out.get('source'), cached=cached, seconds=round(time.time() - t0, 3),
                         model_cost_usd=None if cached else out.get('model_cost_usd'))
        self.calls.append({'tool': tool, 'args': args, 'receipt': rid, 'cached': cached, 'seconds': round(time.time() - t0, 3),
                           'model_cost_usd': None if cached else out.get('model_cost_usd')})
        return rid


def _norm_args(spec, args):
    props = spec['schema'].get('properties') or {}
    unknown = set(args) - set(props)
    if unknown:
        raise ToolError(f"{spec['name']}: unknown arguments {sorted(unknown)}")
    for k in spec['schema'].get('required') or []:
        if args.get(k) in (None, ''):
            raise ToolError(f"{spec['name']}: {k} is required")
    out = {}
    for k, v in args.items():
        if v is None:
            continue
        p = props[k]
        if p.get('enum') and v not in p['enum']:
            raise ToolError(f"{spec['name']}: {k} must be one of {p['enum']}")
        if p.get('type') == 'integer':
            v = int(v)
        elif p.get('type') == 'number':
            v = float(v)
        out[k] = v
    return dict(sorted(out.items()))


# ---------------------------------------------------------------------------------------------------------- tools
def t_catalog(ctx):
    db = ctx.ro()
    try:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY 1")]
        cols = {t: [r[1] for r in db.execute(f'PRAGMA table_info("{t}")')] for t in tables if not t.startswith('raw_')}
        meta = dict(db.execute('SELECT key, value FROM meta').fetchall())
    finally:
        db.close()
    from ..plans import catalog as catmod
    from ..clinical import cohort_families, market, vbc
    cat = catmod.load()
    docs = sorted(p.name for p in Path(ctx.ws.docs_dir).glob('*'))[:200] if Path(ctx.ws.docs_dir).is_dir() else []
    return {'data': {'as_of': meta.get('as_of'), 'semantic_version': meta.get('version'), 'tables': cols,
                     'raw_tables': [t for t in tables if t.startswith('raw_')][:80], 'analyst_docs': docs,
                     'programs': [{'kind': p['kind'], 'id': p['id'], 'label': p.get('label') or p.get('name')} for p in cat['programs']],
                     'program_kinds': sorted({p['kind'] for p in cat['programs']}), 'cohort_families': list(cohort_families.FAMILIES),
                     'market_units': list(market.UNITS), 'market_metrics': list(market.METRICS), 'vbc_metrics': list(vbc.METRICS)},
            'definition': f"semantic layer schema {meta.get('version')} (tables, columns), program catalog {cat.get('version')}, analyst docs",
            'source': 'sqlite_master + plans/programs.json'}


def t_doc(ctx, name):
    p = Path(ctx.ws.docs_dir) / Path(name).name
    if not p.is_file():
        raise ToolError(f'No analyst doc {name!r}')
    return {'data': {'name': p.name, 'text': p.read_text(errors='replace')[:12000]}, 'definition': f'analyst doc {p.name}', 'source': 'analyst_docs'}


def t_cohort(ctx, question):
    from ..clinical import cohort_families
    planned = cohort_families.plan(ctx.semantic, question, ctx.as_of)
    if planned is None:
        if ctx.allow_model_ask:
            return t_ask(ctx, question, answer_type='patient_set')
        raise ToolError('No deterministic cohort family fits this question; use ask (the workspace answer path) or rephrase '
                        f'with an explicit definition. Families: {list(cohort_families.FAMILIES)}')
    sql = planned['graph']['nodes'][0]['parameters']['query']
    db = ctx.ro()
    try:
        cur = db.execute(sql)
        names = [d[0] for d in cur.description]
        rows = cur.fetchall()
    finally:
        db.close()
    ids = [r[names.index('patient_id')] for r in rows] if 'patient_id' in names else []
    extra = {}
    if ids:
        db = ctx.ro()
        try:
            prac = Counter()
            for i in range(0, len(ids), 500):
                ch = ids[i:i + 500]
                for (p,) in db.execute(f"SELECT coalesce(pcp_practice_id,'unattributed') FROM patient WHERE patient_id IN ({','.join('?' * len(ch))})", ch):
                    prac[p] += 1
            extra['by_pcp_practice'] = dict(prac.most_common(30))
        finally:
            db.close()
    table = [dict(zip(names, r)) for r in rows[:200]] if 'patient_id' not in names or len(names) > 1 else None
    return {'data': {'question': question, 'family': planned['receipt']['family'], 'parameters': planned['receipt']['parameters'],
                     'statement': planned['receipt']['statement'], 'count': len(rows), 'patient_ids': ids[:MAX_IDS],
                     'columns': names, 'rows': table, **extra},
            'definition': planned['receipt']['statement'], 'sql': sql, 'source': cohort_families.VERSION}


def t_ask(ctx, question, answer_type=None):
    if not ctx.allow_model_ask:
        raise ToolError('ask (the workspace answer path with model rungs) is disabled for this deliverable; use cohort or the engines')
    out = ctx.ws.ask(question, answer_type=answer_type, send_factory=ctx.send_factory)
    a = out.get('answer')
    if isinstance(a, dict) and isinstance(a.get('patient_ids'), list):
        a = {**a, 'patient_ids': a['patient_ids'][:MAX_IDS], 'count': len(a['patient_ids'])}
    return {'data': {'question': question, 'headline': out.get('headline'), 'answer': a, 'answer_type': out.get('answer_type'),
                     'interpretation': out.get('interpretation'), 'decision': out.get('decision'), 'pipeline': out.get('pipeline')},
            'definition': str(out.get('interpretation') or '')[:600], 'sql': out.get('query') or (out.get('receipt') or {}).get('query'),
            'source': f"workspace answer path ({out.get('pipeline')})", 'model_cost_usd': out.get('cost_usd'), 'no_cache': False}


def _compact_row(r):
    g = r.get('gap') or {}
    v = lambda f: (f or {}).get('value') if isinstance(f, dict) else f
    t = r.get('task') or {}
    row = {'patient_id': r['patient_id'], 'route': r['route'], 'program_id': r.get('program_id'),
           'vendor_slot': (r.get('vendor_slot') or {}).get('slot_id'), 'practice': v((r.get('attribution') or {}).get('practice_id')),
           'pcp_npi': v((r.get('attribution') or {}).get('npi')), 'payer': v((r.get('coverage') or {}).get('payer_id')),
           'line_of_business': v((r.get('coverage') or {}).get('line_of_business')), 'age': v(r.get('age')),
           'priority': t.get('priority'), 'channel': t.get('channel'), 'due_date': t.get('due_date'), 'script_template_id': t.get('script_template_id'),
           'exclusion': ((r.get('exclusions') or [{}])[0] or {}).get('reason') if r['route'] == 'exclude' else None}
    if g.get('type') == 'gdmt_pillar_missing':
        row.update(missing_pillars=v(g.get('missing_pillars')) or [], present_pillars=v(g.get('present_pillars')) or [],
                   barrier_documented_pillars=v(g.get('barrier_documented_pillars')) or [], open_with_barrier_pillars=v(g.get('barrier_pillars')) or [],
                   stale_barrier_pillars=v(g.get('stale_barrier_pillars')) or [])
    elif g.get('type') == 'pt_after_ed_low_back_pain':
        row.update(ed_visit_date=v(g.get('ed_visit_date')), days_since_ed=v(g.get('days_since_ed')), last_pt_date=v(g.get('last_pt_date')),
                   window_open=v(g.get('window_open')))
    return row


def _plan(ctx, program_kind, patient_ids=None):
    key = ('plan', program_kind, json.dumps(patient_ids))
    if key not in ctx._mem:
        ctx._mem[key] = ctx.ws.plan(program_kind=program_kind, patient_ids=patient_ids, act=True)
    return ctx._mem[key]


def t_plan(ctx, program_kind, patient_ids=None):
    env = _plan(ctx, program_kind, patient_ids)
    p = env['plan']
    ex = p.get('execution') or {}
    s = p['summary']
    rows = [_compact_row(r) for r in p['patients']]
    by_practice = defaultdict(Counter)
    for r in rows:
        by_practice[r['practice'] or 'unattributed'][r['route']] += 1
    from ..plans import router
    return {'data': {'program_kind': p['program_kind'], 'as_of': p['as_of'], 'gap_type': p['gap_type'],
                     'programs': [{'id': x.get('id'), 'label': x.get('label') or x.get('name')} for x in (p.get('programs') or [])],
                     'cohort_source': (p.get('cohort') or {}).get('source'), 'patients': s['patients'],
                     'summary': {k: s.get(k) for k in ('by_route', 'by_program', 'by_vendor_slot', 'by_exclusion_reason', 'by_payer',
                                                       'by_line_of_business', 'tasks', 'tasks_by_priority', 'missing_pillar_counts')},
                     'by_practice': {k: dict(v) for k, v in sorted(by_practice.items(), key=lambda kv: -sum(kv[1].values()))},
                     'execution': {'decision_id': ex.get('decision_id'), 'work_items': ex.get('work_items'), 'replay': ex.get('replay'),
                                   'funnel': (ex.get('summary') or {}).get('funnel'), 'outcomes': (ex.get('summary') or {}).get('outcomes'),
                                   'final_state': (ex.get('summary') or {}).get('final_state'), 'completion': ex.get('completion'),
                                   'items': ex.get('items'), 'limits': ex.get('limits')},
                     'patient_rows': rows},
            'definition': (f"deterministic router ({router.SCHEMA}) over the semantic layer; program {p['program_kind']}; cohort from "
                           f"{(p.get('cohort') or {}).get('source')}; routing digest {router.digest(p)[:16]}; action backbone decision "
                           f"{str(ex.get('decision_id'))[:16]} (simulated counterparties)"),
            'source': 'plans/router.py + plans/backbone.py'}


def _store(ctx):
    from ..plans import backbone
    p = backbone.store_path(ctx.ws.dir) / 'program-work.sqlite3'
    if not p.is_file():
        raise ToolError('No action store yet: run the plan tool for this program first')
    return sqlite3.connect(f'file:{p}?mode=ro', uri=True)


def _timeline(payload):
    a = payload.get('action') or {}
    steps = []
    for st in a.get('steps') or []:
        rc = st.get('receipt') or {}
        steps.append({'step': st.get('step'), 'attempt': st.get('attempt'), 'sent_on': st.get('sent_on'), 'status': st.get('status'),
                      'outcome': rc.get('outcome'), 'reason': rc.get('reason'), 'at': rc.get('at'), 'receipt_id': rc.get('receipt_id'),
                      'counterparty': rc.get('counterparty'), 'detail': rc.get('detail')})
    return {'item_id': payload['id'], 'patient_id': payload['patient_id'], 'state': payload.get('state'), 'program_id': a.get('program_id'),
            'program': a.get('program'), 'agent': a.get('agent'), 'vendor': a.get('vendor'), 'milestones': a.get('milestones'),
            'closure': (payload.get('closure') or {}).get('status') if isinstance(payload.get('closure'), dict) else payload.get('closure'),
            'barriers': len(a.get('barriers') or []), 'steps': steps, 'followups': [f.get('type') for f in a.get('followups') or []],
            'human_decisions': payload.get('human_decisions') or []}


ESCALATE = {'appeal': 'upheld denial needs a clinician decision', 'alternate_program': 'program could not serve the patient; choose another route',
            'recontact': 'patient not reached after the agent attempts', 'reroute': 'coverage changed; route needs review',
            'barrier_review': 'documented barrier needs a pharmacist decision'}


def t_work(ctx, program_kind, view='summary', limit=40, patient_id=None):
    lane = f'plan:{program_kind}'
    db = _store(ctx)
    try:
        q = 'SELECT payload FROM work_items WHERE (program = ? OR program LIKE ?)'
        args = [lane, lane + ':followup:%']
        if patient_id:
            q += ' AND patient = ?'
            args.append(patient_id)
        payloads = [json.loads(r[0]) for r in db.execute(q + ' ORDER BY program, patient', args)]
    finally:
        db.close()
    items = [p for p in payloads if ':followup:' not in p['lane']]
    fus = [p for p in payloads if ':followup:' in p['lane']]
    as_of = ctx.as_of
    states = Counter(p.get('state') for p in items)
    fu_counts = Counter(f"{p.get('type')}:{p.get('state')}" for p in fus)
    out = {'program_kind': program_kind, 'lane': lane, 'as_of': as_of, 'plan_items': len(items), 'followups': len(fus),
           'item_states': dict(states), 'followup_counts': dict(fu_counts)}
    # what the agents do without a person: steps with receipts; what reaches a person: escalations
    receipts = sum(len([s for s in (p.get('action') or {}).get('steps') or [] if s.get('receipt')]) for p in items)
    out['counterparty_receipts'] = receipts
    esc = []
    for f in fus:
        human = (f.get('type') == 'appeal' and f.get('resolution') == 'upheld') or f.get('type') in ('alternate_program', 'recontact', 'reroute')
        if human and f.get('state') not in ('resolved', 'superseded', 'cancelled', 'decided'):
            esc.append({'followup_id': f.get('id'), 'patient_id': f['patient_id'], 'type': f.get('type'), 'reason': f.get('reason') or f.get('detail'),
                        'trigger': f.get('trigger'), 'owner': f.get('owner'), 'due': f.get('due'), 'state': f.get('state'),
                        'why_escalated': ESCALATE.get(f.get('type'), 'needs a person'), 'parent_item': f.get('parent_item')})
    upheld = [f for f in fus if f.get('type') == 'appeal' and f.get('resolution') == 'upheld']
    for f in upheld:
        if not any(e['followup_id'] == f.get('id') for e in esc):
            esc.append({'followup_id': f.get('id'), 'patient_id': f['patient_id'], 'type': 'appeal_upheld', 'reason': f.get('reason'),
                        'trigger': f.get('trigger'), 'owner': f.get('owner'), 'due': f.get('due'), 'state': f.get('state'),
                        'why_escalated': ESCALATE['appeal'], 'parent_item': f.get('parent_item')})
    out['escalations'] = esc[:limit]
    out['escalation_count'] = len(esc)
    out['escalations_by_type'] = dict(Counter(e['type'] for e in esc))
    # next week: open follow-ups due in the 7 days after as_of, and items still mid-journey
    from datetime import date, timedelta
    a = date.fromisoformat(str(as_of)[:10])
    wk = (a + timedelta(days=7)).isoformat()
    nxt = [f for f in fus if f.get('state') in ('open', 'pending', 'scheduled') and f.get('due') and str(f['due'])[:10] <= wk]
    out['next_week'] = {'window_end': wk, 'followups_due': len(nxt), 'by_type': dict(Counter(f.get('type') for f in nxt)),
                        'items_in_progress': sum(1 for p in items if p.get('state') not in TERMINAL)}
    if view in ('timelines', 'patient') or patient_id:
        out['timelines'] = [_timeline(p) for p in items[:limit]]
    return {'data': out, 'definition': f'action backbone work store ({lane}): work items, follow-ups and simulated-counterparty receipts as of {as_of}',
            'source': 'plans/backbone.py work store', 'no_cache': True}


TERMINAL = {'completed', 'declined', 'unreachable', 'eligibility_denied', 'coverage_denied', 'prior_auth_denied', 'dropped_out',
            'dropped_after_no_show', 'not_scheduled', 'coverage_found', 'coverage_not_found', 'cancelled'}


def _sim(ctx, program_kind, alternatives, horizon_months, iterations, draws):
    from ..simulator import engine
    from ..plans import catalog as catmod
    key = ('sim', program_kind, json.dumps(alternatives), horizon_months, iterations, draws)
    if key not in ctx._mem:
        ctx._mem[key] = engine.simulate(ctx.semantic, program_kind=program_kind, as_of=ctx.as_of, horizon_months=horizon_months,
                                        alternatives=alternatives, iterations=iterations, workspace=ctx.ws.slug, catalog=catmod.load(),
                                        return_draws=draws)
    return ctx._mem[key]


def t_simulate(ctx, program_kind, alternatives=None, horizon_months=12, iterations=2000):
    out = _sim(ctx, program_kind, alternatives, horizon_months, iterations, False)
    keep = ('id', 'program_kind', 'as_of', 'horizon_months', 'horizon_end', 'iterations', 'interval', 'plan', 'cohort', 'arms',
            'summary_table', 'baseline', 'contract', 'effects', 'assumptions', 'narrative')
    data = {k: out.get(k) for k in keep}
    data['parameters'] = [{k: p.get(k) for k in ('id', 'label', 'value', 'low', 'high', 'kind', 'source', 'why') if k in p} for p in out.get('parameters') or []][:60]
    data['calibration'] = {k: v for k, v in (out.get('calibration') or {}).items() if not isinstance(v, (list, dict)) or k in ('summary', 'held_out')}
    return {'data': data, 'definition': (f"population simulator {out['schema']} {out['id']}: {out['iterations']} Monte Carlo draws, seed {out['seed']}, "
                                         f"horizon {out['horizon_months']} months, routing digest {out['plan']['routing_digest'][:16]}, intervals {out['interval']}"),
            'source': 'simulator/engine.py'}


def _population(ctx, iterations, seed=7):
    from ..clinical import contract_strategy, vbc
    key = ('pop', iterations, seed)
    if key in ctx._mem:
        return ctx._mem[key]
    path = ctx.engine_dir()
    db = vbc._connect(path)
    try:
        as_of = ctx.as_of
        start, end = vbc._period({}, as_of)
        people = vbc.eligibility(db, as_of, start, end)
        c, m = contract_strategy.member_costs(db, people, start, end)
        tr = contract_strategy.observed_trend(db, None, as_of, end)
    finally:
        db.close()
    trend_sd = min(0.10, max(0.005, tr['annual_trend_se'])) if tr.get('annual_trend_se') else contract_strategy.TREND_SD_FALLBACK
    sim = contract_strategy.Simulation(c, m, iterations, seed, 0.0, trend_sd, contract_strategy.DEFAULTS['risk_drift_sd'])
    res = {'boot_rel': (sim.boot / sim.boot.mean()).round(6).tolist(), 'z_trend': sim.z_trend.round(5).tolist(),
           'z_drift': sim.z_drift.round(5).tolist(), 'trend_sd': round(trend_sd, 5), 'risk_drift_sd': contract_strategy.DEFAULTS['risk_drift_sd'],
           'bootstrap_members': int(sim.members), 'bootstrap_base_pmpm': round(sim.base_pmpm, 2), 'base_period': [start, end],
           'observed_trend': {k: (round(v, 4) if isinstance(v, float) else v) for k, v in tr.items()}, 'seed': seed}
    ctx._mem[key] = res
    return res


def _what_if(ctx, program_kind, arm_id, alternatives, iterations):
    from ..clinical import programs as programs_mod, contract_strategy
    from ..simulator import effects as fx, engine, funnel
    from ..plans import catalog as catmod
    out = _sim(ctx, program_kind, alternatives, 12, iterations, True)
    d = out['draws']
    if arm_id not in d['arms']:
        raise ToolError(f"arm {arm_id!r} not simulated; arms: {sorted(d['arms'])}")
    arm = d['arms'][arm_id]
    cat = catmod.load()
    cfg = fx.resolve(fx.load_config(), None)
    plan, _ = engine.resolve_plan(ctx.semantic, None, program_kind=program_kind, as_of=ctx.as_of, catalog=cat)
    arms = funnel.normalize_arms(alternatives, plan['program_kind'], plan, cat)
    arm_spec = next(a for a in arms if a['id'] == arm_id)
    variant, ids, used = funnel.arm_plan(plan, arm_spec, cat)
    K = int(min(cfg['defaults']['funnel_scenarios'], iterations))
    status, start, flags, program = funnel.scenarios(variant, ids, K, workspace=ctx.ws.slug, seed=out['seed'], cat=cat)
    sk = np.arange(iterations) % K
    counts, unit_costs, oap = {}, {}, {}
    for prog in sorted({p for p in program if p}):
        cols = [j for j, p in enumerate(program) if p == prog]
        counts[prog] = {fl: flags[fl][:, cols].sum(1)[sk].astype(int).tolist() for fl in ('outreach', 'enrolled', 'attended', 'completed')}
        pc = cfg['programs'].get(prog) or {}
        unit_costs[prog] = {k: (v or {}).get('value') for k, v in (pc.get('costs') or {}).items()}
        o = pc.get('oap')
        if o:
            enrolled = np.array(counts[prog]['enrolled'], float)
            completed = np.array(counts[prog]['completed'], float)
            p_met = o['p_target_met_if_completed']['value']
            att = float(np.mean(np.where(enrolled > 0, completed * p_met / np.maximum(enrolled, 1), 0.0)))
            oap[prog] = {'track': o.get('track'), 'annual_allowed': o['annual_allowed']['value'], 'months': min(out['horizon_months'], o.get('care_period_months', 12)),
                         'withheld_share': o['withheld_share']['value'], 'outcome_attainment_threshold': o['outcome_attainment_threshold']['value'],
                         'medicare_share': o['medicare_share']['value'], 'co_management_per_enrolled': o['co_management_per_enrolled']['value'],
                         'p_target_met_if_completed': p_met, 'simulated_attainment': round(att, 4),
                         'counts_in_benchmark': int(str(out['as_of'])[:4]) >= int(o['counts_in_aco_benchmark_from_year']['value']),
                         'sources': sorted({(o[k] or {}).get('source') for k in ('annual_allowed', 'withheld_share', 'outcome_attainment_threshold', 'medicare_share', 'co_management_per_enrolled') if isinstance(o.get(k), dict) and o[k].get('source')})}
    terms = programs_mod.contract_terms((cfg.get('contract') or {}).get('program') or 'MSSP ENHANCED')
    popn = out['contract']['population']
    terms = contract_strategy._resolve_msr(dict(terms), popn)
    label = (terms.get('program') or {}).get('label')
    terms = {k: v for k, v in terms.items() if k != 'program'}
    pop = _population(ctx, iterations)
    saved = arm['saved']
    tb = contract_strategy.typical_bounds()
    res = {'program_kind': program_kind, 'arm': {'id': arm_id, 'label': arm_spec.get('label'), 'patients': len(ids), 'programs': used,
                                                 'saved_total': np.asarray(saved).sum(1).round(2).tolist(),
                                                 'pharmacy_total': np.asarray(arm['pharmacy_cost']).sum(1).round(2).tolist(),
                                                 'counts': counts, 'unit_costs': unit_costs, 'oap': oap},
           'population': {**pop, 'member_months': out['contract']['member_months'], 'members': popn,
                          'projected_do_nothing_pmpm': out['contract']['projected_do_nothing_pmpm'], 'basis': out['contract']['population_basis']},
           'terms': terms, 'terms_label': label, 'terms_sources': out['contract'].get('sources'),
           'negotiation_bounds': {k: tb[k] for k in ('sharing_rate_min', 'sharing_rate_max', 'msr_min', 'msr_max')},
           'negotiation_bounds_label': tb['label'], 'simulation_id': out['id'], 'iterations': iterations, 'seed': out['seed'],
           'arms_available': sorted(d['arms'])}
    res['defaults'] = {**finance.DEFAULT_SLIDERS, 'attainment': next((o['simulated_attainment'] for o in oap.values()), None)}
    res['initial'] = finance.evaluate(res)
    res['initial_nash'] = finance.nash(res)
    return res, out, d


def t_what_if(ctx, program_kind, arm='plan', alternatives=None, iterations=1000):
    res, out, _ = _what_if(ctx, program_kind, arm, alternatives, iterations)
    return {'data': res, 'definition': (f"per-draw contract what-if: simulator {out['id']} ({iterations} draws, seed {out['seed']}) avoided cost and "
                                        f"funnel milestones for arm {arm}; population dispersion = contract_strategy member bootstrap x trend x "
                                        f"risk drift (seed {res['population']['seed']}) around the projected do-nothing PMPM "
                                        f"{res['population']['projected_do_nothing_pmpm']}; terms {res['terms_label']}"),
            'source': 'simulator/engine.py + simulator/funnel.py + clinical/contract_strategy.py + clinical/programs.py'}


def t_attribution(ctx, program_kind, by='practice', arm='plan', alternatives=None, iterations=1000, max_players=8):
    res, out, d = _what_if(ctx, program_kind, arm, alternatives, iterations)
    env = _plan(ctx, program_kind)
    rows = {r['patient_id']: _compact_row(r) for r in env['plan']['patients']}
    cohort = d['cohort']
    a = d['arms'][arm]
    pats = set(a['patients'])
    idx = [i for i, p in enumerate(cohort) if p in pats]
    X = np.asarray(a['saved'])[:, idx]
    if by == 'practice':
        names = [rows.get(cohort[i], {}).get('practice') or 'unattributed' for i in idx]
    elif by == 'program':
        names = [a['program'][i] or 'none' for i in idx]
    else:
        names = [rows.get(cohort[i], {}).get('payer') or 'unknown' for i in idx]
    top = [n for n, _ in Counter(names).most_common(max_players - 1)]
    players = [n if n in top else 'all other' for n in names]
    sh = finance.shapley(X, players, res, max_players=max_players)
    per_patient = [{'patient_id': cohort[i], 'player': players[j], 'expected_avoided_cost': round(float(X[:, j].mean()), 2),
                    'p_gap_closed': round(float(np.asarray(a['gap_closed'])[:, i].mean()), 3)} for j, i in enumerate(idx)]
    per_patient.sort(key=lambda r: -r['expected_avoided_cost'])
    return {'data': {'program_kind': program_kind, 'by': by, 'arm': arm, 'simulation_id': out['id'], 'iterations': iterations,
                     'terms_label': res['terms_label'], 'benchmark_basis': 'projected do-nothing PMPM x 1.0 (simulator contract default)',
                     **sh, 'top_patients': per_patient[:40], 'patients': len(idx), 'expected_settlement_change': res['initial']['expected_settlement_change']},
            'definition': (f"exact Shapley split (over {len(set(players))} players by {by}) of the expected settlement change from simulator {out['id']} "
                           f"per-patient avoided cost draws through {res['terms_label']} settlement arithmetic"),
            'source': 'deliverables/finance.shapley over simulator draws'}


def _benchmark(ctx, factor):
    pop = _population(ctx, 2000)
    return round(pop['bootstrap_base_pmpm'] * factor, 2)


def t_contract(ctx, focus='negotiate', benchmark_factor=1.0, options=None, care_management_pmpm=None, risk_aversion=None,
               sharing_rate_min=None, sharing_rate_max=None, msr_min=None, msr_max=None, payer_reservation_value=None):
    from ..clinical import contract_strategy
    bench = _benchmark(ctx, benchmark_factor)
    opts = options or (['MSSP ENHANCED', 'MSSP BASIC E'] if focus != 'negotiate' else ['MSSP ENHANCED'])
    p = {'focus': focus, 'benchmark_pmpm': bench, 'options': [o if isinstance(o, dict) else {'program': str(o)} for o in opts]}
    if care_management_pmpm is not None:
        p['care_management_pmpm'] = care_management_pmpm
    if risk_aversion is not None:
        p['risk_aversion'] = risk_aversion
    cp = {k: v for k, v in (('sharing_rate_min', sharing_rate_min), ('sharing_rate_max', sharing_rate_max), ('msr_min', msr_min),
                            ('msr_max', msr_max), ('reservation_value', payer_reservation_value)) if v is not None}
    if cp:
        p['counterparty'] = cp
    rep = contract_strategy.execute(ctx.engine_dir(), None, p, as_of=ctx.as_of)
    if rep.get('negotiation') and rep['negotiation'].get('frontier'):
        rep['negotiation']['frontier'] = rep['negotiation']['frontier'][:30]
    return {'data': {**rep, 'benchmark_basis': f'observed completion-adjusted allowed PMPM over the base period x {benchmark_factor} (factor stated by the request; default 1.0)'},
            'definition': f"contract_strategy {contract_strategy.VERSION}: {rep.get('method', '')[:400]}", 'source': 'clinical/contract_strategy.py'}


def t_settlement(ctx, benchmark_factor=1.0, program='MSSP ENHANCED', group_by='practice'):
    from ..clinical import vbc
    bench = _benchmark(ctx, benchmark_factor)
    rep = vbc.execute(ctx.engine_dir(), None, {'metric': 'shared_savings', 'program': program, 'terms': {'benchmark_pmpm': bench},
                                              'group_by': group_by}, as_of=ctx.as_of)
    return {'data': {**clean(rep), 'benchmark_basis': f'observed completion-adjusted allowed PMPM x {benchmark_factor}'},
            'definition': f"vbc.shared_savings on {program} terms, benchmark {bench} PMPM; settlement risk = member bootstrap; Shapley by {group_by}",
            'source': 'clinical/vbc.py'}


def t_market(ctx, unit='practice', metrics=None, direction='any', top=25):
    from ..clinical import market
    ms = [m for m in (metrics or '').split(',') if m] if isinstance(metrics, str) else (metrics or [])
    rep = market.execute(ctx.engine_dir(), None, {'unit': unit, 'metrics': ms, 'direction': direction, 'top': top}, as_of=ctx.as_of)
    rep['tickers'] = rep['tickers'][:top]
    return {'data': rep, 'definition': f"market {market.VERSION}: {market.METHOD[:500]}", 'source': 'clinical/market.py'}


def t_quality(ctx, group_by='practice', measure=None):
    from ..clinical import vbc
    p = {'metric': 'quality', 'group_by': group_by}
    if measure:
        p['measure'] = measure
    rep = vbc.execute(ctx.engine_dir(), None, p, as_of=ctx.as_of)
    return {'data': clean(rep), 'definition': f"vbc.quality by {group_by}: {str(rep.get('method') or '')[:400]}", 'source': 'clinical/vbc.py'}


def t_barriers(ctx, limit=60):
    from ..plans import barriers as barriermod, catalog as catmod
    env = _plan(ctx, 'pharmacy_gdmt')
    plan = env['plan']
    missing = {r['patient_id']: ((r.get('gap') or {}).get('missing_pillars') or {}).get('value') or [] for r in plan['patients'] if r['route'] != 'exclude'}
    db = ctx.ro()
    try:
        assessed = barriermod.assess(db, list(missing), plan['as_of'], catmod.load().get('barrier_policy') or {}, missing=missing)
        texts = {}
        rec_ids = {b.get('record_id') for v in assessed.values() for pl in v.values() for b in pl['barriers'] + pl['stale_barriers'] if b.get('record_id')}
    finally:
        db.close()
    work = {}
    try:
        sdb = _store(ctx)
        try:
            for row in sdb.execute("SELECT id, patient, program, payload FROM work_items WHERE program = 'plan:pharmacy_gdmt' OR program LIKE 'plan:pharmacy_gdmt:followup:%'"):
                pl = json.loads(row[3])
                work.setdefault(row[1], []).append({'item_id': row[0], 'lane': row[2], 'state': pl.get('state'), 'type': pl.get('type'),
                                                    'human_decisions': pl.get('human_decisions') or []})
        finally:
            sdb.close()
    except ToolError:
        pass
    rows = {r['patient_id']: _compact_row(r) for r in plan['patients']}
    cards = []
    for pid, pillars in assessed.items():
        r = rows.get(pid) or {}
        cards.append({'patient_id': pid, 'practice': r.get('practice'), 'route': r.get('route'), 'program_id': r.get('program_id'),
                      'missing_pillars': missing.get(pid), 'pillars': {k: {kk: v.get(kk) for kk in ('status', 'barriers', 'stale_barriers', 'excluded_agents', 'remaining_agents', 'usable_agents', 'flags', 'action')}
                                                                       for k, v in pillars.items()},
                      'work_items': work.get(pid, []),
                      'priority_status': min((v['status'] for v in pillars.values()), key=lambda s: ['explained', 'open_with_barrier', 'stale_only'].index(s))})
    order = {'open_with_barrier': 0, 'explained': 1, 'stale_only': 2}
    cards.sort(key=lambda c: (order.get(c['priority_status'], 3), c['patient_id']))
    status_counts = Counter(v['status'] for c in cards for v in c['pillars'].values())
    return {'data': {'as_of': plan['as_of'], 'patients_with_barriers': len(cards), 'pillar_status_counts': dict(status_counts),
                     'barrier_type_counts': dict(Counter(b['barrier_type'] for c in cards for v in c['pillars'].values() for b in v['barriers'])),
                     'cards': cards[:limit], 'source_records': len(rec_ids)},
            'definition': ('therapy_barrier rows (clinical/therapy_barriers.py reader) through the catalog barrier_policy (plans/barriers.py): '
                           'drug-scoped, recency-windowed, missing pillars only; source sentence, record id and character span kept'),
            'source': 'plans/barriers.py'}


def t_outreach(ctx, program_kind, limit=40):
    env = _plan(ctx, program_kind)
    plan = env['plan']
    rows = [r for r in plan['patients'] if r.get('task')]
    ids = [r['patient_id'] for r in rows]
    lang = {}
    db = ctx.ro()
    try:
        for i in range(0, len(ids), 500):
            ch = ids[i:i + 500]
            for pid, pl, interp in db.execute(f"SELECT patient_id, preferred_language, interpreter_needed FROM patient WHERE patient_id IN ({','.join('?' * len(ch))})", ch):
                lang[pid] = {'preferred_language': pl, 'interpreter_needed': bool(interp) if interp is not None else None}
    finally:
        db.close()
    from ..plans import router
    out = []
    for r in rows[:limit]:
        t = r['task']
        c = _compact_row(r)
        barriers_plain = []
        g = r.get('gap') or {}
        for name, pl in (g.get('pillars') or {}).items():
            for b in ((pl.get('barriers') or {}).get('value') or [])[:1]:
                barriers_plain.append({'pillar': name, 'type': b.get('barrier_type'), 'detail': b.get('detail'), 'drug': b.get('drug_key') or b.get('drug_class'),
                                       'evidence_text': b.get('evidence_text'), 'record_id': b.get('record_id')})
        out.append({**c, **lang.get(r['patient_id'], {}), 'gap_text': router._gap_text(g), 'owner': t.get('owner'),
                    'collect_at_outreach': t.get('collect_at_outreach'), 'program_label': next((p.get('label') or p.get('name') for p in plan.get('programs') or [] if p.get('id') == t.get('program_id')), t.get('program_id')),
                    'barriers': barriers_plain})
    return {'data': {'program_kind': program_kind, 'patients_with_tasks': len(rows), 'languages': dict(Counter((lang.get(i) or {}).get('preferred_language') or 'unknown' for i in ids)),
                     'channels': dict(Counter(r['task'].get('channel') for r in rows)), 'patients': out},
            'definition': f"routed plan outreach tasks ({program_kind}) joined to patient preferred_language / interpreter_needed and documented barriers",
            'source': 'plans/router.py tasks + patient table'}


TOOLS = {}


def _tool(name, fn, description, properties=None, required=None):
    TOOLS[name] = {'name': name, 'fn': fn, 'description': description,
                   'schema': {'type': 'object', 'properties': properties or {}, 'required': required or []}}


_PK = {'type': 'string', 'enum': ['pharmacy_gdmt', 'virtual_pt', 'coverage_check'],
       'description': 'program kind: pharmacy_gdmt (heart failure GDMT pharmacist programs), virtual_pt (ED low back pain -> PT, incl. CMS ACCESS MSK)'}
_tool('catalog', t_catalog, 'Semantic catalog: tables and columns, program catalog, cohort families, market and VBC metrics, analyst doc names.')
_tool('doc', t_doc, 'Read one analyst doc (from catalog.analyst_docs).', {'name': {'type': 'string'}}, ['name'])
_tool('cohort', t_cohort, 'A patient list answered by deterministic cohort families (SQL rendered by code, receipt includes the exact definition and SQL). '
      'Ask in plain words with explicit codes / windows when possible, e.g. "Diabetics 18-75 with no statin fill in the last 120 days".',
      {'question': {'type': 'string'}}, ['question'])
_tool('ask', t_ask, 'The workspace answer path (governed metrics, engines, analysts). May use a model; prefer cohort or a specific engine tool.',
      {'question': {'type': 'string'}, 'answer_type': {'type': 'string', 'enum': ['count', 'value', 'patient_set', 'table']}}, ['question'])
_tool('plan', t_plan, 'Route a program: deterministic router (coverage, attribution, gap, program + vendor slot, outreach task) run through the action '
      'backbone (durable work items, simulated counterparties). Returns route counts, by-practice counts, funnel, outcomes, completion and compact patient rows.',
      {'program_kind': _PK, 'patient_ids': {'type': 'array', 'items': {'type': 'string'}}}, ['program_kind'])
_tool('work', t_work, 'Action backbone work state for a program: item states, follow-ups, counterparty receipt count, escalations a person must decide, '
      'next week. view=timelines adds per-patient step timelines with receipts.',
      {'program_kind': _PK, 'view': {'type': 'string', 'enum': ['summary', 'timelines']}, 'limit': {'type': 'integer'}, 'patient_id': {'type': 'string'}},
      ['program_kind'])
_tool('simulate', t_simulate, 'Population simulator: arms (routed plan, alternatives) vs doing nothing over a horizon with 90% intervals: admissions, ED, '
      'medical cost, program cost, net savings, settlement and shared-savings change, quality, parameters with sources.',
      {'program_kind': _PK, 'alternatives': {'type': 'array', 'items': {'type': 'string'}}, 'horizon_months': {'type': 'integer'},
       'iterations': {'type': 'integer'}}, ['program_kind'])
_tool('what_if', t_what_if, 'Per-draw arrays for in-browser contract what-if (sliders: scale, benchmark factor, ACCESS OAP attainment, cost multiplier, '
      'trend): simulator avoided cost + funnel counts, population bootstrap draws, contract terms, OAP parameters, negotiation bounds, and the '
      'initial evaluation. Use with the AgereFinance browser library (evaluate / nash).',
      {'program_kind': _PK, 'arm': {'type': 'string'}, 'alternatives': {'type': 'array', 'items': {'type': 'string'}}, 'iterations': {'type': 'integer'}},
      ['program_kind'])
_tool('attribution', t_attribution, 'Exact Shapley attribution of the simulated shared-savings change across practices, programs or payers, with '
      'standalone and leave-one-out counterfactuals and top patients by expected avoided cost.',
      {'program_kind': _PK, 'by': {'type': 'string', 'enum': ['practice', 'program', 'payer']}, 'arm': {'type': 'string'},
       'alternatives': {'type': 'array', 'items': {'type': 'string'}}, 'iterations': {'type': 'integer'}, 'max_players': {'type': 'integer'}},
      ['program_kind'])
_tool('contract', t_contract, 'Contract finance (options view): value / compare contract tracks or negotiate (Nash bargaining over sharing rate x MSR), '
      'VaR / CVaR, upside call and downside put, Greeks. Benchmark = observed PMPM x benchmark_factor.',
      {'focus': {'type': 'string', 'enum': ['value', 'compare', 'negotiate']}, 'benchmark_factor': {'type': 'number'},
       'options': {'type': 'array', 'items': {'type': 'string'}}, 'care_management_pmpm': {'type': 'number'}, 'risk_aversion': {'type': 'number'},
       'sharing_rate_min': {'type': 'number'}, 'sharing_rate_max': {'type': 'number'}, 'msr_min': {'type': 'number'}, 'msr_max': {'type': 'number'},
       'payer_reservation_value': {'type': 'number'}})
_tool('settlement', t_settlement, 'Exact shared-savings settlement on a named program track with settlement risk (bootstrap VaR) and Shapley fair allocation by group.',
      {'benchmark_factor': {'type': 'number'}, 'program': {'type': 'string'}, 'group_by': {'type': 'string', 'enum': ['practice', 'pcp', 'coverage_type']}})
_tool('market', t_market, 'Population market tickers: practices / PCPs / payers x metrics (ed_rate, admit_rate, pmpm, a1c_poor, bp_control, denial_rate, '
      'no_show_rate) with trend, volatility, surprise bits (look-elsewhere corrected), move type, sparkline, follow-up question.',
      {'unit': {'type': 'string', 'enum': ['practice', 'pcp', 'payer']}, 'metrics': {'type': 'array', 'items': {'type': 'string'}},
       'direction': {'type': 'string', 'enum': ['any', 'worse', 'better']}, 'top': {'type': 'integer'}})
_tool('quality', t_quality, 'Quality measures (internal definitions: diabetes A1c poor control, BP control, statin therapy) by group with shrunk rates and 95% intervals.',
      {'group_by': {'type': 'string', 'enum': ['none', 'practice', 'pcp', 'coverage_type', 'age_band', 'sex', 'race', 'ethnicity', 'language']},
       'measure': {'type': 'string'}})
_tool('barriers', t_barriers, 'GDMT barrier review: per patient the missing pillars, documented barriers with the source sentence, record id and span, '
      'excluded / remaining / usable agents, the barrier-specific action and the work items a decision writes back to.', {'limit': {'type': 'integer'}})
_tool('outreach', t_outreach, 'Per-patient outreach context from the routed plan: channel, owner, priority, due date, script template, preferred language, '
      'interpreter need, the gap and documented barriers in plain words.', {'program_kind': _PK, 'limit': {'type': 'integer'}}, ['program_kind'])


def model_tools():
    """Tool definitions for the Messages API."""
    return [{'name': s['name'], 'description': s['description'], 'input_schema': s['schema']} for s in TOOLS.values()]
