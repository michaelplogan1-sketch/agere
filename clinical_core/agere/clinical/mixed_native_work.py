"""Separate shared native program meaning from per-entity documentary meaning.

Only replayed left assembly and row-preserving direct SQL projections can carry
these field proofs through a mixed result. A new expression over a documentary
column is never made native by a matching value. Native fidelity is conditional
on an explicit, input-bound machine judgment of the program's field meaning.
"""
import collections
import copy
import json

from ...contracts import digest
from . import result_assembly as assembly

VERSION = 'mixed-native-work/1'


def decoded_json(value):
    if isinstance(value, str) and value[:1] in ('[', '{'):
        try: return decoded_json(json.loads(value))
        except ValueError: return value
    if isinstance(value, list): return [decoded_json(v) for v in value]
    if isinstance(value, dict): return {k: decoded_json(v) for k, v in value.items()}
    return value


def candidates(answer, calls, as_of):
    from .backstop_agent import native_program_candidate
    import sqlglot
    from sqlglot import exp

    by = {}
    for call in calls:
        result = call.get('result')
        if isinstance(result, dict) and result.get('result_view'):
            if result['result_view'] in by:
                return []
            by[result['result_view']] = call
    native = {view: c['result'] for view, c in by.items()}
    root = answer.get('table_from_tool')
    if root not in by:
        return []
    interpreted = set((answer.get('table_binding') or {}).get('interpreted_columns') or [])
    final_rows = answer.get('table')
    try:
        root_columns, rows = assembly._rows(native[root])
        if (not isinstance(final_rows,list) or len(final_rows)!=len(rows) or 'patient_id' in interpreted
                or any(not isinstance(r,dict) for r in final_rows)):
            return []
        exact_columns = [c for c in root_columns if c not in interpreted]
        if any(any(c not in final or digest(final[c])!=digest(row[c]) for c in exact_columns)
                for row,final in zip(rows,final_rows)):
            return []
    except (ValueError, TypeError, KeyError):
        return []
    proofs, memo, visiting = {}, {}, set()

    def trace(view):
        if view in memo:
            return memo[view]
        if view in visiting or view not in by:
            return {}
        visiting.add(view)
        out = {}
        try:
            call = by[view]
            columns, rows = assembly._rows(call['result'])
            proposal = {'table_from_tool': view, 'table': rows}
            proof = native_program_candidate(proposal, calls, [], as_of)
            if proof is not None:
                proofs[view] = proof
                out = {col: {'origin': view, 'column': col,
                             'row_indices': list(range(len(rows))), 'path': []}
                       for col in columns}
            elif call['name'] == 'compose_table':
                args = call['input']
                replay = assembly.compose(native, **args)
                if digest(replay) != digest({k: v for k, v in call['result'].items() if k != 'result_view'}):
                    return {}
                base = args['base']
                base_columns, base_rows = assembly._rows(native[base])
                renames = args.get('rename_columns') or {}
                if len(base_rows) != len(rows):
                    return {}
                for col, lineage in trace(base).items():
                    target = renames.get(col, col)
                    if target not in columns:
                        continue
                    if any(digest(r[target]) != digest(b[col]) for r, b in zip(rows, base_rows)):
                        return {}
                    out[target] = {**lineage, 'path': [*lineage['path'], {
                        'result_view': view, 'operation': 'replayed_left_assembly',
                        'input': args, 'result_digest': digest(call['result'])}]}
            elif call['name'] == 'query_sql':
                result = call['result']
                source_contract = result.get('source_contracts') or {}
                from .backstop_agent import NATIVE_SQL_CONTRACT
                if (result.get('native_sql_contract') != NATIVE_SQL_CONTRACT
                        or source_contract.get('as_of') != as_of or not source_contract.get('source_digest')):
                    return {}
                query = result.get('declared_executed_query') or result.get('executed_query')
                tree = sqlglot.parse_one(query, read='sqlite')
                # No changed membership, joins or aggregation can inherit a
                # branch's semantics without its own review. ORDER BY is safe
                # only after the complete direct tuple multiset reconciles.
                if not isinstance(tree, exp.Select) or any(tree.args.get(k) for k in
                        ('joins', 'where', 'group', 'having', 'qualify', 'limit', 'offset', 'distinct', 'with_')):
                    return {}
                source = tree.args.get('from_')
                table = source.this if source is not None else None
                if not isinstance(table, exp.Table) or table.name not in native:
                    return {}
                base = table.name
                base_columns, base_rows = assembly._rows(native[base])
                if len(base_rows) != len(rows):
                    return {}
                direct = {}
                for expression in tree.expressions:
                    value = expression.this if isinstance(expression, exp.Alias) else expression
                    if (isinstance(value, exp.Column) and value.name in base_columns
                            and (not value.table or value.table == table.alias_or_name)):
                        direct[expression.alias_or_name] = value.name
                if not direct or not set(direct) <= set(columns):
                    return {}
                indices = collections.defaultdict(collections.deque)
                for i, row in enumerate(base_rows):
                    indices[digest(decoded_json([row[c] for c in direct.values()]))].append(i)
                row_map = []
                for row in rows:
                    matches = indices[digest(decoded_json([row[c] for c in direct]))]
                    if not matches:
                        return {}
                    row_map.append(matches.popleft())
                if any(indices.values()):
                    return {}
                for target, col in direct.items():
                    lineage = trace(base).get(col)
                    if lineage is not None:
                        if proofs[lineage['origin']]['source_digest'] != source_contract['source_digest']:
                            return {}
                        out[target] = {**lineage,
                            'row_indices': [lineage['row_indices'][i] for i in row_map],
                            'path': [*lineage['path'], {'result_view': view,
                                'operation': 'complete_direct_projection', 'query': query,
                                'result_digest': digest(result)}]}
        except (ValueError, TypeError, KeyError, IndexError, sqlglot.errors.SqlglotError):
            out = {}
        finally:
            visiting.remove(view)
        memo[view] = out
        return out

    traced = trace(root)
    from .evidence_projection import native_timing_projection
    timing = native_timing_projection(calls)
    indices = {c['result']['result_view']: i for i,c in enumerate(calls)
        if isinstance(c.get('result'),dict) and c['result'].get('result_view')}
    review_programs = {}
    for view, proof in proofs.items():
        program = copy.deepcopy(proof)
        for dependency in program['native_receipt_digests']:
            program['native_receipt_digests'][dependency] = timing['current'][indices[dependency]]
        for contract in program['calculation_contracts']:
            if contract['operation']=='compose_table':
                normalized=timing['assemblies'].get(indices[contract['result_view']])
                if normalized is not None:contract['assembly']=normalized
        review_programs[view]=program
    groups = {}
    for field, lineage in traced.items():
        if field in interpreted or field == 'patient_id':
            continue
        origin = lineage['origin']
        group = groups.setdefault(origin, {'schema': VERSION, 'as_of': as_of,
            'result_view': root, 'table_digest': digest(final_rows), 'program': proofs[origin],
            'review_program': review_programs[origin], 'fields': {}})
        group['fields'][field] = lineage
    # Whole-table native routing already has its own connected path.
    return list(groups.values()) if groups and (interpreted or any(f not in traced for f in final_rows[0])) else []


