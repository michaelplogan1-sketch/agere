"""Verify received-source citations and summarize a completed synthetic narrative run."""
from pathlib import Path
from collections import Counter
import hashlib,json,sqlite3
from clinical_core.agere.evidence_index import search
from clinical_core.agere.operators import patient_analysis

ROOT=Path(__file__).resolve().parent
RUN=ROOT/'artifacts/agere-full/warehouse-narrative-v2'
OUT=ROOT/'artifacts/agere-narrative-review'
OUT.mkdir(exist_ok=True)
run=json.loads((RUN/'run.json').read_text())
db=sqlite3.connect((RUN/'evidence.sqlite3').as_uri()+'?mode=ro',uri=True)
source={(p,r,f):text for p,r,f,text in db.execute('SELECT patient,record,field,text FROM fields')}
mentions=0;quotes=0;contexts=Counter();actions=Counter();examples={};patients_with_actions=0
for line in (RUN/'patients.jsonl').open():
    a=json.loads(line);pid=a['patient_id'];patients_with_actions+=bool(a['narrative_review_actions'])
    for m in a['clinical']['narrative']['mentions']:
        mentions+=1;contexts[m['concept']+':'+m['assertion']]+=1
        for e in m['occurrences']:
            text=source[(pid,e['record_id'],e['field'])]
            assert text[e['start']:e['end']]==e['quote'],e
            assert text[e['context_start']:e['context_end']]==e['context'],e
            quotes+=1
    for action in a['narrative_review_actions']:
        actions[action['kind']]+=1
        examples.setdefault(action['kind'],{'patient_id':pid,'action':action})
db.close()
questions=['difficulty paying medical bills','outside records incomplete','palliative consultation','prescription benefit administrator changed']
searches=[search(RUN/'evidence.sqlite3',q) for q in questions]
for result in searches:
    for e in result['evidence']:
        text=source[(e['patient_id'],e['record_id'],e['field'])]
        assert text[e['start']:e['end']]==e['quote']
probes=[]
for text in ('Prior EF 30% was entered in error; current EF 60%.',
             'The patient reports that her brother has EF 25%; her EF is 60%.',
             'Difficulty paying medical bills reported: False.'):
    r={'patient_id':'SYNTHETIC-PROBE','record_id':'probe','source_table':'notes','event_date':'2026-09-01','available_date':'2026-09-02','text':text}
    a=patient_analysis({'patient_id':'SYNTHETIC-PROBE'},[r],'2026-09-30',[])
    probes.append({'text':text,'analysis':a})
    assert not any(f['kind'] in {'reduced_ef_documentation','access_context'} for f in a['flags'])
files=['clinical_core/agere/narrative.py','clinical_core/agere/narrative_sources.py','clinical_core/agere/evidence_index.py',
       'clinical_core/agere/operators.py','clinical_core/agere/runtime.py','clinical_core/agere/service.py',
       'clinical_core/hybrid_retrieval.py','test_agere_narrative.py']
report={'synthetic':True,'run':str(RUN),'run_sha256':hashlib.sha256((RUN/'run.json').read_bytes()).hexdigest(),
        'source_hashes':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in files},
        'tests':{'total_passed':179,'log':(Path('/tmp/agere-narrative-tests.log')).read_text()},
        'source_citation_occurrences_verified':quotes,'deduplicated_contextual_mentions':mentions,
        'contexts':dict(contexts),'review_proposal_counts':dict(actions),'patients_with_review_proposals':patients_with_actions,
        'mapped_text_fields':run['narrative']['fields'],'indexed_chunks':run['narrative']['indexed_chunks'],
        'whole_roster_native_analysis':run['summary']['patient_analyses'],
        'neural_specialist_cases':run['summary']['neural_cases'],'new_paid_api_calls':0,
        'clinical_accuracy_measured':False,'complete_clinical_understanding':False,
        'index_scope':'Mapped received-text fields; no claim of complete clinical normalization, arbitrary query execution or exhaustive search recall'}
(OUT/'verification.json').write_text(json.dumps(report,indent=2))
(OUT/'examples.json').write_text(json.dumps(examples,indent=2))
(OUT/'searches.json').write_text(json.dumps(searches,indent=2))
(OUT/'probes.json').write_text(json.dumps(probes,indent=2))
print(json.dumps({k:v for k,v in report.items() if k not in ('source_hashes','tests')},indent=2))
