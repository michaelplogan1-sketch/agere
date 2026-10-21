"""Portable clinical input reductions, distinct from diagnosis or treatment.

Structured mappings live separately from rule evaluation. Existing narrative EF
extractions are reused with their original authority and exact source spans; no
new regex reader or unvalidated model is silently promoted here.
"""
from collections import defaultdict
from pathlib import Path
import hashlib
import json
import math

from ...contracts import digest
from .clinical_events import iso
from . import clinical_state, longitudinal, source_structure

VERSION='cross-source-clinical-inputs/1'


def mappings():
    raw=json.loads(Path(__file__).with_name('clinical_input_maps.json').read_text())
    if raw['schema']!='clinical-input-maps/1':raise ValueError('Unknown clinical input adapter')
    return raw


def identity():
    from .clinical_events import code_identity
    return digest([*[hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in
        (__file__,Path(__file__).with_name('clinical_input_maps.json'),clinical_state.__file__)],
        source_structure.identity(),longitudinal.code_identity(),code_identity()])


def measurement_rows(patient, payloads, concept, as_of):
    """SQLite binding of the existing literal mapper, without a new reader."""
    adapter=mappings()
    if concept not in adapter['measurements']:raise ValueError('Unknown measurement mapping')
    adapter['measurements']={concept:adapter['measurements'][concept]}
    records=[json.loads(p) if isinstance(p,str) else p for p in json.loads(payloads)]
    return json.dumps(measurements(patient,records,[],as_of,adapter).get(concept,[]),allow_nan=False)


def register(db):
    db.create_function('clinical_measurements',4,measurement_rows)
    return db


def measurement_sql(concept, as_of, *, note_facts=False):
    """Observations, not a current diagnosis. Never substitute a document date.

    Mapped structured values reuse measurements(). Existing narrative extraction
    is retained only as a source-review candidate: its numeric value can be a
    range midpoint, and is never exposed as a literal measured lower/upper value.
    """
    if not iso(as_of):raise ValueError('Explicit measurement cutoff required')
    spec=mappings()['measurements'].get(concept)
    if spec is None:raise ValueError('Unknown measurement mapping')
    streams={s['stream'] for s in spec['sources']}
    for source in spec['sources']:
        for linked in source.get('dependencies',{}).values():streams.update(linked)
    if 'labs' in streams:streams.add('laboratory_specimens')
    quote=lambda value:"'"+value.replace("'","''")+"'"
    structured=("SELECT r.patient_id,json_extract(m.value,'$.id') AS observation_id, "
        f"{quote(concept)} AS concept,json_extract(m.value,'$.lower') AS lower,json_extract(m.value,'$.upper') AS upper, "
        "json_extract(m.value,'$.unit') AS unit,json_extract(m.value,'$.source_date') AS source_date, "
        "json_extract(m.value,'$.arrival_date') AS arrival_date,json_extract(m.value,'$.clinical_date') AS clinical_date, "
        "json_extract(m.value,'$.authority') AS authority,json_extract(m.value,'$.semantic_status') AS semantic_status, "
        "json_extract(m.value,'$.temporality') AS temporality,json_extract(m.value,'$.exclusion') AS exclusion, "
        "NULL AS candidate_value,NULL AS evidence_text,m.value AS evidence, "
        f"{int(note_facts and concept=='lvef')} AS narrative_scan_available,0 AS current_state_established "
        "FROM (SELECT patient_id,json_group_array(payload) AS payloads FROM record "
        f"WHERE stream IN ({','.join(quote(s) for s in sorted(streams))}) "
        f"AND (statement_day(json_extract(payload,'$.available_date')) IS NULL OR "
        f"statement_day(json_extract(payload,'$.available_date'))<='{as_of}') GROUP BY patient_id) r, "
        f"json_each(clinical_measurements(r.patient_id,r.payloads,{quote(concept)},'{as_of}')) m")
    if not note_facts or concept!='lvef':return '('+structured+')'
    narrative=("SELECT n.patient_id,'candidate:'||n.record_id||':'||coalesce(n.source_field,'')||':'||coalesce(n.span_start,'') AS observation_id, "
        "'lvef' AS concept,NULL AS lower,NULL AS upper,n.unit,n.event_date AS source_date, "
        "statement_day(json_extract(r.payload,'$.available_date')) AS arrival_date,NULL AS clinical_date, "
        "'unreviewed_narrative_extraction' AS authority,'interpretation_required' AS semantic_status,n.temporality, "
        "CASE WHEN r.record_id IS NULL THEN 'source_record_unavailable' "
        "WHEN json_extract(r.payload,'$.patient_id') IS NOT n.patient_id OR json_extract(r.payload,'$.synthetic') IS NOT 1 "
        "OR json_extract(r.payload,'$.identity_status') IS NOT 'matched' THEN 'source_identity_unresolved' "
        "WHEN statement_day(json_extract(r.payload,'$.available_date')) IS NULL THEN 'source_availability_unresolved' "
        "WHEN n.source_field IS NULL OR n.span_start IS NULL OR n.span_end IS NULL OR n.span_start<0 OR n.span_end<=n.span_start "
        "OR substr(json_extract(r.payload,'$.'||n.source_field),n.span_start+1,n.span_end-n.span_start) IS NOT n.evidence_text "
        "THEN 'source_span_unverified' ELSE 'unreviewed_extracted_value' END AS exclusion, "
        "n.value AS candidate_value,n.evidence_text,json_object('record_id',n.record_id,'source_table',n.stream, "
        "'source_field',n.source_field,'span_start',n.span_start,'span_end',n.span_end,'evidence_text',n.evidence_text, "
        "'extractor',n.extractor,'negated',n.negated,'experiencer',n.experiencer,'temporality',n.temporality, "
        "'qualifier',n.qualifier,'candidate_value',n.value,'unit',n.unit) AS evidence,1 AS narrative_scan_available,0 AS current_state_established "
        "FROM note_fact n LEFT JOIN record r ON r.record_id=n.record_id AND r.patient_id=n.patient_id "
        "WHERE n.fact_type='ejection_fraction' "
        f"AND (statement_day(n.event_date) IS NULL OR statement_day(n.event_date)<='{as_of}') "
        f"AND (statement_day(json_extract(r.payload,'$.available_date')) IS NULL OR statement_day(json_extract(r.payload,'$.available_date'))<='{as_of}')")
    return '('+structured+' UNION ALL '+narrative+')'


