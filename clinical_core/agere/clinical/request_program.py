"""Compose existing checked analyses as a source-bound request dependency graph.

This is orchestration, not another query engine. Each node goes through the
existing request/analysis compiler, read-only executor and answer workspace.
Independent outputs keep separate evidence and work authority. Dependencies are
compiled SQL relations, never model answers interpolated as data or instructions.
"""
import copy
import re
import sqlite3
import time

from . import general_analysis as G, request_plan as R

VERSION = 'warehouse-request-program/1'
_RESULT = re.compile(r'\{\{\s*result:([a-z][a-z0-9_]{0,40})\s*\}\}')

SYSTEM = '''
COMPOSED REQUESTS:
Use {"program":{"nodes":[...],"unresolved":[]}} when a request needs independent
outputs, different result grains, or an analysis consuming another analysis.
This composes the SAME typed requests and general analyses described above.
Node: {"id":"stable_snake_name","quote":"literal clause from the original request",
"inputs":[],"analysis":{...}} OR replace analysis with "request":{typed request}.
The node's question is its quote. Ordered literal fragments may be a quote array
to include shared population, time or output requirements without rewriting them.
Each node must preserve all applicable filters, definitions and dates. Do not
drop a shared qualification when splitting outputs. All original requirements
must appear in node quotes or program.unresolved:[{"quote":"...","reason":"..."}].
Use only actual received schema. A node may consume earlier computed results as
FROM {{result:node_id}} AS alias in analysis step SQL; list exactly those IDs in
inputs. The compiler embeds their checked SQL as read-only relations. Do not
invent a table, join key, column or business clock. Cycles are invalid.
Keep creating a deliverable, synthetic work and monitoring in the producing
node's workflow, bound to that node's exact outputs. Never union patient sets
from unrelated outputs to create work. A deliverable combining several analyses
can consume their result relations in a final analysis node.
When an output or command cannot be implemented, retain its node with
unresolved:["specific missing evidence, capability or integration"] instead of
analysis/request. An unavailable command must not erase independent results.
Do not use unresolved to disguise a compiler error or an unattempted analysis.
Choose the ordinary single request/analysis for a single supported result.
'''

AUDIT_SYSTEM = G.AUDIT_SYSTEM + '''
For a request program, apply the above source/meaning checks to each node, and
use the following response schema instead of the single-analysis verdict.
Review the original request against this compiled request program.
Return JSON {"equivalent":true|false,"issues":["decomposition/scope omissions"],
"nodes":[{"id":"exact node ID","equivalent":true|false,"issues":["actual mismatch"]}]}.
The top-level verdict checks whether the entire request, including shared
population/date restrictions, dependencies, commands and deliverables, is
faithfully represented. Each node verdict checks its actual SQL, source meanings,
preview, requirements and workflow against its clause AND the full request.
An explicit unavailable capability/evidence requirement represents that part;
it does not invalidate independent observations. It cannot establish a filtered
population or authorize action. Mark an incorrectly computed node false without
rejecting unrelated nodes. Check that dependencies use the intended result and
preserve the original entity grain. Unknown is not false or zero. Do not treat
valid syntax, literal quotation or sample rows as proof of semantic correctness.
'''


def _clause(question, quote):
    fragments = quote if isinstance(quote, list) else [quote]
    if not fragments or len(fragments) > 12:
        raise R.PlanError('A node needs ordered literal request fragments')
    position = 0
    for fragment in fragments:
        if not isinstance(fragment, str) or not fragment.strip():
            raise R.PlanError('A node needs nonempty literal request fragments')
        at = question.find(fragment, position)
        if at < 0:
            raise R.PlanError('Node wording must be ordered literal text from the original request')
        position = at + len(fragment)
    return R.quote_text(quote)


