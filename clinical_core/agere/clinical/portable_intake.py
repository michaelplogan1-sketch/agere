"""Source intake → existing documentary state → immutable mapping review work.

This isolated synthetic scope never replaces or merges the active warehouse.
"""
from collections import Counter
import hashlib,json,sqlite3,tempfile
from pathlib import Path
from ...contracts import digest
from .. import portable_source as adapter
from ..warehouse import Warehouse
from .clinical_state import patient_state, code_identity as state_identity
from .state_contracts import definition,evaluate

VERSION = 'portable-intake-review/1'
ROOT = Path(__file__).resolve().parents[3]
REQUIREMENTS = {
    'renal.egfr': ('labs', [{'test','code'},{'value'},{'unit'},{'status'},{'collection_date','specimen_date'}]),
    'renal.creatinine': ('labs', [{'test','code'},{'value'},{'unit'},{'status'},{'collection_date','specimen_date'}]),
    'renal.uacr': ('labs', [{'test','code'},{'value'},{'unit'},{'status'},{'collection_date','specimen_date'}]),
    'nephrology_attendance': ('referrals', [{'service'},{'attendance_status'},{'attendance_date'}]),
    'financial_report': ('financial', [{'difficulty_paying_medical_bills'}]),
}


def code_identity():
    return digest([state_identity(), *[hashlib.sha256(Path(p).read_bytes()).hexdigest()
        for p in (__file__, adapter.__file__, Path(adapter.__file__).with_name('warehouse.py'))]])


def mapping_coverage(mapping, contract):
    rows = {}
    for requirement in definition(contract)['requirements']:
        key = requirement['binding']; spec = REQUIREMENTS.get(key)
        if spec is None:
            rows[key] = {'status':'unsupported_binding','reason':'Existing documentary engine has no implemented binding for this requirement.'}; continue
        table, groups = spec
        maps = [(kind,m) for kind,m in mapping['kinds'].items() if m['source_table'] == table]
        gaps = [{'source_kind':kind,'missing_any_of':[sorted(g) for g in groups if not g.intersection(m['fields'])]}
                for kind,m in maps]
        gaps = [g for g in gaps if g['missing_any_of']]
        rows[key] = {'status':'mapped' if maps and not gaps else 'mapping_missing',
                     'source_table':table, 'source_kinds':[k for k,_ in maps], 'gaps':gaps,
                     'meaning':'Field coverage only; terminology and clinical semantics are unvalidated.'}
    return rows


def preview(path, request):
    adapter.exact(request, ('package','mapping','contract','cutoff'), 'intake question')
    if request['contract'] not in ('renal_review','affordability'): raise ValueError('Portable renal or affordability documentary question required')
    cutoff = adapter.day(request['cutoff'])
    if cutoff is None: raise ValueError('Explicit observation cutoff required')
    request = json.loads(json.dumps(request, allow_nan=False))
    if cutoff > request['package']['as_of']: raise ValueError('Cutoff beyond declared source release')
    ident = code_identity()
    with tempfile.TemporaryDirectory(prefix='agere-portable-') as tmp:
        w = Warehouse(Path(tmp)/'intake.sqlite3')
        try:
            release, ingestion = adapter.ingest(w, request['package'],request['mapping'])
            patients,snapshot = w.snapshot(release,cutoff)
        finally: w.close()
    coverage = mapping_coverage(request['mapping'],request['contract'])
    report_id = digest([VERSION,ident,request]); rows = []; work = []; counts = Counter()
    def question(pid,kind,key,label,evidence):
        work.append({'id':digest([report_id,pid,kind,key]),'patient_id':pid,'kind':kind,'binding':key,
                     'label':label,'status':'unresolved','evidence':evidence,
                     'next_action':'Review mapping and source evidence; save a new mapping version to reassess.' if kind=='mapping' else
                                   'Acquire or review the missing documentary evidence; this report does not authorize care.'})
    for pid,p in sorted(patients.items()):
        state = patient_state(p['patient'],p['records'],[],cutoff)
        for key,c in coverage.items():
            if c['status']=='mapping_missing':
                state['bindings'][key] = {'status':'unsupported','value':None,'reason':'portable_source_mapping_incomplete',
                    'evidence':[], 'mapped_observations':state['bindings'][key], 'mapping':c,
                    'current_clinical_state_established':False}
        result = evaluate(definition(request['contract']),state['bindings']); counts[result['status']] += 1
        for r in result['requirements']:
            if r['result']!='satisfied':
                kind = 'mapping' if coverage[r['binding']]['status']=='mapping_missing' else 'documentary_evidence'
                question(pid,kind,r['binding'],r['label'],{'state':r['state'],'mapping':coverage[r['binding']]})
        for n,issue in enumerate(ingestion['issues']):
            if issue['patient_id']==pid:
                question(pid,'mapping','source_issue:'+str(n),issue['reason'].replace('_',' '),issue)
        rows.append({'patient_id':pid,'visible_record_count':len(p['records']),
                     'evaluation':result,'source_evidence':p['records']})
    if ident != code_identity(): raise ValueError('Code changed during intake review')
    result = {'id':report_id,'schema':VERSION,'synthetic':True,'request':request,'code_identity':ident,
        'ingestion':ingestion,'snapshot':snapshot,'mapping_coverage':coverage,'patients':rows,'work_items':work,
        'summary':{'roster_patients':len(patients),'patients_without_visible_records':sum(not p['records'] for p in patients.values()),
            'received_records':ingestion['received_records'],'mapped_records':ingestion['mapped_records'],
            'visible_records':snapshot['visible_records'],'source_mapping_issues':len(ingestion['issues']),
            'requirement_mapping_gaps':sum(c['status']=='mapping_missing' for c in coverage.values()),
            'documentary_status_counts':dict(counts),'unresolved_work_items':len(work)},
        'new_paid_api_calls':0,'clinical_mapping_validated':False,'clinical_eligibility_established':False,
        'active_warehouse_modified':False,
        'limits':['Bounded synthetic field-mapping conformance, not an EHR, FHIR or MEDS interoperability certification.',
                  'Only renal, referral attendance and reported-affordability bindings are mapped in this version.',
                  'Mapping declarations and supplied one-to-one identity links are not independently adjudicated.',
                  'Source payload and all roster patients are retained. Excluded and unmapped records do not establish absence of care.',
                  'Recorded dates never substitute for clinical or arrival dates; unknown dates remain unknown.',
                  'Saved mapping questions are local review work, not patient outreach, referrals or clinician approval.']}
    return {**result,'payload_digest':digest(result)}


