"""Versioned buyer/provider declarations and exact contract scope for review.

Matching declared scope never establishes network participation or member benefits.
Existing imported clauses retain their own provenance and mapping-review state.
"""
from pathlib import Path
import hashlib
import json
import sqlite3
from ...contracts import digest
from . import care_capabilities as care, document_clause_bindings as clauses, coverage_work

VERSION = 'buyer-provider-participation/1'


def identity():
    return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), clauses.identity()])


def database(path):
    db = sqlite3.connect(Path(path)/'participation-evidence.sqlite3', timeout=30)
    db.executescript('''CREATE TABLE IF NOT EXISTS declarations(buyer TEXT,revision INTEGER,payload TEXT,PRIMARY KEY(buyer,revision));
      CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY,payload TEXT);
      CREATE TABLE IF NOT EXISTS queue_contexts(sequence INTEGER PRIMARY KEY AUTOINCREMENT,task TEXT,payload TEXT);
      CREATE TABLE IF NOT EXISTS handoffs(task TEXT,revision INTEGER,payload TEXT,PRIMARY KEY(task,revision));''')
    for table in ('declarations', 'reports', 'queue_contexts', 'handoffs'):
        for op in ('UPDATE', 'DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{op} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'Participation history is immutable'); END;")
    return db


def current(db, buyer):
    row = db.execute('SELECT payload FROM declarations WHERE buyer=? ORDER BY revision DESC LIMIT 1', (buyer,)).fetchone()
    return json.loads(row[0]) if row else {'buyer_id': buyer, 'revision': 0, 'declarations': [], 'authority': 'buyer_reported_not_independently_verified'}


def registry(path, buyer):
    buyer = care.text(buyer, 'buyer ID'); db = database(path)
    try: return current(db, buyer)
    finally: db.close()


def normalize(path, d, services):
    care.exact(d, 'id service_id provider_organization_id payer_organization_id plan_id region pathway_id relationship reported_status effective_start effective_end received_on provenance clauses')
    out = {k: care.text(d[k], k, 2000 if k == 'provenance' else 200, empty=k in ('payer_organization_id', 'pathway_id'))
           for k in ('id', 'service_id', 'provider_organization_id', 'payer_organization_id', 'plan_id', 'region', 'pathway_id', 'provenance')}
    if out['service_id'] not in services: raise ValueError('Declaration must name a service in this buyer portfolio')
    if out['pathway_id'] and out['pathway_id'] not in {p['id'] for p in care.catalog()['pathways']}:
        raise ValueError('Unknown participation pathway')
    if d['relationship'] not in ('network_contract', 'program_participation'):
        raise ValueError('Network contract or program participation relationship required')
    if d['relationship'] == 'program_participation' and not out['pathway_id']:
        raise ValueError('Program participation requires an exact pathway')
    if d['reported_status'] not in ('participating', 'not_participating', 'pending', 'unknown'):
        raise ValueError('Explicit reported participation status required')
    out.update(relationship=d['relationship'], reported_status=d['reported_status'])
    for k in ('effective_start', 'effective_end', 'received_on'): out[k] = care.day(d[k])
    if out['effective_end'] < out['effective_start']: raise ValueError('Participation period is reversed')
    if not isinstance(d['clauses'], list) or len(d['clauses']) > 8: raise ValueError('At most eight exact imported clauses per declaration')
    if len({digest(b) for b in d['clauses']}) != len(d['clauses']): raise ValueError('Distinct clause bindings required')
    for b in d['clauses']:
        if not isinstance(b, dict) or b.get('kind') != 'program_clause': raise ValueError('Exact imported program clause required')
        clauses.bind(path, {'patient_id': None, 'as_of': out['received_on']}, b)
    out['clauses'] = sorted(d['clauses'], key=digest)
    return out


def save(path, request):
    care.exact(request, 'buyer_id expected_revision actor actor_kind declarations')
    buyer = care.text(request['buyer_id'], 'buyer ID'); actor = care.text(request['actor'], 'declaration actor')
    if request['actor_kind'] not in ('operator', 'synthetic_test'): raise ValueError('Operator or synthetic-test declaration required')
    if type(request['expected_revision']) is not int or request['expected_revision'] < 0: raise ValueError('Nonnegative expected revision required')
    if not isinstance(request['declarations'], list) or len(request['declarations']) > 100:
        raise ValueError('At most 100 participation declarations per buyer')
    portfolio = care.portfolio(path, buyer); services = {c['id'] for c in portfolio['capabilities']}
    values = sorted([normalize(path, d, services) for d in request['declarations']], key=lambda d: d['id'])
    if len({d['id'] for d in values}) != len(values): raise ValueError('Distinct declaration IDs required')
    db = database(path)
    try:
        db.execute('BEGIN IMMEDIATE'); old = current(db, buyer)
        if old['revision'] != request['expected_revision']: raise care.work.WorkConflict('Participation declarations changed; reload before saving')
        if care.portfolio(path, buyer) != portfolio: raise care.work.WorkConflict('Buyer services changed while saving participation')
        if old['declarations'] == values: return old
        row = {'schema': VERSION, 'buyer_id': buyer, 'revision': old['revision'] + 1, 'declarations': values,
               'actor': actor, 'actor_kind': request['actor_kind'], 'recorded_at': care.work.now(),
               'portfolio_id_at_declaration': portfolio.get('id'), 'authority': 'buyer_reported_not_independently_verified',
               'human_identity_authenticated': False, 'verified_participation': False}
        row['id'] = digest(row)
        db.execute('INSERT INTO declarations VALUES (?,?,?)', (buyer, row['revision'], json.dumps(row))); db.commit()
        return row
    finally: db.close()


def compare(value, declared):
    return 'unknown' if not value or not declared else 'matches' if value == declared else 'mismatch'


def scope(path, declaration, plan, payer, route):
    d = declaration; cov = plan['coverage']; asof = cov['as_of']
    checks = {'plan_id': compare(cov['plan_id'], d['plan_id']), 'region': compare(cov['region'], d['region']),
              'payer_organization': compare(payer, d['payer_organization_id']),
              'received_by_decision': 'matches' if d['received_on'] <= asof else 'mismatch',
              'effective_period': 'matches' if d['effective_start'] <= asof <= d['effective_end'] else 'mismatch',
              'pathway': ('matches' if route['pathway_id'] == d['pathway_id'] else 'mismatch') if d['pathway_id'] else 'not_restricted'}
    bound = []; failures = []
    for b in d['clauses']:
        try: bound.append(clauses.bind(path, {'patient_id': plan['patient_id'], 'as_of': asof}, b))
        except (ValueError, KeyError, FileNotFoundError) as exc: failures.append({'selector': b, 'reason': str(exc)})
    document_state = ('changed_or_unavailable' if failures else 'no_imported_clause' if not bound else
                      'mapping_and_period_usable' if all(b['documentary_mapping_usable'] for b in bound) else 'mapping_or_period_unresolved')
    return {'declaration': d, 'scope_checks': checks,
            'scope_status': 'outside_declared_scope' if 'mismatch' in checks.values() else 'scope_unresolved' if 'unknown' in checks.values() else 'declared_scope_matches',
            'clauses': bound, 'invalid_clause_bindings': failures, 'document_state': document_state,
            'declaration_to_clause_meaning': 'not_independently_reviewed',
            'verified_participation': False, 'verified_benefits': False}


def prepare(path, plan_id, option_id):
    plan = care.inspect_plan(path, plan_id); coverage = coverage_work.association(path, plan)
    option = next((o for o in plan['options'] if o['id'] == option_id), None)
    if not option: raise ValueError('Unknown service option in matching plan')
    reg = registry(path, plan['buyer_id'])
    values = [d for d in reg['declarations'] if d['service_id'] == option['capability']['id']]
    payer = coverage['coverage_selection']['report']['fields'].get('payer_organization_id', '') if coverage else ''
    routes = []
    for route in option['routes']:
        rows = [scope(path, d, plan, payer, route) for d in values]
        possible = [r for r in rows if r['scope_status'] != 'outside_declared_scope']
        # Contradictions are grouped by provider and relationship, not across distinct organizations or contract types.
        groups = {}
        for r in possible:
            d = r['declaration']; groups.setdefault((d['provider_organization_id'], d['relationship']), []).append(d)
        conflicts = [{'provider_organization_id': org, 'relationship': kind, 'declaration_ids': [d['id'] for d in ds]}
                     for (org, kind), ds in groups.items() if {'participating', 'not_participating'} <= {d['reported_status'] for d in ds}]
        routes.append({'pathway_id': route['pathway_id'], 'name': route['pathway']['name'] if route['pathway'] else 'Buyer-specific service funding',
                       'declarations': rows, 'conflicts': conflicts,
                       'status': 'conflicting_declarations' if conflicts else 'declaration_review_required' if possible else 'no_in_scope_declaration',
                       'public_pathway_establishes_participation': False})
    row = {'schema': VERSION, 'code_identity': identity(), 'plan_id': plan_id, 'source_plan_hash': digest(plan), 'option_id': option_id,
           'buyer_id': plan['buyer_id'], 'patient_id': plan['patient_id'], 'service_id': option['capability']['id'], 'as_of': plan['coverage']['as_of'],
           'declaration_registry_id': reg.get('id'), 'declaration_revision': reg['revision'], 'routes': routes,
           'received_coverage_receipt_id': coverage['id'] if coverage else None,
           'reported_patient_plan': plan['coverage']['plan_id'], 'reported_payer_organization': payer,
           'authority': 'Declared scope and document dependencies for internal review only',
           'clinical_execution_authorized': False, 'verified_benefits': False, 'verified_participation': False,
           'remaining_checks': ['Actual provider/network participation', 'Current member benefits and service authorization',
                                'Patient goals and consent', 'Clinical appropriateness', 'Authenticated accountable review and execution authority']}
    row['id'] = digest(row); db = database(path)
    try:
        with db: db.execute('INSERT OR IGNORE INTO reports VALUES (?,?)', (row['id'], json.dumps(row)))
    finally: db.close()
    return row


def inspect(path, key):
    key = care.text(key, 'participation context ID', 64); db = database(path)
    try: row = db.execute('SELECT payload FROM reports WHERE id=?', (key,)).fetchone()
    finally: db.close()
    if not row: raise ValueError('Unknown participation context')
    report = json.loads(row[0])
    if digest({k: v for k, v in report.items() if k != 'id'}) != key or prepare(path, report['plan_id'], report['option_id'])['id'] != key:
        raise care.work.WorkConflict('Participation declarations, clauses or source context changed; refresh the match and internal queue')
    return report
