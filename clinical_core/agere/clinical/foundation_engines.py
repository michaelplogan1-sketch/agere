"""Data-foundation engines on the question path: one registry, so each engine is a new module, not edits across the planner.

An engine module (listed in OPERATIONS as operation -> module name) provides:
  PRODUCT                       capability_registry entry {'population', 'view', 'use'} (the planner menu)
  CONTRACT                      graph_planner CONTRACTS entry {'inputs', 'parameters', 'meaning'}
  parameter_schema(catalog, obj, string, array) -> JSON schema of its parameters (graph_contracts helpers passed in)
  NATIVE_SCALARS                requested shapes it natively answers beyond table/patient_set, e.g. ('count', 'value')
  validate(p) -> p              raises ValueError on bad parameters
  execute(path, patient_ids, params, as_of) -> report     (read-only; patient_ids None = everyone)
  answer(report, plan, question) -> answer fields         (analysis_graph.build_answer_core merges them)
  local_question(question, as_of) -> graph | None         optional: exact deterministic routing (economical_planner.local_plan,
                                                          the cascade's free exact_local_plan tier). Claim only questions the
                                                          engine answers in full; never strip a qualifier to fit.
analysis_graph treats every OPERATIONS entry like CLINICAL_LOGIC (validate / execute / answer / code identity);
graph_contracts types them with an optional population input. Synthetic data only.
"""
import importlib
from pathlib import Path

VERSION = 'foundation-engines/1'
OPERATIONS = {
    'record_linkage': 'record_linkage',  # duplicate patient records: Fellegi-Sunter linkage (person_linkage.py)
    'scanned_document_search': 'scanned_documents',  # OCR text of scanned/faxed/outside documents, page/bbox-cited
    'onboarding_review': 'onboarding_review',  # feeds/formats, canonical (Tuva/OMOP) mapping, unmapped, OMOP round trip
    'source_defects': 'dq_detectors',  # detected source defects (Raha-style detectors + discovered FDs)
    'feed_gaps': 'feed_gaps',  # feed sequence gaps and snapshot recovery
    'ed_visits': 'ed_definition',
    'library_measure': 'library_measure',  # any measure-library measure (FUH/FUM/IET exact routing)  # one governed ED visit definition across EHR/ADT/claims with availability status
}


def module(op):
    return importlib.import_module(f'.{OPERATIONS[op]}', __package__)


def _each():
    for op in OPERATIONS:
        try:
            yield op, module(op)
        except ImportError:  # an engine that fails to import never removes the others from the menu
            continue


def products():
    return {op: m.PRODUCT for op, m in _each()}


def contracts():
    return {op: m.CONTRACT for op, m in _each()}


def native_scalars():
    return {op: tuple(getattr(m, 'NATIVE_SCALARS', ())) for op, m in _each()}


def schema(op, catalog, obj, string, array):
    return module(op).parameter_schema(catalog, obj, string, array)


def files():
    here = Path(__file__)
    return [here, *[here.with_name(f'{name}.py') for name in OPERATIONS.values() if here.with_name(f'{name}.py').exists()]]


def graph(question, op, parameters, purpose, *, interpretation=None):
    """The one-node graph an engine's local_question returns (no population input = the whole received population)."""
    return {'interpretation': interpretation or question, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': op, 'inputs': [], 'parameters': parameters, 'purpose': purpose}]}


def local_question(question, as_of=None):
    """First engine whose exact routing claims the question -> (graph, operation), else (None, None). Never raises."""
    for op, m in _each():
        fn = getattr(m, 'local_question', None)
        if fn is None: continue
        try:
            found = fn(question, as_of)
        except (ValueError, KeyError, TypeError):
            continue
        if found: return found, op
    return None, None