def database(path):
    db = sqlite3.connect(Path(path)/'portable-intake.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
    return db


def save(path, request):
    result = preview(path,request); db = database(path)
    try:
        with db:
            old = db.execute('SELECT payload FROM reviews WHERE id=?',(result['id'],)).fetchone()
            if old and json.loads(old[0])!=result: raise ValueError('Immutable intake review collision')
            db.execute('INSERT OR IGNORE INTO reviews VALUES (?,?)',(result['id'],json.dumps(result,allow_nan=False)))
        return result
    finally: db.close()


def inspect(path, request):
    adapter.exact(request, ('id',), 'saved intake identity'); adapter.word(request['id']); db = database(path)
    try: row = db.execute('SELECT payload FROM reviews WHERE id=?',(request['id'],)).fetchone()
    finally: db.close()
    if row is None: raise ValueError('Unknown saved intake review')
    result = json.loads(row[0])
    if result.get('payload_digest') != digest({k:v for k,v in result.items() if k!='payload_digest'}):
        raise ValueError('Intake payload integrity mismatch')
    if result['id'] != digest([VERSION,result['code_identity'],result['request']]): raise ValueError('Intake identity mismatch')
    # A read-only replay never upgrades an older source or mapping automatically.
    return {**result,'current':result['code_identity']==code_identity()}


def listing(path, request):
    adapter.exact(request, (), 'intake listing'); db = database(path)
    try: rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM reviews ORDER BY id')]
    finally: db.close()
    ident = code_identity()
    return {'reviews':[{'id':r['id'],'source_id':r['request']['package']['source_id'],
        'mapping_version':r['request']['mapping']['version'],'contract':r['request']['contract'],
        'cutoff':r['request']['cutoff'],'summary':r['summary'],'current':r['code_identity']==ident} for r in rows]}


def compare(path, request):
    adapter.exact(request, ('before_id','after_id'), 'mapping comparison')
    before = inspect(path,{'id':request['before_id']}); after = inspect(path,{'id':request['after_id']})
    if not before['current'] or not after['current']: raise ValueError('Current mapping reviews required')
    if before['ingestion']['package_hash'] != after['ingestion']['package_hash']:
        raise ValueError('Exact same source population, cutoff and question required')
    for key in ('package','cutoff','contract'):
        if before['request'][key] != after['request'][key]: raise ValueError('Exact same source population, cutoff and question required')
    changes = []
    for b,a in zip(before['patients'],after['patients']):
        if b['patient_id'] != a['patient_id']: raise ValueError('Population mismatch')
        for old,new in zip(b['evaluation']['requirements'],a['evaluation']['requirements']):
            # Compare documentary state/value, keeping both full provenance chains.
            signature = lambda r:(r['result'],r['state']['status'],r['state'].get('value'),r['state'].get('reason'))
            if signature(old) != signature(new):
                changes.append({'patient_id':a['patient_id'],'binding':new['binding'],'before':old,'after':new})
    return {'schema':'portable-mapping-comparison/1',**request,'roster_patients':after['summary']['roster_patients'],
        'before_mapping':before['request']['mapping'],'after_mapping':after['request']['mapping'],
        'before_summary':before['summary'],'after_summary':after['summary'],'changed_requirements':changes,
        'old_work_items_retained':True,'clinical_change_inferred':False,
        'meaning':'Differences arise from supplied mapping changes applied to the exact same received source and cutoff.'}


def example(path, request):
    adapter.exact(request, (), 'example request')
    return json.loads((ROOT/'config/agere/portable_intake_example.json').read_text())
