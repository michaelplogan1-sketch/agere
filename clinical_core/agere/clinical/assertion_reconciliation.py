"""Reusable documentary assertions and cross-source/clock reconciliation.

Derived receipts, not a second clinical truth store. Structured field readings and
machine interpretations retain different authority. Repeated reports never vote.
"""
from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import time
import zlib

from ...contracts import digest
from . import context_builder as context, longitudinal
from .clinical_events import iso
from . import source_structure, native_assertions, native_care_assertions

VERSION = 'source-assertion-reconciliation/2'
LIMITS = [
    'All records are synthetic. These are documentary accounts, not verified current clinical state.',
    'Machine interpretations remain proposals; exact source binding does not validate meaning.',
    'Source, arrival, clinical and literal-reference clocks are not substituted for one another.',
    'Repeated statements and structured/narrative copies are not independent corroboration.',
    'Unmapped text and missing sources remain explicit. Missing evidence does not prove missing care.',
    'Review steps acquire or reconcile evidence; they do not authorize treatment or outreach.',
]


def validate(p):
    if (not isinstance(p, dict) or not {'streams','concepts'}<=set(p)
        or set(p)-{'streams','concepts','received_annotations','assertion_window'}):
        raise ValueError('Explicit assertion source streams and concepts required')
    if 'received_annotations'in p and type(p['received_annotations'])is not bool:
        raise ValueError('Explicit received-annotation mode must be boolean')
    for key, maximum in [('streams', None), ('concepts', 20)]:
        values = p[key]
        minimum=0 if key=='concepts'and p.get('received_annotations')else 1
        if (not isinstance(values, list) or len(values)<minimum or maximum is not None and len(values)>maximum or
            any(not isinstance(v, str) or not 1 <= len(v) <= 128 for v in values) or len(set(values)) != len(values)):
            raise ValueError('Bounded nonempty assertion '+key+' required')
    result={**p,**{key:sorted(p[key])for key in ('streams','concepts')}}
    if 'assertion_window'in p:
        from .clinical_events import validate_window
        result['assertion_window']=validate_window(p['assertion_window'])
    return result


def code_identity():
    return digest([hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),source_structure.identity(), native_assertions.identity(), native_care_assertions.identity()])


def dependency(path, p):
    p = validate(p)
    base = {'question': 'Reconcile source assertions', **{k:p[k]for k in ('streams','concepts')}, 'contract': '', 'program_ids': [], 'max_bytes': 24000}
    events, event_meta = longitudinal.connect(path)
    events.close()
    narrative_concepts = set(event_meta['vocabulary']['concept'])
    literal_concepts = native_assertions.CONCEPTS | {'financial hardship','care nephrology'} | {'medication '+c for c in ('bb','acei','arb','arni','mra','sglt2')}
    if any(c.startswith('care ') for c in p['concepts']): literal_concepts |= native_care_assertions.concepts(path)
    if set(p['concepts']) - narrative_concepts - literal_concepts:
        raise ValueError('Unmapped assertion concept; declare an adapter before interpretation')
    # The source-context dependency validates reader vocabulary. Literal structured
    # bindings extend it explicitly; retain the full declared scope in our identity.
    identities=[];meta=None
    # The context transport validates at most 30 streams per packet. That is
    # not a population or workflow cap: bind every declared source partition.
    for offset in range(0,len(p['streams']),30):
        identity,part_meta,_,_=context.dependency_identity(path,{**base,'streams':p['streams'][offset:offset+30],
            'concepts':sorted(set(p['concepts'])&narrative_concepts)})
        if meta is not None and meta!=part_meta:raise ValueError('Assertion source partitions changed during preparation')
        meta=part_meta;identities.append(identity)
    if 'assertion_window'in p:
        from .clinical_events import validate_window
        validate_window(p['assertion_window'],meta['snapshot']['as_of'])
    annotation_source=None
    if p.get('received_annotations'):
        semantic=Path(path)/'semantic.sqlite3'
        from .request_plan import warehouse_metadata
        with sqlite3.connect(semantic.resolve().as_uri()+'?mode=ro',uri=True)as db:
            metadata=warehouse_metadata(semantic,db)
        if metadata.get('source_digest')!=meta['source_digest']or metadata.get('as_of')!=meta['snapshot']['as_of']:
            raise ValueError('Derived annotation source differs from the received warehouse')
        st=semantic.stat();annotation_source={'source_digest':metadata['source_digest'],'as_of':metadata['as_of'],
            'semantic_version':metadata.get('version'),'size':st.st_size,'mtime_ns':st.st_mtime_ns}
    return digest([identities, code_identity(), p, annotation_source]), meta, base


