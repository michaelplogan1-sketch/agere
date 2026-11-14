"""Coverage views with explicit dependencies on complete clinical evidence.

Coverage and clinical support are separate judgments. A source-finding cell can
be a reference in the global coverage view only when the complete, unchanged
cell is supplied to its required patient support packet. No clinical finding is
sampled, accepted, or summarized by this representation.
"""
import copy
import json
import math
import re
from collections import Counter
from ...contracts import digest

VERSION='coverage-dependencies/2'


def native_timing_projection(calls):
    """Normalize executor timing through verified, preceding assembly inputs.

    Keep every raw receipt. Only SQL's reserved elapsed_s is removed; assembly
    links are rebound to that input's normalized digest, never dropped. All
    other source, query, join, row and domain content stays in the hash.
    """
    views=Counter(c['result'].get('result_view') for c in calls
                  if isinstance(c.get('result'),dict) and isinstance(c['result'].get('result_view'),str))
    raw=[];prior=[];current=[];proofs=[];seen={};assemblies={}
    for i,call in enumerate(calls):
        observed=call.get('result')or{};raw.append(digest(observed));prior.append(raw[-1]);current.append(raw[-1])
        if not isinstance(observed,dict):continue
        view=observed.get('result_view');tool=call.get('name');elapsed=observed.get('elapsed_s')
        typed=(isinstance(view,str)and bool(view)and views[view]==1
               and isinstance(observed.get('rows'),list)and isinstance(observed.get('columns'),list))
        if(typed and tool=='query_sql'and isinstance(observed.get('executed_query'),str)
           and type(elapsed)in(int,float)and math.isfinite(elapsed)and elapsed>=0):
            current[i]=prior[i]=digest({k:v for k,v in observed.items()if k!='elapsed_s'})
            proofs.append({'index':i,'raw_digest':raw[i],'current_digest':current[i],'excluded_fields':['elapsed_s']})
        elif typed and tool=='compose_table':
            assembly=observed.get('assembly')
            if(isinstance(assembly,dict)and assembly.get('schema')=='result-assembly/1'
               and isinstance(assembly.get('joins'),list)):
                links=[(['assembly','base_result_digest'],assembly.get('base_result_view'),assembly.get('base_result_digest'))]
                valid=True
                for j,join in enumerate(assembly['joins']):
                    if not isinstance(join,dict):valid=False;break
                    links.append((['assembly','joins',j,'source_result_digest'],join.get('result_view'),join.get('source_result_digest')))
                rebindings=[]
                for path,source,expected in links:
                    index=seen.get(source)if isinstance(source,str)else None
                    if index is None or views[source]!=1 or expected!=raw[index]:valid=False;break
                    rebindings.append({'path':path,'index':index,'result_view':source,
                                       'raw_digest':raw[index],'current_digest':current[index]})
                if valid:
                    projected={**observed,'assembly':{**assembly,'joins':[dict(j)for j in assembly['joins']]}}
                    for edge in rebindings:
                        parent=projected
                        for key in edge['path'][:-1]:parent=parent[key]
                        parent[edge['path'][-1]]=edge['current_digest']
                    current[i]=digest(projected)
                    if current[i]!=raw[i]:
                        assemblies[i]=projected['assembly']
                        proofs.append({'index':i,'raw_digest':raw[i],
                            'current_digest':current[i],'assembly_inputs':rebindings})
        if isinstance(view,str)and view and views[view]==1:seen[view]=i
    assembled=any('assembly_inputs'in p for p in proofs)
    # v2's legacy view is the preceding SQL-only normalization. v1 retains its
    # original raw-SQL lookup so existing direct-query cache bindings still work.
    legacy=prior if assembled else raw
    bindings=[{**p,'legacy_digest':legacy[p['index']]}for p in proofs]
    if not assembled:bindings=[{k:v for k,v in p.items()if k!='raw_digest'}for p in bindings]
    return {'current':current,'legacy':legacy,'bindings':bindings,'assemblies':assemblies,
            'schema':'native-query-timing-cache/2'if assembled else'native-query-timing-cache/1'}


def native_timing_legacy_evidence(evidence,compatibility):
    """Validate the producer's exact old view before any cache-only lookup.

    Only proven native receipt digest projections may differ. Actual source text,
    rows, assertions, dates, scope, contracts and claims remain byte-for-byte
    values after lossless decoding. No semantic or approximate match is used.
    """
    from ..deliverables.receipts import uncompact_json
    if not isinstance(compatibility,dict)or compatibility.get('schema')not in ('native-query-timing-cache/1','native-query-timing-cache/2'):return None
    assembled=compatibility['schema']=='native-query-timing-cache/2'
    if len(evidence)!=1:return None
    def decode(text):
        value=json.loads(text)
        return uncompact_json(value)if isinstance(value,dict)and 'encoding'in value else value
    try:
        source=evidence[0];current=decode(source['text']);legacy=decode(compatibility['legacy_text'])
        if(not isinstance(current,dict)or not isinstance(legacy,dict)
           or digest(current)!=compatibility['current_fingerprint']or digest(legacy)!=compatibility['legacy_fingerprint']
           or current.get('id')!=source['id']or current.get('as_of')!=source['date']):return None
        a,b=current['native_bindings'],legacy['native_bindings']
        if not isinstance(a,list)or not isinstance(b,list)or len(a)!=len(b):return None
        pairs=compatibility['bindings']
        if not isinstance(pairs,list)or not pairs:return None
        seen={};restored=copy.deepcopy(legacy)
        views=Counter(v.get('result_view')for v in a if isinstance(v,dict)and isinstance(v.get('result_view'),str))
        for pair in pairs:
            i=pair['index']
            if(type(i)is not int or not 0<=i<len(a)or i in seen
               or a[i].get('result_digest')!=pair['current_digest']or b[i].get('result_digest')!=pair['legacy_digest']):return None
            if assembled:
                if seen and i<=max(seen):return None
                if not isinstance(pair.get('raw_digest'),str)or len(pair['raw_digest'])!=64:return None
            if 'assembly_inputs'in pair:
                if(not assembled or a[i].get('tool')!='compose_table'or b[i].get('tool')!='compose_table'
                   or pair['raw_digest']!=pair['legacy_digest']or 'excluded_fields'in pair):return None
                inputs=pair['assembly_inputs']
                if not isinstance(inputs,list)or not inputs:return None
                for j,edge in enumerate(inputs):
                    path=(['assembly','base_result_digest']if j==0 else ['assembly','joins',j-1,'source_result_digest'])
                    index=edge['index'];view=edge['result_view']
                    if(type(index)is not int or not 0<=index<i or edge['path']!=path
                       or not isinstance(view,str)or views[view]!=1 or a[index].get('result_view')!=view
                       or edge['current_digest']!=a[index].get('result_digest')
                       or edge['raw_digest']!=(seen[index]['raw_digest']if index in seen else b[index].get('result_digest'))):return None
            elif(pair.get('excluded_fields')!=['elapsed_s']or a[i].get('tool')!='query_sql'or b[i].get('tool')!='query_sql'
                 or assembled and pair['legacy_digest']!=pair['current_digest']):return None
            seen[i]=pair;restored['native_bindings'][i]['result_digest']=pair['current_digest']
        if restored!=current:return None
        return [{**source,'text':compatibility['legacy_text']}]
    except (ValueError,KeyError,TypeError,IndexError,AttributeError):return None


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


def _coverage_prose_view(answer):
    """Coverage reads claims and bindings; source support reads the source rows.

    Global prose can bind the same complete cohort to several statements. Those
    native rows belong to the support stage, not another copy in each coverage
    proof. Preserve scalar values, addresses and exact row digests here. Never
    modify the original proof consumed by support_packets.
    """
    for proof in answer.get('computed_prose') or []:
        for source in (proof.get('sources') or {}).values():
            if isinstance(source,dict) and 'source_row' in source:
                row=source.pop('source_row')
                source['source_row_reference']={
                    'binding':copy.deepcopy(source.get('binding')),
                    'row_digest':digest(row),
                    'required_stage':'source_support',
                    'source_support_established':False}
    return answer


def coverage_answer(answer, *, native_table=None, native_audit=None, native_interpretation=None):
    """Coverage-only projection; support receives every complete native value."""
    if native_interpretation is not None:
        if(native_interpretation.get('schema')!='source-interpreted-native-result/1'
           or native_interpretation.get('result_view')!=answer.get('table_from_tool')
           or native_interpretation.get('table_digest')!=digest(answer.get('table'))
           or native_interpretation.get('all_accounts_and_values_reproduced')is not True
           or native_interpretation.get('reproduction_without_provider_dispatch')is not True
           or native_interpretation.get('clinical_qualification')is not False
           or native_interpretation.get('human_review_created')is not False):
            raise ValueError('Source interpretation contract does not bind this complete native table')
        out=copy.deepcopy({k:v for k,v in answer.items()if k!='table'})
        out['table']=[];out['source_interpretation_contract']=copy.deepcopy(native_interpretation)
        if isinstance(out.get('table_binding'),dict):out['table_binding'].pop('query',None)
        prior=out.pop('investigation_review',None)
        if prior is not None:out['prior_review_native_digest']=digest(prior)
        out['coverage_contract']={'schema':'source-interpreted-native-result/1','scope':
            'Code reproduced every original account, source/task/tuple binding, existing machine judgment and conditional documentary transformation without a provider dispatch. Received proposals and source-supported machine interpretations are distinct fields. Only columns/field_roles and native_projection.fields are delivered fields; source_role_definitions describes ancestor data and does not supply omitted final columns. Review requested scope, dates, fields, unresolved reports, new prose and workflow meaning. This is neither independent clinical qualification nor proof of complete question coverage.'}
        return _coverage_prose_view(out)
    if native_audit is not None:
        if(native_audit.get('schema')!='native-qualifier-coverage/1'
            or native_audit.get('table_digest')!=digest(answer.get('table'))
            or native_audit.get('result_view')!=answer.get('table_from_tool')
            or native_audit.get('separate_source_support_required')is not True
            or native_audit.get('clinical_semantic_acceptance')is not False):
            raise ValueError('Native audit coverage proof does not bind this complete table')
        out=copy.deepcopy({k:v for k,v in answer.items()if k!='table'})
        out['table']=[];out['native_audit']=copy.deepcopy(native_audit)
        binding=out.get('table_binding')
        if isinstance(binding,dict):binding.pop('query',None)
        prior=out.pop('investigation_review',None)
        if prior is not None:out['prior_review_native_digest']=digest(prior)
        out['coverage_contract']={'schema':'native-qualifier-coverage/1',
            'scope':'Every native audit account/cell was checked by code against the complete original receipt set. A native_projection also preserves every account/patient identity, the exact received-field roles and an independently replayed output; its complete SQL and computed label meanings are supplied for review. Review the original request against the full parameter, field and conditional-effect contracts. These are received-label effects, not clinically validated conclusions. Do not treat faithful copying, a machine label, a computed literal or an unresolved requested interpretation as clinical acceptance or complete work. Separate source support remains mandatory.'}
        return _coverage_prose_view(out)
    if native_table is not None:
        # The producer has checked every primitive cell against a complete
        # native SQL result. Coverage reviews the requested fields and method,
        # not thousands of copies of those cells. No narrative/source judgment
        # is discharged by the copy receipt.
        out=copy.deepcopy({k:v for k,v in answer.items()if k!='table'})
        out['table']=[]
        out['native_table']=copy.deepcopy(native_table)
        binding=out.get('table_binding')
        if isinstance(binding,dict):binding.pop('query',None)
        prior=out.pop('investigation_review',None)
        if prior is not None:out['prior_review_native_digest']=digest(prior)
        out['coverage_contract']={'schema':'native-table-coverage/1',
            'scope':'Every declared native row is retained in the answer. Check requested outputs, source selection, calculation, dates, units and unknowns against the complete calculation contracts and field manifest. Exact copying is checked by code; it does not establish correct meaning or complete requested interpretation.'}
        return _coverage_prose_view(out)
    out=copy.deepcopy(answer);rows=answer.get('table')or[]
    view,edges=table_view(rows,defer_native_cells=True);out['table']=view
    # Coverage must read the actual native explanations and proposed actions.
    # A dependency marker proves that a cell exists, not that its text answers
    # the request. Keep structured source collections in their complete support
    # packets, but expose scalar text regardless of whether SQL or row_prose
    # produced it. This does not change the separate source-support projection.
    visible=set()
    for index,(actual,projected) in enumerate(zip(rows,view)):
        if not isinstance(actual,dict):continue
        for field,value in actual.items():
            if not isinstance(value,str):continue
            try:structured=isinstance(json.loads(value),(dict,list))
            except (ValueError,TypeError):structured=False
            if not structured:
                projected[field]=value
                visible.add((index,field))
    edges=[edge for edge in edges if tuple(edge['at']) not in visible]
    if 'row_prose' in out:
        # Coverage needs the actual proposed explanation, not the compiler input
        # duplicated alongside it. Source support still checks each full row.
        interpreted=set((answer.get('table_binding')or{}).get('interpreted_columns')or[])
        for actual,projected in zip(rows,view):
            for field in interpreted:
                if field in actual:projected[field]=copy.deepcopy(actual[field])
        edges=[edge for edge in edges if edge.get('field') not in interpreted]
        out.pop('row_prose',None)
        out['computed_prose']=[proof for proof in out.get('computed_prose',[]) if not proof.get('entity_key')]
    binding=out.get('table_binding')
    if isinstance(binding,dict)and isinstance(binding.get('query'),str):
        query=binding.pop('query')
        binding['native_executable_provenance']={'query_digest':digest(query),'characters':len(query),
            'scope':'Expanded SQL provenance retained natively; the declared executed queries remain in the plan.'}
    prior=out.pop('investigation_review',None)
    if prior is not None:out['prior_review_native_digest']=digest(prior)
    # The index and its edge already name the same source cell. Repeating the
    # complete edge inside every marker multiplies opaque integrity metadata.
    # Reindex after visible prose/row interpretation removed its dependencies.
    for index,edge in enumerate(edges):
        row,field=edge['at']
        view[row][field]={'clinical_evidence_dependency':index}
    out['coverage_contract']={'schema':VERSION,'dependency_edges':edges,
        'dependency_encoding':'Each integer clinical_evidence_dependency indexes dependency_edges in order. '
            'Its row and column locate the exact source cell in the corresponding patient support packet.',
        'scope':'Check requested scope, computation, fields and explicit unknowns. Source-finding cells are references to separate required full clinical-support packets. Do not infer clinical truth from a reference or a finding count. Clinical support is confirmed only if every dependency is supported.'}
    return _coverage_prose_view(out)


