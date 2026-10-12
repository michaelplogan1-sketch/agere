"""Bind a declared protocol review scope to existing population decisions.

This projects received accounts for review; it changes no clinical rule, source
interpretation or medication state. Undeclared protocol versions stay unmapped.
"""
from collections import Counter
from ...contracts import digest
from . import medication_accounts as medications

VERSION = 'population-protocol-review/2'
SCOPES = {
    'protocol:reduced_ef_therapy_review@2': {
        'protocol_hash': 'd05b20c4e6f6ad0d0dc307752f2d2f4b04e6ed0351ff28066352a8af3dc7eb53',
        'need_tags': ['medication_review'],
        'basis': 'The pinned protocol requests therapy-history reconciliation. This nominates documentary review, not a medication change or a verified treatment gap.',
    },
}


def rule_identity(program):
    return digest([VERSION, SCOPES[program]]) if program in SCOPES else None


def topics(assessment):
    scope = SCOPES.get(assessment['program']['id'])
    context = assessment.get('protocol_context')
    if (not scope or assessment['review_candidate'] is not True or not assessment['evidence']
            or not context or context['protocol_hash'] != scope['protocol_hash']):
        return []
    return [{'need_tag': tag, 'authority': 'documentary_protocol_review_only',
        'basis': scope['basis'], 'protocol_context': context,
        'program_definition': assessment['program'], 'assessment_id': assessment['id'],
        'source_records': assessment['evidence']} for tag in scope['need_tags']]


def dependency(program, snapshot, patient_ids=None):
    hashes = snapshot['patient_hashes']
    if patient_ids is not None:
        selected = set(patient_ids)
        hashes = {pid: value for pid,value in hashes.items() if pid in selected}
    return {'schema': VERSION, 'rule_identity': rule_identity(program),
        'medication_code': snapshot['code_identity'],
        'terminology_hash': snapshot['terminology_hash'],
        'patient_source_hashes': hashes}


def current_dependency(path, program, patient_ids=None):
    return dependency(program, medications.snapshot(path),patient_ids) if program in SCOPES else None


def medication_context(account):
    """Keep literal use/directions, selected dates, conflicts and exact sources."""
    statements = {s['id']: s for s in account['statements']}
    items = []
    for state in account['states']:
        for key in state['selected_statement_ids']:
            s = statements[key]
            refs = list({e['record_id']: {k: e.get(k) for k in
                ('record_id', 'record_hash', 'source_table', 'source_date', 'arrival_date')}
                for e in s['evidence']}.values())
            # Retain field-level witnesses for displayed values, plus the exact
            # whole review entry. Copies remain identified in the source chain.
            fields = [e for e in s['evidence'] if e['field_path'][0] in
                ('entries', 'medication_name_reported', 'use_statement', 'dose_reported',
                 'frequency_reported', 'directions_verification')]
            items.append({'statement_id': key, 'entity': state['entity'],
                'state': state['status'], 'selected_source_date': state['selected_source_date'],
                'source_date': s['source_date'], 'values': s['values'],
                'prior_statement_ids': state['prior_statement_ids'],
                'evidence': fields, 'source_records': refs, 'link_issues': s['link_issues'],
                'copy_lineage': s['reconciliation']['copy_lineage'],
                'current_regimen_established': False, 'ingestion_established': False,
                'maximum_tolerated_dose': 'not_established'})
    return {'items': items, 'item_count': len(items),
        'state_counts': dict(Counter(s['status'] for s in account['states'])),
        'scope': 'Latest received review per explicitly linked report. Reports are not verified current use; different interviews are not silently merged. Missing accounts do not establish missing care.'}


def prepare(path, program, assessments, as_of):
    if program not in SCOPES:
        return None, {}
    from . import program_work
    snap = medications.snapshot(path)
    contexts = {}
    with program_work.warehouse(path) as src:
        for a in assessments:
            context = a.get('protocol_context')
            if not context or context['protocol_hash'] != SCOPES[program]['protocol_hash']:
                raise ValueError('Pinned population protocol review scope changed')
            account = medications.load(src, a['patient_id'], snap, as_of)
            contexts[a['patient_id']] = {
                'protocol': {k: context[k] for k in ('receipt_id','status','trigger','documentary_readiness')},
                'medications': {'item_count': sum(len(s['selected_statement_ids']) for s in account['states']),
                    'state_counts': dict(Counter(s['status'] for s in account['states']))},
                'clinical_eligibility_established': False}
    return dependency(program, snap,contexts), contexts


def open_member(path, program, member, expected_dependency, as_of, patient_ids=None):
    """Load exact details for one member; never embed all source chains in a cohort."""
    from . import program_work, care_protocols
    snap = medications.snapshot(path)
    if dependency(program,snap,patient_ids) != expected_dependency:
        raise ValueError('Medication sources changed; prepare the population again')
    protocol = care_protocols.inspect(path,member['protocol_review']['protocol']['receipt_id'])
    if protocol['patient_id'] != member['patient_id']:
        raise ValueError('Protocol receipt belongs to another population member')
    with program_work.warehouse(path) as src:
        meds = medication_context(medications.load(src,member['patient_id'],snap,as_of))
    context = {'protocol': {k: protocol[k] for k in ('status','trigger','documentary_readiness','missing_inputs','unimplemented_clinical_requirements')},
        'protocol_receipt_id': protocol['id'], 'bindings': protocol['bindings'],
        'supporting_documentary_evidence': protocol.get('supporting_documentary_evidence',[]),
        'medications': meds, 'clinical_eligibility_established': False}
    if current_dependency(path,program,patient_ids) != expected_dependency:
        raise ValueError('Medication sources changed while opening the member')
    return context