def extract(patient, records, events, p, as_of):
    """Bind literal structured fields and existing reader axes to exact source units."""
    units = context.units(patient, records, events, p, as_of)
    index = {r['record_id']: r for r in records}
    if len(index) != len(records): raise ValueError('Duplicate source record')
    by_record = defaultdict(list)
    for unit in units:
        by_record[unit['source']['record_id']].append(unit)
    assertions = []
    def emit(unit, concept, relation, polarity, value, *, event=None, field=None, native=None, care=None):
        if concept not in p['concepts']and not(event and p.get('received_annotations')):
            return
        source = unit['source']; record = index[source['record_id']]
        date_fields = {'reported_barrier': ('assessment_date',), 'care_completion': ('attendance_date',),
                       'care_order_plan': ('referral_date',), 'prescription_order': ('order_date',),
                       'dispense_record': ('fill_date',) if record['source_table'] == 'pharmacy_claims' else ('dispense_date',),
                       'administration_record': ('administration_date',),
                       'use_observed': ('assessment_date',), 'use_observation_coverage': ('assessment_date',)}
        observed_dates = {k: record[k] for k in date_fields.get(relation, ()) if record.get(k) is not None}
        valid_dates = bool(observed_dates) and all(iso(v) and v <= as_of for v in observed_dates.values()) and len(set(observed_dates.values())) == 1
        clinical = next(iter(observed_dates.values())) if valid_dates else None
        a = {'patient_id': patient, 'concept': concept, 'relation': relation,
             'polarity': polarity, 'value': value, 'subject': 'patient',
             'source_unit_id': unit['id'], 'source': source,
             'evidence': {'field': field, 'value': value},
             'authority': 'literal_structured_field', 'semantic_validation': 'literal_field_mapping_only',
             'clocks': {'source_date': source['event_date'], 'arrival_date': source['available_date'],
                        'clinical_date': clinical, 'reference_date': None},
             'clinical_date_fields': observed_dates,
             'clinical_date_status': 'explicit' if valid_dates else 'unresolved' if observed_dates else 'not_observed',
             'temporality': 'unspecified', 'copied_history': False, 'reference_status': 'not_applicable',
             'reader_review_reasons': [], 'repeated_statement_family': None,
             'current_clinical_state_established': False}
        if native:
            c = native['clock']
            a.update(respondent=native['respondent'], native_binding=native_assertions.VERSION,
                     statement_meaning=native['meaning'], clinical_date_fields=c['clinical_date_fields'],
                     clinical_date_status=c['clinical_date_status'])
            a['clocks']['clinical_date'] = c['clinical_date']
        if care:
            c = care['clock']; refs = care['reference_evidence']
            arrivals = [source['available_date']] + [r['arrival_date'] for r in refs]
            a.update(native_binding=native_care_assertions.VERSION, statement_meaning=care['meaning'],
                care_request_id=care['request_id'], care_service_concept=care['service_concept'],
                care_request_values=care['request_values'], reference_evidence=refs,
                clinical_date_fields=c['clinical_date_fields'], clinical_date_evidence=c['clinical_date_evidence'],
                clinical_date_status='explicit' if c['clinical_date_status'] == 'explicit_date' else c['clinical_date_status'])
            a['clocks'].update(clinical_date=c['clinical_date'],
                arrival_date=max(arrivals) if all(iso(d) for d in arrivals) else None)
            a['clock_semantics'] = 'Source date is this report; arrival date is the latest receipt required by its explicit references. Each original source/receipt clock is retained.'
        if event:
            s = event['source']; parts = {part['field']: part['text'] for part in context.narrative_sources.text_fields(record)}
            text = parts.get(s['field']); start, end = s['start'], s['end']
            if (event['patient_id'] != patient or event['record_hash'] != digest(record) or
                text is None or type(start) is not int or type(end) is not int or
                not 0 <= start < end <= len(text) or text[start:end] != s['quote']):
                raise ValueError('Assertion source span/hash mismatch')
            a.update(subject=event['subject'], authority='machine_annotation', semantic_validation='pending',
                     temporality=event['time'], copied_history=event['copied_history'],
                     reference_status=event['reference_status'],
                     evidence={k: s[k] for k in ('field', 'start', 'end', 'quote')},
                     annotation_id=event['id'], reader_model_identity=event['reader_model_identity'],
                     reader_review_reasons=event.get('review_routing', {}).get('reasons', []) if event.get('review_routing') else [],
                     repeated_statement_family=digest([patient, concept, relation,
                        ' '.join(event['statement']['text'].casefold().split())]))
            a['clocks']['reference_date'] = event['reference_date']
            a['source_structure'] = source_structure.locate(text,start,end)
            # A document's clinical date must not be assigned to every sentence.
            a['clocks']['clinical_date'] = event['clinical_date']
            a['clinical_date_status'] = 'not_observed'
        a['id'] = digest([VERSION, a]); assertions.append(a)
    for rid, received in by_record.items():
        unit = next(u for u in received if u['kind'] == 'structured')
        r = index[rid]; stream = r['source_table']; fields = unit['reported_fields']
        if stream == 'financial' and type(fields.get('difficulty_paying_medical_bills')) is bool:
            value = fields['difficulty_paying_medical_bills']
            emit(unit, 'financial hardship', 'reported_barrier', 'affirmed' if value else 'negated', value,
                 field='difficulty_paying_medical_bills')
        for binding in native_assertions.assess(r, p['concepts'], as_of):
            if binding['status'] == 'mapped':
                emit(unit, binding['concept'], binding['relation'],
                     'affirmed' if binding['value'] else 'negated', binding['value'],
                     field=binding['field'], native=binding)
        drug = fields.get('drug_class')
        if isinstance(drug, str) and drug:
            concept = 'medication '+drug
            if stream in ('medication_history', 'pharmacy_claims', 'medication_orders', 'medication_administration'):
                relation = {'medication_orders': 'prescription_order', 'medication_administration': 'administration_record'}.get(stream, 'dispense_record')
                emit(unit, concept, relation, 'affirmed',
                     {k: r.get(k) for k in ('drug', 'dose', 'status', 'dispense_id', 'prescription_id')}, field='record')
            if type(fields.get('medication_taking_observed')) is bool:
                observed = fields['medication_taking_observed']
                emit(unit, concept, 'use_observed' if observed else 'use_observation_coverage',
                     'affirmed' if observed else 'negated', observed, field='medication_taking_observed')
        if stream == 'referrals' and isinstance(fields.get('service'), str):
            concept = 'care '+fields['service']
            emit(unit, concept, 'care_order_plan', 'affirmed', fields['service'], field='service')
            if fields.get('attendance_status') in ('completed', 'not_attended'):
                completed = fields['attendance_status'] == 'completed'
                emit(unit, concept, 'care_completion', 'affirmed' if completed else 'negated', fields['attendance_status'], field='attendance_status')
    for binding in native_care_assertions.assess(records, p['concepts'], as_of):
        if binding['status'] == 'mapped' and binding['record_id'] in by_record:
            unit = next(u for u in by_record[binding['record_id']] if u['kind'] == 'structured')
            emit(unit, binding['concept'], binding['relation'], 'affirmed', binding['value'],
                 field=binding['field'], care=binding)
    seen = set()
    for event in events:
        if (event['kind'] != 'text' or event['stream'] not in p['streams']
            or event['concept'] not in p['concepts']and not p.get('received_annotations')):
            continue
        if event['id'] in seen:
            raise ValueError('Duplicate narrative assertion')
        seen.add(event['id']); s = event['source']
        matches = [u for u in by_record[s['record_id']] if u['kind'] == 'narrative' and
                   u['field'] == s['field'] and u['start'] <= s['start'] < s['end'] <= u['end']]
        if len(matches) != 1:
            raise ValueError('Narrative assertion not bound to exactly one whole source unit')
        relation = event['relation']
        if event['concept'] == 'financial hardship' and relation == 'clinical_observation':
            relation = 'reported_barrier'
        emit(matches[0], event['concept'], relation, event['assertion'], event['assertion'], event=event)
    return assertions, units