def review_packet(question, candidate, *, pending=None):
    fields = candidate['fields']
    names = list(fields) if pending is None else pending
    # Row values stay in complete native receipts. The reviewer sees all SQL,
    # source definitions, row/field shapes and final field labels/ancestry.
    state = {'id': 'summary', 'question': question, 'as_of': candidate['as_of'],
        'scope': 'Shared program meaning for the named fields only. This is NOT whole-answer coverage, documentary interpretation or clinical qualification.',
        'native_program': candidate['review_program'],
        'program_fields': {name: {**{k: v for k, v in fields[name].items() if k not in ('row_indices','path')},
            'path': [{k: v for k, v in step.items() if k != 'result_digest'} for step in fields[name]['path']]} for name in fields},
        'final_result_view': candidate['result_view'],
        'instruction': 'Decide whether each named output field means what its complete structured program and source definitions establish for this question. Inspect predicates, join keys, field labels, source roles, dates, missingness, domain and units. A faithful SQL result with a misleading field name is not supported. If individual narrative reading is needed or the definitions do not settle the meaning, leave the field unknown. This review cannot establish attendance, responsibility, eligibility or absence beyond the recorded source contract.'}
    claims = [{'id': 'program_' + str(i), 'field': name, 'path': ['program_fields', name]}
              for i, name in enumerate(names)]
    state['claim_manifest'] = claims
    from ..deliverables.receipts import readable_json
    text = json.dumps(readable_json(state), ensure_ascii=False, separators=(',', ':'))
    return {'id': 'summary', 'date': candidate['as_of'], 'text': text,
        'bytes': len(text.encode()), 'claim_manifest': claims,
        'representation': VERSION, 'dependency_complete': True, 'source_context_complete': True}


