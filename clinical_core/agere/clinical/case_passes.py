"""Source-complete bounded interpretation, followed by explicit excerpt reconciliation.

Reuses the chart partitioner, source/citation checks and audited provider gateway.
All first-pass findings survive reconciliation. Model coverage is self-reported,
not an entailment check, and excerpt reconciliation is not whole-chart reasoning.
"""
from .case_synthesis import input_token_total, token_count_calls
from collections import Counter
from pathlib import Path
import hashlib
import json
import sqlite3

from ...contracts import digest
from ..gateway import PersistentGateway
from . import case_synthesis as single, context_builder as context, context_passes
from . import warehouse_interpretation as native

VERSION = 'source-case-passes/1'
MAX_REQUEST_BYTES = 20000
UNIT_STATES = ['discussed', 'unresolved', 'not_relevant_to_question']
MAP_SCHEMA = single.obj({**single.SCHEMA['properties'], 'unit_reviews': {
    'type': 'array', 'items': single.obj({'unit': single.STR,
        'status': {'type': 'string', 'enum': UNIT_STATES}, 'reason': single.STR})}})
MAP_INSTRUCTIONS = single.INSTRUCTIONS + ''' This is ONE pass of a larger declared source scope.
Interpret only this pass; another pass may qualify or contradict it. Return at most
4 findings, each text at most 600 characters, with at most 2 citations of at most
400 characters each, and at most 2 unresolved items per finding (300 characters each).
Return at most 4 unanswered questions and 4 review steps, each at most 300 characters.
Account for EVERY supplied unit exactly once in unit_reviews: discussed if cited by a
finding, unresolved if its meaning/relevance cannot be assessed, or not_relevant_to_question.
Give a reason of at most 160 characters; these are your judgments, not validated coverage.
Preserve cross-pass/source dependencies and don't resolve unavailable records by inference.'''
RECONCILE_INSTRUCTIONS = single.INSTRUCTIONS + ''' Reconcile ALL supplied pass findings,
unresolved unit reviews, and explicit source dependencies. First-pass findings are
unreviewed machine proposals, not independent evidence. Their source excerpts are the
ONLY original text supplied in this step; broader raw units were seen in separate passes.
Use the supplied global unit IDs and exact source excerpts for any new finding.
Look for qualifications, contradictions, temporal differences and missing dependencies.
Do not majority-vote or treat duplicated source statements as independent confirmation.
Keep questions unresolved when omitted raw context could change the answer. A link only
establishes a documentary reference, never a causal or clinical relation. No source
finding will be deleted by this reconciliation. Explicitly identify limits of excerpt
reconciliation. Return at most 4 new findings; do not simply repeat each pass finding.'''


