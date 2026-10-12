"""Versioned additive reference/option bundles without rewriting saved identities.

The selected manifest binds the bundle, including locally revised descendants.
Context-local dispatch keeps parallel comparisons isolated. Legacy respiratory
comparisons retain their original implementation and evidence identities.
"""
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
import copy,hashlib,json,math
from ...contracts import digest
from . import reference_applicability as R,reference_lifecycle as L,care_option_comparison as C

VERSION='option-evidence-registry/1'
CATALOG=Path(__file__).with_name('option_evidence_registry.json')
_EVIDENCE=ContextVar('selected_option_evidence',default=None)

def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])

def bundles():
    data=json.loads(CATALOG.read_text())
    if data['schema']!=VERSION:raise ValueError('Unknown option registry schema')
    out=[];keys=set();dois=set()
    for raw in data['bundles']:
        key=raw['key'];ref=raw['reference'];e=raw['evidence']
        if key in keys or ref['citation']['doi'] in dois:raise ValueError('Distinct registry keys and reference DOIs required')
        keys.add(key);dois.add(ref['citation']['doi'])
        if ref['kind']!='guideline_recommendation' or any(x['review_status']!='machine_curated_unreviewed' for x in (ref,e)):
            raise ValueError('Typed unreviewed reference and evidence required')
        if e['schema']!='care-option-evidence/1' or e['reference_doi']!=ref['citation']['doi']:raise ValueError('Exact reference/evidence DOI required')
        qkeys=[q['key'] for q in ref['questions']]
        if len(qkeys)!=len(set(qkeys)) or not {'population','assessment','preference','access','coverage','currency'}<=set(qkeys):
            raise ValueError('Distinct questions including clinical, access and currency prerequisites required')
        alts=e['alternatives'];akeys=[a['key'] for a in alts]
        if len(alts)<2 or len(akeys)!=len(set(akeys)):raise ValueError('At least two distinct alternatives required')
        for a in alts:
            if not a['compatible_modalities'] or not set(a['compatible_modalities'])<={'virtual','hybrid','in_person'}:raise ValueError('Known proposed delivery modalities required')
            if not a['delivery_questions']:raise ValueError('Alternative delivery questions required')
        for x in e['estimates']:
            if len(x['interval'])!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in [x['point'],*x['interval'],x['null_value']]):raise ValueError('Finite evidence estimate and interval required')
            if not x['interval'][0]<=x['point']<=x['interval'][1] or not 0<x['interval_level']<1:raise ValueError('Ordered evidence interval required')
        if not e['estimates'] and not e.get('quantitative_evidence_status'):raise ValueError('Explain missing comparative estimates')
        marker={'key':key,'version':raw['version'],'bundle_hash':digest(raw),'implementation':identity()}
        ref={**ref,'registry_entry':marker};ref['id']=digest(ref)
        out.append({'reference':ref,'evidence':{**e,'registry_entry':marker}})
    return out

def selected(path,request):
    # A DOI alone is not authority to borrow another bundle's alternatives.
    if not isinstance(request,dict) or 'manifest_id' not in request:return None
    ref=R.load(path,request['manifest_id'])['reference'];marker=ref.get('registry_entry')
    if marker is None:return None
    entry=next((b for b in bundles() if b['reference']['registry_entry']==marker),None)
    if entry is None or entry['reference']['citation']!=ref['citation']:raise ValueError('Exact option evidence bundle changed or is unavailable')
    return entry['evidence']

def install():
    if getattr(C.snapshot,'option_registry_adapter',False):return
    L.install()
    base=L._BASE_REFERENCES;original_evidence=C.evidence
    def references():
        rows=base();extra=[b['reference'] for b in bundles()]
        if {r['id'] for r in rows}&{r['id'] for r in extra}:raise ValueError('Duplicate registered reference')
        return rows+extra
    def evidence():
        e=_EVIDENCE.get();return copy.deepcopy(e) if e is not None else original_evidence()
    def scoped(fn):
        @wraps(fn)
        def call(path,request):
            token=_EVIDENCE.set(selected(path,request))
            try:return fn(path,request)
            finally:_EVIDENCE.reset(token)
        call.option_registry_adapter=True
        return call
    L._BASE_REFERENCES=references;C.evidence=evidence
    C.snapshot=scoped(C.snapshot);C.choices=scoped(C.choices)