def native_observation_view(groups):
    """Share exact overlapping native rows without dropping an observation.

    SQL exploration often projects the same received row several times before
    adding its final relation fields. Keep each observation's own column shape
    and order, with one complete basis row for identical values. This conveys
    no source equivalence or clinical acceptance. All original native receipts
    remain outside this transport view.
    """
    shapes=[];shape_ids={};patients=[]
    for group in groups:
        if set(group)!={'patient_id','native_observations'}:
            raise ValueError('Exact patient-bound native observation group required')
        pid=group['patient_id'];rows=group['native_observations']
        if any(not isinstance(row,dict)or row.get('patient_id')!=pid for row in rows):
            raise ValueError('Native observation must retain its own patient')
        fields=[{k:digest(v)for k,v in row.items()}for row in rows]
        candidates=sorted(range(len(rows)),key=lambda i:(-len(rows[i]),i))
        bases=[];references=[]
        for i,row in enumerate(rows):
            source=next(j for j in candidates if fields[i].keys()<=fields[j].keys()
                        and all(fields[j][k]==v for k,v in fields[i].items()))
            if source not in bases:bases.append(source)
            shape=tuple(row)
            if shape not in shape_ids:shape_ids[shape]=len(shapes);shapes.append(list(shape))
            references.append([bases.index(source),shape_ids[shape]])
        patients.append({'patient_id':pid,'basis_rows':[rows[i]for i in bases],
                         'observation_sequence':references})
    view={'encoding':'native-observation-projections/1','column_shapes':shapes,'patients':patients,
          'original_fingerprint':digest(groups),
          'meaning':'For each observation_sequence [basis,shape], select exactly those column names from that patient basis row, in shape order. Every original observation and value is retained; shared values do not establish common source, independent corroboration or clinical truth.'}
    if restore_native_observations(view)!=groups:
        raise ValueError('Native observation projections did not restore every input')
    from ..deliverables.receipts import compact_json
    size=lambda value:len(json.dumps(compact_json(value),ensure_ascii=False,separators=(',',':')).encode())
    return view if size(view)<size(groups)else groups


def restore_native_observations(view):
    if isinstance(view,list):return copy.deepcopy(view)
    if not isinstance(view,dict)or view.get('encoding')!='native-observation-projections/1':
        raise ValueError('Unknown native observation encoding')
    shapes=view['column_shapes'];groups=[]
    for group in view['patients']:
        rows=[]
        for basis,shape in group['observation_sequence']:
            if type(basis)is not int or type(shape)is not int or basis<0 or shape<0:
                raise ValueError('Exact native row projection indices required')
            original=group['basis_rows'][basis];columns=shapes[shape]
            if len(set(columns))!=len(columns)or original.get('patient_id')!=group['patient_id']:
                raise ValueError('Native projection patient or shape changed')
            rows.append({key:copy.deepcopy(original[key])for key in columns})
        groups.append({'patient_id':group['patient_id'],'native_observations':rows})
    if digest(groups)!=view.get('original_fingerprint'):
        raise ValueError('Native observation values or order changed')
    return groups


def native_integrity_model_view(text,*,coverage_dependencies=False,support_dependencies=False):
    """Use short addresses for opaque integrity hashes in model metadata.

    Cryptographic validation belongs to code. Keep the complete original packet
    and an exact reversible map natively; bind the model view to that packet by
    one hash. Clinical fields, source text, SQL and native input values never
    change. This conveys no source support or clinical acceptance.
    """
    from ..deliverables.receipts import uncompact_json,compact_json,readable_json
    unchanged={'text':text,'bytes':len(text.encode()),'projected':False}
    try:encoded=json.loads(text)
    except ValueError:return unchanged
    if not isinstance(encoded,dict)or encoded.get('encoding')not in ('agere-readable-native-json/1','agere-lossless-compact-json/1'):
        return unchanged
    original=uncompact_json(encoded);state=copy.deepcopy(original)
    metadata={'native_bindings','shared_evidence_provenance','evidence_provenance',
        'native_calculation_contracts','executed_source_definitions','native_column_projections',
        'native_column_inputs','native_document_judgment_contracts','unbound_review_receipts'}
    # These containers carry executable or source values, even when a source
    # happens to name a clinical field "digest". Never rewrite their contents.
    literal={'input','parameters','assembly_specification','rows','indexed_rows','indexed_json_values','query','document','sources'}
    aliases={};values={};paths=[];proofs={};proof_paths=[];coverage_count=0
    def retain_proof(value,path):
        reference='native_proof_'+str(len(proofs));proofs[reference]=value;proof_paths.append(list(path))
        return {'native_receipt_reference':reference,
            'scope':'Complete mechanical validation certificate retained and checked by code. Original clinical/source values and executable selection contracts remain supplied; this reference establishes no clinical support.'}
    manifest_summary=None
    if support_dependencies and original.get('id')=='summary':
        # The manifest repeats addresses, never evidence values. Only the exact
        # code-generated enumeration of a complete nonpatient table qualifies.
        # Keep its global claim addresses visible and every table/source value
        # literal; the full manifest round-trips with the other native proofs.
        answer=original.get('answer');rows=original.get('all_final_rows')
        if (isinstance(answer,dict)and isinstance(rows,list)and rows
            and all(isinstance(row,dict)and 'patient_id'not in row for row in rows)):
            expected=[]
            if 'headline'in answer:expected.append({'id':'headline','field':'headline','path':['answer','headline']})
            facts=answer.get('facts')or{}
            if isinstance(facts,dict):
                for i,key in enumerate(facts):
                    expected.append({'id':'fact_'+str(i),'field':'facts.'+str(key),'path':['answer','facts',key]})
                global_claims=list(expected)
                for i,row in enumerate(rows):
                    for key in row:
                        expected.append({'id':'table_'+str(len(expected)),'field':'table.'+str(i)+'.'+str(key),
                            'path':['all_final_rows',i,key]})
                if original.get('claim_manifest')==expected:
                    state['claim_manifest']=retain_proof(expected,('claim_manifest',))
                    manifest_summary={'global_claims':global_claims,'table_rows':len(rows),
                        'table_cells':len(expected)-len(global_claims),
                        'meaning':'The complete manifest enumerates every global claim and every field of every all_final_rows row in order. All those values and all source evidence remain literal. Only repeated code-owned addresses are referenced; no claim is accepted or source-review obligation removed.'}
    dependency_groups=[]
    if coverage_dependencies:
        result=original.get('result')
        answer=result.get('answer')if isinstance(result,dict)else None
        contract=answer.get('coverage_contract')if isinstance(answer,dict)else None
        if isinstance(contract,dict)and contract.get('schema')==VERSION:
            dependency_groups.append((answer.get('table'),contract.get('dependency_edges'),
                ('result','answer','coverage_contract','dependency_edges'),True))
    if support_dependencies and original.get('id')=='summary'and 'required_clinical_dependencies'in original:
        dependency_groups.append((original.get('all_final_rows'),original.get('required_clinical_dependencies'),
            ('required_clinical_dependencies',),False))
    # These code-owned edges contain only coordinates, identities and hashes.
    # Every actual source cell still belongs to its required patient packet.
    for rows,edges,path,full_edge in dependency_groups:
        if not isinstance(edges,list)or not isinstance(rows,list):raise ValueError('Complete source dependencies required')
        positions={};ordinals={}
        for i,row in enumerate(rows):
            if not isinstance(row,dict):continue
            pid=row.get('patient_id');local=ordinals.get(pid,0);ordinals[pid]=local+1
            for field,value in row.items():
                if not isinstance(value,dict)or set(value)!={'clinical_evidence_dependency'}:continue
                index=value['clinical_evidence_dependency']
                if type(index)is not int or index in positions or not 0<=index<len(edges):
                    raise ValueError('Coverage dependency index changed')
                positions[index]=(i,field,pid,local)
        if len(positions)!=len(edges):raise ValueError('Coverage dependency is missing its cell')
        for index,edge in enumerate(edges):
            i,field,pid,local=positions[index]
            fields={'at','packet','answer_row','source_cell_digest'}|({'patient_id','field'}if full_edge else set())
            if (not isinstance(edge,dict)or set(edge)!=fields
                or not isinstance(pid,str)or edge['at']!=[i,field]
                or full_edge and (edge['patient_id']!=pid or edge['field']!=field)
                or edge['packet']!='patient_'+pid or type(edge['answer_row'])is not int or edge['answer_row']!=local
                or not isinstance(edge['source_cell_digest'],str)or not re.fullmatch('[0-9a-f]{64}',edge['source_cell_digest'])):
                raise ValueError('Coverage source-cell binding changed')
        if edges:
            parent=state
            for key in path[:-1]:parent=parent[key]
            parent[path[-1]]=retain_proof(edges,path);coverage_count+=len(edges)
    def detach(value,path):
        if isinstance(value,dict):
            return {k:retain_proof(v,path+(k,))if k in ('native_column_projection','derived_read_proof')else
                    detach(v,path+(k,))if k not in literal else v for k,v in value.items()}
        if isinstance(value,list):return[detach(v,path+(i,))for i,v in enumerate(value)]
        return value
    for field in sorted(metadata):
        if field in state:state[field]=detach(state[field],(field,))
    if 'native_bindings'in state:
        state['native_bindings']=retain_proof(original['native_bindings'],('native_bindings',))
    def address(value,path):
        if not isinstance(value,str)or not re.fullmatch('[0-9a-f]{64}',value):return value
        alias=aliases.setdefault(value,'@'+str(len(aliases)))
        values[alias]=value;paths.append(list(path));return alias
    def walk(value,path,hash_value=False):
        if isinstance(value,dict):
            out={}
            for key,v in value.items():
                key_address=path+('$key',key)
                replacement=address(key,key_address)if path==('executed_source_definitions',)else key
                out[replacement]=(v if key in literal else walk(v,path+(replacement,),
                    key.endswith(('_digest','_fingerprint'))or key in ('source_definition_keys','original_fingerprint')))
            return out
        if isinstance(value,list):return[walk(v,path+(i,),hash_value)for i,v in enumerate(value)]
        return address(value,path)if hash_value else value
    for field in sorted(metadata):
        if field in state:state[field]=walk(state[field],(field,))
    # Reconstruct each addressed scalar/key, then demand exact whole-packet
    # equality. References are path-qualified; marker-like source data is literal.
    restored=copy.deepcopy(state)
    for path in reversed(paths):
        if '$key'in path:
            parent=restored
            for key in path[:-2]:parent=parent[key]
            old=path[-1];alias=aliases[old];parent[old]=parent.pop(alias)
        else:
            parent=restored
            for key in path[:-1]:parent=parent[key]
            parent[path[-1]]=values[parent[path[-1]]]
    for path in reversed(proof_paths):
        parent=restored
        for key in path[:-1]:parent=parent[key]
        parent[path[-1]]=proofs[parent[path[-1]]['native_receipt_reference']]
    if digest(restored)!=digest(original):raise ValueError('Native integrity addresses did not restore the exact packet')
    if not paths and not proof_paths:return {'text':text,'bytes':len(text.encode()),'projected':False}
    state['native_integrity_projection']={'schema':'native-integrity-addresses/1','complete_native_packet_digest':digest(original),
        'meaning':'@N strings are opaque addresses for cryptographic metadata checked by code and retained in the complete native packet. Equal addresses denote equal original hashes. native_proof_N references bind complete mechanical certificates retained by code. Neither is clinical evidence, a measurement or model-verified support. All clinical values, source text, native inputs and executed SQL remain literal.'}
    if manifest_summary is not None:
        state['native_integrity_projection']['claim_manifest']=manifest_summary
    if coverage_count:
        state['native_integrity_projection']['coverage_dependencies']={
            'cells':coverage_count,'meaning':'Each integer clinical_evidence_dependency names the source cell at this exact output row/column. '
            'Its required support packet is patient_ + patient_id; answer_row is its row ordinal among that patient\'s output rows. '
            'The complete ordered coordinate/hash manifest is checked and retained by code under the native receipt reference. '
            'No source support, absence, clinical meaning or completed review is certified by this mapping.'}
    readable=json.dumps(readable_json(state),ensure_ascii=False,separators=(',',':'))
    packed=json.dumps(compact_json(state,expand_json_strings=True),ensure_ascii=False,separators=(',',':'))
    model_text=min((readable,packed),key=lambda x:len(x.encode()))
    if len(model_text.encode())>=len(text.encode()):return {'text':text,'bytes':len(text.encode()),'projected':False}
    return {'text':model_text,'bytes':len(model_text.encode()),'projected':True,
        'native_projection':{'schema':'native-integrity-addresses/1','complete_native_packet_digest':digest(original),
            'model_state_digest':digest(state),'values':values,'paths':paths,'proofs':proofs,
            'proof_paths':proof_paths,'roundtrip_exact':True}}


