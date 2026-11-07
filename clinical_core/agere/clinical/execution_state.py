"""Deterministic investigator state: native results remain complete and queryable.

This changes the actor's prompt representation, not its evidence or scope. No
model-generated summary, source truncation or learned clinical assertion is used.
"""
import json
from ...contracts import digest

VERSION = 'execution-state/23'
OPERATIONAL_FEEDBACK_AUTHORITIES = frozenset({'unverified_review_feedback'})


class BindingPreservationError(ValueError):
    """A binding-only patch changed the original claim instead of its address."""

    def __init__(self,key,field,expected,rendered):
        self.detail={'code':'binding_meaning_changed','key':key,'field':field,
                     'expected':expected,'rendered':rendered}
        super().__init__('A binding repair must preserve the original sentence, quantities, units and dates. '
            +json.dumps(self.detail)+'. Query the required native scalar evidence, then bind it in the unchanged sentence. '
            'A binding-only operation cannot change source meaning.')

    def failure_identity(self):
        # A new temporary SQL alias does not make the same failed field progress.
        return {k:self.detail[k]for k in ('code','key','field')}


class BindingReferenceError(ValueError):
    """Locate a rejected native binding without discarding the original draft."""

    def __init__(self,key,field,reason):
        self.detail={'code':'binding_reference_invalid','key':key,'field':field,'reason':str(reason)}
        super().__init__(json.dumps(self.detail))


class UnchangedProseRepair(ValueError):
    """A validated patch repeats existing fields and adds no native basis."""


def operational_feedback_result(result):
    """Recognize only complete producer declarations, never row content/names."""
    dependencies = result.get('incomplete_dependencies') if isinstance(result, dict) else None
    return (isinstance(dependencies, list) and bool(dependencies) and all(
        isinstance(item, dict) and item.get('authority') in OPERATIONAL_FEEDBACK_AUTHORITIES
        for item in dependencies))


def review_limitation_view(text, feedback, reference):
    """Project only a bound prior review inside otherwise unchanged prose."""
    if not isinstance(text, str) or not isinstance(feedback, dict) or not isinstance(reference, dict):
        return text
    original = feedback.get('feedback')
    if (reference.get('feedback_digest') != digest(feedback) or not reference.get('result_view') or
            not isinstance(original, str) or not original or original not in text):
        return text
    guidance = review_guidance(original)
    if guidance == original:
        return text
    return {'prose': text.replace(original, guidance), 'original_text_digest': digest(text),
            'authority': 'unverified_review_feedback', 'native_review_reference': reference,
            'meaning': 'Only embedded JSON in the exact prior machine review is referenced. All limitation prose survives; this is not clinical source evidence.'}


def progress_evidence(calls,candidate=None,*,first_call=0):
    """Native dependency identity, independent of temporary names and retries.

    Final tables and bound prose name their actual dependency views. Unused
    follow-up queries are retained in receipts but cannot reset a failed review.
    Unbound scalar callers retain the complete native evidence set. A tool
    batch may select its own calls while resolving references against all
    preceding receipts; revisiting a changed observation is still progress.
    """
    from .harness_improvement import semantic_result
    import re
    pattern=re.compile(r'\bagere_result_\d+\b')
    def refs(value):
        if isinstance(value,str):return set(pattern.findall(value))
        if isinstance(value,dict):return set().union(*(refs(v)for v in value.values()))if value else set()
        if isinstance(value,list):return set().union(*(refs(v)for v in value))if value else set()
        return set()
    by_view={(c.get('result')or{}).get('result_view'):c for c in calls if isinstance(c.get('result'),dict)}
    wanted=refs({k:(candidate or{}).get(k)for k in('table_from_tool','computed_prose','row_prose','headline','facts','value')})if isinstance(candidate,dict)else set()
    selected=set();todo=list(wanted)
    while todo:
        name=todo.pop()
        if name in selected:continue
        selected.add(name);call=by_view.get(name)
        if call:todo.extend(refs(call.get('input'))-selected)
    names={};observed=[]
    def stable(value,key=None):
        if isinstance(value,dict):
            table=isinstance(value.get('rows'),list)and isinstance(value.get('columns'),list)
            # Reserved transport names inside business cells are source data.
            return{k:(v if table and k in('rows','columns')else stable(v,k))for k,v in value.items()
                   if k not in('result_view','actor_callback_id','owned_run_recovery','request_hash','attempt_id')}
        if isinstance(value,list):return[stable(v)for v in value]
        if isinstance(value,str)and key in('sql','query','executed_query','base','result','candidate_result_view','table_from_tool'):
            return pattern.sub(lambda m:names.get(m.group(),m.group()),value)
        return value
    for index,call in enumerate(calls):
        value=stable(semantic_result({k:call.get(k)for k in('name','input','result')}))
        ident=digest(value);out=call.get('result')or{}
        name=out.get('result_view')if isinstance(out,dict)else None
        if name:names[name]='native_'+ident
        if index>=first_call and (not wanted or name in selected):observed.append(ident)
    return sorted(set(observed))


def draft_state(candidate):
    """Reference an unverified draft without copying its native patient table."""
    state={'last_draft':{k:v for k,v in candidate.items()if k not in (
        'table','sources','evidence_text','system_one_judgments','table_binding','investigation_review','verification','limitations')},
        'last_draft_digest':digest(candidate)}if isinstance(candidate,dict)else{'last_draft':candidate,'last_draft_digest':digest(candidate)}
    if not isinstance(candidate,dict):return state
    # Present unchanged compiled fields in their editable native form. This is
    # only prompt state: the compiler must resolve every binding again.
    draft=state['last_draft'];draft.pop('computed_prose',None)
    for proof in candidate.get('computed_prose')or[]:
        if not isinstance(proof,dict)or not all(k in proof for k in ('field','template','bindings','rendered')):continue
        if not proof['bindings']:continue  # parameter-only prose remains ordinary text
        field=proof['field'];target=draft;key=field
        if field.startswith('facts.'):
            target=draft.get('facts');key=field[len('facts.'):]
            if not isinstance(target,dict):continue
            # Never mutate the saved candidate through its nested facts object.
            target=draft['facts']=dict(target)
        elif field!='headline':continue
        if target.get(key)==proof['rendered']:
            target[key]={'template':proof['template'],'bindings':proof['bindings']}
    state['last_draft_prose_instruction']='Return unchanged quantitative fields as these template/bindings objects verbatim. Any edited sentence needs a new template; every binding is rechecked against current executed cells.'
    binding=candidate.get('table_binding')
    if isinstance(binding,dict):
        state['last_draft_table_binding']={k:v for k,v in binding.items()if k!='query'}
        if'query'in binding:state['last_draft_table_binding']['native_query_digest']=digest(binding['query'])
    for key in ('verification','limitations'):
        if key in candidate:state['last_draft'][key]=candidate[key]
    if isinstance(candidate.get('table'),list):
        state['last_draft_rows']=len(candidate['table'])
        state['last_draft_table_instruction']='The last draft table is preserved natively. Reconstruct it only from the matching executed result_view; do not infer omitted values.'
    if isinstance(candidate.get('row_prose'),dict):
        state['localized_prose_repair']={
            'draft_digest':state['last_draft_digest'],
            'instruction':'To change only existing patient/entity prose, return {"repair_draft":{"draft_digest":"the exact digest here","rows":[{"key":{original entity keys},"fields":{existing prose field:replacement}}]}}. '
                'Send only changed fields. An edit may also include basis_additions:[{"result_view":"an executed view","row":zero_based_row}]. '
                'Each additional native row must have the same entity keys; code checks it before merging and the complete compiler checks it again. '
                'Existing bases, other rows and sources are retained. No native value or entity key can be changed. '
                'For a located unbound-literal repair preserve the original sentence, quantities, units and dates using native bindings; '
                'do not remove details to make compilation pass. For other structural changes return the complete answer contract.'}
    return state


