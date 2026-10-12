"""Versioned received-record links, without changing historical native contracts."""
from collections import deque
from pathlib import Path
import hashlib
from ...contracts import digest
from . import warehouse_interpretation as native,context_builder

VERSION='received-reference-graph/1'
# Observed public received schemas, not hidden generator links or clinical rules.
EXTRA={
 'diagnostic_orders':{'referral_packet_record_id':{'referral_documents'},
   'supporting_record_ids':{'care_intake','diagnostic_review_interviews','laboratory_result_notifications','diagnostic_orders','condition_assessments'}},
 'diagnostic_referral_packets':{'order_record_id':{'diagnostic_orders'}},
 'diagnostic_access_contacts':{'order_record_id':{'diagnostic_orders'},'packet_record_id':{'diagnostic_referral_packets'},'report_record_id':{'diagnostic_test_reports'}},
 'diagnostic_test_sessions':{'intake_record_id':{'care_intake'},'order_record_id':{'diagnostic_orders'},'packet_record_id':{'diagnostic_referral_packets'}},
 'diagnostic_test_reports':{'order_record_id':{'diagnostic_orders'},'session_record_id':{'diagnostic_test_sessions'}},
 'received_diagnostic_reports':{'order_record_id':{'diagnostic_orders'},'original_report_record_id':{'diagnostic_test_reports'}},
 'diagnostic_result_reviews':{'order_record_id':{'diagnostic_orders'},'received_report_record_id':{'received_diagnostic_reports'}},
 'diagnostic_review_interviews':{'current_basis_record_id':{'laboratory_result_notifications','condition_assessments'},
   'previous_basis_record_id':{'laboratory_result_notifications','condition_assessments'}},
}

def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),native.identity()])
def spec(stream):
    if stream in EXTRA:return EXTRA[stream]
    if stream not in native.STATEMENTS:return {}
    return native.SOURCE_DEFINITIONS.get(stream,{}).get('links',native.LINK_FIELDS)

def build(records,patient,asof,roots,maximum=200):
    if not isinstance(roots,list) or not roots or any(not isinstance(k,str) for k in roots) or len(set(roots))!=len(roots):raise ValueError('Distinct explicit reference roots required')
    if len(roots)>maximum:raise ValueError('Complete reference component exceeds the record bound; no partial graph supplied')
    by_id={r['record_id']:r for r in records}
    if len(by_id)!=len(records):raise ValueError('Duplicate received record IDs')
    if any(r.get('patient_id')!=patient for r in records):raise ValueError('Reference graph requires same-patient received records')
    if set(roots)-set(by_id) or any(not native.visible(by_id[k],asof) for k in roots):raise ValueError('Reference roots must be received by the decision date')
    edges=[];issues=[];neighbors={k:set() for k in by_id}
    for r in records:
        if not native.visible(r,asof):continue
        clean=context_builder.clean(r);declared=set(r.get('_warehouse_fields',[]));rules=spec(r['source_table'])
        fields=(set(rules)|{k for k in declared if k.endswith(('_record_id','_record_ids'))}) & declared & set(clean)
        for field in sorted(fields):
            raw=clean[field]
            if raw is None:continue
            refs=raw if isinstance(raw,list) else [raw]
            for position,key in enumerate(refs):
                allowed=rules.get(field);target=by_id.get(key) if isinstance(key,str) else None
                reason=('invalid_reference_value' if not isinstance(key,str) or not key else
                    'not_received_for_patient' if target is None else
                    'reference_type_unmapped' if not allowed else
                    'wrong_reference_type' if target['source_table'] not in allowed else
                    'not_received_by_cutoff' if not native.visible(target,asof) else
                    'self_reference' if key==r['record_id'] else None)
                link={'source_id':r['record_id'],'source_hash':digest(r),'source_table':r['source_table'],
                    'field':field,'position':position if isinstance(raw,list) else None,'target_id':key,
                    'expected_source_types':sorted(allowed or []),'target_source_type':target['source_table'] if target else None,
                    'target_hash':digest(target) if target else None,'target_available_date':target.get('available_date') if target else None,
                    'contract':'diagnostic_received_extension' if r['source_table'] in EXTRA else 'historical_native_contract',
                    'clinical_relationship_validated':False}
                link['id']=digest(link)
                if reason:issues.append({**link,'reason':reason});continue
                edges.append(link);neighbors[r['record_id']].add(key);neighbors[key].add(r['record_id'])
    selected=set(roots);pending=deque(sorted(roots))
    while pending:
        for key in sorted(neighbors[pending.popleft()]):
            if key not in selected:
                selected.add(key);pending.append(key)
                if len(selected)>maximum:raise ValueError('Complete reference component exceeds the 200-record bound; no partial graph supplied')
    return {'record_ids':sorted(selected),'edges':sorted((e for e in edges if e['source_id'] in selected and e['target_id'] in selected),key=lambda e:e['id']),
        'issues':sorted((e for e in issues if e['source_id'] in selected),key=lambda e:e['id']),
        'traversal':'Both directions of exact typed record references; not clinical or causal relationships',
        'unmapped_reference_policy':'Reported as unresolved and excluded from expansion; no fuzzy identifier or field-name-only link inference',
        'complete_chart':False,'selection_sufficiency':'unassessed'}
