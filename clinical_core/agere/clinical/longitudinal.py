"""Source-bound event sequences and competing documentation accounts.

Recorded timestamps, explicit clinical dates and dates referenced in text remain
separate clocks. This ledger describes evidence; it does not infer unobserved care.
"""
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
import hashlib, json, re, sqlite3, time

from ...contracts import digest
from ..narrative_sources import text_fields
from .clinical_events import iso, CLINICAL_FIELDS

VERSION='longitudinal-observations/1'
CLOCKS={'source_event':'source_date','arrival':'arrival_date','clinical':'clinical_date','reference':'reference_date'}
FILTERS={'kind','stream','event_type','concept','assertion','subject','relation','time'}
LIMITS=[
 'Sequences describe recorded evidence, not causes or verified clinical pathways.',
 'Source, arrival, explicit clinical and narrative-reference dates are different clocks; no implicit substitution is performed.',
 'Narrative assertions remain unvalidated machine annotations. Repeated text is not independent corroboration.',
 'Absent observations can reflect missing care, outside care, lag, extraction failure or an unmapped source; these alternatives are not assigned invented probabilities.',
 'Admission/discharge links without explicit encounter identity are provisional within-source pairs, not adjudicated stays or readmissions.',
 'This snapshot does not reconstruct superseded or quarantined historical records, even with an earlier availability cutoff.',
]


def code_identity():
    from .. import narrative_sources
    from .clinical_events import code_identity as event_identity
    return digest([*[hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (__file__,narrative_sources.__file__)],event_identity()])


def source(record):
    return {k:record.get(k) for k in ('record_id','patient_id','source_table','source_system','organization_id','event_date','available_date','status')}


def record_events(r,as_of,related=None):
    """One event per role; admission and discharge dates are not mutual conflicts."""
    stream=r['source_table'];roles=[]
    if stream=='adt':
        if r.get('event') in ('A01','admission') or 'admission_date' in r:roles.append(('admission',('admission_date',)))
        if r.get('event') in ('A03','discharge') or 'discharge_date' in r:roles.append(('discharge',('discharge_date',)))
    if not roles:roles=[(stream,CLINICAL_FIELDS.get(stream,()))]
    for role,fields in roles:
        observed={k:r[k] for k in fields if r.get(k) is not None}
        valid=bool(observed) and all(iso(v) and v<=as_of for v in observed.values()) and len(set(observed.values()))==1
        clinical=next(iter(observed.values())) if valid else None
        event={'id':digest([VERSION,r['record_id'],role,observed]),'patient_id':r['patient_id'],
               'kind':'record','stream':stream,'event_type':role,'concept':'','assertion':'','subject':'','relation':'','time':'',
               'source_date':iso(r.get('event_date')),'arrival_date':iso(r.get('available_date')),
               'clinical_date':clinical,'reference_date':None,'reference_status':'not_applicable',
               'clinical_date_status':'explicit' if valid else 'unresolved' if observed else 'not_observed',
               'clinical_date_fields':observed,'source':source(r),'record_hash':digest(r),
               'encounter_id':r.get('encounter_id'),'repeated_text_family':None,'copied_history':False,
               'authority':'Recorded source event; clinical meaning and complete coverage are not established.'}
        from .warehouse_interpretation import native,clock
        if native(r):
            binding=clock(r,as_of,related,fields,role if role in ('admission','discharge') else None)
            event.update(binding)
            event['id']=digest([VERSION,r['record_id'],role,binding])
        yield event


def narrative_reference(text,start,end,as_of):
    """Bind a literal date on the target line or an explicit prior-note section.

    DOB, signature and document dates are never assigned to every assertion.
    A referenced date remains a documentary reference, not a physiological date.
    """
    left=text.rfind('\n',0,start)+1;right=text.find('\n',end)
    if right<0:right=len(text)
    line=text[left:right];candidates=[]
    for m in re.finditer(r'\[(\d{4}-\d{2}-\d{2})\]',line):
        candidates.append({'date':m[1],'start':left+m.start(1),'end':left+m.end(1),'binding':'dated_target_line'})
    copied=False
    # Prior-note headers scope only to the next blank paragraph, not the rest of the chart.
    for m in re.finditer(r'(?im)^Prior note \((\d{4}-\d{2}-\d{2})\):[^\n]*\n',text[:start]):
        stop=text.find('\n\n',m.end());stop=len(text) if stop<0 else stop
        if start<stop:
            copied=True
            candidates.append({'date':m[1],'start':m.start(1),'end':m.end(1),'binding':'explicit_prior_note_section'})
    dates={c['date'] for c in candidates};valid=all(iso(d) and d<=as_of for d in dates)
    status='not_observed' if not candidates else 'invalid_or_future_reference' if not valid else 'conflicting_references' if len(dates)>1 else 'literal_reference'
    return {'reference_date':next(iter(dates)) if status=='literal_reference' else None,
            'reference_status':status,'reference_spans':candidates,'copied_history':copied,
            'statement':{'start':left,'end':right,'text':line}}


