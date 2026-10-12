"""Explicit source expansion and answer return for existing documentary targets."""
from pathlib import Path
import fcntl,hashlib,json,sqlite3
from ...contracts import digest
from . import received_reference_graph as graph,evidence_dispositions as dispositions
from . import record_context,question_context,source_synthesis,evidence_needs,care_capabilities as care

VERSION='reference-scope-work/1'
LIMITS={'requirement_resolved':False,'clinical_execution_authorized':False,'human_review_recorded':False,
        'current_patient_consent_established':False,'new_paid_api_calls':0,'external_actions':0}
def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),graph.identity()])
def database(path):
    db=sqlite3.connect(Path(path)/'reference-scope-work.sqlite3',timeout=30)
    for table in ('previews','selections','answers'):
        db.execute('CREATE TABLE IF NOT EXISTS '+table+'(id TEXT PRIMARY KEY,target TEXT NOT NULL,payload TEXT NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS '+table+'_target ON '+table+'(target)')
        for op in ('UPDATE','DELETE'):db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{op} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'Reference-scope history is immutable'); END")
    return db
def verified(row):
    if row.get('id')!=digest({k:v for k,v in row.items() if k!='id'}):raise ValueError('Reference-scope receipt integrity changed')
    return row
def save(path,table,target,row):
    if table not in ('previews','selections','answers'):raise ValueError('Unknown scope receipt')
    row={**row,'id':digest(row)};db=database(path)
    try:
        with db:db.execute('INSERT OR IGNORE INTO '+table+' VALUES (?,?,?)',(row['id'],digest(target),json.dumps(row,allow_nan=False)))
        prior=verified(json.loads(db.execute('SELECT payload FROM '+table+' WHERE id=?',(row['id'],)).fetchone()[0]))
        if row!=prior:raise ValueError('Reference-scope receipt differs from original')
    finally:db.close()
    return row
def load(path,table,key):
    if table not in ('previews','selections','answers'):raise ValueError('Unknown scope receipt')
    care.text(key,'reference-scope ID',64);db=database(path)
    try:row=db.execute('SELECT payload FROM '+table+' WHERE id=?',(key,)).fetchone()
    finally:db.close()
    if not row:raise ValueError('Unknown reference-scope receipt')
    return verified(json.loads(row[0]))
def source_binding(path,target):
    t=dispositions.resolve_target(path,target);records,meta=record_context.received(path,t['patient_id'])
    if t['as_of']>meta['snapshot']['as_of']:raise ValueError('Decision cutoff exceeds received snapshot')
    return t,records,{'target_hash':digest(t),'records_hash':digest(records),'source_digest':meta['source_digest'],
        'snapshot':meta['snapshot'],'implementation':identity()}
def preview(path,request):
    care.exact(request,'target record_ids');roots=care.strings(request['record_ids'],'record IDs',12,empty=False)
    t,records,binding=source_binding(path,request['target'])
    # Patient-fit roots stay under their original strict question/source contract.
    if request['target'].get('kind')=='patient_fit_question':
        from .patient_fit_work import scope_ids
        scope_ids(t,roots)
    component=graph.build(records,t['patient_id'],t['as_of'],roots)
    eligible=[r for r in records if graph.native.visible(r,t['as_of'])]
    old=question_context.select_sources(eligible,roots,t['as_of']);index={r['record_id']:r for r in records}
    rows=[{'record_id':k,'record_hash':digest(index[k]),'source_table':index[k]['source_table'],
        'source_date':index[k].get('event_date'),'available_date':index[k].get('available_date'),
        'was_in_historical_typed_scope':k in old['connected_record_ids'],'is_root':k in roots} for k in component['record_ids']]
    result={'schema':VERSION,'request':request,'target':t,'binding':binding,'original_question':t['question'],
        'root_record_ids':sorted(roots),'historical_typed_scope':old,'reference_component':component,'records':rows,
        'added_record_ids':sorted(set(component['record_ids'])-set(old['connected_record_ids'])),
        'historical_only_record_ids':sorted(set(old['connected_record_ids'])-set(component['record_ids'])),
        'status':'expansion_available_for_explicit_selection',**LIMITS}
    if source_binding(path,request['target'])[2]!=binding:raise ValueError('Source dependencies changed during reference inspection')
    return save(path,'previews',request['target'],result)
def inspect_preview(path,key):
    r=load(path,'previews',key)
    if source_binding(path,r['request']['target'])[2]!=r['binding']:raise ValueError('Reference-scope sources or target changed; prepare again')
    return r