def care_team_sql(as_of):
    """Latest documentary relationship per patient, clinician and role.

    Explicit assignment and inclusive effective dates are required. A referral,
    an encounter, or a payer attribution is not an assigned care relationship.
    Multiple clinicians remain multiple rows; no ID-based winner is selected.
    """
    if not iso(as_of):raise ValueError('Explicit care-team cutoff required')
    return ("(WITH relationship_sources AS (SELECT patient_id,record_id, "
            "json_extract(payload,'$.clinician_id') AS clinician_id,json_extract(payload,'$.role') AS role, "
            "json_extract(payload,'$.organization_id') AS organization_id, "
            "lower(trim(json_extract(payload,'$.status'))) AS reported_status, "
            "statement_day(json_extract(payload,'$.event_date')) AS source_date, "
            "statement_day(json_extract(payload,'$.available_date')) AS available_date, "
            "json_extract(payload,'$.effective_start') AS start_a,json_extract(payload,'$.relationship_start') AS start_b, "
            "json_extract(payload,'$.effective_end') AS end_a,json_extract(payload,'$.relationship_end') AS end_b, "
            "CASE WHEN json_type(payload,'$.clinician_id') IS NOT 'text' OR json_type(payload,'$.role') IS NOT 'text' OR "
            "(json_extract(payload,'$.patient_id') IS NOT NULL AND json_extract(payload,'$.patient_id')!=patient_id) "
            "THEN 1 ELSE 0 END AS identity_mismatch "
            "FROM record WHERE stream='care_team_relationships'), "
            "dated_relationships AS (SELECT *,statement_day(COALESCE(start_a,start_b)) AS effective_start, "
            "statement_day(COALESCE(end_a,end_b)) AS effective_end, "
            "CASE WHEN (start_a IS NOT NULL AND start_b IS NOT NULL AND start_a!=start_b) OR "
            "(end_a IS NOT NULL AND end_b IS NOT NULL AND end_a!=end_b) OR "
            "(COALESCE(start_a,start_b) IS NOT NULL AND statement_day(COALESCE(start_a,start_b)) IS NULL) OR "
            "(COALESCE(end_a,end_b) IS NOT NULL AND statement_day(COALESCE(end_a,end_b)) IS NULL) OR "
            "(statement_day(COALESCE(end_a,end_b))<statement_day(COALESCE(start_a,start_b))) THEN 1 ELSE 0 END AS interval_issue "
            f"FROM relationship_sources WHERE (source_date IS NULL OR source_date<='{as_of}') "
            f"AND (available_date IS NULL OR available_date<='{as_of}')), "
            "ranked_relationships AS (SELECT *, "
            "max(source_date IS NULL OR available_date IS NULL) OVER(PARTITION BY patient_id,clinician_id,role) AS undated, "
            "rank() OVER(PARTITION BY patient_id,clinician_id,role ORDER BY source_date DESC) AS latest "
            "FROM dated_relationships), "
            "relationship_state AS (SELECT patient_id,clinician_id,role,max(source_date) AS source_date, "
            "min(organization_id) AS organization_id,min(reported_status) AS reported_status, "
            "min(effective_start) AS effective_start,min(effective_end) AS effective_end, "
            "json_group_array(record_id) AS source_record_ids, "
            "CASE WHEN typeof(clinician_id)!='text' OR trim(clinician_id)='' OR typeof(role)!='text' OR trim(role)='' "
            "OR max(identity_mismatch)=1 THEN 'unknown_identity' "
            "WHEN max(undated)=1 THEN 'unknown_date' WHEN max(interval_issue)=1 THEN 'invalid_interval' "
            "WHEN count(DISTINCT json_array(reported_status,effective_start,effective_end,organization_id))>1 THEN 'conflicting' "
            "WHEN min(reported_status) IN ('ended','inactive','discontinued','removed','unassigned') THEN 'ended_documentation' "
            "WHEN min(reported_status) IN ('referred','referral_received') THEN 'referral_only' "
            "WHEN min(reported_status) IS NULL OR min(reported_status) NOT IN ('assigned','active') THEN 'unknown_status' "
            "WHEN min(effective_start) IS NULL THEN 'unknown_start' "
            f"WHEN min(effective_start)>'{as_of}' THEN 'not_started' "
            f"WHEN min(effective_end)<'{as_of}' THEN 'ended_documentation' "
            "ELSE 'documented_assignment' END AS assignment_status "
            "FROM ranked_relationships WHERE latest=1 OR undated=1 GROUP BY patient_id,clinician_id,role) "
            "SELECT *,CASE WHEN assignment_status='documented_assignment' THEN 1 "
            "WHEN assignment_status IN ('referral_only','ended_documentation','not_started') THEN 0 END AS assigned_at_snapshot, "
            "0 AS current_responsibility_verified FROM relationship_state)")