def apply_prose_repair(proposal,candidate,*,calls=(),feedback=None,binding_rejections=None):
    """Validate an exact-draft edit; confer no evidence authority.

    By default the patch is atomic. The compiler loop may retain individually
    valid binding edits, leaving rejected sentences unchanged. Structural and
    source-meaning repairs remain atomic; every merged draft needs full review.
    """
    import copy
    if not isinstance(proposal,dict) or set(proposal)!={'repair_draft'}:
        raise ValueError('A prose repair must contain only repair_draft')
    repair=proposal['repair_draft']
    answer_targets=answer_prose_repair_targets(candidate,feedback)
    if answer_targets:
        source_meaning=feedback['prose_repair'].get('intent')=='source_meaning_review'
        if (not isinstance(repair,dict)or set(repair)!={'draft_digest','fields'}
                or repair['draft_digest']!=digest(candidate)or not isinstance(repair['fields'],list)or not repair['fields']):
            raise ValueError('This exact draft requires only located headline/facts binding edits in repair_draft.fields')
        native={c['result']['result_view']:c['result']for c in calls if isinstance(c.get('result'),dict)
            and c['result'].get('result_view')and not c['result'].get('error')}
        allowed={tuple(t['path'])for t in answer_targets};seen=set()
        merged=copy.deepcopy(candidate);merged.update(copy.deepcopy(draft_state(candidate)['last_draft']))
        for edit in repair['fields']:
            if not isinstance(edit,dict)or set(edit)!={'path','bind_literals'}or not isinstance(edit['path'],list):
                if not source_meaning or not isinstance(edit,dict)or set(edit)!={'path','value'}or not isinstance(edit['path'],list):
                    raise ValueError('Answer edits require the exact located path and its permitted binding or source-meaning value')
            if any(not isinstance(k,str)for k in edit['path']):raise ValueError('Answer repair path must contain literal keys')
            path=tuple(edit['path'])
            if path not in allowed or path in seen:raise ValueError('Answer repair may edit each producer-located path once')
            seen.add(path);parent=merged if len(path)==1 else merged['facts'];field=path[-1]
            if source_meaning:
                value=edit.get('value')
                if not isinstance(value,(str,dict))or not value:
                    raise ValueError('Source-meaning edits require nonempty text or a computed template')
                parent[field]=copy.deepcopy(value)
            else:parent[field]=bind_prose_literals(parent[field],{'bind_literals':edit['bind_literals']},native)
        if source_meaning and all(
            repair_rendered_value((merged if len(t['path'])==1 else merged['facts'])[t['path'][-1]],native)==
            repair_rendered_value((candidate if len(t['path'])==1 else candidate['facts'])[t['path'][-1]],native)
            for t in answer_targets):
            raise UnchangedProseRepair('No source-supported answer prose changed')
        for name in ('computed_prose','investigation_review','verification'):merged.pop(name,None)
        return merged
    if not isinstance(repair,dict) or set(repair)!={'draft_digest','rows'}:
        raise ValueError('repair_draft requires draft_digest and rows')
    if not isinstance(candidate,dict) or repair['draft_digest']!=digest(candidate):
        raise ValueError('Prose repair does not match the current draft; use its current digest or return a complete answer')
    spec=candidate.get('row_prose')
    if not isinstance(spec,dict) or set(spec)!={'keys','rows'} or not isinstance(spec['rows'],list):
        raise ValueError('Current draft has no keyed prose to repair')
    keys=spec['keys']
    if not isinstance(keys,list) or not keys or any(not isinstance(k,str)for k in keys) or len(set(keys))!=len(keys):
        raise ValueError('Current draft has invalid prose keys; return a complete answer')
    def identity(key):
        if (not isinstance(key,dict) or set(key)!=set(keys)
                or any(type(v)not in(str,int,float,bool)for v in key.values())):
            raise ValueError('Prose repair must use the exact existing entity keys')
        return digest(key)
    existing={}
    for row in spec['rows']:
        if not isinstance(row,dict) or not isinstance(row.get('fields'),dict):
            raise ValueError('Current draft has invalid prose rows; return a complete answer')
        ident=identity(row.get('key'))
        if ident in existing:raise ValueError('Current draft has duplicate entity keys; return a complete answer')
        existing[ident]=row
    edits=repair['rows']
    if not isinstance(edits,list) or not edits:raise ValueError('Prose repair needs changed fields')
    from .result_assembly import _rows
    native={c['result']['result_view']:c['result']for c in calls if isinstance(c.get('result'),dict)
            and c['result'].get('result_view')and not c['result'].get('error')}
    targets=prose_repair_targets(candidate,feedback)
    located={(digest(t['key']),t['field']):t for t in targets or[]}
    retain_valid=(isinstance(binding_rejections,list) and isinstance(feedback,dict)
        and feedback.get('repair_kind')=='deterministic_compiler' and targets
        and feedback.get('prose_repair',{}).get('intent')!='source_meaning_review'
        and all(t['code']in ('unbound_literal','missing_scalar_source')for t in targets))
    rejected=[]
    changes={};additions={}
    for edit in edits:
        if not isinstance(edit,dict) or not {'key','fields'}<=set(edit) or set(edit)-{'key','fields','basis_additions'}:
            raise ValueError('Each prose edit requires key and fields, with optional checked basis_additions; native values cannot be patched')
        ident=identity(edit['key']);fields=edit['fields']
        if ident not in existing:raise ValueError('Prose repair cannot add or rename an entity')
        if ident in changes:raise ValueError('Prose repair repeats an entity')
        if not isinstance(fields,dict) or not fields or not set(fields)<=set(existing[ident]['fields']):
            raise ValueError('Prose repair may replace only existing prose fields')
        if any(not isinstance(v,(str,dict))for v in fields.values()):
            raise ValueError('Prose fields require text or a computed template; deletion is not supported')
        extra=edit.get('basis_additions',[])
        if not isinstance(extra,list):raise ValueError('basis_additions must be native row addresses')
        addresses={(a['result_view'],a['row'])for a in existing[ident].get('basis',[])}
        for address in extra:
            if (not isinstance(address,dict)or set(address)!={'result_view','row'}or type(address['row'])is not int
                    or address['row']<0 or not isinstance(address['result_view'],str)or address['result_view']not in native):
                raise ValueError('Additional prose basis must address an executed native row')
            result=native[address['result_view']]
            from .result_assembly import incomplete_reason
            if incomplete_reason(result):raise ValueError('Additional prose basis has incomplete source dependencies')
            _,rows=_rows(result)
            if address['row']>=len(rows)or identity({k:rows[address['row']].get(k)for k in keys})!=ident:
                raise ValueError('Additional prose basis belongs to another entity or is unavailable')
            pair=(address['result_view'],address['row'])
            if pair in addresses:raise ValueError('Additional prose basis duplicates a retained address')
            addresses.add(pair)
        accepted={}
        for field,value in fields.items():
            target=located.get((ident,field))
            if targets and target is None:
                raise ValueError('A focused repair may edit only the producer-located fields')
            derived=[]
            if isinstance(value,dict)and 'bind_literals'in value:
                if (not target or target['code']not in ('unbound_literal','missing_scalar_source')
                        or feedback.get('prose_repair',{}).get('intent')=='source_meaning_review'):
                    raise ValueError('Literal binding edits require a located binding-only compiler failure')
                try:
                    value=bind_prose_literals(existing[ident]['fields'][field],value,native,
                        key=edit['key'],resolved_bases=derived,basis=existing[ident].get('basis'))
                except ValueError as exc:
                    error=BindingReferenceError(edit['key'],field,exc)
                    if not retain_valid:raise error from exc
                    rejected.append(error)
                    continue
            if target and target['code']in ('unbound_literal','missing_scalar_source'):
                original=existing[ident]['fields'][field]
                rendered=repair_rendered_value(value,native,basis=[*existing[ident].get('basis',[]),*extra])
                expected=repair_rendered_value(original,native,basis=existing[ident].get('basis'))
                if canonical_repair_prose(rendered)!=canonical_repair_prose(expected):
                    error=BindingPreservationError(edit['key'],field,expected,rendered)
                    if not retain_valid:raise error
                    rejected.append(error)
                    continue
            accepted[field]=value
            # Compact edits already resolve complete same-entity scalar rows.
            # Derive their bases once; do not ask the actor to repeat addresses.
            for address in derived:
                pair=(address['result_view'],address['row'])
                if pair not in addresses:
                    addresses.add(pair);extra=[*extra,address]
        changes[ident]={k:v for k,v in accepted.items()if existing[ident]['fields'][k]!=v}
        additions[ident]=extra if accepted else []
    if rejected and not any(changes.values()):raise rejected[0]
    if not any(changes.values())and not any(additions.values()):
        raise UnchangedProseRepair('Prose repair contains no changed fields; inspect the unresolved dependency')
    merged=copy.deepcopy(candidate)
    # Restore the existing numeric templates, not their rendered numeric text.
    # Sources and all complete rows stay in the native candidate, not the patch.
    editable=draft_state(candidate)['last_draft']
    merged.update(copy.deepcopy(editable))
    for name in ('computed_prose','investigation_review','verification'):
        merged.pop(name,None)
    for row in merged['row_prose']['rows']:
        ident=identity(row['key'])
        if ident in changes:
            row['fields'].update(copy.deepcopy(changes[ident]));row['basis'].extend(copy.deepcopy(additions[ident]))
    if rejected:binding_rejections.extend(copy.deepcopy(e.detail)for e in rejected)
    return merged


def resolve_prose_binding(address,native,key):
    """Resolve exact entity/source keys to the existing native scalar contract."""
    from .result_assembly import _rows,incomplete_reason
    if (not isinstance(address,dict)or set(address)not in ({'result_view','row','column'},{'result_view','where','column'})
            or not isinstance(address['result_view'],str)or address['result_view']not in native
            or not isinstance(address['column'],str)):
        raise ValueError('Binding requires an executed result_view, column and either where or row')
    result=native[address['result_view']]
    if incomplete_reason(result):raise ValueError('Binding has incomplete source dependencies')
    columns,rows=_rows(result)
    if address['column']not in columns:raise ValueError('Binding column is unavailable: '+address['column'])
    if 'where'in address:
        if not isinstance(key,dict)or not key:raise ValueError('Key-selected binding requires the original entity key')
        filters=address['where']
        if not isinstance(filters,list):raise ValueError('Binding where must be exact column/value filters')
        selected={}
        for item in filters:
            if (not isinstance(item,dict)or set(item)!={'column','value'}or not isinstance(item['column'],str)
                    or item['column']not in columns or item['column']in selected
                    or item['value']is not None and type(item['value'])not in(str,int,float,bool)):
                raise ValueError('Binding filters require unique native columns and scalar values')
            if item['column']in key and digest(item['value'])!=digest(key[item['column']]):
                raise ValueError('Binding filter cannot change the original entity key')
            selected[item['column']]=item['value']
        selected.update(key)
        matches=[i for i,row in enumerate(rows)if all(k in row and digest(row[k])==digest(v)for k,v in selected.items())]
        if len(matches)!=1:
            raise ValueError('Binding selection requires exactly one same-entity row: '+json.dumps({
                'result_view':address['result_view'],'column':address['column'],'where':filters,'matches':len(matches)}))
        index=matches[0]
    else:
        index=address['row']
        if type(index)is not int or not 0<=index<len(rows):raise ValueError('Binding row is unavailable')
    if key is not None and any(k not in rows[index]or digest(rows[index][k])!=digest(v)for k,v in key.items()):
        raise ValueError('Binding row belongs to another entity or lacks its keys')
    return {'result_view':address['result_view'],'row':index,'column':address['column']}


def bind_prose_literals(value,operation,native,*,key=None,resolved_bases=None,basis=None):
    """Attach native cells to unchanged literal spans, without model rewriting.

    This only constructs the existing scalar template contract. The ordinary
    compiler still checks entity bases, source completeness and every value;
    independent review still checks meaning. No new evidence is created.
    """
    import string,re
    if set(operation)!={'bind_literals'}or not isinstance(operation['bind_literals'],list)or not operation['bind_literals']:
        raise ValueError('bind_literals requires a nonempty list of literal/binding edits')
    value=expand_prose_bindings(value,basis)
    if isinstance(value,str):parts=[(value,None)];bindings={}
    else:
        repair_rendered_value(value,native)
        bindings=dict(value['bindings']);parts=[]
        for literal,name,spec,conversion in string.Formatter().parse(value['template']):
            if name is not None and (not re.fullmatch(r'[A-Za-z_]\w*',name)or spec or conversion):
                raise ValueError('Literal binding requires plain existing placeholders')
            parts.append((literal,name))
    replacements={};used=set(bindings)
    for edit_index,edit in enumerate(operation['bind_literals']):
        if (not isinstance(edit,dict)or not {'literal','binding'}<=set(edit)
                or set(edit)-{'literal','binding','occurrence'}or not isinstance(edit['literal'],str)or not edit['literal']):
            raise ValueError('Each literal edit requires exact literal text and an executed scalar binding')
        literal=edit['literal'];matches=[]
        # A numeric repair addresses a whole value, never a substring of a
        # record ID, date, larger number or decimal. This mirrors the compiler's
        # distinction between quantities and source/temporal addresses.
        number=r'[+-]?\d+(?:,\d{3})*(?:\.\d+)?'
        numeric_literal=re.fullmatch(number,literal) is not None
        pattern=(r'(?<!\w)'+re.escape(literal)+r'(?!\w)')if literal.isalpha()else re.escape(literal)
        for index,(text,_)in enumerate(parts):
            if numeric_literal:
                protected=prose_temporal_spans(text)
                protected.extend(m.span()for m in re.finditer(r'\b[A-Za-z][A-Za-z0-9]*(?:[.-][A-Za-z0-9]+)*\b',text)
                                 if any(c.isdigit()for c in m.group()))
                matches.extend((index,m.start(),m.end())for m in re.finditer(r'(?<![\w.])'+number+r'(?![\w.])',text)
                    if m.group()==literal and not any(m.start()<end and start<m.end()for start,end in protected))
            else:
                # Number words are words; multiword/source quotations remain
                # exact substring selections rather than inferred quantities.
                matches.extend((index,m.start(),m.end())for m in re.finditer(pattern,text))
        if 'occurrence'not in edit and len(matches)!=1:
            raise ValueError('Literal must occur exactly once, or specify its zero-based occurrence outside existing placeholders: '
                +json.dumps({'edit':edit_index,'literal':literal,'matches':len(matches)}))
        occurrence=edit.get('occurrence',0)
        if type(occurrence)is not int or occurrence<0 or occurrence>=len(matches):
            raise ValueError('Literal occurrence is unavailable outside existing placeholders: '
                +json.dumps({'edit':edit_index,'literal':literal,'occurrence':occurrence,'matches':len(matches)}))
        index,start,end=matches[occurrence]
        if any(start<b and a<end for a,b,_ in replacements.get(index,[])):
            raise ValueError('Literal binding edits cannot overlap')
        address=resolve_prose_binding(edit['binding'],native,key)
        rendered=repair_rendered_value({'template':'{value}','bindings':{'value':address}},native)
        if (numeric_literal and literal.startswith('+')and not rendered.startswith('+')
            and canonical_repair_prose(literal[1:])==canonical_repair_prose(rendered)):
            # Keep the explicitly written positive sign in the sentence. The
            # native scalar supplies the identical magnitude; do not require
            # another SQL value merely to reproduce display punctuation.
            start+=1
            literal=literal[1:]
        if canonical_repair_prose(literal)!=canonical_repair_prose(rendered):
            raise ValueError('Literal binding must preserve the exact value; query its actual source instead of changing the sentence')
        serial=len(used);name='bound_literal_'+str(serial)
        while name in used:serial+=1;name='bound_literal_'+str(serial)
        used.add(name);bindings[name]=address;replacements.setdefault(index,[]).append((start,end,name))
        if resolved_bases is not None:resolved_bases.append({k:address[k]for k in ('result_view','row')})
    template=[]
    escape=lambda text:text.replace('{','{{').replace('}','}}')
    for index,(text,name)in enumerate(parts):
        cursor=0
        for start,end,new_name in sorted(replacements.get(index,[])):
            template.extend((escape(text[cursor:start]),'{'+new_name+'}'));cursor=end
        template.append(escape(text[cursor:]))
        if name is not None:template.append('{'+name+'}')
    return {'template':''.join(template),'bindings':bindings}