def received_annotation_assertions(patient, records, facts, units, as_of):
    """Bind derived note facts as proposals, preserving every value/unit and bad binding.

    Neither an extraction confidence nor an exact quote validates the proposed
    clinical meaning. Missing/invalid spans remain unbound rows, never negatives.
    """
    index={r['record_id']:r for r in records};by_record=defaultdict(list)
    for unit in units:by_record[unit['source']['record_id']].append(unit)
    assertions=[];unbound=[]
    for fact in facts:
        ident=digest(['derived-note-fact/1',patient,fact]);r=index.get(fact['record_id'])
        reason=None;field=fact.get('source_field');start=fact.get('span_start');end=fact.get('span_end')
        text=next((p['text']for p in context.narrative_sources.text_fields(r)if p['field']==field),None)if r else None
        if r is None:reason='source_record_unavailable'
        elif fact.get('patient_id')!=patient or fact.get('stream')!=r['source_table']:reason='source_identity_mismatch'
        elif fact.get('event_date')!=r.get('event_date'):reason='source_clock_mismatch'
        elif (text is None or type(start)is not int or type(end)is not int or not 0<=start<end<=len(text)
              or text[start:end]!=fact.get('evidence_text')):reason='source_span_mismatch'
        matching=[u for u in by_record[fact['record_id']]if u['kind']=='narrative'and u['field']==field
            and type(start)is int and type(end)is int and u['start']<=start<end<=u['end']]
        if not reason and len(matching)!=1:reason='whole_source_unit_unresolved'
        if reason:
            unbound.append({'id':ident,'reason':reason,'annotation':fact,
                'source_date':r.get('event_date')if r else None,'arrival_date':r.get('available_date')if r else None})
            continue
        unit=matching[0];reference=longitudinal.narrative_reference(text,start,end,as_of)
        polarity='affirmed'if fact.get('negated')in (0,False)else'negated'if fact.get('negated')in (1,True)else'unknown'
        subject=fact.get('experiencer')or'unknown';temporality=fact.get('temporality')or'unknown'
        domain={'fact_type':fact['fact_type'],'qualifier':fact.get('qualifier'),
            'value':fact.get('value'),'unit':fact.get('unit')}
        structure=source_structure.locate(text,start,end)
        reasons=[]
        if subject!='patient':reasons.append('not_patient_or_subject_unresolved')
        if polarity!='affirmed':reasons.append('not_affirmed_or_polarity_unresolved')
        if temporality!='current':reasons.append('not_current_or_temporality_unresolved')
        if reference['reference_status']=='literal_reference'and reference['reference_date']!=r.get('event_date'):
            reasons.append('reference_date_differs_from_source_statement')
        elif reference['reference_status']not in ('literal_reference','not_observed','not_applicable'):
            reasons.append('reference_date_unresolved')
        if reference['copied_history']or structure['role']=='copied_history':reasons.append('copied_history_cannot_refresh_state')
        if structure['role']=='family_history'and subject=='patient':reasons.append('subject_conflicts_with_family_history_section')
        if structure['clinical_account_excluded']:reasons.append('nonclinical_source_role')
        a={'patient_id':patient,'concept':'received '+fact['fact_type'],'relation':'received_observation',
            'polarity':polarity,'value':fact.get('value'),'unit':fact.get('unit'),'value_domain':domain,
            'subject':subject,'temporality':temporality,'source_unit_id':unit['id'],'source':unit['source'],
            'evidence':{'field':field,'start':start,'end':end,'quote':text[start:end]},
            'authority':'machine_annotation','semantic_validation':'pending','annotation_origin':'derived_note_fact',
            'annotation_id':ident,'received_annotation':fact,'reader_model_identity':fact.get('extractor'),
            'clocks':{'source_date':r.get('event_date'),'arrival_date':r.get('available_date'),
                'clinical_date':None,'reference_date':reference['reference_date']},
            'clinical_date_fields':{},'clinical_date_status':'not_observed','source_structure':structure,
            'copied_history':reference['copied_history']or structure['role']=='copied_history',
            'reference_status':reference['reference_status'],'reader_review_reasons':reasons,
            'repeated_statement_family':digest([patient,domain,' '.join(reference['statement']['text'].casefold().split())]),
            'qualifier_effects':reasons,'mention_only_positive_annotation':True,
            'positive_under_received_qualifiers':not reasons,'clinical_interpretation_validated':False,
            'current_clinical_state_established':False}
        a['id']=digest([VERSION,a]);assertions.append(a)
    return assertions,unbound


