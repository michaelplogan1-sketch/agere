"""Shared graph types for planning and execution; semantic validators stay local.

Typed planning contracts cover the measured source-review path. Other operators
retain their explicit legacy JSON wire contract, never an invented generic type.
The runtime validators remain authoritative for dates, bounds and source meaning.
"""
import copy

VERSION = 'graph-operator-contracts/13'
SET_OPS = {'cohort', 'evidence', 'intersect', 'union', 'difference', 'sequence',
           'interpretation', 'source_state', 'pre_event_state', 'requirements', 'outcome',
           'protocol_review', 'program_document', 'functional_review', 'domain_review', 'appointment_followthrough'}
WORK_SUPPORT = ('accountability_review', 'program_acquisition', 'measure_review')
TYPED = {'aggregate', 'explain', 'sql', 'observation_process', 'reference_review', 'cohort', 'evidence', 'intersect', 'union', 'difference', 'compare', 'medication_identity',
         'assertion_review', 'source_state', 'pre_event_state', 'context', 'context_passes', 'functional_review',
         'domain_review', 'worklist', 'program_acquisition', 'retrieve', 'forecast',
         'source_changes', 'review', 'measure_review', 'financial_accounts', 'accountability_review', 'outcome', 'appointment_followthrough', 'interpretation', 'requirements', 'protocol_review', 'program_document'}


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def string(values=None, description=None):
    result = {'type': 'string'}
    if values is not None:
        values=sorted(set(values))
        if values:result['enum'] = values
        else:result['description']='No registered value supplied; leave this operation unused and declare the requirement unmet.'
    if description:
        result['description'] = description
    return result


def array(items, description=None):
    result = {'type': 'array', 'items': items}
    if description:
        result['description'] = description
    return result


def input_contract(op):
    lo, hi = ((0, 0) if op in ('cohort', 'evidence') else
              (2, 2) if op == 'difference' else
              (2, 10) if op in ('intersect', 'union') else
              (0, 1) if op == 'sql' else (1, 10) if op == 'reference_review' else (3, 10) if op == 'compare' else (1, 4) if op == 'worklist' else (1, 1))
    result = {'count': [lo, hi], 'type': 'membership',
              'output': 'membership' if op in SET_OPS else 'report'}
    if op in ('baseline', 'pre_event_state'): result['first_operation'] = 'outcome'
    if op == 'worklist': result['optional_support_reports'] = list(WORK_SUPPORT)
    if op == 'reference_review':
        from .graph_references import EVIDENCE_OPERATIONS
        result['optional_evidence_inputs'] = list(EVIDENCE_OPERATIONS)
    return result


def validate_inputs(op, inputs, seen):
    contract = input_contract(op)
    from .graph_references import EVIDENCE_OPERATIONS
    if any(x not in seen or (seen[x] not in SET_OPS and not
           (op == 'reference_review' and i >= 1 and seen[x] in EVIDENCE_OPERATIONS) and not
           (op == 'worklist' and i >= 1 and seen[x] in WORK_SUPPORT)) for i, x in enumerate(inputs)):
        raise ValueError('Only earlier membership nodes may be inputs; no cycles or result-as-population coercion')
    lo, hi = contract['count']
    if not lo <= len(inputs) <= hi:
        raise ValueError(f'{op} requires {lo}..{hi} typed inputs')
    if op in ('baseline', 'pre_event_state') and seen[inputs[0]] != 'outcome':
        raise ValueError('Baseline/pre-event state requires a direct upstream outcome membership')
    if op == 'reference_review' and any(seen[x] not in EVIDENCE_OPERATIONS for x in inputs[1:]):
        raise ValueError('Reference inputs after the population must be supported evidence receipts')
    if op == 'worklist':
        support = [seen[x] for x in inputs[1:]]
        if any(x not in WORK_SUPPORT for x in support) or len(set(support)) != len(support):
            raise ValueError('Worklist support inputs must be distinct registered review reports')


