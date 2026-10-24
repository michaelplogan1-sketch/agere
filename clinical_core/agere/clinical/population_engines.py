"""Engine forms: population and insight questions answered EXACTLY, with no model, by governed definitions and engines (2026-10-23).

A typed request (request_plan) states one query. Many population questions are not one query: "who should we prioritize", "what is driving
PMPM", "where are our biggest care gaps" ask for a handful of facts that belong together -- the gap counts and how they overlap, the spend
split that explains a PMPM, the practices ranked on a measure. The form recognises the SHAPE of such a question (a family), binds every
content word of the question to a recognised phrase or a family word (anything left over declines the form: nothing is silently
dropped), computes each fact from the decision-context definitions in population_sql, and states every fact with its numerator and
denominator. The answer carries the facts as data (answer['insight']['facts']), a plain-language statement of each (answer['narrative']),
the tables behind them, and a read-back of how the question was understood.

Families (insight_families.py): practice_gap, prioritize, care_gaps, spend_drivers, spend_concentration, yoy_trend, denial_drivers,
association, segment_compare, frequent_users; plus pdc_share (adherence through the governed default_definition engine).
"""
import re
import sqlite3
import time

from . import decision_context, population_sql
from .request_plan import PlanError, fingerprint

VERSION = 'population-engines/1'
REVIEW = 'fully_consumed_engine_form'
FAMILIES = []  # [(name, module-level class)] registered by insight_families


def family(cls):
    FAMILIES.append(cls)
    return cls


class Declined(Exception):
    """A family recognised the shape but the question carries something it does not compute."""


def interpret(catalog, question, *, population=None):
    """-> (contract, interpretation) for the first family that recognises the question and accounts for all of it, else None."""
    from . import insight_families  # noqa: F401 - registers the families
    if population is not None: return None  # a saved cohort keeps its own request language
    text = ' '.join(question.strip().split())
    for cls in FAMILIES:
        try:
            spec = cls.parse(text, catalog)
        except Declined:
            continue
        if not spec: continue
        from .general_analysis import uncovered
        left = uncovered(text, [*spec['quotes'], *cls.WORDS])
        if left: continue  # a word of the question no recognised phrase accounts for: this form does not answer it
        import json as _json
        numbers = set(re.findall(r'(?<![\w.])\d[\d,]*(?:\.\d+)?', text))
        carried = set(re.findall(r'\d[\d,]*(?:\.\d+)?', _json.dumps(spec['params'], default=str)))
        have = {float(c.replace(',', '')) for c in carried}
        if any(float(n.replace(',', '')) not in have for n in numbers): continue  # a number the form did not compile is a requirement dropped
        body = {'version': VERSION, 'kind': 'engine_form', 'family': cls.NAME, 'params': spec['params'], 'original_question': question,
                'source_identity': catalog.identity, 'quotes': spec['quotes']}
        contract = {**body, 'id': fingerprint(body),
                    # the shape the shared planner reads on every contract
                    'request': {'source': {'table': 'patient', 'quote': ''}, 'select': [], 'where': None, 'group_by': [], 'order_by': [],
                                'limit': None, 'unresolved': [], 'workflow': []},
                    'query': '', 'returns': 'table', 'membership_query': None, 'bindings': []}
        return contract, {'interpreter': 'population_engine', 'review': REVIEW, 'execution_accepted': True, 'attempts': [],
                          'family': cls.NAME}
    return None


def graph_for(contract):
    return {'interpretation': contract['original_question'][:2000], 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'population_engine', 'operation': 'population_engine', 'inputs': [],
                       'parameters': {'contract_id': contract['id'], 'family': contract['family']},
                       'purpose': 'Governed population computation of the original question'}]}


def execute(stack, question, graph, receipt, *, population=None, parent=None, started=None, economics=None):
    started = time.perf_counter() if started is None else started
    from ..deliverables.answer_context import source_identity
    from .request_plan import Catalog
    execution_snapshot = source_identity(stack.path)
    contract = receipt['request_contract']
    cls = next(c for c in FAMILIES if c.NAME == contract['family'])
    current = Catalog.load(stack.path)
    if current.identity != contract['source_identity']:
        raise PlanError('Warehouse changed during planning; recompute against the new snapshot')
    ctx = population_sql.Ctx(stack.path, current.target, current.identity['as_of'], question)
    try:
        result = cls.run(ctx, contract['params'])
    except (sqlite3.Error, ValueError, KeyError) as exc:  # a relation or column this warehouse does not carry: say so, never a partial number
        raise PlanError(f"{cls.NAME} cannot be computed from the received data ({type(exc).__name__}: {str(exc)[:160]}); no reduced question was executed") from exc
    answer = assemble(cls, ctx, contract, result, question)
    out = {'schema': VERSION, 'synthetic': True, 'question': question, 'route': 'shared_planner', 'graph': graph,
           'results': [{'node': graph['nodes'][0], 'result': {'queries': ctx.queries, 'row_count': len(result.get('table') or [])}}],
           'answer': answer, 'request_contract': contract, 'planner_receipt': receipt,
           'new_paid_api_calls': receipt['new_paid_api_calls'], 'planning_economics': economics(receipt) if economics else None,
           'elapsed_seconds': time.perf_counter() - started, 'clinical_execution_authorized': False, 'execution_snapshot': execution_snapshot,
           'computation_complete': True, 'workflow': []}
    if Catalog.load(stack.path).identity != contract['source_identity'] or source_identity(stack.path) != execution_snapshot:
        raise PlanError('Warehouse changed during execution; no unstable answer was accepted')
    return out


def assemble(cls, ctx, contract, r, question):
    facts = r['facts']
    ids = r.get('patient_ids') or []
    table = r.get('table')
    lines = [f['text'] for f in facts]
    limitations = ['Results describe received records. No matching record does not establish absent care.',
                   'Computed from the governed definitions of the decision context; every fact below carries its numerator and denominator.',
                   *r.get('notes', [])]
    answer = {'status': 'answered', 'answer_type': 'table' if table else 'insight', 'headline': r['headline'],
              'narrative': lines, 'insight': {'family': cls.NAME, 'facts': facts, 'tables': r.get('tables', {})},
              'missing': [], 'limitations': list(dict.fromkeys(limitations)), 'patient_ids': ids,
              'actionable_cohort': bool(ids) and bool(r.get('cohort_role')), 'unknown_patient_ids': [], 'unknown_count': 0,
              'query_evidence': {'queries': ctx.queries[:12], 'source_identity': contract['source_identity']},
              'request_id': contract['id'], 'data_absence_established': False,
              'scope': 'Observed warehouse query; clinical execution is not authorized.',
              'read_back': r['read_back']}
    if table is not None: answer['table'] = table
    if ids: answer['count'] = len(ids)
    if r.get('count') is not None: answer['count'] = r['count']
    if r.get('value') is not None: answer['value'] = r['value']
    if r.get('cohort_role'): answer['membership_role'] = r['cohort_role']
    return answer


def fact(fid, text, value=None, numerator=None, denominator=None, unit=None, **extra):
    out = {'id': fid, 'text': text, 'value': value, 'unit': unit}
    if numerator is not None: out.update(numerator=numerator, denominator=denominator)
    out.update(extra)
    return out
