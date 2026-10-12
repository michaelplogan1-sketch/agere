"""Source-first training acquisition; family protections precede sampling.

The generator ancestry/split register is isolated preparation metadata. Hidden
clinical events, outcomes and answers never become features, targets or labels.
"""
from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json
import math
import re
import sqlite3
import time

from ...contracts import digest
from ..narrative_sources import text_fields
from . import semantic_reader as reader, semantic_learning as learning, longitudinal

VERSION='source-learning-queue/1'
LIMITS=[
    'All data are synthetic. This is a training acquisition queue, not a care or outreach list.',
    'Priority is an engineering sampling heuristic, not clinical severity or probability of error.',
    'No-annotation source fields are extraction-audit candidates, not confirmed retrieval misses.',
    'The score-independent sample covers unique source templates, not population prevalence.',
    'Text normalization is a duplicate heuristic; semantic paraphrases can remain in different groups.',
    'Existing annotation models may have encountered these sources. This is development acquisition, not independent validation.',
    'Machine agreement is not a reviewed label. No clinical action or model release follows automatically.',
]


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def write(path,value):Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def normalize(text):
    # Compare wording rather than simulator names/IDs on document headers.
    # This affects selection groups only; the original source reaches readers.
    text=re.sub(r'(?im)^(?:patient|patient id|dob):[^\n]*', '[source header]',text)
    text=re.sub(r'\bECO-[A-Fa-f0-9]+\b','[synthetic id]',text)
    text=re.sub(r'\bSynthetic Person [A-Fa-f0-9]+\b','[synthetic person]',text)
    return ' '.join(re.sub(r'\d+','N',text).casefold().split())