def prepare(catalog, question, spec, *, population=None):
    if not isinstance(spec, dict) or set(spec) != {'nodes', 'unresolved'}:
        raise R.PlanError('program needs nodes and unresolved')
    wire = copy.deepcopy(spec)
    nodes, unresolved = wire['nodes'], wire['unresolved']
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= G.MAX_STEPS:
        raise R.PlanError(f'program needs 1..{G.MAX_STEPS} nodes')
    if not isinstance(unresolved, list):
        raise R.PlanError('program unresolved must be a list')
    for item in unresolved:
        if not isinstance(item, dict) or set(item) != {'quote', 'reason'} or not isinstance(item['reason'], str) or not item['reason'].strip():
            raise R.PlanError('An unresolved requirement needs its quote and a specific reason')
        _clause(question, item['quote'])
    definitions = {}
    for node in nodes:
        if not isinstance(node, dict) or set(node) - {'id', 'quote', 'inputs', 'analysis', 'request', 'unresolved'}:
            raise R.PlanError('Node keys: id, quote, inputs and one of analysis/request/unresolved')
        ident = node.get('id')
        if not isinstance(ident, str) or not G._NAME.fullmatch(ident) or ident in definitions:
            raise R.PlanError('Program node IDs must be unique snake_case names')
        clause = _clause(question, node.get('quote'))
        if len(set(node) & {'analysis', 'request', 'unresolved'}) != 1:
            raise R.PlanError('Each node needs exactly one analysis, request or unresolved disposition')
        inputs = node.get('inputs')
        if not isinstance(inputs, list) or any(not isinstance(i, str) for i in inputs) or len(set(inputs)) != len(inputs):
            raise R.PlanError('Each node needs distinct input node IDs')
        if 'unresolved' in node and (not isinstance(node['unresolved'], list) or not node['unresolved'] or
                                    any(not isinstance(s, str) or not s.strip() for s in node['unresolved'])):
            raise R.PlanError('An unavailable node needs specific reasons')
        definitions[ident] = (node, clause)
    missing = G.uncovered(question, [R.quote_text(n['quote']) for n in nodes] + [R.quote_text(u['quote']) for u in unresolved])
    if missing:
        raise R.PlanError('Original requirements missing from program: ' + ', '.join(missing))
    if any(i not in definitions for n in nodes for i in n['inputs']):
        raise R.PlanError('Program references an unknown input node')
    compiled, remaining = {}, list(definitions)
    while remaining:
        ready = [ident for ident in remaining if all(i in compiled for i in definitions[ident][0]['inputs'])]
        if not ready:
            raise R.PlanError('Request program has a dependency cycle')
        for ident in ready:
            node, clause = definitions[ident]
            entry = {'id': ident, 'question': clause, 'quote': node['quote'], 'inputs': node['inputs'],
                     'contract': None, 'unresolved': node.get('unresolved', [])}
            if 'unresolved' not in node:
                try:
                    if any(compiled[i]['contract'] is None for i in node['inputs']):
                        raise R.PlanError('A required input has no executable result')
                    if any((compiled[i]['contract'].get('request') or {}).get('analyses') for i in node['inputs']):
                        raise R.PlanError('Registered engine output is not yet exposed as a result relation; its cohort SQL is not its computed result')
                    if 'analysis' in node:
                        analysis = copy.deepcopy(node['analysis'])
                        refs = set()
                        for step in analysis.get('steps', []):
                            sql = step.get('sql', '')
                            refs.update(_RESULT.findall(sql))
                            if any(i not in node['inputs'] for i in _RESULT.findall(sql)):
                                raise R.PlanError('Result SQL must reference declared input nodes')
                            step['sql'] = _RESULT.sub(lambda m: '(' + compiled[m[1]]['contract']['query'] + ')', sql)
                        if refs != set(node['inputs']):
                            raise R.PlanError('Every declared input must participate through a result relation')
                        entry['contract'] = G.prepare(catalog, clause, analysis, population=population)
                    else:
                        if node['inputs']:
                            raise R.PlanError('Use a general analysis to consume result relations')
                        entry['contract'] = R.compile_request(catalog, clause, node['request'], population=population)
                except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                    # Retain independent outputs and make this engineering failure
                    # visible. The review sees it and cannot confer work authority.
                    entry['unresolved'] = ['Compilation failed: ' + str(exc)]
            compiled[ident] = entry
            remaining.remove(ident)
    body = {'kind': 'request_program', 'version': VERSION, 'original_question': question,
            'program': wire, 'nodes': list(compiled.values()), 'output_order': [n['id'] for n in nodes], 'unresolved': unresolved,
            'population': None if population is None else sorted(set(population)),
            'source_identity': catalog.identity}
    return {**body, 'id': R.fingerprint(body)}


def sealed(contract):
    if contract.get('id') != R.fingerprint({k: v for k, v in contract.items() if k != 'id'}):
        raise R.PlanError('Request program differs from its sealed definition')
    return contract


