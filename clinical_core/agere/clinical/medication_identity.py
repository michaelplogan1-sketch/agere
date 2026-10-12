"""Offline medication-name identity and formulation review over received sources.

Named drug concepts are not taking/indication/tolerance assertions. This layer
does not promote a fill, list entry or negated narrative mention into actual use.
"""
from pathlib import Path
from collections import Counter,defaultdict
import hashlib,json,re,sqlite3,time
from ...contracts import digest
from ..narrative_sources import text_fields
from .clinical_state import normalize_dose

VERSION='medication-identity-review/1'
HF_SOURCE='https://www.heart.org/-/media/832EA0F4E73948848612F228F7FA2D35.pdf'
PROTOCOL={
 'id':'hf_medication_name_context','version':'2022-guideline-documentary-screen/1',
 'source':HF_SOURCE,'source_type':'guideline_slide_set','pages':[71,72,74],
 'short_support':['bisoprolol, carvedilol, sustained-release metoprolol succinate','when the use of ARNi is not feasible'],
 'purpose':'Compare documentary drug identity/formulation with the named beta-blocker options; preserve ACEi/ARB/ARNi as separate recorded RAAS-family evidence.',
 'review_status':'engineering_source_checked; not a complete reviewed clinical protocol',
 'clinical_eligibility_authority':False,
 'required_context':['Confirmed clinical phenotype and observation date','Current medication use and formulation',
                     'Indication, contraindications, feasibility, tolerance and patient preferences'],
}
LIMITS=[
 'This is retrospective documentary identity matching, not current treatment, adherence, eligibility or a dose recommendation.',
 'An exact RxNorm concept can identify an ingredient without resolving route, release formulation, product strength or actual regimen.',
 'Narrative matches include historical, negated, copied and other-person mentions. They do not assert exposure.',
 'Dose notation is distinct from product strength, dose equivalence, target dose and maximum tolerated dose.',
 'RAAS categories come from explicit source drug_class fields; this subset does not independently validate every source classification.',
 'The pinned terminology subset is incomplete. Unmatched names remain visible; absence of a named term is not absent care.',
]


def attached(path):
    link=json.loads((Path(path)/'medication-terminology-link.json').read_text());root=Path(link['path'])
    catalog=json.loads((root/'catalog.json').read_text())
    if catalog.get('schema')!='rxnorm-exact-subset/1' or digest(catalog)!=link['catalog_hash']:raise ValueError('Terminology identity mismatch')
    return catalog


def identity(path):
    from .. import narrative_sources
    from . import clinical_state
    return digest([VERSION,attached(path),PROTOCOL,*[hashlib.sha256(Path(p).read_bytes()).hexdigest()
                   for p in (__file__,narrative_sources.__file__,clinical_state.__file__)]])


def term_index(catalog):
    result={}
    for row in catalog['entries']:
        key=' '.join(row['query'].casefold().split())
        if key in result:raise ValueError('Duplicate terminology query')
        result[key]=row
    return result


def concept(name,index):
    if not isinstance(name,str):return {'status':'unmatched','raw':name,'concepts':[]}
    key=' '.join(name.casefold().split());row=index.get(key)
    if row is None:return {'status':'unmatched','raw':name,'concepts':[]}
    return {'status':row['status'],'raw':name,'query':row['query'],'concepts':row['concepts'],'lookup':row['lookup']}


def formulation(name,record):
    """Only literal formulation evidence; do not infer release from salt or daily dosing."""
    values=[str(name or ''),str(record.get('formulation') or ''),str(record.get('release_type') or '')]
    text=' '.join(values).casefold()
    er=bool(re.search(r'\b(?:extended[ -]release|sustained[ -]release|er|xl|cr)\b',text))
    ir=bool(re.search(r'\b(?:immediate[ -]release|ir)\b',text))
    if er and ir:return 'conflicting_release_notation'
    return 'extended_release_documented' if er else 'immediate_release_documented' if ir else 'release_not_documented'


def beta_name_screen(name,release):
    n=str(name).casefold()
    if n in ('bisoprolol','carvedilol'):return 'named_option_match'
    if n=='metoprolol succinate':
        return 'named_option_match' if release=='extended_release_documented' else 'formulation_unresolved' if release=='release_not_documented' else 'formulation_requires_review'
    if n in ('metoprolol','metoprolol tartrate'):return 'salt_or_formulation_requires_review'
    return 'outside_named_beta_blocker_screen'