def source_packet_model_view(packet):
    """Project only model-facing metadata; the bound producer stays unchanged."""
    from ..deliverables.receipts import uncompact_json
    wire=native_integrity_model_view(packet['text'],support_dependencies=packet.get('id')=='summary')
    if not wire['projected']:return packet
    original=uncompact_json(json.loads(packet['text']))
    if original.get('claim_manifest')!=packet.get('claim_manifest'):return packet
    projection=wire['native_projection']
    return {**packet,'text':wire['text'],
        'claim_manifest':{'native_manifest_digest':digest(packet['claim_manifest']),
            'claims':len(packet['claim_manifest']),
            'meaning':'Complete code-owned addresses remain in the original producer packet. All claimed values remain in the model text; this reference grants no support.'},
        'native_model_projection':{k:projection[k]for k in
            ('schema','complete_native_packet_digest','model_state_digest','roundtrip_exact')},
        'original_packet_text_digest':digest(packet['text'])}


def native_json_paths(query, view, column):
    """Recognize complete static JSON branches read by table-valued expansion.

    Only an unmodified forwarded cell consumed by json_each qualifies. Hidden
    whole-document/parse-position columns, dynamic paths and other uses retain
    the original column. These paths establish input use, not source meaning.
    """
    import sqlglot
    from sqlglot import exp
    try:tree=sqlglot.parse_one(query,read='sqlite')
    except (sqlglot.errors.SqlglotError,ValueError,TypeError):return None
    if (not isinstance(tree,exp.Select)or tree.args.get('with_')
        or any(not isinstance(s.parent,exp.Count)for s in tree.find_all(exp.Star))):return None
    tables=[t for t in tree.find_all(exp.Table)if isinstance(t.this,exp.Identifier)and t.name==view]
    if len(tables)!=1:return None
    table=tables[0];inner=table.find_ancestor(exp.Select)
    source=inner.args.get('from_')if inner is not None else None
    if inner is tree and source is not None and source.this is table:
        # A table-valued expansion can consume a static branch directly from
        # every native row. Retain that COMPLETE branch at every original row
        # index; the caller reexecutes the full query before using it.
        refs=[c for c in tree.find_all(exp.Column)if c.name==column]
        if not refs:return None
        paths=[]
        for ref in refs:
            extract=ref.parent
            if(ref.table!=table.alias_or_name or not isinstance(extract,(exp.JSONExtract,exp.JSONExtractScalar))
                or extract.this is not ref):return None
            path=extract.expression.sql(dialect='sqlite')
            if len(path)<2 or path[0]!="'"or path[-1]!="'":return None
            path=path[1:-1]
            if not re.fullmatch(r'\$\.[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*',path):return None
            fn=extract.parent
            # Direct static scalar/object extraction also reads exactly this
            # branch. Every consuming query is replayed below, including its
            # grouping, filters and NULL behavior; no claim is certified here.
            if not isinstance(fn,exp.Anonymous)or fn.name.lower()!='json_each':
                paths.append(path)
                continue
            if(not isinstance(fn,exp.Anonymous)or fn.name.lower()!='json_each'
                or len(fn.expressions)!=1 or fn.expressions[0]is not extract):return None
            owner=fn.parent
            if not isinstance(owner,exp.Table)or not owner.alias:return None
            for col in tree.find_all(exp.Column):
                if col.find_ancestor(exp.Select)is not tree:continue
                if not col.table:return None
                if col.table==owner.alias and col.name not in ('key','value','type','atom','fullkey','path'):return None
            paths.append(path)
        return list(dict.fromkeys(paths))
    if (inner is None or inner is tree or len(inner.selects)!=1 or source is None or source.this is not table
        or any(v for k,v in inner.args.items()if k not in ('expressions','from_','limit','offset'))):return None
    selected=inner.selects[0];cell=selected.this if isinstance(selected,exp.Alias)else selected
    if not isinstance(cell,exp.Column)or cell.name!=column:return None
    if [c for c in tree.find_all(exp.Column)if c.name==column]!=[cell]:return None
    sub=inner.parent
    if not isinstance(sub,exp.Subquery)or not sub.alias:return None
    outer=sub.find_ancestor(exp.Select);forwarded=[c for c in tree.find_all(exp.Column)
        if c.table==sub.alias and c.name==selected.alias_or_name]
    if not forwarded:return None
    paths=[]
    for ref in forwarded:
        fn=ref.parent
        if (ref.find_ancestor(exp.Select)is not outer or not isinstance(fn,exp.Anonymous)
            or fn.name.lower()!='json_each' or len(fn.expressions)!=2 or fn.expressions[0]is not ref):return None
        path=fn.expressions[1]
        if not isinstance(path,exp.Literal)or not path.is_string or not re.fullmatch(r'\$\.[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*',path.this):return None
        owner=fn.parent
        if not isinstance(owner,exp.Table)or not owner.alias:return None
        for col in outer.find_all(exp.Column):
            if col.find_ancestor(exp.Select)is not outer:continue
            if not col.table:return None
            if col.table==owner.alias and col.name not in ('key','value','type','atom','fullkey','path'):return None
        paths.append(path.this)
    return list(dict.fromkeys(paths))


def selected_json_input(view,column,consumers,by):
    """Prove the exact JSON elements/fields used by flat native consumers.

    This is a candidate input representation, never a new execution result.
    Every consumer is replayed at full element scope with its statically read
    fields, then at witnessed element scope, against the complete original
    recorded output. Clauses, joins, NULLs and output order stay exact.
    Unrecognized JSON uses retain the original input.
    """
    import sqlite3
    import sqlglot
    from sqlglot import exp
    from .backstop_agent import output_table
    from .semantic_analyst import SemanticWarehouse
    patterns=[];paths=set();root_path=None;selected={};sources=set()
    def path_keys(node):
        text=node.sql(dialect='sqlite')
        if len(text)<2 or text[0]!="'"or text[-1]!="'":return None
        text=text[1:-1]
        return text[2:].split('.')if re.fullmatch(r'\$\.[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*',text)else None
    for consumer in consumers:
        query=consumer.get('query')
        if not isinstance(query,str):return None
        try:tree=sqlglot.parse_one(query,read='sqlite')
        except (sqlglot.errors.SqlglotError,ValueError,TypeError):return None
        if (not isinstance(tree,exp.Select)or any(v for k,v in tree.args.items()
                if k not in ('expressions','from_','joins','where','order'))
            or any(tree.find(t)for t in (exp.Star,exp.Subquery,exp.AggFunc,exp.Window))):return None
        physical=[t for t in tree.find_all(exp.Table)if isinstance(t.this,exp.Identifier)]
        own=[t for t in physical if t.name==view]
        if len(own)!=1 or any(t.name not in by or t.db not in ('','temp')for t in physical):return None
        sources.update(t.name for t in physical);table=own[0]
        refs=[c for c in tree.find_all(exp.Column)if c.table==table.alias_or_name and c.name==column]
        if len(refs)!=1:return None
        fn=refs[0].parent
        if (not isinstance(fn,exp.Anonymous)or fn.name.lower()!='json_each'
            or len(fn.expressions)!=2 or fn.expressions[0]is not refs[0]):return None
        keys=path_keys(fn.expressions[1]);owner=fn.parent
        if not keys or not isinstance(owner,exp.Table)or not owner.alias:return None
        if root_path is not None and keys!=root_path:return None
        root_path=keys
        outputs={x.alias_or_name for x in tree.selects}
        for ref in tree.find_all(exp.Column):
            if not ref.table:
                if not(ref.name in outputs and isinstance(ref.parent,exp.Ordered)):return None
            if ref.name.casefold()in ('rowid','_rowid_','oid'):return None
            if ref.table!=owner.alias:continue
            extract=ref.parent
            if(ref.name!='value'or not isinstance(extract,(exp.JSONExtract,exp.JSONExtractScalar))
                or extract.this is not ref):return None
            fields=path_keys(extract.expression)
            if not fields:return None
            paths.add(tuple(fields))
        patterns.append((consumer,tree,table.alias_or_name,owner.alias))
    if not patterns or not paths:return None
    columns,rows=output_table(by[view]['result'])
    if column not in columns:return None
    values=[];branches=[]
    for row in rows:
        value=row[columns.index(column)]
        if isinstance(value,str):
            try:value=json.loads(value)
            except ValueError:return None
        if not isinstance(value,dict):return None
        values.append(value);current=value;present=True
        for key in root_path:
            if not isinstance(current,dict)or key not in current:present=False;break
            current=current[key]
        if present and current is not None and not isinstance(current,list):return None
        if present and isinstance(current,list)and any(not isinstance(v,dict)for v in current):return None
        branches.append((present,current if present else None))
    def element_view(item):
        target={}
        for fields in sorted(paths):
            value=item
            for field in fields:
                if not isinstance(value,dict)or field not in value:break
                value=value[field]
            else:
                dst=target
                for field in fields[:-1]:dst=dst.setdefault(field,{})
                dst[fields[-1]]=copy.deepcopy(value)
        return target
    def branch_payload(present,branch):
        target={};dst=target
        if present:
            for key in root_path[:-1]:dst=dst.setdefault(key,{})
            dst[root_path[-1]]=branch
        return target
    # The parser admitted exactly one static expansion and only these static
    # element fields. Replay their FULL original element scope first. Loading
    # unused account histories into SQLite adds no proof or source meaning.
    field_inputs=[branch_payload(present,[element_view(item)for item in branch]
        if isinstance(branch,list)else branch)for present,branch in branches]
    quote=lambda name:'"'+name.replace('"','""')+'"'
    ordinal='__agere_projection_row'
    existing={c.name for _,tree,_,_ in patterns for c in tree.find_all(exp.Column)}|set(columns)
    while ordinal in existing:ordinal+='_'
    checks=[]
    try:
        with sqlite3.connect(':memory:')as db:
            for name in sorted(sources):
                cols,data=output_table(by[name]['result'])
                if not cols or len(set(cols))!=len(cols):return None
                if name==view:
                    data=[[*row[:cols.index(column)],field_inputs[i],*row[cols.index(column)+1:]]for i,row in enumerate(data)]
                db.execute('CREATE TEMP TABLE '+quote(name)+' ('+','.join(map(quote,cols))+')')
                # SQLite performs the same json_extract conversion as native
                # row materialization, without one enormous literal SQL string.
                insert='INSERT INTO '+quote(name)+' VALUES ('+','.join("json_extract(?1,'$["+str(i)+"]')"for i in range(len(cols)))+')'
                db.executemany(insert,((json.dumps(row,default=str,allow_nan=False),)for row in data))
                for col in cols:
                    if col=='id'or col.endswith('_id'):
                        db.execute('CREATE INDEX '+quote(name+'_projection_'+str(cols.index(col)))+' ON '+quote(name)+' ('+quote(col)+')')
            db.execute('ALTER TABLE '+quote(view)+' ADD COLUMN '+quote(ordinal)+' INTEGER')
            db.execute('UPDATE '+quote(view)+' SET '+quote(ordinal)+'=rowid-1')
            db.execute("SELECT * FROM json_each('[]')").fetchall();db.enable_load_extension(False)
            db.commit()
            db.execute('PRAGMA query_only=ON');db.set_authorizer(SemanticWarehouse._authorize)
            for consumer,tree,alias,element in patterns:
                expected_cols,expected=output_table(by[consumer['result_view']]['result'])
                cursor=db.execute(consumer['query']);actual=[list(r)for r in cursor]
                if [c[0]for c in cursor.description]!=expected_cols or digest(actual)!=digest(expected):return None
                witness=tree.copy();witness.set('expressions',list(witness.expressions)+[
                    exp.column(ordinal,table=alias),exp.column('key',table=element)])
                observed=db.execute(witness.sql(dialect='sqlite')).fetchall()
                if digest([list(r[:-2])for r in observed])!=digest(expected):return None
                for row in observed:
                    source,index=row[-2:]
                    if(type(source)is not int or not 0<=source<len(rows)or type(index)is not int
                        or not branches[source][0]or not isinstance(branches[source][1],list)
                        or not 0<=index<len(branches[source][1])):return None
                    selected.setdefault(source,set()).add(index)
                checks.append({'result_view':consumer['result_view'],'query_digest':digest(consumer['query']),
                    'witness_query':witness.sql(dialect='sqlite'),'output_columns':expected_cols,
                    'output_digest':digest(expected),'output_rows':len(expected),'complete_original_output_exact':True})
            projected=[];observations=[]
            for row_index,(present,branch)in enumerate(branches):
                narrowed=branch
                if present and isinstance(branch,list):
                    narrowed=[]
                    for index,item in enumerate(branch):
                        if index not in selected.get(row_index,set()):continue
                        narrowed.append(element_view(item))
                observations.append([present,json.dumps(narrowed,ensure_ascii=False,separators=(',',':'),allow_nan=False)if present else None])
                projected.append(branch_payload(present,narrowed))
            db.set_authorizer(None);db.execute('PRAGMA query_only=OFF')
            db.executemany('UPDATE '+quote(view)+' SET '+quote(column)+'=? WHERE '+quote(ordinal)+'=?',
                ((json.dumps(value,separators=(',',':')),i)for i,value in enumerate(projected)))
            db.commit()
            db.execute('PRAGMA query_only=ON');db.set_authorizer(SemanticWarehouse._authorize)
            for consumer,_,_,_ in patterns:
                cols,expected=output_table(by[consumer['result_view']]['result']);cursor=db.execute(consumer['query'])
                if [c[0]for c in cursor.description]!=cols or digest([list(r)for r in cursor])!=digest(expected):return None
    except (sqlite3.Error,ValueError,TypeError,KeyError,AttributeError):return None
    return {'schema':'native-replayed-json-selection/1','column':column,'path':'$.'+'.'.join(root_path),
        'value_encoding':'canonical-json','row_values':observations,'selected_row_indices':sorted(selected),
        'selected_element_indices':[[i,sorted(v)]for i,v in sorted(selected.items())],
        'element_field_paths':['$.'+'.'.join(p)for p in sorted(paths)],'checks':checks,
        'complete_receipt_digest':digest({k:v for k,v in by[view]['result'].items()if k!='result_view'}),
        'scope':'Complete original recorded SQL outputs, types, order, duplicates and NULLs reproduced at full element scope and selected element scope from every statically read field. Only witnessed elements/fields are shown; all original rows, unselected elements and source receipts remain native. An empty selection is not absence of data or a clinical finding. No source meaning is certified.'}


