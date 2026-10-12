"""Item-level documentary medication chains over explicit received source links.

A requested-name match associates a response with a question, not two drug
products with each other. Copies are traceable copies, never independent votes.
No source clock, pharmacy label or review entry establishes a current regimen.
"""
from collections import defaultdict
from pathlib import Path
import hashlib,json,sqlite3
from ...contracts import digest
from . import warehouse_interpretation as native, medication_identity as names
from .clinical_state import normalize_dose

VERSION='medication-item-accounts/1'
RELATION='medication_reconciliation_item'
FIELDS=('reported','historical_list','review','medication_name','interview_record_id')
STREAMS=('reported_medication_entries','medication_history_interviews','medication_lists',
         'medication_information_requests','pharmacy_information_responses','received_pharmacy_information',
         'medication_review_decisions','medication_clarification_contacts','pharmacy_dispenses')
LIMITS=['Explicit source pointers and requested names establish documentary associations, not therapeutic equivalence.',
        'A copied pharmacy response is not independent corroboration; a dispensing label is not actual use or verified current directions.',
        'Historical list dose, reported dose and label units remain separate; no inferred strength conversion or maximum tolerated dose.',
        'A completed conversation or review does not establish new orders, stopped therapy or medication administration.',
        'Missing accounts may reflect source coverage. No absent-care or nonadherence inference.']


def identity():
    from . import clinical_state
    return digest([VERSION,native.identity(),*[hashlib.sha256(Path(p).read_bytes()).hexdigest()
                   for p in (__file__,names.__file__,clinical_state.__file__)]])


def value_at(record,path):
    value=record
    for part in path:
        if isinstance(part,str) and isinstance(value,dict):value=value.get(part)
        elif type(part) is int and isinstance(value,list) and 0<=part<len(value):value=value[part]
        else:return None
    return value


def ref(r,path):
    path=list(path)
    return {**native.reference(r,path[0]),'field_path':path,'field':'.'.join(map(str,path)),
            'value':value_at(r,path)}


def normalized(value):return ' '.join(value.casefold().split()) if isinstance(value,str) else None