def composition_output_schema(candidate,feedback,calls):
    """Constrain a located unfinished composition, without selecting its meaning.

    Membership comes from the complete executed table. A draft-bound producer
    review supplies the entity key names and locates the omission. This is not
    a question-keyword router, clinical verdict or permission to invent prose.
    """
    from .result_assembly import _rows,incomplete_reason
    if (not isinstance(candidate,dict)or not isinstance(feedback,dict)
            or feedback.get('repair_kind')!='incomplete_composition'
            or feedback.get('composition_draft_digest')!=digest(candidate)):return None
    try:
        targets=feedback['composition']['targets']
        if not isinstance(targets,list)or not targets:return None
        keys=sorted(set(targets[0])-{'row','reason'})
        if not keys:return None
        native={c['result']['result_view']:c['result']for c in calls if isinstance(c.get('result'),dict)
            and c['result'].get('result_view')and not c['result'].get('error')}
        root=candidate['table_from_tool'];result=native[root]
        if incomplete_reason(result):return None
        columns,rows=_rows(result)
        if not rows or not set(keys)<=set(columns):return None
        entities=[{k:r[k]for k in keys}for r in rows]
        if any(type(v)not in(str,int,float,bool)for key in entities for v in key.values()):return None
        json.dumps(entities,allow_nan=False)
        if len({digest(key)for key in entities})!=len(rows):return None
        for target in targets:
            if (set(target)-{'row','reason'}!=set(keys)or type(target['row'])is not int
                    or not 0<=target['row']<len(rows)
                    or digest({k:target[k]for k in keys})!=digest(entities[target['row']])):return None
        field_names=[]
        spec=candidate.get('row_prose')
        if spec is not None:
            if (not isinstance(spec['keys'],list)or len(spec['keys'])!=len(keys)
                    or set(spec['keys'])!=set(keys)or not spec['rows']):return None
            keys=list(spec['keys'])
            field_names=list(spec['rows'][0]['fields'])
            if not field_names or any(set(e['fields'])!=set(field_names)for e in spec['rows']):return None
        def obj(properties,required=None):
            return {'type':'object','properties':properties,'required':list(properties)if required is None else required,
                    'additionalProperties':False}
        prose={'anyOf':[{'type':'string'},obj({'template':{'type':'string'},
            'bindings':{'type':'object','additionalProperties':{'type':'object'}}})]}
        fields=({'type':'object','properties':{k:prose for k in field_names},'required':field_names,
                 'additionalProperties':prose}if field_names else
                {'type':'object','minProperties':1,'additionalProperties':prose})
        if set(field_names)&set(columns):return None
        fields['propertyNames']={'not':{'enum':columns}}
        row=obj({'key':{'type':'object','enum':entities},
            'basis':{'type':'array','minItems':1,'items':obj({'result_view':{'type':'string','enum':list(native)},
                                                         'row':{'type':'integer','minimum':0}})},'fields':fields})
        return {'type':'object','properties':{
            'status':{'enum':['answered','partial','unsupported']},'answer_type':{'enum':['table']},
            'headline':prose,'table_from_tool':{'type':'string','enum':list(native)},
            'row_prose':obj({'keys':{'type':'array','enum':[keys]},
                'rows':{'type':'array','items':row,'minItems':len(rows),'maxItems':len(rows)}})},
            'required':['status','answer_type','headline','table_from_tool','row_prose'],'additionalProperties':True}
    except (KeyError,TypeError,ValueError,IndexError):return None


def composition_contract_error(proposal,schema):
    """Enforce native membership even if a transport ignores its output schema.

    The ordinary compiler and full review still check scalar values, meaning,
    fields, provenance and source support after this structural boundary.
    """
    if schema is None:return None
    reason='Complete the retained row interpretation: return row_prose once for every native entity, with its exact keys and all requested text fields.'
    try:
        spec=proposal['row_prose'];contract=schema['properties']['row_prose']['properties']
        entries=spec['rows'];row=contract['rows']['items']['properties'];keys=contract['keys']['enum'][0]
        if spec['keys']!=keys or not isinstance(entries,list)or len(entries)!=contract['rows']['minItems']:return reason
        expected={digest(k)for k in row['key']['enum']};seen=set();required=set(row['fields'].get('required',[]))
        native_columns=set(row['fields']['propertyNames']['not']['enum']);field_names=None
        for entry in entries:
            if not isinstance(entry['key'],dict)or set(entry['key'])!=set(keys):return reason
            ident=digest(entry['key'])
            if ident not in expected or ident in seen:return reason
            seen.add(ident);fields=entry['fields']
            if not isinstance(fields,dict)or not fields or not required<=set(fields):return reason
            if set(fields)&native_columns:return 'Use new named row-prose fields; the executed native columns must remain unchanged.'
            if field_names is None:field_names=set(fields)
            elif set(fields)!=field_names:return 'Return the same named interpretation fields for every native entity; keep unavailable evidence explicit.'
        return None if seen==expected else reason
    except (KeyError,TypeError,ValueError):return reason


def binding_repair_schema(focus):
    """Constrain binding output to small edits, not replacement sentences.

    This is a generation contract, never an acceptance shortcut. Runtime still
    checks the exact draft, target entity/field, value, basis and full answer.
    """
    if focus and focus.get('scope')=='answer':
        if focus.get('intent')=='source_meaning_review':return None
        def obj(properties):return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}
        address=obj({'result_view':{'type':'string'},'row':{'type':'integer','minimum':0},'column':{'type':'string'}})
        edit={'type':'object','properties':{'literal':{'type':'string','minLength':1},'binding':address,
            'occurrence':{'type':'integer','minimum':0}},'required':['literal','binding'],'additionalProperties':False}
        field=obj({'path':{'enum':[t['path']for t in focus['answer_targets']]},
            'bind_literals':{'type':'array','minItems':1,'items':edit}})
        return obj({'repair_draft':obj({'draft_digest':{'enum':[focus['draft_digest']]},'fields':{'type':'array','items':field}})})
    if (not focus or focus.get('intent')=='source_meaning_review' or not focus.get('targets')
            or any(t.get('code')not in ('unbound_literal','missing_scalar_source')for t in focus['targets'])):
        return None
    def obj(properties,required=None):
        return {'type':'object','properties':properties,'required':list(properties)if required is None else required,'additionalProperties':False}
    selector=obj({'column':{'type':'string'},'value':{'type':['string','number','boolean','null']}})
    address={'result_view':{'type':'string'},'column':{'type':'string'},'where':{'type':'array','items':selector}}
    edit=obj({'literal':{'type':'string','minLength':1},'binding':obj(address),
              'occurrence':{'type':'integer','minimum':0}},['literal','binding'])
    field=obj({'bind_literals':{'type':'array','items':edit,'minItems':1}})
    fields={t['field']:field for t in focus['targets']}
    key=obj({k:{'enum':list({digest(t['key'][k]):t['key'][k]for t in focus['targets']}.values())}for k in focus['keys']})
    row=obj({'key':key,'fields':obj(fields,[])})
    return obj({'repair_draft':obj({'draft_digest':{'enum':[focus['draft_digest']]},'rows':{'type':'array','items':row}})})


def prose_temporal_spans(text):
    """Exact valid ISO month/day/timestamp addresses, not measured quantities.

    Retain their literal precision and text. Source meaning and clock roles
    still require review; recognizing a date never verifies the date's truth.
    Invalid calendars, decimal values and ordinary signed counts stay numeric.
    """
    import re
    from datetime import date,datetime
    pattern=r'(?<!\w)\d{4}-\d{2}(?:-\d{2}(?:[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?)?(?![\w-])'
    spans=[]
    for match in re.finditer(pattern,text):
        value=match.group()
        try:
            if len(value)==7:date.fromisoformat(value+'-01')
            else:datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError:continue
        spans.append(match.span())
    # Calendar-year intervals need a temporal frame and a clause boundary;
    # "2028-2029 patients" remains a quantity rather than a date exemption.
    interval=r'\b(?:in|during|across|between|from|for|years?)\s+(?P<interval>\d{4}\s*(?:[-–]|to|through|and)\s*\d{4})(?=$|\s*[,.;:()]|\s+(?:and|with|while|but)\b)'
    for match in re.finditer(interval,text,re.I):
        years=re.findall(r'\d{4}',match['interval'])
        if 1<=int(years[0])<=int(years[1])<=9999:spans.append(match.span('interval'))
    return sorted(set(spans))


def temporal_template_literals(template,bindings,native):
    """Locate clock spans across actual scalar placeholders and literal text.

    Only literal portions of a valid rendered clock are masked. Placeholder
    values retain their ordinary native binding validation; no quantity value
    is matched by coincidence or certified by this rendering projection.
    """
    import string
    parts=list(string.Formatter().parse(template));original=[p[0]for p in parts]
    rendered='';segments=[]
    for literal,name,spec,conversion in parts:
        start=len(rendered);rendered+=literal;segments.append((start,len(rendered)))
        if name is not None:
            if spec or conversion or name not in bindings:return original
            try:rendered+=repair_rendered_value({'template':'{value}','bindings':{'value':bindings[name]}},native)
            except (ValueError,KeyError,TypeError,IndexError):return original
    spans=prose_temporal_spans(rendered);masked=[]
    for literal,(start,end)in zip(original,segments):
        for left,right in reversed(spans):
            a,b=max(start,left),min(end,right)
            if a<b:literal=literal[:a-start]+literal[b-start:]
        masked.append(literal)
    return masked


