"""Immutable selection provenance across documentary and care-review layers."""
from pathlib import Path
import hashlib
import zlib
import json
from ...contracts import digest
from . import requirement_work, care_protocols as protocols

VERSION='combined-selection-context/1'


def identity():
    from . import program_documents,program_acquisition,measure_composition
    return digest([hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   requirement_work.identity(),protocols.code_identity(),program_documents.code_identity(),program_acquisition.identity(),measure_composition.identity()])


def contexts(path,patients,nodes,preparations,*,evaluated_patients,documentary=None,program_documents=None,program_acquisition=None,measure=None):
    db=protocols.storage(path);result={};current=identity();ic=protocols.interpretation.code_identity()
    try:
        for pid in sorted(set(patients)):
            refs=[]
            for node in nodes:
                prepared=preparations[node['id']];spec=prepared['protocol']
                assessed=pid in evaluated_patients[node['id']]
                key=protocols.receipt_id(prepared,pid,ic) if assessed else None
                raw=db.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(key,pid)).fetchone() if assessed else None
                if assessed and raw is None:raise ValueError('Evaluated protocol receipt missing')
                refs.append({'node_id':node['id'],'protocol_id':protocols.qualified(spec),'protocol_hash':spec['hash'],
                    'filter':node['parameters']['filter'],'receipt_id':key,
                    'status':json.loads(zlib.decompress(raw[0]))['status'] if raw else 'not_assessed'})
            result[pid]={'schema':VERSION,'patient_id':pid,'code_identity':current,
                'documentary':(documentary or {}).get(pid),'protocols':refs,'authority':'review_selection_provenance_only'}
            if program_documents:result[pid]['program_documents']=program_documents.get(pid)
            if program_acquisition:result[pid]['program_acquisition']=program_acquisition.get(pid)
            if measure:result[pid]['measure']=measure.get(pid)
    finally:db.close()
    return result


def verify(path,contexts):
    if not contexts:return
    current=identity();prepared={};db=protocols.storage(path);legacy=[];documents=[];acquisitions=[];measures=[]
    ic=protocols.interpretation.code_identity()
    try:
        for context in contexts:
            if isinstance(context,dict) and context.get('schema')==requirement_work.VERSION:
                legacy.append(context);continue
            if (not isinstance(context,dict) or set(context)-{'program_documents','program_acquisition','measure'}!={'schema','patient_id','code_identity','documentary','protocols','authority'}
                or context['schema']!=VERSION or context['code_identity']!=current
                or context['authority']!='review_selection_provenance_only'):
                raise ValueError('Selection contract changed; prepare a new work proposal')
            if context['documentary']:
                if context['documentary'].get('patient_id')!=context['patient_id']:raise ValueError('Documentary patient mismatch')
                legacy.append(context['documentary'])
            if context.get('program_documents'):
                if context['program_documents'].get('patient_id')!=context['patient_id']:raise ValueError('Program-document patient mismatch')
                documents.append(context['program_documents'])
            if context.get('measure'):
                if context['measure'].get('patient_id')!=context['patient_id']:raise ValueError('Measure patient mismatch')
                measures.append(context['measure'])
            if context.get('program_acquisition'):
                if context['program_acquisition'].get('patient_id')!=context['patient_id']:raise ValueError('Acquisition patient mismatch')
                acquisitions.append(context['program_acquisition'])
            if not isinstance(context['protocols'],list) or not (context['protocols'] or context.get('program_documents') or context.get('program_acquisition') or context.get('measure')):raise ValueError('Protocol or program-document references missing')
            for ref in context['protocols']:
                if not isinstance(ref,dict) or set(ref)!={'node_id','protocol_id','protocol_hash','filter','receipt_id','status'}:
                    raise ValueError('Exact protocol reference required')
                p=protocols.validate({'protocol':ref['protocol_id'],'filter':ref['filter']})
                spec=protocols.definition(p['protocol'])
                if ref['protocol_hash']!=spec['hash']:raise ValueError('Protocol definition changed')
                if ref['receipt_id'] is None:
                    if ref['status']!='not_assessed':raise ValueError('Missing protocol receipt')
                    continue
                if p['protocol'] not in prepared:prepared[p['protocol']]=protocols.prepare(path,p)
                expected=protocols.receipt_id(prepared[p['protocol']],context['patient_id'],ic)
                if ref['receipt_id']!=expected:raise ValueError('Protocol source or interpretation changed; reassess program work')
                raw=db.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(expected,context['patient_id'])).fetchone()
                if raw is None or json.loads(zlib.decompress(raw[0]))['status']!=ref['status']:
                    raise ValueError('Protocol selection receipt unavailable or changed')
    finally:db.close()
    requirement_work.verify(path,legacy)
    from .program_documents import verify_contexts
    verify_contexts(path,documents)
    from .program_acquisition import verify_contexts as verify_acquisition
    verify_acquisition(path,acquisitions)

    from .measure_composition import verify_contexts as verify_measure
    verify_measure(path,measures)
