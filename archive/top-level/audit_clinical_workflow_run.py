"""Verify every reported fact against the visible synthetic sources and cached result.

This checks provenance, not the correctness of clinical interpretation. Hidden truth
is never opened. The source dataset and historical result files are not modified.
"""
from pathlib import Path
from collections import Counter
import hashlib,json,re,time
from clinical_core.agere.warehouse import Warehouse
from clinical_core.agere.narrative_sources import text_fields
from clinical_core.contracts import digest

ROOT=Path(__file__).resolve().parent
RUN=ROOT/'artifacts/agere-clinical-workflows-v2'

def value_at(record,field):
    match=re.fullmatch(r'(\w+)\[(\d+)\]',field)
    return record[match[1]][int(match[2])] if match else record.get(field)

def audit():
    meta=json.loads((RUN/'run.json').read_text());snap=meta['snapshot'];t=time.perf_counter()
    w=Warehouse(ROOT/'artifacts/agere-full/warehouse.sqlite3')
    try:
        patients,observed=w.snapshot(snap['release'],snap['as_of'])
        bindings={r['record_id']:r for r in json.loads((ROOT/'artifacts/agere-full/document-extractions.json').read_text())['records']}
        for p in patients.values():
            for r in p['records']:
                if r['record_id'] in bindings:
                    b=bindings[r['record_id']]
                    assert r['source_table']=='documents' and b['patient_id']==r['patient_id']
                    r.update(extracted_text=b['text'],extraction_method=b['method'])
        code={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in meta['source_code_hashes']}
        assert code==meta['source_code_hashes'],'Run dependencies changed; cannot claim matching cache replay'
        specs=json.loads((ROOT/'config/agere/programs.json').read_text());code_hash=digest(code)
        counts=Counter();errors=[];keys=[]
        with (RUN/'patients.jsonl').open() as stream:
            for line in stream:
                row=json.loads(line);pid=row['patient_id'];p=patients[pid]
                records={r['record_id']:r for r in p['records']}
                fields={rid:{x['field']:x['text'] for x in text_fields(r)} for rid,r in records.items()}
                key=digest({'namespace':'clinical-review/1','records':p,'as_of':snap['as_of'],'rules':specs,'code':code_hash});keys.append(key)
                cached=w.cached(key);counts['patients']+=1
                if cached==row:counts['exact_patient_cache_matches']+=1
                else:errors.append({'patient_id':pid,'issue':'cache_mismatch_or_miss'})
                ids={f['fact_id'] for f in row['state']['facts']}
                for workflow in row['workflows'].values():
                    if not set(workflow['support_fact_ids'])<=ids:errors.append({'patient_id':pid,'issue':'invented_workflow_support'})
                for f in row['state']['facts']:
                    counts['facts']+=1
                    for e in f['evidence']:
                        counts['evidence_bindings']+=1
                        try:
                            r=records[e['record_id']]
                            assert r['patient_id']==pid==f['patient_id']
                            for attr in ('source_table','event_date','available_date'):assert e[attr]==r[attr],attr
                            if 'quote' in e:
                                text=fields[e['record_id']][e['field']]
                                assert text[e['start']:e['end']]==e['quote'],'quote_offset'
                                if 'context' in e:assert text[e['context_start']:e['context_end']]==e['context'],'context_offset'
                                counts['exact_text_bindings']+=1
                            else:
                                assert value_at(r,e['field'])==e['value'],'structured_value'
                                counts['exact_structured_bindings']+=1
                        except (AssertionError,KeyError,IndexError,TypeError) as err:
                            errors.append({'patient_id':pid,'fact_id':f['fact_id'],'record_id':e.get('record_id'),'issue':str(err)})
        result={'synthetic':True,'status':'passed' if not errors else 'failed','counts':dict(counts),'errors':errors,
            'snapshot_matches':observed==snap,'cache_key_digest_matches':digest(keys)==meta['cache_key_digest'],
            'elapsed_seconds':time.perf_counter()-t,'new_paid_api_calls':0,
            'scope':'Exact source, chronology, patient identity, workflow support and cached output integrity; not clinical accuracy'}
        if not result['snapshot_matches'] or not result['cache_key_digest_matches']:result['status']='failed'
        (RUN/'source-audit.json').write_text(json.dumps(result,indent=2));return result
    finally:w.close()

if __name__=='__main__':print(json.dumps(audit(),indent=2))
