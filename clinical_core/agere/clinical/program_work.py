"""Version dispatch for persistent work; historical engine retained byte for byte.

The default code_identity is the historical engine identity for existing callers.
New functional proposals bind the current engine and its source adapter explicitly.
No historical proposal, assessment, or event is rewritten by this dispatch layer.
"""
from .program_work_v1 import *
from . import program_work_v1 as legacy, program_work_current as current, functional_programs


def engine(program):
    from .work_adapters import resolve
    return resolve(program) or (current if functional_programs.handles(program) else legacy)

def definition(program):return engine(program).definition(program)
def registered_programs():
    from .work_adapters import catalog
    return legacy.registered_programs()+functional_programs.catalog()+catalog()
def analysis_rows(path,src,spec,patients=None):return engine(spec['id']).analysis_rows(path,src,spec,patients)
def preparation_identity(path,programs):
    from . import work_context_preservation as preservation
    base=[[p,engine(p).preparation_identity(path,[p])] for p in sorted(set(programs))]
    return digest([base,preservation.dependency(path,programs)])
def prepare(path,program,patient_ids,**kwargs):
    from . import work_context_preservation as preservation
    patient_ids=list(patient_ids)
    return engine(program).prepare(path,program,patient_ids,**preservation.inherit(path,program,patient_ids,kwargs))

def stored_engine(path,table,key):
    db=database(path)
    try:
        row=load(db,table,key)
        return engine(row['program']['id'] if table=='work_proposals' else row['program_id'])
    finally:db.close()

def materialize(path,proposal_id,actor):
    from . import answer_retirement as retirement
    return retirement.materialize(path,stored_engine(path,'work_proposals',proposal_id),proposal_id,actor)
def change(path,request):
    if not isinstance(request,dict) or not isinstance(request.get('item_id'),str):raise ValueError('Explicit work-event request required')
    return stored_engine(path,'work_items',request['item_id']).change(path,request)
def list_items(path,**kwargs):
    from .program_work_views import list_items as view
    return view(path,**kwargs)
