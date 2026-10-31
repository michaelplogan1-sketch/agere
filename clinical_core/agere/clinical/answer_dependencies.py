"""Saved derivation and observation checks at reuse and mutation boundaries.

The saved answer remains inspectable. A current derivation is not a claim of
clinical validity. These checks do not make independent stores one transaction;
callers recheck inside their work transaction and after slow generation.
"""
from pathlib import Path

from .request_plan import fingerprint

VERSION = 'answer-dependencies/1'
FIELDS = ('question', 'original_question', 'source', 'answer', 'patient_ids',
          'patient_rows', 'input_patient_ids', 'graph', 'planner_receipt', 'cohort_transform', 'workflow',
          'context_policy_identity', 'feedback_identity', 'investigation')


def wal_identity(path):
    """Committed SQLite WAL writes may leave the main database stat unchanged."""
    target = Path(path) / 'semantic.sqlite3-wal'
    try:
        stat = target.stat()
        return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
    except FileNotFoundError:
        return None


def seal(path, context):
    return {'schema': VERSION, 'derivation': fingerprint({k: context.get(k) for k in FIELDS}),
            'observation': {'source': context['source'], 'wal': wal_identity(path)}}


def source_readings(path, context):
    from ..deliverables.patient_evidence import active_assertion_ids
    from .typed_document_review import assertion_disposition
    return assertion_disposition(path, active_assertion_ids(context))


def validate(path, context, *, source, policy, check_probes=False):
    saved = context.get('dependencies')
    issues = []
    derivation = context.get('context_policy_identity') == policy
    used = ((context.get('planner_receipt') or {}).get('interpretation') or {}).get('procedural_memory')
    if used:
        from . import procedural_memory
        contract = (context.get('planner_receipt') or {}).get('request_contract') or {}
        if ('population' not in contract or used.get('identity') != procedural_memory.identity(
                path, context['question'], probe=check_probes, population=contract.get('population'))):
            derivation = False
            issues.append('A procedural lesson used by this answer is no longer applicable; re-evaluate the original question.')
    observation = context.get('source') == source
    if not saved or saved.get('schema') != VERSION:
        derivation = False
        issues.append('Saved answer has no current dependency receipt; ask again before reuse.')
    else:
        if saved.get('derivation') != fingerprint({k: context.get(k) for k in FIELDS}):
            derivation = False
            issues.append('Saved answer contents differ from their sealed derivation.')
        observation = observation and saved.get('observation') == {'source': source, 'wal': wal_identity(path)}
    evidence = context.get('answer', {}).get('evidence_completeness') or {}
    complete = evidence.get('status') != 'incomplete'
    if not derivation:
        issues.append('Planning definitions or the saved derivation are no longer current.')
    if not observation:
        issues.append('Warehouse observations changed; recompute before reuse.')
    if not complete:
        issues.append('Some analytical requirements lack executed evidence.')
    premises = []
    if context.get('graph'):  # PlanFence: the premises the saved plan was derived under must still hold before reuse or action
        try:
            from .plan_fence import premise_issues
            premises = premise_issues(path, context.get('question'), context['graph'])
        except Exception:  # noqa: BLE001 - premise validation annotates; it never makes a saved answer unreadable
            premises = []
        issues += ['Premise changed: ' + p for p in premises]
    disposition = source_readings(path, context)
    if not disposition['source_readings_current']:
        issues.append('A dependent source reading is suspended.' if disposition['status'] == 'suspended'
                      else 'Source-reading status is unavailable.')
    return {'schema': VERSION, 'derivation_current': derivation, 'observation_current': observation,
            'evidence_complete': complete, 'premises_current': not premises,
            'source_readings_current': disposition['source_readings_current'], 'source_disposition': disposition,
            'current': derivation and observation and complete and not premises and disposition['source_readings_current'], 'issues': issues}
