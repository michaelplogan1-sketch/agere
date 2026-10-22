"""Exact answers for factory questions, computed by the deterministic compiler/executor on the active warehouse.

For every item: resolve its canonical request (typed families carry it; grammar families are read by the shared
planner's free parser), compile it with the shared compiler (request_plan.compile_request / request_program.prepare /
cohort-family spec), execute it read-only (sql_query.execute, the same governor the planner uses; analytical engines and
request programs through stack.run_graph via shared_planner.execute_contract / request_program.execute) and keep only
items that execute to a complete answer. Each labeled row records the answer, its result signature, the exact SQL and
the source tables/columns it read (provenance). Learned-shape saving is suppressed: labeling never writes the active
stack's learning stores. Typed items whose wording the free parser reads differently (different result) are dropped
as ambiguous. Never reads private/ eval truth.

Parallel (process pool over fixed-size units), resumable (a unit with a .done marker is skipped), sharded output.
Usage: python tools/training/label.py --questions artifacts/training/questions --out artifacts/training/labels --workers 6
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import get_context
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

VERSION = 'training-label/1'
SCHEMA = 'training-label-row/1'
MAX_ROWS_KEPT = 50
UNIT = 500


class _Retriever:
    """Metadata-only retriever: graph execution reads snapshot metadata; text search is not part of labeling."""
    encoder = reranker = None

    def __init__(self, path):
        self.path, self._meta = Path(path), None

    @property
    def meta(self):
        if self._meta is None: self._meta = json.loads((self.path / 'run.json').read_text())
        return self._meta

    def search(self, *a, **k): raise RuntimeError('evidence search is not used for exact labels')


class LightStack:
    """The parts of Stack that compiled-request execution uses, without loading the embedding index."""
    shared_request_planning = True
    allow_live = False

    def __init__(self, path):
        self.path = Path(path)
        self.retriever = _Retriever(path)
        self.receipts = self.path / 'provider-receipts'

    def run_graph(self, plan, *, question=None):
        from clinical_core.agere.clinical.analysis_graph import execute
        return execute(self, plan, question=question)

    def review(self, *a, **k): raise RuntimeError('model review is not used for exact labels')


class Context:
    def __init__(self, stack_path, full_stack=False, grammar_check=True, s1_verify=False):
        from clinical_core.agere.clinical import request_plan as R
        from tools.training.question_factory import assert_public
        self.path = assert_public(stack_path)
        self.catalog = R.Catalog.load(self.path)
        self.as_of = self.catalog.identity.get('as_of')
        self.grammar_check = grammar_check
        self.s1_verify, self.s1_ledger = s1_verify, None  # System One re-reads chart yes/no items (live Jev/Perplexity, cents)
        if full_stack:
            from clinical_core.agere.clinical.stack_runtime import Stack
            self.stack = Stack(self.path, allow_live=False)
        else:
            self.stack = LightStack(self.path)
        self._tables = None

    @property
    def tables(self):
        if self._tables is None:
            from clinical_core.agere.clinical.cohort_families import _tables
            self._tables = _tables(self.path)
        return self._tables


# ------------------------------------------------------------------------------------------------------- compile
def resolve(ctx, item):
    request = item.get('request')
    if request: return request
    from tools.training.question_factory import resolve_request
    return resolve_request(ctx.catalog, item['canonical'])


def compile_contract(ctx, question, request):
    from clinical_core.agere.clinical import request_plan as R, request_program
    if request['kind'] == 'program': return request_program.prepare(ctx.catalog, question, request['value'])
    if request['kind'] == 'request': return R.compile_request(ctx.catalog, question, request['value'])
    if request['kind'] == 'analysis':
        from clinical_core.agere.clinical import general_analysis
        return general_analysis.prepare(ctx.catalog, question, request['value'])
    raise ValueError(f"unknown request kind {request['kind']}")


def receipt(contract, review):
    return {'request_contract': contract, 'interpretation': {'interpreter': 'compositional_grammar', 'review': review,
            'execution_accepted': True, 'attempts': []}, 'new_paid_api_calls': 0, 'known_api_estimate_usd': 0,
            'api_cost_known': True, 'steps': [], 'feedback_identity': None,
            # program_origin: suppresses learned-shape saving so labeling never writes the stack's learning stores
            'program_origin': {'training_label': VERSION}}


# ------------------------------------------------------------------------------------------------------- execute
def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:20]


def compact_report(report):
    out = {'returns': report['returns'], 'columns': report.get('columns')}
    if report['returns'] in ('count', 'value'): out['value'] = report['value']
    elif report['returns'] == 'patient_set':
        ids = report['patient_ids']; out.update(count=len(ids), patient_ids_sha=signature(ids), sample_patient_ids=ids[:10])
    else:
        rows = report['rows']; out.update(row_count=len(rows), rows=rows[:MAX_ROWS_KEPT])
    out['truncated'] = bool(report.get('truncated'))
    return out


def compact_answer(answer):
    keep = ('status', 'answer_type', 'headline', 'count', 'value', 'missing')
    out = {k: answer[k] for k in keep if k in answer}
    if answer.get('table') is not None: out.update(row_count=len(answer['table']), rows=answer['table'][:MAX_ROWS_KEPT])
    if answer.get('patient_ids'): out['patient_ids_sha'] = signature(sorted(answer['patient_ids']))
    if answer.get('sections'): out['sections'] = [{'title': s['title'], 'answer': compact_answer(s['answer'])} for s in answer['sections']]
    return out


def execute_plain(ctx, contract):
    from clinical_core.agere.clinical import sql_query
    report = sql_query.execute(ctx.path, {'query': contract['query'], 'returns': contract['returns']}, target=ctx.catalog.target,
                               time_limit=float(os.environ.get('AGERE_LABEL_SECONDS', '30')))
    if report['truncated']: raise ValueError('result exceeds the complete-output bound')
    return compact_report(report)


def execute_contract(ctx, question, request, contract):
    """-> compact answer (status answered) or raises ValueError with the reason."""
    from clinical_core.agere.clinical import shared_planner as S, request_program
    if request['kind'] == 'request' and not contract['request'].get('analyses') and not contract['request'].get('workflow'):
        return {'status': 'answered', **execute_plain(ctx, contract)}
    if request['kind'] == 'program':
        result = request_program.execute(ctx.stack, question, request_program.graph_for(contract),
                                         receipt(contract, 'fully_consumed_grammar_program'))
    elif request['kind'] == 'analysis':
        from clinical_core.agere.clinical import general_analysis as G
        result = G.execute(ctx.stack, question, G.graph_for(contract), receipt(contract, 'fully_consumed_grammar'), economics=S.economics)
    else:
        result = S.execute_contract(ctx.stack, question, S.graph_for(contract), receipt(contract, 'fully_consumed_grammar'))
    answer = result['answer']
    if answer.get('status') != 'answered' or answer.get('missing'):
        raise ValueError('incomplete answer: ' + '; '.join(map(str, answer.get('missing') or [answer.get('status')]))[:300])
    return compact_answer(answer)


def execute_slots(ctx, slots):
    from clinical_core.agere.clinical import bottled, cohort_families as cf, sql_query
    spec = bottled.spec_from_slots(slots, ctx.as_of)
    graph = cf.to_graph({'family': spec['family'], 'parameters': spec['parameters'], 'returns': spec['returns']}, ctx.as_of, ctx.tables)
    params = graph['nodes'][0]['parameters']
    report = sql_query.execute(ctx.path, params, target=ctx.catalog.target, time_limit=float(os.environ.get('AGERE_LABEL_SECONDS', '30')))
    if report['truncated']: raise ValueError('result exceeds the complete-output bound')
    return {'status': 'answered', **compact_report(report)}, params['query'], spec


# ------------------------------------------------------------------------------- hard types: engines and steps
ENGINES = {'medication_state': 'medication_state', 'default_definition': 'default_definitions'}


def cohort_ids(ctx, canonical):
    """Exact patient ids of a grammar-read cohort question (full list, not the compact sample)."""
    from clinical_core.agere.clinical import sql_query
    from tools.training.question_factory import resolve_request
    request = resolve_request(ctx.catalog, canonical)
    if request is None or request['kind'] != 'request': raise ValueError(f'cohort not read by the grammar: {canonical}')
    contract = compile_contract(ctx, canonical, request)
    report = sql_query.execute(ctx.path, {'query': contract['query'], 'returns': 'patient_set'}, target=ctx.catalog.target,
                               time_limit=float(os.environ.get('AGERE_LABEL_SECONDS', '30')))
    if report.get('truncated'): raise ValueError('cohort exceeds the complete-output bound')
    return report['patient_ids'], contract


def execute_engine(ctx, value):
    """A registered engine (validate/execute/answer contract of analysis_graph.CLINICAL_LOGIC) on its exact cohort.
    -> (compact answer, provenance)"""
    import importlib
    if value['operation'] not in ENGINES: raise ValueError(f"engine {value['operation']} has no exact labeler")
    mod = importlib.import_module(f"clinical_core.agere.clinical.{ENGINES[value['operation']]}")
    ids, contract = cohort_ids(ctx, value['cohort']) if value.get('cohort') else (None, None)
    report = mod.execute(ctx.path, ids, value['parameters'], ctx.as_of)
    if report.get('declined'): raise ValueError('engine declined: ' + '; '.join(map(str, report.get('missing') or []))[:300])
    members = sorted(report.get('patient_ids') or [])
    scalars = {k: v for k, v in report.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    out = {'status': 'answered', 'answer_type': value.get('returns') or 'table', 'count': len(members),
           'patient_ids_sha': signature(members), 'sample_patient_ids': members[:10],
           'value': {**scalars, 'patients': len(members)} if value.get('returns') != 'count' else len(members)}
    if report.get('definition_name'):
        out['definition'] = {'id': report.get('definition'), 'name': report['definition_name'], 'basis': report.get('definition_basis'),
                             'period': report.get('period'), 'version': report.get('schema')}
    groups = report.get('groups') or report.get('by_group')
    if isinstance(groups, list): out['rows'] = groups[:MAX_ROWS_KEPT]
    prov = provenance(contract) if contract else {'tables': [], 'columns': [], 'query': None, 'request_id': None}
    prov = {**prov, 'engine': value['operation'], 'engine_version': report.get('schema'), 'cohort': value.get('cohort')}
    return out, prov


def _rows_by(answer, by):
    rows = answer.get('rows') or []
    key = next((c for c in (answer.get('columns') or []) if c not in ('count', 'patients')), None)
    val = next((c for c in (answer.get('columns') or []) if c in ('count', 'patients')), None)
    if key is None or val is None: raise ValueError('step result is not a grouped count')
    return {str(r.get(key)): r.get(val) or 0 for r in rows}, key


def combine(kind, answers, spec):
    """Exact combination of executed step answers."""
    if kind == 'delta_by_group':
        a, key = _rows_by(answers[0], spec.get('by')); b, _ = _rows_by(answers[1], spec.get('by'))
        rows = sorted(({key: g, 'before': a.get(g, 0), 'after': b.get(g, 0), 'change': b.get(g, 0) - a.get(g, 0)} for g in set(a) | set(b)),
                      key=lambda r: (-abs(r['change']), r[key]))
        total = sum(r['change'] for r in rows)
        return {'status': 'answered', 'answer_type': 'table', 'returns': 'table', 'row_count': len(rows), 'rows': rows[:MAX_ROWS_KEPT],
                'value': {'total_change': total, 'top_driver': rows[0][key] if rows else None,
                          'top_driver_share': round(rows[0]['change'] / total, 4) if rows and total else None}}
    if kind == 'trend':
        series, _ = _rows_by(answers[0], 'month')
        pts = [series[k] for k in sorted(series)]
        if len(pts) < 2: raise ValueError('a trend needs at least two periods')
        n = len(pts); mx = (n - 1) / 2; my = sum(pts) / n
        slope = sum((i - mx) * (y - my) for i, y in enumerate(pts)) / sum((i - mx) ** 2 for i in range(n))
        rel = slope / my if my else 0.0
        direction = 'up' if rel > 0.01 else 'down' if rel < -0.01 else 'flat'
        return {'status': 'answered', 'answer_type': 'value', 'returns': 'value', 'rows': [{'month': k, 'count': series[k]} for k in sorted(series)],
                'value': {'direction': direction, 'slope_per_period': round(slope, 4), 'first': pts[0], 'last': pts[-1]}}
    nums = []
    for a in answers:
        v = a.get('value')
        if not isinstance(v, (int, float)): raise ValueError('step result is not a single number')
        nums.append(v)
    if kind in ('share', 'count_and_share'):
        share = round(nums[1] / nums[0], 4) if nums[0] else None
        return {'status': 'answered', 'answer_type': 'value', 'returns': 'value',
                'value': {'share': share} if kind == 'share' else {'count': nums[1], 'share': share, 'of': nums[0]}}
    if kind == 'difference':
        return {'status': 'answered', 'answer_type': 'value', 'returns': 'value',
                'value': {'difference': nums[1] - nums[0], 'percent_change': round((nums[1] - nums[0]) / nums[0], 4) if nums[0] else None}}
    if kind == 'ratio':
        return {'status': 'answered', 'answer_type': 'value', 'returns': 'value', 'value': {'ratio': round(nums[0] / nums[1], 4) if nums[1] else None}}
    raise ValueError(f'unknown combine {kind}')


def execute_steps(ctx, value):
    """Each step is a canonical sub-question the grammar reads; executed exactly, then combined. -> (answer, provenance, steps)"""
    from tools.training.question_factory import resolve_request
    answers, tables, queries, done = [], set(), [], []
    for step in value['steps']:
        request = resolve_request(ctx.catalog, step)
        if request is None: raise ValueError(f'step not read by the grammar: {step}')
        contract = compile_contract(ctx, step, request)
        answers.append(execute_contract(ctx, step, request, contract))
        p = provenance(contract); tables.update(p['tables']); queries.append(p['query'])
        done.append({'question': step, 'request': request, 'answer_key': answer_key(answers[-1])})
    out = combine(value['combine'], answers, value)
    return out, {'tables': sorted(tables), 'columns': [], 'query': None, 'queries': queries, 'request_id': signature(value)}, done


def s1_verify(ctx, item, answer):
    """System One re-reads the patient's passages for a chart yes/no item; the label stands only when it agrees.
    -> (verified_by, detail) or raises ValueError('disputed ...')."""
    from clinical_core.agere.clinical import system_one
    v = item['verify']
    import sqlite3
    with sqlite3.connect(f'file:{ctx.catalog.target}?mode=ro', uri=True) as db:
        passages = [{'id': r[0], 'text': r[1], 'date': (r[2] or ctx.as_of)[:10]} for r in db.execute(
            'SELECT record_id, evidence_text, event_date FROM note_fact WHERE patient_id=? AND evidence_text IS NOT NULL '
            'ORDER BY (fact_type=? AND value=?) DESC, event_date DESC LIMIT 12', (v['patient_id'], v.get('fact_type'), v.get('value')))]
    if not passages: return 'exact_sql', {'reason': 'no passages to re-read'}
    out = system_one.decide(f"Do the notes for this patient document {v['fact']}?", system_one.CHART_ASSERTION_OPTIONS, passages,
                            as_of=ctx.as_of, task_key='chart_assertion', ledger=ctx.s1_ledger, allow_live=True)
    exact = 'yes' if (answer.get('value') or 0) > 0 else 'no'
    said = out.get('label')
    detail = {'label': said, 'decided_by': out.get('decided_by'), 'cost_usd': out.get('cost_usd'), 'exact': exact}
    if said == exact or (exact == 'no' and said in ('unknown', 'no')):
        return 'system_one', detail
    raise ValueError(f'disputed by System One: exact {exact}, System One {said}')


# ---------------------------------------------------------------------------------------------------- provenance
FIELD = re.compile(r'^[a-z_]+\.[a-z_0-9.]+$')


def provenance(contract):
    """Source tables and columns the compiled request reads, plus the exact SQL."""
    tables, columns, queries = set(), set(), []

    def visit(v):
        if isinstance(v, dict):
            for k, x in v.items():
                if k == 'table' and isinstance(x, str): tables.add(x)
                elif k in ('field', 'left', 'right') and isinstance(x, str) and FIELD.match(x):
                    columns.add(x); tables.add(x.split('.')[0])
                elif k in ('query', 'membership_query') and isinstance(x, str): queries.append(x)
                else: visit(x)
        elif isinstance(v, list):
            for x in v: visit(x)
    visit({k: contract.get(k) for k in ('request', 'program', 'analysis', 'nodes', 'query')})
    for q in queries:
        tables.update(t for t in re.findall(r'\b(?:FROM|JOIN)\s+"?([A-Za-z_][A-Za-z_0-9]*)"?', q) if not t.startswith(('concept_', 'age_')))
    return {'tables': sorted(t for t in tables if t not in ('result', 'population', 'selected_groups')), 'columns': sorted(columns),
            'query': contract.get('query'), 'request_id': contract.get('id')}


def answer_key(answer):
    """Comparable identity of an answer (value / rows / membership), for agreement checks."""
    from clinical_core.agere.clinical import bottled
    if 'value' in answer: return json.dumps(answer['value'])
    if 'patient_ids_sha' in answer: return answer['patient_ids_sha'] + str(answer.get('count'))
    if 'rows' in answer: return bottled.signature([list(r.values()) if isinstance(r, dict) else r for r in answer['rows']])
    if 'count' in answer: return str(answer['count'])
    return signature(answer)


# ------------------------------------------------------------------------------------------------------- one item
def label_item(ctx, item):
    started = time.perf_counter()
    q = item['canonical']
    base = {'schema': SCHEMA, 'labeler': VERSION, 'id': item['id'], 'family': item['family'], 'form': item['form'],
            'template_family': item['template_family'], 'canonical': q, 'request_source': item['request_source']}
    try:
        request = resolve(ctx, item)
        if request is None: return {**base, 'status': 'not_parsed', 'seconds': round(time.perf_counter() - started, 4)}
        if request['kind'] == 'engine':
            answer, prov = execute_engine(ctx, request['value'])
            contract_kind = 'engine'
        elif request['kind'] == 'steps':
            answer, prov, steps = execute_steps(ctx, request['value'])
            base['steps'] = steps
            contract_kind = 'steps'
        elif request['kind'] == 'cohort_family_slots':
            answer, sql, spec = execute_slots(ctx, request['value'])
            prov = {'tables': sorted(set(re.findall(r'\b(?:FROM|JOIN)\s+"?([a-z_][a-z_0-9]*)"?', sql))), 'columns': [], 'query': sql,
                    'request_id': signature(spec['parameters'])}
            contract_kind = 'cohort_family'
        else:
            try:
                contract = compile_contract(ctx, q, request)
            except Exception as exc:  # noqa: BLE001 - any compiler rejection drops the item with its reason
                return {**base, 'status': 'compile_error', 'reason': f'{type(exc).__name__}: {exc}'[:300],
                        'seconds': round(time.perf_counter() - started, 4)}
            answer = execute_contract(ctx, q, request, contract)
            prov = provenance(contract)
            contract_kind = contract.get('kind') or 'request'
            if item['request_source'] == 'typed' and ctx.grammar_check:
                agree = grammar_agreement(ctx, q, contract, answer)
                if agree is False:
                    return {**base, 'status': 'ambiguous_reading', 'reason': 'the free parser reads this wording to a different answer',
                            'seconds': round(time.perf_counter() - started, 4)}
                base['grammar_reads'] = agree is not None
        base['verified_by'] = 'exact_sql' if request['kind'] in ('request', 'program', 'analysis', 'cohort_family_slots') else 'exact_' + request['kind']
        if item.get('verify') and getattr(ctx, 's1_verify', False):
            base['verified_by'], base['verification'] = s1_verify(ctx, item, answer)
    except Exception as exc:  # noqa: BLE001 - execution failures are recorded, never fatal to the run
        status = 'disputed' if str(exc).startswith('disputed') else 'exec_error'
        return {**base, 'status': status, 'reason': f'{type(exc).__name__}: {exc}'[:300],
                'seconds': round(time.perf_counter() - started, 4)}
    if item['request_source'] == 'grammar': base['grammar_reads'] = True
    return {**base, 'status': 'ok', 'request': request, 'contract_kind': contract_kind, 'answer': answer,
            'answer_key': answer_key(answer), 'provenance': prov, 'paraphrases': item['paraphrases'], 'chain': item.get('chain'),
            'slots': item.get('slots'), 'expected_engine': item.get('expected_engine'), 'router_label': item.get('router_label'),
            'definition_dependent': item.get('definition_dependent', False),
            'engines': engines_of(request), 'source_identity': ctx.catalog.identity.get('source_digest'),
            'seconds': round(time.perf_counter() - started, 4)}


def engines_of(request):
    if request['kind'] == 'cohort_family_slots': return ['cohort_family:' + request['value']['family']]
    if request['kind'] == 'engine': return [request['value']['operation']]
    if request['kind'] == 'steps': return []
    found = []

    def visit(v):
        if isinstance(v, dict):
            if 'operation' in v and isinstance(v['operation'], str) and 'parameters' in v: found.append(v['operation'])
            for x in v.values(): visit(x)
        elif isinstance(v, list):
            for x in v: visit(x)
    visit(request['value'])
    return sorted(set(found))


def grammar_agreement(ctx, question, contract, answer):
    """None: the free parser does not read the wording; True/False: it does, with the same / a different answer."""
    from tools.training.question_factory import resolve_request
    theirs = resolve_request(ctx.catalog, question)
    if theirs is None: return None
    try:
        other = compile_contract(ctx, question, theirs)
        if other.get('query') == contract.get('query'): return True
        return answer_key(execute_contract(ctx, question, theirs, other)) == answer_key(answer)
    except Exception:  # noqa: BLE001 - a parse that does not execute is not a competing reading
        return None


# ------------------------------------------------------------------------------------------------ units and runs
def units(questions, unit=UNIT, limit=None):
    out, total = [], 0
    for shard in sorted(Path(questions).glob('questions-*.jsonl')):
        with shard.open() as f: n = sum(1 for _ in f)
        for start in range(0, n, unit):
            end = min(n, start + unit)
            if limit is not None:
                if total >= limit: return out
                end = min(end, start + limit - total)
            out.append((str(shard), start, end)); total += end - start
    return out


_CTX = {}


def _context(stack, full_stack, grammar_check, s1_verify=False):
    key = (stack, full_stack, grammar_check, s1_verify)
    if key not in _CTX: _CTX[key] = Context(stack, full_stack, grammar_check, s1_verify)
    return _CTX[key]


def label_unit(shard, start, end, out, stack, full_stack=False, grammar_check=True, s1_verify=False):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    name = f'{Path(shard).stem.replace("questions", "labels")}-{start:07d}'
    done = out / f'{name}.done'
    if done.exists(): return json.loads(done.read_text())
    ctx = _context(stack, full_stack, grammar_check, s1_verify)
    path, rejects = out / f'{name}.jsonl', out / f'{name.replace("labels-", "rejects-", 1)}.jsonl'
    finished = set()
    for p in (path, rejects):  # resume inside a partially written unit
        if p.exists():
            for line in p.read_text().splitlines():
                try: finished.add(json.loads(line)['id'])
                except (ValueError, KeyError): pass
    stats = {'unit': name, 'items': 0, 'ok': 0, 'status': {}, 'seconds': 0.0}
    with path.open('a') as good, rejects.open('a') as bad, Path(shard).open() as src:
        for i, line in enumerate(src):
            if i < start: continue
            if i >= end: break
            item = json.loads(line)
            if item['id'] in finished: continue
            row = label_item(ctx, item)
            (good if row['status'] == 'ok' else bad).write(json.dumps(row, separators=(',', ':'), default=str) + '\n')
            stats['items'] += 1; stats['ok'] += row['status'] == 'ok'; stats['seconds'] += row['seconds']
            stats['status'][row['status']] = stats['status'].get(row['status'], 0) + 1
    done.write_text(json.dumps(stats))
    return stats


def run(questions, out, stack=None, workers=4, limit=None, full_stack=False, grammar_check=True, unit=UNIT, s1_verify=False):
    from tools.training.question_factory import active_stack
    stack = str(stack or active_stack())
    todo = units(questions, unit, limit)
    started, results = time.time(), []
    if workers <= 1:
        results = [label_unit(s, a, b, out, stack, full_stack, grammar_check, s1_verify) for s, a, b in todo]
    else:
        with ProcessPoolExecutor(max_workers=workers, mp_context=get_context('spawn')) as pool:
            futures = [pool.submit(label_unit, s, a, b, out, stack, full_stack, grammar_check, s1_verify) for s, a, b in todo]
            for f in as_completed(futures):
                try: results.append(f.result())
                except Exception:  # noqa: BLE001 - one failed unit is retried on the next (resumable) run
                    traceback.print_exc()
    return summarize(out, time.time() - started, workers)


def labeled_files(d):
    return [p for p in Path(d).glob('labels-*.jsonl') if not p.name.endswith('.rejects.jsonl')]


def reject_files(d):
    return [*Path(d).glob('rejects-*.jsonl'), *Path(d).glob('*.rejects.jsonl')]


def summarize(out, wall=None, workers=None):
    """Counts, label success rate, family diversity and a 1M-item wall-time estimate from the labeled units."""
    out = Path(out)
    status, fam_ok, fam_all, templates, engines, seconds, n = {}, {}, {}, set(), {}, 0.0, 0
    for p in sorted(labeled_files(out)) + sorted(reject_files(out)):
        for line in p.read_text().splitlines():
            r = json.loads(line); n += 1; seconds += r.get('seconds', 0)
            status[r['status']] = status.get(r['status'], 0) + 1
            fam_all[r['family']] = fam_all.get(r['family'], 0) + 1
            if r['status'] == 'ok':
                fam_ok[r['family']] = fam_ok.get(r['family'], 0) + 1; templates.add(r['template_family'])
                for e in r.get('engines') or ['sql']: engines[e] = engines.get(e, 0) + 1
    ok = status.get('ok', 0)
    per_item = seconds / max(1, n)
    report = {'labeler': VERSION, 'items': n, 'labeled': ok, 'success_rate': round(ok / max(1, n), 4), 'status': status,
              'families': {f: {'items': fam_all[f], 'labeled': fam_ok.get(f, 0), 'rate': round(fam_ok.get(f, 0) / fam_all[f], 3)}
                           for f in sorted(fam_all)},
              'family_count': len(fam_ok), 'template_families_labeled': len(templates), 'engines': engines,
              'cpu_seconds_per_item': round(per_item, 4)}
    if wall: report.update(wall_seconds=round(wall, 1), workers=workers)
    w = workers or 6
    report['estimate_1M'] = {'workers': w, 'hours': round(1_000_000 * per_item / w / 3600, 1),
                             'basis': 'mean per-item labeling seconds on this run, divided across workers (I/O contention not modelled)'}
    (out / 'summary.json').write_text(json.dumps(report, indent=1))
    return report


def main(argv=None):
    from tools.training.question_factory import DEFAULT_OUT
    ap = argparse.ArgumentParser()
    ap.add_argument('--questions', default=str(DEFAULT_OUT / 'questions')); ap.add_argument('--out', default=str(DEFAULT_OUT / 'labels'))
    ap.add_argument('--stack', default=None); ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--limit', type=int, default=None); ap.add_argument('--unit', type=int, default=UNIT)
    ap.add_argument('--full-stack', action='store_true', help='load the real Stack (embedding index) instead of LightStack')
    ap.add_argument('--no-grammar-check', action='store_true')
    ap.add_argument('--s1-verify', action='store_true', help='System One re-reads chart yes/no items (live Jev/Perplexity; cents)')
    a = ap.parse_args(argv)
    if a.s1_verify:
        from run_integrated_stack import keys
        keys()
    print(json.dumps(run(a.questions, a.out, a.stack, a.workers, a.limit, a.full_stack, not a.no_grammar_check, a.unit, a.s1_verify), indent=1))


if __name__ == '__main__':
    main()
