"""Explicit source-clock diagnostics and dated selection, without inferred clocks.

Diagnostics never rewrite the accounting or clinical clock engines. A selected
date window is a scope choice; linked evidence outside it remains visible.
"""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
from ...contracts import digest
from . import need_candidates as candidates, record_context, source_synthesis
from . import question_context, warehouse_interpretation as native, financial_accounts as finance, clinical_events

VERSION='evidence-timing/1'


def identity():
    return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),native.identity(),
        candidates.identity(),finance.identity(),hashlib.sha256(Path(clinical_events.__file__).read_bytes()).hexdigest()])


def inputs(path,request):
    if not isinstance(request,dict) or set(request)!={'kind','id'}:raise ValueError('Exact timing source kind and receipt required')
    if request['kind']=='candidates':
        parent=candidates.inspect(path,request['id']);pid=parent['binding']['patient_id']
        ids=[r['record_id'] for r in parent['candidates']];question=parent['query']
    elif request['kind']=='context':
        parent=source_synthesis.context(path,request['id']);pid=parent['patient_id']
        ids=parent.get('record_scope',{}).get('record_ids')
        if ids is None:ids=list((parent.get('model_context') or {}).get('sources',{}))
        question=parent['parameters']['question']
    else:raise ValueError('Unsupported timing source kind')
    if len(ids)>200:raise ValueError('Timing inspection is bounded to 200 explicit records')
    records,meta=record_context.received(path,pid);byid=native.index(records)['records']
    if set(ids)-set(byid):raise ValueError('Timing source is no longer received for this patient')
    binding={'request':request,'parent_hash':digest(parent),'patient_id':pid,'record_ids':sorted(ids),
        'patient_source_fingerprint':digest(records),'snapshot':meta['snapshot'],'question':question}
    return records,binding


def timestamp(value):
    day,instant=native.day(value)
    if value is None:return {'status':'not_received','represented_date':None,'utc_date':None,'instant_utc':None}
    if not day:return {'status':'unresolved_or_invalid_offset_timestamp','represented_date':None,'utc_date':None,'instant_utc':None}
    if not instant:return {'status':'date_only','represented_date':day,'utc_date':None,'instant_utc':None}
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    utc=parsed.astimezone(timezone.utc)
    return {'status':'offset_timestamp','represented_date':day,'utc_date':utc.date().isoformat(),
        'instant_utc':instant,'explicit_offset_seconds':int(parsed.utcoffset().total_seconds()),
        'offset_calendar_rollover':day!=utc.date().isoformat()}


def diagnose(record,asof,index):
    refs={field:native.reference(record,field) for field in ('event_date','event_date_precision','recorded_date','available_date','event_time','recorded_time','signed_time','available_time') if field in record}
    stamp=timestamp(record.get('event_time'));source_day=clinical_events.iso(record.get('event_date'))
    if stamp['status']=='offset_timestamp' and source_day:
        relation='same_represented_calendar_date' if source_day==stamp['represented_date'] else 'calendar_basis_unresolved'
    else:relation='not_comparable_as_date_and_offset_timestamp'
    clinical=native.clock(record,asof,index)
    financial_key=finance.time_key(record)
    return {'record_id':record['record_id'],'source_table':record['source_table'],'record_hash':digest(record),
        'raw_clock_fields':refs,'source_timestamp':stamp,'source_date_timestamp_relation':relation,
        'other_timestamps':{field:timestamp(record.get(field)) for field in ('recorded_time','signed_time','available_time')},
        'source_date_precision':record.get('event_date_precision','not_declared'),
        'clinical_clock':clinical,
        'basis_dates':{'source_event':source_day if record.get('event_date_precision') in (None,'day','minute') else None,'arrival':clinical_events.iso(record.get('available_date')),
            'clinical':clinical['clinical_date'] if clinical['clinical_date_status'] in ('explicit_date','explicit_future_date') else None},
        'date_timezone_binding':'not_established','calendar_difference_is_contradiction':False,
        'financial_clock_key_under_existing_contract':list(financial_key) if financial_key else None,
        'temporal_sufficiency_assessed':False}


def prepare(path,request):
    records,binding=inputs(path,request);key=digest([identity(),binding]);target=candidates.location(path,key,'evidence-timing')
    if target.exists():return {**inspect(path,key),'cache_hit':True}
    ix=native.index(records);rows=[diagnose(ix['records'][rid],binding['snapshot']['as_of'],ix) for rid in binding['record_ids']]
    result={'schema':VERSION,'id':key,'implementation':identity(),'binding':binding,'records':rows,
        'summary':{'records':len(rows),'calendar_basis_unresolved':sum(r['source_date_timestamp_relation']=='calendar_basis_unresolved' for r in rows),
            'explicit_offset_calendar_rollovers':sum(r['source_timestamp'].get('offset_calendar_rollover',False) for r in rows),
            'clinical_clock_statuses':dict(Counter(r['clinical_clock']['clinical_date_status'] for r in rows))},
        'meaning_resolved':False,'source_dates_rewritten':False,'financial_projection_recomputed':False,'new_paid_api_calls':0,
        'limits':['A timestamp offset defines that instant, not the timezone or meaning of a separate source date.',
            'Arrival, source event and explicit clinical clocks answer different questions; none silently replaces another.',
            'Calendar differences are diagnostics, not established contradictions or evidence of completed care.',
            'Only these selected records are inspected. Clinical meaning and temporal sufficiency remain unassessed.']}
    if inputs(path,request)[1]!=binding:raise ValueError('Timing source dependencies changed during preparation')
    result['result_digest']=digest(result);candidates.write(target,result);return {**result,'cache_hit':False}