def aggregate_json_input(view, column, consumers, by):
    """Replay aggregates using every array element and only statically read fields.

    Array length needs cardinality, not the unused contents of each object.
    Element projections keep missing paths, explicit NULLs, order and duplicates.
    No predicate, aggregate or output is rewritten; any unfamiliar use fails closed.
    """
    import sqlite3
    import sqlglot
    from sqlglot import exp
    from .backstop_agent import output_table
    from .semantic_analyst import SemanticWarehouse
    columns,rows=output_table(by[view]['result'])
    if column not in columns:return None
    branches={}
    def keys(node):
        text=node.sql(dialect='sqlite')
        return tuple(text[3:-1].split('.')) if re.fullmatch(r"'\$\.[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*'",text) else None
    for consumer in consumers:
        try:tree=sqlglot.parse_one(consumer['query'],read='sqlite')
        except (sqlglot.errors.SqlglotError,KeyError,TypeError):return None
        if (not isinstance(tree,exp.Select)or tree.args.get('with_')or tree.find(exp.Subquery)
                or tree.find(exp.Window)or any(not isinstance(s.parent,exp.Count)for s in tree.find_all(exp.Star))):return None
        physical=[t for t in tree.find_all(exp.Table)if isinstance(t.this,exp.Identifier)]
        if len(physical)!=1 or physical[0].name!=view or physical[0].db not in ('','temp'):return None
        owner=physical[0].alias_or_name
        refs=[c for c in tree.find_all(exp.Column)if c.table==owner and c.name==column]
        if not refs:return None
        aliases={}
        for ref in refs:
            fn=ref.parent
            if(not isinstance(fn,exp.Anonymous)or fn.name.lower()not in ('json_each','json_array_length')
               or len(fn.expressions)!=2 or fn.expressions[0]is not ref):return None
            path=keys(fn.expressions[1])
            if not path:return None
            fields=branches.setdefault(path,set())
            if fn.name.lower()=='json_each':
                table=fn.parent
                if not isinstance(table,exp.Table)or not table.alias or table.alias in aliases:return None
                aliases[table.alias]=fields
        outputs={x.alias_or_name for x in tree.selects}
        for ref in tree.find_all(exp.Column):
            if ref.table==owner:continue
            if not ref.table:
                if ref.name in columns or ref.name not in outputs or not isinstance(ref.parent,(exp.Ordered,exp.Group)):return None
                continue
            if ref.table not in aliases:return None
            if ref.name=='key':continue  # every original array ordinal is retained
            extract=ref.parent
            if(ref.name!='value'or not isinstance(extract,(exp.JSONExtract,exp.JSONExtractScalar))
               or extract.this is not ref):return None
            path=keys(extract.expression)
            if not path:return None
            aliases[ref.table].add(path)
    if not branches:return None
    # Overlapping roots could overwrite a differently projected ancestor.
    if any(a!=b and b[:len(a)]==a for a in branches for b in branches):return None
    if any(a!=b and b[:len(a)]==a for fields in branches.values()for a in fields for b in fields):return None
    at=columns.index(column);projected=[];observations={p:[]for p in branches}
    def select(value,path):
        for key in path:
            if not isinstance(value,dict)or key not in value:return False,None
            value=value[key]
        return True,value
    def put(target,path,value):
        for key in path[:-1]:target=target.setdefault(key,{})
        target[path[-1]]=value
    for row in rows:
        value=row[at]
        if isinstance(value,str):
            try:value=json.loads(value)
            except ValueError:return None
        if not isinstance(value,dict):return None
        partial={}
        for path,fields in branches.items():
            present,branch=select(value,path)
            if present and branch is not None and not isinstance(branch,list):return None
            narrowed=branch
            if isinstance(branch,list):
                if any(not isinstance(item,dict)for item in branch):return None
                narrowed=[]
                for item in branch:
                    kept={}
                    for field in sorted(fields):
                        found,cell=select(item,field)
                        if found:put(kept,field,copy.deepcopy(cell))
                    narrowed.append(kept)
            observations[path].append([present,json.dumps(narrowed,ensure_ascii=False,separators=(',',':'),allow_nan=False)if present else None])
            if present:put(partial,path,narrowed)
        projected.append([*row[:at],partial,*row[at+1:]])
    quote=lambda name:'"'+name.replace('"','""')+'"'
    checks=[]
    try:
        with sqlite3.connect(':memory:')as db:
            db.execute('CREATE TEMP TABLE '+quote(view)+' ('+','.join(map(quote,columns))+')')
            insert='INSERT INTO '+quote(view)+' VALUES ('+','.join("json_extract(?1,'$["+str(i)+"]')"for i in range(len(columns)))+')'
            db.executemany(insert,((json.dumps(row,default=str,allow_nan=False),)for row in projected))
            db.execute("SELECT * FROM json_each('[]')").fetchall();db.enable_load_extension(False)
            db.commit()
            db.execute('PRAGMA query_only=ON');db.set_authorizer(SemanticWarehouse._authorize)
            for consumer in consumers:
                expected_columns,expected=output_table(by[consumer['result_view']]['result'])
                cursor=db.execute(consumer['query']);actual=[list(r)for r in cursor]
                if [c[0]for c in cursor.description]!=expected_columns or digest(actual)!=digest(expected):return None
                checks.append({'result_view':consumer['result_view'],'query_digest':digest(consumer['query']),
                    'columns':expected_columns,'row_count':len(actual),'output_digest':digest(actual),
                    'full_output_cells_types_order_duplicates_nulls_exact':True})
    except (sqlite3.Error,ValueError,TypeError,KeyError):return None
    return {'columns':{column},'contract':'native-replayed-json-array-fields/1','checks':checks,
        'observations':[{'column':column,'path':'$.'+'.'.join(path),'value_encoding':'canonical-json',
            'row_values':observations[path],'element_field_paths':['$.'+'.'.join(f)for f in sorted(fields)],
            'scope':'Every original row and array element remains in order, including duplicates. Only statically read element fields are shown; empty objects retain cardinality, not missing-source findings. Missing and JSON null are distinct. All original downstream SQL outputs reexecuted exactly; full original source receipts remain native. Source meaning still requires review.'}
            for path,fields in sorted(branches.items())]}


def native_json_projection(view, columns, rows, consumers, by):
    """Keep exact used JSON branches and reexecute every consuming program.

    Every original row/identity remains. The isolated read-only comparison uses
    native inputs only and requires exact full output cells, types and order.
    Complete original receipts remain the source; projected branches cannot
    establish clinical facts from fields that were not read.
    """
    import sqlite3
    from .backstop_agent import output_table
    from .semantic_analyst import SemanticWarehouse
    from .sql_query import native_result_source,native_query_bindings
    candidates={};checks=[]
    for column in columns:
        uses=[c for c in consumers if column in c['columns_read']]
        if not uses:continue
        selected=selected_json_input(view,column,uses,by)
        if selected is not None:
            return {'columns':{column},'observations':[{k:selected[k]for k in
                    ('column','path','value_encoding','row_values','scope')}],
                'checks':[{'result_view':c['result_view'],'query_digest':c['query_digest'],
                    'columns':c['output_columns'],'row_count':c['output_rows'],'output_digest':c['output_digest'],
                    'full_output_cells_types_order_duplicates_nulls_exact':True}for c in selected['checks']],
                'selection_proofs':[{k:v for k,v in selected.items()if k!='row_values'}],
                'contract':'native-replayed-json-selection/1'}
        aggregate=aggregate_json_input(view,column,uses,by)
        if aggregate is not None:return aggregate
        paths=[]
        for consumer in uses:
            read=native_json_paths(consumer['query'],view,column)
            if not read:break
            paths.extend(read)
        else:
            values=[]
            for row in rows:
                value=row[columns.index(column)]
                if isinstance(value,str):
                    try:value=json.loads(value)
                    except ValueError:break
                if value is not None and not isinstance(value,(dict,list)):break
                values.append(value)
            else:candidates[column]=(list(dict.fromkeys(paths)),values)
    if not candidates:return None
    projected=copy.deepcopy(rows);observations=[]
    for column,(paths,values)in candidates.items():
        partial=[{}for _ in rows]
        for path in paths:
            keys=path[2:].split('.');seen=[]
            for index,value in enumerate(values):
                current=value;present=True
                for key in keys:
                    if not isinstance(current,dict)or key not in current:present=False;break
                    current=current[key]
                # A branch is one exact JSON value, not another relational
                # table. Keep it as JSON text so the existing wire dictionary
                # shares equal branches without thousands of nested layouts.
                try:encoded=json.dumps(current,ensure_ascii=False,separators=(',',':'),allow_nan=False)if present else None
                except (ValueError,TypeError):return None
                if present and digest(json.loads(encoded))!=digest(current):return None
                seen.append([present,encoded])
                if present:
                    target=partial[index]
                    for key in keys[:-1]:target=target.setdefault(key,{})
                    target[keys[-1]]=copy.deepcopy(current)
            observations.append({'column':column,'path':path,'value_encoding':'canonical-json','row_values':seen,
                'scope':'Each [present,canonical_json] addresses the same original row index. Parse present JSON values to restore exact types and complete contents; missing and JSON null are distinct.'})
        for index,row in enumerate(projected):row[columns.index(column)]=partial[index]
    relevant=[c for c in consumers if set(c['columns_read'])&set(candidates)]
    try:
        with sqlite3.connect(':memory:')as db:
            registered=set()
            for consumer in relevant:
                tree,tables=native_query_bindings(consumer['query'],by)
                if tree is None or tables is None:return None
                import sqlglot
                physical=[t for t in tree.find_all(sqlglot.exp.Table)if isinstance(t.this,sqlglot.exp.Identifier)]
                if any(t.name not in by or t.db not in ('','temp')for t in physical):return None
                for table in tables:
                    name=table.name
                    if name in registered:continue
                    result=by[name]['result'];cols,source=output_table(result)
                    if name==view:cols,source=columns,projected
                    db.execute('CREATE TEMP TABLE "'+name.replace('"','""')+'" AS '+native_result_source(cols,source))
                    registered.add(name)
            db.execute("SELECT * FROM json_each('[]')").fetchall()
            db.enable_load_extension(False);db.execute('PRAGMA query_only=ON');db.set_authorizer(SemanticWarehouse._authorize)
            for consumer in relevant:
                cursor=db.execute(consumer['query']);cols=[c[0]for c in cursor.description];actual=[list(r)for r in cursor]
                expected_cols,expected=output_table(by[consumer['result_view']]['result'])
                if cols!=expected_cols or digest(actual)!=digest(expected):return None
                checks.append({'result_view':consumer['result_view'],'query_digest':consumer['query_digest'],
                    'columns':cols,'row_count':len(actual),'output_digest':digest(actual),
                    'full_output_cells_types_order_duplicates_nulls_exact':True})
    except (sqlite3.Error,ValueError,TypeError,KeyError):return None
    return {'columns':set(candidates),'observations':observations,'checks':checks}


