"""Source-review program adapter; no clinical-state or benefit determination."""
from pathlib import Path
import hashlib,json
from ...contracts import digest
from . import functional_review as review

VERSION='functional-program-adapter/1'
PROGRAM='functional_support_review'

def handles(program):return program==PROGRAM
def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),review.identity()])
def definition(program):
    if not handles(program):raise ValueError('Unknown functional review program')
    return {'id':PROGRAM,'name':'Function and support source review','operator':'functional_support_review',
        'version':VERSION,'definition_identity':identity(),
        'purpose':'Reconcile dated falls, mobility and support accounts before considering services; no current need or eligibility established.'}
def catalog():return [definition(PROGRAM)]

def rows(path,spec,patients=None):
    if spec!=definition(spec['id']):raise ValueError('Functional program definition changed')
    population=review.prepare(path)
    meta=json.loads((Path(path)/'run.json').read_text())
    selected=None if patients is None else set(patients)
    result={}
    for row in population['patients']:
        pid=row['patient_id']
        if selected is not None and pid not in selected:continue
        # Missing selected sources remain an unavailable assessment, never a negative need.
        if not row['record_ids']:continue
        value={'synthetic':True,'patient_id':pid,'source_run':meta['source_run'],
            'operator':PROGRAM,'adapter_identity':identity(),'functional_source_review':row,
            'guidance':population['guidance'],
            'workflow':{'as_of':population['snapshot']['as_of'],
                'review_candidate':row['source_review_candidate'],'status':row['status'],
                'missing_information':row['unknowns']+['Current needs, preferences, service indication and member benefits require separate review.'],
                'clinical_eligibility_established':False,'authority':'dated_source_review_only'}}
        result[pid]=json.dumps(value,sort_keys=True,allow_nan=False)
    return result

FILTERS=('source_review_candidates','source_available','source_absent')
def prepare_work(path,request):
    from . import program_work
    if not isinstance(request,dict) or set(request)!={'review_id','patient_ids'}:
        raise ValueError('Exact functional review and patient selection required')
    patients=request['patient_ids']
    if not isinstance(patients,list) or not patients or len(patients)>2000 or any(not isinstance(p,str) for p in patients) or len(set(patients))!=len(patients):
        raise ValueError('Distinct bounded patient selection required')
    population=review.inspect(path,request['review_id'])
    if not set(patients)<={r['patient_id'] for r in population['patients']}:raise ValueError('Unknown functional patient')
    result=program_work.prepare(path,PROGRAM,patients,input_membership={
        'functional_review_id':population['id'],'selected_patients':sorted(patients),
        'selection_authority':'explicit_source_review_selection_not_clinical_eligibility'})
    review.inspect(path,population['id'])
    return {**result,'functional_review_id':population['id']}

def validate(parameters):
    if not isinstance(parameters,dict) or set(parameters)!={'filter'} or parameters['filter'] not in FILTERS:
        raise ValueError('Exact functional source-review filter required')
    return dict(parameters)

def membership(population,patients,parameters):
    p=validate(parameters);selected=set(patients);index={r['patient_id']:r for r in population['patients']}
    if not selected<=set(index):raise ValueError('Functional population does not include selected patients')
    yes=set();unknown=set()
    for pid in selected:
        row=index[pid]
        if p['filter']=='source_review_candidates':
            if row['source_review_candidate']:yes.add(pid)
            else:unknown.add(pid) # Failure to nominate never establishes absence of need.
        elif bool(row['record_ids'])==(p['filter']=='source_available'):yes.add(pid)
    return yes,unknown,{'functional_review_id':population['id'],'filter':p['filter'],
        'patients_evaluated':len(selected),'population_counts':population['counts'],
        'authority':'source_review_selection_only','clinical_state_established':False,
        'meaning':'Source availability is an observed coverage fact. No nomination is unresolved clinical need, not a negative clinical finding.',
        'new_paid_api_calls':0,'external_actions_executed':0}
