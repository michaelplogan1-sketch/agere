"""Fit local source-grounded forecasting specialists on the synthetic warehouse."""
import argparse,json
from pathlib import Path
from clinical_core.agere.clinical.warehouse_prediction import fit

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--stack',type=Path,default=Path('artifacts/agere-population-stack-v3'))
    p.add_argument('--output',type=Path,default=Path('artifacts/agere-warehouse-predictors-v1'))
    p.add_argument('--feature-cutoff',default='2026-07-31');p.add_argument('--label-through',default='2026-08-31')
    a=p.parse_args();r=fit(a.stack,a.output,a.feature_cutoff,a.label_through,
                          {'stream':'adt','conditions':[{'field':'event','op':'eq','value':'A01'}]})
    print(json.dumps({k:r[k] for k in ('schema','splits','models','test_training_prevalence_baseline','patients_predicted','elapsed_seconds','new_paid_api_calls')},indent=2))