def annotation_event(identifier,pid,p,r,as_of):
    s=p['source'];parts={part['field']:part['text'] for part in text_fields(r)}
    field=s.get('field');text=parts.get(field);start=s.get('start');end=s.get('end')
    if (pid!=r['patient_id'] or s['record_id']!=r['record_id'] or s['source_table']!=r['source_table']
        or s['event_date']!=r['event_date'] or s['available_date']!=r['available_date']
        or text is None or type(start) is not int or type(end) is not int or not 0<=start<end<=len(text)
        or text[start:end]!=s['quote']):raise ValueError('Narrative source binding mismatch')
    reference=narrative_reference(text,start,end,as_of);axes=p['axes'];line=reference['statement']['text']
    return {'id':identifier,'patient_id':pid,'kind':'text','stream':r['source_table'],'event_type':'text_annotation',
            'concept':p['input']['concept'],**axes,'source_date':iso(r.get('event_date')),'arrival_date':iso(r.get('available_date')),
            'clinical_date':None,**reference,'clinical_date_status':'not_observed',
            'source':{**source(r),'field':field,'start':start,'end':end,'quote':s['quote']},'record_hash':digest(r),
            'reader_model_identity':p['model_identity'],'review_routing':p.get('review_routing'),
            'encounter_id':r.get('encounter_id'),
            'repeated_text_family':digest([pid,p['input']['concept'],axes,' '.join(line.lower().split())]),
            'authority':'Unvalidated source-bound machine annotation; literal reference date is not clinical adjudication.'}


