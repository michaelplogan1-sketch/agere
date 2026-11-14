"""Offline, task-outcome delegation evaluation over the existing journal.

No caller imports fitted thresholds to select a provider. A confidence is a score,
not a success label. Operational completion and model self-grades are not gold.
"""
import copy
import json
import math
import sqlite3
import uuid
import hashlib
from functools import lru_cache
from collections import Counter,defaultdict
from pathlib import Path
from ...contracts import digest
from ..request_lineage import execution_observation,ACTIVE

VERSION='decision-loop-evaluation/1'
TYPES={'route','operator_choice','clarify_or_not'}


@lru_cache(maxsize=1)
def code_identity():
    return {name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()for name in
        ('decision_evaluation.py','system_one_gate.py','system_one.py','tier_router.py','route_arbiter.py')}


def snapshot_identity(stack):
    try:
        meta=stack.retriever.meta
        return meta.get('snapshot')if isinstance(meta,dict)else None
    except Exception:
        return None  # Optional identity telemetry must not affect routing.


def observe(*,decision_type,model,confidence,proposal,incumbent,identity,inputs):
    """Passive logging of an already computed choice; never another model call."""
    if decision_type not in TYPES or type(confidence)not in (int,float)or not math.isfinite(confidence)or not 0<=confidence<=1:
        return
    try:
        execution_observation('decision_observed',schema=VERSION,decision_id=uuid.uuid4().hex,
            decision_type=decision_type,model=model,confidence=confidence,
            proposal_digest=digest(proposal),incumbent_digest=digest(incumbent),
            policy_identity=digest([identity,code_identity()]),input_digest=digest(inputs),
            outcome_label_available=False,threshold_activation=False)
    except Exception:
        pass  # Optional telemetry cannot change an existing routing decision.


def evaluate_pair(*,state,incumbent,proposal,run,judge,guard,decision_type,model,
                  confidence,family,partition,identity,evidence_kind,outcome_basis):
    """Force ONE studied decision, then execute each complete continuation.

    `run` must isolate all mutable state per arm, including native stores, and
    return success/cost inputs for a separately supplied locked evaluator.
    Both arms run even if one fails; every error/cost stays in the receipt.
    This API does not invoke providers itself or authorize new provider work.
    """
    if partition not in ('fit','validate')or evidence_kind not in ('observed','authored_fixture'):
        raise ValueError('Declare a development partition and evidence kind')
    if decision_type not in TYPES or not isinstance(family,str)or not family or not outcome_basis:
        raise ValueError('Decision type, source family and locked outcome basis required')
    if type(confidence)not in (int,float)or not math.isfinite(confidence)or not 0<=confidence<=1:
        raise ValueError('Finite decision confidence required')
    before=digest(guard());arms={}
    for name,choice in (('incumbent',incumbent),('proposal',proposal)):
        try:
            result=run(copy.deepcopy(state),copy.deepcopy(choice))
            assessment=judge(result)
            if type(assessment.get('success'))is not bool:raise ValueError('Independent complete-workflow success label required')
            cost=assessment.get('cost')
            if cost is not None and (type(cost)not in (int,float)or not math.isfinite(cost)or cost<0):
                raise ValueError('All-attempt cost must be nonnegative or explicitly unknown')
            arms[name]={'success':assessment['success'],'cost':cost,'result_digest':digest(result),
                        'receipt':assessment.get('receipt')}
        except Exception as exc:
            arms[name]={'success':False,'cost':None,'error':type(exc).__name__+': '+str(exc)}
        if digest(guard())!=before:raise ValueError('Source or execution policy changed during paired continuation')
    row={'schema':VERSION,'pair_id':uuid.uuid4().hex,'decision_type':decision_type,'model':model,
        'confidence':confidence,'family':family,'partition':partition,'policy_identity':digest([identity,code_identity()]),
        'state_digest':digest(state),'guard_digest':before,'evidence_kind':evidence_kind,'evaluator_code':code_identity(),
        'outcome_basis':outcome_basis,'arms':arms,'study':'single_decision_complete_continuation',
        'threshold_activation':False}
    return row


def record_pair(row):
    """A named Execution owns each receipt, in the existing append-only journal."""
    if ACTIVE.get()is None:raise ValueError('An execution-journal context is required')
    ACTIVE.get().append('decision_pair_evaluated',pair=row)


def read_journal(filename):
    filename=Path(filename).resolve()
    counts=Counter();pairs=[]
    with sqlite3.connect(filename.as_uri()+'?mode=ro',uri=True)as db:
        for payload, in db.execute('SELECT payload FROM question_request_events ORDER BY execution,sequence'):
            event=json.loads(payload);counts[event.get('kind')]+=1
            if event.get('kind')=='decision_pair_evaluated':pairs.append(event['pair'])
    return pairs,dict(counts)