def review(question, branches, *, judge, ledger, cache, frontier=None):
    from .backstop_agent import field_review_observation
    reviews = []
    for branch in branches:
        packet = review_packet(question, branch)
        key = digest([VERSION, question, packet['text']])
        if key in cache:
            reviews.append({**cache[key], 'candidate_digest': digest(branch), 'reused': True, 'steps': [], 'frontier_calls': 0, 'frontier_cost_usd': 0})
            continue
        named = {c['id']: {'type': 'choice', 'instructions': {
            'claim': c, 'task': 'Check the shared native program and source definitions once for this field, not individual copies. The final field label must be justified. Unknown if narrative interpretation is required.'},
            'criteria': {'supported': 'The complete structured program supports this field meaning, including its dates, source role, population, domain, units and unknowns.',
                         'wrong_program': 'The executed selection, calculation, label, role, domain or clock does not support this field as stated.',
                         'source_reading': 'Individual documentary meaning is required for this field.',
                         'unknown': 'This program does not settle the field meaning.'}}
                 for c in packet['claim_manifest']}
        result = judge('Review the shared executed program for these output fields', named,
            [{'id': 'native_program', 'date': branch['as_of'], 'text': packet['text']}],
            as_of=branch['as_of'], ledger=ledger, allow_live=True,
            long_context=packet['bytes'] > 32768)
        supported, pending = {}, []
        for claim in packet['claim_manifest']:
            reading = (result.get('judgments') or {}).get(claim['id'], {})
            observation, _ = field_review_observation(reading, result.get('steps') or [], claim['id'])
            j = observation.get('model_judgment') or {}
            p, threshold = j.get('probability'), j.get('threshold')
            if (observation.get('state') == 'supported' and j.get('decided_by')
                    and type(p) in (int, float) and type(threshold) in (int, float) and 0 < threshold <= p <= 1):
                supported[claim['field']] = observation
            elif not reading.get('decided_by'):
                pending.append(claim['field'])
        extra = None
        if pending and frontier is not None:
            remaining = review_packet(question, branch, pending=pending)
            try:
                extra = frontier(remaining, ledger)
            except Exception as exc:
                cache[key] = {'schema': VERSION, 'key': key, 'candidate_digest': digest(branch),
                    'question_digest': digest(question), 'supported_fields': supported,
                    'typed_review': result, 'steps': result.get('steps') or [],
                    'frontier_review': None, 'frontier_calls': 0, 'frontier_cost_usd': None,
                    'frontier_failure': {'type': type(exc).__name__, 'error': str(exc)},
                    'clinical_qualification': False, 'whole_answer_accepted': False}
                raise
            if extra.get('status') == 'reviewed' and (extra.get('verdict') or {}).get('joint_support') == 'supported':
                by_id = {c['id']: c['field'] for c in remaining['claim_manifest']}
                for claim_id in extra['verdict']['supported_claim_ids']:
                    supported[by_id[claim_id]] = {'state': 'supported',
                        'authority': 'frontier_machine_program_judgment',
                        'review_packet_digest': digest(remaining['text']), 'review_digest': digest(extra),
                        'clinical_qualification': False}
        record = {'schema': VERSION, 'key': key, 'candidate_digest': digest(branch),
            'question_digest': digest(question), 'supported_fields': supported,
            'typed_review': result, 'steps': result.get('steps') or [], 'frontier_review': extra,
            'frontier_calls': (extra or {}).get('calls', 0),
            'frontier_cost_usd': (extra or {}).get('cost_usd', 0),
            'clinical_qualification': False, 'whole_answer_accepted': False}
        cache[key] = record
        reviews.append(record)
    return reviews


