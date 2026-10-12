"""Verify v1 receipts whose digest preimage inherited the parent's digest.

The v1 writer copies the parent before setting result_digest. For parents which
already have that key, its previous value is part of the original preimage.
Reconstruct that exact preimage, then check code, parent, context and request
identity normally. No saved output, parent or provider response is modified.
"""
from . import linked_claim_reconciliation as L
from ...contracts import digest
import json

VERSION='linked-claim-inherited-digest/1'
def inspect(path,key):
    saved=json.loads(L.location(path,key).read_text())
    if saved.get('id')!=key or saved.get('code_identity')!=L.identity() or saved.get('status')!='complete':raise ValueError('Linked claim review identity changed')
    parent,row,_,_,expected=L.prepare(path,saved['parent_answer_id'])
    if expected!=key or digest(parent)!=saved['parent_answer_hash'] or digest(row)!=saved['context_digest']:raise ValueError('Linked claim source or parent changed')
    content={k:v for k,v in saved.items() if k!='result_digest'}
    if 'result_digest' in parent:content['result_digest']=parent['result_digest']
    if saved.get('result_digest')!=digest(content):raise ValueError('Linked claim result integrity changed')
    return saved

def install():
    L.install()
    if getattr(L.inspect,'inherited_digest_verified',False):return
    inspect.inherited_digest_verified=True;L.inspect=inspect