def assertion_window(assertions, window, as_of):
    from .clinical_events import validate_window
    window=validate_window(window,as_of);kept=[];outside=[];unknown=[]
    clock={'source_event':'source_date','arrival':'arrival_date','clinical':'clinical_date'}
    for a in assertions:
        day=a['clocks'].get(clock.get(window['basis']))
        state=True if window['basis']=='all_observed'else None if not iso(day)else (
            (not window['start']or day>=window['start'])and day<=(window['end']or as_of))
        if state is False:outside.append(a['id']);continue
        if state is None:unknown.append(a['id'])
        # The selected date basis never replaces a source/reference/clinical clock.
        kept.append({**a,'assertion_window_state':'selected'if state else'unknown'})
    return kept,{'window':window,'assertions_selected':len(kept)-len(unknown),'assertions_with_unknown_date':len(unknown),
        'unknown_date_assertion_ids':unknown,'assertions_outside_window':len(outside),'outside_window_assertion_ids':outside,
        'clinical_dates_substituted':False}


def documentary_clock(a):
    clocks = a['clocks']
    if a['authority'] == 'literal_structured_field':
        if a['clinical_date_status'] == 'unresolved':
            return None, None
        if clocks['clinical_date']:
            return 'explicit_clinical_date', clocks['clinical_date']
        return 'source_statement_date', clocks['source_date']
    if a['reference_status'] == 'literal_reference':
        return 'literal_reference_date', clocks['reference_date']
    if a['reference_status'] in ('not_observed', 'not_applicable') and a['temporality'] == 'current' and not a['copied_history']:
        return 'source_statement_date', clocks['source_date']
    return None, None