def parameter_schema(op, catalog):
    """Selected vocabulary shapes, derived from actual runtime registries."""
    from . import cohorts, source_state, functional_programs, domain_programs
    from .clinical_events import WINDOW_SCHEMA
    streams = sorted(catalog['cohort_fields']['record_fields'])
    evidence = catalog['evidence_catalog']
    stream_list = array(string(catalog.get('source_streams', streams)), '1..30 distinct explicitly loaded source streams; never widen scope.')
    stream_list['minItems']=1
    if op == 'aggregate':
        from . import aggregate, concepts as library
        return obj({'measure': string(aggregate.STATISTICS),
            'concept': string(['', *library.CONDITIONS, *library.MEASUREMENTS, *library.ENCOUNTERS],
                              'What is counted/averaged. patients: any concept or "" (population size); events: encounter or condition concept; value: measurement concept.'),
            'statistic': string(sorted({s for v in aggregate.STATISTICS.values() for s in v}),
                                'patients: count|share; events: count|rate_per_1000; value: mean|median'),
            'group_by': string(aggregate.GROUP_BY), 'adjust': string(aggregate.ADJUSTMENTS, 'Case-mix standardisation for counts/shares'),
            'window': obj({'start': {'type': 'string', 'description': 'YYYY-MM-DD or empty'}, 'end': {'type': 'string', 'description': 'YYYY-MM-DD or empty'}}),
            'value_aggregate': string(['latest', 'all']), 'top': {'type': 'integer', 'minimum': 1, 'maximum': 200}})
    if op == 'sql':
        from . import sql_query
        return obj({'query': {'type': 'string', 'description': 'One read-only SELECT/WITH over the semantic_layer tables. Reference the upstream population (if an input is given) as table population(patient_id).'},
                    'returns': string(sql_query.RETURNS, 'count/value: first column of first row; patient_set: first column = patient_id; table: all rows')})
    if op == 'explain':
        from . import explain, concepts as library
        return obj({'outcome': obj({'type': string(explain.OUTCOME_TYPES),
                'concept': string(['', *library.CONDITIONS, *library.MEASUREMENTS, *library.ENCOUNTERS], 'Outcome concept for concept/measurement types; "" for readmission'),
                'op': string(['', 'lt', 'le', 'gt', 'ge'], 'measurement outcomes only'), 'value': {'type': 'number'},
                'aggregate': string(['latest', 'any'])}),
            'window': obj({'start': {'type': 'string'}, 'end': {'type': 'string'}})})
    if op == 'observation_process':
        return obj({'stream':string(catalog.get('source_streams',streams)),
            'months':{'type':'integer','minimum':2,'maximum':24},
            'available_by':{'type':'string','format':'date'}})
    if op == 'reference_review':
        refs=evidence.get('scientific_references',[])
        return obj({'reference_id':string(r['id'] for r in refs),'available_by':{'type':'string','format':'date'},
            'bindings':array(obj({'question_key':string(q['key'] for r in refs for q in r['questions']),
                'node_ids':{**array(string(), 'Earlier evidence input IDs; empty explicitly means no linked evidence. Context is not an applicability judgment.'),'maxItems':9,'uniqueItems':True}}))})
    if op == 'accountability_review':
        from .program_accountability import definitions
        return obj({'contract':string(definitions()),'available_by':{'type':'string','format':'date'}})
    if op == 'outcome':
        from .outcomes import FILTERS
        contracts=evidence.get('outcome_contracts',[])
        ids=([r[contracts['columns'].index('id')] for r in contracts['rows']]
             if isinstance(contracts,dict) else [r['id'] for r in contracts])
        return obj({'contract':string(ids),
                    **{k:{'type':'string','format':'date'} for k in ('index_from','index_through','available_by')},
                    'filter':string(FILTERS)})
    if op == 'requirements':
        from .evidence_requirements import FILTERS
        return obj({'contract':string(r['id'] for r in evidence.get('documentary_contracts',[])), 'filter':string(FILTERS)})
    if op == 'protocol_review':
        from .care_protocols import FILTERS
        return obj({'protocol':string(evidence.get('protocol_versions',[]), 'Exact registered id@version; current unversioned aliases are accepted by runtime but explicit versions preserve the requested rule.'), 'filter':string(FILTERS)})
    if op == 'program_document':
        from .program_documents import FILTERS
        return obj({'package':string(r['id'] for r in evidence.get('program_documents',[])), 'filter':string(FILTERS)})
    if op == 'cohort':
        spec=copy.deepcopy(cohorts.SCHEMA)
        from . import concepts as concept_library
        for prop in spec['properties'].values():
            patient,record,concept,measurement=prop['items']['anyOf']
            patient['properties']['field']=string(cohorts.PATIENT_FIELDS)
            concept['properties']['concept']=string([*concept_library.CONDITIONS,*concept_library.MEASUREMENTS,*concept_library.ENCOUNTERS])
            measurement['properties']['concept']=string(concept_library.MEASUREMENTS)
            if streams:record['properties']['stream']=string(streams)
            else:prop['items']={'anyOf':[patient,concept,measurement]}
        return obj({'specification':spec})
    if op == 'evidence':
        from .evidence_queries import FIELDS
        props={'source':string(['text','structured']), 'concept':{'type':'string','minLength':1,'maxLength':120},
            'observation':string(['present','not_observed']), 'concept_match':string(['equals','prefix']),
            'value_equals':{'anyOf':[{'type':'string','maxLength':120},{'type':'boolean'}]},
            'event_date_from':{'type':'string','format':'date'},'event_date_through':{'type':'string','format':'date'}}
        for key in sorted(FIELDS-{'concept'}):props[key]=string(evidence.get('text_axis_values',{}).get(key))
        clause={'type':'object','properties':props,'required':['source','concept'],'additionalProperties':False}
        clauses={'type':'array','items':clause,'minItems':1,'maxItems':8,
            'description':'An empty evidence query is invalid. If no mapped concept supports the request, omit this node and declare the unmet requirement.'}
        return obj({'plan':obj({'match':string(['all','any']),'clauses':clauses})})
    if op in ('intersect', 'union', 'difference', 'compare', 'medication_identity'):
        return obj({})
    if op == 'assertion_review':
        concepts=array(string(evidence['assertion_concepts']), '1..20 distinct mapped concepts required. Empty does not mean all concepts.')
        concepts['minItems']=1
        return obj({'streams': stream_list, 'concepts': concepts})
    if op == 'interpretation':
        from .interpretation_state import FILTERS
        schema=parameter_schema('assertion_review',catalog)
        schema['properties']['filter']=string(FILTERS)
        schema['required'].append('filter')
        return schema
    if op in ('source_state', 'pre_event_state'):
        concepts=[c for c in evidence['assertion_concepts'] if
            (c.split(' ',1)[0] if c.startswith(('medication ','care ')) else c) in source_state.RELATIONS]
        return obj({'streams':stream_list,'concept':string(concepts),
            'relation':string([r for values in source_state.RELATIONS.values() for r in values],
                'Must match the concept family in the supplied source_state relation contract; runtime checks the pair.'),
            'clock':string(source_state.CLOCKS),'filter':string(source_state.FILTERS)})
    if op in ('context', 'context_passes'):
        from .state_contracts import CONTRACTS as state_contracts
        return obj({'question': string(description='Exact bounded source question, 1..1500 characters.'),
            'streams': stream_list, 'concepts': array(string(), '0..20 exact supplied narrative concepts; [] means unfiltered.'),
            'contract': string(['', *state_contracts],
                'Optional clinical evidence contract; use empty string for an open source question. Never use a graph/schema version or invent a contract.'),
            'program_ids': array(string(r['id'] for r in evidence.get('program_documents', [])),
                '0..5 supplied imported package hashes; [] when no quoted program is requested.'),
            'max_bytes': {'type': 'integer', 'description': '2048..60000 UTF-8 bytes; omissions must remain explicit.'}})
    if op == 'functional_review':
        return obj({'filter': string(functional_programs.FILTERS)})
    if op == 'domain_review':
        return obj({'domain': string(domain_programs.PROGRAMS.values()), 'filter': string(domain_programs.FILTERS)})
    if op == 'worklist':
        return obj({'program': string(r['id'] for r in evidence['work_programs'])})
    if op == 'program_acquisition':
        return obj({'package': string(r['id'] for r in evidence['program_documents'])})
    if op == 'appointment_followthrough':
        from .appointment_graph import FILTERS
        cutoff={'type':'string','format':'date','minLength':10,'maxLength':10,
            'description':'Literal YYYY-MM-DD receipt cutoff, no later than snapshot.as_of.'}
        return obj({'available_by':cutoff,'filter':string(FILTERS)})
    if op == 'financial_accounts':
        cutoff=copy.deepcopy(WINDOW_SCHEMA['properties']['start'])
        cutoff.update(type='string',format='date',minLength=10,maxLength=10,
            description='Literal YYYY-MM-DD receipt cutoff, no later than snapshot.as_of. Use that actual date when no earlier cutoff is requested; never the text snapshot as_of.')
        return obj({'available_by':cutoff})
    if op == 'measure_review':
        components={}
        for role in ('denominator','numerator','exclusion','attribution'):
            packages=[r['id'] for r in evidence.get('program_documents',[]) if r.get('role')==role]
            package=obj({'package': string(packages,
                'Exact supplied package of this component role, with a definition relevant to the question. Unrelated packages cannot substitute for missing definitions.')})
            if not packages:
                # Empty enums are invalid provider schemas. A false schema makes
                # the impossibility explicit without authorizing an invented ID.
                package['properties']['package']={'not':{}}
            if role in ('denominator','numerator'):
                components[role]=package
            else:
                components[role]={'anyOf':[package,obj({'unbound':string()}),obj({'not_applied':string()})]}
        result=obj({'title':string(),'components':obj(components)})
        result['description']='Denominator and numerator require relevant quoted packages. If either is unavailable, omit this operation and declare that requirement unmet; preserve other supported operations.'
        return result
    if op == 'retrieve':
        return obj({'query': string(), 'analysis_window': copy.deepcopy(WINDOW_SCHEMA)})
    if op == 'forecast':
        return obj({'model': string(['all', 'logistic', 'boosted', 'boosted_blocks'])})
    if op == 'source_changes':
        return obj({'streams': array(string(streams), '1..5 distinct loaded source streams.')})
    if op == 'review':
        return obj({'providers': array(string(['jev', 'perplexity']), '1..2 distinct providers.'),
                    'question': string(), 'maximum_patients': {'type': 'integer', 'description': '1..3'}})
    return None


def validate_parameters(op, p):
    """Reuse the same semantic validators as direct graph execution."""
    from . import assertion_reconciliation, source_state, context_builder, functional_programs, domain_programs, evidence_queries
    if op=='evidence':
        if not isinstance(p,dict) or set(p)!={'plan'}:raise ValueError('Evidence node needs its explicit plan')
        return {'plan':evidence_queries.validate_plan(p['plan'])}
    validator = {'assertion_review': assertion_reconciliation.validate, 'source_state': source_state.validate, 'pre_event_state': source_state.validate,
                 'context': context_builder.validate, 'context_passes': context_builder.validate,
                 'functional_review': functional_programs.validate, 'domain_review': domain_programs.validate}.get(op)
    return validator(p) if validator else p