def _native_column_consumers(view, columns, receipt_digest, by, dependencies, replays):
    """Use original SQLite read maps or separately replay-proved dependencies."""
    needed=set();consumers=[]
    for name in dependencies:
        if name==view:continue
        call=by.get(name)
        if not call:return None
        observed=call['result'];replay=replays.get(name)
        if replay and view in replay['sources']:
            source=replay['sources'][view];query=replay['query']
            if source['receipt_digest']!=receipt_digest:return None
            read=source['columns'];proof={'derived_read_proof':replay}
        else:
            if view not in (observed.get('read_views')or[]):
                assembly=observed.get('assembly')or{}
                linked=[assembly.get('base_result_view')]+[j.get('result_view')for j in assembly.get('joins',[])]
                if view in linked or re.search(r'(?<![A-Za-z0-9_])'+re.escape(view)+r'(?![A-Za-z0-9_])',
                        json.dumps(call.get('input')or{},default=str)):return None
                continue
            reads=observed.get('native_read_columns')or{}
            source=(reads.get('sources')or{}).get(view)or{}
            query=observed.get('declared_executed_query')or observed.get('executed_query')
            if (call.get('name')!='query_sql'or reads.get('schema')!='sqlite-native-column-reads/1'
                or not isinstance(query,str)or reads.get('query_digest')!=digest(query)
                or source.get('complete')is not True or source.get('receipt_digest')!=receipt_digest):return None
            read=source.get('columns');proof={}
        if not isinstance(read,list)or any(not isinstance(c,str)for c in read):return None
        extra=set(read)-set(columns)
        # SQLite records intrinsic ROWID reads separately from declared fields.
        # They qualify only after the complete JSON consumer is replayed below;
        # never discard them based on an assumed order or matching cell alone.
        intrinsic={'rowid','_rowid_','oid'}
        if extra and (not all(c.casefold()in intrinsic for c in extra)
                      or any(c.casefold()in intrinsic for c in columns)):return None
        needed.update(set(read)&set(columns))
        consumers.append({'result_view':name,'query':query,'query_digest':digest(query),
            'columns_read':read,'result_digest':digest(observed),
            **({'intrinsic_rowid_reads':sorted(extra)}if extra else{}),**proof})
    return (needed,consumers)if consumers else None


def assembly_column_dependencies(calls, dependencies, by):
    """Recover exact reads only after reproducing a complete native assembly.

    Collection joins read their key, domain and selected item column, not every
    diagnostic field in a reader receipt. Changed input hashes, specifications
    or output cells retain the conservative full-receipt path.
    """
    from .result_assembly import compose
    from .backstop_agent import output_table
    proofs={};prior={}
    for call in calls:
        result=call.get('result')or{}
        if not isinstance(result,dict):continue
        view=result.get('result_view');spec=call.get('input')or{}
        if view in dependencies and call.get('name')=='compose_table':
            assembly=result.get('assembly')or{}
            try:
                expected=compose(prior,**spec)
                if (assembly!=expected['assembly']or output_table(result)!=output_table(expected)
                    or result.get('truncated')is not False):raise ValueError('Assembly changed')
                base=spec['base'];columns=output_table(prior[base])[0]
                reads={base:set(spec.get('base_columns',columns))}
                reads[base].update(spec['keys'])
                for join in spec.get('joins')or[]:
                    name=join['result'];fields=set(join['on'].values())
                    reads[base].update(join['on'])
                    fields.update([join['items_from']]if join.get('items_from')else
                                  join.get('columns',output_table(prior[name])[0]))
                    if join.get('domain'):fields.add(join['domain']['column'])
                    reads.setdefault(name,set()).update(fields)
                proofs[view]={'schema':'replayed-assembly-column-reads/1','query':None,
                    'assembly_specification':copy.deepcopy(spec),'result_digest':digest(result),
                    'sources':{name:{'columns':[c for c in output_table(prior[name])[0]if c in fields],
                        'receipt_digest':digest({k:v for k,v in prior[name].items()if k!='result_view'})}
                        for name,fields in reads.items()},
                    'scope':'Exact original assembly, source hashes, output cells, types, order and multiplicity reproduced; no source meaning or clinical acceptance inferred.'}
            except (ValueError,TypeError,KeyError,AttributeError):pass
        if view and view in by:prior[view]=result
    return proofs


def replayed_column_dependencies(calls, roots, dependencies, by):
    """Recover legacy literal-query reads by replaying an output projection.

    This is a derived proof, never a replacement execution receipt. The existing
    SQL compiler removes unused SELECT outputs; every predicate, join, constant,
    date and scope survives. Full original inputs and every selected output cell
    must match. Modern/malformed read maps do not fall back to this legacy path.
    """
    import sqlite3
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.qualify import qualify
    from sqlglot.optimizer.pushdown_projections import pushdown_projections
    from .backstop_agent import output_table,NATIVE_SQL_CONTRACT
    from .result_assembly import incomplete_reason
    from .semantic_analyst import SemanticWarehouse
    from .sql_query import native_query_model_projection,native_result_source
    replays={}
    for call in reversed(calls):
        original=call.get('result')or{};view=original.get('result_view')
        if view in dependencies and call.get('name')=='read_patient_profiles':
            proof=_profile_population_dependency(call,by,dependencies)
            if proof:replays[view]=proof
            continue
        if (view not in dependencies or view in roots or call.get('name')!='query_sql'
            or original.get('native_sql_contract')!=NATIVE_SQL_CONTRACT or original.get('error')
            or incomplete_reason(original)or original.get('read_views')!=[]
            or 'native_read_columns'in original or original.get('declared_executed_query')):continue
        columns,expected=output_table(original)
        if not columns or len(set(columns))!=len(columns)or any(len(r)!=len(columns)for r in expected):continue
        consumers=_native_column_consumers(view,columns,digest({k:v for k,v in original.items()if k!='result_view'}),by,dependencies,replays)
        if not consumers:continue
        wanted=[c for c in columns if c in consumers[0]or c=='patient_id']
        if not wanted or len(wanted)==len(columns):continue
        try:
            model=native_query_model_projection(original.get('executed_query'),{k:v['result']for k,v in by.items()})
            if not model:continue
            inputs={b['result_view']for b in model['source_bindings']}
            if not inputs<=dependencies or any(incomplete_reason(by[n]['result'])or by[n]['result'].get('error')for n in inputs):continue
            schema={'temp':{n:{c:'UNKNOWN'for c in output_table(by[n]['result'])[0]}for n in inputs}}
            tree=sqlglot.parse_one(model['query'],read='sqlite')
            if (tree.find(exp.Placeholder)or any(isinstance(n,(exp.Like,exp.ILike,exp.RegexpLike))for n in tree.walk())
                or any(n.args.get('distinct')for n in tree.find_all(exp.Select))
                or any(n.args.get('distinct')is not False for n in tree.find_all(exp.Union))):continue
            physical=[t for t in tree.find_all(exp.Table)if isinstance(t.this,exp.Identifier)]
            if any(t.name not in inputs or t.db not in ('','temp')for t in physical):continue
            # The only synthetic column in the literal-to-table binding is an
            # unshadowed row ordinal used directly by ORDER BY, never a filter,
            # output value, join or arithmetic expression.
            ordinals={b['result_view']:b['native_order_column']for b in model['source_bindings']}
            for col in tree.find_all(exp.Column):
                if col.name.casefold()not in ('_rowid_','rowid','oid'):continue
                owner=col.find_ancestor(exp.Select);source=(owner.args.get('from_')or exp.From()).this
                if (not isinstance(source,exp.Table)or source.name not in inputs
                    or col.name!=ordinals[source.name]or not isinstance(col.parent,exp.Ordered)
                    or col.parent.parent is not owner.args.get('order')):raise ValueError('Non-ordering intrinsic column')
            tree=qualify(tree,dialect='sqlite',schema=schema,validate_qualify_columns=False,
                         allow_partial_qualification=True,quote_identifiers=False,identify=False)
            literals=lambda t:Counter(n.this for n in t.find_all(exp.Literal)if n.is_string)
            def clauses(t):
                result=[]
                for n in t.walk():
                    if isinstance(n,exp.Join):
                        # Compare the join identity/type/ON/USING separately
                        # from the SELECT output list inside its input.
                        node=n.copy();node.set('this',exp.table_('_input_'+n.this.alias_or_name))
                        result.append(node.sql(dialect='sqlite'))
                    elif isinstance(n,(exp.Where,exp.Group,exp.Having,exp.Order,exp.Limit,exp.Offset)):
                        result.append(n.sql(dialect='sqlite'))
                result.extend('TABLE:'+n.sql(dialect='sqlite')for n in t.find_all(exp.Table)if isinstance(n.this,exp.Identifier))
                return Counter(result)
            prior_literals=literals(tree);prior_clauses=clauses(tree)
            def select_outputs(node):
                if isinstance(node,exp.Union):select_outputs(node.this);select_outputs(node.expression)
                elif isinstance(node,exp.Select)and [s.alias_or_name for s in node.selects]==columns:
                    node.set('expressions',[s for s in node.selects if s.alias_or_name in wanted])
                else:raise ValueError('Unqualified output shape')
            select_outputs(tree);tree=pushdown_projections(tree)
            if literals(tree)!=prior_literals or clauses(tree)!=prior_clauses:continue
            # Repeated scope arrays remain COMPLETE ordinary SQL parameters.
            # They are never substituted by a broader source population.
            parameters={};names={}
            for literal in list(tree.find_all(exp.Literal)):
                if not literal.is_string:continue
                try:value=json.loads(literal.this)
                except ValueError:continue
                if not isinstance(value,list):continue
                name=names.setdefault(literal.this,'native_json_'+str(len(names)))
                parameters[name]=literal.this;literal.replace(exp.Placeholder(this=name))
            query=tree.sql(dialect='sqlite');reads={};intrinsic={}
            with sqlite3.connect(':memory:')as db:
                for name in inputs:
                    cols,rows=output_table(by[name]['result'])
                    db.execute('CREATE TEMP TABLE "'+name+'" AS '+native_result_source(cols,rows))
                db.execute("SELECT * FROM json_each('[]')").fetchall()
                def authorize(action,arg1,arg2,database,source):
                    verdict=SemanticWarehouse._authorize(action,arg1,arg2,database,source)
                    if verdict==sqlite3.SQLITE_OK and action==sqlite3.SQLITE_READ and database=='temp'and arg1 in inputs:
                        if arg2 in schema['temp'][arg1]:reads.setdefault(arg1,set()).add(arg2)
                        elif arg2.casefold()=='rowid'and ordinals[arg1].casefold()not in {c.casefold()for c in schema['temp'][arg1]}:
                            intrinsic.setdefault(arg1,set()).add(ordinals[arg1])
                        elif arg2:raise ValueError('Unknown source column')
                    return verdict
                db.enable_load_extension(False);db.execute('PRAGMA query_only=ON');db.set_authorizer(authorize)
                cursor=db.execute(query,parameters);actual_columns=[c[0]for c in cursor.description];actual=[list(r)for r in cursor]
            selected=[[r[columns.index(c)]for c in wanted]for r in expected]
            if actual_columns!=wanted or digest(actual)!=digest(selected):continue
            replays[view]={'schema':'replayed-native-output-columns/1','original_result_digest':digest(original),
                'original_query_digest':digest(original['executed_query']),'query':query,'parameters':parameters,
                'source_bindings':model['source_bindings'],'selected_columns':wanted,'row_count':len(actual),
                'selected_output_digest':digest(actual),'selected_cells_types_order_duplicates_nulls_exact':True,
                'predicates_joins_order_limits_and_original_literals_unchanged_before_binding':True,
                'intrinsic_order_columns':{n:sorted(v)for n,v in intrinsic.items()},
                'sources':{n:{'columns':sorted(reads.get(n,set())),
                    'receipt_digest':digest({k:v for k,v in by[n]['result'].items()if k!='result_view'})}for n in inputs},
                'scope':'Derived native read proof only. Original receipts are unchanged. Full scope parameters, joins, dates, predicates and row multiplicity remain; intrinsic ordinals establish order only. No source meaning or clinical eligibility is certified.'}
        except (sqlite3.Error,sqlglot.errors.SqlglotError,ValueError,TypeError,KeyError,AttributeError):continue
    return replays