def expand_date_pairs(text):
    """Expand only two valid ISO dates sharing a year/month, never a ratio."""
    import re
    from datetime import date
    def expand(match):
        try:
            first=date.fromisoformat(match.group(1))
            second=date(first.year,first.month,int(match.group(2)))
        except ValueError:return match.group()
        return first.isoformat()+' and '+second.isoformat()
    return re.sub(r'(?<![\w/.-])(\d{4}-\d{2}-\d{2})/(\d{1,2})(?![\w/-]|\.\d)',expand,text)


def canonical_repair_prose(text):
    """Allow number spelling/format changes, but no lost sentence content."""
    import re
    from decimal import Decimal
    words=('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty').split()
    numbers={w:str(i)for i,w in enumerate(words)}
    def replace(match):
        value=match.group()
        return numbers[value.lower()]if value.lower()in numbers else format(Decimal(value.replace(',','')).normalize(),'f')
    normalized=re.sub(r'(?<![\w.-])\d+(?:,\d{3})*(?:\.\d+)?(?![\w.-])|\b(?:'+ '|'.join(words)+r')\b',replace,expand_date_pairs(text),flags=re.I)
    return re.sub(r'\s+',' ',normalized).strip()


def expand_prose_bindings(value,basis=None):
    """Expand explicit row-local references; never infer evidence or entity keys.

    This is representation only. The compiler still validates every basis and
    scalar against current executed results. Persisted proofs use full addresses.
    """
    if not isinstance(value,dict)or not isinstance(value.get('bindings'),dict):return value
    bindings={}
    for name,address in value['bindings'].items():
        if isinstance(address,dict)and 'basis'in address:
            if (set(address)!={'basis','column'}or type(address['basis'])is not int
                    or not isinstance(address['column'],str)or not isinstance(basis,list)
                    or not 0<=address['basis']<len(basis)):
                raise ValueError('Scalar basis reference requires a valid zero-based index in this entity\'s basis and a column')
            row=basis[address['basis']]
            if (not isinstance(row,dict)or set(row)!={'result_view','row'}or not isinstance(row['result_view'],str)
                    or type(row['row'])is not int or row['row']<0):
                raise ValueError('Scalar basis reference must name an explicit native row address')
            address={**row,'column':address['column']}
        bindings[name]=address
    return {**value,'bindings':bindings}


def repair_rendered_value(value,native,*,basis=None):
    """Preview exact scalar insertion for preservation only; compiler still runs."""
    from .result_assembly import _rows
    value=expand_prose_bindings(value,basis)
    if isinstance(value,str):return value
    if not isinstance(value,dict)or set(value)!={'template','bindings'}or not isinstance(value['template'],str)or not isinstance(value['bindings'],dict):
        raise ValueError('Binding repair requires a scalar template')
    values={}
    for name,address in value['bindings'].items():
        if (not isinstance(address,dict)or set(address)!={'result_view','row','column'}
                or type(address['row'])is not int or address['row']<0 or not isinstance(address['result_view'],str)
                or not isinstance(address['column'],str)or address['result_view']not in native):
            raise ValueError('Binding repair must use available executed scalar addresses')
        _,rows=_rows(native[address['result_view']])
        if address['row']>=len(rows)or address['column']not in rows[address['row']]:raise ValueError('Binding repair scalar is unavailable')
        scalar=rows[address['row']][address['column']]
        if scalar is not None and type(scalar)not in(str,int,float,bool):raise ValueError('Binding repair cell is not scalar')
        values[name]='unknown'if scalar is None else str(scalar)
    try:return value['template'].format_map(values)
    except (ValueError,KeyError,IndexError,AttributeError)as exc:raise ValueError('Binding repair template is invalid')from exc


def valid_native_prose_context(context):
    """Check the exact typed observation provenance carried by a repair view."""
    try:
        rows=context['evidence'];provenance=context['evidence_provenance']
        origins={p['row_digest']:p['origins']for p in provenance}
        contracts={c['result_view']:c for c in context['native_calculation_contracts']}
        questions={digest(r['questions'])for r in context.get('review_scope',[])}
        if (not context.get('patient_id')or not context.get('packet_digest')or not rows or not contracts or
                len(origins)!=len(provenance)or {digest(r)for r in rows}!=set(origins)):
            return False
        for row in rows:
            if row.get('patient_id')!=context['patient_id']or not origins[digest(row)]:return False
            for origin in origins[digest(row)]:
                if origin.get('kind')=='executed_native_result':
                    c=contracts[origin['result_view']]
                    if origin['tool']!=c['tool']or origin['input_digest']!=digest(c.get('input')):return False
                elif origin.get('kind')=='machine_document_observation':
                    if origin.get('question_digest')not in questions:return False
                else:return False
        return True
    except (KeyError,TypeError,ValueError,AttributeError):return False


def source_prose_repair(candidate,support,packets,coverage):
    """Locate only reviewed prose, retaining complete source and draft bindings.

    An undecided check permits source inspection, not a finding of error. Native
    fields, incomplete source and unlocalized obligations remain unresolved;
    they do not authorize edits or suppress independent bound prose repairs.
    """
    from ..deliverables.receipts import uncompact_json
    if (not isinstance(candidate,dict)or
        coverage.get('decided_by')and coverage.get('label')!='answers_fully'):return None
    if support.get('all_dependencies_checked')is not True:
        # The reviewer stops further work on a decided field fault. That stop
        # must not revoke the exact reviews already obtained, or authorize a
        # rewrite of unreviewed patients. Keep their full obligations below.
        unchecked=support.get('unchecked_packet_ids');reviewed=support.get('packets')or[]
        checks={p.get('id'):p for p in reviewed}
        faults={p.get('id')for p in reviewed if p.get('decided_by')and any(
            f.get('decided_by')and f.get('label')not in ('supported','unknown',None)
            for f in p.get('claim_feedback')or[])}
        if (support.get('all_dependencies_checked')is not False or
                support.get('packet_manifest_complete')is not True or support.get('label')!='contradicted' or
                support.get('decided_by')!='all_dependency_checks' or not isinstance(unchecked,list)or not unchecked or
                len(set(unchecked))!=len(unchecked)or not faults.intersection(support.get('failed_packet_ids')or[])or
                any(k not in checks or checks[k].get('decided_by')or checks[k].get('label')!='unknown'for k in unchecked)):
            return None
    global_repair=global_source_prose_repair(candidate,support,packets)
    if global_repair:return global_repair
    spec=candidate.get('row_prose')or{}
    if spec.get('keys')!=['patient_id']:return None
    try:
        rows={r['patient_id']:r for r in candidate['table']}
        prose={r['key']['patient_id']:r for r in spec['rows']}
        if len(rows)!=len(candidate['table'])or len(prose)!=len(spec['rows']):return None
        producers={p['id']:p for p in packets};targets=[];contexts={};native_contexts={};seen=set();remaining=[]
        pending=[p for p in support['packets']if p.get('label')!='supported'or not p.get('decided_by')]
        checks={p['id']:p for p in support['packets']}
        if not pending:return None
        for reviewed in pending:
            packet=producers.get(reviewed['id']);review=reviewed.get('field_review')or{}
            if packet is None:
                remaining.append({'packet_id':reviewed['id'],'reason':'No direct prose producer for this source check'});continue
            if (packet.get('patient_dependency_bound')is not True or not reviewed.get('claim_feedback')
                    or not review.get('packet_digest')or not review.get('manifest_digest')):
                # Summary and whole-packet unknowns are still obligations, but
                # have no localized field authority to partition or edit.
                remaining.append({'packet_id':reviewed['id'],'reason':'No complete producer-bound prose review'});continue
            source_state=uncompact_json(json.loads(packet['text']));source_packet_digest=digest(packet['text'])
            if review.get('packet_digest')!=digest(packet['text']):
                # Reconstruct the exact existing source-partition parent. Only
                # already-supported children can remain outside this repair.
                from .evidence_projection import partition_support_packet
                parts=partition_support_packet(packet)
                parent=next((p for p in parts if p['id']==reviewed['id']),None)
                required=(parent or{}).get('required_source_packet_ids')
                if not required or any(checks.get(k,{}).get('label')!='supported'or
                    not checks[k].get('decided_by')for k in required):
                    remaining.append({'packet_id':reviewed['id'],'reason':'Source dependency is not settled'});continue
                packet=parent
            if (packet.get('patient_dependency_bound')is not True or packet.get('source_context_complete')is not True or
                packet.get('dependency_complete')is not True or packet.get('parent_packet_id')or
                not reviewed.get('claim_feedback')or review.get('packet_digest')!=digest(packet['text'])or
                review.get('manifest_digest')!=digest(packet['claim_manifest'])):
                remaining.append({'packet_id':reviewed['id'],'reason':'No complete producer-bound prose review'});continue
            state=uncompact_json(json.loads(packet['text']));pid=packet['source_dependency_patient_id']
            if (state.get('patient_id')!=pid or source_state.get('patient_id')!=pid or
                source_state.get('source_context_gaps')):return None
            native_context=None
            if not source_state.get('source_contexts'):
                # Structured evidence has no document body to copy. Preserve
                # its typed rows, execution contracts and machine-observation
                # status instead of inventing a document or losing locality.
                native_context={'patient_id':pid,'packet_digest':source_packet_digest,
                    **{k:source_state.get(k)for k in ('evidence','evidence_provenance','native_calculation_contracts',
                        'executed_source_definitions','shared_evidence','review_scope')}}
                if not valid_native_prose_context(native_context):return None
            entry=prose[pid];claims={c['id']:c for c in packet['claim_manifest']};before_targets=len(targets)
            for f in reviewed['claim_feedback']:
                claim=claims[f['claim_id']];field=f['field'];path=f['path']
                if (f.get('packet_id')!=packet['id']or field!=claim['field']or path!=claim['path']or
                    len(path)!=3 or path[0]!='answer_rows'or path[2]!=field or
                    type(path[1])is not int or state['answer_rows'][path[1]].get('patient_id')!=pid or
                    (pid,field)in seen):return None
                if field not in entry['fields']:
                    # A native-field uncertainty cannot authorize editing that
                    # field, or prevent independent located prose corrections.
                    # Keep it explicit; the full reviewer must still recheck it.
                    remaining.append({'packet_id':packet['id'],'claim_id':claim['id'],
                        'field':field,'reason':'Native field requires its own evidence check'});continue
                if state['answer_rows'][path[1]][field]!=rows[pid][field]:return None
                seen.add((pid,field))
                targets.append({'key':entry['key'],'field':field,'basis':entry['basis'],
                    'code':'source_meaning_review','value_digest':digest(entry['fields'][field]),
                    'reviewed_value_digest':digest(rows[pid][field]),'packet_id':packet['id'],
                    'packet_digest':review['packet_digest'],'source_packet_digest':source_packet_digest,'claim_id':claim['id'],
                    'review_disposition':f.get('label'),'decided_by':f.get('decided_by'),
                    'reason':f.get('reason'),'source_fault_established':bool(f.get('decided_by')and f.get('label')!='unknown')})
            if native_context is not None and len(targets)>before_targets:native_contexts[pid]=native_context
            for c in source_state.get('source_contexts',[]) if len(targets)>before_targets else []:
                if c.get('patient_id')!=pid or not c.get('record_id')or not c.get('date')or not isinstance(c.get('text'),str)or not c['text']:return None
                ident=(pid,c['record_id'],c['date'])
                if ident in contexts and contexts[ident]!=c:return None
                contexts[ident]=c
        if not targets:return None
        sources=list(contexts.values())
        native_sources=list(native_contexts.values())
        return {'schema':'native-prose-repair-targets/1','intent':'source_meaning_review',
            'draft_digest':digest(candidate),'targets':targets,'source_contexts':sources,
            'remaining_dependencies':remaining,
            'all_dependencies_checked':support.get('all_dependencies_checked')is True,
            'unchecked_packet_ids':list(support.get('unchecked_packet_ids')or[]),
            'source_contexts_digest':digest(sources),'coverage_still_required':True,
            'native_source_contexts':native_sources,'native_source_contexts_digest':digest(native_sources),
            'authority':'machine_review','clinical_qualification':False}
    except (KeyError,ValueError,TypeError,IndexError):return None


