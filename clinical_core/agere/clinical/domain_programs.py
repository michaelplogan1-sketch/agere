"""Domain accounts nominate source review, not disease or clinical eligibility."""
import hashlib,json
from pathlib import Path
from ...contracts import digest
from . import domain_review as review

VERSION='domain-program-adapter/1'
PROGRAMS={'respiratory_source_review':'respiratory','behavioral_source_review':'behavioral'}
FILTERS=('source_review_candidates','source_available','source_absent')

def definition(program):
    if not isinstance(program,str) or program not in PROGRAMS:raise ValueError('Unknown domain source-review program')
    domain=PROGRAMS[program];spec=review.definition(domain)
    return {'id':program,'name':spec['name'],'operator':'domain_source_review','domain':domain,'version':VERSION,
        'definition_identity':digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),review.identity(),program,domain]),
        'purpose':'Review received '+domain+' accounts and unresolved evidence before service matching; no diagnosis, current need or benefits established.'}

def rows(path,spec,patients=None):
    if spec!=definition(spec['id']):raise ValueError('Domain program definition changed')
    population=review.prepare(path,spec['domain']);meta=json.loads((Path(path)/'run.json').read_text())
    selected=None if patients is None else set(patients);result={}
    for row in population['patients']:
        pid=row['patient_id']
        if (selected is not None and pid not in selected) or not row['record_ids']:continue
        value={'synthetic':True,'patient_id':pid,'source_run':meta['source_run'],'operator':spec['id'],
            'adapter_identity':spec['definition_identity'],'domain_source_review':row,'guidance':population['guidance'],
            'workflow':{'as_of':population['snapshot']['as_of'],'review_candidate':row['source_review_candidate'],
                'status':row['status'],'missing_information':row['unknowns'],
                'clinical_eligibility_established':False,'authority':'received_source_review_only'}}
        result[pid]=json.dumps(value,sort_keys=True,allow_nan=False)
    return result

def validate(parameters):
    if not isinstance(parameters,dict) or set(parameters)!={'domain','filter'} or parameters['filter'] not in FILTERS:
        raise ValueError('Exact domain and source-review filter required')
    review.definition(parameters['domain']);return dict(parameters)

def membership(population,patients,parameters):
    p=validate(parameters)
    if population['domain']!=p['domain']:raise ValueError('Domain population mismatch')
    selected=set(patients);index={r['patient_id']:r for r in population['patients']}
    if not selected<=set(index):raise ValueError('Patient missing from domain population')
    yes=set();unknown=set()
    for pid in selected:
        row=index[pid]
        if p['filter']=='source_review_candidates':
            if row['source_review_candidate']:yes.add(pid)
            else:unknown.add(pid)
        elif bool(row['record_ids'])==(p['filter']=='source_available'):yes.add(pid)
    return yes,unknown,{'domain_review_id':population['id'],'domain':p['domain'],'filter':p['filter'],
        'population_counts':population['counts'],'patients_evaluated':len(selected),
        'authority':'source_review_selection_only','clinical_state_established':False,
        'meaning':'Matching accounts nominate evidence review. Other patients retain unknown need; observed source absence is not clinical absence.',
        'new_paid_api_calls':0,'external_actions_executed':0}

def prepare_work(path,request):
    from . import program_work
    if not isinstance(request,dict) or set(request)!={'review_id','patient_ids'}:raise ValueError('Exact domain receipt and patient selection required')
    ids=request['patient_ids']
    if not isinstance(ids,list) or not 1<=len(ids)<=2000 or any(not isinstance(p,str) for p in ids) or len(set(ids))!=len(ids):
        raise ValueError('Distinct bounded patient selection required')
    population=review.inspect(path,request['review_id'])
    if not set(ids)<={r['patient_id'] for r in population['patients']}:raise ValueError('Unknown domain patient')
    program=next(k for k,v in PROGRAMS.items() if v==population['domain'])
    result=program_work.prepare(path,program,ids,input_membership={'domain_review_id':population['id'],
        'selected_patients':sorted(ids),'selection_authority':'explicit_source_review_selection_not_clinical_eligibility'})
    review.inspect(path,population['id'])
    return {**result,'domain_review_id':population['id'],'domain':population['domain']}
