"""Coverage views with explicit dependencies on complete clinical evidence.

Coverage and clinical support are separate judgments. A source-finding cell can
be a reference in the global coverage view only when the complete, unchanged
cell is supplied to its required patient support packet. No clinical finding is
sampled, accepted, or summarized by this representation.
"""
import copy
import json
import math
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


def coverage_answer(answer):
    """Coverage-only projection; support receives every complete native value."""
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
    out['coverage_contract']={'schema':VERSION,'dependency_edges':edges,
        'scope':'Check requested scope, computation, fields and explicit unknowns. Source-finding cells are references to separate required full clinical-support packets. Do not infer clinical truth from a reference or a finding count. Clinical support is confirmed only if every dependency is supported.'}
    return out


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
        if len(text.encode())>max_bytes:
            alternate=json.dumps(compact_json(part,expand_json_strings=True),ensure_ascii=False,separators=(',',':'))
            if len(alternate.encode())<len(text.encode()):text=alternate
        if uncompact_json(json.loads(text))!=part:raise ValueError('Claim partition did not round-trip')
        return {**packet,'text':text,'bytes':len(text.encode()),'claim_manifest':selected,
            'parent_packet_id':packet['id'],'representation':'complete_source_claim_partition'}
    groups=[];current=[]
    for claim in claims:
        candidate=project(current+[claim])
        if candidate['bytes']>max_bytes:
            if current:groups.append(project(current));current=[]
            candidate=project([claim])
            if candidate['bytes']>max_bytes:
                try:return source_dependency_packets(packet,max_bytes=max_bytes)
                except ValueError as exc:
                    raise ValueError('A complete single-claim dependency still exceeds the provider envelope; '+str(exc))from exc
        current.append(claim)
    if current:groups.append(project(current))
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
