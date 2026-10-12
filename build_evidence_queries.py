from pathlib import Path
import json
from clinical_core.agere.clinical.evidence_queries import build_index,execute,CATALOG
from clinical_core.agere.clinical.relation_learning import write
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'artifacts/agere-evidence-queries-v1'
if __name__=='__main__':
    if not (OUT/'run.json').exists():print(json.dumps(build_index(ROOT/'artifacts/agere-clinical-workflows-v2',ROOT/'artifacts/agere-evidence-reader-v4/warehouse',OUT)),flush=True)
    results=[]
    for item in CATALOG:
        cold=execute(OUT,item['plan'],limit=10);warm=execute(OUT,item['plan'],limit=10)
        assert warm['cache_hit'] and cold['patients']==warm['patients']
        results.append({**item,'result':cold,'warm_elapsed_seconds':warm['elapsed_seconds']})
        print(json.dumps({'query':item['id'],'matches':cold['patients_matching_evidence_plan'],'cold_seconds':cold['elapsed_seconds'],'warm_seconds':warm['elapsed_seconds']}),flush=True)
    write(OUT/'catalog-results.json',results)