def project(pid,records,asof,terminology=None):
    if any(r['patient_id']!=pid or r.get('synthetic') is not True or not native.native(r) or not native.visible(r,asof) for r in records):
        raise ValueError('Visible same-patient native synthetic medication sources required')
    ix=native.index(records)['records'];by_stream=defaultdict(list)
    for r in records:by_stream[r['source_table']].append(r)
    index=names.term_index(terminology) if terminology else {};rows=[];used=set()
    reports=by_stream['reported_medication_entries']
    name_peers=defaultdict(list)
    for r in reports:name_peers[r.get('interview_record_id'),normalized(r.get('medication_name_reported'))].append(r['record_id'])

    def build(parent,item_path,item,reported_id):
        evidence=[];links=[];issues=[];nodes=[];edges=[];copies=[]
        def issue(reason,record_id,field,**detail):issues.append({'reason':reason,'record_id':record_id,'field':field,**detail})
        def node(r,role,path=()):
            fields=[ref(r,path)] if path else [ref(r,[f]) for f in sorted(r) if not f.startswith('_') and f not in ('synthetic',)]
            evidence.extend(fields)
            key=digest([r['record_id'],list(path)])
            if not any(n['id']==key for n in nodes):nodes.append({'id':key,'role':role,'record_id':r['record_id'],
                'field_path':list(path),'source_table':r['source_table'],'source_date':r.get('event_date'),
                'arrival_date':r.get('available_date'),'value':value_at(r,path) if path else {k:r[k] for k in r.get('_warehouse_fields',[])},
                'evidence':fields})
            return key
        def target(owner,path,stream,role,required=True):
            rid=value_at(owner,path);r=ix.get(rid) if isinstance(rid,str) else None
            reason=('required_reference_not_recorded' if not rid else 'referenced_source_not_received' if r is None else
                    'wrong_linked_source_type' if r['source_table']!=stream else None)
            if reason:
                if required or rid:issue(reason,rid,'.'.join(map(str,path)),expected_stream=stream)
                return None
            links.append({'field':'.'.join(map(str,path)),'target':native.reference(r,'record_id')})
            a=node(owner,'reference_origin',path);b=node(r,role)
            edges.append({'from':a,'to':b,'basis':'explicit_source_pointer','source_field':ref(owner,path)})
            return r
        def array(r,key):
            value=r.get(key)
            if not isinstance(value,list):issue('item_array_missing_or_invalid',r['record_id'],key);return []
            if any(not isinstance(x,dict) for x in value):issue('item_not_an_object',r['record_id'],key)
            return [(i,x) for i,x in enumerate(value) if isinstance(x,dict)]

        node(parent,'clinician_review_item' if item is not None else 'reported_product',item_path)
        reported=target(parent,[*item_path,'reported_entry_record_id'],'reported_medication_entries','reported_product') if item is not None else parent
        if reported:used.add(reported['record_id']);node(reported,'reported_product')
        interview=target(reported,['interview_record_id'],'medication_history_interviews','interview_scope') if reported else None
        historical=target(reported,['historical_list_record_id'],'medication_lists','historical_list',False) if reported else None
        if item is not None:
            review_interview=target(parent,['interview_record_id'],'medication_history_interviews','review_interview')
            if interview and review_interview and interview['record_id']!=review_interview['record_id']:
                issue('review_and_report_interview_disagree',parent['record_id'],'interview_record_id')
            if reported and normalized(item.get('medication_name'))!=normalized(reported.get('medication_name_reported')):
                issue('review_and_report_names_disagree',parent['record_id'],'.'.join(map(str,item_path))+'.medication_name')
            received=target(parent,['received_pharmacy_information_record_id'],'received_pharmacy_information','received_pharmacy_copy')
            label_items=[]
            if received:
                request=target(received,['request_record_id'],'medication_information_requests','pharmacy_request')
                original=target(received,['original_response_record_id'],'pharmacy_information_responses','original_pharmacy_response')
                if request:
                    request_interview=target(request,['interview_record_id'],'medication_history_interviews','requested_interview')
                    if interview and request_interview and interview['record_id']!=request_interview['record_id']:
                        issue('request_and_report_interview_disagree',request['record_id'],'interview_record_id')
                if original:
                    original_request=target(original,['request_record_id'],'medication_information_requests','original_request')
                    # Resent request records must explicitly point to the initial request.
                    request_ids={request['record_id']} if request else set()
                    cursor=request;seen=set()
                    while cursor and cursor.get('original_request_record_id') and cursor['record_id'] not in seen:
                        seen.add(cursor['record_id']);cursor=target(cursor,['original_request_record_id'],'medication_information_requests','prior_request')
                        if cursor:request_ids.add(cursor['record_id'])
                    if cursor and cursor.get('original_request_record_id') and cursor['record_id'] in seen:
                        issue('cyclic_request_lineage',cursor['record_id'],'original_request_record_id')
                    if original_request and request and original_request['record_id'] not in request_ids:
                        issue('copy_and_original_request_disagree',received['record_id'],'request_record_id')
                    if received.get('response_status')!=original.get('response_status'):
                        issue('copy_response_status_disagrees',received['record_id'],'response_status')
                original_items=array(original,'items') if original else []
                received_items=array(received,'items')
                # Document-level fidelity includes missing/extra items, without order assumptions.
                if original and sorted(digest(x) for _,x in original_items)!=sorted(digest(x) for _,x in received_items):
                    issue('received_copy_items_disagree',received['record_id'],'items')
                for i,x in received_items:
                    if not reported or normalized(x.get('requested_name'))!=normalized(reported.get('medication_name_reported')):continue
                    a=node(received,'received_pharmacy_item',['items',i]);label_items.append({'node_id':a,'item':x})
                    if len(name_peers[reported.get('interview_record_id'),normalized(reported.get('medication_name_reported'))])!=1:
                        issue('requested_name_matches_multiple_reported_entries',received['record_id'],f'items.{i}.requested_name')
                    edges.append({'from':a,'to':node(reported,'reported_product'),
                        'basis':'same_interview_and_exact_requested_name_only','product_equivalence_established':False})
                    matches=[j for j,y in original_items if y==x]
                    if original and len(matches)==1:
                        b=node(original,'original_pharmacy_item',['items',matches[0]])
                        copies.append({'copy':a,'original':b,'independent_corroboration':False})
                        edges.append({'from':a,'to':b,'basis':'explicit_original_response_and_identical_item'})
                    elif original:issue('original_item_missing_or_ambiguous',original['record_id'],'items',candidate_indices=matches)
                    dispense=target(received,['items',i,'original_dispense_record_id'],'pharmacy_dispenses','original_dispense')
                    if dispense:
                        label=x.get('dispensing_label')
                        if not isinstance(label,dict):issue('dispensing_label_missing_or_invalid',received['record_id'],f'items.{i}.dispensing_label')
                        else:
                            for field,value in label.items():
                                if field in dispense and (type(dispense[field]) is not type(value) or dispense[field]!=value):
                                    issue('label_and_dispense_literal_disagreement',received['record_id'],f'items.{i}.dispensing_label.{field}',target_record_id=dispense['record_id'])
                available=item.get('dispensing_label_available')
                if type(available) is bool and available!=bool(label_items):
                    issue('review_label_presence_disagrees_with_received_items',parent['record_id'],'.'.join(map(str,item_path))+'.dispensing_label_available')
            for contact in by_stream['medication_clarification_contacts']:
                if contact.get('review_decision_record_id')==parent['record_id']:
                    node(contact,'clarification_contact');target(contact,['review_decision_record_id'],'medication_review_decisions','review_document')
        else:
            label_items=[];issue('review_entry_not_received',reported_id,'reported_entry_record_id',expected_stream='medication_review_decisions')
        reported_values={k:reported[k] for k in ('medication_name_reported','use_statement','dose_reported','frequency_reported',
          'directions_verification','prescriber_stop_order_established','administration_observed','information_source') if reported and k in reported}
        name=reported_values.get('medication_name_reported') or (item or {}).get('medication_name')
        dose=reported_values.get('dose_reported');freq=reported_values.get('frequency_reported')
        notation=normalize_dose((str(dose)+' '+str(freq)) if dose is not None and freq is not None else dose)
        identity=names.concept(name,index)
        values={'medication_name':name,'reported':reported_values,'historical_list':
                {k:historical[k] for k in ('name','dose','frequency','indication') if k in historical} if historical else None,
                'review':item,'interview_record_id':reported.get('interview_record_id') if reported else None}
        chain={'schema':VERSION,'nodes':nodes,'edges':edges,'copy_lineage':copies,
               'reported_name_identity':identity,'reported_dose_notation':notation,
               'reported_release_notation':names.formulation(name,reported or {}),
               'pharmacy_items':label_items,'ingestion_established':False,'current_regimen_established':False,
               'maximum_tolerated_dose':'not_established','limits':LIMITS}
        rows.append({'id':digest([VERSION,parent['record_id'],item_path,evidence,issues]),'patient_id':pid,
            'domain':'medication','relation':RELATION,'entity':reported_id or 'unlinked:'+parent['record_id']+':'+str(item_path),
            'values':values,'source_date':parent.get('event_date'),'reported_use':None,
            'evidence':list({digest(e):e for e in evidence}.values()),'linked_sources':links,'link_issues':issues,
            'reconciliation':chain,'clinical_truth_established':False,'authority':'Literal received medication source chain'})

    for review in by_stream['medication_review_decisions']:
        entries=review.get('entries')
        if not isinstance(entries,list):
            # Invalid arrays remain a source-dependent unknown account.
            build(review,[],{},None);continue
        for i,item in enumerate(entries):
            build(review,['entries',i],item if isinstance(item,dict) else {},item.get('reported_entry_record_id') if isinstance(item,dict) else None)
    for r in reports:
        if r['record_id'] not in used:build(r,[],None,r['record_id'])
    groups=defaultdict(list)
    for row in rows:groups[row['entity']].append(row)
    states=[]
    for entity,items in sorted(groups.items()):
        undated=any(native.day(r['source_date'])[0] is None for r in items)
        latest=max((r['source_date'] for r in items if r['source_date']),default=None)
        selected=items if undated else [r for r in items if r['source_date']==latest]
        signatures={digest(r['values']) for r in selected}
        status=('undated' if undated else 'source_link_unresolved' if any(r['link_issues'] for r in selected) else
                'conflicting' if len(signatures)>1 else 'documented_source_statement')
        states.append({'domain':'medication','relation':RELATION,'entity':entity,'status':status,
            'selected_source_date':None if undated else latest,'selected_statement_ids':[r['id'] for r in selected],
            'prior_statement_ids':[r['id'] for r in items if r not in selected],
            'meaning':'Latest review account for the explicitly referenced report; no current regimen inferred.'})
    return {'schema':native.VERSION,'patient_id':pid,'synthetic':True,'statements':rows,'states':states}