def pending_program_work(branches, reviews):
    """A shared program fault/unknown must not become a patient retry campaign."""
    faults, unresolved = [], []
    for branch, reviewed in zip(branches, reviews):
        packet = review_packet('', branch)
        typed = (reviewed.get('typed_review') or {}).get('judgments') or {}
        for claim in packet['claim_manifest']:
            field = claim['field']
            if field in reviewed.get('supported_fields', {}):continue
            judgment = typed.get(claim['id']) or {}
            if judgment.get('decided_by') and judgment.get('label') == 'source_reading':continue
            target = {'result_view': branch['program']['result_view'], 'final_field': field,
                'program_digest': digest(branch['program'])}
            if judgment.get('decided_by') and judgment.get('label') == 'wrong_program':
                faults.append({**target, 'judgment': judgment})
            else:unresolved.append(target)
        frontier = reviewed.get('frontier_review') or {}
        if frontier.get('status') == 'reviewed':
            pending = [c['field'] for c in packet['claim_manifest']
                if not (typed.get(c['id']) or {}).get('decided_by')]
            remaining = review_packet('', branch, pending=pending)
            by_id = {c['id']: c['field'] for c in remaining['claim_manifest']}
            for finding in (frontier.get('verdict') or {}).get('findings', []):
                if finding['label'] in ('contradicted', 'not_established'):
                    field = by_id[finding['claim_id']]
                    faults.append({'result_view': branch['program']['result_view'], 'final_field': field,
                        'program_digest': digest(branch['program']), 'frontier_finding': finding})
                    unresolved = [u for u in unresolved if u['final_field'] != field]
    return {'faults': faults, 'unresolved': unresolved}


def attach(packets, answer, branches, reviews):
    """Bind accepted shared meaning to exact final cells in current packets."""
    from ..deliverables.receipts import uncompact_json
    table = answer.get('table') or []
    for packet in packets:
        if packet.get('patient_dependency_bound') is not True or packet.get('dependency_complete') is not True:
            continue
        state = uncompact_json(json.loads(packet['text']))
        rows = state.get('answer_rows') or []
        pid = state.get('patient_id')
        selected = [r for r in table if r.get('patient_id') == pid]
        # The lossless packet decoder restores original cell types, including
        # JSON strings. Require exact equality; no value/type coercion here.
        if digest(selected) != digest(rows):
            continue
        fields = {}
        for branch, reviewed in zip(branches, reviews):
            if (reviewed.get('candidate_digest') != digest(branch) or branch['table_digest'] != digest(table)
                    or branch['as_of'] != packet['date'] or branch['result_view'] != packet.get('result_view')):
                continue
            for claim in packet['claim_manifest']:
                field = claim['field']; path = claim['path']
                observation = reviewed['supported_fields'].get(field)
                if not observation or field not in branch['fields'] or len(path) != 3 or path[0] != 'answer_rows':
                    continue
                judgment = observation.get('model_judgment') or {}
                probability, threshold = judgment.get('probability'), judgment.get('threshold')
                typed = (observation.get('state') == 'supported' and judgment.get('label') == 'supported'
                    and judgment.get('decided_by') and type(probability) in (int,float)
                    and type(threshold) in (int,float) and 0 < threshold <= probability <= 1)
                frontier_review = reviewed.get('frontier_review') or {}
                frontier = (observation.get('authority') == 'frontier_machine_program_judgment'
                    and frontier_review.get('status') == 'reviewed'
                    and (frontier_review.get('verdict') or {}).get('joint_support') == 'supported'
                    and observation.get('review_digest') == digest(frontier_review))
                if not (typed or frontier):continue
                fields[claim['id']] = {'schema': VERSION, 'field': field, 'path': path,
                    'value_digest': digest(rows[path[1]][field]), 'patient_id': pid,
                    'as_of': packet['date'], 'source_digest': branch['program']['source_digest'],
                    'program_digest': digest(branch['program']), 'meaning_review_key': reviewed['key'],
                    'meaning_observation': observation, 'authority': 'native_copy_with_machine_program_meaning',
                    'clinical_qualification': False}
        if fields:
            packet['shared_program_fields'] = {'schema': VERSION, 'packet_digest': digest(packet['text']),
                'manifest_digest': digest(packet['claim_manifest']), 'fields': fields}
    return packets


def field_proof(packet, claim, state):
    binding = packet.get('shared_program_fields') or {}
    if (binding.get('schema') != VERSION or binding.get('packet_digest') != digest(packet['text'])
            or binding.get('manifest_digest') != digest(packet['claim_manifest'])):
        return None
    proof = (binding.get('fields') or {}).get(claim['id'])
    if not proof or proof['path'] != claim['path'] or proof['field'] != claim['field']:
        return None
    try:
        value = state
        for part in claim['path']: value = value[part]
        if (digest(value) != proof['value_digest'] or state.get('patient_id') != proof['patient_id']
                or state.get('as_of') != proof['as_of'] or packet['date'] != proof['as_of']):
            return None
        return proof
    except (KeyError, IndexError, TypeError):
        return None