def _profile_population_dependency(call, by, dependencies):
    """Prove a legacy profile lookup's complete DISTINCT patient-ID selection.

    Profile values remain source data. Only this exact received-ID lookup may
    expose a read dependency; arbitrary reader/analysis inputs stay conservative.
    """
    import sqlite3
    import sqlglot
    from sqlglot import exp
    from .backstop_agent import output_table
    from .result_assembly import incomplete_reason
    from .semantic_analyst import SemanticWarehouse
    from .sql_query import native_result_source
    original=call['result'];query=(call.get('input')or{}).get('patient_query_sql')
    if (original.get('kind')!='received_patient_profiles'or incomplete_reason(original)or original.get('error')
        or not (original.get('source')or{}).get('source_digest')or not original['source'].get('as_of')
        or not isinstance(query,str)or (call.get('input')or{}).get('patient_ids')is not None):return None
    try:
        tree=sqlglot.parse_one(query,read='sqlite')
        source=(tree.args.get('from_')or exp.From()).this
        if (not isinstance(tree,exp.Select)or len(tree.selects)!=1 or not isinstance(tree.selects[0],exp.Column)
            or tree.selects[0].name!='patient_id'or tree.selects[0].table or not tree.args.get('distinct')
            or not isinstance(source,exp.Table)or not isinstance(source.this,exp.Identifier)
            or source.name not in dependencies or source.db not in ('','temp')or source.alias
            or any(v for k,v in tree.args.items()if k not in ('expressions','from_','distinct'))):return None
        result=by[source.name]['result'];columns,rows=output_table(result)
        if incomplete_reason(result)or result.get('error')or columns.count('patient_id')!=1:return None
        ids=original.get('input_patient_ids')
        if not isinstance(ids,list)or any(not isinstance(p,str)or not p for p in ids)or len(set(ids))!=len(ids):return None
        with sqlite3.connect(':memory:')as db:
            db.execute('CREATE TEMP TABLE "'+source.name+'" AS '+native_result_source(columns,rows))
            db.enable_load_extension(False);db.execute('PRAGMA query_only=ON');db.set_authorizer(SemanticWarehouse._authorize)
            cursor=db.execute(query);actual=[list(r)for r in cursor]
            if [c[0]for c in cursor.description]!=['patient_id']or actual!=[[p]for p in ids]:return None
        return {'schema':'replayed-profile-population/1','original_result_digest':digest(original),
            'original_query_digest':digest(query),'query':query,'parameters':{},'selected_columns':['patient_id'],
            'row_count':len(actual),'selected_output_digest':digest(actual),
            'selected_cells_types_order_duplicates_nulls_exact':True,'intrinsic_order_columns':{},
            'sources':{source.name:{'columns':['patient_id'],
                'receipt_digest':digest({k:v for k,v in result.items()if k!='result_view'})}},
            'scope':'Complete native patient-ID lookup exactly reproduces the received profile input IDs and order. No profile, clinical or documentary value is inferred from identity.'}
    except (sqlite3.Error,sqlglot.errors.SqlglotError,ValueError,TypeError,KeyError,AttributeError):return None


def native_column_projections(calls, roots, dependencies):
    """Project intermediate machine outputs to SQLite's actual column reads.

    Complete producer receipts remain native evidence. This only discharges
    copying of unused columns for aggregate dependencies, never source meaning.
    Unknown consumers, incomplete results, direct answer bindings and document
    readers retain their complete context. Row order and multiplicity survive.
    """
    from .backstop_agent import output_table,NATIVE_SQL_CONTRACT
    from .result_assembly import incomplete_reason
    by={};duplicates=set()
    for call in calls:
        result=call.get('result')or{}
        view=result.get('result_view')if isinstance(result,dict)else None
        if view in by:duplicates.add(view)
        if view:by[view]=call
    if dependencies is None or duplicates:return {}
    replays=replayed_column_dependencies(calls,roots,dependencies,by)
    replays.update(assembly_column_dependencies(calls,dependencies,by))
    projections={}
    # A set's process-local order must not change an otherwise identical
    # source-review packet or its paid-request/cache identity after restart.
    for view in sorted(dependencies-set(roots)):
        producer=by.get(view)
        if not producer or producer.get('name')not in ('execute_analysis','query_sql','read_patient_profiles','review_documents'):continue
        result=producer['result']
        if result.get('error')or incomplete_reason(result):continue
        if producer['name']=='read_patient_profiles'and(result.get('kind')!='received_patient_profiles'
                or not (result.get('source')or{}).get('source_digest')or not result['source'].get('as_of')):continue
        if producer['name']=='query_sql'and(result.get('native_sql_contract')!=NATIVE_SQL_CONTRACT
                or not isinstance(result.get('executed_query'),str)or not result['executed_query'].strip()):continue
        if producer['name']=='query_sql':
            import sqlglot
            try:program=sqlglot.parse_one(result['executed_query'],read='sqlite')
            except (sqlglot.errors.SqlglotError,ValueError,TypeError):continue
            if any(isinstance(node,(sqlglot.exp.Like,sqlglot.exp.ILike,sqlglot.exp.RegexpLike))for node in program.walk()):continue
        columns,rows=output_table(result)
        if (not columns or len(set(columns))!=len(columns)
            or any(len(row)!=len(columns)for row in rows)):continue
        receipt_digest=digest({k:v for k,v in result.items()if k!='result_view'})
        used=_native_column_consumers(view,columns,receipt_digest,by,dependencies,replays)
        if not used:continue
        needed,consumers=used
        if producer['name']=='review_documents'and any(
                (c.get('derived_read_proof')or{}).get('schema')!='replayed-assembly-column-reads/1'for c in consumers):continue
        # Keep explicit entity identity even when only row cardinality was read.
        json_paths=native_json_projection(view,columns,rows,consumers,by)
        intrinsic_consumers={c['result_view']for c in consumers if c.get('intrinsic_rowid_reads')}
        if intrinsic_consumers and (not json_paths or not intrinsic_consumers<=
                {check['result_view']for check in json_paths['checks']}):continue
        json_columns=json_paths['columns']if json_paths else set()
        # A focused reader can have unresolved candidate spans outside the
        # collected finding column. Preserve its actual review status too;
        # projecting unused diagnostics must never imply a complete review.
        selected=[c for c in columns if (c in needed or c=='patient_id' or
            producer['name']=='review_documents'and c in ('review_status','documents_reviewed'))and c not in json_columns]
        if set(selected)==set(columns):continue
        projected=[[row[columns.index(c)]for c in selected]for row in rows]
        if any(isinstance(value,(dict,list))for row in projected for value in row):continue
        projections[view]={'schema':'native-column-projection/1','result_view':view,
            'producer':{'tool':producer['name'],'input':producer.get('input')},
            'complete_receipt_digest':receipt_digest,'complete_columns':columns,
            'columns':selected,'rows':projected,'row_count':len(rows),
            'projected_rows_digest':digest(projected),'consumers':consumers,
            **({'json_path_observations':json_paths['observations'],'json_path_output_checks':json_paths['checks'],
                'json_path_contract':json_paths.get('contract','native-static-json-paths/1'),
                **({'json_selection_proofs':json_paths['selection_proofs']}if json_paths.get('selection_proofs')else{})}if json_paths else{}),
            'scope':'Every native row, duplicate and NULL in the columns actually read by the executed SQL is retained in original order. JSON path observations, when present, retain every original row index and complete selected branch, with missing distinct from JSON null; complete downstream outputs were reexecuted exactly. Complete producer receipts remain native evidence. This proves dependency-copy fidelity only; omitted documentary fields provide no support for clinical interpretation. Counted machine statuses do not establish eligibility, current need, treatment or causal effects.'}
    return projections


def patient_selected_json_inputs(projection,patient_ids):
    """Put every witnessed JSON input in its own required patient packet.

    Unconsumed rows stay in native receipts under the full replay proof. They
    cannot establish absence, a negative financial state or clinical support.
    A selected input outside the final patients keeps the full global view.
    """
    if projection.get('json_path_contract')!='native-replayed-json-selection/1':return None
    columns=projection.get('columns');rows=projection.get('rows');branches=projection.get('json_path_observations')
    proofs=projection.get('json_selection_proofs');receipt=projection.get('complete_receipt_digest')
    if(not isinstance(columns,list)or columns.count('patient_id')!=1 or not isinstance(rows,list)
       or any(not isinstance(row,list)or len(row)!=len(columns)for row in rows)
       or projection.get('row_count')!=len(rows)or projection.get('projected_rows_digest')!=digest(rows)
       or not isinstance(branches,list)or not branches or not isinstance(proofs,list)
       or len(branches)!=len(proofs)):raise ValueError('Selected JSON input projection changed')
    pid_at=columns.index('patient_id');selected=set();decoded=[]
    for branch,proof in zip(branches,proofs):
        values=branch.get('row_values');indices=proof.get('selected_row_indices');elements=proof.get('selected_element_indices')
        if(proof.get('schema')!='native-replayed-json-selection/1'or proof.get('complete_receipt_digest')!=receipt
           or any(branch.get(k)!=proof.get(k)for k in ('column','path','value_encoding'))
           or not isinstance(values,list)or len(values)!=len(rows)or not isinstance(indices,list)
           or any(type(i)is not int or not 0<=i<len(rows)for i in indices)or indices!=sorted(set(indices))
           or not isinstance(elements,list)or [e[0]for e in elements]!=indices):raise ValueError('Selected JSON witness changed')
        lengths={}
        for item in elements:
            if(not isinstance(item,list)or len(item)!=2 or not isinstance(item[1],list)or not item[1]
               or any(type(i)is not int or i<0 for i in item[1])or item[1]!=sorted(set(item[1]))):
                raise ValueError('Selected JSON element coordinates changed')
            lengths[item[0]]=len(item[1])
        for i,value in enumerate(values):
            if(not isinstance(value,list)or len(value)!=2 or type(value[0])is not bool
               or not value[0]and value[1]is not None):raise ValueError('Selected JSON presence changed')
            try:actual=json.loads(value[1])if value[0]else None
            except (ValueError,TypeError):raise ValueError('Selected JSON value changed')from None
            if i in lengths:
                if not value[0]or not isinstance(actual,list)or len(actual)!=lengths[i]:raise ValueError('Selected JSON elements missing')
            elif actual not in (None,[]):raise ValueError('Unwitnessed JSON input cannot be excluded')
        selected.update(indices);decoded.append((branch,values))
    if any(not isinstance(row[pid_at],str)for row in rows):raise ValueError('Selected JSON input patient changed')
    if any(rows[i][pid_at]not in patient_ids for i in selected):return None
    groups={}
    for i in sorted(selected):groups.setdefault(rows[i][pid_at],[]).append(i)
    inputs={};edges=[]
    for pid,indices in groups.items():
        item={'result_view':projection['result_view'],'columns':columns,'indexed_rows':[[i,rows[i]]for i in indices],
            'indexed_json_values':[{'column':branch['column'],'path':branch['path'],'value_encoding':branch['value_encoding'],
                'indexed_values':[[i,values[i]]for i in indices]}for branch,values in decoded],
            'complete_receipt_digest':receipt,'native_projection_digest':digest(projection),
            'scope':'All exact witnessed source-field values for this patient, with original native row positions and presence/NULL distinctions. These are query inputs, not clinical findings; all source/meaning checks still apply.'}
        inputs[pid]=item;edges.append({'packet':'patient_'+pid,'native_inputs_digest':digest(item)})
    supplied={i for item in inputs.values()for i,_ in item['indexed_rows']}
    if supplied!=selected:raise ValueError('Selected JSON input schedule lost an original row')
    unselected=[[i,rows[i],[values[i]for _,values in decoded]]for i in range(len(rows))if i not in selected]
    summary={'schema':'patient-native-selected-json-dependencies/1','result_view':projection['result_view'],
        'projection_contract':{k:v for k,v in projection.items()if k not in ('rows','json_path_observations')},
        'required_patient_inputs':edges,'selected_input_rows':len(selected),
        'unused_native_input_rows':len(unselected),'unused_native_inputs_digest':digest(unselected),
        'complete_projection_digest':digest(projection),
        'scope':'Every witnessed source input is present in its required patient packet. All original input rows remain native. Unselected rows had no witnessed JSON element in any complete replayed consumer; they establish neither no accounts nor negative/absent clinical facts. This contract proves dependency transport only; all patient/source and global meaning checks must pass.'}
    return summary,inputs