def selected_rows(db,pid=None):
    clause='stream IN ('+','.join('?' for _ in STREAMS)+')';args=list(STREAMS)
    if pid is not None:clause+=' AND patient=?';args.append(pid)
    return db.execute('SELECT patient,id,payload FROM records WHERE '+clause+' ORDER BY patient,id',args)


def snapshot(path):
    path=Path(path)
    # Derived warehouse only. Exact source payloads remain untouched; this avoids
    # scanning unrelated notes/labs for every item-level requirement snapshot.
    indexed=sqlite3.connect(path/'stack.sqlite3',timeout=30)
    try:
        with indexed:indexed.execute('CREATE INDEX IF NOT EXISTS medication_source_records ON records(stream,patient,id)')
    finally:indexed.close()
    db=sqlite3.connect((path/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True);hashes={}
    try:
        for pid,rid,raw in selected_rows(db):
            hashes.setdefault(pid,hashlib.sha256()).update(json.dumps([rid,hashlib.sha256(raw.encode()).hexdigest()]).encode())
    finally:db.close()
    terminology=names.attached(path) if (path/'medication-terminology-link.json').exists() else None
    return {'patient_hashes':{pid:h.hexdigest() for pid,h in hashes.items()},'terminology':terminology,
            'code_identity':identity(),'terminology_hash':digest(terminology)}


def load(db,pid,snap,asof):
    records=[];h=hashlib.sha256()
    for patient,rid,raw in selected_rows(db,pid):
        if patient!=pid:raise ValueError('Medication source patient mismatch')
        h.update(json.dumps([rid,hashlib.sha256(raw.encode()).hexdigest()]).encode());records.append(json.loads(raw))
    if (h.hexdigest() if records else None)!=snap['patient_hashes'].get(pid):raise ValueError('Medication item sources changed during evaluation')
    return project(pid,records,asof,snap['terminology'])
