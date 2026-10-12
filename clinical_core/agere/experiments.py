"""Executable mathematical laboratory with explicit synthetic unit scenarios."""
import json
from pathlib import Path
import numpy as np
from .analytics import crossfit_effect,risk_adjusted_facilities
from .mathematics import benjamini_hochberg,allocate,facility_shrinkage
from ..signal_math import gaussian_change_points
from ..information_value import expected_information_value
from ..process_monitoring import monitor


def run(output):
    rng=np.random.default_rng(20261008);rows=[]
    for i in range(400):
        severity=float(rng.normal());treatment=int(rng.random()<.5)
        rows.append({'family_id':str(i),'severity':severity,'treatment':treatment,
                     'outcome':float(2*treatment+severity+rng.normal(scale=.2)),
                     'facility':'A' if i%2 else 'B'})
    binary=[{**r,'outcome':int(rng.random()<1/(1+np.exp(-r['severity'])))} for r in rows]
    information=expected_information_value(prior={'outside_care':.5,'care_absent':.5},
        utilities={'reconcile':{'outside_care':1,'care_absent':0},'review_gap':{'outside_care':0,'care_absent':1}},
        channels={'external_history':{'available':True,'cost':.05,'basis':'Illustrative symmetric channel',
             'likelihoods':{'outside_care':{'positive':.9,'negative':.1},'care_absent':{'positive':.1,'negative':.9}}}},
        utility_scale='Illustrative normalized decision utility, not dollars or QALYs',assumption_source='Synthetic mathematical smoke scenario')
    result={'synthetic':True,'scope':'Algorithm experiments, separate from the patient warehouse and clinical validation',
        'known_effect_scenario':{'generating_effect':2,'estimate':crossfit_effect(rows,['severity'])},
        'risk_adjusted_facilities':risk_adjusted_facilities(binary,['severity']),
        'small_denominator_shrinkage':facility_shrinkage([{'site':'A','eligible':10,'events':2},{'site':'B','eligible':100,'events':20}]),
        'fdr':benjamini_hochberg([.001,.02,.04,.4]),
        'allocation':allocate([{'id':'retrieve_record','cost':1,'expected_benefit':2},{'id':'review_complex_case','cost':3,'expected_benefit':4}],3,2),
        'change_points':gaussian_change_points(list(range(12)),[1]*6+[5]*6,[.2]*12),
        'process_control':monitor([10]*6+[4]*6,reference_mean=10,reference_sd=1),
        'information_value':information}
    Path(output).write_text(json.dumps(result,indent=2,allow_nan=False));return result
