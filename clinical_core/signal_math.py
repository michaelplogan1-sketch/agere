"""Uncertainty-weighted change-point analysis inspired by astronomical methods.

Gaussian measurements with supplied independent standard errors; no diagnosis,
clinical threshold, causal effect, or automatic alert follows from segmentation.
"""
import math


def gaussian_change_points(times,values,errors,*,penalty=6.0,max_points=2048):
    """Exact dynamic program for a penalized piecewise-constant Gaussian model.

    Block objective: (sum(x/sigma²))² / (2 sum(1/sigma²)) - penalty.
    The omitted sum(x²/sigma²) is constant across partitions. The penalty is a
    caller assumption, not a calibrated false-alarm probability.
    """
    n=len(times)
    if n!=len(values) or n!=len(errors):raise ValueError('Mismatched measurement arrays')
    if not math.isfinite(penalty) or penalty<0:raise ValueError('Invalid penalty')
    if n>max_points:raise ValueError('Series exceeds bounded quadratic computation')
    if not all(math.isfinite(v) for seq in (times,values,errors) for v in seq):raise ValueError('Nonfinite observation')
    if any(s<=0 for s in errors):raise ValueError('Uncertainty must be strictly positive')
    if any(b<=a for a,b in zip(times,times[1:])):raise ValueError('Times must be strictly increasing')
    if not n:return {'blocks':[],'change_indices':[],'diagnosis':None}
    p0=[0.0];p1=[0.0]
    for x,s in zip(values,errors):
        try:w=1/(s*s)
        except ZeroDivisionError:raise ValueError('Uncertainty exceeds numerical precision') from None
        if not math.isfinite(w) or w<=0:raise ValueError('Uncertainty exceeds numerical precision')
        p0.append(p0[-1]+w);p1.append(p1[-1]+x*w)
        if not math.isfinite(p0[-1]) or not math.isfinite(p1[-1]):raise ValueError('Measurement exceeds numerical range')
    best=[0.0]+[-math.inf]*n;previous=[0]*(n+1)
    for end in range(1,n+1):
        for start in range(end):
            w=p0[end]-p0[start];z=p1[end]-p1[start]
            if w<=0:raise ValueError('Uncertainty scales exceed numerical precision')
            value=best[start]+0.5*(z/w)*z-penalty
            if not math.isfinite(value):raise ValueError('Measurement exceeds numerical range')
            if value>best[end]:best[end]=value;previous[end]=start
    blocks=[];end=n
    while end:
        start=previous[end];w=p0[end]-p0[start]
        blocks.append(dict(start_index=start,end_index_exclusive=end,start_time=times[start],
            end_time=times[end-1],weighted_mean=(p1[end]-p1[start])/w,
            standard_error=math.sqrt(1/w),observations=end-start))
        end=start
    blocks.reverse()
    return dict(blocks=blocks,change_indices=[b['start_index'] for b in blocks[1:]],
        penalty=penalty,objective=best[n],diagnosis=None,
        assumptions=['Piecewise constant latent mean','Independent Gaussian measurement errors with known standard deviations',
            'Consistent units and assay context','Observation timing is not itself modeled'],
        limitations=['Not calibrated for clinical decisions','Informative sampling, correlated errors and source changes can mimic deterioration',
            'Retrospective boundaries are not prospective warning performance'])
