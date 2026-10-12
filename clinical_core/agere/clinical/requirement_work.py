"""Carry documentary selection evidence into immutable program assessments."""
import json
from pathlib import Path
import hashlib
import zlib

from ...contracts import digest
from . import evidence_requirements as requirements, source_state

VERSION='requirement-work-context/1'


def identity():
    return digest([hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),requirements.code_identity()])


def contexts(path,patients,nodes,snapshots,*,evaluated_patients):
    """Reference all upstream requirement evaluations, including nonmatching ones.

    A union may include a patient outside a prerequisite's evaluated population.
    Such a node is explicitly not_assessed, never fabricated as supporting evidence.
    """
    db=requirements.storage(path);result={};code=requirements.code_identity();current=identity()
    specs={node['id']:requirements.definition(node['parameters']['contract']) for node in nodes}
    interpretation_code=requirements.interpretation.code_identity()
    try:
        for pid in sorted(set(patients)):
            refs=[]
            for node in nodes:
                contract=specs[node['id']]
                key=requirements.receipt_id(snapshots[node['id']],pid,code=code,interpretation_code=interpretation_code)
                assessed=pid in evaluated_patients[node['id']]
                raw=db.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(key,pid)).fetchone() if assessed else None
                if assessed and raw is None:raise ValueError('Evaluated requirement receipt missing')
                refs.append({'node_id':node['id'],'contract_id':contract['id'],'contract_hash':contract['hash'],
                    'filter':node['parameters']['filter'],'receipt_id':key if raw else None,
                    'status':json.loads(zlib.decompress(raw[0]))['status'] if raw else 'not_assessed'})
            result[pid]={'schema':VERSION,'patient_id':pid,'code_identity':current,'requirements':refs,
                         'authority':'documentary_selection_provenance_only'}
    finally:db.close()
    return result


def verify(path,contexts):
    """Verify current per-patient sources/reviews once per distinct contract."""
    if not contexts:return
    snaps={};code=requirements.code_identity();current=identity();db=requirements.storage(path)
    specs=requirements.definitions();interpretation_code=requirements.interpretation.code_identity()
    try:
        for context in contexts:
            if (not isinstance(context,dict) or set(context)!={'schema','patient_id','code_identity','requirements','authority'}
                or context['schema']!=VERSION or context['code_identity']!=current
                or context['authority']!='documentary_selection_provenance_only'):
                raise ValueError('Documentary selection contract changed; prepare a new work proposal')
            if not isinstance(context['requirements'],list) or not context['requirements']:raise ValueError('Requirement references missing')
            for ref in context['requirements']:
                spec=specs.get(ref['contract_id'])
                if spec is None:raise ValueError('Unknown documentary contract')
                if ref['contract_hash']!=spec['hash']:raise ValueError('Documentary definition changed; prepare a new work proposal')
                if ref['receipt_id'] is None:
                    if ref['status']!='not_assessed':raise ValueError('Missing requirement receipt')
                    continue
                if spec['id'] not in snaps:
                    snaps[spec['id']]=requirements.snapshot(path,{'contract':spec['id'],'filter':'all'})
                expected=requirements.receipt_id(snaps[spec['id']],context['patient_id'],code=code,interpretation_code=interpretation_code)
                if ref['receipt_id']!=expected:raise ValueError('Documentary source interpretation changed; reassess program work')
                raw=db.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(expected,context['patient_id'])).fetchone()
                if raw is None or json.loads(zlib.decompress(raw[0]))['status']!=ref['status']:
                    raise ValueError('Documentary selection receipt unavailable or changed')
    finally:db.close()
