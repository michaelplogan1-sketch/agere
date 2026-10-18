"""Routed enrollment plans: turn a patient list into a per-patient routed plan (coverage, attribution, gap, program +
vendor slot or coverage queue or exclusion, outreach task), with lineage on every field.

- catalog.py   program catalog (programs.json): programs, eligibility as data, gaps, exclusions, vendor slots
- router.py    route(semantic_db, patient_ids, program_kind, as_of, gap_details) -- deterministic, no model
- request.py   recognize plan requests and split out the cohort question / program kind -- deterministic text rules
- narrative.py optional model-drafted narrative and outreach scripts (prose only; off by default)
- answer.py    cohort -> route -> narrative -> answer envelope with a `plan` block
"""
from .catalog import load as load_catalog
from .request import is_plan_request, parse as parse_request
from .router import candidates, route, tasks, tasks_csv, summarize

__all__ = ['load_catalog', 'is_plan_request', 'parse_request', 'candidates', 'route', 'tasks', 'tasks_csv', 'summarize']
