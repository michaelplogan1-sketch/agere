"""Canonical saved-answer view over a complete source-plan answer selection.

No additional interpretation is invented by joining groups. An optional existing
cross-group reconciliation can supply conclusions; otherwise that uncertainty
remains explicit. Parent answers and their local reference scopes are preserved.
"""
import copy
import hashlib
import json
from pathlib import Path
from collections import Counter
from ...contracts import digest
from . import care_capabilities as care, source_plan_answers as A, source_plan_reconciliation as R

VERSION='complete-source-plan-answer/1'

def identity():return digest([VERSION,A.identity(),R.identity(),hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])

def database(path):
    db=care.db_open(path)
    db.executescript('''CREATE TABLE IF NOT EXISTS complete_plan_answers(id TEXT PRIMARY KEY,context TEXT NOT NULL,payload TEXT NOT NULL);
      CREATE TRIGGER IF NOT EXISTS complete_plan_answers_update BEFORE UPDATE ON complete_plan_answers BEGIN SELECT RAISE(ABORT,'Immutable complete-plan answer'); END;
      CREATE TRIGGER IF NOT EXISTS complete_plan_answers_delete BEFORE DELETE ON complete_plan_answers BEGIN SELECT RAISE(ABORT,'Immutable complete-plan answer'); END;''')
    return db

def exists(path,key,*,context=False):
    care.text(key,'saved answer identity',64)
    db=database(path)
    try:return db.execute('SELECT 1 FROM complete_plan_answers WHERE '+('context' if context else 'id')+'=?',(key,)).fetchone() is not None
    finally:db.close()