def number(value):
    if type(value) not in (int,float,str) or isinstance(value,bool):return None
    try:n=float(value)
    except (ValueError,TypeError):return None
    return n if math.isfinite(n) and n>=0 else None


def field_value(record,path):
    """Literal dotted source path; no expression evaluation or inferred aliases."""
    value=record
    for part in path.split('.'):
        if not isinstance(value,dict) or part not in value:return None
        value=value[part]
    return value


def measurements(patient,records,analyses,as_of,adapter=None):
    """Exact source observations plus unchanged, explicitly unreviewed proposals."""
    if not iso(as_of):raise ValueError('Explicit clinical input cutoff required')
    adapter=adapter or mappings();by_id={r['record_id']:r for r in records};result=defaultdict(list)
    from .warehouse_interpretation import index,native,laboratory_status
    from .clinical_events import bind_event
    related=index(records)
    if len(by_id)!=len(records):raise ValueError('Duplicate clinical source ID')
    if any(r['patient_id']!=patient or r.get('synthetic') is not True or r.get('identity_status')!='matched'
           or not iso(r.get('available_date')) or r['available_date']>as_of for r in records):
        raise ValueError('Clinical inputs require matched synthetic sources received by cutoff')
    for concept,spec in adapter['measurements'].items():
        for r in records:
            for s in spec['sources']:
                if r['source_table']!=s['stream']:continue
                if s.get('required_adapter') and r.get('_warehouse_source',{}).get('adapter')!=s['required_adapter']:continue
                if any(field_value(r,k)!=v for k,v in s.get('where',{}).items()):continue
                if s['names'] and not {str(r.get(k,'')).lower() for k in ('test','code')} & set(s['names']):continue
                raw_value=field_value(r,s['value_field']);v=number(raw_value)
                raw_unit=s['unit_literal'] if 'unit_literal' in s else field_value(r,s['unit_field'])
                unit=str(raw_unit or '').lower().replace(' ','')
                conversion=spec['units'].get(unit);reason=None
                status=field_value(r,s['status_field']) if s.get('status_field') else laboratory_status(r)
                if status not in s.get('accepted_status',('final','corrected','amended')):reason='unfinalized_result'
                if v is None:reason=reason or 'invalid_numeric_value'
                if v is not None and s.get('valid_range') and not s['valid_range'][0]<=v<=s['valid_range'][1]:reason=reason or 'numeric_value_outside_source_range'
                if s.get('quality_field') and field_value(r,s['quality_field']) not in s['accepted_quality']:reason=reason or 'measurement_quality_unresolved'
                if conversion is None:reason=reason or 'unit_not_mapped'
                linked=[];link_issues=[]
                for field,streams in s.get('dependencies',{}).items():
                    target=by_id.get(r.get(field));issue=None
                    if target is None:issue='referenced_source_not_received' if r.get(field) else 'source_reference_not_provided'
                    elif target['source_table'] not in streams:issue='wrong_linked_source_type'
                    if issue:link_issues.append({'field':field,'record_id':r.get(field),'reason':issue})
                    else:linked.append({'field':field,'record_id':target['record_id'],'patient_id':patient,'source_table':target['source_table'],'record_hash':digest(target)})
                if link_issues:reason=reason or 'unresolved_measurement_dependency'
                when={k:r[k] for k in s['date_fields'] if r.get(k) is not None}
                valid=when and all(iso(v) and v<=as_of for v in when.values()) and len(set(when.values()))==1
                clock_evidence=[];clinical=next(iter(when.values())) if valid else None
                if native(r):
                    binding=bind_event(r,as_of,related);when=binding['clinical_date_fields']
                    valid=binding['clinical_date_status']=='explicit_date';clinical=binding['clinical_date'] if valid else None
                    clock_evidence=binding['clinical_date_evidence']
                    if binding['clock_link_issues']:reason=reason or 'unresolved_clock_dependency'
                if when and not valid:reason=reason or 'conflicting_or_invalid_clinical_date'
                value=v*conversion if v is not None and conversion is not None else None
                row={'patient_id':patient,'concept':concept,'lower':value,'upper':value,'unit':spec['unit'],
                    'authority':'literal_structured_field','semantic_status':'literal_mapping',
                    'subject':'patient','assertion':'affirmed','temporality':'unspecified','copied_history':False,
                    'source_date':iso(r.get('event_date')),'arrival_date':r['available_date'],
                    'clinical_date':clinical,'reference_date':None,
                    'clinical_date_fields':when,'clinical_date_evidence':clock_evidence,'exclusion':reason,
                    'linked_sources':linked,'link_issues':link_issues,
                    'evidence':[{'record_id':r['record_id'],'patient_id':patient,'source_table':r['source_table'],
                        'record_hash':digest(r),'field':s['value_field'],'value':raw_value,
                        'unit_field':s.get('unit_field'),'raw_unit':raw_unit,
                        'unit_authority':'explicit_adapter_field_unit' if 'unit_literal' in s else 'received_unit_field',
                        'conversion_factor':conversion,'source_date':r.get('event_date'),'arrival_date':r['available_date']}]}
                row['id']=digest([VERSION,row]);result[concept].append(row)
    facts=clinical_state.bound_facts(analyses,records,patient)
    from ..narrative_sources import text_fields
    for concept,spec in adapter['measurements'].items():
        for fact in facts.get(spec['fact_concept'],[]) if spec['fact_concept'] else []:
            value=fact['value'];lo=number(value.get('lower')) if isinstance(value,dict) else None
            hi=number(value.get('upper')) if isinstance(value,dict) else None
            if hi is None:hi=lo
            reason=None
            if lo is None or hi is None or hi<lo or not isinstance(value,dict) or value.get('unit')!=spec['unit']:reason='unusable_extracted_value'
            refs=[];dates=[];structures=[]
            for ev in fact['source']:
                r=by_id[ev['record_id']];quote=ev.get('quote')
                if quote is not None:
                    raw=next(t['text'] for t in text_fields(r) if t['field']==ev['field'])
                    ref=longitudinal.narrative_reference(raw,ev['start'],ev['end'],as_of)
                    dates.append(ref);structures.append(source_structure.locate(raw,ev['start'],ev['end']))
                refs.append({**ev,'patient_id':patient})
            excluded_roles=structures and all(s['clinical_account_excluded'] for s in structures)
            if excluded_roles:reason='nonclinical_source_role'
            if fact['subject'] not in ('patient','patient_context'):reason=reason or 'other_or_unknown_subject'
            if fact['assertion']!='affirmed':reason=reason or 'not_affirmative_measurement'
            known_refs={r['reference_date'] for r in dates if r.get('reference_date')}
            row={'patient_id':patient,'concept':concept,'lower':lo,'upper':hi,'unit':spec['unit'],
                 'authority':fact['authority'],'semantic_status':'unreviewed_numeric_interpretation',
                 'subject':fact['subject'],'assertion':fact['assertion'],'temporality':fact['temporality'],
                 'copied_history':any(r.get('copied_history') for r in dates),
                 'source_date':iso(fact['source_date']),'arrival_date':fact['arrival_date'],
                 'clinical_date':fact.get('clinical_date'),
                 'reference_date':next(iter(known_refs)) if len(known_refs)==1 else None,
                 'reference_details':dates,'source_roles':structures,'exclusion':reason,'evidence':refs,
                 'fact_id':fact['fact_id']}
            row['id']=digest([VERSION,row]);result[concept].append(row)
    return dict(result)