def global_source_prose_repair(candidate,support,packets):
    """Bind a global meaning edit to its exact complete source review.

    A global packet may locate prose faults as well as patient packets. Retain
    the whole packet and every unrelated obligation; no native cell is editable.
    """
    from ..deliverables.receipts import uncompact_json
    from .backstop_agent import global_narrative_claims
    try:
        packet=next(p for p in packets if p['id']=='summary')
        checked=next(p for p in support['packets']if p['id']=='summary')
        review=checked.get('field_review')or{}
        claims=global_narrative_claims(packet)
        if (packet.get('source_context_complete')is not True or packet.get('dependency_complete')is not True
                or not claims or review.get('packet_digest')!=digest(packet['text'])
                or review.get('manifest_digest')!=digest(claims)):return None
        state=json.loads(packet['text'])
        if 'encoding'in state:state=uncompact_json(state)
        by_id={c['id']:c for c in claims};targets=[];seen=set()
        for f in checked.get('claim_feedback')or[]:
            claim=by_id.get(f.get('claim_id'));path=f.get('path')
            if (not claim or f.get('packet_id')!='summary'or f.get('field')!=claim['field']
                    or path!=claim['path']or tuple(path)in seen or
                    f.get('label')not in('unknown','wrong_entity','wrong_domain','wrong_subject','wrong_time','wrong_role','missing_evidence')):return None
            seen.add(tuple(path));path=path[1:]
            value=candidate[path[0]]if len(path)==1 else candidate['facts'][path[1]]
            observed=state['answer'][path[0]]if len(path)==1 else state['answer']['facts'][path[1]]
            if not isinstance(value,(str,dict))or value!=observed:return None
            targets.append({'path':path,'field':claim['field'],'code':'source_meaning_review',
                'value_digest':digest(value),'reviewed_value_digest':digest(observed),
                'packet_id':'summary','packet_digest':review['packet_digest'],'claim_id':claim['id'],
                'review_disposition':f.get('label'),'decided_by':f.get('decided_by'),
                'reason':f.get('reason'),'source_fault_established':bool(f.get('decided_by')and f.get('label')not in('supported','unknown',None))})
        if not targets:return None
        sources=[{k:packet[k]for k in ('id','text','claim_manifest','source_context_complete','dependency_complete')}]
        return {'schema':'native-prose-repair-targets/1','scope':'answer','intent':'source_meaning_review',
            'draft_digest':digest(candidate),'answer_targets':targets,'source_packets':sources,
            'source_packets_digest':digest(sources),'coverage_still_required':True,
            'all_dependencies_checked':support.get('all_dependencies_checked')is True,
            'unchecked_packet_ids':list(support.get('unchecked_packet_ids')or[]),
            'authority':'machine_review','clinical_qualification':False}
    except (StopIteration,KeyError,ValueError,TypeError,IndexError):return None


def answer_prose_repair_targets(candidate,feedback):
    """Only compiler-bound top-level quantities authorize a small binding edit."""
    # Aggregate/scalar answers have the same headline/fact binding contract
    # without entity prose. Their exact producer targets authorize only these
    # fields; row edits still require the separate keyed-prose contract.
    if not isinstance(candidate,dict)or not isinstance(feedback,dict):return None
    receipt=feedback.get('prose_repair')
    if (not isinstance(receipt,dict)or receipt.get('schema')!='native-prose-repair-targets/1'
            or receipt.get('draft_digest')!=digest(candidate)):
        return None
    source_meaning=receipt.get('intent')=='source_meaning_review'and receipt.get('scope')=='answer'
    if source_meaning:
        # Reconstruct from the complete producer packet, rather than trusting
        # target paths/digests copied into operational feedback.
        from .backstop_agent import global_narrative_claims
        try:
            sources=receipt.get('source_packets');targets=receipt.get('answer_targets')
            if (not isinstance(sources,list)or len(sources)!=1 or receipt.get('source_packets_digest')!=digest(sources)
                    or not isinstance(targets,list)or not targets):return None
            checks=[{'id':'summary','field_review':{'packet_digest':targets[0].get('packet_digest'),
                'manifest_digest':digest(global_narrative_claims(sources[0]))},
                'claim_feedback':[{'packet_id':t.get('packet_id'),'claim_id':t.get('claim_id'),'field':t.get('field'),
                    'path':['answer',*t.get('path',[])],'label':t.get('review_disposition'),
                    'decided_by':t.get('decided_by'),'reason':t.get('reason')}for t in targets]}]
            rebuilt=global_source_prose_repair(candidate,{'packets':checks},sources)
            return targets if rebuilt and rebuilt['answer_targets']==targets else None
        except (KeyError,TypeError,ValueError,IndexError):return None
    if feedback.get('repair_kind')!='deterministic_compiler':return None
    targets=receipt.get('answer_targets')
    if not isinstance(targets,list)or not targets:return None
    seen=set()
    for target in targets:
        if not isinstance(target,dict)or target.get('code')!='unbound_literal':return None
        path=target.get('path')
        if (not isinstance(path,list)or not(path==['headline']or len(path)==2 and path[0]=='facts'and isinstance(path[1],str))):return None
        try:value=candidate[path[0]]if len(path)==1 else candidate['facts'][path[1]]
        except (KeyError,TypeError):return None
        if not isinstance(value,(str,dict)):return None
        if target.get('value_digest')!=digest(value)or tuple(path)in seen:return None
        seen.add(tuple(path))
    return targets


def prose_repair_targets(candidate,feedback):
    """Only exact producer-bound row failures permit a narrower repair view."""
    if not isinstance(candidate,dict)or not isinstance(feedback,dict):return None
    receipt=feedback.get('prose_repair')
    if not isinstance(receipt,dict)or receipt.get('schema')!='native-prose-repair-targets/1':return None
    if receipt.get('draft_digest')!=digest(candidate):return None
    spec=candidate.get('row_prose')or{};rows=spec.get('rows');keys=spec.get('keys')
    if not isinstance(rows,list)or not isinstance(keys,list):return None
    entries={digest(r.get('key')):r for r in rows if isinstance(r,dict)}
    targets=receipt.get('targets')
    if not isinstance(targets,list)or not targets:return None
    found=set()
    for target in targets:
        if not isinstance(target,dict)or not isinstance(target.get('key'),dict)or set(target['key'])!=set(keys):return None
        ident=digest(target['key']);entry=entries.get(ident);field=target.get('field')
        if not isinstance(field,str)or not entry or field not in(entry.get('fields')or{}):return None
        if target.get('value_digest')!=digest(entry['fields'][field])or target.get('basis')!=entry.get('basis'):return None
        if (ident,field)in found:return None
        found.add((ident,field))
    if receipt.get('intent')=='source_meaning_review':
        sources=receipt.get('source_contexts');wanted={t['key'].get('patient_id')for t in targets}
        native_sources=receipt.get('native_source_contexts',[])
        if (not isinstance(native_sources,list)or any(not valid_native_prose_context(c)for c in native_sources)or
            native_sources and receipt.get('native_source_contexts_digest')!=digest(native_sources)):
            return None
        native_patients={c['patient_id']for c in native_sources}
        if any(c['packet_digest']!=t.get('source_packet_digest')for c in native_sources
               for t in targets if t['key'].get('patient_id')==c['patient_id']):return None
        if (not isinstance(sources,list)or not(sources or native_sources)or not all(isinstance(c,dict)for c in sources)or
            receipt.get('source_contexts_digest')!=digest(sources)or
            {c.get('patient_id')for c in sources}|native_patients!=wanted or any(
                not c.get('record_id')or not c.get('date')or not isinstance(c.get('text'),str)or not c['text']for c in sources)):return None
        rows={r.get('patient_id'):r for r in candidate.get('table',[])}
        if any(t.get('code')!='source_meaning_review'or (not receipt.get('pending_compiler')and
            t.get('reviewed_value_digest')!=digest(rows.get(t['key'].get('patient_id'),{}).get(t['field'])))for t in targets):return None
    return targets


def continue_source_prose_repair(previous,feedback,candidate,assessment):
    """Carry source provenance into a compiler-owned binding repair.

    Source interpretation has produced a new draft. Its failed bindings now
    need sentence-preserving edits, not renewed permission to rewrite meaning.
    Keep the compiler's exact targets and the earlier source context; complete
    source and coverage review still follow successful compilation.
    """
    import copy
    if assessment.get('repair_kind')!='deterministic_compiler':return assessment
    receipt=feedback.get('prose_repair',{})if isinstance(feedback,dict)else{}
    if receipt.get('intent')!='source_meaning_review':return assessment
    allowed=prose_repair_targets(previous,feedback);failed=prose_repair_targets(candidate,assessment)
    if not allowed or not failed:return assessment
    scope={(digest(t['key']),t['field'])for t in allowed}
    if any((digest(t['key']),t['field'])not in scope for t in failed):return assessment
    try:
        if previous['table_from_tool']!=candidate['table_from_tool']:return assessment
        old=previous['row_prose'];new=candidate['row_prose']
        if old['keys']!=new['keys']or len(old['rows'])!=len(new['rows']):return assessment
        updated={}
        for before,after in zip(old['rows'],new['rows']):
            if before['key']!=after['key']or before['basis']!=after['basis']or set(before['fields'])!=set(after['fields']):return assessment
            ident=digest(before['key']);updated[ident]=after
            if any(v!=after['fields'][field]for field,v in before['fields'].items()if(ident,field)not in scope):return assessment
        # Only the named prose may change; global fields and other contracts
        # cannot acquire source-edit authority from this continuation.
        ignored={'row_prose','table','table_binding','computed_prose','sources','investigation_review','verification'}
        if {k:v for k,v in draft_state(previous)['last_draft'].items()if k not in ignored}!={k:v for k,v in draft_state(candidate)['last_draft'].items()if k not in ignored}:return assessment
        fields=set().union(*(set(r['fields'])for r in old['rows']))
        def native_rows(a):return[{k:v for k,v in r.items()if k not in fields}for r in a['table']]
        if native_rows(previous)!=native_rows(candidate):return assessment
        targets=copy.deepcopy(failed)
        continued={**copy.deepcopy(receipt),**copy.deepcopy(assessment['prose_repair']),
            'intent':'source_binding_repair','targets':targets,'pending_compiler':True,
            'source_review_origin_digest':receipt.get('source_review_origin_digest',receipt['draft_digest']),
            'compiler_feedback':assessment.get('feedback')}
        patients={t['key'].get('patient_id')for t in targets}
        for key in ('source_contexts','native_source_contexts'):
            if key in continued:
                continued[key]=[c for c in continued[key]if c.get('patient_id')in patients]
                continued[key+'_digest']=digest(continued[key])
        return {**assessment,'prose_repair':continued}
    except (KeyError,TypeError,ValueError):return assessment