def preview(catalog, contract, population=None):
    shown = []
    for node in sealed(contract)['nodes']:
        c = node['contract']
        result = {'id': node['id'], 'question': node['question'], 'inputs': node['inputs'], 'unresolved': node['unresolved']}
        if c:
            result.update(contract=c)
            try:
                if c.get('kind') == 'general_analysis':
                    _, result['preview'] = G.preview(catalog, c, population)
                # Typed engine results are reviewed via their registered contracts,
                # not reimplemented as a sample query in this orchestration layer.
            except (ValueError, KeyError) as exc:
                result['preview_error'] = str(exc)
        shown.append(result)
    return shown


def validate_audit(audit, contract):
    def verdict(value, keys):
        return (isinstance(value, dict) and set(value) == keys and type(value.get('equivalent')) is bool and
                isinstance(value.get('issues'), list) and all(isinstance(i, str) for i in value['issues']))
    if not verdict(audit, {'equivalent', 'issues', 'nodes'}) or not isinstance(audit['nodes'], list):
        raise R.PlanError('Program review needs overall and per-node verdicts')
    ids = [n['id'] for n in contract['nodes']]
    if (any(not verdict(n, {'id', 'equivalent', 'issues'}) or not isinstance(n['id'], str) for n in audit['nodes']) or
            sorted(n['id'] for n in audit['nodes']) != sorted(ids)):
        raise R.PlanError('Program review must name every node exactly once')
    return audit


def cacheable(contract, interpretation):
    """Do not make a partially compiled or rejected program a permanent cache hit."""
    if contract['unresolved']:return False
    if interpretation.get('review') == 'fully_consumed_grammar_program' and interpretation.get('execution_accepted'):
        if any(n['inputs'] or not n['contract'] or n['contract'].get('kind') == 'general_analysis' for n in contract['nodes']):
            return False
    else:
        audit = validate_audit(interpretation['semantic_review'], contract)
        if not audit['equivalent'] or audit['issues']:return False
        if any(not n['equivalent'] or n['issues'] for n in audit['nodes']):return False
    for node in contract['nodes']:
        child = node['contract']
        if not child or node['unresolved']:
            return False
        if (child.get('analysis') or child.get('request'))['unresolved']:
            return False
    return True


def graph_for(contract):
    return {'schema': VERSION, 'question': contract['original_question'], 'nodes': [
        {'id': n['id'], 'operation': 'analysis' if n['contract'] else 'unavailable',
         'inputs': n['inputs'], 'purpose': n['question'],
         'parameters': {'request_id': n['contract']['id']} if n['contract'] else {}}
        for n in contract['nodes']]}


