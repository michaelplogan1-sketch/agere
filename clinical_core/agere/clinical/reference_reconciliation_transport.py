"""Lossless transport for reconciliation's already-tabular dependencies.

The original codec expects dependency object lists. Source-pass bodies keep
that exact codec. This adapter handles only the previously unsupported table
shape, without changing source contexts, units or saved source-pass requests.
"""
from copy import deepcopy
import json
from . import reference_table_context as F, context_builder as B

VERSION = 'reference-reconciliation-transport/1'
DECLARATION = 'Dependency columns and rows retain their original table shape. Nested unresolved_references and availability typed references use ordered object reference tables with presence masks and optional column dictionaries. Expand dictionaries and presence masks exactly; omitted fields are not null. Original excerpts are unchanged.'


def restore(wire):
    value=deepcopy(wire)
    if value.pop('reference_table_transport') != {'schema':VERSION,'instructions':DECLARATION}:
        raise ValueError('Unknown reconciliation reference codec')
    value['dependencies']=F.unmatrix(value['dependencies'])
    if 'unresolved_references' in value['dependencies']:
        value['dependencies']['unresolved_references']=F.untable(value['dependencies']['unresolved_references'])
    scope=value['received_source_availability']
    if 'records' in scope:scope['records']=F.unmatrix(scope['records'])
    for k in ('typed_references','unresolved_typed_references'):scope[k]=F.untable(scope[k])
    return value


def compact(body):
    original=json.loads(body['messages'][0]['content']); wire=deepcopy(original)
    dependency=wire['dependencies']
    if not isinstance(dependency,dict) or not {'columns','rows'}<=set(dependency):
        raise ValueError('Reconciliation dependency table required')
    wire['dependencies']=F.matrix(dependency)
    if 'unresolved_references' in dependency:
        wire['dependencies']['unresolved_references']=F.table(dependency['unresolved_references'])
    scope=wire['received_source_availability']
    if 'records' in scope:scope['records']=F.matrix(scope['records'])
    for k in ('typed_references','unresolved_typed_references'):scope[k]=F.table(scope[k])
    wire['reference_table_transport']={'schema':VERSION,'instructions':DECLARATION}
    if B.encode(restore(wire))!=B.encode(original):raise ValueError('Reconciliation metadata did not reconstruct exactly')
    result=deepcopy(body);result['messages'][0]['content']=B.encode(wire).decode();return result


def install():
    F.install()
    if getattr(F.compact_body,'reconciliation_tables',False):return
    old=F.compact_body
    def compact_any(body):
        wire=json.loads(body['messages'][0]['content'])
        return compact(body) if isinstance(wire.get('dependencies'),dict) else old(body)
    compact_any.reconciliation_tables=True;F.compact_body=compact_any