def interval_test(lower,upper,op,threshold):
    """Three-valued predicate over a recorded range, never its midpoint."""
    if lower is None or upper is None:return None
    if op=='lt':return True if upper<threshold else False if lower>=threshold else None
    if op=='le':return True if upper<=threshold else False if lower>threshold else None
    if op=='gt':return True if lower>threshold else False if upper<=threshold else None
    if op=='ge':return True if lower>=threshold else False if upper<threshold else None
    raise ValueError('Unsupported measurement comparator')


def measurement_binding(observations,binding,as_of):
    clock={'source_statement_date':'source_date','explicit_clinical_date':'clinical_date'}[binding['clock']]
    usable=[];pending=[];excluded=[]
    for row in observations:
        if row['exclusion']:
            # Only affirmatively irrelevant assertions can be discarded. An
            # unusable result may be the newest observation; do not fall back
            # silently to an older normal (or abnormal) result.
            target=excluded if row['exclusion'] in ('nonclinical_source_role','other_or_unknown_subject','not_affirmative_measurement') else pending
            target.append({'observation_id':row['id'],'reason':row['exclusion']});continue
        day=row.get(clock)
        # Historical values can nominate a history review, never refresh a
        # current measurement merely because a copied note arrived recently.
        if binding['selection']=='latest' and (row['temporality'] in ('historical','resolved') or row['copied_history']):
            excluded.append({'observation_id':row['id'],'reason':'historical_or_copy_not_current_measurement'});continue
        if not iso(day) or day>as_of:
            pending.append({'observation_id':row['id'],'reason':'selected_clock_unresolved'});continue
        usable.append((day,row))
    latest=max((d for d,_ in usable),default=None)
    selected=[r for d,r in usable if binding['selection']=='any_history' or d==latest]
    signatures={(r['lower'],r['upper'],r['unit']) for r in selected}
    conflict=binding['selection']=='latest' and len(signatures)>1
    tests=[interval_test(r['lower'],r['upper'],binding['op'],binding['value']) for r in selected]
    literal=(True if True in tests else None if pending or not tests or None in tests else False) if binding['selection']=='any_history' else (
        None if conflict or pending or not tests else tests[0])
    unreviewed=[r['id'] for r in selected if r['authority']!='literal_structured_field']
    accepted=[None if r['id'] in unreviewed else t for r,t in zip(selected,tests)]
    result=(True if True in accepted else None if pending or not accepted or None in accepted else False) if binding['selection']=='any_history' else (
        None if pending or conflict or unreviewed else literal)
    if binding.get('expected','match')=='known':
        result=True if result is not None else None
        literal=True if literal is not None else None
    # The literal comparison only nominates source/clinical review. It does not
    # bypass missing dates or machine interpretation to make a treatment decision.
    return {'value':result,'literal_signal':literal,'status':'conflicting' if conflict else
        'documented' if result is not None else 'interpretation_required' if unreviewed else 'input_unresolved' if pending else
        'range_indeterminate' if tests else 'historical_or_excluded' if excluded else 'unobserved',
        'selected_date':latest,'clock':binding['clock'],'selection':binding['selection'],
        'selected_observations':selected,'pending':pending,'excluded':excluded,
        'unreviewed_observation_ids':unreviewed,'all_observations':observations,
        'current_clinical_state_established':False}