def code_identity():
    return digest([VERSION, single.code_identity(), context_passes.code_identity(), native.identity(),
                   hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def dependencies(path, row):
    """Typed references touching scope; no outside-patient content is transported."""
    db = sqlite3.connect((Path(path)/'stack.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)
    try:
        records = [json.loads(r[0]) for r in db.execute(
            'SELECT payload FROM records WHERE patient=? ORDER BY id', (row['patient_id'],))]
        ix = native.index(records)
        selected = set(row['model_context']['sources'])
        edges, issues = [], []
        for record in records:
            statement = native.statement(record, row['model_context']['snapshot']['as_of'], ix)
            if not statement:
                continue
            rid = record['record_id']
            for link in statement['linked_sources']:
                target = link['target']['record_id']
                if rid in selected or target in selected:
                    edges.append({'from': rid, 'to': target, 'field': link['field'],
                                  'scope': 'in_scope' if {rid, target} <= selected else 'outside_declared_scope'})
            if rid in selected:
                issues.extend({'source_record': rid, **issue} for issue in statement['link_issues'])
        # Retain only IDs/statuses for unresolved references, never other patients' content.
        for issue in issues:
            found = db.execute('SELECT patient FROM records WHERE id=?', (issue['record_id'],)).fetchone()
            if found and found[0] != row['patient_id']:
                issue['reason'] = 'cross_patient_reference_not_evidence'
        return {'edges': edges, 'unresolved_references': issues,
                'patient_record_fingerprint': digest(sorted((r['record_id'], digest(r)) for r in records)),
                'meaning': 'Explicit typed source references; no causal or clinical inference.'}
    finally:
        db.close()


def unit_map(packet):
    return {'u'+str(i): {'unit': unit, 'text': unit['text'] if unit['kind'] == 'narrative'
                        else context.encode(unit['reported_fields']).decode()}
            for i, unit in enumerate(packet['evidence'], 1)}


def envelope(wire, instructions, schema):
    return {'model': PersistentGateway.MODELS['claude'], 'max_tokens': 4000,
            'system': instructions, 'messages': [{'role': 'user', 'content': context.encode(wire).decode()}],
            'output_config': {'format': {'type': 'json_schema', 'schema': schema}}}


def request_bytes(body):
    # Must match the existing gateway's actual transport, including JSON escaping.
    return len(json.dumps(body, allow_nan=False).encode())


def pass_request(packet, links):
    units = unit_map(packet)
    sources = [{**entry['source'], 'clocks': entry['clocks']} for entry in packet['sources'].values()]
    selected = set(packet['sources'])
    wire = {'schema': VERSION, 'stage': 'source_pass', 'synthetic': True,
        'patient_id': packet['patient_id'], 'as_of': packet['as_of'], 'question': packet['question'],
        'source_scope': packet['source_scope'], 'pass_number': packet.get('pass_number'),
        'passes_in_case': packet.get('passes_in_case'), 'requirements': packet['documentary_requirements'],
        'program_context': packet['program_context'],
        'missing_evidence': packet['missing_evidence_in_selected_sources'],
        'dependencies': [e for e in links['edges'] if e['from'] in selected or e['to'] in selected],
        'unresolved_references': [e for e in links['unresolved_references'] if e['source_record'] in selected],
        'sources': {'columns': list(sources[0]) if sources else [], 'rows': [list(s.values()) for s in sources]},
        'units': [{'id': key, 'source': v['unit']['source_ref'], 'kind': v['unit']['kind'],
                   'field': v['unit']['field'], 'text': v['text']} for key, v in units.items()]}
    return envelope(wire, MAP_INSTRUCTIONS, MAP_SCHEMA), units


def plan(row, links, maximum_passes=8):
    if type(maximum_passes) is not int or not 1 <= maximum_passes <= 16:
        raise ValueError('Explicit source-pass cap must be between 1 and 16')
    packet = row['model_context']
    candidates = [{**u, **packet['sources'][u['source_ref']]} for u in packet['evidence']]
    bundle = context_passes.partition(row['patient_id'], {**row['parameters'], 'max_bytes': MAX_REQUEST_BYTES},
        candidates, {'snapshot': packet['snapshot']}, packet['documentary_requirements'],
        packet['versioned_program_context'], wire_size=lambda p: request_bytes(pass_request(p, links)[0]))
    if not bundle['review_ready']:
        raise ValueError('An indivisible source unit or required dependency header exceeds the gateway bound; no calls made')
    if len(bundle['passes']) > maximum_passes:
        raise ValueError('Complete declared scope needs '+str(len(bundle['passes']))+' source passes, exceeding the requested cap; no calls made')
    record_passes = {}
    for p in bundle['passes']:
        for rid in p['sources']:
            record_passes.setdefault(rid, []).append(p['pass_number'])
    links = {**links, 'record_passes': record_passes,
        'cross_pass_edges': [e for e in links['edges'] if e['scope'] == 'in_scope' and
                            not set(record_passes[e['from']]) & set(record_passes[e['to']])]}
    return bundle, links


def parse_pass(raw, body, units, row):
    if raw.get('model') != body['model'] or raw.get('stop_reason') != 'end_turn':
        raise ValueError('Incomplete pass or unexpected model revision')
    value = json.loads(''.join(v['text'] for v in raw.get('content', []) if v.get('type') == 'text'))
    if not isinstance(value, dict) or set(value) != set(MAP_SCHEMA['properties']):
        raise ValueError('Invalid source-pass result fields')
    reviews = value.pop('unit_reviews')
    core_raw = {**raw, 'content': [{'type': 'text', 'text': json.dumps(value)}]}
    result = single.parse(core_raw, body, units, row)
    for field in ('findings', 'unanswered', 'review_steps'):
        if len(result[field]) > 4:
            raise ValueError('Source-pass output bound exceeded')
    for f in result['findings']:
        single.bounded_text(f['text'], 600)
        if len(f['citations']) > 2 or len(f['unresolved']) > 2:
            raise ValueError('Source-pass evidence bound exceeded')
        for c in f['citations']: single.bounded_text(c['quote'], 400)
        for text in f['unresolved']: single.bounded_text(text, 300)
    for text in result['unanswered']: single.bounded_text(text, 300)
    for step in result['review_steps']: single.bounded_text(step['question'], 300)
    if not isinstance(reviews, list) or len(reviews) != len(units):
        raise ValueError('Every source unit must have a coverage disposition')
    seen = set(); cited = {c['unit'] for f in result['findings'] for c in f['citations']}
    for review in reviews:
        if not isinstance(review, dict) or set(review) != {'unit', 'status', 'reason'}:
            raise ValueError('Invalid unit disposition')
        key = review['unit']
        if not isinstance(key, str) or key not in units or key in seen or review['status'] not in UNIT_STATES:
            raise ValueError('Unknown or duplicate unit disposition')
        if review['status'] == 'discussed' and key not in cited:
            raise ValueError('Discussed source unit must be cited')
        single.bounded_text(review['reason'], 160); seen.add(key)
        review.update(source_unit_id=units[key]['unit']['id'], semantic_validation='pending')
    return {**result, 'unit_reviews': reviews}


def reconciliation_request(row, passes, links):
    units = unit_map(row['model_context'])
    names = {v['unit']['id']: k for k, v in units.items()}
    findings, excerpts, pending = [], {}, []
    source_ids = {rid: 's'+str(i) for i, rid in enumerate(sorted(row['model_context']['sources']), 1)}
    for p in passes:
        for f in p['result']['findings']:
            citations = []
            for c in f['citations']:
                key = names[c['source_unit_id']]
                excerpts.setdefault(key, {'unit': key, 'source': source_ids[c['source']['record_id']],
                    'field': c['field'], 'kind': c['kind'], 'excerpts': []})
                if c['quote'] not in excerpts[key]['excerpts']: excerpts[key]['excerpts'].append(c['quote'])
                citations.append({'unit': key, 'excerpt_index': excerpts[key]['excerpts'].index(c['quote'])})
            findings.append({k: f[k] for k in ('id', 'kind', 'text', 'unresolved')} | {'citations': citations})
        pending.append({'pass': p['pass_number'], 'unanswered': p['result']['unanswered'],
                        'unit_reviews': [{'unit': names[r['source_unit_id']], 'status': r['status'],
                                          'reason': r['reason']} for r in p['result']['unit_reviews']],
                        'review_steps': p['result']['review_steps']})
    sources = [{'alias': source_ids[rid], **value['source'], 'clocks': value['clocks']}
               for rid, value in sorted(row['model_context']['sources'].items())]
    wire_links = {'columns': ['from', 'to', 'field', 'scope'],
        'rows': [[source_ids.get(e['from'], e['from']), source_ids.get(e['to'], e['to']), e['field'], e['scope']]
                 for e in links['edges']], 'unresolved_references': links['unresolved_references'],
        'record_passes': {source_ids[k]: v for k, v in links['record_passes'].items()}, 'meaning': links['meaning']}
    def table(values, columns):
        return {'columns': columns, 'rows': [[value[c] for c in columns] for value in values]}
    # Lossless tabular factoring removes repeated JSON keys, never clinical text.
    # Source hashes remain in the durable receipts; the full metadata is retained
    # here too, using a dictionary for repeated clocks and source strings.
    clock_values = []
    for source in sources:
        value = source['clocks']
        if value not in clock_values: clock_values.append(value)
        source['clocks'] = {'clock_index': clock_values.index(value)}
    for f in findings:
        f['citations'] = [[c['unit'], c['excerpt_index']] for c in f['citations']]
    for p in pending:
        p['unit_reviews'] = table(p['unit_reviews'], ['unit', 'status', 'reason'])
        p['review_steps'] = table(p['review_steps'], ['kind', 'finding_ids', 'question'])
    wire = {'schema': VERSION, 'stage': 'excerpt_reconciliation', 'synthetic': True,
        'question': row['parameters']['question'], 'patient_id': row['patient_id'],
        'as_of': row['model_context']['snapshot']['as_of'], 'source_scope': row['parameters']['streams'],
        'missing_evidence': row['missing_evidence'], 'dependencies': wire_links,
        'sources': table(sources, list(sources[0])), 'source_clocks': clock_values,
        'requirements': row['model_context']['documentary_requirements'],
        'program_context': row['model_context']['versioned_program_context'],
        'pass_findings': table(findings, ['id', 'kind', 'text', 'unresolved', 'citations']),
        'pass_coverage_and_open_questions': pending,
        'original_source_excerpts': table(list(excerpts.values()), ['unit', 'source', 'field', 'kind', 'excerpts']),
        'scope': 'All pass findings retained; exact cited excerpts only. Tables use their columns in row order. Citation pairs are [unit, zero-based excerpt index]. Clock indexes refer to source_clocks.'}
    body = envelope(wire, RECONCILE_INSTRUCTIONS, single.SCHEMA)
    if request_bytes(body) > MAX_REQUEST_BYTES:
        raise ValueError('All pass findings and dependencies exceed the reconciliation request bound; source-pass progress saved, reconciliation unassessed')
    return body, units, excerpts


def parse_reconciliation(raw, body, units, row, excerpts):
    result = single.parse(raw, body, units, row)
    if len(result['findings']) > 4: raise ValueError('Reconciliation finding bound exceeded')
    for f in result['findings']:
        for c in f['citations']:
            if not any(c['quote'] in q for q in excerpts.get(c['unit'], {}).get('excerpts', [])):
                raise ValueError('Reconciliation cited text not supplied in its evidence excerpts')
    return result


def source_components(row, links, passes=()):
    """Keep explicit source references and known finding/review dependencies together."""
    parent = {rid: rid for rid in row['model_context']['sources']}
    def root(rid):
        while parent[rid] != rid:
            parent[rid] = parent[parent[rid]]; rid = parent[rid]
        return rid
    def join(ids):
        ids = sorted(set(ids) & set(parent))
        for rid in ids[1:]: parent[root(rid)] = root(ids[0])
    for edge in links['edges']: join([edge['from'], edge['to']])
    for p in passes:
        supports = {f['id']: {c['source']['record_id'] for c in f['citations']} for f in p['result']['findings']}
        for ids in supports.values(): join(ids)
        for step in p['result']['review_steps']:
            join({rid for f in step['finding_ids'] for rid in supports[f]})
    groups = {}
    for rid in parent: groups.setdefault(root(rid), set()).add(rid)
    return sorted(groups.values(), key=lambda ids: sorted(ids))


def reconciliation_jobs(row, passes, links, maximum=8):
    """Try one full reconciliation, then losslessly pack dependency components.

    An indivisible component still blocks. Unknown relationships between separate
    jobs are explicitly unassessed; lack of an explicit link is not independence.
    """
    def build(ids):
        packet = row['model_context']
        subrow = {**row, 'model_context': {**packet,
            'sources': {rid: value for rid, value in packet['sources'].items() if rid in ids},
            'evidence': [u for u in packet['evidence'] if u['source_ref'] in ids]}}
        unit_ids = {u['id'] for u in subrow['model_context']['evidence']}
        selected_passes = []
        for p in passes:
            r = p['result']; findings = [f for f in r['findings'] if any(c['source']['record_id'] in ids for c in f['citations'])]
            fids = {f['id'] for f in findings}
            reviews = [v for v in r['unit_reviews'] if v['source_unit_id'] in unit_ids]
            if reviews:
                selected_passes.append({**p, 'result': {**r, 'findings': findings, 'unit_reviews': reviews,
                    'review_steps': [s for s in r['review_steps'] if not s['finding_ids'] or set(s['finding_ids']) & fids]}})
        selected_links = {**links, 'edges': [e for e in links['edges'] if e['from'] in ids or e['to'] in ids],
            'record_passes': {rid: v for rid, v in links['record_passes'].items() if rid in ids},
            'unresolved_references': [v for v in links['unresolved_references'] if v['source_record'] in ids]}
        body, units, excerpts = reconciliation_request(subrow, selected_passes, selected_links)
        return {'body': body, 'units': units, 'excerpts': excerpts, 'row': subrow,
                'source_records': sorted(ids), 'finding_ids': [f['id'] for p in selected_passes for f in p['result']['findings']]}
    jobs = []; pending = set()
    for component in source_components(row, links, passes):
        try: build(pending | component)
        except ValueError as error:
            if 'reconciliation request bound' not in str(error): raise
            if not pending: raise
            jobs.append(build(pending)); pending = set(component)
            build(pending)  # refuse an indivisible overflow before any reconciliation call
        else: pending |= component
    if pending: jobs.append(build(pending))
    if len(jobs) > maximum: raise ValueError('Reconciliation exceeds the explicit request cap; source-pass progress retained')
    original_ids = {f['id'] for p in passes for f in p['result']['findings']}
    assigned = [f for j in jobs for f in j['finding_ids']]
    if len(assigned) != len(set(assigned)) or set(assigned) != original_ids:
        raise ValueError('Reconciliation must preserve every finding exactly once')
    return jobs


def saved_path(path, key):
    # Reuse exact ID/path validation; keep legacy single-pass receipts unchanged.
    return single.receipt_path(path, key).parent.parent/'case-passes'/(key+'.json')


def atomic_write(target, value):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp'); temporary.write_text(json.dumps(value, indent=2)); temporary.replace(target)


def inspect(path, key):
    target = saved_path(path, key)
    if not target.exists(): return single.inspect(path, key)
    saved = json.loads(target.read_text())
    if saved['id'] != key or saved['code_identity'] != code_identity():
        raise ValueError('Multi-pass synthesis implementation changed; rebuild answer')
    row = single.verified_context(path, saved['context_id'])
    if digest(row) != saved['context_digest'] or digest(dependencies(path, row)) != saved['dependencies_digest']:
        raise ValueError('Synthesis source/dependency scope changed; rebuild answer')
    if saved.get('status') != 'complete':
        raise ValueError('Incomplete source interpretation cannot enter program assessment')
    return saved


def progress(path, context_id):
    row = single.verified_context(path, context_id)
    link_digest = digest(dependencies(path, row))
    key = digest([VERSION, code_identity(), context_id, digest(row), link_digest])
    target = saved_path(path, key).parent/(key+'-progress.json')
    if not target.exists(): raise ValueError('No progress for the current context and implementation')
    return json.loads(target.read_text())


def preview(path, context_id, maximum_passes=8):
    row = single.verified_context(path, context_id); links = dependencies(path, row)
    try:
        body, units = single.request(row)
    except ValueError as error:
        if 'gateway bound' not in str(error): raise
    else:
        return {'schema': VERSION, 'context_id': context_id, 'patient_id': row['patient_id'],
            'source_passes': 1, 'maximum_inference_calls': 1, 'reservation_ceiling_usd': .5,
            'source_units': len(units), 'request_bytes': [request_bytes(body)], 'dependencies': links,
            'new_paid_api_calls': 0, 'scope': 'Original complete-scope synthesis route; model accuracy is unassessed.'}
    bundle, links = plan(row, links, maximum_passes)
    reconciliation_cap = min(maximum_passes, len(source_components(row, links)))
    return {'schema': VERSION, 'context_id': context_id, 'patient_id': row['patient_id'],
        'source_passes': len(bundle['passes']), 'maximum_inference_calls': len(bundle['passes'])+reconciliation_cap,
        'reservation_ceiling_usd': (len(bundle['passes'])+reconciliation_cap)*.5,
        'maximum_reconciliation_calls': reconciliation_cap,
        'source_units': bundle['counts']['source_units'],
        'request_bytes': [request_bytes(pass_request(p, links)[0]) for p in bundle['passes']],
        'dependencies': links, 'new_paid_api_calls': 0,
        'scope': 'Every declared unit is partitioned; final reconciliation is separately size-checked. Model accuracy is unassessed.'}


def _rename(result, prefix):
    return {**result, 'findings': [{**f, 'id': prefix+f['id'], 'stage': prefix.rstrip('_')} for f in result['findings']],
            'review_steps': [{**s, 'finding_ids': [prefix+k for k in s['finding_ids']]} for s in result['review_steps']]}


def synthesize(path, context_id, ledger, *, allow_live=False, maximum_passes=8):
    row = single.verified_context(path, context_id)
    # Keep the existing source-complete route and its saved identity when it fits.
    try:
        single.request(row)
    except ValueError as error:
        if 'gateway bound' not in str(error): raise
    else:
        return single.synthesize(path, context_id, ledger, allow_live=allow_live)
    original_links = dependencies(path, row)
    bundle, links = plan(row, original_links, maximum_passes)
    identity = code_identity(); row_digest = digest(row); link_digest = digest(original_links)
    key = digest([VERSION, identity, context_id, row_digest, link_digest])
    target = saved_path(path, key); out = target.parent/'provider'; out.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return {**inspect(path, key), 'cache_hit': True, 'new_paid_api_calls': 0, 'new_token_count_calls': 0}
    if not allow_live: raise ValueError('Live synthesis route disabled; no saved answer for this source version')
    prepared = [pass_request(p, links) for p in bundle['passes']]
    def request_key(body): return digest({'contract': 'context-student/1', 'provider': 'claude', 'body': body})
    def available(body):
        old = ledger.db.execute('SELECT status FROM calls WHERE id=?', (request_key(body),)).fetchone()
        if old and old[0] != 'validated': raise ValueError('Prior synthesis pending/failed; no silent paid retry')
        return bool(old)
    required = sum(not available(b) for b, _ in prepared)+min(maximum_passes, len(source_components(row, links)))
    remaining_usd = ledger.receipt()['remaining_reservation_usd']
    if remaining_usd is not None and remaining_usd < required*.5:
        raise ValueError('Shared authorization cannot cover all source passes and reconciliation; no calls made')
    def fresh():
        if digest(single.verified_context(path, context_id)) != row_digest or digest(dependencies(path, row)) != link_digest:
            raise ValueError('Source/dependency scope changed during interpretation; saved provider receipts retained')
    progress = {'schema': VERSION, 'id': key, 'context_id': context_id, 'patient_id': row['patient_id'],
        'status': 'source_passes_pending', 'passes': [], 'source_units': bundle['unit_manifest'],
        'context_digest': row_digest, 'code_identity': identity, 'dependencies_digest': link_digest,
        'dependencies': links, 'new_paid_api_calls': 0, 'new_token_count_calls': 0}
    progress_target = target.parent/(key+'-progress.json')
    def call(body, parser):
        if request_bytes(body) > MAX_REQUEST_BYTES: raise ValueError('Provider request exceeds gateway bound')
        available(body); fresh()
        tokens = single.token_check(body, out, allow_live=allow_live)
        progress['new_token_count_calls'] += token_count_calls(tokens)
        fresh()
        result, usage = single.send(body, 'claude', ledger, out, parse=parser)
        if usage.get('cache_hit'):
            from ..economics import usage_cost
            previous = ledger.db.execute('SELECT response,seconds FROM calls WHERE id=?', (request_key(body),)).fetchone()
            if previous:
                raw = json.loads(previous[0]); usage = {**usage, 'usage': raw.get('usage'),
                    'original_seconds': previous[1], 'cost_estimate': usage_cost(raw['model'], raw.get('usage'))}
        progress['new_paid_api_calls'] += int(not usage.get('cache_hit', False)); fresh()
        return result, usage, tokens
    try:
        for packet, (body, units) in zip(bundle['passes'], prepared):
            result, usage, tokens = call(body, lambda raw: parse_pass(raw, body, units, row))
            progress['passes'].append({'pass_number': packet['pass_number'], 'source_units': [u['id'] for u in packet['evidence']],
                'result': _rename(result, 'p'+str(packet['pass_number'])+'_'), 'usage': usage,
                'token_check': tokens, 'request_bytes': request_bytes(body)})
            atomic_write(progress_target, progress)
        jobs = reconciliation_jobs(row, progress['passes'], links, maximum_passes)
        needed = sum(not available(j['body']) for j in jobs)
        remaining_usd = ledger.receipt()['remaining_reservation_usd']
        if remaining_usd is not None and remaining_usd < needed*.5:
            raise ValueError('Shared authorization cannot cover the remaining reconciliation requests; progress retained')
        reconciliations = []
        for i, job in enumerate(jobs, 1):
            body = job['body']
            result, usage, tokens = call(body, lambda raw: parse_reconciliation(raw, body, job['units'], job['row'], job['excerpts']))
            result = _rename(result, 'r'+str(i)+'_')
            reconciliations.append({'result': result, 'usage': usage, 'token_check': tokens,
                'request_bytes': request_bytes(body), 'source_records': job['source_records'],
                'input_finding_ids': job['finding_ids'],
                'excerpt_unit_ids': [job['units'][k]['unit']['id'] for k in job['excerpts']]})
            progress['reconciliations'] = reconciliations; atomic_write(progress_target, progress)
        result = {k: [value for r in reconciliations for value in r['result'][k]] for k in ('findings', 'unanswered', 'review_steps')}
        if len(reconciliations) > 1:
            result['unanswered'].append('Relationships between the separate reconciliation groups remain unassessed. No explicit source link does not establish clinical independence.')
        findings = [f for p in progress['passes'] for f in p['result']['findings']]+result['findings']
        unanswered = list(dict.fromkeys([q for p in progress['passes'] for q in p['result']['unanswered']]+result['unanswered']))
        steps = [s for p in progress['passes'] for s in p['result']['review_steps']]+result['review_steps']
        reviews = [r for p in progress['passes'] for r in p['result']['unit_reviews']]
        if sorted(r['source_unit_id'] for r in reviews) != sorted(bundle['unit_manifest']):
            raise ValueError('Source unit coverage reconciliation failed')
        receipts = [p['token_check'] for p in progress['passes']]+[r['token_check'] for r in reconciliations]
        saved = {'schema': VERSION, 'id': key, 'synthetic': True, 'status': 'complete',
            'patient_id': row['patient_id'], 'question': row['parameters']['question'], 'context_id': context_id,
            'context_digest': row_digest, 'dependencies_digest': link_digest, 'code_identity': identity,
            'source_scope': row['parameters']['streams'], 'source_inventory': row.get('source_inventory'),
            'dependencies': links, 'passes': progress['passes'], 'findings': findings, 'unanswered': unanswered,
            'review_steps': [{**s, 'id': digest([key, 'review', i, s]), 'status': 'proposed',
                              'execution_authorized': False} for i, s in enumerate(steps)],
            'coverage': {'received_units': len(reviews), 'submitted_units': len(reviews),
                'model_accounted_units': len(reviews), 'unit_dispositions': dict(Counter(r['status'] for r in reviews)),
                'unassessed_units': [r for r in reviews if r['status'] == 'unresolved'],
                'complete_declared_scope': True, 'complete_chart': False,
                'reconciliation_scope': 'all_pass_findings_and_cited_excerpts',
                'reconciliation_groups': len(reconciliations), 'cross_group_relations_assessed': len(reconciliations) == 1,
                'reconciled_original_unit_ids': sorted({key for r in reconciliations for key in r['excerpt_unit_ids']}),
                'raw_units_not_revisited_in_reconciliation': sorted(bundle['unit_manifest']),
                'missing_evidence': row['missing_evidence'], 'retrieval_recall_assessed': False,
                'interpretation_accuracy_assessed': False, 'coverage_basis': 'provider_submission_and_model_self_report'},
            'reconciliation': {'result': result, 'groups': reconciliations,
                'request_bytes': max(r['request_bytes'] for r in reconciliations)},
            'provider': 'claude', 'model': body['model'],
            'route_reason': 'Complete original units exceed one gateway request; bounded raw passes followed by cited-excerpt reconciliation.',
            'token_check': {'input_tokens': input_token_total(receipts), 'basis': 'Sum of measured provider counts; unknown if any pass has no token measurement'},
            'usage': [p['usage'] for p in progress['passes']]+[r['usage'] for r in reconciliations],
            'new_paid_api_calls': progress['new_paid_api_calls'], 'new_token_count_calls': progress['new_token_count_calls'],
            'cache_hit': False, 'clinical_state_established': False, 'clinical_execution_authorized': False,
            'limits': single.LIMITS+['Reconciliation sees exact cited excerpts and all pass findings, not all raw evidence simultaneously.',
                'Model unit dispositions are unreviewed judgments. Submitted evidence does not establish correct interpretation.',
                'All pass findings and unresolved questions remain available; no majority voting or silent suppression.']}
        fresh(); atomic_write(target, saved)
        progress['status'] = 'complete'; atomic_write(progress_target, progress)
        return saved
    except Exception as error:
        progress['status'] = 'incomplete'; progress['error'] = str(error)
        progress['unassessed_source_units'] = sorted(set(bundle['unit_manifest'])-
            {u for p in progress['passes'] for u in p['source_units']})
        progress['reconciliation_assessed'] = False
        atomic_write(progress_target, progress)
        raise
