"""Bounded, inspectable neighborhoods of received source records.

Edges are existing typed documentary references, never causal/clinical claims.
No new patient truth store or model labels. Exact patient/source checks precede
context preparation and the existing statement-reader handoff.
"""
from collections import Counter, deque
from pathlib import Path
import json
import sqlite3
from ...contracts import digest
from ..narrative_sources import text_fields
from . import context_builder, warehouse_interpretation as native

VERSION = 'received-evidence-thread/1'
LIMITS = ('Explicit source references only; adjacency is not temporal order, causation or current clinical state. '
          'Unreceived or untyped references remain unresolved. Display previews do not establish model context coverage. '
          'All records are synthetic. No model call, clinical action or external request occurs.')


def connect(path):
    path = Path(path)
    meta = json.loads((path/'run.json').read_text())
    if meta.get('synthetic') is not True:
        raise ValueError('Synthetic received snapshot required')
    return sqlite3.connect((path/'stack.sqlite3').resolve().as_uri()+'?mode=ro', uri=True), meta


def atlas(path):
    db, meta = connect(path)
    try:
        rows = [{'stream':s,'records':n,'patients':p,
                 'domain':native.STATEMENTS[s][0] if s in native.STATEMENTS else 'other',
                 'literal_account_mapped':s in native.STATEMENTS}
                for s,n,p in db.execute('SELECT stream,count(*),count(DISTINCT patient) FROM records GROUP BY stream ORDER BY stream')]
        groups=Counter()
        for r in rows:groups[r['domain']]+=r['records']
        return {'schema':VERSION,'synthetic':True,'snapshot':meta['snapshot'],'sources':rows,
                'domain_records':dict(groups),'records':sum(r['records'] for r in rows),
                'patients':db.execute('SELECT count(*) FROM patients').fetchone()[0],
                'source_categories':len(rows),'new_paid_api_calls':0,
                'scope':'Received source inventory. A mapped literal account is not validated clinical understanding.'}
    finally:db.close()


def preview(r):
    parts=list(text_fields(r)); first=parts[0] if parts else None
    return {'record_id':r['record_id'],'patient_id':r['patient_id'],'stream':r['source_table'],
            'source_date':r.get('event_date'),'arrival_date':r.get('available_date'),
            'narrative_fields':len(parts),'preview':first['text'][:220] if first else '',
            'preview_field':first['field'] if first else None,
            'preview_is_partial':bool(first and len(first['text'])>220)}


def records(path, stream, patient='', offset=0):
    if not isinstance(stream,str) or not 1<=len(stream)<=128 or not isinstance(patient,str) or len(patient)>150:
        raise ValueError('Bounded source and optional patient required')
    if type(offset) is not int or not 0<=offset<=1000000:raise ValueError('Invalid source page')
    db,meta=connect(path)
    try:
        if not db.execute('SELECT 1 FROM records WHERE stream=? LIMIT 1',(stream,)).fetchone():raise ValueError('Unknown source category')
        if patient and not db.execute('SELECT 1 FROM patients WHERE id=?',(patient,)).fetchone():raise ValueError('Unknown patient')
        where='stream=?'+(' AND patient=?' if patient else '');args=[stream]+([patient] if patient else [])
        total=db.execute('SELECT count(*) FROM records WHERE '+where,args).fetchone()[0]
        values=[preview(json.loads(r[0])) for r in db.execute('SELECT payload FROM records WHERE '+where+' ORDER BY patient,id LIMIT 12 OFFSET ?',args+[offset])]
        return {'schema':VERSION,'synthetic':True,'snapshot':meta['snapshot'],'stream':stream,
                'patient':patient,'total':total,'offset':offset,'limit':12,'records':values,'new_paid_api_calls':0}
    finally:db.close()


def checked_records(db,patient,asof):
    if not isinstance(patient,str) or not 1<=len(patient)<=150:raise ValueError('Bounded patient identity required')
    if not db.execute('SELECT 1 FROM patients WHERE id=?',(patient,)).fetchone():raise ValueError('Unknown patient')
    rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM records WHERE patient=? ORDER BY id',(patient,))]
    if any(r.get('patient_id')!=patient or r.get('synthetic') is not True or r.get('identity_status')!='matched' or not native.visible(r,asof) for r in rows):
        raise ValueError('Visible matched same-patient synthetic sources required')
    return rows