def import_lineage(stack,release,output):
    """Extract only patient ID, ancestry and split from the matching generator.

    This offline boundary reads ancestry metadata stored alongside hidden data;
    all other keys are discarded without inspection or export. Runtime model
    requests continue to use received warehouse content only.
    """
    stack,release,output=Path(stack),Path(release),Path(output)
    meta=json.loads((stack/'run.json').read_text());manifest=json.loads((release/'manifest.json').read_text())
    if manifest.get('schema')=='single-healthcare-warehouse/1':
        from .original_source_lineage import prepare
        return prepare(stack,release,output)
    if meta.get('synthetic') is not True or manifest.get('synthetic') is not True:raise ValueError('Synthetic lineage sources required')
    observed={k:v['sha256'] for k,v in manifest['files'].items() if k.startswith('observed/')}
    if digest(observed)!=meta['snapshot']['release']:raise ValueError('Lineage release does not match warehouse release')
    source=release/'truth/patients.jsonl'
    if sha(source)!=manifest['files']['truth/patients.jsonl']['sha256']:raise ValueError('Generator ancestry register changed')
    database=sqlite3.connect((stack/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    try:ids={r[0] for r in database.execute('SELECT id FROM patients')}
    finally:database.close()
    patient_families={};family_partitions={}
    with source.open() as f:
        for line in f:
            raw=json.loads(line)
            pid,family,split=(raw[k] for k in ('patient_id','source_family','split'))
            del raw  # No clinical/latent data retained.
            if not isinstance(pid,str) or not isinstance(family,str) or not family or pid in patient_families:raise ValueError('Invalid ancestry register')
            if split not in ('train','calibration','test'):raise ValueError('Unknown generator partition')
            part={'train':'train','calibration':'calibration','test':'validation'}[split]
            if family in family_partitions and family_partitions[family]!=part:raise ValueError('Source family spans generator partitions')
            patient_families[pid]=family;family_partitions[family]=part
    if set(patient_families)!=ids:raise ValueError('Ancestry register does not cover exact warehouse roster')
    output.mkdir(parents=True,exist_ok=False)
    policy={'lineage_source':str(source),'patient_families':patient_families,'family_partitions':family_partitions,'additional_protected_families':[]}
    learning.validate_policy(policy);write(output/'policy.json',policy)
    report={'synthetic':True,'warehouse_release':meta['snapshot']['release'],'generator_manifest_sha256':sha(release/'manifest.json'),
        'ancestry_source_sha256':sha(source),'policy_sha256':sha(output/'policy.json'),
        'extracted_fields':['patient_id','source_family','split'],'patients':len(ids),'families':len(family_partitions),
        'partition_patient_counts':dict(Counter(family_partitions[f] for f in patient_families.values())),
        'hidden_clinical_labels_exported':False,'purpose':'Offline split/protection metadata only; not model input'}
    write(output/'manifest.json',report);return report


def lineage(root,meta):
    root=Path(root);m=json.loads((root/'manifest.json').read_text());p=json.loads((root/'policy.json').read_text())
    if m.get('synthetic') is not True or m['warehouse_release']!=meta['snapshot']['release'] or sha(root/'policy.json')!=m['policy_sha256']:
        raise ValueError('Lineage policy/source mismatch')
    return learning.validate_policy(p),m


def eligibility(patient,policy,protected):
    family=policy['patient_families'].get(patient)
    if patient in protected['patient_ids'] or family in set(protected['family_ids'])|set(policy['additional_protected_families']):return 'protected_evaluation_family'
    if not family:return 'missing_patient_family'
    if policy['family_partitions'].get(family)!='train':return 'nontraining_partition'
    return None


def select(candidates,size):
    if type(size) is not int or not 4<=size<=500:raise ValueError('Queue size must be 4–500')
    # One stable representative per normalized full-field/concept template.
    groups={}
    for r in candidates:
        if r['group'] not in groups or r['id']<groups[r['group']]['id']:groups[r['group']]=r
    rows=list(groups.values());selected=[];used=set();patients=Counter();families=Counter()
    def add(r,lane):
        if r['id'] in used or patients[r['patient_id']]>=2 or families[r['patient_family']]>=3:return False
        selected.append({**r,'selection_lane':lane});used.add(r['id']);patients[r['patient_id']]+=1;families[r['patient_family']]+=1;return True
    def round_robin(pool,slots,lane,priority=False):
        buckets=defaultdict(list)
        for r in sorted(pool,key=lambda r:(-r['priority'] if priority else 0,digest([lane,r['group']]))):
            buckets[(r['stream'],r['target']['concept'] if r['target'] else 'no_annotation')].append(r)
        n=0
        while n<slots and any(buckets.values()):
            for key in sorted(buckets):
                while buckets[key] and n<slots:
                    if add(buckets[key].pop(0),lane):n+=1;break
    audit=math.ceil(size/4)
    round_robin([r for r in rows if not r['target']],audit,'extraction_audit')
    round_robin([r for r in rows if r['target']],audit,'score_independent_audit')
    round_robin([r for r in rows if r['target']],size-len(selected),'interpretation_priority',True)
    if len(selected)<size:round_robin(rows,size-len(selected),'diversity_fill')
    return selected,len(groups)


def build(stack,lineage_root,output,*,size=96):
    stack,lineage_root,output=Path(stack),Path(lineage_root).resolve(),Path(output)
    meta=json.loads((stack/'run.json').read_text());policy,lm=lineage(lineage_root,meta);protected=learning.protections()
    if meta.get('synthetic') is not True:raise ValueError('Synthetic warehouse required')
    if output.exists():raise ValueError('Fresh queue artifact required')
    started=time.perf_counter();events,em=longitudinal.connect(stack)
    src=sqlite3.connect((stack/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    index=defaultdict(list);counts=Counter();candidates={};unmapped=Counter()
    # The event index exposes older machine proposals. They are sampling hints,
    # never reference labels for the new task.
    try:
        for raw, in events.execute("SELECT payload FROM events WHERE kind='text'"):
            e=json.loads(raw);reason=eligibility(e['patient_id'],policy,protected)
            counts['annotation_occurrences_scanned']+=1
            if reason:counts['annotation_excluded:'+reason]+=1;continue
            s=e['source'];index[(s['record_id'],s['field'])].append(e)
        saved=reader.db(stack)
        try:
            existing=defaultdict(list)
            for raw, in saved.execute('SELECT payload FROM readings'):
                r=json.loads(raw)
                if r['reader_identity']==reader.identity():existing[digest(r['target'])].append(r)
        finally:saved.close()
        for pid,raw in src.execute('SELECT patient,payload FROM records ORDER BY id'):
            counts['records_scanned']+=1;reason=eligibility(pid,policy,protected)
            if reason:counts['records_excluded:'+reason]+=1;continue
            r=json.loads(raw)
            if r.get('synthetic') is not True or r.get('identity_status')!='matched' or r['available_date']>meta['snapshot']['as_of']:raise ValueError('Invalid visible source record')
            for part in text_fields(r):
                text=part['text'];field=part['field'];counts['eligible_received_text_fields']+=1
                annotated=index.get((r['record_id'],field),[])
                if not annotated:counts['eligible_fields_without_annotations']+=1;unmapped[r['source_table']]+=1
                for event in annotated or [None]:
                    target=None;reasons=[];priority=0
                    if event:
                        s=event['source'];a,b=s['start'],s['end']
                        if event['record_hash']!=digest(r) or not 0<=a<b<=len(text) or text[a:b]!=s['quote']:raise ValueError('Stale annotation source/span')
                        target={'patient_id':pid,'record_id':r['record_id'],'field':field,'start':a,'end':b,'concept':event['concept']}
                        prior=[p for p in existing.get(digest(target),[]) if p['source_hash']==event['record_hash']]
                        disagreements=[axis for axis in reader.AXES if len({p['reading']['labels'][axis] for p in prior})>1]
                        if disagreements:priority+=3;reasons.append('saved_reader_disagreement:'+','.join(disagreements))
                        if event.get('review_routing',{}).get('priority')=='context_conflict':priority+=2;reasons.append('older_reader_context_conflict')
                        if event.get('copied_history'):priority+=1;reasons.append('copied_history')
                        if event.get('reference_status') in ('invalid_or_future_reference','conflicting_references'):priority+=1;reasons.append('unresolved_literal_date')
                    concept=target['concept'] if target else 'unannotated_received_field'
                    group=digest([concept,normalize(text)])
                    item={'patient_id':pid,'patient_family':policy['patient_families'][pid],'partition':'train',
                        'record_id':r['record_id'],'field':field,'stream':r['source_table'],'source_hash':digest(r),
                        'target':target,'group':group,'text_characters':len(text),'priority':priority,'priority_reasons':reasons,
                        'annotation_id':event['id'] if event else None,'machine_hint':{k:event[k] for k in ('concept','assertion','subject','time','relation')} if event else None,
                        'labels':None,'human_reviewed':False,'training_eligible':False}
                    item['id']=digest([VERSION,meta['source_digest'],item])
                    counts['candidate_bindings']+=1
                    # Streamed deduplication keeps memory proportional to templates.
                    if group not in candidates or item['id']<candidates[group]['id']:candidates[group]=item
        selected,unique=select(candidates.values(),size)
        output.mkdir(parents=True)
        write(output/'items.json',selected)
        report={'schema':VERSION,'synthetic':True,'source_identity':{k:meta[k] for k in ('source_digest','snapshot')},
            'reader_identity':reader.identity(),'annotation_identity':em['reader_model_identity'],'code_sha256':sha(__file__),
            'lineage_root':str(lineage_root),'lineage_policy_sha256':lm['policy_sha256'],'protection_register':protected,
            'counts':dict(counts),'requested':size,'selected':len(selected),'unique_eligible_templates':unique,
            'selected_patients':len({r['patient_id'] for r in selected}),'selected_families':len({r['patient_family'] for r in selected}),
            'selection_counts':dict(Counter(r['selection_lane'] for r in selected)),
            'selected_streams':dict(Counter(r['stream'] for r in selected)),
            'selected_concepts':dict(Counter(r['target']['concept'] for r in selected if r['target'])),
            'received_fields_without_annotations_by_stream':dict(unmapped),'files':{'items.json':sha(output/'items.json')},
            'method':'Full source scan; exclude reserved and nontraining ancestry before sampling; normalized full-field/concept deduplication; source/concept round-robin with independent audit lanes; at most two fields per patient and three per source family.',
            'training_labels_created':0,'new_paid_api_calls':0,'elapsed_seconds':time.perf_counter()-started,'limits':LIMITS}
        write(output/'run.json',report)
        if learning.protections()!=protected:raise ValueError('Protection register changed during build')
        # Link only after all source and policy checks succeed.
        attach=stack/'learning-queue-link.json';tmp=attach.with_suffix('.tmp')
        write(tmp,{'path':str(output.resolve()),'run_sha256':sha(output/'run.json')});tmp.replace(attach)
        return report
    finally:src.close();events.close()


def connected(stack):
    stack=Path(stack);link=json.loads((stack/'learning-queue-link.json').read_text());root=Path(link['path'])
    run=json.loads((root/'run.json').read_text());meta=json.loads((stack/'run.json').read_text())
    if sha(root/'run.json')!=link['run_sha256'] or sha(__file__)!=run['code_sha256']:raise ValueError('Learning queue artifact/code changed; rebuild required')
    if run['source_identity']!={k:meta[k] for k in ('source_digest','snapshot')} or run['reader_identity']!=reader.identity():raise ValueError('Learning queue source or reader changed')
    if run['protection_register']!=learning.protections():raise ValueError('Learning protections changed; rebuild queue')
    p,m=lineage(run['lineage_root'],meta)
    if m['policy_sha256']!=run['lineage_policy_sha256'] or sha(root/'items.json')!=run['files']['items.json']:raise ValueError('Learning queue inputs changed')
    return root,run,p,json.loads((root/'items.json').read_text())


def target_db(root):
    db=sqlite3.connect(Path(root)/'marked-targets.sqlite3',timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS targets(id TEXT PRIMARY KEY,item TEXT,payload TEXT)')
    return db


def inspect(stack,item_id):
    root,run,policy,items=connected(stack)
    item=next((i for i in items if i['id']==item_id),None)
    if item is None:raise ValueError('Unknown learning queue item')
    if eligibility(item['patient_id'],policy,run['protection_register']):raise ValueError('Item no longer eligible for training acquisition')
    probe=item['target'] or {'patient_id':item['patient_id'],'record_id':item['record_id'],'field':item['field'],'start':0,'end':1,'concept':'source audit'}
    record,text,meta=reader.source(stack,probe)
    if digest(record)!=item['source_hash']:raise ValueError('Learning queue received source changed')
    database=target_db(root)
    try:marked=[json.loads(r[0]) for r in database.execute('SELECT payload FROM targets WHERE item=? ORDER BY id',(item_id,))]
    finally:database.close()
    targets=([item['target']] if item['target'] else [])+[r['target'] for r in marked]
    readings=[];db=reader.db(stack)
    try:
        keys={digest(t) for t in targets}
        for raw, in db.execute('SELECT payload FROM readings WHERE patient=?',(item['patient_id'],)):
            r=json.loads(raw)
            if digest(r['target']) in keys and r['source_hash']==item['source_hash'] and r['reader_identity']==reader.identity():readings.append(reader.inspect(stack,r['id']))
    finally:db.close()
    return {'synthetic':True,'item':item,'text':text[:100000],'text_complete':len(text)<=100000,'source_text_characters':len(text),
        'marked_targets':marked,'readings':readings,'target_options':targets,'source_verified':True,
        'automatic_training_eligible':False,'new_paid_api_calls':0,'limits':LIMITS}


def mark_target(stack,payload):
    if not isinstance(payload,dict) or set(payload)!={'item_id','quote','concept','occurrence'}:raise ValueError('Explicit item, quote, concept and occurrence required')
    quote,concept,n=payload['quote'],payload['concept'],payload['occurrence']
    if not isinstance(quote,str) or not 1<=len(quote)<=2000 or not isinstance(concept,str) or not 1<=len(concept.strip())<=150 or type(n) is not int or not 0<=n<500:
        raise ValueError('Bounded exact quote, concept and zero-based occurrence required')
    inspected=inspect(stack,payload['item_id']);item=inspected['item']
    probe={'patient_id':item['patient_id'],'record_id':item['record_id'],'field':item['field'],'start':0,'end':1,'concept':concept}
    _,text,_=reader.source(stack,probe);matches=[m.start() for m in re.finditer('(?='+re.escape(quote)+')',text)]
    if n>=len(matches):raise ValueError('Quote occurrence not found in received source')
    target={**probe,'start':matches[n],'end':matches[n]+len(quote)}
    row={'id':digest([VERSION,item['id'],target,item['source_hash']]),'target':target,'quote':quote,'quote_occurrences':len(matches),
        'source_hash':item['source_hash'],'labels_created':False,'authority':'Locally marked source target, not a source interpretation review'}
    root,_,_,_=connected(stack);db=target_db(root)
    try:
        with db:db.execute('INSERT OR IGNORE INTO targets VALUES (?,?,?)',(row['id'],item['id'],json.dumps(row)))
    finally:db.close()
    return row


def list_items(stack,*,lane='',stream='',offset=0):
    if type(offset) is not int or not 0<=offset<=100000 or not isinstance(lane,str) or not isinstance(stream,str):raise ValueError('Bounded queue filters required')
    _,run,_,items=connected(stack)
    selected=[r for r in items if (not lane or r['selection_lane']==lane) and (not stream or r['stream']==stream)]
    db=reader.db(stack)
    try:
        counts=Counter()
        for raw, in db.execute('SELECT payload FROM readings'):
            r=json.loads(raw)
            if r['reader_identity']==reader.identity():counts[(digest(r['target']),r['source_hash'])]+=1
    finally:db.close()
    return {'synthetic':True,'run':run,'rows':[{**r,'saved_readings':counts[(digest(r['target']),r['source_hash'])] if r['target'] else 0} for r in selected[offset:offset+12]],
        'total':len(selected),'offset':offset,'limit':12,'new_paid_api_calls':0}


def suggest(stack,item_id,provider,*,allow_live=False):
    if provider not in ('perplexity','jev','student_lexical','student_minilm'):raise ValueError('Unknown training suggestion provider')
    r=inspect(stack,item_id)
    if not r['item']['target']:raise ValueError('An extraction-audit field needs a deliberately marked target before interpretation')
    if provider.startswith('student_'):
        from .semantic_student import read
        return read(stack,r['item']['target'],provider)
    return reader.read(stack,r['item']['target'],provider,allow_live=allow_live)
