"""Paired group bootstrap of a fixed saved development comparison; no refitting."""
from collections import defaultdict
from pathlib import Path
import json,math
import numpy as np
from ...contracts import digest
from . import appointment_qualification as Q,case_passes
VERSION='appointment-comparison-uncertainty/1'
PLAN={'replicates':2000,'seed':1729,'interval':0.95,'method':'paired_component_percentile_bootstrap','metric':'patient_weighted_brier_difference_model_minus_constant','quantile_method':'linear','selection':'both_frozen_calibrated_candidates','analysis_timing':'post_fit_descriptive_uncertainty_not_preregistered_inference'}
REFERENCE={'title':'Efron (1979), Bootstrap Methods: Another Look at the Jackknife','doi':'10.1214/aos/1176344552','url':'https://sites.stat.washington.edu/courses/stat527/s13/readings/ann_stat1979.pdf','checked_on':'2026-10-11','use':'Empirical resampling method, especially section 2; this grouped paired implementation and its applicability are engineering choices, not clinical validation.'}
LIMITS=['Intervals condition on the saved fitted/calibrated models and original development partition. Training, calibration and model-selection uncertainty are not included.',
 'Resamples treat protected components as independent and exchangeable; source dependence outside those groups, representativeness and clinical capture remain unverified.',
 'Pointwise percentile intervals are approximate and have no simultaneous-comparison guarantee. This is a post-fit descriptive analysis, not a preregistered significance test.',
 'Negative Brier difference favors the candidate on this source-report task only. Crossing zero does not establish equivalence; excluding zero would not establish clinical utility or external validity.',
 'No outer evaluation, new model fitting, threshold selection, individual patient score or automatic promotion occurs.']
LINK='appointment-comparison-uncertainty.json'
def identity():return digest([Q.Q.sha(__file__),Q.identity(),PLAN])
def calculate(rows,predictions,prevalence):
 if type(prevalence) not in (float,int) or not math.isfinite(prevalence) or not 0<=prevalence<=1:raise ValueError('Valid training prevalence required')
 names=Q.Q.PLAN['candidates'];byid={r['patient_id']:r for r in rows};pp={p['patient_id']:p for p in predictions}
 if len(byid)!=len(rows) or len(pp)!=len(predictions) or set(byid)!=set(pp):raise ValueError('Exact unique held-out patient join required')
 groups=defaultdict(list)
 for pid,r in byid.items():
  if r['partition']!='development_validation' or not r.get('component') or type(r['recorded_label']) is not int or r['recorded_label'] not in (0,1):raise ValueError('Held-out development rows only')
  p=pp[pid]
  if p['recorded_label']!=r['recorded_label'] or set(p['models'])!=set(names):raise ValueError('Frozen comparison predictions required')
  if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in p['models'].values()):raise ValueError('Finite probability required')
  groups[r['component']].append((r['recorded_label'],p['models']))
 if len(groups)<2:raise ValueError('At least two protected development components required')
 keys=sorted(groups);sizes=np.array([len(groups[k]) for k in keys]);draws=np.random.default_rng(PLAN['seed']).integers(len(keys),size=(PLAN['replicates'],len(keys)));den=sizes[draws].sum(axis=1);comparisons={}
 for name in names:
  losses=np.array([sum((p[name]-y)**2-(prevalence-y)**2 for y,p in groups[k]) for k in keys]);sample=losses[draws].sum(axis=1)/den
  lo,hi=np.quantile(sample,[.025,.975],method=PLAN['quantile_method'])
  comparisons[name]={'difference':float(losses.sum()/sizes.sum()),'lower':float(lo),'upper':float(hi),'includes_zero':bool(lo<=0<=hi)}
 return {'patients':len(rows),'protected_components':len(groups),'recorded_class_counts':{str(i):sum(r['recorded_label']==i for r in rows) for i in (0,1)},'comparisons':comparisons,'plan':PLAN,'limits':LIMITS,'reference':REFERENCE,'new_model_fits':0,'clinical_prediction_authorized':False}
def prepare(path):
 current=Q.status(path)
 if not current.get('current') or current['status']!='research_comparison_fitted':raise ValueError('Current fitted development comparison required')
 link=Q.R.read(Path(path)/Q.LINK);root=Path(link['path']);private=Q.R.read(root/'qualification.json');pred=Q.R.read(root/'models/development-predictions.json')
 train=[r['recorded_label'] for r in private['rows'] if r['partition']=='train'];rows=[r for r in private['rows'] if r['partition']=='development_validation']
 r=calculate(rows,pred,sum(train)/len(train));r.update(schema=VERSION,qualification_id=current['id'],implementation_identity=identity(),qualification_report_sha256=link['sha256'],predictions_sha256=Q.Q.sha(root/'models/development-predictions.json'))
 if Q.status(path)['id']!=current['id']:raise ValueError('Qualification changed during uncertainty calculation')
 r['id']=digest(r);case_passes.atomic_write(Path(path)/LINK,r);return r

def status(path,current):
 p=Path(path)/LINK
 if not p.exists():return {'available':False}
 r=Q.R.read(p)
 if r.get('id')!=digest({k:v for k,v in r.items() if k!='id'}) or r['implementation_identity']!=identity() or r['qualification_id']!=current.get('id'):raise ValueError('Comparison uncertainty dependency changed')
 link=Q.R.read(Path(path)/Q.LINK)
 if link['sha256']!=r['qualification_report_sha256'] or Q.Q.sha(Path(link['path'])/'models/development-predictions.json')!=r['predictions_sha256']:raise ValueError('Comparison prediction dependency changed')
 return {**r,'available':True,'current':True}