def focused_prose_state(candidate,feedback,calls):
    """A compiler-selected repair view; the full native answer stays intact."""
    from .result_assembly import _rows
    answer_targets=answer_prose_repair_targets(candidate,feedback)
    if answer_targets:
        editable=draft_state(candidate)['last_draft']
        row_prose=candidate.get('row_prose')
        if feedback['prose_repair'].get('intent')=='source_meaning_review':
            receipt=feedback['prose_repair']
            return {'schema':'focused-native-prose-repair/1','scope':'answer','intent':'source_meaning_review',
                'draft_digest':digest(candidate),'keys':[],'targets':[],
                'answer_targets':[{**t,'value':editable[t['path'][0]]if len(t['path'])==1 else editable['facts'][t['path'][1]]}for t in answer_targets],
                'source_packets':receipt['source_packets'],'unchecked_packet_ids':receipt.get('unchecked_packet_ids',[]),
                'native_answer_rows':len(candidate.get('table')or[]),'coverage_still_required':True,
                'patch_contract':{'draft_digest':digest(candidate),'fields':[{'path':'exact located path array','value':'source-supported text or computed template'}]},
                'instruction':'Inspect the named prose against the complete retained source packet. Unknown is not a finding of error. Change only a justified source meaning using repair_draft.fields with path and value. Keep all unrelated statements and native values unchanged. Every quantitative literal needs a binding to actual executed evidence; all existing basis rows are retained. Empty fields means no justified edit, not source acceptance. Complete compilation, coverage and source review still follow.'}
        return {'schema':'focused-native-prose-repair/1','scope':'answer','draft_digest':digest(candidate),'keys':[],
            'targets':[],'answer_targets':[{**t,'value':editable[t['path'][0]]if len(t['path'])==1 else editable['facts'][t['path'][1]]}for t in answer_targets],
            'native_answer_rows':len(candidate.get('table')or[]),'complete_row_prose_rows':len(row_prose.get('rows')or[])if isinstance(row_prose,dict)else 0,
            'retained_patient_target_count':len(feedback['prose_repair'].get('targets')or[]),
            'patch_contract':{'draft_digest':digest(candidate),'fields':[{'path':'exact located path array','bind_literals':[{'literal':'exact unbound span','binding':{'result_view':'executed view','row':'zero-based native row','column':'scalar column'}}]}]},
            'instruction':'Bind only the located headline/facts literals using repair_draft.fields. Preserve the entire original sentence, existing placeholders, quantities, dates and uncertainty. Do not rewrite other prose or regenerate the answer. Use exact executed scalar cells, or query their actual source/definition first; never copy draft constants into SQL. The complete native answer, any entity prose and all located failures remain retained and are recompiled after this patch. These edits establish no source meaning or clinical acceptance.'}
    targets=prose_repair_targets(candidate,feedback)
    if targets is None:return None
    keys=candidate['row_prose']['keys'];wanted={digest(t['key'])for t in targets}
    entries={digest(r['key']):r for r in candidate['row_prose']['rows']}
    def selected(row):
        return all(k in row for k in keys)and digest({k:row[k]for k in keys})in wanted
    bases={(b['result_view'],b['row'])for t in targets for b in t['basis']}
    native_rows=[];record_ids=set()
    def is_collection(value):
        if isinstance(value,(dict,list)):return True
        if isinstance(value,str)and value.lstrip().startswith(('{','[')):
            try:return isinstance(json.loads(value),(dict,list))
            except ValueError:pass
        return False
    def collect_ids(value):
        if isinstance(value,str)and is_collection(value):value=json.loads(value)
        if isinstance(value,list):
            for item in value:collect_ids(item)
        elif isinstance(value,dict):
            if isinstance(value.get('record_id'),str):record_ids.add(value['record_id'])
            for item in value.values():collect_ids(item)
    for call in calls:
        result=call.get('result')or{}
        if not isinstance(result,dict)or result.get('error')or not result.get('result_view'):continue
        name=result['result_view']
        if name not in {view for view,_ in bases}:continue
        columns,rows=_rows(result)
        for index,row in enumerate(rows):
            if not selected(row):continue
            if (name,index)not in bases:continue
            scalars={k:v for k,v in row.items()if not is_collection(v)}
            native_rows.append({'result_view':name,'row':index,'values':scalars,
                'nested_columns_available_by_query':[k for k,v in row.items()if is_collection(v)],'row_digest':digest(row)})
            collect_ids(row)
    fields=[{**target,'value':entries[digest(target['key'])]['fields'][target['field']]}for target in targets]
    state={'schema':'focused-native-prose-repair/1','draft_digest':digest(candidate),
        'keys':keys,'targets':fields,'native_scalar_rows':native_rows,
        'cited_sources':[s for s in candidate.get('sources',[])if s.get('record_id')in record_ids],
        'native_answer_rows':len(candidate.get('table')or[]),'repair_entities':len(wanted),
        'complete_row_prose_rows':len(candidate['row_prose']['rows']),
        'patch_contract':draft_state(candidate)['localized_prose_repair'],
        'instruction':'These are all located compiler failures, not a reduced patient cohort or a clinical conclusion. '
            'Repair only the named fields. Preserve each original sentence, numerical details, units, dates and uncertainty; '
            'use scalar bindings rather than deleting facts. Number words may become the equivalent numeral. '
            'For binding-only repairs prefer the compact field value {"bind_literals":[{"literal":"exact unbound text",'
            '"binding":{"result_view":"an executed view","column":"its scalar column",'
            '"where":[{"column":"record_id","value":"the exact source ID"}]}}]}. '
            'Code attaches each binding to that exact span in the retained sentence and preserves all existing placeholders. '
            'Alphabetic literals match whole words only. Numeric literals match complete values outside dates and identifiers, never a substring of another number. '
            'Do not repeat or rewrite the sentence. If the literal occurs more than once outside placeholders, include '
            '"occurrence":zero_based_index for the intended occurrence in the ORIGINAL template. '
            'The original entity key is always applied by code. Use exact native source/date/field filters to select one row; '
            'where:[] is valid only when that view has exactly one row for this entity. Do not count native row positions. '
            'Code resolves the scalar address and derives basis additions; do not supply basis_additions for these compact edits. '
            'The cell must reproduce the same text/value; complete compilation and source review still apply. '
            'Do not copy draft numbers or entity IDs into source-free SQL constants: that cannot establish an evidence binding. '
            'Every other native row, original prose field and source remains retained and receives the complete review. '
            'Additional basis rows must carry the same entity keys. For a source value inside a nested column, use query_sql '
            'to select its complete exact value with the entity keys and source record/date; do not truncate quotes. '
            'All original results and their scope remain available in executed_results. Nothing here establishes source support.'}
    receipt=feedback['prose_repair']
    if receipt.get('intent')=='source_binding_repair':
        state.update(source_review_origin_digest=receipt['source_review_origin_digest'],
            source_contexts=receipt.get('source_contexts',[]),
            native_source_contexts=receipt.get('native_source_contexts',[]),
            coverage_still_required=True)
        state['instruction']+=' The source correction is retained, but this step only repairs its compiler-located bindings. Source contexts are reading material, not executed scalar cells; full source and coverage review remains required.'
    if receipt.get('intent')=='source_meaning_review':
        state.update(intent='source_meaning_review',source_contexts=receipt['source_contexts'],
            source_contexts_digest=receipt['source_contexts_digest'],coverage_still_required=True,
            native_source_contexts=receipt.get('native_source_contexts',[]),
            remaining_dependencies=receipt.get('remaining_dependencies',[]),
            all_dependencies_checked=receipt.get('all_dependencies_checked'),
            unchecked_packet_ids=receipt.get('unchecked_packet_ids',[]),
            instruction='Inspect only these exact unresolved prose fields against the complete dated source contexts and retained native results. '
                'An undecided model judgment is not a proven error; do not change facts solely to satisfy a probability threshold. '
                'native_source_contexts are executed structured observations and explicitly labeled machine findings, not invented documents or clinical truth. '
                'Their row provenance, source definitions and review scope are retained; acquire a missing original note when its meaning is needed. '
                'Make entity, drug/reason, date, role and source scope explicit where supported. Preserve correct detail and supported unknowns; '
                'Describe each drug and its own event dates separately; shared lists of drugs and dates must not imply every combination. '
                'do not replace unfinished interpretation with generic missingness. Proposed review is not completed care or verified eligibility. '
                'Return only supported changed fields through repair_draft. Preserve native quantities using their existing scalar bindings; '
                'query complete same-entity nested evidence when an additional binding is needed. '
                'A numerical quotation in row prose also requires an executed scalar: if the extracted findings omit it, '
                'use query_sql on the original record text with its entity key, record_id and date, select the complete exact quotation, '
                'then bind that text scalar and include its row in basis_additions. Reading the source_contexts here alone does not create a scalar binding. '
                'All other patient rows, prose and sources remain intact. '
                'Unchecked packets and remaining dependencies are still open obligations, not accepted findings or permission to edit them. '
                'The complete answer, changed dependencies and global question coverage still require verification; this repair view does not accept them.')
        if receipt.get('pending_compiler'):
            state['compiler_feedback']=receipt['compiler_feedback']
            state['instruction']+=' The current proposal has not compiled. Revise these same source-review fields as needed to correct meaning and bindings; this is not a sentence-preserving binding-only edit. Do not delete native values or evade actual quantitative evidence requirements.'
    return state