def inspect_record(r,index,pattern):
    results=[];source={'record_id':r['record_id'],'patient_id':r['patient_id'],'source_table':r['source_table'],
      'record_hash':digest(r),'source_date':r.get('event_date'),'arrival_date':r.get('available_date')}
    fields=['drug']
    if r.get('_warehouse_source',{}).get('adapter')=='original-cohort-warehouse-adapter/1':
        fields+=['medication_name','medication_display']
        if r['source_table']=='medication_lists':fields.append('name')
    for field in fields:
        if not isinstance(r.get(field),str):continue
        c=concept(r[field],index);release=formulation(r[field],r)
        results.append({'kind':'structured_name','identity':c,'source':{**source,'field':field,'value':r[field]},
          'recorded_class':r.get('drug_class'),'recorded_status':r.get('status'),
          'release':release,'release_source_fields':{k:r[k] for k in ('formulation','release_type') if k in r},
          'dose_notation':normalize_dose(r.get('dose')),'dose_source':{**source,'field':'dose','value':r.get('dose')},
          'beta_blocker_name_screen':beta_name_screen(r[field],release),'actual_use_established':False})
    for part in text_fields(r):
        for m in pattern.finditer(part['text']):
            matched=m.group();results.append({'kind':'narrative_name_only','identity':concept(matched,index),
             'source':{**source,'field':part['field'],'quote':matched,'start':m.start(),'end':m.end(),
                       'context':part['text'][max(0,m.start()-100):m.end()+160]},
             'assertion':'not_interpreted_by_name_match','actual_use_established':False})
    return results


def execute(path,patient_ids,limit=10):
    if type(limit) is not int or not 1<=limit<=25:raise ValueError('Bounded patient preview required')
    path=Path(path);catalog=attached(path);index=term_index(catalog)
    pattern=re.compile(r'(?<!\w)(?:'+'|'.join(re.escape(x) for x in sorted(index,key=lambda x:(-len(x),x)))+r')(?!\w)',re.I)
    meta=json.loads((path/'run.json').read_text())
    if meta.get('synthetic') is not True:raise ValueError('Synthetic warehouse required')
    db=sqlite3.connect((path/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    counts=Counter();people=defaultdict(set);names=Counter();statuses=Counter();previews=[];ids=set(patient_ids)
    try:
        universe={r[0] for r in db.execute('SELECT id FROM patients')}
        if not ids<=universe:raise ValueError('Unknown patient in medication scope')
        for pid in sorted(ids):
            collected=[];total=0;flags=set();raw_records=0
            for (raw,) in db.execute('SELECT payload FROM records WHERE patient=? ORDER BY id',(pid,)):
                r=json.loads(raw);raw_records+=1
                if r['patient_id']!=pid or r.get('synthetic') is not True or r['available_date']>meta['snapshot']['as_of']:raise ValueError('Invalid patient/source snapshot')
                hits=inspect_record(r,index,pattern)
                for h in hits:
                    counts[h['kind']]+=1;statuses[h['identity']['status']]+=1;total+=1
                    names[(h['identity'].get('query') or str(h['identity']['raw']),h['kind'])]+=1
                    people['any_named_medication'].add(pid)
                    if h['kind']=='structured_name':
                        flags.add(h['beta_blocker_name_screen'])
                        if h['recorded_class'] in ('ace','acei','arb','arni'):flags.add('recorded_raas_'+h['recorded_class'])
                        if h['identity']['status']!='resolved':flags.add('unresolved_drug_identity')
                    else:flags.add('narrative_drug_name')
                    if len(collected)<24:collected.append(h)
            for flag in flags:people[flag].add(pid)
            counts['source_records_inspected']+=raw_records
            if total and len(previews)<limit:
                previews.append({'patient_id':pid,'mentions':collected,'mentions_total':total,
                                 'preview_truncated':total>len(collected),'documentary_flags':sorted(flags)})
        return {'schema':VERSION,'synthetic':True,'patients_evaluated':len(ids),'record_counts':dict(counts),
                'patient_counts':{k:len(v) for k,v in sorted(people.items())},'identity_status_counts':dict(statuses),
                'names':[{'name':n,'kind':k,'occurrences':v} for (n,k),v in sorted(names.items())],
                'patients':previews,'patients_returned':len(previews),'protocol_context':PROTOCOL,
                'terminology_version':catalog['version'],'terminology_retrieved_at':catalog['retrieved_at'],
                'terminology_time_basis':'Current pinned terminology applied retrospectively; not historical knowledge availability.',
                'source_identity':{'source_run':meta['source_run'],'source_digest':meta['source_digest'],'snapshot':meta['snapshot'],'terminology_code_identity':identity(path)},
                'limits':LIMITS,'new_paid_api_calls':0,'clinical_eligibility_established':False}
    finally:db.close()
