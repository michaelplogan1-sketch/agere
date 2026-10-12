"""Established mathematical operators repurposed for Agere; assumptions are explicit."""
from __future__ import annotations
import math
import numpy as np
from scipy.optimize import Bounds,LinearConstraint,milp
from scipy.stats import beta


def numeric(*arrays):
    out=[np.asarray(x,dtype=float) for x in arrays]
    if not all(np.isfinite(x).all() for x in out):raise ValueError('Finite numbers required')
    return out

def benjamini_hochberg(p_values):
    p,=numeric(p_values)
    if p.ndim!=1 or np.any((p<0)|(p>1)):raise ValueError('Invalid p values')
    if not len(p):return []
    order=np.argsort(p);ranked=p[order]*len(p)/np.arange(1,len(p)+1)
    adjusted=np.minimum.accumulate(ranked[::-1])[::-1].clip(0,1);q=np.empty_like(p);q[order]=adjusted
    return q.tolist()

def facility_shrinkage(rows,prior_events=1.,prior_nonevents=1.):
    if prior_events<=0 or prior_nonevents<=0:raise ValueError('Positive prior required')
    result=[]
    for r in rows:
        n,k=r['eligible'],r['events']
        if type(n)!=int or type(k)!=int or not 0<=k<=n:raise ValueError('Invalid counts')
        a=k+prior_events;b=n-k+prior_nonevents
        result.append({**r,'posterior_rate':a/(a+b),'interval_95':beta.ppf([.025,.975],a,b).tolist(),
                       'prior':[prior_events,prior_nonevents],'risk_adjusted':False,
                       'interpretation':'Beta-binomial shrinkage; case-mix adjustment requires covariates'})
    return result

def aipw(outcome,treatment,propensity,mu0,mu1,*,groups=None,overlap=.05):
    """AIPW on externally cross-fitted nuisance predictions; clustered uncertainty optional."""
    y,a,e,m0,m1=numeric(outcome,treatment,propensity,mu0,mu1)
    if any(x.ndim!=1 or len(x)!=len(y) for x in (a,e,m0,m1)) or len(y)<4:raise ValueError('Matched observations required')
    if not np.isin(a,[0,1]).all() or len(np.unique(a))!=2:raise ValueError('Both treatment arms required')
    if not 0<overlap<.5:raise ValueError('Invalid overlap bound')
    if np.any((e<overlap)|(e>1-overlap)):
        return {'status':'overlap_failure','outside_overlap':int(np.sum((e<overlap)|(e>1-overlap))),
                'effect':None,'reason':'No silent clipping or causal extrapolation'}
    psi=m1-m0+a*(y-m1)/e-(1-a)*(y-m0)/(1-e);estimate=float(psi.mean())
    if groups is None:se=float(np.std(psi,ddof=1)/math.sqrt(len(y)))
    else:
        if len(groups)!=len(y):raise ValueError('Group length mismatch')
        unique=set(groups)
        if len(unique)<2:raise ValueError('At least two independent clusters')
        sums=np.array([sum(psi[i]-estimate for i,g in enumerate(groups) if g==key) for key in unique])
        se=float(np.sqrt(len(unique)/(len(unique)-1)*sum(sums*sums))/len(y))
    return {'status':'computed','effect':estimate,'standard_error':se,'interval_95':[estimate-1.96*se,estimate+1.96*se],
            'method':'Augmented inverse probability weighting',
            'assumptions':['Nuisance predictions cross-fitted without patient/family leakage','No unmeasured confounding',
                           'Consistency, overlap, correctly specified propensity or outcome regression'],
            'causal_validity_verified':False}

def allocate(candidates,budget,capacity=None):
    if not math.isfinite(budget) or budget<0:raise ValueError('Invalid budget')
    if not candidates:return {'status':'computed','selected':[],'cost':0.,'objective':0.}
    benefit,cost=numeric([x['expected_benefit'] for x in candidates],[x['cost'] for x in candidates])
    if np.any(cost<0):raise ValueError('Negative costs')
    if len({r['id'] for r in candidates})!=len(candidates):raise ValueError('Duplicate candidates')
    matrix=[cost];upper=[budget]
    if capacity is not None:
        if type(capacity)!=int or capacity<0:raise ValueError('Invalid capacity')
        matrix.append(np.ones(len(cost)));upper.append(capacity)
    result=milp(-benefit,integrality=np.ones(len(cost)),bounds=Bounds(0,1),
                constraints=LinearConstraint(np.array(matrix),-np.inf,np.array(upper)),options={'time_limit':10})
    if result.x is None:return {'status':'no_solution','selected':[]}
    chosen=np.flatnonzero(result.x>.5)
    return {'status':'optimal' if result.success else 'bounded_feasible','selected':[candidates[i]['id'] for i in chosen],
            'cost':float(sum(cost[chosen])),'objective':float(sum(benefit[chosen])),
            'interpretation':'Allocation under supplied benefit assumptions, not measured clinical benefit'}

def route_search(confidence,correct,local_cost,escalation_cost,max_error=.05):
    confidence,correct,local_cost,escalation_cost=numeric(confidence,correct,local_cost,escalation_cost)
    if any(len(x)!=len(correct) for x in (confidence,local_cost,escalation_cost)) or not len(correct):raise ValueError('Aligned calibration rows required')
    if not np.isin(correct,[0,1]).all() or np.any((confidence<0)|(confidence>1)) or np.any(local_cost<0) or np.any(escalation_cost<0):raise ValueError('Invalid routing rows')
    results=[]
    for threshold in sorted(set([0.,1.000001,*confidence.tolist()])):
        accept=confidence>=threshold;n=int(accept.sum());errors=int(np.sum(1-correct[accept]))
        # One-sided exact binomial upper bound; point accuracy alone cannot authorize routing.
        upper=float(beta.ppf(.95,errors+1,n-errors)) if n>errors else 1.
        if n==0:upper=0.
        total=float(np.sum(local_cost)+np.sum(escalation_cost[~accept]))
        results.append({'threshold':threshold,'accepted':n,'errors':errors,'error_upper_95':upper,'estimated_cost':total})
    valid=[r for r in results if r['error_upper_95']<=max_error]
    best=min(valid,key=lambda r:(r['estimated_cost'],-r['accepted']))
    return {'selected':best,'candidates':results,'scope':'Calibration-set selection; needs untouched test audit',
            'escalation_accuracy_assumed':False}

def claim_development(rows,cutoff,max_lag=12):
    """Actuarial runout triangle. Descriptive paid amounts, not projected ultimate liabilities."""
    from datetime import date
    asof=date.fromisoformat(cutoff);cells={}
    for r in rows:
        if r.get('available_date','9999')>cutoff or r['service_date']>cutoff or r['paid_date']>cutoff:continue
        service=date.fromisoformat(r['service_date']);paid=date.fromisoformat(r['paid_date'])
        lag=(paid.year-service.year)*12+paid.month-service.month
        if not 0<=lag<=max_lag:continue
        key=(service.strftime('%Y-%m'),lag)
        amount=sum(float(x.get('payer_paid_usd',0)) for x in r.get('lines',[]))
        if not math.isfinite(amount):raise ValueError('Invalid claim dollars')
        cells[key]=cells.get(key,0)+amount
    return {'cells':[{'service_month':m,'payment_lag_months':l,'paid_usd':v} for (m,l),v in sorted(cells.items())],
            'projection':None,'interpretation':'Observed claims development; enrollment and maturity needed for ultimate-cost modeling'}
