"""Received diagnostic field joins, medication accounts and clinical review wiring."""
import argparse,json,sqlite3,zlib
from collections import Counter
from pathlib import Path
from clinical_core.agere.clinical import clinical_inputs as ci,care_protocols as protocols
from clinical_core.agere.warehouse_activation import code_identity as qualification_code


def main():
    p=argparse.ArgumentParser();p.add_argument('--build',type=Path,required=True);args=p.parse_args()
    out=args.build.resolve();path=out/'stack';meta=json.loads((path/'run.json').read_text())
    source=Path(meta['snapshot']['source_dataset']['source'])
    src=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
    db=sqlite3.connect((path/'stack.sqlite3').as_uri()+'?mode=ro',uri=True)
    try:
        # Independent public received-field query. No private clinical targets.
        expected={}
        for rid,pid,raw in src.execute("SELECT record_id,patient_id,facts FROM source_records WHERE source_table IN ('diagnostic_test_reports','received_diagnostic_reports') AND json_extract(facts,'$.test_kind')='echocardiogram'"):
            f=json.loads(raw);value=f['measurements'].get('LVEF_percent')
            expected[rid]=(pid,value,f['test_quality'],f['report_status'],None,'measurements.LVEF_percent')
        for rid,pid,raw in src.execute("SELECT record_id,patient_id,facts FROM source_records WHERE source_table='inpatient_cardiac_reports' AND json_extract(facts,'$.test')='transthoracic_echocardiogram'"):
            f=json.loads(raw)
            expected[rid]=(pid,f.get('LVEF_percent'),f.get('image_quality'),f.get('status'),f.get('acquired_time','')[:10] or None,'LVEF_percent')
        ids=[pid for pid, in db.execute('SELECT id FROM patients ORDER BY id')]
        low=set();actual={};counts=Counter();examples=[];relations=Counter();native_patients=set()
        for pid in ids:
            records=[json.loads(raw) for raw, in db.execute('SELECT payload FROM records WHERE patient=? ORDER BY id',(pid,))]
            rows=ci.measurements(pid,records,[],meta['snapshot']['as_of']).get('lvef',[])
            for row in rows:
                rid=row['evidence'][0]['record_id'];actual[rid]=row
                patient,value,quality,status,clinical_date,field=expected[rid]
                assert patient==pid and row['evidence'][0]['value']==value
                assert row['evidence'][0]['field']==field
                assert row['clinical_date']==clinical_date
                usable=type(value) in (int,float) and 0<=value<=100 and ((quality=='adequate' and status in ('final','corrected','amended')) if field.startswith('measurements.') else (quality=='adequate_for_global_function' and status=='performed_final'))
                # Validate original report/session/order links independently.
                for link in row['linked_sources']:
                    raw=src.execute('SELECT patient_id,source_table,available_date FROM source_records WHERE record_id=?',(link['record_id'],)).fetchone()
                    assert raw is not None and raw[:2]==(pid,link['source_table']) and raw[2]<=meta['snapshot']['as_of']
                assert bool(row['exclusion']) is not usable,(rid,row['exclusion'])
                counts[row['exclusion'] or 'usable_literal_measurement']+=1
                if usable and value<=40:
                    low.add(pid)
                    if len(examples)<3:examples.append(row)
            native=json.loads(db.execute("SELECT payload FROM analyses WHERE patient=? AND operator='warehouse_interpretation'",(pid,)).fetchone()[0])
            for s in native['statements']:
                relations[s['relation']]+=1
                if s['relation']=='reported_product_use':
                    native_patients.add(pid)
                    assert s['reported_use'] is None and s['clinical_truth_established'] is False
        assert set(actual)==set(expected)
        # Exercise the existing general clinical protocol on all patients. Source
        # history can nominate review; unreviewed notes cannot become accepted EF.
        _,_,result=protocols.execute(path,ids,{'protocol':'reduced_ef_therapy_review','filter':'all'})
        (out/'diagnostic-protocol.json').write_text(json.dumps(result,indent=2)+'\n')
        storage=protocols.storage(path);prepared=protocols.prepare(path,{'protocol':'reduced_ef_therapy_review','filter':'all'})
        supported=set()
        try:
            for pid in ids:
                key=protocols.receipt_id(prepared,pid)
                row=json.loads(zlib.decompress(storage.execute('SELECT payload FROM receipts WHERE id=?',(key,)).fetchone()[0]))
                if row['bindings']['low_ef_history']['value'] is True:supported.add(pid)
                assert not row['clinical_eligibility_established'] and not row['clinical_execution_authorized']
        finally:storage.close()
        assert supported==low,(len(supported),len(low))
        receipt={'synthetic':True,'source_snapshot':meta['snapshot'],'patients':len(ids),
                 'qualification_code_identity':qualification_code(),
                 'received_lvef_records':len(actual),'measurement_states':dict(counts),
                 'received_lvef_records_with_explicit_acquisition_date':sum(r['clinical_date'] is not None for r in actual.values()),
                 'patients_with_usable_received_lvef_at_or_below_40':len(low),
                 'protocol_positive_measurement_population_matches_public_fields':True,
                 'protocol_status_counts':result['status_counts'],
                 'native_relation_counts':dict(relations),'patients_with_reported_product_accounts':len(native_patients),
                 'measurement_examples':examples,'new_paid_api_calls':0,'new_model_weights':False,
                 'clinical_accuracy_or_eligibility_established':False}
        (out/'diagnostic-verification.json').write_text(json.dumps(receipt,indent=2)+'\n')
        print(json.dumps({k:v for k,v in receipt.items() if k not in ('source_snapshot','measurement_examples')},indent=2),flush=True)
    finally:src.close();db.close()


if __name__=='__main__':main()
