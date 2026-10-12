from pathlib import Path
import json
from clinical_core.agere.clinical.active_review import build
ROOT=Path(__file__).resolve().parent
if __name__=='__main__':
    prior=[]
    for path in ['agere-evidence-reader-v2/warehouse-transfer-cases.json','agere-evidence-reader-v3/fresh-cases.json','agere-evidence-reader-v4/fresh-cases.json']:
        prior.extend(json.loads((ROOT/'artifacts'/path).read_text()))
    result=build(ROOT/'artifacts/agere-evidence-reader-v4',prior)
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2))
