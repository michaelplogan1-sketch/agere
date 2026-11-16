"""Qualify received v1.23 accounts and shared question paths; public data only."""
import argparse,json,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere import received_source_catalog as C,narrative_sources as N
from clinical_core.agere.clinical import context_builder as B,program_documents as D
from clinical_core.agere.clinical import evidence_requirements as R,measure_composition as M,program_work as W
from clinical_core.agere.clinical.graph_workbench import requirement_examples
from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.agere.warehouse_activation import code_identity

KEYS=('prescriber_reassessment_request','renewal_clarification_deferral','transport_support_report','community_service_delivery_report')


def write(path,value):path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def copy_packages(previous,path):
    copied=[]
    # Immutable source/rule definitions only. No prior reviews, receipts or work.
    for item in D.packages(previous):
        old=D.load_package(previous,item['id'])
        spec={k:old[k] for k in ('document','spec','bindings','input_bindings','provenance','role')}
        new=D.install(path,spec);assert new['id']==item['id']
        assert D.inspect_package(path,new['id'])['review']['revision']==0
        copied.append(new['id'])
    return copied


def main():
    p=argparse.ArgumentParser();p.add_argument('--build',type=Path,required=True)
    p.add_argument('--previous',type=Path,required=True);p.add_argument('--inventory',type=Path,required=True);args=p.parse_args()
    out=args.build;path=out/'stack';folder=out/'source-extension';folder.mkdir(exist_ok=True)
    meta=json.loads((path/'run.json').read_text());cutoff=meta['snapshot']['as_of'];inventory=json.loads((args.inventory/'inventory.json').read_text())
    assert inventory['source_sha256']==meta['snapshot']['source_dataset']['source_sha256']
    missing=inventory.get('prior_rows_missing',0);explained=inventory.get('prior_rows_retracted',0) if inventory.get('retraction') else 0
    assert inventory.get('rebaseline') is True or (missing==explained and (inventory['prior_rows_changed_or_missing']==missing or inventory.get('restatement')))
    assert inventory['roster_digest']==meta['snapshot']['source_dataset']['original_patient_id_digest']
    src=sqlite3.connect(Path(meta['snapshot']['source_dataset']['source']).as_uri()+'?mode=ro',uri=True)
    db=sqlite3.connect((path/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    raw={};received=Counter();verified=Counter();issues=Counter();texts=Counter();samples={};results=[]
    ids=[p[0] for p in db.execute('SELECT id FROM patients ORDER BY id')]
    try:
        tables=sorted(C.DEFINITIONS);slots=','.join('?' for _ in tables)
        for rid,pid,table,date,available,payload in src.execute('SELECT record_id,patient_id,source_table,event_date,available_date,facts FROM source_records WHERE source_table IN ('+slots+')',tables):
            assert available<=cutoff;raw[rid]={'patient':pid,'table':table,'date':date,'facts':json.loads(payload)};received[table]+=1
        print(json.dumps({'stage':'public_received_extension','records':len(raw)}),flush=True)
        for pid,payload in db.execute("SELECT patient,payload FROM analyses WHERE operator='warehouse_interpretation'"):
            report=json.loads(payload)
            for s in report['statements']:
                table=s['evidence'][0]['source_table'] if s['evidence'] else ''
                if table not in C.DEFINITIONS:continue
                spec=C.DEFINITIONS[table];rid=s['evidence'][0]['record_id'];record=raw[rid]
                assert record['patient']==pid and s['relation']==spec['relation'] and not s['clinical_truth_established']
                expected={k:v for k,v in record['facts'].items() if k in spec['fields']};assert s['values']==expected
                assert {e['field']:e['value'] for e in s['evidence']}==expected
                for link in s['linked_sources']:
                    target=link['target'];field=link['field'].split('[')[0]
                    linked=src.execute('SELECT patient_id,source_table,available_date FROM source_records WHERE record_id=?',(target['record_id'],)).fetchone()
                    assert linked and linked[0]==pid and linked[1] in spec['links'][field] and linked[2]<=cutoff
                    verified['typed_same_patient_links']+=1
                issues.update(x['reason'] for x in s['link_issues']);verified['literal_fields']+=len(expected);verified['source_accounts']+=1
                received[table]-=1
        assert not any(received.values()),received
        print(json.dumps({'stage':'literal_accounts_verified','checks':dict(verified)}),flush=True)
        # Inspect real nested payloads, every declared new narrative field, and
        # the resulting context units without executing any source text.
        for table,payload in db.execute('SELECT stream,payload FROM records WHERE stream IN ('+slots+')',tables):
            r=json.loads(payload);parts=list(N.text_fields(r));units=B.units(r['patient_id'],[r],[],{'streams':[table],'concepts':[]},cutoff)
            for part in parts:
                found=[u for u in units if u['kind']=='narrative' and u['field']==part['field']]
                assert all(part['text'][u['start']:u['end']]==u['text'] for u in found)
                texts[table+'.'+part['field']]+=1
            if table=='received_home_health_documents' and isinstance(r.get('content'),dict) and 'medication_changed' in r['content']:
                assert units[0]['reported_fields']['content']['medication_changed']==r['content']['medication_changed']
                samples.setdefault('mixed_text_structured_context',{'patient_id':r['patient_id'],'record_id':r['record_id'],'units':units})
                verified['nested_medication_siblings_preserved']+=1
        copied=copy_packages(args.previous/'stack',path)
        print(json.dumps({'stage':'source_context_and_packages_verified','packages':len(copied)}),flush=True)
        stack=Stack(path,allow_live=False)
        try:
            for example in requirement_examples():
                key=example['graph']['nodes'][1]['parameters']['contract']
                if key not in KEYS:continue
                spec=R.definition(key);source=spec['bindings']['report']['source'];relation=source['relation'];condition=source['conditions'][0]
                # Independent latest-account comparison on public literal rows.
                table=next(t for t,s in C.DEFINITIONS.items() if s['relation']==relation);groups=defaultdict(list)
                for rid,r in raw.items():
                    if r['table']!=table:continue
                    f=r['facts'];entity=(f.get('pharmacy_prescription_id') or f.get('prescription_id') or f.get('account_id') or f.get('document_id') or f.get('medication_order_record_id') or f.get('referral_record_id') or str(f.get('medication_name',f.get('medication_name_reported',f.get('name',rid)))).casefold())
                    groups[r['patient'],entity].append((rid,r))
                outcomes=defaultdict(list)
                for (pid,_),rows in groups.items():
                    if any(not r['date'] for _,r in rows):outcomes[pid].append(None);continue
                    latest=max(r['date'] for _,r in rows);current=[r for _,r in rows if r['date']==latest];valid=True
                    for r in current:
                        for field,targets in C.DEFINITIONS[table]['links'].items():
                            refs=r['facts'].get(field);refs=refs if isinstance(refs,list) else [refs] if refs is not None else []
                            for ref in refs:
                                linked=src.execute('SELECT patient_id,source_table,available_date FROM source_records WHERE record_id=?',(ref,)).fetchone()
                                valid &= bool(linked and linked[0]==pid and linked[1] in targets and linked[2]<=cutoff)
                    signatures={json.dumps({k:v for k,v in r['facts'].items() if k in C.DEFINITIONS[table]['fields'] and not k.endswith('_id') and k not in ('respondent','status_source')},sort_keys=True) for r in current}
                    v=current[0]['facts'].get(condition['field']);outcomes[pid].append(v==condition['value'] if valid and len(signatures)==1 and type(v) is type(condition['value']) else None)
                wanted={pid:'supported' if True in outcomes[pid] else 'unresolved' if not outcomes[pid] or None in outcomes[pid] else 'contradicted' for pid in ids}
                result=stack.compose(example['question']);write(folder/(key+'.json'),result);assert result['new_paid_api_calls']==0
                selected,unknown,repeat=R.execute(path,ids,{'contract':key,'filter':'supported'})
                assert selected=={p for p,v in wanted.items() if v=='supported'}
                assert unknown=={p for p,v in wanted.items() if v=='unresolved'}
                assert repeat['processing_counts']=={'patient_cache_hits':len(ids)}
                results.append({'contract':key,'patients':len(ids),'states':dict(Counter(wanted.values())),'public_source_sets_match':True,'replay':repeat['processing_counts'],'question_result_id':result['result_id']})
                print(json.dumps(results[-1]),flush=True)
            # The migrated rule definitions remain usable, but never bring old
            # source-specific result IDs or human-review authority with them.
            quoted=next(k for k in copied if D.load_package(path,k)['spec']['version']=='2' and D.load_package(path,k)['role']=='review')
            result=stack.compose('Prepare evidence requests for imported program package '+quoted+' across all synthetic patients.')
            assert result['new_paid_api_calls']==0;write(folder/'quoted-acquisition.json',result)
            assert result['results'][-1]['result']['patients_evaluated']==len(ids)
            components={role:next(k for k in copied if D.load_package(path,k)['role']==role) for role in ('denominator','numerator')}
            result=stack.compose(M.question(components['denominator'],components['numerator']));write(folder/'unbound-measure.json',result)
            assert result['results'][-1]['result']['counts']['unresolved_measure_patients']==len(ids)
            graph=result['graph'];params=graph['nodes'][-1]['parameters']
            params['components']['exclusion']={'not_applied':'Descriptive received affordability report statistic only.'}
            params['components']['attribution']={'not_applied':'Entire observed synthetic roster; no payer attribution applied.'}
            result=stack.run_plan(graph);write(folder/'descriptive-measure.json',result);measure=result['results'][-1]['result']
            from verify_native_requirements import expected,CASES
            wanted=expected(src,ids,cutoff,CASES['native_affordability_report']);totals=Counter(wanted.values())
            assert measure['counts']['known_numerator']==totals['supported']
            assert measure['counts']['known_denominator']==totals['supported']+totals['contradicted']
            assert measure['counts']['unresolved_measure_patients']==totals['unresolved']
            graph['nodes'].append({'id':'work','operation':'worklist','inputs':['population','measure'],
                'parameters':{'program':'protocol:affordability_action_review@2'},'purpose':'Prepare source-linked review assessments without work execution'})
            work=stack.run_plan(graph);write(folder/'measure-work.json',work);work_result=work['results'][-1]['result']
            wdb=W.database(path)
            try:
                proposal=W.load(wdb,'work_proposals',work_result['proposal_id'])
                assessments=[W.load(wdb,'work_assessments',key) for key in proposal['assessment_ids']]
                assert sorted(r['patient_id'] for r in assessments)==ids
                assert all(r['decision_context']['measure']['population_id']==measure['id'] for r in assessments)
                verified['current_source_measure_work_contexts']=len(assessments)
            finally:wdb.close()
            sample=samples['mixed_text_structured_context']
            graph={'interpretation':'Inspect nested received home-health text with its discrete sibling fields.',
                'coverage':'supported_observation','unmet_requirements':[],'nodes':[
                {'id':'patient','operation':'cohort','inputs':[],'purpose':'Exact source patient',
                 'parameters':{'specification':{'all':[{'kind':'patient','field':'patient_id','op':'eq','value':sample['patient_id']}],'any':[],'exclude':[]}}},
                {'id':'context','operation':'context_passes','inputs':['patient'],'purpose':'Transport every received scoped source unit',
                 'parameters':{'question':'What do the received home-health documents say about medications and service plans?',
                  'streams':['received_home_health_documents'],'concepts':[],'contract':'','program_ids':[],'max_bytes':10000}}]}
            result=stack.run_plan(graph);assert result['new_paid_api_calls']==0;write(folder/'nested-context-question.json',result)
        finally:stack.close()
        for key,value in samples.items():write(folder/(key+'.json'),value)
        receipt={'synthetic':True,'passed':True,'patients':len(ids),'source_snapshot':meta['snapshot'],'qualification_code_identity':code_identity(),
            'source_catalog_sha256':C.identity(),'new_categories':sorted(C.DEFINITIONS),'checks':dict(verified),'unresolved_source_links':dict(issues),
            'narrative_field_counts':dict(texts),'contracts':results,'immutable_packages_copied':copied,'prior_reviews_copied':0,'prior_patient_receipts_copied':0,
            'measure_counts':measure['counts'],'measure_id':measure['id'],'measure_work_counts':work_result['counts'],
            'new_paid_api_calls':0,'clinical_accuracy_measured':False,'complete_interpretation_claimed':False}
        write(out/'source-extension-verification.json',receipt)
        audit={'synthetic':True,'prior_release':inventory['previous'],'new_release':inventory['release'],'as_of':cutoff,'patients':len(ids),
            'patient_id_digest':inventory['roster_digest'],'identical_roster':not inventory.get('new_patients'),'new_patients':inventory.get('new_patients',0),'prior_received_rows':inventory['previous_records'],
            'new_received_rows':inventory['records'],'added_received_rows':inventory['records']-inventory['previous_records'],
            'source_categories':inventory['source_categories'],'prior_rows_missing_or_changed':inventory['prior_rows_changed_or_missing'],
            'manifest_source_sha256':inventory['source_sha256'],'public_inventory_hash':digest(inventory),'private_files_read':False}
        write(out/'migration-audit.json',audit)
        print(json.dumps({k:receipt[k] for k in ('passed','patients','checks','unresolved_source_links','contracts','new_paid_api_calls')},indent=2),flush=True)
    finally:src.close();db.close()


if __name__=='__main__':main()