def reconcile(patient, assertions, as_of):
    """Preserve opposite accounts, date bases and history without majority voting."""
    if len({a['id'] for a in assertions}) != len(assertions):
        raise ValueError('Duplicate assertion identity')
    groups = defaultdict(list)
    for a in assertions:
        if a['patient_id'] != patient or a['source']['patient_id'] != patient:
            raise ValueError('Cross-patient assertion')
        domain=digest(a['value_domain'])if a.get('annotation_origin')=='derived_note_fact'else''
        groups[(a['concept'], a['relation'], a.get('care_request_id') or '',domain)].append(a)
    results = []
    for (concept, relation, request_id, domain), items in sorted(groups.items()):
        accounts = defaultdict(list); excluded = []; families = defaultdict(list)
        for a in items:
            if a.get('repeated_statement_family'):
                families[a['repeated_statement_family']].append(a)
        repeated_ids = set()
        for family in families.values():
            if len(family) > 1:
                # Identical text may be copied; do not refresh undated evidence with a later note timestamp.
                first = min(a['clocks']['source_date'] or '' for a in family)
                repeated_ids.update(a['id'] for a in family if (a['clocks']['source_date'] or '') != first and not a['clocks']['reference_date'])
        for a in items:
            basis, day = documentary_clock(a); reason = None
            if source_structure.exclusion(a): reason = source_structure.exclusion(a)
            elif a.get('assertion_window_state')=='unknown':reason='assertion_window_date_unresolved'
            elif 'subject_conflicts_with_family_history_section'in a.get('reader_review_reasons',[]):reason='source_subject_unresolved'
            elif a['subject'] != 'patient': reason = 'not_patient_assertion'
            elif a['polarity'] not in ('affirmed', 'negated'): reason = 'qualified_or_unresolved_assertion'
            elif not iso(a['clocks']['arrival_date']) or a['clocks']['arrival_date'] > as_of: reason = 'not_available_at_cutoff'
            elif a['id'] in repeated_ids or a.get('source_repetition',{}).get('cannot_refresh_state'):
                reason = 'repeated_text_cannot_refresh_state'
            elif not iso(day): reason = 'documentary_date_unresolved'
            elif day > as_of: reason = 'future_documentary_date'
            if reason: excluded.append({'assertion_id': a['id'], 'reason': reason})
            else: accounts[(basis, day)].append(a)
        dated = []
        for (basis, day), rows in sorted(accounts.items()):
            polarities = {a['polarity'] for a in rows}
            dated.append({'date_basis': basis, 'date': day,
                'status': 'opposing_accounts' if len(polarities) > 1 else next(iter(polarities))+'_account',
                'assertion_ids': [a['id'] for a in rows],
                'literal_structured_assertions': sum(a['authority'] == 'literal_structured_field' for a in rows),
                'machine_proposals': sum(a['authority'] == 'machine_annotation' for a in rows)})
        latest = {}; changes = []
        for basis in sorted({a['date_basis'] for a in dated}):
            series = [a for a in dated if a['date_basis'] == basis]
            latest[basis] = series[-1]
            for before, after in zip(series, series[1:]):
                if before['status'] != after['status']:
                    changes.append({'from': before, 'to': after, 'meaning': 'documentary_account_change_not_verified_clinical_change'})
        positive = {a['polarity'] for a in items if a['subject'] == 'patient' and a['polarity'] in ('affirmed', 'negated')}
        status = ('same_date_conflict' if any(a['status'] == 'opposing_accounts' for a in dated) else
                  'opposing_reports_over_time_or_unresolved_clock' if len(positive) > 1 else
                  'dated_documentary_accounts' if dated else 'unresolved_documentary_accounts')
        results.append({'concept': concept, 'relation': relation, 'care_request_id': request_id or None, 'status': status,
            **({'value_domain':items[0]['value_domain']}if domain else{}),
            'dated_accounts': dated, 'latest_by_clock': latest, 'documentary_changes': changes,
            'excluded_from_dated_accounts': excluded,
            'repeated_statement_groups': [{'family': key, 'assertion_ids': [a['id'] for a in value]} for key, value in sorted(families.items()) if len(value) > 1],
            'independent_corroboration_established': False,
            'current_clinical_state_established': False})
    return results