def inspect(path,key):
    result=json.loads(candidates.location(path,key,'evidence-timing').read_text())
    if result['id']!=key or result['implementation']!=identity() or result['result_digest']!=digest({k:v for k,v in result.items() if k!='result_digest'}):raise ValueError('Timing receipt integrity changed')
    if inputs(path,result['binding']['request'])[1]!=result['binding'] or digest([identity(),result['binding']])!=key:raise ValueError('Timing source dependencies changed')
    return result


def window(path,request):
    if not isinstance(request,dict) or set(request)!={'timing_id','window'}:raise ValueError('Exact timing receipt and explicit window required')
    row=inspect(path,request['timing_id']);w=clinical_events.validate_window(request['window'],row['binding']['snapshot']['as_of'])
    if w['basis']=='all_observed' or not (w['start'] or w['end']):raise ValueError('Choose an explicit date basis and at least one date bound')
    groups={'within':[],'outside':[],'unresolved':[]}
    for r in row['records']:
        day=r['basis_dates'][w['basis']]
        group='unresolved' if day is None else 'outside' if (w['start'] and day<w['start']) or (w['end'] and day>w['end']) else 'within'
        groups[group].append({'record_id':r['record_id'],'date':day,'record_hash':r['record_hash']})
    result={'schema':VERSION,'implementation':identity(),'timing_id':row['id'],'timing_hash':digest(row),'window':w,
        'groups':groups,'counts':{k:len(v) for k,v in groups.items()},'question_answerable':False,'new_paid_api_calls':0,
        'meaning':'Literal date-window membership only. Unknown dates are explicit, and outside-window evidence is not absent care.'}
    result['id']=digest(result);candidates.write(candidates.location(path,result['id'],'evidence-windows'),result);return result


def inspect_window(path,key):
    result=json.loads(candidates.location(path,key,'evidence-windows').read_text())
    if result['id']!=key or result['implementation']!=identity() or digest({k:v for k,v in result.items() if k!='id'})!=key:raise ValueError('Date-window receipt integrity changed')
    if digest(inspect(path,result['timing_id']))!=result['timing_hash']:raise ValueError('Date-window source dependencies changed')
    return result


def select(path,request):
    if not isinstance(request,dict) or set(request)!={'window_id','include_unresolved'} or type(request['include_unresolved']) is not bool:raise ValueError('Exact window and explicit unknown-date selection required')
    w=inspect_window(path,request['window_id']);timing=inspect(path,w['timing_id']);b=timing['binding']
    roots=[r['record_id'] for r in w['groups']['within']+(w['groups']['unresolved'] if request['include_unresolved'] else [])]
    if not roots:raise ValueError('No selected records in this window; this does not establish absent care')
    records,_=record_context.received(path,b['patient_id'])
    closure=question_context.select_sources(records,roots,b['snapshot']['as_of'])
    context=record_context.create(path,b['patient_id'],b['question'],closure['connected_record_ids'])
    if digest(inspect_window(path,w['id']))!=digest(w):raise ValueError('Date-window sources changed during selection')
    result={'schema':VERSION,'implementation':identity(),'window_id':w['id'],'window_hash':digest(w),
        'include_unresolved':request['include_unresolved'],'selection':closure,'context_id':context['id'],
        'record_scope':context['record_scope'],'budget':context['budget'],'dispatch':context['dispatch'],'omissions':context['omissions'],
        'linked_records_outside_selected_roots':sorted(set(closure['connected_record_ids'])-set(roots)),
        'selected_roots_excluded_from_window':[r['record_id'] for r in w['groups']['outside']],
        'unresolved_roots_excluded':[] if request['include_unresolved'] else [r['record_id'] for r in w['groups']['unresolved']],
        'meaning_resolved':False,'need_binding_changed':False,'new_paid_api_calls':0,
        'scope':'Date window chooses roots; complete typed-reference closure can retain records outside it. No temporal or clinical sufficiency claim.'}
    result['id']=digest(result);candidates.write(candidates.location(path,result['id'],'evidence-window-contexts'),result);return result


def inspect_selection(path,key):
    result=json.loads(candidates.location(path,key,'evidence-window-contexts').read_text())
    if result['id']!=key or result['implementation']!=identity() or digest({k:v for k,v in result.items() if k!='id'})!=key:raise ValueError('Dated context integrity changed')
    if digest(inspect_window(path,result['window_id']))!=result['window_hash']:raise ValueError('Dated context dependencies changed')
    if record_context.inspect(path,result['context_id'])['record_scope']!=result['record_scope']:raise ValueError('Dated record scope changed')
    return result
