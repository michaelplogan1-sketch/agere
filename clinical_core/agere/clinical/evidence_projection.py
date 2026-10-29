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