def patient_receipt(patient, records, events, parameters, meta, received_facts=None):
    p = validate(parameters); as_of = meta['snapshot']['as_of']
    assertions, units = extract(patient, records, events, p, as_of)
    annotation_unbound=[];window_coverage=None
    if p.get('received_annotations'):
        derived,annotation_unbound=received_annotation_assertions(patient,records,received_facts or[],units,as_of)
        assertions.extend(derived)
    # Keep the first received statement even when it falls outside the output
    # window. Filtering first would make an old repetition appear newly current.
    families=defaultdict(list)
    for a in assertions:
        if a.get('repeated_statement_family'):families[a['repeated_statement_family']].append(a)
    for family in families.values():
        if len(family)<2:continue
        first=min(a['clocks']['source_date']or''for a in family)
        anchors=[{'assertion_id':a['id'],'source':a['source']}for a in family if(a['clocks']['source_date']or'')==first]
        for a in family:
            a['source_repetition']={'first_received_statement_date':first or None,'anchors':anchors,
                'cannot_refresh_state':(a['clocks']['source_date']or'')!=first and not a['clocks']['reference_date'],
                'copying_established':False,'independent_corroboration_established':False}
            if a.get('annotation_origin')=='derived_note_fact'and a['source_repetition']['cannot_refresh_state']:
                a['positive_under_received_qualifiers']=False
                a['qualifier_effects']=[*a['qualifier_effects'],'repeated_text_cannot_refresh_state']
    if 'assertion_window'in p:
        assertions,window_coverage=assertion_window(assertions,p['assertion_window'],as_of)
        for row in annotation_unbound:
            clock={'source_event':'source_date','arrival':'arrival_date','clinical':'clinical_date'}
            window=p['assertion_window'];day=row.get(clock.get(window['basis']))
            row['assertion_window_state']=('selected'if window['basis']=='all_observed'else'unknown'if not iso(day)else
                'selected'if(not window['start']or day>=window['start'])and day<=(window['end']or as_of)else'outside')
    groups = reconcile(patient, assertions, as_of)
    repeated={ident for g in groups for family in g['repeated_statement_groups']for ident in family['assertion_ids']}
    for a in assertions:
        if a.get('annotation_origin')=='derived_note_fact'and a['id']in repeated:
            a['qualifier_effects']=[*a['qualifier_effects'],'repetition_is_not_independent_corroboration']
    mapped = {a['source_unit_id'] for a in assertions}
    unbound = [u['id'] for u in units if u['id'] not in mapped]
    represented = {a['concept'] for a in assertions}
    missing_concepts = sorted(set(p['concepts']) - represented)
    sources = {u['source']['record_id']: u['source'] for u in units}
    missing_streams = sorted(set(p['streams']) - {s['source_table'] for s in sources.values()})
    steps = []
    def step(kind, detail, ids=None):
        steps.append({'kind': kind, 'detail': detail, 'assertion_ids': ids or [], 'execution_authorized': False})
    proposals = [a['id'] for a in assertions if a['authority'] == 'machine_annotation']
    if proposals: step('validate_interpretation', 'Review machine-proposed subject, polarity, relation and time against the cited statements.', proposals)
    conflicts = [a['id'] for g in groups if g['status'] in ('same_date_conflict','opposing_reports_over_time_or_unresolved_clock')
                 for a in assertions if a['concept'] == g['concept'] and a['relation'] == g['relation']]
    if conflicts: step('resolve_opposing_accounts', 'Reconcile the source accounts; no count or confidence score resolves disagreement.', conflicts)
    if any(g['excluded_from_dated_accounts'] for g in groups): step('resolve_date_or_scope', 'Inspect excluded subjects, qualifications, dates and repeated history before treating a report as current.')
    if unbound or missing_concepts: step('interpret_unmapped_evidence', 'Received units without an in-scope assertion remain unassessed, not negative. Inspect the retained unit references.')
    if missing_streams: step('establish_source_coverage', 'Determine whether absent source streams reflect unavailable feeds, delayed records or unobserved events; no cause is assumed.')
    if any(c.startswith('medication ') for c in p['concepts']): step('confirm_medication_state', 'Distinguish current reported use, observed administration, supply, indication and hold rationale; orders/fills and unobserved ingestion are insufficient.')
    if any(c.startswith('care ') or c == 'outside care' for c in p['concepts']): step('verify_care_completion', 'Seek dated attendance/results or outside-source confirmation; a referral, request or coverage statement is not completed care.')
    if 'financial hardship' in p['concepts']: step('confirm_current_need_and_program', 'Confirm current affordability, assistance-specific consent and effective program requirements separately.')
    return {'schema': VERSION, 'synthetic': True, 'patient_id': patient, 'as_of': as_of, 'parameters': p,
        'status': 'opposing_accounts_need_review' if conflicts else
                  'assertions_reconciled_for_review' if assertions else 'no_mapped_assertions',
        'assertions': assertions, 'groups': groups, 'sources': sources,
        'coverage': {'received_source_units': len(units), 'units_with_in_scope_assertions': len(mapped),
            'units_without_in_scope_assertions': len(unbound), 'unmapped_unit_ids': unbound,
            'concepts_without_assertions': missing_concepts, 'streams_without_records': missing_streams,
            'complete_semantic_interpretation': False,
            **({'assertion_window':window_coverage}if window_coverage is not None else{}),
            **({'derived_note_fact_rows':len(received_facts or[]),'unbound_annotation_rows':annotation_unbound,
                'derived_annotation_meaning':'Received derived rows across the declared streams before assertion-window filtering; exact selected/unknown/outside assertion counts are separate. Not qualified clinical facts. Values/units and failed bindings are retained.'}if p.get('received_annotations')else{}),
            'native_fields': native_assertions.coverage(records, p, as_of),
            'native_care_fields': native_care_assertions.coverage(records, p, as_of)},
        'counts': {'assertions': len(assertions), 'structured_assertions': sum(a['authority'] == 'literal_structured_field' for a in assertions),
            'machine_proposals': len(proposals), 'groups': len(groups), 'same_date_conflict_groups': sum(g['status'] == 'same_date_conflict' for g in groups),
            'opposing_groups': sum(g['status'] in ('same_date_conflict','opposing_reports_over_time_or_unresolved_clock') for g in groups),
            'documentary_changes': sum(len(g['documentary_changes']) for g in groups),
            'repeated_statement_groups': sum(len(g['repeated_statement_groups']) for g in groups)},
        'next_evidence_steps': steps, 'current_clinical_state_established': False,
        'new_paid_api_calls': 0, 'limits': LIMITS}


