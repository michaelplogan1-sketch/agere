"""Independent received-span audit and preservation of prior artifacts/history."""
import hashlib
import json
import sqlite3
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
from clinical_core.agere.narrative_sources import text_fields
from clinical_core.agere.clinical import program_work, learning_cohorts as C, learning_queue as L, semantic_learning as S
from clinical_core.agere.clinical.meaning_source_dispatch import pinned

OUT=Path('artifacts/agere-reader-comparison-v1');PRIOR=Path('artifacts/agere-retrieval-source-plan-v1')


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    plan=json.loads((OUT/'predeclaration.json').read_text());report=json.loads((OUT/'http-verification.json').read_text())
    assert sha(OUT/'predeclaration.json')==report['predeclaration_sha256']
    protected=json.loads((PRIOR/'protected-for-continuation.json').read_text())['protected_files']
    for p,h in protected.items():assert sha(p)==h,p
    prior_count=len(protected)
    for p in ['clinical_core/agere/clinical/reader_comparison.py','clinical_core/agere/clinical/reader_role_coverage.py',
              'clinical_core/agere/clinical/reader_target_dates.py',str(OUT/'predeclaration.json'),
              str(OUT/'development-exclusion-register.json')]:protected[p]=sha(p)
    (OUT/'protected-for-continuation.json').write_text(json.dumps({'schema':'agere-protected-continuation/1','protected_files':protected},indent=2)+'\n')
    histories=json.loads((PRIOR/'history-baseline.json').read_text());con=program_work.database(stack_path())
    for table,old in histories.items():assert [list(r) for r in con.execute('SELECT * FROM '+table+' ORDER BY 1,2')]==old
    con.close();(OUT/'history-baseline.json').write_text(json.dumps(histories,indent=2)+'\n')
    ledger=json.loads((PRIOR/'ledger-row-baseline.json').read_text());con=sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3')
    assert con.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==len(ledger)
    for row in ledger:assert list(con.execute('SELECT * FROM calls WHERE id=?',(row[0],)).fetchone())==row
    con.close();(OUT/'ledger-row-baseline.json').write_text(json.dumps(ledger,indent=2)+'\n')
    external=[r for r in json.loads(Path('artifacts/agere-multipass-synthesis-v1/research-recovery-index.json').read_text())['files'] if r['path'].startswith('/Users/mikel/Documents/')]
    for r in external:assert sha(r['path'])==r['sha256']
    assert len(pinned(stack_path()))==33
    baseline=json.loads((OUT/'semantic-baseline.json').read_text());readings=sqlite3.connect(stack_path()/'semantic-readings.sqlite3')
    for table,b in baseline.items():
        now=[list(r) for r in readings.execute('SELECT * FROM '+table+' ORDER BY 1,2')]
        if table=='readings':
            indexed={r[0]:r for r in now}
            for old in b['rows']:assert indexed[old[0]]==old
        else:assert now==b['rows']
    meta=json.loads((stack_path()/'run.json').read_text());policy,_=L.lineage('artifacts/agere-current-source-learning-v1/acquisition/lineage',meta);protection=S.protections()
    dataset=C.dataset(stack_path(),plan['cohort_id'])[2];families={r['patient_family'] for r in dataset};templates={s for r in dataset for s in r['source_families']}
    source=sqlite3.connect((stack_path()/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True);audits=[]
    for case in plan['cases']:
        t=case['target'];assert L.eligibility(t['patient_id'],policy,protection) is None
        assert policy['patient_families'][t['patient_id']] not in families
        patient,raw=source.execute('SELECT patient,payload FROM records WHERE id=?',(t['record_id'],)).fetchone();record=json.loads(raw)
        assert patient==t['patient_id'] and digest(record)==case['source_hash']
        text=next(f['text'] for f in text_fields(record) if f['field']==t['field'])
        assert text[t['start']:t['end']]==case['quote'] and digest(L.normalize(text)) not in templates
        r=json.loads((OUT/(case['id']+'.json')).read_text());assert r['context']['text']==text and r['context']['context_complete']
        assert digest(r['context'])==case['context_digest']
        roles=[]
        for role,v in r['variants'].items():
            if v['status']!='completed':continue
            saved=json.loads(readings.execute('SELECT payload FROM readings WHERE id=?',(v['reading_id'],)).fetchone()[0])
            assert saved['context']==r['context'] and saved['target']==t and saved['source_hash']==digest(record)
            assert saved['model_identity']==r['models'][role]['model_identity'] and saved['reading']['labels']==v['labels']
            assert saved['new_paid_api_calls']==0 and saved['training_label_created'] is False
            if role=='cohort_windowed_minilm':assert saved['student']['window_coverage']['complete_source_coverage'] and not saved['student']['window_coverage']['truncated']
            roles.append(role)
        audits.append({'case_id':case['id'],'exact_quote_verified':True,'complete_source_field':True,'cohort_patient_family_overlap':False,'normalized_template_overlap':False,'verified_roles':roles})
    assert source.execute('SELECT COUNT(*) FROM patients').fetchone()[0]==1614
    assert source.execute('SELECT COUNT(*) FROM records').fetchone()[0]==1238686
    source.close();after=readings.execute('SELECT COUNT(*) FROM readings').fetchone()[0];readings.close()
    result={'status':'passed','prior_protected_files':prior_count,'protected_files':len(protected),'ledger_rows_unchanged':len(ledger),
            'history_counts':{k:len(v) for k,v in histories.items()},'documents_files_unchanged':len(external),'historical_source_routes':33,
            'prior_semantic_readings_preserved':baseline['readings']['count'],'new_semantic_readings':after-baseline['readings']['count'],
            'human_reviews_created':0,'source_audits':audits,'clinical_validated':False}
    (OUT/'preservation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='source_audits'}))


if __name__=='__main__':main()
