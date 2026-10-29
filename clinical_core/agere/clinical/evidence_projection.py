"""Coverage views with explicit dependencies on complete clinical evidence.

Coverage and clinical support are separate judgments. A source-finding cell can
be a reference in the global coverage view only when the complete, unchanged
cell is supplied to its required patient support packet. No clinical finding is
sampled, accepted, or summarized by this representation.
"""
import copy
import json
from ...contracts import digest

VERSION='coverage-dependencies/1'


def finding_collection(value):
    if isinstance(value,str):
        try:value=json.loads(value)
        except (ValueError,TypeError):return None
    if not isinstance(value,list)or not value:return None
    if not all(isinstance(v,dict)and isinstance(v.get('record_id'),str)
               and isinstance(v.get('quote'),str)and v['quote'] for v in value):return None
    return value


def table_view(table,*,defer_native_cells=False,index_dependencies=False):
    """Return the global table view and exact source-cell dependency edges."""
    rows=copy.deepcopy(table);edges=[];indices={}
    for index,row in enumerate(table):
        if not isinstance(row,dict)or not isinstance(row.get('patient_id'),str):continue
        pid=row['patient_id'];local=indices.get(pid,0);indices[pid]=local+1
        for field,value in row.items():
            if field=='patient_id':continue
            found=finding_collection(value)
            native=defer_native_cells and bool(value) and isinstance(value,(str,list,dict))
            if found is None and not native:continue
            edge={'at':[index,field],'packet':'patient_'+pid,'patient_id':pid,
                  'answer_row':local,'field':field,'source_cell_digest':digest(value)}
            if index_dependencies:
                # Identity is already explicit in at=[row,field], packet and
                # the patient_id of that row. Repeating it in every edge can
                # force otherwise readable native packets into compression.
                edge={k:v for k,v in edge.items()if k not in ('patient_id','field')}
            edges.append(edge)
            rows[index][field]={'clinical_evidence_dependency':len(edges)-1 if index_dependencies else {
                'packet':edge['packet'],'field':field,'answer_row':local,
                'source_cell_digest':edge['source_cell_digest'],
                **({'findings':len(found),'record_ids':list(dict.fromkeys(v['record_id']for v in found))} if found is not None else
                   {'kind':'complete_native_cell'})}}
    return rows,edges


def restore_table(rows,edges,patient_rows):
    """Verify dependency closure and restore exact native cell values."""
    restored=copy.deepcopy(rows);used=set()
    for dependency_index,edge in enumerate(edges):
        address=tuple(edge['at'])
        if address in used:raise ValueError('Duplicate evidence dependency address')
        used.add(address)
        index,field=address
        pid=restored[index].get('patient_id')
        if (not isinstance(pid,str)or pid!=edge.get('patient_id',pid)or
                edge['packet']!='patient_'+pid or field!=edge.get('field',field)):
            raise ValueError('Clinical dependency changed entity or field identity')
        marker=restored[index][field].get('clinical_evidence_dependency')
        if not ((type(marker)is int and marker==dependency_index)or
                isinstance(marker,dict)and all(k in edge and marker.get(k)==edge[k]for k in ('packet','answer_row','field','source_cell_digest'))):
            raise ValueError('Clinical dependency marker differs from its edge')
        value=patient_rows[edge['packet']][edge['answer_row']][field]
        if digest(value)!=edge['source_cell_digest']:raise ValueError('Clinical dependency differs from its native source cell')
        restored[index][field]=copy.deepcopy(value)
    return restored


def coverage_answer(answer):
    """Coverage-only projection; support receives every complete native value."""
    out=copy.deepcopy(answer);rows=answer.get('table')or[]
    view,edges=table_view(rows,defer_native_cells=True);out['table']=view
    binding=out.get('table_binding')
    if isinstance(binding,dict)and isinstance(binding.get('query'),str):
        query=binding.pop('query')
        binding['native_executable_provenance']={'query_digest':digest(query),'characters':len(query),
            'scope':'Expanded SQL provenance retained natively; the declared executed queries remain in the plan.'}
    prior=out.pop('investigation_review',None)
    if prior is not None:out['prior_review_native_digest']=digest(prior)
    out['coverage_contract']={'schema':VERSION,'dependency_edges':edges,
        'scope':'Check requested scope, computation, fields and explicit unknowns. Source-finding cells are references to separate required full clinical-support packets. Do not infer clinical truth from a reference or a finding count. Clinical support is confirmed only if every dependency is supported.'}
    return out