def aggregate_json_calculation_view(projection, producer, calls):
    """Keep a replayed numeric aggregation native; expose its field semantics.

    Only SUM/COUNT with literal array paths and explicit grouping qualify.
    Textual/narrative aggregates, filters and arbitrary SQL stay unprojected.
    This proves calculation fidelity, never source interpretation or support.
    """
    import sqlglot
    from sqlglot import exp
    from .backstop_agent import output_table
    if projection.get('json_path_contract')!='native-replayed-json-array-fields/1':return None
    observations=projection.get('json_path_observations')or[]
    columns={o.get('column')for o in observations}
    if len(columns)!=1:return None
    column=next(iter(columns));view=projection['result_view'];consumers=projection['consumers']
    by={c['result']['result_view']:c for c in calls if isinstance(c.get('result'),dict)and c['result'].get('result_view')}
    uses=[c for c in consumers if column in c['columns_read']]
    regenerated=aggregate_json_input(view,column,uses,by)
    if (regenerated is None or regenerated['observations']!=observations
        or regenerated['checks']!=projection.get('json_path_output_checks')):return None
    numeric=set();categorical=set();outputs=[]
    for consumer in uses:
        try:tree=sqlglot.parse_one(consumer['query'],read='sqlite')
        except sqlglot.errors.SqlglotError:return None
        if any(tree.args.get(k)for k in ('where','having','qualify','limit','offset','distinct')):return None
        owner=next(t.alias_or_name for t in tree.find_all(exp.Table)if isinstance(t.this,exp.Identifier))
        arrays={}
        for table in tree.find_all(exp.Table):
            fn=table.this
            if isinstance(fn,exp.Anonymous)and fn.name.lower()=='json_each':
                arrays[table.alias]=fn.expressions[1].this
        groups=tree.args.get('group');group_sql=set()
        for g in groups.expressions if groups is not None else []:
            if isinstance(g,exp.Literal)and not g.is_string and g.this.isdigit():
                i=int(g.this)-1
                if not 0<=i<len(tree.selects):return None
                g=tree.selects[i];g=g.this if isinstance(g,exp.Alias)else g
            group_sql.add(g.sql(dialect='sqlite'))
        for expression in tree.selects:
            node=expression.this if isinstance(expression,exp.Alias)else expression
            if isinstance(node,exp.Count)and isinstance(node.this,exp.Star):continue
            if isinstance(node,exp.Sum):
                value=node.this
                if isinstance(value,exp.Anonymous)and value.name.lower()=='json_array_length':continue
                if not isinstance(value,(exp.JSONExtract,exp.JSONExtractScalar)):return None
                if not isinstance(value.this,exp.Column)or value.this.table not in arrays:return None
                path=value.expression.sql(dialect='sqlite')[1:-1]
                numeric.add((arrays[value.this.table],path));continue
            if node.sql(dialect='sqlite')not in group_sql:return None
            if isinstance(node,exp.Column)and node.table==owner and node.name!=column:continue
            if isinstance(node,(exp.JSONExtract,exp.JSONExtractScalar))and isinstance(node.this,exp.Column)and node.this.table in arrays:
                categorical.add((arrays[node.this.table],node.expression.sql(dialect='sqlite')[1:-1]));continue
            return None
        cols,rows=output_table(by[consumer['result_view']]['result'])
        outputs.append({'result_view':consumer['result_view'],'query':consumer['query'],'columns':cols,'rows':rows})
    if numeric&categorical:return None
    profiles=[]
    for branch in observations:
        fields=branch['element_field_paths'];seen={f:{'present':0,'null':0,'absent':0,'categories':{}}for f in fields}
        lengths=[]
        for present,raw in branch['row_values']:
            value=json.loads(raw)if present else None
            lengths.append({'present':present,'json_null':present and value is None,'elements':len(value)if isinstance(value,list)else None})
            for item in value or []:
                for field in fields:
                    current=item;found=True
                    for key in field[2:].split('.'):
                        if not isinstance(current,dict)or key not in current:found=False;break
                        current=current[key]
                    stats=seen[field]
                    if not found:stats['absent']+=1;continue
                    stats['present']+=1
                    if current is None:stats['null']+=1;continue
                    if (branch['path'],field)in numeric:
                        if type(current)not in (int,float)or type(current)is float and not math.isfinite(current):return None
                    elif (branch['path'],field)in categorical:
                        if not isinstance(current,(str,int,float,bool)):return None
                        key=digest(current);entry=stats['categories'].setdefault(key,{'value':current,'elements':0});entry['elements']+=1
                    else:return None
        profiles.append({'column':column,'path':branch['path'],'row_count':len(lengths),
            'present_arrays':sum(x['present']and not x['json_null']for x in lengths),
            'missing_arrays':sum(not x['present']for x in lengths),'null_arrays':sum(x['json_null']for x in lengths),
            'array_elements':sum(x['elements']or 0 for x in lengths),
            'fields':[{'path':field,'role':'numeric_aggregate_operand'if(branch['path'],field)in numeric else'grouping_key',
                **{k:v for k,v in stats.items()if k!='categories'},
                **({'complete_categories':list(stats['categories'].values())}if stats['categories']else{})}for field,stats in seen.items()],
            'complete_observations_digest':digest(branch['row_values'])})
    return {'schema':'native-json-aggregate-inputs/1','profiles':profiles,'complete_executed_outputs':outputs,
        'full_replayed_outputs_exact':True,'original_input_projection_digest':digest(projection),
        'scope':'Every original row and array element was checked by native code and every complete consuming SUM/COUNT query reproduced exactly. Individual numeric operands remain in the complete native receipt, not model context. Field paths, missing/NULL distinctions, all categorical values, full queries and every grouped output remain supplied. No cross-currency sum or clinical interpretation is inferred from these profiles; required source/domain/date/denominator/meaning checks remain.'}


def native_calculation_input_view(projection, producer, calls):
    """Show code-checked aggregate inputs without asking a model to recount IDs.

    This is an arithmetic inspection view, NOT a reversible row encoding or a
    documentary/clinical finding. Original complete rows and entity mappings
    remain in immutable native receipts. The caller must retain individual
    evidence for patient claims and all mandatory source/meaning reviews.
    """
    from .backstop_agent import output_table
    from .result_assembly import incomplete_reason
    result=producer.get('result')or{}
    if (producer.get('name')not in ('execute_analysis','query_sql','read_patient_profiles')
        or projection.get('schema')!='native-column-projection/1' or result.get('error')or incomplete_reason(result)
        or projection.get('result_view')!=result.get('result_view')
        or projection.get('producer')!={'tool':producer.get('name'),'input':producer.get('input')}
        or projection.get('complete_receipt_digest')!=digest({k:v for k,v in result.items()if k!='result_view'})):
        return projection
    columns,original=output_table(result);selected=projection.get('columns');rows=projection.get('rows')
    if (not columns or len(set(columns))!=len(columns)or any(len(r)!=len(columns)for r in original)
        or not isinstance(selected,list)or len(set(selected))!=len(selected)or 'patient_id'not in selected
        or not set(selected)<=set(columns)or not isinstance(rows,list)
        or projection.get('complete_columns')!=columns or projection.get('row_count')!=len(original)
        or digest(rows)!=digest([[r[columns.index(c)]for c in selected]for r in original])
        or projection.get('projected_rows_digest')!=digest(rows)):return projection
    identities=[r[selected.index('patient_id')]for r in rows]
    if any(not isinstance(pid,str)or not pid for pid in identities):return projection
    by={c['result']['result_view']:c for c in calls if isinstance(c.get('result'),dict)and c['result'].get('result_view')}
    if len(by)!=sum(isinstance(c.get('result'),dict)and bool(c['result'].get('result_view'))for c in calls):return projection
    consumers=projection.get('consumers')
    if not consumers:return projection
    for consumer in consumers:
        source=by.get(consumer['result_view'])
        if not source or consumer.get('result_digest')!=digest(source['result']):return projection
    # Do not replace varying JSON source observations by a marginal profile.
    # A row-invariant branch is kept in full, with exact all-row equality.
    aggregate=aggregate_json_calculation_view(projection,producer,calls)
    invariant=[]
    for branch in ([]if aggregate is not None else projection.get('json_path_observations',[])):
        values=branch.get('row_values');column=branch.get('column');path=branch.get('path')
        if (projection.get('json_path_contract')!='native-static-json-paths/1'
            or branch.get('value_encoding')!='canonical-json'or column not in columns
            or not isinstance(path,str)or not re.fullmatch(r'\$\.[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*',path)
            or not isinstance(values,list)or len(values)!=len(rows)or not values
            or len({digest(v)for v in values})!=1):return projection
        try:
            for row,observation in zip(original,values):
                if not isinstance(observation,list)or len(observation)!=2 or type(observation[0])is not bool:return projection
                current=row[columns.index(column)]
                if isinstance(current,str):current=json.loads(current)
                present=True
                for key in path[2:].split('.'):
                    if not isinstance(current,dict)or key not in current:present=False;break
                    current=current[key]
                if present!=observation[0]or (present and digest(json.loads(observation[1]))!=digest(current))or (not present and observation[1]is not None):return projection
        except (ValueError,TypeError):return projection
        invariant.append({**{k:v for k,v in branch.items()if k!='row_values'},
            'native_all_row_equality':{'row_count':len(values),'row_values_digest':digest(values),
                'present':values[0][0],'complete_value':values[0][1]},
            'scope':'This complete static branch is exactly equal at every original native row index, including presence versus JSON null. The native receipt retains every entity mapping; no clinical or documentary assertion is certified.'})
    fields=[c for c in selected if c!='patient_id'];groups={}
    for row,pid in zip(rows,identities):
        values=[row[selected.index(c)]for c in fields]
        if any(isinstance(v,(dict,list))or type(v)is float and not math.isfinite(v)for v in values):return projection
        key=digest(values);group=groups.setdefault(key,{'values':values,'rows':0,'patients':set()})
        group['rows']+=1;group['patients'].add(pid)
    profiles=[{'values':g['values'],'rows':g['rows'],'distinct_patient_ids':len(g['patients'])}for g in groups.values()]
    if sum(g['rows']for g in profiles)!=len(rows):return projection
    population=result.get('input_patient_ids')
    scope=({'rows':len(population),'distinct_patient_ids':len(set(population)),
            'ordered_patient_ids_digest':digest(population),'native_field':'input_patient_ids'}
           if isinstance(population,list)and all(isinstance(p,str)and p for p in population)else None)
    return {**{k:v for k,v in projection.items()if k not in ('rows','json_path_observations')},
        'schema':'native-calculation-input/1',
        'input_fidelity':{'native_projection_digest':digest(projection),'rows_digest':digest(rows),
            'row_count':len(rows),'distinct_patient_ids':len(set(identities)),
            'ordered_patient_ids_digest':digest(identities),'input_population':scope,
            'profile_columns':fields,'complete_joint_value_frequencies':profiles,
            'all_original_rows_and_entity_mappings_retained_natively':True,
            'row_copy_checked_against_complete_producer':True},
        **({'json_path_observations':invariant}if invariant else{}),
        **({'json_aggregate_input_fidelity':aggregate}if aggregate is not None else{}),
        'native_method':result.get('method'),
        'scope':'Code checked every selected native input cell against its complete producer and computed these joint value frequencies from ALL rows. Original rows, duplicate IDs, identity mappings, scope inputs and full receipts remain native and available by result_view/receipt digest. This is calculation/input fidelity only, not a reversible encoding or source/clinical acceptance. Profiles do not establish patient overlap: use the actual native SQL outputs and their complete queries for arithmetic, never sum profile patient counts across groups. Source selection, definitions, period, denominator, missingness, documentary meaning, clinical interpretation and all final claims still require mandatory review. No patient-level claim may be verified from this aggregate view.'}


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
    parent_digest=digest(state)
    def project(selected):
        # Only claimed answer roots are pruned. The source context is immutable
        # and can be shared while constructing a reversible wire view; copying
        # it for every trial partition creates no new evidence.
        part={k:copy.deepcopy(v)if k in allowed else v for k,v in state.items()}
        selected_ids={c['id']for c in selected}
        for claim in claims:
            if claim['id'] not in selected_ids:
                parent=at(part,claim['path'][:-1]);key=claim['path'][-1]
                if not isinstance(parent,dict):raise ValueError('Claim must name a native answer field')
                del parent[key]
        part['claim_manifest']=selected
        part['scope']='Check ONLY the listed claim_manifest fields in this partition. All original source inputs and native calculations remain complete context, not additional answer claims.'
        part['claim_partition']={'parent_packet_id':packet['id'],'parent_fingerprint':parent_digest,
            'source_inputs_digest':source_digest,'parent_claim_count':len(claims),
            'claim_ids':[c['id'] for c in selected],
            'closure':'All parent claims are scheduled exactly once across the partitions; source inputs are identical in every partition.'}
        assert {k:v for k,v in part.items() if k in protected}==protected
        assert all(at(part,c['path'])==originals[c['id']] for c in selected)
        readable=json.dumps(readable_json(part),ensure_ascii=False,separators=(',',':'))
        text=readable if len(readable.encode())<=max_bytes else json.dumps(compact_json(part),ensure_ascii=False,separators=(',',':'))
        if len(text.encode())>max_bytes:
            alternate=json.dumps(compact_json(part,expand_json_strings=True),ensure_ascii=False,separators=(',',':'))
            if len(alternate.encode())<len(text.encode()):text=alternate
        if uncompact_json(json.loads(text))!=part:raise ValueError('Claim partition did not round-trip')
        return {**packet,'text':text,'bytes':len(text.encode()),'claim_manifest':selected,
            'parent_packet_id':packet['id'],'representation':'complete_source_claim_partition'}
    # Check the complete set once, then bisect only oversized groups. Growing
    # every prefix repeatedly serialized the same complete source context.
    # Group boundaries may differ; order and exactly-once claim closure do not.
    groups=[];pending=[claims]
    while pending:
        selected=pending.pop();candidate=project(selected)
        if candidate['bytes']<=max_bytes:groups.append(candidate);continue
        if len(selected)==1:
            try:return source_dependency_packets(packet,max_bytes=max_bytes)
            except ValueError as exc:
                raise ValueError('A complete single-claim dependency still exceeds the provider envelope; '+str(exc))from exc
        middle=len(selected)//2
        pending.extend((selected[middle:],selected[:middle]))
    if [c['id'] for part in groups for c in part['claim_manifest']]!=ids:
        raise ValueError('Claim partition does not close the original manifest')
    return [{**part,'id':packet['id']+'__claims_'+str(i)} for i,part in enumerate(groups)]


