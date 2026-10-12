"""Query explicit clinical workflow results without disguising matching as reasoning."""
import json,re
from pathlib import Path
from .workflows import NAMES

ALIASES={
 'heart_failure':{'heart failure treatment evidence review','review heart failure treatment evidence','heart failure'},
 'kidney':{'kidney trajectory and referral evidence review','review kidney trajectory and referrals','kidney'},
 'affordability':{'affordability evidence and assistance review','review affordability and assistance evidence','affordability'},
}
PATTERNS={'heart_failure':r'\b(?:heart failure|hfref|gdmt|cardiac|ejection fraction)\b',
          'kidney':r'\b(?:kidney|ckd|renal|creatinine|egfr|nephrolog\w*)\b',
          'affordability':r'\b(?:afford\w*|financial|paying|assistance|cost of (?:care|medication))\b'}


def query(run,question):
    if not isinstance(question,str) or not question.strip() or len(question)>2000:raise ValueError('Bounded question required')
    root=Path(run);q=re.sub(r'\s+',' ',question.lower()).strip(' .?!')
    exact=next((key for key,aliases in ALIASES.items() if q in aliases),None)
    keys=[exact] if exact else [key for key,pattern in PATTERNS.items() if re.search(pattern,q)]
    results=[json.loads((root/(key+'.json')).read_text()) for key in keys]
    return {'question':question,'synthetic':True,'status':'defined_workflow_results' if exact else 'related_workflows_only' if keys else 'unsupported_question',
            'question_fully_answered':bool(exact),'workflows':results,
            'supported_workflows':NAMES,'execution_authorized':False,
            'scope':'Exact workflow invocation runs its declared review definition; other questions receive related workflows only. Arbitrary filters, causality, eligibility and treatment appropriateness are not inferred.',
            'patient_detail_endpoint':'/api/patient?id=<patient_id>',
            'evidence_contract':'Patient results contain fact IDs, quoted sources, exclusions, missing information and proposed actions'}
