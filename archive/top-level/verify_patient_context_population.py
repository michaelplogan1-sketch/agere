"""Full-roster literal projection audit against received runtime records only."""
import json,sqlite3
from collections import Counter
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import patient_context as p

def main():
    path=stack_path();counts=Counter();per_patient=[]
    with sqlite3.connect(path/'stack.sqlite3') as db:
        patients=[r[0] for r in db.execute('SELECT id FROM patients ORDER BY id')]
        for index,patient in enumerate(patients):
            result=p.prepare(path,{'patient_id':patient,'as_of':'2029-08-15'});count=0
            assert result['current_consent']=='not_established' and not result['clinical_execution_authorized']
            for report in result['reports']:
                raw=json.loads(db.execute('SELECT payload FROM records WHERE id=?',(report['record_id'],)).fetchone()[0])
                assert report['record_hash']==p.digest(raw) and report['patient_id']==raw['patient_id']==patient
                assert raw['available_date']<=result['as_of']
                expected=set(raw.get('_warehouse_fields',raw))&p.FIELDS[raw['source_table']]
                assert set(report['fields'])|set(report['unrepresented_fields'])==expected
                for key,value in report['fields'].items():
                    assert type(raw[key])==type(value) and raw[key]==value;count+=1
                counts['received_reports']+=1;counts['superseded_reports']+=bool(report['superseded_by']);counts['unknown_source_dates']+=report['source_date'] is None
            counts['patients']+=1;counts['patients_with_reports']+=bool(result['reports']);counts['literal_differences']+=len(result['literal_differences']);counts['projected_values']+=count
            per_patient.append({'patient_id':patient,'context_id':result['id'],'reports':len(result['reports']),'fields':count,'literal_differences':len(result['literal_differences'])})
            if (index+1)%400==0:print('Audited',index+1,'received patient contexts',flush=True)
    value={'scope':'Exact literal projection only; not clinician labels, hidden generator truth or clinical validation','counts':dict(counts),'patients':per_patient,'new_provider_calls':0,'source_categories':sorted(p.FIELDS),'identity':p.identity()}
    Path('artifacts/agere-patient-context-v1/population-audit.json').write_text(json.dumps(value,indent=2)+'\n');print(json.dumps(counts,indent=2))
if __name__=='__main__':main()