def recovery_context(replay,tools):
    """Keep complete recovery feedback queryable; send native draft references.

    Owned recovery is still unfinished work, never an accepted learned answer.
    This handles the draft before it can become nested original-request text.
    """
    meta={k:v for k,v in replay.items()if k not in ('calls','final_answer','resume_draft')}
    feedback=meta.get('downstream_review_feedback')
    if not feedback or tools.semantic is None:return meta
    native_digest=digest(feedback)
    raw=json.dumps(feedback,default=str,allow_nan=False)
    columns=['feedback_json','feedback_digest'];rows=[[raw,native_digest]]
    existing=next((v for v in tools.executed_results.values()if v.get('kind')=='unverified_recovery_feedback'and v.get('feedback_digest')==native_digest),None)
    if existing is not None:
        if existing.get('rows')!=[dict(zip(columns,rows[0]))]:
            raise ValueError('Recovery feedback native view changed')
    else:
        existing=tools.retain_result({'columns':columns,'rows':[dict(zip(columns,rows[0]))],
            'truncated':False,'kind':'unverified_recovery_feedback','feedback_digest':native_digest,
            'incomplete_dependencies':[{'authority':'unverified_review_feedback',
                'reason':'Retained unfinished feedback/draft is operational state, not source evidence for an answer.'}]})
    name=existing['result_view']
    reference={'result_view':name,'json_column':'feedback_json','feedback_digest':native_digest,
        'instruction':'Full original feedback and draft remain queryable with query_sql/json_extract. Draft values are unverified; bind any final answer to current executed results and complete verification.'}
    def project(value,path='$'):
        if isinstance(value,list):return[project(v,path+'['+str(i)+']')for i,v in enumerate(value)]
        if not isinstance(value,dict):return value
        out={}
        for key,item in value.items():
            child=path+'.'+json.dumps(key)
            if key=='unverified_retained_draft':
                draft=draft_state(item)
                # Finished server responses can carry complete verifier JSON
                # inside limitation strings. Preserve the prose and retain all
                # embedded structured data at its exact original native path.
                if isinstance(item,dict) and isinstance(item.get('limitations'),list):
                    draft['last_draft']['limitations']=[review_guidance(text,json_path=child+'."limitations"['+str(i)+']')
                        for i,text in enumerate(item['limitations'])]
                out[key]={'native_feedback':reference,**draft}
            else:out[key]=project(item,child)
        return out
    meta['downstream_review_feedback']=project(feedback)
    meta['downstream_review_native']=reference
    return meta


def review_reference(feedback,tools):
    """Keep complete structured review feedback queryable, never a new fact.

    A source review can repeat record lists in its prose and in every field
    judgment. Its exact receipt belongs in the existing native result store;
    copying both representations into an actor prompt can exceed service input
    context even when the patient evidence already stays native.
    """
    if not isinstance(feedback,dict) or not isinstance(feedback.get('support'),dict):return None
    if not feedback['support'].get('packets'):return None
    if getattr(tools,'semantic',None) is None or not callable(getattr(tools,'retain_result',None)):return None
    ident=digest(feedback)
    row={'feedback_json':json.dumps(feedback,default=str,allow_nan=False),'feedback_digest':ident}
    existing=next((v for v in tools.executed_results.values()
        if v.get('kind')=='unverified_review_feedback' and v.get('feedback_digest')==ident),None)
    if existing is not None:
        if existing.get('columns')!=list(row) or existing.get('rows')!=[row] or existing.get('truncated'):
            raise ValueError('Review feedback native view changed')
    else:
        # Operational state must neither consume clinical result sequence
        # numbers nor populate the source-record quotation index.
        from .backstop_agent import define_result
        name='agere_review_feedback_'+ident
        existing={'columns':list(row),'rows':[row],'result_view':name,
            'truncated':False,'kind':'unverified_review_feedback','feedback_digest':ident,
            'incomplete_dependencies':[{'result_view':name,'authority':'unverified_review_feedback',
                'reason':'Reviewer feedback is queryable operational state, not source evidence for an answer.'}]}
        tools.result_sources[name]=define_result(tools.semantic,name,list(row),[list(row.values())],receipt=existing)
        tools.executed_results[name]=existing
    return {'result_view':existing['result_view'],'json_column':'feedback_json','feedback_digest':ident,
        'guidance_path':'$.feedback','packets_path':'$.support.packets',
        'guidance':review_guidance(feedback.get('feedback')),
        'instruction':'The complete exact reviewer receipt, repair prose and every claim/source binding are retained here. '
            'Use query_sql with json_extract/json_each to inspect the relevant packet, field_review and claim_feedback entries. '
            'Packet IDs are opaque labels; use only explicit patient_id bindings. '
            'This is unverified reviewer feedback, not patient evidence or a clinical truth label. '
            'Unknown feedback is an unresolved dependency, not a proven source fault. '
            'Retain unaffected native work and uncertainty; verify any final answer against the current source receipts.'}


def review_guidance(text,*,json_path='$.feedback'):
    """Preserve all repair prose; reference only embedded JSON data literals.

    References include exact character offsets into the retained original text.
    No prefix, word count, model summary or output ceiling is used. A replacement
    is used only when shorter than that JSON literal; small guidance is exact.
    """
    if not isinstance(text,str):return text
    decoder=json.JSONDecoder();parts=[];cursor=0;index=0
    while index<len(text):
        if text[index] not in '[{':index+=1;continue
        try:value,end=decoder.raw_decode(text,index)
        except ValueError:index+=1;continue
        marker="[Exact JSON in native_feedback: substr(json_extract(feedback_json,'"+json_path.replace("'","''")+"'),"+str(index+1)+','+str(end-index)+')]'
        if isinstance(value,(list,dict)) and len(marker)<end-index:
            parts.extend([text[cursor:index],marker]);cursor=end
        index=end
    return ''.join([*parts,text[cursor:]])


def documentation_references(out):
    loaded = out.get('loaded_documentation') or {}
    return {'identity': loaded.get('identity'),
            'sections': [{k: section[k] for k in ('section_id', 'sha256', 'title')}
                         for section in loaded.get('sections', [])]}


def loaded_documentation(calls, identity):
    """One exact copy of each actually loaded section of the current contract."""
    import hashlib
    sections = {}
    if identity is None:return None
    for call in calls:
        loaded = (call.get('result') or {}).get('loaded_documentation') if isinstance(call.get('result'), dict) else None
        if not loaded or loaded.get('identity') != identity:continue
        for section in loaded.get('sections', []):
            if hashlib.sha256(section['documentation_text'].encode()).hexdigest() != section['sha256']:
                raise ValueError('Loaded documentation digest changed')
            key = section['section_id']
            if key in sections and sections[key] != section:
                raise ValueError('Conflicting documentation sections under one installed identity')
            sections[key] = section
    return {'identity': identity, 'sections': list(sections.values()),
            'instruction': 'These are complete first-party documentation sections already loaded from native SQL. '
                'Use their exact definitions; do not fetch them again unless different sections or a changed contract are needed. '
                'They are documentation, never patient evidence.'} if sections else None


def catalog(calls,*,readings_start=0):
    entries=[];native={};duplicate_views=set()
    for index,call in enumerate(calls):
        out=call.get('result')
        entry={'call':index,'tool':call.get('name'),'input_digest':digest(call.get('input')),
               'result_digest':digest(out)}
        if not isinstance(out,dict):
            entry['observation']=out
        elif out.get('error'):
            entry.update(error=out['error'],failed_input=call.get('input'))
        else:
            rows=out.get('rows')
            if not isinstance(rows,list):rows=next((out[k]for k in ('table','documents','passages')if isinstance(out.get(k),list)),None)
            if rows is None and out.get('result_view'):
                # The executor also registers scalar/metric population frames.
                # Use its exact shared shape rather than resending opaque engine
                # payloads that already have a native queryable handle.
                from .backstop_agent import output_table
                columns,matrix=output_table(out)
                if columns:rows=[dict(zip(columns,row))for row in matrix]
            if isinstance(rows,list)and out.get('result_view'):
                entry.update(result_view=out['result_view'],columns=out.get('columns')or
                    list(dict.fromkeys(k for r in rows if isinstance(r,dict)for k in r)),
                    rows_computed=len(rows),truncated=bool(out.get('truncated')))
                # The row handle preserves values, but cannot reconstruct the
                # executed source/filter contract after conversation compaction.
                # Carry the executor's actual SQL, never infer it from a proposed
                # input. Portable receipts inline native rows for independent
                # replay; their declared_executed_query is the exact scoped SQL
                # that ran before that serialization, not a model summary.
                if call.get('name')=='query_sql' and isinstance(out.get('executed_query'),str):
                    declared=out.get('declared_executed_query')
                    entry['executed_query']=declared if isinstance(declared,str) else out['executed_query']
                    if isinstance(declared,str) and declared!=out['executed_query']:
                        entry['portable_executable_provenance']={
                            'query_digest':digest(out['executed_query']),
                            'characters':len(out['executed_query']),
                            'scope':'Complete portable SQL with embedded native rows remains in the execution receipt. '
                                'executed_query is the exact scoped statement that ran against read_views; no source acceptance is inferred.'}
                    elif not isinstance(declared,str):
                        from .sql_query import native_query_model_projection
                        projection=native_query_model_projection(out['executed_query'],{k:v for k,v in native.items()if k not in duplicate_views})
                        if projection is not None:
                            entry.pop('executed_query',None);entry['executed_program_view']=projection
                from .semantic_analyst import collection_manifest,complete_actor_reading,actor_result
                if complete_actor_reading(out):
                    # These are explicitly requested readings, not previews or
                    # model summaries. Retain them after later tools/feedback so
                    # interpretation does not require rereading the same rows.
                    if index>=readings_start:entry['complete_reading']=actor_result(out)
                    else:entry['prior_reading_not_inherited']={'rows':len(rows),'reading_digest':digest(out['actor_reading']),
                        'instruction':'This is retained source-only state, not a reading requested by this actor. Every row remains native; query the evidence needed for interpretation with read_all_rows=true.'}
                collections=collection_manifest(rows)
                if collections:entry['structured_collection_manifest']=collections
                # Small scalar/aggregate results are useful exact observations;
                # patient/free-text tables stay native and queryable by view.
                if out.get('loaded_documentation'):
                    entry['documentation_sections'] = documentation_references(out)
                elif not any(isinstance(r,dict)and'patient_id'in r for r in rows) and len(json.dumps(rows,default=str))<2500:
                    entry['computed_rows']=rows
                for key in ('coverage','method','record_ids_filter','accounting','assembly','source_scope','field_patient_queries','exhaustive_reason','scope_warnings',
                            'native_sql_contract','read_views','native_query_receipt','incomplete_dependencies','registered_domain_functions',
                            'metric_id','name','unit','period','computed_as','status','parameters','analysis_population_count','missing','limitations'):
                    if key in out:
                        if key=='coverage'and isinstance(out[key],list):
                            entry['coverage']={'patients':len(out[key]),'received_documents':sum(r.get('received_documents',0)for r in out[key]),
                                'unique_whole_documents':sum(r.get('unique_whole_documents',0)for r in out[key]),
                                'inventory_whole_documents':sum(r.get('inventory_whole_documents',r.get('unique_whole_documents',0))for r in out[key]),
                                'source_scope':out.get('source_scope'),'chart_wide_absence_established':False}
                        else:entry[key]=out[key]
                if isinstance(rows,list)and any(isinstance(r,dict)and'review_json'in r for r in rows):
                    fields={}
                    for row in rows:
                        try:found=json.loads(row.get('review_json')or'{}')
                        except (ValueError,TypeError):continue
                        for key,values in found.items():
                            if isinstance(values,list):
                                v=fields.setdefault(key,{'patients_with_findings':0,'findings':0,'json_path':'$.'+key})
                                v['patients_with_findings']+=bool(values);v['findings']+=len(values)
                    entry['narrative_field_manifest']=fields
                view=out['result_view']
                if view in native:duplicate_views.add(view)
                native[view]=out
            else:
                # Non-table contracts/instructions must remain visible. They
                # are not silently summarized by a language model or dropped.
                entry['observation']=out
        entries.append(entry)
    return entries


