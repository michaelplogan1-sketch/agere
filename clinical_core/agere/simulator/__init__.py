"""Agere population simulator: test an action plan against a simulated copy of the customer's population before acting,
then compare the prediction with what actually happened.

- data.py      workspace history in memory (members, admissions / ED visits with their own claim costs, other spending)
- baseline.py  do-nothing model: monthly admission / ED rates and spending, time-split calibrated
- funnel.py    who receives the intervention and when (the action backbone's simulated counterparties, per scenario)
- effects.py   parameters (config.json: cited effects, assumptions, costs) and effect models (GDMT pillar combination
               anchored to the network meta-analysis value; never a product of hazard ratios)
- engine.py    simulate(): Monte Carlo over patients and parameters for do-nothing vs each arm, settlement through vbc.settle
- actual.py    compare_to_actual(): predicted vs observed at a later as_of, observed effect, simulator calibration
"""
from .engine import simulate
from .actual import compare_to_actual

__all__ = ['simulate', 'compare_to_actual']
