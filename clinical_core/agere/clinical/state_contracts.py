"""Versioned documentary requirements, not clinical eligibility protocols."""
from ...contracts import digest

VERSION='clinical-evidence-contracts/1'
DRUGS=('bb','arni','mra','sglt2')

def requirement(binding,label,accept='known'):
    return {'binding':binding,'label':label,'accept':accept}

CONTRACTS={
 'hf_therapy':{'title':'Heart failure phenotype and therapy evidence',
   'requirements':[requirement('hf_documentation','Heart failure documentation'),
     requirement('ef_clinical','EF with an explicit observation date'),
     *[requirement('therapy.'+drug+'.'+axis,drug.upper()+': '+label) for drug in DRUGS
       for axis,label in [('use','current actual use'),('exception','current exception assessment'),('tolerance','maximum tolerated dose assessment')]],
     requirement('goals','Confirmed patient goals and treatment preferences')],
   'boundary':'Review prerequisites only. Does not diagnose HFrEF or determine GDMT indication, target dose, tolerance, contraindications or treatment.'},
 'renal_review':{'title':'Renal interpretation and referral evidence',
   'requirements':[requirement('renal.egfr','eGFR with a verified collection date'),
     requirement('renal.creatinine','Creatinine with a verified collection date'),
     requirement('renal.uacr','UACR with a verified collection date'),
     requirement('nephrology_attendance','Completed nephrology attendance evidence'),
     requirement('renal_context','Clinical cause, acute/chronic context and referral indications')],
   'boundary':'Review prerequisites only. Does not calculate kidney-failure risk, diagnose CKD/AKI or decide referral eligibility.'},
 'affordability':{'title':'Affordability and assistance evidence',
   'requirements':[requirement('financial_report','Explicit reported affordability assessment'),
     requirement('financial_current','Current affordability confirmation'),
     requirement('assistance_consent','Consent specific to the assistance program'),
     requirement('payer_eligibility','Effective program eligibility requirements')],
   'boundary':'Reported need is distinct from confirmed current need, consent or payer-specific eligibility.'},
}

def definition(key):
    if not isinstance(key,str) or key not in CONTRACTS:raise ValueError('Unknown clinical evidence contract')
    spec={'id':key,'version':VERSION,**CONTRACTS[key],
          'authority':'Internal synthetic development requirements; not a medical guideline or payer protocol',
          'clinical_eligibility_authority':False}
    return {**spec,'hash':digest(spec)}


def evaluate(spec,bindings):
    rows=[]
    for requirement in spec['requirements']:
        state=bindings.get(requirement['binding'],{'status':'unsupported','value':None,'reason':'binding_not_implemented','evidence':[]})
        known=state['status']=='documented'
        match=(known and (requirement['accept']=='known' or state['value'] is True))
        result='satisfied' if match else 'not_satisfied' if known else 'unresolved'
        rows.append({**requirement,'result':result,'state':state})
    return {'contract':spec,'status':'requirements_documented' if all(r['result']=='satisfied' for r in rows) else
             'documented_requirement_not_satisfied' if any(r['result']=='not_satisfied' for r in rows) else 'requirements_unresolved',
            'requirements':rows,'unresolved_bindings':[r['binding'] for r in rows if r['result']=='unresolved'],
            'clinical_eligibility_established':False,'clinical_execution_authorized':False}