def narrative_preview(r):
    """Whole paragraphs, exact offsets, with explicit omitted-unit accounting."""
    output=[]; used=0; total=0; omitted=0
    for part in text_fields(r):
        for a,b,text in context_builder.paragraphs(part['text']):
            total+=1
            if len(output)>=16 or used+len(text.encode())>12000:
                omitted+=1;continue
            output.append({'field':part['field'],'start':a,'end':b,'text':text,'field_characters':len(part['text'])})
            used+=len(text.encode())
    return {'paragraphs':output,'total_paragraphs':total,'omitted_paragraphs':omitted,
            'complete_narrative_display':omitted==0,'display_bytes':used,
            'scope':'Whole paragraphs for target selection. Reader context is independently reconstructed from the original field.'}


def inspect(path,patient,record,max_records=18):
    if not isinstance(record,str) or not 1<=len(record)<=180:raise ValueError('Bounded source record required')
    if type(max_records) is not int or not 1<=max_records<=24:raise ValueError('Thread must contain 1–24 records at most')
    db,meta=connect(path)
    try:
        db.execute('BEGIN'); rows=checked_records(db,patient,meta['snapshot']['as_of']); ix=native.index(rows)
        root=ix['records'].get(record)
        if root is None:raise ValueError('Unknown source record for this patient')
        # Resolve referenced identities outside the patient scope only to distinguish
        # invalid links. Their contents are never returned as thread evidence.
        refs=set()
        for r in rows:
            if r['source_table'] not in native.STATEMENTS:continue
            spec=native.SOURCE_DEFINITIONS.get(r['source_table'],{}).get('links',native.LINK_FIELDS)
            for f in spec:
                raw=r.get(f);refs.update(v for v in (raw if isinstance(raw,list) else [raw]) if isinstance(v,str) and v)
        for ref in sorted(refs-set(ix['records'])):
            found=db.execute('SELECT payload FROM records WHERE id=?',(ref,)).fetchone()
            if found:ix['records'][ref]=json.loads(found[0])
        statements={};edges=[];neighbors={r['record_id']:set() for r in rows}
        for r in rows:
            s=native.statement(r,meta['snapshot']['as_of'],ix)
            if not s:continue
            rid=r['record_id'];statements[rid]=s
            for link in s['linked_sources']:
                target=link['target']['record_id'];neighbors[rid].add(target);neighbors[target].add(rid)
                edges.append({'from':rid,'to':target,'field':link['field'],'meaning':'explicit_typed_source_reference'})
        chosen=[];seen={record};todo=deque([record])
        while todo:
            rid=todo.popleft();chosen.append(rid)
            for other in sorted(neighbors[rid]):
                if other not in seen:seen.add(other);todo.append(other)
        # Traverse the full connected component for honest omission counts; return
        # only the bounded prefix, never a false "complete chart" claim.
        included=set(chosen[:max_records]);nodes=[];issues=[]
        for rid in chosen[:max_records]:
            r=ix['records'][rid];s=statements.get(rid);fields={k:context_builder.clean(r[k]) for k in r.get('_warehouse_fields',[]) if k in r}
            fields=context_builder.structured_without_text(fields,list(text_fields(r)))
            encoded=context_builder.encode(fields);omitted_fields=len(encoded)>5000
            nodes.append({**preview(r),'record_hash':digest(r),'root':rid==record,
                          'relation':s['relation'] if s else 'unmapped_literal_account',
                          'domain':s['domain'] if s else 'other','fields':{} if omitted_fields else fields,
                          'fields_omitted_for_display_budget':omitted_fields,
                          'clinical_clock':native.clock(r,meta['snapshot']['as_of'],ix),
                          'narrative':narrative_preview(r) if rid==record else None})
            for issue in s.get('link_issues',[]) if s else []:
                issues.append({'source_record':rid,**issue})
        kept=[e for e in edges if e['from'] in included and e['to'] in included]
        boundary=[e for e in edges if (e['from'] in included)!=(e['to'] in included)]
        identity=digest([VERSION,native.identity(),meta['source_digest'],patient,record,
                         [(rid,digest(r)) for rid,r in sorted(ix['records'].items())],max_records])
        return {'schema':VERSION,'id':identity,'synthetic':True,'snapshot':meta['snapshot'],'patient_id':patient,
                'root_record':record,'nodes':nodes,'edges':kept,'unresolved_references':issues,
                'component_records':len(chosen),'displayed_records':len(nodes),'omitted_records':len(chosen)-len(nodes),
                'boundary_links':boundary,'context_streams':sorted({n['stream'] for n in nodes}),
                'context_scope':'Context preparation covers all received records in these categories for this patient; it is not limited to displayed nodes.',
                'new_paid_api_calls':0,'clinical_truth_established':False,'limits':LIMITS}
    finally:db.close()