def _column_order_reading(reading):
    """Encode complete table cells once under their columns; never alter native rows."""
    if not isinstance(reading,dict):return reading
    columns=reading.get('columns');rows=reading.get('rows')
    if (not isinstance(columns,list) or not columns or not all(isinstance(c,str)for c in columns)
            or len(set(columns))!=len(columns) or not isinstance(rows,list) or not rows
            or not all(isinstance(row,dict) and set(row)==set(columns)for row in rows)):
        return reading
    projected={**reading,'rows':[[row[column]for column in columns]for row in rows],
        'row_encoding':'Each row contains values in columns order. All cells and row order are exact; native receipts retain the original row objects.'}
    # Repeated source text is a transport cost, not additional evidence. Keep
    # its complete bytes once; every occurrence still has its own row/column.
    # Structured cells stay literal to avoid confusing native objects with refs.
    if ('text_dictionary' not in reading and
            not any(isinstance(value,(dict,list,tuple))for row in projected['rows']for value in row)):
        from collections import Counter
        counts=Counter(value for row in projected['rows']for value in row if isinstance(value,str))
        dictionary=[];indices={}
        for value,count in counts.items():
            if count<2:continue
            index=len(dictionary);literal=len(json.dumps(value));reference=len(json.dumps({'text_ref':index}))
            if count*(literal-reference)>literal+2:
                indices[value]=index;dictionary.append(value)
        if dictionary:
            encoded={**projected,
                'rows':[[{'text_ref':indices[value]}if isinstance(value,str)and value in indices else value
                         for value in row]for row in projected['rows']],
                'text_dictionary':dictionary,
                'row_encoding':projected['row_encoding']+' A cell {"text_ref": N} means the exact complete string at text_dictionary[N] (zero-based). '
                    'Substitute that string at that cell before interpretation or quoting. This only deduplicates identical text; '
                    'distinct rows, entities, dates, relations and contradictory observations remain distinct. All other cells are literal.'}
            if len(json.dumps(encoded,default=str))<len(json.dumps(projected,default=str)):
                projected=encoded
    # Small tables can cost more with the format explanation. Preserve them.
    return projected if len(json.dumps(projected,default=str))<len(json.dumps(reading,default=str)) else reading


def messages(request,calls,*,recent_results=(),feedback=None,candidate=None,documentation_identity=None,documentation_in_system=False,feedback_native=None,readings_start=0):
    from .semantic_analyst import actor_result,complete_actor_reading
    focus=focused_prose_state(candidate,feedback,calls)
    selected_keys={digest(t['key'])for t in focus['targets']}if focus else set()
    def recent(call):
        out = call.get('result')
        if complete_actor_reading(out):
            return {'name':call.get('name'),'input':call.get('input'),
                    'result':{'result_view':out.get('result_view'),
                              'complete_reading_in':('executed_results; exact repair-entity rows in repair_reading when keyed'if focus else'executed_results; exact rows retained there once')}}
        if isinstance(out, dict) and out.get('loaded_documentation'):
            out = {k: v for k, v in out.items() if k not in ('rows', 'loaded_documentation')}
            out['documentation_sections'] = documentation_references(call['result'])
        return {'name': call.get('name'), 'input': call.get('input'), 'result': actor_result(out)}
    state={'schema':VERSION,'original_request':request,'executed_results':catalog(calls,readings_start=readings_start),
        'latest_results':[recent(c) for c in recent_results],
        'instruction':'This is the current deterministic execution state, not a new question or a clinical summary. '
            'All native receipts are retained. SQL result_views retain every computed row; a reading preview does not limit native membership. '
            'Each executed_query in executed_results is the exact successful SQL receipt for that view; inspect it for source, filter, period and aggregation scope without rerunning it. '
            'An executed_program_view is explicitly a snapshot-equivalent display: its literal row sources match complete native views; all other SQL logic remains. The full original SQL stays native. '
            'A truncated producer or incomplete_dependencies cannot support an accepted calculation or population scope. '
            'Read any earlier result by query_sql; read_all_rows=true supplies the complete selected rows for interpretation. '
            'Explicit complete_reading entries remain here across turns; do not query them again merely to see their values. '
            'Use compose_table to join already executed results without regenerating SQL. '
            'A missing preview is not missing evidence. Preserve original scope and all requested outputs. '
            'Avoid repeating successful work; correct unresolved errors and current review feedback. '
            'Return table_from_tool naming the complete composed result; never transcribe patient tables.'}
    loaded = loaded_documentation(calls, documentation_identity)
    if loaded is not None:
        state['loaded_documentation'] = ({'identity':loaded['identity'],
            'sections':[{k:r[k]for k in ('section_id','title','sha256')}for r in loaded['sections']],
            'instruction':'The exact loaded first-party sections appear in the cacheable system documentation block; these are references to that complete text, not patient evidence.'}
            if documentation_in_system else loaded)
    if feedback is not None:
        # Operational reviewer receipts remain native; preserve the exact current
        # repair instructions without resending all model probabilities/costs.
        if isinstance(feedback,dict):
            if feedback_native is not None and feedback_native.get('feedback_digest')!=digest(feedback):
                raise ValueError('Review feedback reference does not match the current receipt')
            visible=('status','reason','repair_kind') if feedback_native is not None else ('status','feedback','reason','repair_kind')
            state['current_review_feedback']={k:feedback[k]for k in visible if k in feedback}
            state['current_review_native_digest']=digest(feedback)
            if feedback_native is not None:
                state['current_review_feedback']['native_feedback']=feedback_native
                if 'feedback'in feedback:state['current_review_feedback']['feedback']=feedback_native['guidance']
            coverage=feedback.get('coverage')or{}
            if coverage:
                state['current_review_feedback']['coverage']={k:coverage[k]for k in ('label','reasons','reject')if k in coverage}
            support=feedback.get('support')or{}
            if support.get('field_review_states'):
                state['current_review_feedback']['field_review_states']=support['field_review_states']
            fields=('id','patient_id','label','reason') if feedback_native is not None else ('id','label','reason','claim_feedback')
            pending=[]
            for packet in support.get('packets',[]):
                if packet.get('label')=='supported'and packet.get('decided_by'):continue
                item={k:packet[k]for k in fields if k in packet}
                if not packet.get('decided_by'):
                    item['label']='unknown'
                    if packet.get('label')!='unknown':item['observed_label']=packet.get('label')
                if feedback_native is not None:item['claim_feedback_entries']=len(packet.get('claim_feedback')or[])
                if isinstance(packet.get('field_review'),dict):
                    item['field_review']={k:packet['field_review'][k]for k in (
                        'schema','authority','packet_digest','manifest_digest','expected_count','supported_count',
                        'unresolved_count','fault_count','all_fields_supported','remaining_obligation',
                        'complete_dependency_accepted','clinical_qualification')if k in packet['field_review']}
                pending.append(item)
            if pending:state['current_review_feedback']['unresolved_dependencies']=pending
            if support.get('failed_packet_ids'):
                state['current_review_feedback']['failed_dependency_ids']=support['failed_packet_ids']
        else:state['current_review_feedback']=feedback
    if focus and focus.get('scope')=='answer':
        state['focused_prose_repair']=focus
        state['instruction']+=' Only the producer-located headline/facts fields are editable now under their exact patch contract; the complete native answer, any entity prose and all row repair obligations remain retained.'
    elif focus:
        # This subset is a repair view, never complete population evidence.
        from .result_assembly import _rows
        # A binding-only edit cannot change its sentence. When every failed
        # sentence for an entity explicitly names known records, present those
        # records in full rather than all unrelated findings for that entity.
        # Uncited fields and source-meaning review keep the broader reading.
        import re
        native={c['result']['result_view']:c['result']for c in calls
            if isinstance(c.get('result'),dict)and c['result'].get('result_view')and not c['result'].get('error')}
        records={};cited={};uncited=set()
        for entry in state['executed_results']:
            reading=entry.get('complete_reading')
            if not isinstance(reading,dict):continue
            try:columns,rows=_rows(reading)
            except ValueError:continue
            if not (set(focus['keys'])|{'record_id'})<=set(columns):continue
            for row in rows:
                if isinstance(row.get('record_id'),str)and row['record_id']:
                    key=digest({k:row[k]for k in focus['keys']})
                    records.setdefault(key,set()).add(row['record_id'])
        if focus.get('intent')!='source_meaning_review':
            for target in focus['targets']:
                key=digest(target['key'])
                try:prose=repair_rendered_value(target['value'],native,basis=target.get('basis'))
                except ValueError:uncited.add(key);continue
                found={record for record in records.get(key,())
                       if re.search(r'(?<![\w-])'+re.escape(record)+r'(?![\w-])',prose)}
                if not found:uncited.add(key)
                cited.setdefault(key,set()).update(found)
        cited={k:v for k,v in cited.items()if k not in uncited}
        for entry in state['executed_results']:
            reading=entry.get('complete_reading')
            if not isinstance(reading,dict):continue
            try:columns,rows=_rows(reading)
            except ValueError:continue  # Preserve incomplete readings and their warnings in full.
            if not set(focus['keys'])<=set(columns):continue
            indices=[i for i,row in enumerate(rows)if digest({k:row[k]for k in focus['keys']})in selected_keys]
            entity_rows=len(indices)
            if 'record_id'in columns and cited:
                indices=[i for i in indices if not isinstance(rows[i].get('record_id'),str)
                    or not rows[i]['record_id']
                    or digest({k:rows[i][k]for k in focus['keys']})not in cited
                    or rows[i]['record_id']in cited[digest({k:rows[i][k]for k in focus['keys']})]]
            entry.pop('complete_reading')
            entry['repair_reading']={'columns':columns,'rows':[rows[i]for i in indices],'native_row_indices':indices,
                'native_total_rows':len(rows),'complete_native_result_retained':True,
                'scope':'Exact rows for the located repair entities only; all other rows remain queryable and in the final answer.'}
            if len(indices)!=entity_rows:
                entry['repair_reading'].update(repair_entity_rows=entity_rows,
                    selection='Explicit record references in every failed binding sentence for the entity; no relevance inference or row cap.',
                    scope='Complete rows for explicitly cited records in binding-only repair. Other records remain in this result_view and are queryable with read_all_rows=true. This is not a source review or a narrowed cohort.')
        state['focused_prose_repair']=focus
        state['instruction']+=' A focused_prose_repair replaces full draft repetition only for the producer-bound repair fields; it never narrows the requested output or source obligations.'
    elif candidate is not None:
        state.update(draft_state(candidate))
    for entry in state['executed_results']:
        for name in ('complete_reading','repair_reading'):
            if name in entry:entry[name]=_column_order_reading(entry[name])
    if focus:
        # These complete producer-bound contexts can repeat entire native
        # definitions and observations across repair entities. Share exact
        # values using the existing reversible evidence encoding, after scope
        # and provenance validation. The saved review and draft stay native.
        from ..deliverables.receipts import dictionary_json,undictionary_json
        projected=dict(focus)
        for name in ('source_contexts','native_source_contexts'):
            original=focus.get(name)
            if not original:continue
            encoded=dictionary_json(original)
            if len(json.dumps(encoded,default=str).encode())>=len(json.dumps(original,default=str).encode()):continue
            if undictionary_json(encoded)!=original:
                raise ValueError('Repair source context did not restore exactly')
            projected[name]=encoded
        state['focused_prose_repair']=projected
    return [{'role':'user','content':json.dumps(state,default=str,allow_nan=False)}]
