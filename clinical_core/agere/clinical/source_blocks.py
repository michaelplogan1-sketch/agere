"""Poisson block segmentation of observed source arrivals.

Scargle et al., arXiv:1207.5578, section 3.2. Calendar exposure describes
received-record intensity, not verified clinical surveillance or incidence.
"""
from calendar import monthrange
from collections import Counter
from datetime import date
import math
from .clinical_events import iso


def poisson_blocks(counts, exposure, penalty=6.0):
    if not counts or len(counts)!=len(exposure) or len(counts)>240:
        raise ValueError('One to 240 aligned count/exposure bins required')
    if any(type(n) is not int or n<0 for n in counts) or any(type(t) not in (int,float) or not math.isfinite(t) or t<=0 for t in exposure):
        raise ValueError('Nonnegative integer counts and positive finite exposure required')
    if type(penalty) not in (int,float) or not math.isfinite(penalty) or penalty<0:
        raise ValueError('Finite nonnegative penalty required')
    n=len(counts);cn=[0];ct=[0.0]
    for k,t in zip(counts,exposure):cn.append(cn[-1]+k);ct.append(ct[-1]+t)
    best=[0.0]+[-math.inf]*n;prior=[0]*(n+1)
    for end in range(1,n+1):
        for start in range(end):
            k=cn[end]-cn[start];t=ct[end]-ct[start]
            # The -N term and per-bin constants are invariant across partitions.
            fitness=k*math.log(k/t) if k else 0.0
            score=best[start]+fitness-penalty
            if score>best[end]:best[end]=score;prior[end]=start
    blocks=[];end=n
    while end:
        start=prior[end];k=cn[end]-cn[start];t=ct[end]-ct[start]
        blocks.append({'start_index':start,'end_index_exclusive':end,'count':k,'calendar_days':t,'records_per_day':k/t})
        end=start
    blocks.reverse()
    return {'blocks':blocks,'change_indices':[b['start_index'] for b in blocks[1:]],
            'penalty':penalty,'objective':best[-1],'false_alarm_probability':None,
            'assumptions':['Independent Poisson counts conditional on a constant block rate.',
                           'Calendar exposure measures received-document intensity, not clinical follow-up.',
                           'Penalty is an engineering parameter, not a calibrated clinical alert threshold.'],
            'method_reference':'https://arxiv.org/html/1207.5578v3#S3.SS2'}


def month_bins(as_of, months=12):
    cutoff=date.fromisoformat(as_of);last=cutoff.year*12+cutoff.month-1
    labels=[f'{x//12:04d}-{x%12+1:02d}' for x in range(last-months+1,last+1)]
    days=[monthrange(int(x[:4]),int(x[5:]))[1] for x in labels]
    days[-1]=cutoff.day
    return labels,days


def source_summary(records, as_of, streams, months=12):
    labels,days=month_bins(as_of,months);reports=[]
    for stream in streams:
        chosen=[r for r in records if r['source_table']==stream and iso(r.get('available_date'))
                and r['available_date']<=as_of and r['available_date'][:7] in labels]
        counts=Counter(r['available_date'][:7] for r in chosen)
        series=[counts[m] for m in labels]
        result=poisson_blocks(series,days)
        reports.append({'source':stream,'months':labels,'record_counts':series,'date_basis':'arrival',
                        'source_record_ids':[r['record_id'] for r in chosen],**result})
    return {'method':'Penalized Poisson Bayesian-block-style segmentation of received records',
            'sources':reports,'clinical_change_established':False,
            'limitations':['Bursts can reflect batching, source outages, copied content or a real change in activity.',
                           'Changing source intensity alone cannot establish clinical deterioration.']}