def assemble(result,reconciliation=None):
    snap=result['snapshot']; plan=result['plan']; sources={};evidence=[];findings=[];retained=[];passes=[];unanswered=[];steps=[];usage=[];units=[];lineage=[]
    if sorted(g['context_id'] for g in snap['groups'])!=sorted(g['context_id'] for g in plan['groups']):
        raise ValueError('Every declared source-plan group must have exactly one saved answer')
    if len(result['answers'])!=len(snap['groups']) or len(result['contexts'])!=len(snap['groups']):
        raise ValueError('Every declared group must have one answer and context')
    if any(c['model_context']['snapshot']!=plan['preview']['snapshot'] for c in result['contexts']):
        raise ValueError('Source-plan snapshots differ')
    for g,a in zip(snap['groups'],result['answers']):
        c=next(c for c in result['contexts'] if c['id']==g['context_id'])
        if a['id']!=g['answer_id'] or a['context_id']!=c['id'] or a['patient_id']!=snap['patient_id'] or a['status']!='complete':raise ValueError('Group answer identity differs')
        if a['context_digest']!=digest(c) or a['question']!=c['parameters']['question']:
            raise ValueError('Group context binding differs')
        if set(sources)&set(c['model_context']['sources']):raise ValueError('Source-plan groups overlap')
        sources.update(copy.deepcopy(c['model_context']['sources']));evidence.extend(copy.deepcopy(c['model_context']['evidence']))
        scope={'group_number':g['group_number'],'answer_id':a['id'],'context_id':c['id']}
        prefix='g'+str(g['group_number'])+':'
        # Legacy reconciled answers keep earlier p* claims in the same list.
        # Preserve their audit role; only current group conclusions feed planning.
        current=[f for f in a['findings'] if not (a['coverage'].get('reconciliation_groups') and f.get('stage','').startswith('p'))]
        earlier=[f for f in a['findings'] if f not in current]+a.get('retained_source_findings',[])
        ids={f['id']:prefix+f['id'] for f in current}
        for items,destination in ((current,findings),(earlier,retained)):
            for f in items:
                out=copy.deepcopy(f);out.update(id=prefix+f['id'],reference_scope=scope,original_finding_id=f['id'])
                destination.append(out)
        for p in a.get('passes',[]):
            p=copy.deepcopy(p);p['reference_scope']={**scope,'original_pass_number':p['pass_number']};p['pass_number']=len(passes)+1;passes.append(p)
        units.extend(a['coverage']['unassessed_units'])
        unanswered.extend('Group '+str(g['group_number'])+': '+u for u in a.get('unanswered',[]))
        for s in a.get('review_steps',[]):
            if set(s['finding_ids'])-set(ids):continue # original retained-pass steps stay in the parent
            steps.append({**copy.deepcopy(s),'id':prefix+s.get('id',digest(s)),'finding_ids':[ids[x] for x in s['finding_ids']],
                          'reference_scope':scope})
        usage.extend(a['usage'] if isinstance(a['usage'],list) else [a['usage']])
        lineage.append({**scope,'answer_digest':digest(a),'context_digest':digest(c),'source_units':g['source_units']})
    record_ids=sorted(sources)
    if record_ids!=sorted(plan['preview']['selected_record_ids']) or len(evidence)!=snap['source_units']:
        raise ValueError('Complete-plan source accounting differs')
    if len({u['id'] for u in evidence})!=len(evidence):raise ValueError('Repeated source units across groups')
    if any(s['source']['patient_id']!=snap['patient_id'] for s in sources.values()):raise ValueError('Cross-patient complete-plan evidence')
    if len(findings)!=len({f['id'] for f in findings}):raise ValueError('Duplicate scoped finding identity')
    for f in findings+retained:
        for citation in f['citations']:
            if sources.get(citation['source']['record_id'],{}).get('source')!=citation['source'] or citation.get('citation_verified') is not True:
                raise ValueError('Group finding citation differs from received source')
    streams=sorted({s['source']['source_table'] for s in sources.values()})
    record_scope={'record_ids':record_ids,'selected_records':len(record_ids),'excluded_records':snap['excluded_records'],
        'excluded_from_selected_categories':sum(plan['preview']['excluded_by_category'].get(s,0) for s in streams),
        'plan_id':plan['id'],'analysis_window':snap['analysis_window']}
    context={'schema':VERSION,'patient_id':snap['patient_id'],'parameters':{'question':snap['original_question'],'streams':streams},
        'record_scope':record_scope,'omissions':[],'dispatch':{'source_review_allowed':False,'reason':'Use original bounded group contexts; this view never sends a combined chart.'},
        'model_context':{'snapshot':plan['preview']['snapshot'],'sources':sources,'evidence':evidence},
        'source_plan_id':plan['id'],'source_plan_digest':digest(plan),'snapshot_id':snap['id'],'group_lineage':lineage,'rule_identity':identity()}
    context['id']=digest(context)
    cross='Cross-group relationships remain unassessed. Group answers do not establish a combined clinical, medication-use or benefit conclusion.'
    if reconciliation:
        if reconciliation['snapshot_id']!=snap['id'] or reconciliation['plan_id']!=plan['id'] or reconciliation.get('status')!='complete':
            raise ValueError('Cross-group reconciliation belongs to another complete answer selection')
        retained=findings+retained;findings=copy.deepcopy(reconciliation['result']['findings'])
        unanswered.extend(reconciliation['result']['unanswered']);steps=copy.deepcopy(reconciliation['result']['review_steps'])
        usage.append(reconciliation['usage'])
    elif len(snap['groups'])>1:unanswered.append(cross)
    counts=Counter()
    for a in result['answers']:counts.update(a['coverage'].get('unit_dispositions',{}))
    answer={'schema':VERSION,'status':'complete','synthetic':True,'patient_id':snap['patient_id'],'question':snap['original_question'],
        'context_id':context['id'],'context_digest':digest(context),'snapshot_id':snap['id'],'source_plan_id':plan['id'],
        'analysis_window':snap['analysis_window'],'group_lineage':lineage,'rule_identity':identity(),
        'record_scope':record_scope,'source_scope':streams,'findings':findings,'retained_source_findings':retained,
        'unanswered':list(dict.fromkeys(unanswered)),'review_steps':steps,'passes':passes,
        'coverage':{'received_units':snap['source_units'],'submitted_units':snap['source_units'],'model_accounted_units':snap['source_units'],
            'unassessed_units':units,'complete_declared_scope':True,'complete_chart':False,'unit_dispositions':dict(counts),
            'cross_group_relations_assessed':bool(reconciliation),'group_answers':len(snap['groups']),'reconciliation_groups':0},
        'dependencies':{'group_answers':lineage,'source_plan_digest':digest(plan),'answer_selection_digest':digest(snap)},
        'cross_group_reconciliation_id':reconciliation['id'] if reconciliation else None,
        'model':'saved group interpretations','usage':usage,'token_check':{'input_tokens':sum(a['token_check']['input_tokens'] for a in result['answers'])+(reconciliation['token_check']['input_tokens'] if reconciliation else 0)},
        'new_paid_api_calls':0,'cache_hit':True,'clinical_execution_authorized':False,
        'limits':['Complete means every declared group has an accounted saved answer; it does not mean the whole chart or clinical completeness.',
                  'Original group prose and local reference aliases retain their group/answer scope. Equal aliases across groups are not identical sources.',
                  'Composition performs no new inference. Unknowns and source exclusions remain explicit.',
                  'No clinical validation, current benefits, service eligibility, consent or external execution authority is established.']}
    answer['id']=digest(answer)
    return {'answer':answer,'context':context}

