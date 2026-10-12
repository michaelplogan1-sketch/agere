"""Literal dates inside the exact target; not inferred event dates or model edits."""
import datetime
import re
from ...contracts import digest
from . import semantic_reader as R

VERSION='statement-target-literal-dates/1'
PATTERN=re.compile(r'(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)')


def date(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):return None
    try:return datetime.date.fromisoformat(value)
    except ValueError:return None


def audit(packet,target):
    text=packet['text'];a,b=packet['target_start'],packet['target_end']
    if (type(a) is not int or type(b) is not int or not 0<=a<b<=len(text) or
        text[a:b]!=packet['target_quote'] or target['end']-target['start']!=b-a):
        raise ValueError('Exact marked target required')
    source=date(packet.get('source_date'));matches=[];invalid=[]
    for match in PATTERN.finditer(packet['target_quote']):
        literal=match.group();parsed=date(literal)
        reference={'quote':literal,'field':target['field'],'start':target['start']+match.start(),'end':target['start']+match.end()}
        if parsed is None:invalid.append({**reference,'reason':'Invalid calendar date'});continue
        delta=(parsed-source).days if source else None
        matches.append({**reference,'date':parsed.isoformat(),'days_relative_to_source':delta,
                        'relative_to_source':'unknown_source_date' if delta is None else 'before_source_date' if delta<0 else 'after_source_date' if delta>0 else 'same_source_date'})
    return {'schema':VERSION,'target':target,'dates':matches,'invalid_literals':invalid,
            'source_date':packet.get('source_date'),'available_date':packet.get('available_date'),
            'legacy_packet_reference_date':packet.get('literal_reference_date'),
            'scope':'Only full ISO dates written inside the marked target; no adjacent event, copied history or inferred date is transferred.',
            'event_date_established':False,'model_labels_changed':False,'clinical_state_established':False,
            'limits':['An earlier date does not by itself identify the event it describes or negate a current condition.',
                      'No matching literal does not mean that temporal evidence is absent. Other date formats, relative time and adjacent context remain unassessed.']}


def inspect(stack,body):
    if not isinstance(body,dict) or set(body)!={'target'}:raise ValueError('Exact source target required')
    record,text,meta=R.source(stack,body['target']);packet=R.packet(record,text,body['target'],meta['snapshot']['as_of'])
    return {**audit(packet,body['target']),'source_hash':digest(record),'context_digest':digest(packet),
            'new_paid_api_calls':0,'external_model_calls':0}
