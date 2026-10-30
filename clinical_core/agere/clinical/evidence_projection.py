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

    def encode(value):
        readable=json.dumps(readable_json(value),ensure_ascii=False,separators=(',',':'))
        if len(readable.encode())<=max_bytes:return readable
        alternatives=[readable,json.dumps(compact_json(value),ensure_ascii=False,separators=(',',':')),
                      json.dumps(compact_json(value,expand_json_strings=True),ensure_ascii=False,separators=(',',':'))]
        text=readable if len(readable.encode())<=max_bytes else min(alternatives,key=lambda x:len(x.encode()))
        if uncompact_json(json.loads(text))!=value:raise ValueError('Source dependency transport did not round-trip')
        return text

    parent['scope']=('Check the visible native claims and their source kind, scope and bindings. Source-local documentary '
        'objects and complete contexts are required child dependencies, restored exactly by code. Their references alone '
        'establish no clinical fact or aggregate. Deferred documentary objects are checked in their source dependencies. '
        'Return unknown for any additional clinical interpretation requiring deferred content rather than the visible native evidence. '
        'source_dependency_json_string preserves a native JSON-string cell: its value is the complete parsed value; code restores its exact format.')
    parent['source_dependency_contract']={'parent_fingerprint':digest(state),'native_roundtrip_verified':True,
        'required_objects':len(originals),'complete_sources':len(contexts),
        'acceptance':'This parent and every required complete-source child must pass; no clinical support is inherited from a reference.'}
    parent_text=encode(parent)
    if len(parent_text.encode())>max_bytes:raise ValueError('Non-source dependency still exceeds provider envelope')

    def child(selected,index,*,transport=True):
        docs={k:v for group in selected for k,v in group['contexts'].items()}
        observations={k:v for group in selected for k,v in group['observations'].items()}
        rows=[{'dependency_id':key,'observation':value}for key,value in observations.items()]
        claims=[{'id':'source_'+str(i),'field':(v.get('field')or (v.get('assertion')or v).get('concept')or 'documentary_observation'),
                 'path':['answer_rows',i,'observation']}for i,v in enumerate(observations.values())]
        value={'id':packet['id']+'__sources_'+str(index),'patient_id':pid,'question':state.get('question'),'as_of':state.get('as_of'),
            'scope':'Check every supplied source-local documentary observation against its complete matching source and exact bound question. Check entity, relation, subject, negation and time; a mention or matching quote alone is insufficient. Current means current at the source statement, not snapshot-current care. These are machine source observations, not clinical certification. Do not infer chart-wide absence, eligibility or current treatment.',
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
             'source_dependency_roundtrip_verified':True,'required_source_packet_ids':required_ids,
             'source_dependency_fingerprint':digest(state)}]
    output.extend({**packet,'id':value['id'],'text':text,'bytes':len(text.encode()),'claim_manifest':value['claim_manifest'],
                   'parent_packet_id':packet['id'],'representation':'complete_source_dependency',
                   'source_dependency_roundtrip_verified':True,'source_dependency_parent_id':packet['id'],
                   'source_dependency_fingerprint':digest(state)}for value,text in children)
    return output
