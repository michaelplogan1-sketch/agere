"""Fixed development routing checks; never opens the independent sealed bank.

All warehouse reads are mode=ro. Runtime caches and work stores are untouched.
This is engineering regression evidence, not an independently adjudicated test.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time

from clinical_core.agere.clinical import request_plan as R, shared_planner as S, request_vocabulary as V, sql_query

ROOT = Path(__file__).resolve().parent
WORKLOAD = ROOT/'tests/development/shared_planner_36.json'


def run(warehouse, execute=False):
    workload = json.loads(WORKLOAD.read_text())
    # Fresh cache-free workspace; the large source database remains read-only.
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary)
        (path/'semantic.sqlite3').symlink_to(Path(warehouse).resolve()/'semantic.sqlite3')
        profile = Path(warehouse)/'source-profile.json'
        if profile.exists(): (path/'source-profile.json').symlink_to(profile.resolve())
        catalog = R.Catalog.load(path)
        attempts, rows = [], []
        def forbidden(*args, **kwargs):
            attempts.append(1)
            raise AssertionError('Regression attempted model fallback')
        for index, base in enumerate(workload['questions']):
            for variant, suffix in workload['variants'].items():
                question = base+suffix
                before = time.perf_counter()
                row = {'question': question, 'variant': variant}
                try:
                    contract, receipt = S.interpret(catalog, question, model=forbidden)
                    row.update(compiled=True, interpreter=receipt['interpreter'], contract_id=contract['id'])
                    if execute:
                        population = sql_query.execute(path, {'query': contract['membership_query'], 'returns': 'patient_set'},
                                                       target=catalog.target)['patient_ids']
                        if contract['request'].get('analyses'):
                            result = V.execute(path, population, contract['request']['analyses'][0]['parameters'])['answer']
                            row['answer'] = {k: result.get(k) for k in ('status', 'value', 'count', 'table', 'computed_as', 'missing')}
                        else:
                            result = sql_query.execute(path, {'query': contract['query'], 'returns': contract['returns']},
                                                       target=catalog.target)
                            row['answer'] = {k: result[k] for k in ('value', 'rows') if k in result}
                        row['executed'] = True
                except Exception as exc:
                    row.update(error=f'{type(exc).__name__}: {exc}', executed=False)
                row['seconds'] = round(time.perf_counter()-before, 4)
                rows.append(row)
            if execute: print(f'Completed {index+1}/{len(workload["questions"])} question families', flush=True)
        q = 'Count patients over 65'
        age_contract, _ = S.interpret(catalog, q, model=forbidden)
        age_value = sql_query.execute(path, {'query':age_contract['query'], 'returns':'count'}, target=catalog.target)['value']
        with sqlite3.connect(f'file:{catalog.target}?mode=ro', uri=True) as db:
            cutoff = catalog.identity['as_of']
            direct_age = db.execute("""SELECT count(*) FROM patient WHERE
                CAST(strftime('%Y',?) AS INTEGER) - CAST(strftime('%Y',birth_date) AS INTEGER)
                - (strftime('%m-%d',?) < strftime('%m-%d',birth_date)) > 65 AND birth_date <= ?""",
                (cutoff,cutoff,cutoff)).fetchone()[0]
            direct_ed = db.execute("SELECT count(*) FROM utilization_event WHERE kind='ed_visit' AND event_date BETWEEN '2029-01-01' AND '2029-12-31'").fetchone()[0]
            claims_only = db.execute("""SELECT u.patient_id FROM utilization_event u JOIN patient p USING(patient_id)
                WHERE u.kind='ed_visit' AND u.event_date BETWEEN '2029-01-01' AND '2029-12-31'
                AND p.data_segment='claims_only' AND instr(u.sources,'claim')>0 LIMIT 1""").fetchone()
        ed_contract, _ = S.interpret(catalog, 'Count ED visits in 2029', model=forbidden)
        ed_value = sql_query.execute(path, {'query':ed_contract['query'], 'returns':'count'}, target=catalog.target)['value']
        ed_members = sql_query.execute(path, {'query':ed_contract['membership_query'], 'returns':'patient_set'}, target=catalog.target)['patient_ids']
        failures = [r for r in rows if r.get('error')]
        return {'schema':'shared-planner-correctness-regression/1', 'workload_sha256':hashlib.sha256(WORKLOAD.read_bytes()).hexdigest(),
                'source_identity':catalog.identity, 'policy_identity':S.policy_identity(),
                'base_questions':len(workload['questions']), 'questions_with_variants':len(rows),
                'compiled':sum(r.get('compiled',False) for r in rows), 'executed':sum(r.get('executed',False) for r in rows),
                'model_attempts':len(attempts), 'frontier_calls_per_100_questions':100*len(attempts)/len(rows),
                'frontier_calls_measurement':'All models forbidden; a fallback attempt fails its case. No paid calls are made.',
                'age':{'compiled':age_value,'independent_birth_date_sql':direct_age,'match':age_value==direct_age},
                'ed':{'compiled':ed_value,'direct_all_source':direct_ed,'match':ed_value==direct_ed,
                      'claims_only_member_included':bool(claims_only and claims_only[0] in ed_members)},
                'failures':failures, 'rows':rows,
                'qualification':'Fixed development regression, not independent clinical adjudication or a comparative benchmark.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--warehouse', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    warehouse = args.warehouse or Path(json.loads((ROOT/'config/agere/active_warehouse.json').read_text())['build'])/'stack'
    result = run(warehouse, args.execute)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('rows','source_identity')},indent=2))
    raise SystemExit(bool(result['failures']) or not result['age']['match'] or not all(result['ed'][k] for k in ('match','claims_only_member_included')))
