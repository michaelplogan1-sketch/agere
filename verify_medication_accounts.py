"""Public-source medication item audit and full question/acquisition execution.

This audit uses received source facts, never private generator labels. It verifies
links/counts/source fidelity, not clinical correctness or drug-taking behavior.
"""
import argparse,json,sqlite3,time
from pathlib import Path
from collections import Counter,defaultdict
from unittest.mock import patch
from clinical_core.agere.clinical import medication_accounts as m,evidence_requirements as er,native_requirements as nr
from clinical_core.agere.clinical import evidence_acquisition as acquisition,analysis_graph,graph_planner
from clinical_core.agere.clinical.graph_workbench import requirement_examples
from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.contracts import digest


def write(p,v):p.write_text(json.dumps(v,indent=2,allow_nan=False)+'\n')


def main():
    p=argparse.ArgumentParser();p.add_argument('--stack',type=Path,required=True);p.add_argument('--out',type=Path,required=True);args=p.parse_args()
    out=args.out;out.mkdir(parents=True,exist_ok=True);meta=json.loads((args.stack/'run.json').read_text());asof=meta['snapshot']['as_of']
    src=sqlite3.connect(Path(meta['snapshot']['source_dataset']['source']).as_uri()+'?mode=ro',uri=True)
    db=sqlite3.connect((args.stack/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    ids=sorted(r[0] for r in src.execute('SELECT patient_id FROM patients'));assert ids==sorted(r[0] for r in db.execute('SELECT id FROM patients'))
    raw={};by_patient=defaultdict(dict)
    # Independent literal input adapter, using public warehouse columns/facts.
    sql='SELECT record_id,patient_id,source_table,event_date,facts FROM source_records WHERE source_table IN ('+','.join('?' for _ in m.STREAMS)+') AND available_date<=?'
    for rid,pid,stream,day,f in src.execute(sql,[*m.STREAMS,asof]):
        raw[rid]={'record_id':rid,'patient_id':pid,'stream':stream,'date':day,'facts':json.loads(f)};by_patient[pid][rid]=raw[rid]
    expected={'medication_regimen_confirmation':{},'medication_label_report':{}};expected_entries=0;expected_reports=0
    for pid in ids:
        rows=by_patient[pid];values=defaultdict(list);reviewed=set()
        for r in rows.values():
            if r['stream']!='medication_review_decisions':continue
            for item in r['facts']['entries']:
                expected_entries+=1;rid=item['reported_entry_record_id'];reviewed.add(rid)
                report=rows.get(rid);copy=rows.get(r['facts'].get('received_pharmacy_information_record_id'))
                valid=(report is not None and report['stream']=='reported_medication_entries' and copy is not None and copy['stream']=='received_pharmacy_information')
                if valid:
                    interview=rows.get(report['facts'].get('interview_record_id'));historical=rows.get(report['facts'].get('historical_list_record_id'))
                    origin=rows.get(copy['facts'].get('original_response_record_id'));request=rows.get(copy['facts'].get('request_record_id'))
                    valid=bool(interview and interview['stream']=='medication_history_interviews' and
                        (not report['facts'].get('historical_list_record_id') or historical and historical['stream']=='medication_lists') and
                        origin and origin['stream']=='pharmacy_information_responses' and request and request['stream']=='medication_information_requests')
                    if valid:
                        assert r['facts']['interview_record_id']==report['facts']['interview_record_id']==request['facts']['interview_record_id']
                        assert item['medication_name'].casefold()==report['facts']['medication_name_reported'].casefold()
                        assert copy['facts']['request_record_id']==origin['facts']['request_record_id']
                        assert copy['facts']['items']==origin['facts']['items']
                        matching=[x for x in copy['facts']['items'] if x['requested_name'].casefold()==item['medication_name'].casefold()]
                        assert bool(matching)==item['dispensing_label_available']
                        for x in matching:assert rows[x['original_dispense_record_id']]['stream']=='pharmacy_dispenses'
                values[rid].append((r['date'],item,valid))
        for r in rows.values():
            if r['stream']=='reported_medication_entries':
                expected_reports+=1
                if r['record_id'] not in reviewed:values[r['record_id']].append((r['date'],None,False))
        results={k:[] for k in expected}
        for entries in values.values():
            assert len(entries)==1,'Audit must be extended for repeated public review accounts'
            day,item,valid=entries[0];valid=valid and day is not None
            results['medication_regimen_confirmation'].append(item['disposition'] in ('current_regimen_not_verified','historical_dispensing_label_available_current_regimen_requires_confirmation') if valid else None)
            results['medication_label_report'].append(item['dispensing_label_available'] if valid else None)
        for key,v in results.items():expected[key][pid]='supported' if True in v else 'unresolved' if not v or None in v else 'contradicted'
    snap=nr.snapshot(args.stack,[m.RELATION]);counts=Counter();examples={};issues=Counter()
    for pid in ids:
        report=m.load(db,pid,snap['medication'],asof);counts['patients_with_accounts']+=bool(report['statements'])
        for s in report['statements']:
            counts['item_accounts']+=1;issues.update(x['reason'] for x in s['link_issues'])
            for e in s['evidence']:
                if e['field_path'][0] in raw[e['record_id']]['facts']:
                    value=raw[e['record_id']]['facts']
                    for part in e['field_path']:value=value[part]
                    assert value==e['value'];counts['public_literal_fields_verified']+=1
            chain=s['reconciliation'];assert not chain['ingestion_established'] and not chain['current_regimen_established']
            counts['copied_item_links']+=len(chain['copy_lineage'])
            counts['review_entries']+=s['values']['review'] is not None
            key='unresolved' if s['link_issues'] else 'label' if chain['pharmacy_items'] else 'no_label'
            examples.setdefault(key,{'patient_id':pid,'statement':s})
        counts.update('state_'+s['status'] for s in report['states'])
    assert counts['review_entries']==expected_entries
    write(out/'examples.json',examples);stack=Stack(args.stack,allow_live=False);checks=[]
    try:
        for key,statuses in expected.items():
            ex=next(e for e in requirement_examples() if e['graph']['nodes'][1]['parameters']['contract']==key)
            result=stack.compose(ex['question']);write(out/(key+'-question.json'),result)
            detail=result['results'][-1]['result'];assert detail['patients_evaluated']==len(ids)
            yes,unknown,replay=er.execute(args.stack,ids,{'contract':key,'filter':'supported'})
            assert detail['status_counts']==dict(Counter(statuses.values()))
            assert yes=={pid for pid,status in statuses.items() if status=='supported'}
            assert unknown=={pid for pid,status in statuses.items() if status=='unresolved'}
            assert replay['processing_counts']=={'patient_cache_hits':len(ids)}
            write(out/(key+'-receipt.json'),er.inspect(args.stack,replay['patients'][0]['id']))
            checks.append({'contract':key,'counts':detail['status_counts'],'exact_public_patient_sets_match':True,'repeat':replay['processing_counts']})
            print(json.dumps(checks[-1]),flush=True)
        proposal=acquisition.prepare(args.stack,list(expected),ids)
        write(out/'acquisition-summary.json',acquisition.summary_view(proposal))
        listing=acquisition.list_items(args.stack,proposal['id'],offset=0,limit=5);write(out/'acquisition-items.json',listing)
        if listing['items']:write(out/'acquisition-inspection.json',acquisition.inspect_item(args.stack,proposal['id'],listing['items'][0]['id']))
        sizes=[]
        with patch.object(graph_planner,'send',side_effect=lambda body,*a,**kw:sizes.append(len(json.dumps(body,allow_nan=False).encode()))):
            graph_planner.propose('x'*1500,None,None,snapshot=meta['snapshot'],graph_catalog=analysis_graph.catalog(args.stack),observed_values=json.loads((args.stack/'cohort-values.json').read_text()))
        assert sizes[0]<=20000,sizes
        receipt={'schema':'medication-item-verification/1','synthetic':True,'snapshot':meta['snapshot'],'patients':len(ids),
          'public_review_entries':expected_entries,'public_reported_entries':expected_reports,'counts':dict(counts),'link_issues':dict(issues),
          'contracts':checks,'acquisition_proposal':proposal['id'],'planner_request_bytes':sizes[0],
          'code_identity':m.identity(),'requirement_code_identity':er.code_identity(),'new_paid_api_calls':0,
          'clinical_accuracy_measured':False,'new_model_weights':False}
        write(out/'verification.json',receipt);print(json.dumps(receipt),flush=True)
    finally:stack.close();db.close();src.close()

if __name__=='__main__':main()