def execute(stack, question, graph, receipt, *, population=None, parent=None, started=None):
    from . import shared_planner as S
    from ..deliverables.answer_context import source_identity
    started = started or time.perf_counter()
    contract = sealed(receipt['request_contract'])
    if contract['population'] != (None if population is None else sorted(set(population))):
        raise R.PlanError('Execution scope differs from the sealed request program')
    snapshot = source_identity(stack.path)
    if R.Catalog.load(stack.path).identity != contract['source_identity']:
        raise R.PlanError('Warehouse changed during planning; replan the original request')
    interpretation = receipt['interpretation']
    audit = interpretation.get('semantic_review')
    deterministic = False
    if interpretation.get('review') == 'fully_consumed_grammar_program' and interpretation.get('execution_accepted'):
        from . import request_language
        # Reparse the literal original request. A flag on a model/injected
        # contract cannot grant deterministic authority to another program.
        regenerated = request_language.parse_program(R.Catalog.load(stack.path),question,bound=population is not None)
        deterministic = regenerated is not None and R.fingerprint(regenerated) == R.fingerprint(contract['program'])
    if audit:
        validate_audit(audit, contract)
    reviews = {n['id']: n for n in (audit or {}).get('nodes', [])}
    parts, sections, computations = [], [], {}
    overall_accepted = deterministic or bool(interpretation.get('execution_accepted') and audit and audit['equivalent']
                            and not audit['issues'] and not contract['unresolved'])
    for node in contract['nodes']:
        problems = list(node['unresolved'])
        problems += ['Required output is incomplete: ' + i for i in node['inputs'] if not computations[i]]
        result = None
        if node['contract'] and not problems:
            review = reviews.get(node['id'], {})
            child_receipt = {**receipt, 'request_contract': node['contract'], 'steps': [],
                             'new_paid_api_calls': 0, 'known_api_estimate_usd': 0, 'api_cost_known': True,
                             'interpretation': {**interpretation, 'attempts': [], 'semantic_review': None if deterministic else review,
                                 'review':'fully_consumed_grammar' if deterministic else interpretation.get('review'),
                                 'execution_accepted': deterministic or (overall_accepted and review.get('equivalent') is True and not review.get('issues'))},
                             'program_origin': {'id': contract['id'], 'node_id': node['id'], 'original_question': question,
                                 'feedback_identity': receipt.get('feedback_identity'),
                                 'review': {'equivalent': (audit or {}).get('equivalent'), 'issues': (audit or {}).get('issues')},
                                 'input_request_ids': {i: next(n['contract']['id'] for n in contract['nodes'] if n['id'] == i)
                                                       for i in node['inputs']}}}
            from .learning import feedback_identity
            child_receipt['feedback_identity'] = feedback_identity(stack.path, node['question'])
            child_graph = G.graph_for(node['contract']) if node['contract'].get('kind') == 'general_analysis' else S.graph_for(node['contract'])
            try:
                result = (G.execute(stack, node['question'], child_graph, child_receipt, population=population,
                                    parent=parent, economics=S.economics) if node['contract'].get('kind') == 'general_analysis'
                          else S.execute_contract(stack, node['question'], child_graph, child_receipt,
                                                  population=population, parent=parent))
            except (ValueError, KeyError, sqlite3.Error, RuntimeError) as exc:
                problems.append('Execution failed: ' + str(exc))
        answer = result['answer'] if result else {
            'status': 'partial', 'answer_type': 'unsupported', 'headline': node['question'],
            'missing': problems, 'limitations': [], 'patient_ids': [], 'actionable_cohort': False}
        # A complete read-only candidate can feed another candidate calculation.
        # Its lack of review still blocks action authority throughout the graph.
        computations[node['id']] = bool(result and result.get('computation_complete', answer['status'] == 'answered'))
        sections.append({'id': node['id'], 'title': node['question'], 'answer': answer, 'inputs': node['inputs']})
        parts.append({'id': node['id'], 'question': node['question'], 'result': result})
    # No context or action is saved before the entire read phase completes.
    if source_identity(stack.path) != snapshot or R.Catalog.load(stack.path).identity != contract['source_identity']:
        raise R.PlanError('Warehouse changed during request execution; no mixed-snapshot result accepted')
    from .learning import correction_context, feedback_identity
    if correction_context(stack.path, question) and receipt.get('feedback_identity') != feedback_identity(stack.path, question):
        raise R.PlanError('Relevant request feedback changed during execution; recheck the original request')
    order = {ident: i for i, ident in enumerate(contract['output_order'])}
    sections.sort(key=lambda s: order[s['id']])
    parts.sort(key=lambda p: order[p['id']])
    missing = [f"{R.quote_text(u['quote'])}: {u['reason']}" for u in contract['unresolved']]
    missing += [s['title'] + ': ' + m for s in sections for m in s['answer'].get('missing', [])]
    if not overall_accepted:
        missing += (audit or {}).get('issues') or ['Request decomposition has not been accepted by review.']
    complete = all(s['answer']['status'] == 'answered' for s in sections) and not missing
    # Follow-ups narrow the one patient set every patient-level output selected; differing sets are never unioned.
    selections = [s['answer'] for s in sections if
                  s['answer'].get('answer_type') == 'patient_set' or
                  s['answer'].get('membership_role') in ('selected', 'shared_selection') or
                  s['answer'].get('actionable_cohort') is True or
                  any(isinstance(r, dict) and (r.get('patient_id') or r.get('patient'))
                      for r in s['answer'].get('table') or [])]
    from ..deliverables.answer_context import patient_rows
    chosen = [{r['patient_id'] for r in patient_rows(a)} for a in selections]
    established = bool(chosen) and all(c == chosen[0] for c in chosen) and all(
        a.get('status') == 'answered' for a in selections)
    shared = sorted(chosen[0]) if established else []
    answer = {'status': 'answered' if complete else 'partial', 'answer_type': 'composite',
              'headline': f"{sum(s['answer']['status'] == 'answered' for s in sections)} of {len(sections)} requested outputs answered",
              'sections': sections, 'missing': missing, 'limitations': [], 'patient_ids': shared,
              'actionable_cohort': False, 'data_absence_established': False,
              'shared_selection_established': established}
    if established: answer['membership_role'] = 'shared_selection'
    row = scalar_row(sections, contract)
    if row and complete: answer['table'] = [row]  # independent single-number outputs read side by side: one row, one column per output
    child_calls = sum((p['result'] or {}).get('new_paid_api_calls', 0) for p in parts)
    economics = S.economics(receipt)
    if child_calls:
        economics.update(api_cost_known=False, new_api_estimate_usd=None)
    out = {'schema': VERSION, 'synthetic': True, 'route': 'shared_planner', 'question': question,
           'graph': graph, 'answer': answer, 'program_results': parts, 'results': [],
           'planner_receipt': receipt, 'request_contract': contract, 'execution_snapshot': snapshot,
           'new_paid_api_calls': receipt['new_paid_api_calls'] + child_calls, 'planning_economics': economics,
           'elapsed_seconds': time.perf_counter() - started, 'workflow': [],
           'clinical_execution_authorized': False}
    if parent:  # a program over a saved cohort ("how many of them are diabetic, and how many hypertensive") records its lineage like a single request
        out['cohort_transform'] = {'parent_context_id': parent['id'], 'input_patient_ids': sorted(population or []),
                                   'operations': ['Apply request program to exact saved membership'], 'request_id': contract['id']}
    return out


