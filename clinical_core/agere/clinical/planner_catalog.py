"""Lossless factoring of the planner vocabulary; no selected fields are dropped."""
import copy
import json
from collections import Counter
import yaml


def identifier_groups(values):
    prefixes=Counter(v.partition('_')[0]+'_' for v in values if '_' in v)
    selected={p for p,n in prefixes.items() if n>1 and len(p)*(n-1)>12}
    groups={}
    for value in values:
        prefix=value.partition('_')[0]+'_' if '_' in value else ''
        prefix=prefix if prefix in selected else ''
        groups.setdefault(prefix,[]).append(value[len(prefix):])
    return groups


def factor(catalog):
    data=copy.deepcopy(catalog)
    fields=data['cohort_fields']['record_fields']
    if fields and all(isinstance(v,dict) for v in fields.values()):
        # Grounded fields carry observed vocabularies per stream; keep them intact.
        common=[]
        data['cohort_fields']['record_fields']={'common_to_all_streams':common,'additional_by_stream':fields}
    else:
        common=sorted(set.intersection(*(set(v) for v in fields.values()))) if fields else []
        data['cohort_fields']['record_fields']={
            'common_to_all_streams':common,
            'additional_by_stream':{k:[x for x in v if x not in common] for k,v in fields.items()}}
    longitudinal=data['evidence_catalog']['longitudinal']
    if longitudinal.get('available'):
        events=set(longitudinal.pop('event_types'));streams=set(longitudinal['streams'])
        longitudinal['event_types']={'streams_minus':sorted(streams-events),'plus':sorted(events-streams)}
        longitudinal['streams']={'cohort_streams_minus':sorted(set(fields)-streams),'plus_by_prefix':identifier_groups(sorted(streams-set(fields)))}
    contracts=data['node_contracts']
    if contracts and all(v['parameters']=={} for v in contracts.values()):
        data['node_contracts']={'shared':{'parameters':{}},'columns':['operation','meaning','inputs'],
            'rows':[[op,v['meaning'],v.get('inputs',['earlier_set'])] for op,v in contracts.items()]}
    else:
        data['node_contracts']={'columns':['operation','parameters','meaning','inputs'],
            'rows':[[op,v['parameters'],v['meaning'],v.get('inputs',['earlier_set'])] for op,v in contracts.items()]}
    concepts=data['evidence_catalog']['concepts'];groups={}
    for value in concepts.pop('structured'):
        prefix,sep,suffix=value.partition(':')
        groups.setdefault(prefix+sep if sep else '',[]).append(suffix if sep else value)
    concepts['structured_by_prefix']=groups
    for key in ('documentary_contracts','care_protocols','baseline_profiles','landmark_contracts','program_documents'):
        rows=data['evidence_catalog'].get(key)
        if rows and isinstance(rows,list) and all(isinstance(row,dict) and set(row)==set(rows[0]) for row in rows):
            columns=list(rows[0])
            data['evidence_catalog'][key]={'columns':columns,'rows':[[row[col] for col in columns] for row in rows]}
    return data


def serialize(catalog, *, source_names_in_schema=None):
    data=factor(catalog)
    if source_names_in_schema is not None:
        fields=data['cohort_fields']['record_fields']
        if fields['common_to_all_streams'] or any(fields['additional_by_stream'].values()) or set(fields['additional_by_stream'])!=set(source_names_in_schema):
            raise ValueError('Only exactly deferred empty field contracts can refer to the output schema')
        fields['additional_by_stream']={'names':'See output_config.format.schema segments.items source_streams.items.enum; exact complete names are supplied there.',
                                        'fields':'Deferred; no empty field contract is asserted.'}
    # Share repeated values with ordinary YAML anchors. Expansion must recover
    # every literal field and contract, including punctuation in source strings.
    counts=Counter()
    def count(value):
        if isinstance(value,str):counts[value]+=1
        elif isinstance(value,dict):
            for k,v in value.items():count(k);count(v)
        elif isinstance(value,list):
            for v in value:count(v)
    count(data)
    strings={s:s for s,n in counts.items() if n>1 and (len(s)-5)*(n-1)>9}
    sequences={};mappings={}
    def shared(value):
        if isinstance(value,str):return strings.get(value,value)
        if isinstance(value,dict):
            result={shared(k):shared(v) for k,v in value.items()}
            key=json.dumps(result,sort_keys=True,ensure_ascii=False)
            return mappings.setdefault(key,result) if len(key)>30 else result
        if isinstance(value,list):
            result=[shared(v) for v in value]
            if result and all(isinstance(v,str) for v in result):
                key=tuple(result)
                if sum(map(len,result))>10:return sequences.setdefault(key,result)
            return result
        return value
    class CompactDumper(yaml.SafeDumper):
        def ignore_aliases(self,value):
            if isinstance(value,str) and value in strings:return False
            return super().ignore_aliases(value)
        def generate_anchor(self,node):
            self.last_anchor_id+=1
            return format(self.last_anchor_id,'x')
    text=yaml.dump(shared(data),Dumper=CompactDumper,default_flow_style=True,sort_keys=False,allow_unicode=True,width=1000000)
    # Only remove separator whitespace identified by the YAML lexer; never
    # alter commas/spaces inside source values or explanatory sentences.
    positions=[t.end_mark.index for t in yaml.scan(text) if isinstance(t,yaml.tokens.FlowEntryToken)]
    for pos in reversed(positions):
        if text[pos:pos+1]==' ':text=text[:pos]+text[pos+1:]
    if yaml.safe_load(text)!=data:raise ValueError('Planner catalog serialization changed values')
    return ('YAML: fields=common+additions; streams=cohort record-field keys-cohort_streams_minus+plus_by_prefix; '
            'event types=streams-streams_minus+plus; prefix groups join prefix+suffix. Table shared fields apply to every row. Aliases reuse exact values.\n'+text)
