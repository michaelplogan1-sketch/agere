"""Diverse source-bound learning queue using standard information measures.

Jensen-Shannon divergence is disagreement between uncalibrated model distributions,
not a probability of clinical error. A separate audit slice samples agreement too.
No queue item becomes a training label or clinical action automatically.
"""
from collections import defaultdict,Counter
from pathlib import Path
import math,json,sqlite3
from ...contracts import digest
from .evidence_reader import AXES
from .relation_learning import normalize,write

VERSION='annotation-active-review/1'


def entropy(values):
    if not values or any(not math.isfinite(v) or v<0 for v in values) or not math.isclose(sum(values),1.,abs_tol=1e-6):
        raise ValueError('Normalized finite probabilities required')
    return -sum(v*math.log2(v) for v in values if v)


def js_divergence(p,q):
    keys=sorted(set(p)|set(q));a=[p.get(k,0.) for k in keys];b=[q.get(k,0.) for k in keys]
    # Validate endpoints before mixing, then tolerate tiny floating residuals.
    ha,hb=entropy(a),entropy(b);value=entropy([(x+y)/2 for x,y in zip(a,b)])-(ha+hb)/2
    return max(0.,min(1.,value))


def source_group(row):
    return digest([row['input']['concept'],normalize(row['source']['context'])])


def rank(row):
    annotation=row['annotation'];p=annotation['axes'];q=annotation['alternate_axes']
    js={a:js_divergence(p[a]['probabilities'],q[a]['probabilities']) for a in AXES}
    uncertainty={a:entropy(list(p[a]['probabilities'].values()))/math.log2(len(p[a]['probabilities'])) if len(p[a]['probabilities'])>1 else 0. for a in AXES}
    priority=annotation['review_routing']['priority'];tier={'context_conflict':2,'model_disagreement':1,'routine_annotation_review':0}[priority]
    return {'priority_tier':tier,'max_js_bits':max(js.values()),'js_by_axis':js,'mean_normalized_entropy':sum(uncertainty.values())/len(AXES)}


def select(rows,*,size=96,audit_fraction=.2,exclude_groups=()):
    if type(size) is not int or not 1<=size<=500 or not math.isfinite(audit_fraction) or not 0<=audit_fraction<=1:raise ValueError('Bounded queue configuration required')
    excluded=set(exclude_groups);groups={};seen=0;skipped=0
    for row in rows:
        seen+=1
        if row.get('synthetic') is not True or row['annotation'].get('decision_authority') is not False:raise ValueError('Synthetic untrusted annotations required')
        group=source_group(row)
        if group in excluded:skipped+=1;continue
        stable=digest([group,row['patient_id'],row['source']])
        # Pick the representative before ranking; repeated documentation cannot
        # inflate divergence or dominate the queue through frequency.
        if group not in groups or stable<groups[group][0]:groups[group]=(stable,row)
    candidates=[]
    for group,(stable,row) in groups.items():candidates.append({'group':group,'stable':stable,'row':row,'rank':rank(row)})
    audit_slots=math.ceil(size*audit_fraction);selected=[];used=set();patient_counts=Counter()
    def add(item,reason):
        if item['group'] in used or patient_counts[item['row']['patient_id']]>=2:return False
        used.add(item['group']);patient_counts[item['row']['patient_id']]+=1
        selected.append({**item['row'],'review_selection':{'group':item['group'],'reason':reason,**item['rank']},'human_reviewed':False,'training_label':None})
        return True
    # Reserve an audit slice independent of model scores; deterministic hashing
    # makes sampling reproducible and includes possible shared-model failures.
    for item in sorted(candidates,key=lambda v:digest(['agreement-audit/1',v['group']])):
        if len(selected)>=audit_slots:break
        add(item,'score_independent_audit')
    strata=defaultdict(list)
    for item in sorted(candidates,key=lambda v:(-v['rank']['priority_tier'],-v['rank']['max_js_bits'],-v['rank']['mean_normalized_entropy'],v['stable'])):
        if item['group'] not in used:strata[item['row']['input']['concept']].append(item)
    while len(selected)<size and any(strata.values()):
        for concept in sorted(strata):
            while strata[concept] and len(selected)<size:
                if add(strata[concept].pop(0),'diverse_context_disagreement'):break
    return {'schema':VERSION,'synthetic':True,'requested':size,'selected':len(selected),'annotations_scanned':seen,'bindings_excluded_as_previously_labeled':skipped,'unique_eligible_groups':len(groups),
        'selection_counts':dict(Counter(r['review_selection']['reason'] for r in selected)),
        'concept_counts':dict(Counter(r['input']['concept'] for r in selected)),
        'method':'Numeric/beta-blocker-normalized clause+concept deduplication, stable representative; score-independent audit slice, then concept round-robin ranking by conflict tier and Jensen-Shannon divergence. Maximum two excerpts per patient.',
        'limitations':['Divergence and entropy are uncalibrated learning heuristics, not clinical severity or correctness','Semantic duplicates can survive heuristic normalization','Audit is sampled across unique groups, not population prevalence','No labels or clinical actions created'],
        'new_paid_api_calls':0,'decision_authority':False,'rows':selected}


def build(model_path,excluded_cases):
    root=Path(model_path);run=json.loads((root/'warehouse/run.json').read_text())
    if run['reader_schema']!='context-evidence-reader/4':raise ValueError('Context reader warehouse required')
    excluded={source_group(r) for r in excluded_cases};db=sqlite3.connect((root/'warehouse/annotations.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    try:
        result=select((json.loads(p) for (p,) in db.execute('SELECT payload FROM annotations')),exclude_groups=excluded)
    finally:db.close()
    result['model_identity']=run['model_identity'];result['source_run']=run['source_run']
    write(root/'review-batch.json',result)
    write(root/'review-batch-summary.json',{k:v for k,v in result.items() if k!='rows'})
    return result