def scalar_row(sections, contract):
    """One summary row for scalar outputs and explicitly requested single-row rankings.

    Keep child sections and their evidence intact. Multi-row outputs never get
    joined by position or silently reduced to their first row.
    """
    if len(sections) < 2: return None
    nodes = {n['id']: n for n in contract['nodes']}
    row = {}
    for section in sections:
        answer = section['answer']
        if answer.get('status') != 'answered': return None
        request = (((nodes.get(section['id']) or {}).get('contract') or {}).get('request') or {})
        select = request.get('select') or [{}]
        if answer.get('answer_type') in ('value', 'count'):
            key = 'count' if answer['answer_type'] == 'count' else 'value'
            value = answer.get(key)
            if not isinstance(value, (int,float)) or isinstance(value,bool): return None
            values = {str(select[0].get('alias') or section['id']): value}
        elif (request.get('order_by') and (request.get('limit') or {}).get('value') == 1
              and request.get('group_by') and len(answer.get('table') or []) == 1):
            values = answer['table'][0]
            if not isinstance(values,dict) or not values: return None
        else:
            return None
        for name,value in values.items():
            row[name if name not in row else f"{name}_{section['id']}"] = value
    return row


def materialize(stack, result):
    """Save each output and execute only its own requested workflow.

    The aggregate never acquires a unioned patient population. Existing workspace
    validation, effect boundaries and idempotency apply to every child workflow.
    """
    from ..deliverables import answer_context as A, workflow as W
    parts = result['program_results']
    for part in parts:
        child = part['result']
        if child:
            part['action_context'] = A.capture(stack.path, part['question'], child,
                                              original_question=result['question'])
    steps, calls, cost, known = [], 0, 0, True
    for part in parts:
        child, context = part['result'], part.get('action_context')
        if context:
            section = next(s for s in result['answer']['sections'] if s['id'] == part['id'])
            section['action_context'] = context
        if child and context and child.get('workflow'):
            delivered = W.materialize(stack.path, context['id'], generation='frontier',
                                      allow_live=bool(getattr(stack, 'allow_live', False)))
            for step in delivered['steps']:
                steps.append({**step, 'index': len(steps), 'node_index': step['index'],
                              'node_id': part['id'], 'context_id': context['id']})
            calls += delivered['new_paid_api_calls']
            cost += delivered['known_api_estimate_usd']
            known = known and delivered['api_cost_known']
    result['workflow'] = steps
    result['new_paid_api_calls'] += calls
    economics = result['planning_economics']
    prior = economics.get('new_api_estimate_usd')
    economics.update(workflow_api_estimate_usd=cost if known else None,
                     workflow_known_api_estimate_usd=cost,
                     known_api_estimate_usd=economics.get('known_api_estimate_usd', 0) + cost,
                     new_api_estimate_usd=prior + cost if prior is not None and known else None)
    return result
