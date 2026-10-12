"""Adapt any registered source-review protocol into existing accountable work.

Protocol candidates include unresolved source-review signals. Neither a candidate
nor documentary readiness establishes clinical or program eligibility.
"""
from pathlib import Path
import hashlib,json,zlib
from ...contracts import digest
from . import care_protocols as P

PREFIX='protocol:'
VERSION='protocol-program-adapter/1'


def handles(key):return isinstance(key,str) and key.startswith(PREFIX)


def identity():return digest([hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),P.code_identity()])


def definition(key):
    if not handles(key):raise ValueError('Namespaced protocol review program required')
    spec=P.definition(key[len(PREFIX):])
    return {'id':key,'name':'Protocol review: '+spec['title']+' · v'+spec['version'],'operator':'protocol_review','protocol_id':P.qualified(spec),
        'version':spec['version'],'protocol_hash':spec['hash'],'purpose':spec['purpose'],
        'authority':'source_review_only','clinical_eligibility_established':False}


def catalog():return [definition(PREFIX+key) for key in P.definitions()]


def preparation(path,spec):return P.prepare(path,{'protocol':spec['protocol_id'],'filter':'all'})


def project(row):
    if row.get('synthetic') is not True or type(row.get('review_candidate')) is not bool:raise ValueError('Explicit synthetic protocol candidate state required')
    return {'synthetic':True,'patient_id':row['patient_id'],'source_run':row['base']['source_run'],
        'operator':'protocol_review','adapter':VERSION,'protocol_receipt_id':row['id'],'protocol_evidence':row,
        'source_bound_facts':[],
        'workflow':{'as_of':row['as_of'],'review_candidate':row['review_candidate'],'status':row['status'],
            'missing_information':[m['label'] for m in row['missing_inputs']]+row['unimplemented_clinical_requirements'],
            'proposed_actions':row['proposed_actions'],'clinical_eligibility_established':False,
            'authority':'Protocol source-review candidacy, not verified treatment or program eligibility'}}


def rows(path,spec,patient_ids=None):
    if spec!=definition(spec['id']):raise ValueError('Protocol program definition changed')
    prep=preparation(path,spec);ids=sorted(prep['patient_inputs'] if patient_ids is None else set(patient_ids))
    params={'protocol':spec['protocol_id'],'filter':'all'}
    P.execute(path,ids,params,prepared=prep,preview_limit=0);db=P.storage(path);result={};ic=P.interpretation.code_identity()
    try:
        for pid in ids:
            key=P.receipt_id(prep,pid,ic);raw=db.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(key,pid)).fetchone()
            if raw is None:raise ValueError('Current program protocol receipt missing')
            row=json.loads(zlib.decompress(raw[0]))
            if row['patient_id']!=pid or row['protocol']['hash']!=spec['protocol_hash']:raise ValueError('Protocol receipt identity mismatch')
            result[pid]=json.dumps(project(row),sort_keys=True,allow_nan=False)
    finally:db.close()
    fresh=preparation(path,spec)
    if any(P.receipt_id(fresh,pid,ic)!=P.receipt_id(prep,pid,ic) for pid in ids):raise ValueError('Protocol program inputs changed during assessment')
    return result