def select(path,request):
    care.exact(request,'preview_id record_ids');p=inspect_preview(path,request['preview_id'])
    ids=care.strings(request['record_ids'],'record IDs',200,empty=False)
    if set(ids)-set(p['reference_component']['record_ids']):raise ValueError('Selected record is not in this exact reference preview')
    if not set(p['root_record_ids'])<=set(ids):raise ValueError('Retain the original question roots in the expanded scope')
    # The frozen context writer uses a deterministic temporary filename. Serialize
    # this extension's concurrent selections across both threads and processes.
    with (Path(path)/'reference-scope-context.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        inspect_preview(path,p['id'])
        c=record_context.create(path,p['target']['patient_id'],p['original_question'],ids)
    row={'schema':VERSION,'request':request,'target':p['target'],'preview_id':p['id'],'preview_hash':digest(p),
        'implementation':identity(),'context_id':c['id'],'context_hash':digest(c),'record_scope':c['record_scope'],
        'selected_record_ids':sorted(ids),'unselected_record_ids':sorted(set(p['reference_component']['record_ids'])-set(ids)),
        'unresolved_references':p['reference_component']['issues'],'omissions':c['omissions'],'dispatch':c['dispatch'],
        'status':'source_context_prepared' if c['dispatch']['source_review_allowed'] and not c['omissions'] else 'selected_scope_incomplete',**LIMITS}
    if inspect_preview(path,p['id'])!=p or digest(record_context.inspect(path,c['id']))!=row['context_hash']:raise ValueError('Reference scope changed during selection')
    return save(path,'selections',p['request']['target'],row)
def inspect_selection(path,key):
    row=load(path,'selections',key)
    if row['implementation']!=identity() or digest(inspect_preview(path,row['preview_id']))!=row['preview_hash']:raise ValueError('Reference selection dependencies changed')
    if digest(record_context.inspect(path,row['context_id']))!=row['context_hash']:raise ValueError('Reference selection context changed')
    return row
def answer_binding(path,request):
    care.exact(request,'selection_id answer_id');row=inspect_selection(path,request['selection_id']);record_context.verified(path,row['context_id'])
    answer=source_synthesis.inspect(path,request['answer_id'])
    if answer.get('status')!='complete' or (len(answer.get('passes',[]))>1 and not answer.get('reconciliation',{}).get('groups')):raise ValueError('Complete saved interpretation and required reconciliation needed')
    if answer['context_id']!=row['context_id'] or answer['patient_id']!=row['target']['patient_id']:raise ValueError('Interpretation must use this exact reference selection and patient')
    return row,answer,{'selection_hash':digest(row),'answer_hash':digest(answer)}
def attach(path,request):
    row,answer,binding=answer_binding(path,request);needs=evidence_needs.prepare(path,answer['id'],[])
    result={'schema':VERSION,'request':request,'target':row['target'],'implementation':identity(),'binding':binding,
        'context_id':row['context_id'],'question':row['target']['question'],'followup_needs_id':needs['id'],
        'summary':{'findings':len(answer['findings']),'exact_citations':sum(len(f['citations']) for f in answer['findings']),
            'review_questions':len(answer['review_steps']),**needs['summary']},'status':'machine_interpretation_attached_for_review',**LIMITS}
    if answer_binding(path,request)[2]!=binding:raise ValueError('Reference answer changed during attachment')
    return save(path,'answers',row['target']['target'],result)
def listing(path,request):
    care.exact(request,'target');key=digest(request['target']);db=database(path)
    try:rows={table:[verified(json.loads(r[0])) for r in db.execute('SELECT payload FROM '+table+' WHERE target=? ORDER BY rowid',(key,))] for table in ('previews','selections','answers')}
    finally:db.close()
    if not any(rows.values()):dispositions.resolve_target(path,request['target'])
    for table,items in rows.items():
        for row in items:
            reasons=[]
            try:
                if table=='previews':inspect_preview(path,row['id'])
                elif table=='selections':inspect_selection(path,row['id'])
                else:
                    if row['implementation']!=identity() or answer_binding(path,row['request'])[2]!=row['binding']:raise ValueError('Reference answer dependencies changed')
                    evidence_needs.inspect(path,row['followup_needs_id'])
            except (ValueError,KeyError,FileNotFoundError) as exc:reasons.append(str(exc))
            row['current']=not reasons;row['stale_reasons']=reasons
    return {'schema':VERSION,**request,**rows,**LIMITS}
