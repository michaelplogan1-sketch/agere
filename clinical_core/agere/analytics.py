"""Outcome and contract analytics on explicit input tables, without inventing absent fields."""
from collections import Counter,defaultdict
import numpy as np
from sklearn.linear_model import LogisticRegression,Ridge
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from .mathematics import aipw,facility_shrinkage

def crossfit_effect(rows,covariates):
    required={'outcome','treatment','family_id',*covariates}
    if not rows or any(required-r.keys() for r in rows):return {'status':'missing_inputs','required':sorted(required)}
    x=np.array([[r[k] for k in covariates] for r in rows],float);y=np.array([r['outcome'] for r in rows],float)
    a=np.array([r['treatment'] for r in rows],int);groups=np.array([r['family_id'] for r in rows])
    if not np.isfinite(x).all() or not np.isfinite(y).all():raise ValueError('Nonfinite outcome/covariate')
    if len(set(groups))<4:return {'status':'insufficient_independent_groups'}
    e=np.zeros(len(y));m0=e.copy();m1=e.copy()
    for train,test in GroupKFold(n_splits=min(5,len(set(groups)))).split(x,a,groups):
        if len(set(a[train]))<2:return {'status':'missing_treatment_arm_in_training_fold'}
        propensity=make_pipeline(StandardScaler(),LogisticRegression(max_iter=500)).fit(x[train],a[train])
        e[test]=propensity.predict_proba(x[test])[:,1]
        for arm,output in ((0,m0),(1,m1)):
            ix=train[a[train]==arm]
            if len(ix)<2:return {'status':'insufficient_arm_observations'}
            fit=make_pipeline(StandardScaler(),Ridge(alpha=1)).fit(x[ix],y[ix]);output[test]=fit.predict(x[test])
    return {**aipw(y,a,e,m0,m1,groups=groups),'covariates':covariates,'nuisance_models':'Group-cross-fitted logistic propensity and ridge outcomes'}

def risk_adjusted_facilities(rows,covariates):
    required={'outcome','facility','family_id',*covariates}
    if not rows or any(required-r.keys() for r in rows):return {'status':'missing_inputs','required':sorted(required)}
    x=np.array([[r[k] for k in covariates] for r in rows],float);y=np.array([r['outcome'] for r in rows]);g=np.array([r['family_id'] for r in rows])
    if not np.isin(y,[0,1]).all() or not np.isfinite(x).all():raise ValueError('Finite covariates and binary outcomes required')
    if len(set(g))<4:return {'status':'insufficient_independent_groups'}
    predicted=np.zeros(len(y))
    for train,test in GroupKFold(n_splits=min(5,len(set(g)))).split(x,y,g):
        if len(set(y[train]))<2:return {'status':'missing_outcome_class_in_fold'}
        model=make_pipeline(StandardScaler(),LogisticRegression(max_iter=500)).fit(x[train],y[train]);predicted[test]=model.predict_proba(x[test])[:,1]
    grouped=defaultdict(list)
    for i,r in enumerate(rows):grouped[r['facility']].append(i)
    result=[]
    for facility,ix in sorted(grouped.items()):
        expected=float(sum(predicted[ix]));observed=int(sum(y[ix]))
        result.append({'facility':facility,'patients':len(ix),'events':observed,'expected_events':expected,
                       'observed_expected_ratio':observed/expected if expected else None})
    return {'status':'computed','facilities':result,'covariates':covariates,
            'interpretation':'Cross-fitted observed/expected comparison; residual confounding and selection remain. Not a best-provider ranking.'}

def network_usage(rows):
    required={'patient_id','provider','network_status','service_date','reason'}
    if not rows or any(required-r.keys() for r in rows):return {'status':'missing_inputs','required':sorted(required)}
    result=defaultdict(lambda:{'out_of_network_visits':0,'reasons':Counter(),'providers':set()})
    for r in rows:
        if r['network_status']=='out_of_network':
            v=result[r['patient_id']];v['out_of_network_visits']+=1;v['reasons'][r['reason'] or 'unknown']+=1;v['providers'].add(r['provider'])
    return {'status':'computed','patients':[{'patient_id':pid,**v,'providers':sorted(v['providers']),'reasons':dict(v['reasons'])} for pid,v in result.items()],
            'interpretation':'Reasons are supplied evidence, not inferred from network status'}

def coding_reconciliation(rows):
    required={'patient_id','documented_conditions','submitted_conditions','measurement_year'}
    if not rows or any(required-r.keys() for r in rows):return {'status':'missing_inputs','required':sorted(required)}
    return {'status':'computed','patients':[{'patient_id':r['patient_id'],'year':r['measurement_year'],
                'documentation_review_candidates':sorted(set(r['documented_conditions'])-set(r['submitted_conditions'])),
                'unsupported_submission_review':sorted(set(r['submitted_conditions'])-set(r['documented_conditions']))} for r in rows],
            'interpretation':'Documentation comparison only; no unsupported diagnosis, coding recommendation or risk score'}