def _validate(rows,*,include_fixtures):
    seen={};families={};out=[];excluded=Counter()
    for row in rows:
        if row.get('schema')!=VERSION:excluded['different_schema']+=1;continue
        if row.get('evidence_kind')!='observed'and not (include_fixtures and row.get('evidence_kind')=='authored_fixture'):
            excluded['not_observed']+=1;continue
        if row.get('study')!='single_decision_complete_continuation':excluded['not_in_loop']+=1;continue
        ident=row['pair_id'];key=digest(row)
        if ident in seen:
            if seen[ident]!=key:raise ValueError('Conflicting pair receipts')
            excluded['duplicate_pair']+=1;continue
        seen[ident]=key
        if row['partition']not in ('fit','validate')or row['decision_type']not in TYPES:raise ValueError('Invalid partition or decision type')
        # One family cannot teach the threshold and also assess its transfer.
        family=row['family']
        if not family:raise ValueError('Source family required')
        if family in families and families[family]!=row['partition']:raise ValueError('Fit/validation source-family overlap')
        families[family]=row['partition']
        p=row['confidence']
        if type(p)not in (int,float)or not math.isfinite(p)or not 0<=p<=1:raise ValueError('Invalid confidence')
        for arm in ('incumbent','proposal'):
            a=row['arms'][arm]
            if type(a['success'])is not bool:raise ValueError('Workflow outcome label required')
            if a['cost']is not None and(type(a['cost'])not in (int,float)or not math.isfinite(a['cost'])or a['cost']<0):
                raise ValueError('Invalid cost')
        out.append(row)
    return out,dict(excluded)


def policy_metrics(rows,threshold,*,probe_cost):
    delegated=0;baseline=0;success=0;known=True;savings=0.;deltas=[]
    for row in rows:
        a,b=row['arms']['incumbent'],row['arms']['proposal']
        selected=threshold is not None and row['confidence']>=threshold
        actual=b if selected else a
        delegated+=selected;baseline+=a['success'];success+=actual['success']
        deltas.append(int(actual['success'])-int(a['success']))
        # A confidence query is paid even when its proposal is not selected.
        if probe_cost is None or a['cost']is None or actual['cost']is None:known=False
        else:savings+=a['cost']-actual['cost']-(probe_cost if threshold is not None else 0)
    n=len(rows)
    return {'tasks':n,'delegated':delegated,'baseline_successes':baseline,'policy_successes':success,
        'success_delta':(success-baseline)/n if n else None,'all_attempt_cost_saving':savings if known else None,
        'paired_standard_error':(sum((d-sum(deltas)/n)**2 for d in deltas)/(n*(n-1)))**.5 if n>1 else None}


def fit(rows,*,min_pairs=20,max_success_loss=0.0,probe_cost=None,include_fixtures=False):
    """Fit on development families; assess once on separate validation families.

    Chooses the cheapest observed complete-task policy meeting the declared
    empirical quality floor. Error bars are descriptive; this is not a CRC or
    non-inferiority certificate. Unknown cost never becomes zero-cost savings.
    """
    if type(min_pairs)is not int or min_pairs<2:raise ValueError('At least two pairs per partition required')
    if type(max_success_loss)not in (int,float)or not math.isfinite(max_success_loss)or not 0<=max_success_loss<=1:
        raise ValueError('Declare an empirical workflow-loss tolerance')
    if probe_cost is not None and(type(probe_cost)not in (int,float)or not math.isfinite(probe_cost)or probe_cost<0):
        raise ValueError('Probe cost must be measured or unknown')
    rows,excluded=_validate(rows,include_fixtures=include_fixtures)
    grouped=defaultdict(list)
    for row in rows:grouped[(row['decision_type'],row['model'],row['policy_identity'])].append(row)
    groups=[]
    for (kind,model,identity),items in sorted(grouped.items()):
        training=[r for r in items if r['partition']=='fit'];validation=[r for r in items if r['partition']=='validate']
        group={'decision_type':kind,'model':model,'policy_identity':identity,'fit_pairs':len(training),'validation_pairs':len(validation),
               'threshold':None,'status':'insufficient_outcome_pairs','threshold_activation':False}
        groups.append(group)
        if min(len(training),len(validation))<min_pairs:continue
        candidates=[]
        for threshold in sorted({r['confidence']for r in training}):
            m=policy_metrics(training,threshold,probe_cost=probe_cost)
            if m['success_delta']>=-max_success_loss and m['all_attempt_cost_saving']is not None and m['all_attempt_cost_saving']>0:
                candidates.append((m['all_attempt_cost_saving'],threshold,m))
        if not candidates:group['status']='retain_incumbent_no_supported_saving';continue
        _,threshold,training_metrics=max(candidates,key=lambda c:(c[0],c[1]))
        group.update(threshold=threshold,status='offline_candidate_requires_paired_qualification',fit_metrics=training_metrics,
            validation={name:policy_metrics(validation,t,probe_cost=probe_cost)for name,t in
                        [('incumbent',None),('always_delegate',0.0),('fixed_0.5',0.5),('fitted',threshold)]})
        checked=group['validation']['fitted']
        group['empirical_validation_passed']=checked['success_delta']>=-max_success_loss and checked['all_attempt_cost_saving']is not None and checked['all_attempt_cost_saving']>0
        if not group['empirical_validation_passed']:group['status']='validation_failed_retain_incumbent'
    status='offline_candidates'if any(g['threshold']is not None for g in groups)else(
        'retain_incumbent'if groups and all(g['status']!='insufficient_outcome_pairs'for g in groups)else'insufficient_outcome_pairs')
    return {'schema':VERSION,'status':status,
        'groups':groups,'excluded':excluded,'min_pairs':min_pairs,'max_success_loss':max_success_loss,
        'decision_types':{kind:('outcome_pairs_present'if any(g['decision_type']==kind for g in groups)else'insufficient_outcome_pairs')for kind in sorted(TYPES)},
        'probe_cost':probe_cost,'fixture_evidence_included':include_fixtures,'runtime_policy_changed':False,
        'limitation':'No risk guarantee or provider activation. Refit after model, tools, mapping, rules or task-family changes.'}