def partition_support_packet(packet, *, max_bytes=230000):
    """Partition claimed outputs, retaining every source input in every part.

    This is a transport schedule, not evidence selection. Other output fields
    remain in the native evidence; each declared claim is checked exactly once.
    A single claim that still cannot fit remains unresolved at the caller.
    """
    from ..deliverables.receipts import compact_json, readable_json, uncompact_json
    state=uncompact_json(json.loads(packet['text']))
    claims=state.get('claim_manifest') or []
    if not claims or claims!=packet.get('claim_manifest'):
        raise ValueError('Partition requires the exact complete claim manifest')
    ids=[c['id'] for c in claims];paths=[tuple(c['path']) for c in claims]
    if len(set(ids))!=len(ids) or len(set(paths))!=len(paths):
        raise ValueError('Partition claim identities and paths must be unique')
    allowed={'answer','answer_rows','all_final_rows'}
    if any(not p or p[0] not in allowed or any(p==q[:len(p)] for q in paths if p!=q) for p in paths):
        raise ValueError('Partition paths must address distinct answer fields')
    def at(value,path):
        for key in path:value=value[key]
        return value
    # Access every value before projection, so missing paths cannot disappear.
    originals={c['id']:at(state,c['path']) for c in claims}
    protected={k:v for k,v in state.items() if k not in allowed|{'claim_manifest','scope'}}
    source_digest=digest(protected)
    def project(selected):
        part=copy.deepcopy(state);selected_ids={c['id'] for c in selected}
        for claim in claims:
            if claim['id'] not in selected_ids:
                parent=at(part,claim['path'][:-1]);key=claim['path'][-1]
                if not isinstance(parent,dict):raise ValueError('Claim must name a native answer field')
                del parent[key]
        part['claim_manifest']=selected
        part['scope']='Check ONLY the listed claim_manifest fields in this partition. All original source inputs and native calculations remain complete context, not additional answer claims.'
        part['claim_partition']={'parent_packet_id':packet['id'],'parent_fingerprint':digest(state),
            'source_inputs_digest':source_digest,'parent_claim_count':len(claims),
            'claim_ids':[c['id'] for c in selected],
            'closure':'All parent claims are scheduled exactly once across the partitions; source inputs are identical in every partition.'}
        assert {k:v for k,v in part.items() if k in protected}==protected
        assert all(at(part,c['path'])==originals[c['id']] for c in selected)
        readable=json.dumps(readable_json(part),ensure_ascii=False,separators=(',',':'))
        text=readable if len(readable.encode())<=max_bytes else json.dumps(compact_json(part),ensure_ascii=False,separators=(',',':'))
        if uncompact_json(json.loads(text))!=part:raise ValueError('Claim partition did not round-trip')
        return {**packet,'text':text,'bytes':len(text.encode()),'claim_manifest':selected,
            'parent_packet_id':packet['id'],'representation':'complete_source_claim_partition'}
    groups=[];current=[]
    for claim in claims:
        candidate=project(current+[claim])
        if candidate['bytes']>max_bytes:
            if current:groups.append(project(current));current=[]
            candidate=project([claim])
            if candidate['bytes']>max_bytes:raise ValueError('A complete single-claim dependency still exceeds the provider envelope')
        current.append(claim)
    if current:groups.append(project(current))
    if [c['id'] for part in groups for c in part['claim_manifest']]!=ids:
        raise ValueError('Claim partition does not close the original manifest')
    return [{**part,'id':packet['id']+'__claims_'+str(i)} for i,part in enumerate(groups)]