def source_dependency_packets(packet, *, max_bytes=230000):
    """Move source-local assertions to required complete-source dependencies.

    The parent keeps every other native input. Every replaced object and source
    context is supplied intact to a child; exact reconstruction is checked before
    dispatch. Parent references establish no clinical fact. All parents and all
    children must pass the existing support aggregator.
    """
    from ..deliverables.receipts import compact_json,readable_json,uncompact_json,_JSON_STRING_FORMATS
    from .document_review import AXES,RELATION_VERSION
    state=uncompact_json(json.loads(packet['text']))
    pid=state.get('patient_id')
    if not pid or packet.get('source_context_complete')is False:
        raise ValueError('Source partition requires complete patient-bound contexts')
    contexts=state.get('source_contexts')or[]
    questions={digest(q):q for r in state.get('review_scope',[]) for q in (r.get('questions')or{}).values() if isinstance(q,dict)}
    originals={};kinds={};groups={}
    marker='required_source_dependency';literal='source_dependency_literal';json_string='source_dependency_json_string'

    def parsed_string(value):
        if isinstance(value,str)and value[:1]in ('[','{'):
            try:
                decoded=json.loads(value)
                if isinstance(decoded,(dict,list)):
                    for name,options in _JSON_STRING_FORMATS.items():
                        if json.dumps(decoded,allow_nan=False,**options)==value:return decoded,name
            except (ValueError,TypeError):pass
        return None

    def retain(value,kind,source_key):
        key=digest(value)
        if key in originals and originals[key]!=value:raise ValueError('Source dependency collision')
        originals[key]=value;kinds[key]=kind
        groups.setdefault(source_key,{'contexts':{},'observations':{}})[kind][key]=value
        return {marker:key,'kind':kind}

    def source_key(doc):
        return (doc.get('patient_id'),doc.get('record_id'),str(doc.get('date')or'')[:10],digest(doc.get('text')))

    def observation(value):
        a=value.get('assertion')if isinstance(value.get('assertion'),dict)else value
        if a.get('binding_version')!=RELATION_VERSION:return None
        source=a.get('source')or{};q=questions.get(a.get('question_digest'))
        if not q or a.get('patient_id')!=pid or source.get('patient_id')!=pid:
            raise ValueError('Source observation lacks its exact patient/question binding')
        if a.get('entity')!=q['entity']or a.get('relation')!=q['relation']or a.get('subject')!=q['subject']:
            raise ValueError('Source observation meaning differs from its question')
        if any(a.get(k)not in options for k,options in AXES.items()):raise ValueError('Invalid source assertion axes')
        if a.get('current_clinical_state_established')is not False or a.get('authority')!='machine_annotation':
            raise ValueError('Source-local dependency cannot establish current clinical state')
        raw={k:v for k,v in a.items()if k!='id'}
        if a.get('id')!=digest([RELATION_VERSION,raw]):raise ValueError('Source assertion identity changed')
        key=(pid,source.get('record_id'),source.get('event_date'),source.get('document_text_digest'))
        matches=[doc for doc in contexts if source_key(doc)==key]
        if not matches:raise ValueError('Source assertion lacks its complete matching context')
        evidence=a.get('evidence')or{};start,end=evidence.get('start'),evidence.get('end')
        if (type(start)is not int or type(end)is not int or not 0<=start<end<=len(matches[0]['text'])or
                matches[0]['text'][start:end]!=evidence.get('quote')or a.get('value')!=evidence.get('quote')):
            raise ValueError('Source assertion quote/offset changed')
        if a is not value and any(value.get(k)!=expected for k,expected in {
                'value':a['value'],'quote':evidence['quote'],'record_id':source['record_id'],
                'date':source['event_date'],'span_start':start,'span_end':end}.items()):
            raise ValueError('Source observation wrapper differs from its assertion')
        return retain(value,'observations',key)

    def project(value):
        encoded=parsed_string(value)
        if encoded:
            decoded,format_name=encoded
            return {json_string:{'format':format_name,'value':project(decoded)}}
        if isinstance(value,list):return[project(v)for v in value]
        if isinstance(value,dict):
            bound=observation(value)
            if bound is not None:return bound
            if set(value)in ({marker,'kind'},{literal},{json_string}):
                return {literal:[[k,project(v)]for k,v in value.items()]}
            return{k:project(v)for k,v in value.items()}
        return value

    parent={k:project(v)for k,v in state.items()if k!='source_contexts'}
    parent['source_contexts']=[]
    for doc in contexts:
        key=source_key(doc)
        if doc.get('patient_id')!=pid or not isinstance(doc.get('text'),str):raise ValueError('Invalid complete source context')
        parent['source_contexts'].append(retain(doc,'contexts',key))
    if not groups or any(not g['contexts']or not g['observations']for g in groups.values()):
        raise ValueError('Every deferred source requires complete context and explicit observations')

    def restore(value):
        if isinstance(value,list):return[restore(v)for v in value]
        if isinstance(value,dict):
            if set(value)=={json_string}:
                encoded=value[json_string]
                return json.dumps(restore(encoded['value']),allow_nan=False,**_JSON_STRING_FORMATS[encoded['format']])
            if set(value)=={marker,'kind'}:
                key=value[marker]
                if key not in originals or value['kind']!=kinds[key]:raise ValueError('Invalid source dependency reference')
                return copy.deepcopy(originals[key])
            if set(value)=={literal}:return{k:restore(v)for k,v in value[literal]}
            return{k:restore(v)for k,v in value.items()}
        return value
    if restore(parent)!=state:raise ValueError('Source dependencies did not restore every original native value')

    provenance={p['row_digest']:p.get('origins',[])for p in state.get('evidence_provenance',[])}
    native_reviews=[row for row in state.get('evidence',[])if isinstance(row,dict)and any(
        origin.get('tool')=='review_documents'for origin in provenance.get(digest(row),[]))]
    def decoded(value):
        parsed=parsed_string(value)
        if parsed:return decoded(parsed[0])
        if isinstance(value,list):return[decoded(v)for v in value]
        if isinstance(value,dict):return{k:decoded(v)for k,v in value.items()}
        return value
    def source_field(value):
        value=decoded(value)
        if not isinstance(value,list)or not value:return False
        def bound(item):
            if not isinstance(item,dict):return False
            a=item.get('assertion')if isinstance(item.get('assertion'),dict)else item
            return a.get('binding_version')==RELATION_VERSION
        if all(bound(item)for item in value):return True
        # A collected native review may contain operational scope metadata as
        # well as source objects. Only an exact projection of a recorded review
        # qualifies; arbitrary clinical prose containing a source does not.
        return all(isinstance(item,dict)and {'review_status','documents_reviewed','source_findings_json'}<=set(item)and any(
            set(item)<=set(row)and all(item[k]==row[k]for k in item)for row in native_reviews)for item in value)
    def at(value,path):
        for key in path:value=value[key]
        return value
    original_claims=state.get('claim_manifest')or[]
    deferred=[claim['id']for claim in original_claims if source_field(at(state,claim['path']))]
    parent['claim_manifest']=[claim for claim in original_claims if claim['id']not in deferred]
    field_uses={}
    def references(value):
        if isinstance(value,list):
            return set().union(*(references(v)for v in value))if value else set()
        if isinstance(value,dict):
            if set(value)=={marker,'kind'}:return{value[marker]}
            return set().union(*(references(v)for v in value.values()))if value else set()
        return set()
    for claim in original_claims:
        use={**claim,'native_value_digest':digest(at(state,claim['path']))}
        for key in references(at(parent,claim['path'])):
            if kinds[key]=='observations':field_uses.setdefault(key,[]).append(use)

    def encode(value):
        readable=json.dumps(readable_json(value),ensure_ascii=False,separators=(',',':'))
        if len(readable.encode())<=max_bytes:return readable
        alternatives=[readable,json.dumps(compact_json(value),ensure_ascii=False,separators=(',',':')),
                      json.dumps(compact_json(value,expand_json_strings=True),ensure_ascii=False,separators=(',',':'))]
        text=readable if len(readable.encode())<=max_bytes else min(alternatives,key=lambda x:len(x.encode()))
        if uncompact_json(json.loads(text))!=value:raise ValueError('Source dependency transport did not round-trip')
        return text

    parent['scope']=('Check ONLY the listed claim_manifest fields and their source kind, scope and bindings. Source-local documentary '
        'objects and complete contexts are required child dependencies, restored exactly by code. Their references alone '
        'establish no clinical fact or aggregate. Deferred documentary objects are checked in their source dependencies. '
        'Return unknown for any additional clinical interpretation requiring deferred content rather than the visible native evidence. '
        'source_dependency_json_string preserves a native JSON-string cell: its value is the complete parsed value; code restores its exact format.')
    parent['source_dependency_contract']={'parent_fingerprint':digest(state),'native_roundtrip_verified':True,
        'required_objects':len(originals),'complete_sources':len(contexts),
        'deferred_source_claim_ids':deferred,
        'acceptance':'This parent and every required complete-source child must pass; no clinical support is inherited from a reference.'}
    parent_text=encode(parent)
    if len(parent_text.encode())>max_bytes:raise ValueError('Non-source dependency still exceeds provider envelope')

    def child(selected,index,*,transport=True):
        docs={k:v for group in selected for k,v in group['contexts'].items()}
        observations={k:v for group in selected for k,v in group['observations'].items()}
        rows=[{'dependency_id':key,'observation':value,'final_field_uses':field_uses.get(key,[])}for key,value in observations.items()]
        claims=[{'id':'source_'+str(i),'field':(v.get('field')or (v.get('assertion')or v).get('concept')or 'documentary_observation'),
                 'path':['answer_rows',i,'observation']}for i,v in enumerate(observations.values())]
        value={'id':packet['id']+'__sources_'+str(index),'patient_id':pid,'question':state.get('question'),'as_of':state.get('as_of'),
            'scope':'Check every supplied source-local documentary observation against its complete matching source and exact bound question. Check entity, relation, subject, negation and time; a mention or matching quote alone is insufficient. For every final_field_uses entry, also check that the observation matches that final field meaning: a different drug, service, person or relation cannot establish it. A neutral source-observation collection is not itself a current-treatment claim. Current means current at the source statement, not snapshot-current care. These are machine source observations, not clinical certification. Do not infer chart-wide absence, eligibility or current treatment.',
            'source_contexts':list(docs.values()),'source_context_dependency_ids':list(docs),'source_questions':questions,
            'answer_rows':rows,'claim_manifest':claims,
            'source_dependency_contract':{'parent_packet_id':packet['id'],'parent_fingerprint':digest(state),
                'observation_dependency_ids':list(observations),'complete_source_contexts':True}}
        return value,encode(value)if transport else None
    children=[];current=[]
    for group in groups.values():
        value,_=child(current+[group],len(children),transport=False)
        if current and len(json.dumps(readable_json(value),ensure_ascii=False,separators=(',',':')).encode())>max_bytes:
            if current:
                previous,previous_text=child(current,len(children));children.append((previous,previous_text));current=[]
        current.append(group)
    if current:children.append(child(current,len(children)))
    supplied={}
    for value,text in children:
        if len(text.encode())>max_bytes:raise ValueError('One complete source dependency exceeds provider envelope')
        supplied.update(zip(value['source_context_dependency_ids'],value['source_contexts']))
        supplied.update((r['dependency_id'],r['observation'])for r in value['answer_rows'])
    if supplied!=originals:raise ValueError('Source dependency schedule did not retain every original object')
    required_ids=[value['id']for value,text in children]
    parent['source_dependency_contract']['required_packet_ids']=required_ids
    parent_text=encode(parent)
    if len(parent_text.encode())>max_bytes:raise ValueError('Source dependency manifest exceeds provider envelope')
    output=[{**packet,'text':parent_text,'bytes':len(parent_text.encode()),'representation':'source_dependency_parent',
             'claim_manifest':parent['claim_manifest'],
             'source_dependency_roundtrip_verified':True,'required_source_packet_ids':required_ids,
             'source_dependency_patient_id':pid,
             'source_dependency_fingerprint':digest(state),
             'source_dependency_mechanical_only':bool(original_claims)and len(deferred)==len(original_claims)}]
    output.extend({**packet,'id':value['id'],'text':text,'bytes':len(text.encode()),'claim_manifest':value['claim_manifest'],
                   'parent_packet_id':packet['id'],'representation':'complete_source_dependency',
                   'source_dependency_roundtrip_verified':True,'source_dependency_parent_id':packet['id'],
                   'source_dependency_patient_id':pid,
                   'source_claim_assertions':{claim['id']:(row['observation'].get('assertion')or row['observation'])
                       for claim,row in zip(value['claim_manifest'],value['answer_rows'])},
                   'source_dependency_fingerprint':digest(state)}for value,text in children)
    return output