def build(retriever,request):
    care.exact(request,'snapshot_id'+(' reconciliation_id' if 'reconciliation_id' in request else ''))
    result=A.inspect(retriever,{'id':request['snapshot_id']})
    reconciliation=R.inspect(retriever,{'id':request['reconciliation_id']}) if request.get('reconciliation_id') else None
    return assemble(result,reconciliation)

def prepare(retriever,request):
    result=build(retriever,request);saved={'request':request,**result};saved['digest']=digest(saved)
    if build(retriever,request)!=result:raise ValueError('Source dependencies changed during composition')
    db=database(retriever.path)
    try:
        with db:db.execute('INSERT OR IGNORE INTO complete_plan_answers VALUES (?,?,?)',(result['answer']['id'],result['context']['id'],json.dumps(saved,allow_nan=False)))
    finally:db.close()
    return result['answer']

def inspect(retriever,key,*,context=False):
    db=database(retriever.path)
    try:row=db.execute('SELECT payload FROM complete_plan_answers WHERE '+('context' if context else 'id')+'=?',(key,)).fetchone()
    finally:db.close()
    if not row:raise ValueError('Unknown complete-plan answer')
    saved=json.loads(row[0])
    if saved['digest']!=digest({k:v for k,v in saved.items() if k!='digest'}):raise ValueError('Complete-plan answer integrity changed')
    result=build(retriever,saved['request'])
    if result!={k:saved[k] for k in ('answer','context')}:raise ValueError('Complete-plan answer source or rule dependencies changed')
    return result['context' if context else 'answer']

def binding(retriever,answer):
    context=inspect(retriever,answer['context_id'],context=True)
    versions={'snapshot':context['model_context']['snapshot'],'context_digest':digest(context),
        'records':{k:s['source']['record_hash'] for k,s in context['model_context']['sources'].items()},
        'source_plan_id':context['source_plan_id'],'source_plan_digest':context['source_plan_digest'],
        'answer_selection_id':answer['snapshot_id'],'group_lineage':answer['group_lineage']}
    return answer,context,{'question':answer['question'],'patient_id':answer['patient_id'],'context_id':context['id'],
        'answer_id':answer['id'],'answer_digest':digest(answer),'analysis_window':answer['analysis_window'],
        'source_versions':versions,'record_scope':answer['record_scope'],'answer_coverage':answer['coverage']}
