"""Explicit local adapter registry; no user-controlled imports or code execution."""
from . import appointment_followthrough,cardiometabolic_review,domain_programs,program_work_v1 as legacy,functional_programs
from .registered_program_work import AdapterEngine

REGISTRATIONS=(('respiratory_source_review',domain_programs),('behavioral_source_review',domain_programs),('cardiometabolic_source_review',cardiometabolic_review),('appointment_followthrough_source_review',appointment_followthrough))

def entries():
    reserved={r['id'] for r in legacy.registered_programs()}|{r['id'] for r in functional_programs.catalog()}
    out={}
    for program,adapter in REGISTRATIONS:
        if not isinstance(program,str) or program in out or program in reserved:
            raise ValueError('Distinct adapter program IDs required; existing programs cannot be shadowed')
        spec=adapter.definition(program)
        if spec.get('id')!=program or not spec.get('definition_identity'):raise ValueError('Adapter must bind its selected program definition')
        out[program]=adapter
    return out

def resolve(program):
    if not isinstance(program,str):raise ValueError('Explicit program ID required')
    adapter=entries().get(program)
    return AdapterEngine(adapter) if adapter is not None else None

def catalog():return [a.definition(p) for p,a in entries().items()]