def storage(path):
    db = sqlite3.connect(Path(path)/'assertion-receipts.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,patient TEXT,identity TEXT,payload BLOB)')
    return db


def execute(path, patient_ids, parameters, preview_limit=4):
    start = time.perf_counter(); path = Path(path); p = validate(parameters)
    if type(preview_limit) is not int or not 0 <= preview_limit <= 10: raise ValueError('Bounded preview required')
    identity, meta, _ = dependency(path, p)
    db = sqlite3.connect((path/'stack.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)
    events, _ = longitudinal.connect(path); out = storage(path)
    ids = sorted(set(patient_ids)); statuses = Counter(); counts = Counter(); actions = Counter(); preview = []; members=[]
    facts=defaultdict(list)
    if p.get('received_annotations'):
        with sqlite3.connect((path/'semantic.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)as semantic:
            semantic.row_factory=sqlite3.Row
            for offset in range(0,len(ids),500):
                batch=ids[offset:offset+500]
                query='SELECT rowid AS native_row_id,* FROM note_fact WHERE patient_id IN ('+','.join('?'for _ in batch)+') AND stream IN ('+','.join('?'for _ in p['streams'])+') ORDER BY rowid'
                for fact in semantic.execute(query,[*batch,*p['streams']]):facts[fact['patient_id']].append(dict(fact))
    try:
        if set(ids)-{r[0] for r in db.execute('SELECT id FROM patients')}: raise ValueError('Unknown assertion population patient')
        with out:
            for patient in ids:
                key = digest([identity, patient]); old = out.execute('SELECT payload FROM receipts WHERE id=?', (key,)).fetchone()
                if old: row = json.loads(zlib.decompress(old[0])); counts['cache_hits'] += 1
                else:
                    records = [json.loads(r[0]) for r in db.execute(
                        'SELECT payload FROM records WHERE patient=? AND stream IN ('+','.join('?' for _ in p['streams'])+') ORDER BY id',
                        (patient, *p['streams']))]
                    ev = [json.loads(r[0]) for r in events.execute('SELECT payload FROM events WHERE patient=? ORDER BY id', (patient,))]
                    row = patient_receipt(patient, records, ev, p, meta, received_facts=facts[patient]); row.update(id=key, identity=identity)
                    out.execute('INSERT INTO receipts VALUES (?,?,?,?)', (key, patient, identity, zlib.compress(context.encode(row))))
                    counts['new_receipts'] += 1
                statuses[row['status']] += 1; counts.update(row['counts'])
                members.append({'patient_id':patient,'receipt_id':key,'receipt_digest':digest(row),'status':row['status']})
                counts.update({k: row['coverage'][k] for k in ('received_source_units','units_with_in_scope_assertions','units_without_in_scope_assertions')})
                actions.update(s['kind'] for s in row['next_evidence_steps'])
                candidate = {k: row[k] for k in ('id','patient_id','status','counts','next_evidence_steps')}
                # Inspectable findings first, with deterministic patient ordering.
                # This affects only the small preview, never the population counts.
                preview = sorted(preview+[candidate], key=lambda x: (not bool(x['counts']['opposing_groups']),
                    not bool(x['counts']['assertions']), x['patient_id']))[:preview_limit]
        return {'schema': VERSION, 'synthetic': True, 'identity': identity, 'parameters': p,
            'patients_evaluated': len(ids), 'population_digest': digest(ids), 'statuses': dict(statuses),
            'counts': dict(counts), 'patients_needing_evidence_step': dict(actions), 'patients': preview,
            'review_members':members,
            'preview_selection': 'Opposing accounts first, then patients with assertions, then patient ID. Preview is not a prevalence sample.',
            'all_receipts_persisted': True, 'new_paid_api_calls': 0, 'elapsed_seconds': time.perf_counter()-start,
            'current_clinical_state_established': False, 'limits': LIMITS}
    finally: db.close(); events.close(); out.close()


def inspect(path, receipt_id):
    if not isinstance(receipt_id, str) or not re.fullmatch('[a-f0-9]{64}', receipt_id): raise ValueError('Exact assertion receipt required')
    db = storage(path)
    try: row = db.execute('SELECT payload FROM receipts WHERE id=?', (receipt_id,)).fetchone()
    finally: db.close()
    if row is None: raise ValueError('Unknown assertion receipt')
    result = json.loads(zlib.decompress(row[0]))
    if result['identity'] != dependency(path, result['parameters'])[0]: raise ValueError('Stale assertion receipt; rebuild required')
    source = sqlite3.connect((Path(path)/'stack.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)
    try:
        for rid, ref in result['sources'].items():
            raw = source.execute('SELECT payload FROM records WHERE id=? AND patient=?', (rid, result['patient_id'])).fetchone()
            if raw is None or digest(json.loads(raw[0])) != ref['record_hash']: raise ValueError('Changed assertion source record')
    finally: source.close()
    return result


def reconcile_bundle(path, bundle_id):
    """Connect the same reducer across every prepared chart pass, without new inference."""
    from .context_passes import inspect as inspect_bundle
    bundle = inspect_bundle(path, bundle_id, verify_sources=True)
    p = {k: bundle['parameters'][k] for k in ('streams', 'concepts')}
    if not p['concepts']: raise ValueError('Declare concepts to reconcile chart-pass assertions')
    result = execute(path, [bundle['patient_id']], p, preview_limit=1)
    receipt = inspect(path, result['patients'][0]['id'])
    pass_map = {u['id']: page['pass_number'] for page in bundle['passes'] for u in page['evidence']}
    return {**receipt, 'bundle_id': bundle_id,
        'pass_binding': [{'assertion_id': a['id'], 'source_unit_id': a['source_unit_id'],
                          'pass_number': pass_map.get(a['source_unit_id']),
                          'in_blocked_unit': a['source_unit_id'] not in pass_map} for a in receipt['assertions']],
        'pass_transport_complete': not bundle['blocked_units'],
        'specialist_proposals_promoted': False}


def bind_specialist_review(bundle, review, reconciled):
    """Attach proposal support to the shared assertion contract; never imply agreement validates it."""
    from .context_passes import reconcile as check_passes
    if (review.get('bundle_id') != bundle['id'] or review.get('patient_id') != bundle['patient_id'] or
        reconciled.get('bundle_id') != bundle['id'] or reconciled['patient_id'] != bundle['patient_id']):
        raise ValueError('Specialist review and source reconciliation do not share a case')
    check_passes(bundle, review['proposals'])
    by_unit = defaultdict(list)
    for a in reconciled['assertions']: by_unit[a['source_unit_id']].append(a['id'])
    bindings = [{'pass_number': p['pass_number'], 'source_unit_id': p['source_unit_id'],
                 'proposed_label': p['label'], 'related_assertion_ids': by_unit[p['source_unit_id']],
                 'semantic_acceptance': 'pending', 'agreement_is_not_validation': True} for p in review['proposals']]
    return {**review, 'source_reconciliation': reconciled, 'support_bindings': bindings,
            'automatic_acceptance': False, 'whole_patient_clinical_state': 'unknown'}
