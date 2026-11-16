"""Independent public-source joins and complete-set checks for native meanings."""
import argparse
from collections import Counter,defaultdict
from datetime import datetime
import json,sqlite3
from pathlib import Path
from clinical_core.agere.clinical.cohorts import select,empty


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--build',type=Path,required=True)
    out=parser.parse_args().build.resolve()
    connection=json.loads((out/'connection.json').read_text());asof=connection['snapshot']['as_of']
    source=Path(connection['snapshot']['source_dataset']['source'])
    src=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
    db=sqlite3.connect((out/'stack'/'stack.sqlite3').as_uri()+'?mode=ro',uri=True)
    def explicit_day(value):
        dt=datetime.fromisoformat(value)
        assert dt.tzinfo is not None
        return dt.date().isoformat()
    try:
        expected_nonuse={pid for pid, in src.execute("SELECT DISTINCT patient_id FROM source_records WHERE source_table='medication_followup_reports' AND json_extract(facts,'$.reported_intent')='not_using'")}
        query={**empty(),'all':[{'kind':'record','stream':'medication_followup_reports',
            'conditions':[{'field':'reported_intent','op':'eq','value':'not_using'}],
            'window':{'basis':'all_observed','start':'','end':''}}]}
        selected=select(out/'stack',query)
        assert set(selected['patient_ids'])==expected_nonuse

        # Explicit public record joins, independent of the engine's clock code.
        joined={}
        for rid,pid,time,direct in src.execute("""SELECT l.record_id,l.patient_id,
            json_extract(s.facts,'$.collected_time'),json_extract(l.facts,'$.specimen_time')
            FROM source_records l JOIN source_records s
            ON s.record_id=json_extract(l.facts,'$.specimen_record_id')
            AND s.patient_id=l.patient_id AND s.source_table='laboratory_specimens'
            WHERE l.source_table='labs' AND s.available_date<=?""",(asof,)):
            day=explicit_day(time)
            if direct:assert explicit_day(direct)==day
            assert day<=asof
            joined[rid]=(pid,day)
        for rid,(pid,day) in joined.items():
            actual=db.execute('SELECT patient,clinical_date,status,payload FROM clinical_events WHERE record=?',(rid,)).fetchone()
            assert actual[:3]==(pid,day,'explicit_date')
            evidence=json.loads(actual[3])['clinical_date_evidence']
            assert any(e['source_table']=='laboratory_specimens' and e['field']=='collected_time' for e in evidence)
        missing_specs={rid for rid, in src.execute("""SELECT l.record_id FROM source_records l
            LEFT JOIN source_records s ON s.record_id=json_extract(l.facts,'$.specimen_record_id')
            AND s.patient_id=l.patient_id AND s.source_table='laboratory_specimens' AND s.available_date<=?
            WHERE l.source_table='labs' AND json_extract(l.facts,'$.specimen_record_id') IS NOT NULL
            AND s.record_id IS NULL""",(asof,))}
        for rid in missing_specs:
            assert db.execute('SELECT clinical_date,status FROM clinical_events WHERE record=?',(rid,)).fetchone()==(None,'unresolved_source_link')

        adt=defaultdict(set);roles={}
        for rid,pid,event,start,end in src.execute("""SELECT a.record_id,a.patient_id,json_extract(a.facts,'$.event'),
             json_extract(e.facts,'$.start_time'),json_extract(e.facts,'$.end_time')
             FROM source_records a JOIN source_records e ON a.encounter_id=e.encounter_id AND a.patient_id=e.patient_id
             WHERE a.source_table='adt' AND e.source_table='encounters' AND e.available_date<=?
             AND json_extract(e.facts,'$.type') IN ('inpatient','observation')""",(asof,)):
            value=end if event=='discharge' else start
            if value:adt[rid].add((pid,explicit_day(value)));roles[rid]=event
        role_counts=Counter()
        for rid,values in adt.items():
            assert len(values)==1
            pid,day=next(iter(values))
            assert db.execute('SELECT patient,clinical_date,status FROM clinical_events WHERE record=?',(rid,)).fetchone()==(pid,day,'explicit_date')
            role_counts[roles[rid]]+=1

        relations=Counter();states=Counter();steps=Counter();patients=0;reported_nonuse=set();example=None
        for pid,raw in db.execute("SELECT patient,payload FROM analyses WHERE operator='warehouse_interpretation' ORDER BY patient"):
            result=json.loads(raw);patients+=1
            assert result['clinical_state_established'] is False and result['care_gap_established'] is False
            relations.update(result['relation_counts']);states.update(result['state_counts'])
            steps.update(s['action'] for s in result['evidence_acquisition_steps'])
            for statement in result['statements']:
                if statement['relation']=='reported_use' and statement['values'].get('reported_intent')=='not_using':
                    assert statement['reported_use'] is False;reported_nonuse.add(pid)
                    if example is None:example=statement
        assert patients==connection['patients'] and reported_nonuse==expected_nonuse
        fields=dict(db.execute("""SELECT json_extract(source,'$.field'),count(*) FROM passages
           WHERE json_extract(source,'$.field') IN ('goc','course_summary','history_text','ordering_reason') GROUP BY 1"""))
        assert set(fields)=={'goc','course_summary','history_text','ordering_reason'}
        annotations=Counter()
        adb=sqlite3.connect((out/'annotations'/'annotations.sqlite3').as_uri()+'?mode=ro',uri=True)
        try:
            for raw, in adb.execute("""SELECT payload FROM annotations WHERE json_extract(payload,'$.source.field')
                IN ('goc','course_summary','history_text','ordering_reason')"""):
                annotation=json.loads(raw);e=annotation['source']
                pid,facts=src.execute('SELECT patient_id,facts FROM source_records WHERE record_id=?',(e['record_id'],)).fetchone()
                assert pid==annotation['patient_id']
                text=json.loads(facts)[e['field']]
                assert text[e['start']:e['end']]==e['quote']
                annotations[e['field']]+=1
        finally:adb.close()
        result={'synthetic':True,'snapshot':connection['snapshot'],'patients_interpreted':patients,
            'linked_lab_collection_dates_verified':len(joined),'linked_adt_clinical_dates_verified':dict(role_counts),
            'unresolved_lab_specimen_links_preserved':len(missing_specs),
            'patients_with_any_explicit_not_using_report':len(expected_nonuse),
            'nonuse_scope':'Any received not_using report, not current medication use or adherence.',
            'typed_cohort_matches_public_sql':True,'native_report_matches_public_sql':True,
            'native_relation_counts':dict(relations),'native_state_counts':dict(states),
            'evidence_acquisition_steps':dict(steps),'new_narrative_field_passages':fields,
            'new_field_annotation_quotes_verified':dict(annotations),
            'nonuse_evidence_example':example,'encoder_stats':json.loads((out/'stack'/'run.json').read_text())['encoder_stats'],
            'new_paid_api_calls':0,'clinical_accuracy_or_outcome_improvement_measured':False}
        (out/'interpretation-verification.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k not in ('snapshot','nonuse_evidence_example')},indent=2),flush=True)
    finally:src.close();db.close()


if __name__=='__main__':main()