def build(stack_path,output):
    stack_path,output=Path(stack_path),Path(output)
    if output.exists():raise ValueError('Fresh longitudinal artifact directory required')
    from .analysis_graph import evidence_connection
    meta=json.loads((stack_path/'run.json').read_text());edb,em=evidence_connection(stack_path,meta)
    src=sqlite3.connect((stack_path/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    output.mkdir(parents=True);db=sqlite3.connect(output/'events.sqlite3');started=time.perf_counter();counts=Counter();types=Counter();dates=Counter()
    db.executescript('''CREATE TABLE events(id TEXT PRIMARY KEY,patient TEXT,kind TEXT,stream TEXT,event_type TEXT,concept TEXT,assertion TEXT,subject TEXT,relation TEXT,time TEXT,source_date TEXT,arrival_date TEXT,clinical_date TEXT,reference_date TEXT,payload TEXT);
        CREATE TABLE patients(id TEXT PRIMARY KEY);''')
    def insert(e):
        db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',[e['id'],e['patient_id'],*[e[k] for k in ('kind','stream','event_type','concept','assertion','subject','relation','time','source_date','arrival_date','clinical_date','reference_date')],json.dumps(e,allow_nan=False)])
        counts[e['kind']]+=1;types[e['event_type']]+=1
        for k in CLOCKS.values():dates[k]+=bool(e.get(k))
    try:
        with db:
            for pid, in src.execute('SELECT id FROM patients ORDER BY id'):db.execute('INSERT INTO patients VALUES (?)',(pid,))
            for pid, in src.execute('SELECT id FROM patients ORDER BY id'):
                records={rid:json.loads(raw) for rid,raw in src.execute('SELECT id,payload FROM records WHERE patient=?',(pid,))}
                from .warehouse_interpretation import index
                related=index(list(records.values()))
                for r in records.values():
                    for e in record_events(r,meta['snapshot']['as_of'],related):insert(e)
                for ident,raw in edb.execute("SELECT id,payload FROM evidence WHERE patient=? AND kind='text'",(pid,)):
                    p=json.loads(raw);rid=p['source']['record_id']
                    if rid not in records:raise ValueError('Annotation source absent from warehouse')
                    insert(annotation_event(ident,pid,p,records[rid],meta['snapshot']['as_of']))
            db.executescript('CREATE INDEX event_patient ON events(patient,kind); CREATE INDEX event_type ON events(event_type,patient); CREATE INDEX event_concept ON events(concept,assertion,subject,patient);')
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Event index integrity failure')
        vocab={k:[r[0] for r in db.execute('SELECT DISTINCT '+k+' FROM events WHERE '+k+"!='' ORDER BY "+k)] for k in sorted(FILTERS)}
        report={'schema':VERSION,'synthetic':True,'source_digest':meta['source_digest'],'source_run':meta['source_run'],
                'snapshot':meta['snapshot'],'reader_model_identity':em['model_identity'],'code_identity':code_identity(),
                'patients':db.execute('SELECT count(*) FROM patients').fetchone()[0],'counts':dict(counts),'event_types':dict(types),
                'date_counts':dict(dates),'vocabulary':vocab,'elapsed_seconds':time.perf_counter()-started,
                'new_paid_api_calls':0,'limitations':LIMITS}
        (output/'run.json').write_text(json.dumps(report,indent=2))
        (stack_path/'longitudinal-link.json').write_text(json.dumps({'path':str(output.resolve()),'manifest_sha256':hashlib.sha256((output/'run.json').read_bytes()).hexdigest()},indent=2))
        return report
    finally:src.close();edb.close();db.close()


def connect(stack_path):
    path=Path(stack_path);link=json.loads((path/'longitudinal-link.json').read_text());root=Path(link['path'])
    raw=(root/'run.json').read_bytes();meta=json.loads(raw);stack=json.loads((path/'run.json').read_text())
    if hashlib.sha256(raw).hexdigest()!=link['manifest_sha256'] or meta['code_identity']!=code_identity():raise ValueError('Longitudinal artifact/code changed; rebuild required')
    if meta['source_digest']!=stack['source_digest'] or meta['snapshot']!=stack['snapshot']:raise ValueError('Longitudinal snapshot mismatch')
    annotation=json.loads((path/'annotation-link.json').read_text())
    if meta['reader_model_identity']!=annotation['model_identity']:raise ValueError('Longitudinal reader changed; rebuild required')
    return sqlite3.connect((root/'events.sqlite3').resolve().as_uri()+'?mode=ro',uri=True),meta


def validate_selector(value,vocabulary=None):
    if not isinstance(value,dict) or not value or not set(value)<=FILTERS or value.get('kind') not in ('record','text'):
        raise ValueError('Explicit record/text selector with mapped fields required')
    if value['kind']=='text' and not value.get('concept'):raise ValueError('Text sequence requires an explicit concept')
    if value['kind']=='record' and not (value.get('event_type') or value.get('stream')):raise ValueError('Record sequence needs an event type or source stream')
    if value['kind']=='record' and set(value)&{'concept','assertion','subject','relation','time'}:raise ValueError('Narrative axes cannot select structured record events')
    for k,v in value.items():
        if not isinstance(v,str) or not 1<=len(v)<=120:raise ValueError('Bounded exact event selector required')
        if vocabulary is not None and v not in vocabulary[k]:raise ValueError('Unmapped longitudinal selector: '+k)
    return value


def validate_parameters(p,asof,operation,vocabulary=None):
    common={'basis','available_by'}
    required=common|({'first','second','min_days','max_days','same_organization'} if operation=='sequence' else {'anchor','before_days','after_days','concepts'})
    if not isinstance(p,dict) or set(p)!=required:raise ValueError('Explicit longitudinal operation parameters required')
    if p['basis'] not in CLOCKS or not iso(p['available_by']) or p['available_by']>asof:raise ValueError('Mapped clock and observed availability cutoff required')
    days=('min_days','max_days') if operation=='sequence' else ('before_days','after_days')
    if any(type(p[k]) is not int or not 0<=p[k]<=3650 for k in days):raise ValueError('Bounded nonnegative day windows required')
    if operation=='sequence':
        if p['max_days']<p['min_days'] or type(p['same_organization']) is not bool:raise ValueError('Ordered day window and explicit organization constraint required')
        for key in ('first','second'):validate_selector(p[key],vocabulary)
    else:
        validate_selector(p['anchor'],vocabulary)
        if not isinstance(p['concepts'],list) or len(p['concepts'])>8 or any(not isinstance(c,str) or not 1<=len(c)<=120 for c in p['concepts']):raise ValueError('At most eight exact narrative concepts required')
        if vocabulary is not None and any(c not in vocabulary['concept'] for c in p['concepts']):raise ValueError('Unmapped narrative concept')
    return p


def rows(db,ids,selector,available_by):
    clauses=[];params=[]
    for k,v in sorted(selector.items()):clauses.append(k+'=?');params.append(v)
    result=defaultdict(list);eligible=sorted(set(ids))
    for offset in range(0,len(eligible),500):
        batch=eligible[offset:offset+500]
        query='SELECT patient,payload FROM events WHERE '+' AND '.join(clauses)+' AND patient IN ('+','.join('?' for _ in batch)+') AND arrival_date<=?'
        for pid,raw in db.execute(query,[*params,*batch,available_by]):result[pid].append(json.loads(raw))
    return result


def sequence(path,ids,p):
    db,meta=connect(path);validate_parameters(p,meta['snapshot']['as_of'],'sequence',meta['vocabulary'])
    yes=set();unknown=set();examples=[];clock=CLOCKS[p['basis']];pair_count=0;missing_dates=Counter()
    try:
        first=rows(db,ids,p['first'],p['available_by']);second=rows(db,ids,p['second'],p['available_by'])
        for pid in sorted(ids):
            aa=first.get(pid,[]);bb=second.get(pid,[]);found=[];n=0;unresolved=False
            for a in aa:
                for b in bb:
                    if a['id']==b['id']:continue
                    organization_unknown=False
                    if p['same_organization']:
                        orga=a['source']['organization_id'];orgb=b['source']['organization_id']
                        organization_unknown=not orga or not orgb
                        if not organization_unknown and orga!=orgb:continue
                    if not a[clock] or not b[clock]:unresolved=True;continue
                    delta=(date.fromisoformat(b[clock])-date.fromisoformat(a[clock])).days
                    if p['min_days']<=delta<=p['max_days']:
                        if organization_unknown:unresolved=True;continue
                        n+=1
                        if len(found)<3:found.append({'first':a,'second':b,'days':delta,'within_day_order_known':delta!=0})
            if n:yes.add(pid);pair_count+=n
            elif unresolved:unknown.add(pid)
            missing_dates['first']+=sum(not a[clock] for a in aa);missing_dates['second']+=sum(not b[clock] for b in bb)
            if (n or unresolved) and len(examples)<10:examples.append({'patient_id':pid,'status':'observed_sequence' if n else 'unresolved_timing_or_organization','matching_pairs':n,'examples':found})
        return yes,unknown,{'parameters':p,'patients_considered':len(ids),'observed_pairs':pair_count,
            'unresolved_date_observations':dict(missing_dates),'patient_examples':examples,
            'sequence_meaning':'At least one pair of distinct source-bound observations within the declared clock/window. Same-day pairs have unknown within-day order.',
            'limitations':LIMITS}
    finally:db.close()


def reconcile_accounts(events):
    grouped=defaultdict(list);excluded=[];families=Counter(e['repeated_text_family'] for e in events if e['kind']=='text')
    for e in events:
        if e['kind']!='text':continue
        if e['subject']!='patient' or e['assertion'] not in ('affirmed','negated') or e['relation']!='clinical_observation':
            excluded.append(e['id']);continue
        # A historical statement without a referenced date cannot determine a dated patient state.
        when=e['reference_date'] or (e['source_date'] if e['reference_status']=='not_observed' and e['time']=='current' and not e['copied_history'] else None)
        if not when:excluded.append(e['id']);continue
        grouped[(e['concept'],when,'reference' if e['reference_date'] else 'source_event')].append(e)
    accounts=[]
    for (concept,when,basis),items in sorted(grouped.items()):
        polarities={e['assertion'] for e in items}
        accounts.append({'concept':concept,'date':when,'date_basis':basis,
            'status':'conflicting_reports' if len(polarities)>1 else 'affirmed_report' if 'affirmed' in polarities else 'denied_report',
            'evidence_ids':[e['id'] for e in items],'text_families':len({e['repeated_text_family'] for e in items}),
            'current_clinical_state_established':False})
    return {'dated_accounts':accounts,'excluded_from_state_evidence_ids':excluded,
            'repeated_text_families':[{'id':k,'observations':n} for k,n in families.items() if n>1],
            'status':'documentary_accounts_only','probabilities_assigned':False}


def provisional_episodes(events,basis):
    clock=CLOCKS[basis];admissions=[e for e in events if e['event_type']=='admission'];discharges=[e for e in events if e['event_type']=='discharge'];used=set();result=[]
    for a in sorted(admissions,key=lambda e:(e[clock] or '',e['id'])):
        candidates=[]
        for d in discharges:
            if a['patient_id']!=d['patient_id']:continue
            if d['id'] in used or not a[clock] or not d[clock] or d[clock]<a[clock]:continue
            same_source=(a['source']['source_system'],a['source']['organization_id'])==(d['source']['source_system'],d['source']['organization_id'])
            if not same_source or not a['source']['source_system'] or not a['source']['organization_id']:continue
            if a['encounter_id'] or d['encounter_id']:
                if a['encounter_id']!=d['encounter_id']:continue
            intervening=[x for x in admissions if x['patient_id']==a['patient_id'] and x['id']!=a['id'] and x[clock] and a[clock]<=x[clock]<=d[clock]
                         and x['source']['source_system']==a['source']['source_system'] and x['source']['organization_id']==a['source']['organization_id']]
            if a['encounter_id'] or not intervening:candidates.append(d)
        if candidates:
            first=min(d[clock] for d in candidates);candidates=[d for d in candidates if d[clock]==first]
        d=candidates[0] if len(candidates)==1 else None
        if d:used.add(d['id'])
        result.append({'admission':a,'discharge':d,'status':'explicit_encounter_link' if d and a['encounter_id'] else 'provisional_same_source_pair' if d else 'unresolved_pair',
                       'date_basis':basis,'observed_span_days':(date.fromisoformat(d[clock])-date.fromisoformat(a[clock])).days if d else None,
                       'missing_or_ambiguous_discharge':not bool(d),'clinical_stay_adjudicated':False})
    return {'episodes':result,'unpaired_discharge_ids':[d['id'] for d in discharges if d['id'] not in used],
            'patient_currently_admitted_inferred':False,'readmission_determined':False}


def context(path,ids,p,limit=10):
    db,meta=connect(path);validate_parameters(p,meta['snapshot']['as_of'],'context',meta['vocabulary']);clock=CLOCKS[p['basis']]
    summary=Counter(patients_evaluated=len(ids));preview=[]
    try:
        anchors=rows(db,ids,p['anchor'],p['available_by'])
        for pid in sorted(ids):
            if not anchors.get(pid):continue
            summary['patients_with_anchor']+=1;events=rows(db,[pid],{'kind':'record'},p['available_by']).get(pid,[])
            text=rows(db,[pid],{'kind':'text'},p['available_by']).get(pid,[])
            if p['concepts']:text=[e for e in text if e['concept'] in p['concepts']]
            selected=[];undated=[]
            valid_anchors=[a for a in anchors[pid] if a[clock]]
            for e in events+text:
                if not e[clock]:undated.append(e);continue
                if any(-p['before_days']<=(date.fromisoformat(e[clock])-date.fromisoformat(a[clock])).days<=p['after_days'] for a in valid_anchors):selected.append(e)
            accounts=reconcile_accounts(text);episodes=provisional_episodes(events,p['basis'])
            summary['anchors']+=len(anchors[pid]);summary['anchors_without_selected_date']+=len(anchors[pid])-len(valid_anchors)
            summary['context_observations']+=len(selected);summary['undated_observations']+=len(undated)
            summary['conflicting_dated_accounts']+=sum(a['status']=='conflicting_reports' for a in accounts['dated_accounts'])
            summary['repeated_text_families']+=len(accounts['repeated_text_families'])
            summary['provisional_paired_episodes']+=sum(e['status']=='provisional_same_source_pair' for e in episodes['episodes'])
            if len(preview)<limit:
                selected.sort(key=lambda e:(e[clock],e['id']))
                preview.append({'patient_id':pid,'anchors':anchors[pid],'context':selected[:80],'context_total':len(selected),
                    'undated_examples':undated[:10],'undated_total':len(undated),'accounts':accounts,'episodes':episodes,
                    'accounts_scope':'All selected-concept annotations received by cutoff, including history outside the anchor window.',
                    'missingness_hypotheses':[
                        {'hypothesis':'Care happened outside contributing sources','status':'unresolved','discriminating_evidence':'Outside encounter/dispense reports with event dates and patient/source identity.'},
                        {'hypothesis':'Care was planned but not completed','status':'unresolved','discriminating_evidence':'Completion records linked to the specific order or referral; a request alone is insufficient.'},
                        {'hypothesis':'The record is delayed or the feed is incomplete','status':'unresolved','discriminating_evidence':'Source delivery logs and later snapshots, compared with population source-volume changes.'},
                        {'hypothesis':'The reader missed or misinterpreted narrative evidence','status':'unresolved','discriminating_evidence':'Review exact received text and source-bound annotations, including copied sections.'}],
                    'clinical_cause_established':False,'automatic_action_authorized':False})
        return {'operator':'longitudinal_context','synthetic':True,'summary':dict(summary),'parameters':p,
                'patients':preview,'patients_returned':len(preview),'all_patients_computed':True,
                'snapshot':meta['snapshot'],'limitations':LIMITS,'new_paid_api_calls':0}
    finally:db.close()
