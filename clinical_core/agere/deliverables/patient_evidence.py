"""Carry already executed patient evidence into every saved report format.

These are attributed observations, never new medication/reason adjudications.
Keep the answer intact; the complete native context remains the download.
"""
import json,re

# The existing clinical presentation has a declared input interface. Producers
# bind their own aliases with native composition; the renderer does not invent
# medication meanings from new column names.
CLINICAL_REVIEW_CONTRACT={
    'schema':'clinical-medication-review-view/1',
    'method':'Use compose_table.rename_columns for explicit source aliases. Values remain native. This presentation contract does not select patients, infer facts or qualify clinical meaning.',
    'columns':{
        'patient_id':'Exact received patient identity',
        'on_rasi_pillar':'RAAS/ARNI picked-up-fill flag in the declared analysis window: 0,1 or null',
        'on_beta_blocker_pillar':'Evidence-based beta-blocker picked-up-fill flag: 0,1 or null',
        'on_mra_pillar':'MRA picked-up-fill flag: 0,1 or null',
        'on_sglt2i_pillar':'SGLT2 inhibitor picked-up-fill flag: 0,1 or null',
        'pillars_missing':'Native names of pillars without a fill observation',
        'recent_gdmt_fill_observations':'Intact object list: drug_name (received medicine name, explicit null if unavailable),drug_class,latest_fill_date_in_window,observed_fill_count. Missing drug_name is an unbound presentation field, not evidence that the source lacks a name.',
        'fill_window_start_exclusive':'Native lower date bound',
        'fill_window_end_inclusive':'Native upper date bound',
        'gdmt_dose_rows':'Intact dose objects restricted by registered GDMT membership; include all received source/date fields',
        'care_assignments':'Intact dated assignment objects, with clinician_id,role,specialty,assigned_at_snapshot and source clocks',
        'documentary_findings':'Intact source-bound entity/relation findings',
        'dated_correspondence_roles':'Intact dated source roles, distinct from assignment',
        'proposed_review_steps':'Native patient-specific review steps; proposals, not care execution',
        'all_medication_context':'Optional intact broader medication collection for provenance; not the requested GDMT dose subset'},
    'unknowns':'Unavailable facts remain null or explicitly unknown with source scope. Missing data and missing column bindings are different. Preserve all retained source objects and all original patients.'}


def clinical_collection_issues(answer):
    """Validate the existing optional presentation interface before paid review.

    Only a producer using this declared field opts into the contract. Names are
    source values, never inferred from a drug class; explicit null stays unknown.
    Native JSON aggregates and composition lists have the same shape.
    """
    for row in answer.get('table')or[]:
        if not isinstance(row,dict)or'recent_gdmt_fill_observations'not in row:continue
        value=row['recent_gdmt_fill_observations']
        if value is None:continue
        try:value=json.loads(value)if isinstance(value,str)else value
        except ValueError:value=False
        if not isinstance(value,list)or any(not isinstance(f,dict)or
            not isinstance(f.get('drug_class'),str)or not f['drug_class'].strip()or
            'drug_name'not in f or(f['drug_name']is not None and not isinstance(f['drug_name'],str))for f in value):
            return ['Bind recent_gdmt_fill_observations to intact objects with drug_class and drug_name from the received fill source (explicit null when unavailable). Missing keys are unbound fields; do not infer medicine names from classes.']
    return []


def fill_name(fill):
    """Keep historical missing bindings distinct from received unknown names."""
    if'drug_name'not in fill:return 'Medicine name not returned in this result'
    return fill['drug_name']or'Medicine name unavailable in the received source'


def capture_profiles(path, patient_ids, answer=None,graph=None):
    """Snapshot received identities and clinical observations for patient review.

    Names come from the same received/staged source as the semantic layer. This
    restores display fields without fuzzy identity matching or care attribution.
    Missing inputs remain missing, including on older fixture/recovery builds.
    """
    from pathlib import Path
    from datetime import date
    import sqlite3
    from .receipts import fingerprint
    path=Path(path);roster=path/'stack.sqlite3';run_file=path/'run.json'
    ids=sorted(set(patient_ids))
    if not ids or not roster.is_file() or not run_file.is_file():return None
    run=json.loads(run_file.read_text());cutoff=(run.get('snapshot')or{}).get('as_of')
    if not cutoff:return None
    cutoff=str(cutoff)[:10];people={};clinician_ids=set()
    def connect(p):return sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True)
    def chunks(values):
        for i in range(0,len(values),900):yield values[i:i+900]
    with connect(roster)as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='patients'").fetchone():return None
        for chunk in chunks(ids):
            marks=','.join('?'for _ in chunk)
            for pid,raw in db.execute('SELECT id,payload FROM patients WHERE id IN('+marks+')',chunk):
                person=json.loads(raw)
                if person.get('patient_id',pid)!=pid:raise ValueError('Received profile identity differs from roster')
                profile_day=str(person.get('profile_captured_date')or'')[:10]
                visible=not profile_day or profile_day<=cutoff
                profile={'patient_id':pid,'identity_status':'received_profile'if visible else'profile_after_snapshot',
                    'profile_source_date':profile_day or None,'source_profile_fingerprint':fingerprint(person)}
                for key in ('display_name','birth_date','age_reported','sex_reported','preferred_language','interpreter_needed'):
                    profile[key]=person.get(key)if visible else None
                try:
                    born=date.fromisoformat(profile['birth_date']);at=date.fromisoformat(cutoff)
                    profile['age_at_snapshot']=at.year-born.year-((at.month,at.day)<(born.month,born.day))if born<=at else None
                except (ValueError,TypeError):profile['age_at_snapshot']=None
                people[pid]=profile
    semantic=path/'semantic.sqlite3'
    if semantic.is_file():
        with connect(semantic)as db:
            db.row_factory=sqlite3.Row
            tables={r[0]for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if'patient'in tables:
                cols=[r[1]for r in db.execute('PRAGMA table_info(patient)')]
                keep=[k for k in ('patient_id','coverage_type','coverage_status','pcp_clinician_id','coverage_source')if k in cols]
                if'patient_id'in keep:
                    for chunk in chunks(ids):
                        for row in db.execute('SELECT '+','.join(keep)+' FROM patient WHERE patient_id IN('+','.join('?'for _ in chunk)+')',chunk):
                            p=people.get(row['patient_id'])
                            if p is not None:p['structured_profile']=dict(row)
                            if dict(row).get('pcp_clinician_id'):clinician_ids.add(row['pcp_clinician_id'])
            if'measurement_latest'in tables:
                for chunk in chunks(ids):
                    for row in db.execute('SELECT * FROM measurement_latest WHERE patient_id IN('+','.join('?'for _ in chunk)+') ORDER BY patient_id,concept',chunk):
                        p=people.get(row['patient_id'])
                        if p is not None:p.setdefault('latest_measurements',[]).append(dict(row))
    # Identity lookup never infers an assignment: the original dated assignment
    # objects remain the evidence for role and current responsibility.
    for row in (answer or{}).get('table')or[]:
        if not isinstance(row,dict):continue
        for raw in row.values():
            try:assignments=json.loads(raw)if isinstance(raw,str)and raw[:1]in('[','{')else raw
            except ValueError:continue
            for item in assignments if isinstance(assignments,list)else[]:
                if isinstance(item,dict)and item.get('clinician_id'):clinician_ids.add(item['clinician_id'])
    if graph and semantic.is_file():
        from ..clinical.plan_fence import premises
        wanted={v for v in premises(graph)['tables']if v.startswith('phenotype_')}
        if wanted:
            from ..clinical.semantic_analyst import SemanticWarehouse
            wh=SemanticWarehouse(semantic)
            try:
                registered=set(wh.define(cutoff))
                for view in sorted(wanted&registered):
                    for chunk in chunks(ids):
                        query='SELECT * FROM "'+view+'" WHERE patient_id IN('+','.join('?'for _ in chunk)+')'
                        cursor=wh.db.execute(query,chunk);cols=[c[0]for c in cursor.description]
                        for values in cursor:
                            item=dict(zip(cols,values));p=people.get(item.get('patient_id'))
                            if p is not None:p.setdefault('registered_cohort_evidence',[]).append({'view':view,'as_of':cutoff,'fields':item})
            finally:wh.close()
    clinicians={};entities=path.parent/'warehouse.sqlite3';release=(run.get('snapshot')or{}).get('release')
    if entities.is_file()and release and clinician_ids:
        with connect(entities)as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='source_entities'").fetchone():
                for chunk in chunks(sorted(clinician_ids)):
                    for ident,raw in db.execute("SELECT id,payload FROM source_entities WHERE release=? AND kind='clinicians' AND id IN("+','.join('?'for _ in chunk)+')',[release,*chunk]):
                        item=json.loads(raw)
                        if item.get('clinician_id',ident)!=ident:raise ValueError('Received clinician identity differs from registry')
                        clinicians[ident]={k:item.get(k)for k in ('clinician_id','display_name','specialty','organization_id','npi')}
                        clinicians[ident]['source_profile_fingerprint']=fingerprint(item)
    return {'schema':'patient-review-profiles/1','source_digest':run.get('source_digest'),'release':release,'as_of':cutoff,
        'patients':people,'clinicians':clinicians,
        'identity_basis':'Exact received patient and clinician IDs; names do not establish a current care relationship.'}

RECOMMENDATION_FIELD_PATTERN=r'^(?!.*(?:_status|_count|_type|_number|_n)$).*(?:recommend|next_(?:step|action)|action_plan|proposed_(?:[a-z]+_)*(?:action|step)|review_(?:step|plan))'

def recommendation_field(name):
    return bool(re.search(RECOMMENDATION_FIELD_PATTERN,name,re.I))


def review_cards(context, evidence=None):
    """A reading projection of saved observations, never a new clinical verdict.

    The four-pillar display is a domain presentation of explicit returned fields;
    it neither selects a cohort nor executes a disease-specific answer handler.
    Full original rows and source objects stay in the saved context.
    """
    profiles=context.get('patient_profiles')or{};people=profiles.get('patients')or{};clinicians=profiles.get('clinicians')or{}
    evidence=evidence if evidence is not None else projection(context)
    source_feedback={}
    reviews=[*((context.get('investigation')or{}).get('answer_reviews')or[])]
    saved_review=(context.get('answer')or{}).get('investigation_review')
    if isinstance(saved_review,dict)and saved_review not in reviews:reviews.append(saved_review)
    patient_verification={};packet_families={}
    known_parents={'patient_'+str(pid)for pid in [*people,*(context.get('patient_ids')or[]),
        *[r['patient_id']for r in(context.get('answer')or{}).get('table')or[]if isinstance(r,dict)and r.get('patient_id')]]}
    for review in reviews:
        support=review.get('support')or{};groups={}
        packets=support.get('packets')or[];unchecked=support.get('unchecked_packet_ids');checked=support.get('checked_source_packets')
        # Older complete manifests omitted parent IDs on deferred results.
        # Recover only the producer's exact source-partition ID format and
        # validate it against received patient identities; no clinical aliasing.
        manifest=support.get('packet_manifest_complete')is True or(type(checked)is int and isinstance(unchecked,list)
            and len(packets)==checked+len(unchecked)and len({p.get('id')for p in packets})==len(packets)
            and set(unchecked)<={p.get('id')for p in packets})
        for packet in packets:
            ident=packet.get('parent_packet_id')or packet.get('id')
            if not packet.get('parent_packet_id')and isinstance(ident,str):
                parent=re.sub(r'__sources_\d+$','',ident)
                candidates={p for p in (ident,parent)if p in known_parents}
                ident=next(iter(candidates))if len(candidates)==1 else None
            if isinstance(ident,str)and ident.startswith('patient_'):
                groups.setdefault(ident,[]).append({k:packet.get(k)for k in ('id','label','decided_by','reason','status')})
        for parent,packets in groups.items():
            complete=manifest or support.get('all_dependencies_checked')is True or(len(packets)==1 and packets[0]['id']==parent
                and packets[0].get('decided_by')!='native_source_dependency_closure'
                and set(packet_families.get(parent,({},False))[0])<={parent})
            current={}if complete else dict(packet_families.get(parent,({},False))[0])
            current.update({p['id']:p for p in packets});packet_families[parent]=(current,complete)
        for feedback in (review.get('support')or{}).get('claim_feedback')or[]:
            ident=feedback.get('source_assertion_id')
            if ident and feedback.get('label')not in (None,'supported'):
                source_feedback.setdefault(ident,[]).append(feedback)
    for parent,(packets,complete)in packet_families.items():
        packets=list(packets.values());pending=[p for p in packets if p.get('label')!='supported'or not p.get('decided_by')]
        label=next((s for s in ('contradicted','not_established')if any(p.get('label')==s for p in pending)),
                   'unknown'if pending or not complete else'supported')
        awaiting=bool(pending)and all(p.get('status')=='awaiting_dependency_repair'for p in pending)
        patient_verification[parent[len('patient_'):]]={'id':parent,'label':label,
            'decided_by':'all_required_packets'if label=='supported'else None,
            'reason':next((p['reason']for p in pending if p.get('reason')),None),
            'packet_manifest_complete':complete,'packets':packets,
            'status':'awaiting_dependency_repair'if awaiting else'supported'if label=='supported'else'unresolved_verification',
            'notice':('Source checks for this patient are pending while a source dependency is repaired. The saved observations remain available for review.'if awaiting else
                'The saved source-support check is unresolved for this patient. Confirm the recorded findings against the source records before using the interpretation.')if label!='supported'else None}
    suspended_ids = {item['assertion_id'] for item in (context.get('source_disposition') or {}).get('suspended_assertions', [])}
    for pid, notes in evidence.get('notes', {}).items():
        affected = sorted(suspended_ids & {ident for note in notes for ident in note.get('audit_only_assertion_ids', [])})
        if affected:
            patient_verification[pid] = {'label': 'unknown', 'decided_by': None,
                'status': 'suspended_source_reading', 'suspended_assertion_ids': affected,
                'notice': 'A source interpretation for this patient is suspended pending review. Prior readings remain available for inspection; their removal from active findings does not establish that a reason is absent. The saved answer has not been recalculated.',
                'saved_verification': patient_verification.get(pid)}
    def decoded(value):
        if isinstance(value,str)and value[:1]in('[','{'):
            try:return json.loads(value)
            except ValueError:pass
        return value
    def items(value):
        value=decoded(value);return value if isinstance(value,list)else[]
    def person_name(cid):return(clinicians.get(cid)or{}).get('display_name')or cid or'Name unavailable'
    def reasons(findings,entity,relations=None,states=None):
        groups={}
        for f in findings:
            if not isinstance(f,dict)or(f.get('entity')or{}).get('id')!=entity:continue
            if relations is not None and f.get('relation')not in relations:continue
            assertion=f.get('assertion')or{}
            key=tuple(str(f.get(k)or'')for k in ('quote','relation','polarity','temporality','subject'))+(assertion.get('question_digest'),)
            group=groups.setdefault(key,{'quote':f.get('quote'),'relation':f.get('relation'),'polarity':f.get('polarity'),
                'temporality':f.get('temporality'),'subject':f.get('subject'),'sources':[],'findings':[]})
            source={'record_id':f.get('record_id'),'date':f.get('date')}
            if source not in group['sources']:group['sources'].append(source)
            group['findings'].append(f)
            binding=(states or{}).get(assertion.get('id'))
            if binding and binding['assertion']!=assertion:
                raise ValueError('Finding differs from its reconciled source assertion')
            accounts=binding['groups']if binding else[]
            if accounts:
                group.setdefault('documentary_accounts',[])
                for account in accounts:
                    if account not in group['documentary_accounts']:group['documentary_accounts'].append(account)
                if any(a['status']in ('same_date_conflict','opposing_reports_over_time_or_unresolved_clock')for a in accounts):
                    group['review_status']='Different source accounts require reconciliation'
                    group['review_qualification']='The source records contain different accounts for this topic. They may describe different dates or circumstances; confirm which account applies now.'
            feedback=source_feedback.get((f.get('assertion')or{}).get('id'))
            if feedback:
                group['review_status']='Unresolved source interpretation'
                group.setdefault('review_feedback',[]).extend(feedback)
                group['review_qualification']='Machine readers disagree or remain uncertain about this relation. The passage is retained for review; it is not an accepted treatment reason or evidence of individual tolerability.'
        return list(groups.values())
    def parts(answer):
        yield answer
        for section in answer.get('sections')or[]:
            if isinstance(section,dict)and isinstance(section.get('answer'),dict):yield from parts(section['answer'])
    cards={}
    for answer in parts(context.get('answer')or{}):
        for row in answer.get('table')or[]:
            if not isinstance(row,dict)or not row.get('patient_id'):continue
            original_row=row;row=dict(row)
            aliases={'on_rasi_pillar':('on_raas_inhibitor','on_raas','raas_pillar'),
                'on_beta_blocker_pillar':('on_beta_blocker','beta_blocker_pillar'),
                'on_mra_pillar':('on_mra','mra_pillar'),'on_sglt2i_pillar':('on_sglt2i','sglt2_pillar'),
                'pillars_missing':('missing_pillars',),'gdmt_dose_rows':('dose_evidence','gdmt_dose_evidence'),
                'care_assignments':('structured_care_team','care_team_assignments')}
            for field,names in aliases.items():
                if field not in row:
                    for alias in names:
                        if alias in row:row[field]=row[alias];break
            if 'proposed_review_steps'not in row:
                steps=[row[k]for k in ('registered_pillar_gap_step','registered_dose_step','cardiology_pcp_step')if row.get(k)is not None]
                if steps:row['proposed_review_steps']=steps
            pid=row['patient_id'];profile=people.get(pid)or{}
            retired={i for review in evidence.get('notes',{}).get(pid,[])for i in review.get('audit_only_assertion_ids',[])}
            # Consume the existing typed reconciliation, preserving each review's
            # scope. Do not vote across reviews or collapse different questions.
            state_index={};state_groups=[];seen_groups=set()
            for review in evidence.get('notes',{}).get(pid,[]):
                state=review.get('documentary_state')or{}
                active_ids={(f.get('assertion')or{}).get('id')for field,values in (review.get('findings')or{}).items()
                    if field not in review.get('audit_only_fields',[])for f in values if isinstance(f,dict)}
                assertions={a['id']:a for a in state.get('assertions',[])if a.get('id')in active_ids and a.get('patient_id')==pid and (a.get('source')or{}).get('patient_id')==pid}
                for group in state.get('groups',[]):
                    ids={ident for a in group.get('dated_accounts',[])for ident in a.get('assertion_ids',[])}
                    ids.update(a['assertion_id']for a in group.get('excluded_from_dated_accounts',[]))
                    if not ids or not ids<=set(assertions):continue
                    identity=json.dumps(group,sort_keys=True)
                    if identity not in seen_groups:state_groups.append(group);seen_groups.add(identity)
                    for ident in ids:
                        binding=state_index.setdefault(ident,{'assertion':assertions[ident],'groups':[]})
                        if binding['assertion']!=assertions[ident]:raise ValueError('Reconciled assertion identity changed between source reviews')
                        if group not in binding['groups']:binding['groups'].append(group)
            opposed=sum(g['status']in ('same_date_conflict','opposing_reports_over_time_or_unresolved_clock')for g in state_groups)
            unresolved=sum(g['status']=='unresolved_documentary_accounts'for g in state_groups)
            reconciliation_parts=[]
            if opposed:reconciliation_parts.append(str(opposed)+' '+('set of chart statements differs'if opposed==1 else'sets of chart statements differ'))
            if unresolved:reconciliation_parts.append(str(unresolved)+' '+('set has'if unresolved==1 else'sets have')+' unclear timing or context')
            reconciliation_summary=('; '.join(reconciliation_parts)+
                '. Review the dated passages before using them as current clinical facts.')if reconciliation_parts else None
            def bound_reasons(findings,entity,relations=None):return reasons(findings,entity,relations,state_index)
            for cohort in profile.get('registered_cohort_evidence')or[]:
                for field,value in cohort.get('fields',{}).items():
                    if field not in row:row[field]=value
            assignments=list(items(row.get('care_assignments')))
            for table in evidence.get('supporting_tables')or[]:
                if {'role','clinician_id','assigned_at_snapshot'}<=set(table['columns'])and not table.get('truncated'):
                    assignments.extend(r for r in table['rows']if r.get('patient_id')==pid)
            unique={}
            for a in assignments:
                if not isinstance(a,dict):continue
                key=tuple(str(a.get(k))for k in ('clinician_id','role','effective_start','source_date','source_record_ids'))
                if key not in unique or len(a)>len(unique[key]):unique[key]=a
            assignments=list(unique.values())
            def care(role):
                found=[a for a in assignments if isinstance(a,dict)and(
                    a.get('role')=='primary_care'if role=='Primary care'else'cardiol'in str(a.get('specialty')or(clinicians.get(a.get('clinician_id'))or{}).get('specialty')or'').lower()or a.get('is_cardiology')==1)]
                current=[a for a in found if a.get('assigned_at_snapshot')==1]
                return {'role':role,'current':[{'name':person_name(a.get('clinician_id')),**a}for a in current],
                    'other_records':[{'name':person_name(a.get('clinician_id')),**a}for a in found if a not in current],
                    'status':'Current assignment documented'if current else'Current assignment not established'}
            narrative_roles=[]
            for review in evidence.get('notes',{}).get(pid,[]):narrative_roles.extend(review.get('source_clinicians')or[])
            narrative_roles=list({json.dumps(r,sort_keys=True):r for r in narrative_roles}.values())
            clinical=[]
            if row.get('hfref_basis'):clinical.append({'label':'Heart failure evidence','value':str(row['hfref_basis']).replace('_',' ')})
            if row.get('lowest_documented_ef')is not None:
                clinical.append({'label':'Lowest documented EF','value':str(row['lowest_documented_ef'])+'%',
                    'interpretation':'Historical minimum; current EF needs confirmation'})
            if row.get('latest_low_ef_date'):
                clinical.append({'label':'Latest qualifying low-EF observation','value':row['latest_low_ef_date'],
                    'interpretation':'This date does not necessarily belong to the historical minimum'})
            for m in profile.get('latest_measurements')or[]:
                if m.get('concept')not in ('egfr','creatinine','potassium','heart_rate','systolic_bp','diastolic_bp','bp_systolic','bp_diastolic','systolic','diastolic'):continue
                clinical.append({'label':str(m['concept']).replace('_',' '),'value':m.get('value_canonical'),
                    'unit':m.get('unit_canonical'),'date':m.get('event_date'),'record_id':m.get('record_id'),
                    'value_censor':m.get('value_censor'),'same_day_values':m.get('same_day_values'),
                    'same_day_min':m.get('same_day_min')})
            findings=list(items(row.get('documentary_findings')))
            findings=[f for f in findings if not isinstance(f,dict)or(f.get('assertion')or{}).get('id')not in retired]
            # A later source review can contribute the answer's counts while
            # its patient table still references an earlier assembly. The
            # reading view must retain those actual bound observations too.
            # Assertion/contract identities stay intact; no field-name merge
            # or clinical adjudication chooses between different readings.
            def finding_identity(f):return json.dumps({k:v for k,v in f.items()if k!='field'}if isinstance(f,dict)else f,sort_keys=True)
            seen_findings={finding_identity(f)for f in findings}
            for review in evidence.get('notes',{}).get(pid,[]):
                declared=review.get('findings')
                for field,values in declared.items()if isinstance(declared,dict)else[]:
                    if field in review.get('audit_only_fields',[]):continue
                    for value in values if isinstance(values,list)else[]:
                        if not isinstance(value,dict)or not isinstance(value.get('entity'),dict):continue
                        finding={**value,'field':field};identity=finding_identity(finding)
                        if identity not in seen_findings:findings.append(finding);seen_findings.add(identity)
            fills=items(row.get('recent_gdmt_fill_observations'));doses=items(row.get('gdmt_dose_rows'))
            therapy=[]
            window_start=row.get('fill_window_start_exclusive');window_end=row.get('fill_window_end_inclusive')
            declared_scopes=[g for g in answer.get('grounding')or[]if isinstance(g,str)and re.search(r'\b(?:fills?|pillars?)\b',g,re.I)and re.search(r'\b(?:months?|years?|window)\b',g,re.I)]
            # Exploratory history may remain in the investigation after its
            # flags were replaced. Report the saved declaration as a declaration;
            # an earlier macro call alone cannot establish the final time basis.
            historical_fill=not(window_start and window_end)and not declared_scopes and any(re.search(r'\{\{\s*drug_fill:',str(t.get('input',{}).get('sql')or''))for t in(context.get('investigation')or{}).get('tool_calls')or[])
            fill_scope=('Picked-up fill after '+str(window_start)+' through '+str(window_end)if window_start and window_end else
                'Saved analysis time basis: '+' '.join(declared_scopes)if declared_scopes else
                'Received fill history through the snapshot; no recency window is established by these membership flags'if historical_fill else'Recent-fill observation window defined by the saved analysis')
            fill_scope_display=fill_scope
            if declared_scopes and not(window_start and window_end):
                declared_window=re.search(r'\b\d+\s+(?:months?|years?)\s+(?:ending(?: at)?|through)\s+\d{4}-\d{2}-\d{2}\b',' '.join(declared_scopes),re.I)
                fill_scope_display=('Declared analysis window: '+declared_window.group()if declared_window else
                    'Observation window declared in the saved analysis; confirm current medication use')
            # These labels describe the source-defined fields returned by the
            # analysis, not a fresh treatment rule or an individual dose target.
            pillars=[('rasi','RAAS inhibitor / ARNI','on_rasi_pillar','raas_inhibitor',{'ace_inhibitor','arb','arni'}),
                ('beta_blocker','Evidence-based beta-blocker','on_beta_blocker_pillar','hf_evidence_based_beta_blocker',{'hf_evidence_based_beta_blocker'}),
                ('mra','Mineralocorticoid receptor antagonist','on_mra_pillar','mra',{'mra'}),
                ('sglt2i','SGLT2 inhibitor','on_sglt2i_pillar','sglt2_inhibitor',{'sglt2_inhibitor'})]
            from ..clinical import drug_classes
            for group,label,field,entity,classes in pillars:
                if field not in row:continue
                observed=[f for f in fills if f.get('drug_class')in classes]
                ds=[d for d in doses if classes.intersection(drug_classes.classify(str(d.get('drug_key')or'').replace('_',' ')))]
                # Drug classification supplies presentation grouping only. Any
                # unmapped dose observation is retained separately below.
                therapy.append({'group':group,'label':label,'observed_status':
                    ('Historical fill observed'if historical_fill else'Fill observed in the saved analysis'if declared_scopes and not(window_start and window_end)else'Recent fill observed')if row[field]==1 else('No fill observed in the returned history'if historical_fill else'No fill observed in the saved analysis'if declared_scopes and not(window_start and window_end)else'No recent fill observed')if row[field]==0 else'Fill status unresolved',
                    'fill_scope':fill_scope,
                    'fill_scope_display':fill_scope_display,
                    'fill_observed':True if row[field]==1 else False if row[field]==0 else None,
                    'current_use':'Confirm current regimen','fills':observed,'dose_observations':ds,
                    'documented_reasons':bound_reasons(findings,entity,{'treatment_reason','reason_not_on_therapy','not_prescribed_reason','documented_reason_not_on_therapy'}),
                    'documented_status_accounts':bound_reasons(findings,entity,{'documented_status'})})
            rationale=[]
            if row.get('hfref_basis'):rationale.append('Source-defined HFrEF cohort: '+str(row['hfref_basis']).replace('_',' '))
            missing=row.get('pillars_missing')
            if 'pillars_missing'not in row:missing=', '.join(label for group,label,field,entity,classes in pillars if row.get(field)==0)
            if missing:rationale.append(('Reconcile pillars without a fill in the returned history: 'if historical_fill else'Reconcile pillars without a recent-fill observation: ')+str(missing).replace('_',' '))
            if doses:rationale.append('Review dated dose/state observations and their reference-dose comparisons')
            regimen_statements=bound_reasons(findings,'gdmt',{'documented_medication_regimen'})
            def declared_therapy_accounts(relation):
                entities=dict.fromkeys((f.get('entity')or{}).get('id')for f in findings
                    if isinstance(f,dict)and f.get('relation')==relation
                    and(f.get('entity')or{}).get('kind')in('medication','medication_class'))
                groups=[]
                for entity in entities:
                    for group in bound_reasons(findings,entity,{relation}):
                        group['entity']=next(f['entity']for f in group['findings']if f.get('entity'))
                        group['source_fields']=sorted({f['field']for f in group['findings']if f.get('field')})
                        groups.append(group)
                return groups
            # These declared relations are broader than a current non-use or
            # titration reason. Consume their actual meaning without changing
            # the assertion, inferring current use, or aliasing reason types.
            regimen_statements.extend(declared_therapy_accounts('therapy_status'))
            therapy_limitation_accounts=declared_therapy_accounts('therapy_reason')
            dose_limiting=bound_reasons(findings,'gdmt',{'dose_titration_reason'})+bound_reasons(findings,'gdmt_dose')
            represented_dose={finding_identity(f)for group in dose_limiting for f in group['findings']}
            for group in declared_therapy_accounts('dose_titration_reason'):
                if any(finding_identity(f)not in represented_dose for f in group['findings']):
                    dose_limiting.append(group)
                    represented_dose.update(finding_identity(f)for f in group['findings'])
            tolerance_statements=bound_reasons(findings,'gdmt',{'individual_maximum_tolerated_dose'})
            cardiology_statements=bound_reasons(findings,'cardiology')
            # Presentation categories are not an exhaustive relation registry.
            # Keep a new typed question visible without relabelling its meaning
            # as one of the older clinical reason/status contracts.
            displayed=[*regimen_statements,*therapy_limitation_accounts,*dose_limiting,*tolerance_statements,*cardiology_statements]
            for t in therapy:displayed.extend(t['documented_reasons']);displayed.extend(t['documented_status_accounts'])
            represented={finding_identity(f)for g in displayed for f in g['findings']}
            audit_ids=set();questions={}
            from ...contracts import digest
            for review in evidence.get('notes',{}).get(pid,[]):
                for field,q in (review.get('questions')or{}).items():
                    if isinstance(q,dict):questions[digest(q)]=q
                    if field in review.get('audit_only_fields',[]):
                        audit_ids.update((v.get('assertion')or{}).get('id')for v in (review.get('findings')or{}).get(field,[])if isinstance(v,dict)and(v.get('assertion')or{}).get('id'))
            for group in [*regimen_statements,*therapy_limitation_accounts,*dose_limiting]:
                if group.get('relation')in('therapy_status','therapy_reason','dose_titration_reason'):
                    group['source_questions']=list({digest(q):q for f in group['findings']
                        if(q:=questions.get((f.get('assertion')or{}).get('question_digest')))is not None}.values())
            remaining=[f for f in findings if isinstance(f,dict)and(f.get('entity')or{}).get('id')and finding_identity(f)not in represented and
                (f.get('assertion')or{}).get('id')not in audit_ids]
            additional=[]
            for entity,relation in dict.fromkeys(((f.get('entity')or{}).get('id'),f.get('relation'))for f in remaining):
                for group in bound_reasons(remaining,entity,{relation}):
                    group['entity']=next(f['entity']for f in group['findings']if f.get('entity'))
                    group['source_questions']=list({digest(q):q for f in group['findings']
                        if(q:=questions.get((f.get('assertion')or{}).get('question_digest')))is not None}.values())
                    additional.append(group)
            if row.get('max_dose_reason_findings')or dose_limiting:rationale.append('Review the documented dose-limiting statements below with the responsible clinician')
            program={'title':'Theoretical GDMT titration program'if therapy else'Clinical review',
                'status':'Potential review opportunity; eligibility requires clinical assessment',
                'rationale':rationale,'proposed_steps':row.get('proposed_review_steps'),
                'eligibility_established':False,
                'confirm':['Current medication use and doses','Individual tolerated maximum and current contraindications',
                    'Responsible clinician and patient preferences','Program availability and coverage']}if therapy else None
            cards[pid]={'patient_id':pid,'patient_name':profile.get('display_name')or'Name unavailable in received profile',
                'profile':profile,'clinical_context':clinical,'care_team':[care('Primary care'),care('Cardiology')],
                'cardiology_statements':cardiology_statements,
                'dated_correspondence_roles':narrative_roles,'therapy':therapy,'all_dose_observations':doses,
                'regimen_statements':regimen_statements,'dose_limiting_statements':dose_limiting,
                'therapy_limitation_accounts':therapy_limitation_accounts,
                'individual_tolerance_statements':tolerance_statements,'program_review':program,
                'additional_documentary_accounts':additional,
                'source_verification':patient_verification.get(pid),
                'empty_source_reason': 'Source interpretation unresolved: a reading is suspended pending source review. An empty active finding does not establish that a reason is absent.' if (patient_verification.get(pid) or {}).get('status') == 'suspended_source_reading' else None,
                'documentary_reconciliation':{'groups':state_groups,'current_clinical_state_established':False,
                    'summary':reconciliation_summary,
                    'basis':'Existing source assertion reconciliation; different accounts are retained without voting or inferring clinical change.'},
                'individual_max_tolerated_dose':('Dated source statements are retained below; the current individual maximum requires verification'
                    if tolerance_statements else'Not established by the reviewed evidence'),
                'source_reviews':evidence.get('notes',{}).get(pid,[]),'original_result_row':original_row}
    return cards


def source_reading_notice(context):
    status = (context.get('source_disposition') or {}).get('status')
    if status == 'suspended':
        return 'This saved answer relies on a suspended source reading. Review the source evidence or ask again before preparing program work. The original answer has not been recalculated.'
    if status == 'unavailable':
        return 'Source-reading status is unavailable. The saved evidence remains inspectable; restore source verification before changing work.'
    return None


def active_assertion_ids(context):
    """Use the same recorded retirement semantics as the patient projection.

    Prior readings and native execution history are inspectable provenance, not
    fresh dependencies. Live suspensions must not erase their own dependency.
    """
    evidence = projection(context, apply_source_disposition=False)
    active = set()

    def collect(value):
        if isinstance(value, str) and value[:1] in ('[', '{'):
            try:
                value = json.loads(value)
            except ValueError:
                return set()
        found = set()
        if isinstance(value, dict):
            assertion = value.get('assertion')
            if isinstance(assertion, dict) and isinstance(assertion.get('id'), str):
                found.add(assertion['id'])
            for key, item in value.items():
                if key.startswith(('prior_', 'legacy_', 'initial_source_')) or key in ('audit_only_assertion_ids', 'prior_findings'):
                    continue
                if key == 'assertions' and isinstance(item, list):
                    found.update(a['id'] for a in item if isinstance(a, dict) and isinstance(a.get('id'), str))
                else:
                    found.update(collect(item))
        elif isinstance(value, list):
            for item in value:
                found.update(collect(item))
        return found

    for notes in evidence['notes'].values():
        for note in notes:
            audit_ids = set()
            for field, values in (note.get('findings') or {}).items():
                if field not in note.get('audit_only_fields', []):
                    active.update(collect(values))
                else:
                    audit_ids.update(collect(values))
            active.update(collect(note.get('documentary_state')) - audit_ids)
    answer_ids = collect(context.get('answer')) | collect(context.get('patient_rows'))
    # Retirement in a source-review view cannot discharge a claim still carried
    # in an active answer field. Only explicit prior/audit fields are passive.
    return sorted(active | answer_ids)


def projection(context, *, apply_source_disposition=True):
    from ...contracts import digest
    investigation = context.get('investigation') or {}
    notes = {}
    replacements={};local_replacements={};reviews=investigation.get('document_reviews')or[]
    suspended = {item['assertion_id'] for item in (context.get('source_disposition') or {}).get('suspended_assertions', [])} if apply_source_disposition else set()
    for index,review in enumerate(reviews):
        recheck=review.get('candidate_observation_review')or{}
        if recheck.get('source_rows_digest')and'checked_assertions'not in recheck:
            replacements.setdefault(recheck['source_rows_digest'],set()).update(recheck.get('checked_fields')or[])
        for prior in range(index):
            local_replacements.setdefault(prior,set()).update((c['patient_id'],c['field'],c['assertion_id'])
                for c in recheck.get('checked_assertions',[]))
    for index,review in enumerate(reviews):
        questions = review.get('questions') or {}
        audit_fields=sorted(replacements.get(digest(review.get('rows')or[]),set()))
        for row in review.get('rows') or []:
            pid = row.get('patient_id')
            if not pid:
                continue
            raw = row.get('review_json')
            try:
                found = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError):
                found = None
            prior_findings={};retired=[]
            if isinstance(found,dict):
                active={}
                for field,values in found.items():
                    if not isinstance(values,list):active[field]=values;continue
                    active[field]=[]
                    for value in values:
                        ident=(value.get('assertion')or{}).get('id')if isinstance(value,dict)else None
                        if ident and (ident in suspended or (pid,field,ident)in local_replacements.get(index,set())):
                            prior_findings.setdefault(field,[]).append(value);retired.append(ident)
                        else:active[field].append(value)
                found=active
            raw_state=row.get('documentary_state_json')
            try:documentary_state=json.loads(raw_state)if isinstance(raw_state,str)else raw_state
            except (TypeError,ValueError):documentary_state=None
            if documentary_state:
                from ..clinical.assertion_reconciliation import reconcile
                retired.extend(a['id'] for a in documentary_state.get('assertions', [])
                               if a.get('id') in suspended and a['id'] not in retired)
                assertions=[a for a in documentary_state.get('assertions',[])if a.get('id')not in retired]
                if retired:
                    documentary_state={**documentary_state,'assertions':assertions,
                        'groups':reconcile(pid,assertions,(context.get('source')or{}).get('as_of'))}
            raw_roles=row.get('source_clinicians_json')or'[]'
            try:roles=json.loads(raw_roles)if isinstance(raw_roles,str)else raw_roles
            except (TypeError,ValueError):roles=None
            try:
                scope=row.get('evidence_scope_json')
                scope=json.loads(scope)if isinstance(scope,str)else scope
                scope={k:scope[k]for k in ('inventory_documents','selected_documents','field_documents','field_applicability',
                    'complete_chart_review','absence_established','expansion')if k in scope}if isinstance(scope,dict)else None
                pending=row.get('review_unresolved_json')
                pending=json.loads(pending)if isinstance(pending,str)else pending
                unresolved=len(pending)if isinstance(pending,list)else None
            except (TypeError,ValueError):scope=None;unresolved=None
            notes.setdefault(pid, []).append({
                'source_clinicians':roles,
                'documentary_state':documentary_state,
                'source_scope':review.get('source_scope'),
                'evidence_scope':scope,'unresolved_source_fields':unresolved,
                'questions': questions, 'findings': found,
                'prior_findings':prior_findings,'audit_only_assertion_ids':retired,
                'audit_only_fields':audit_fields,
                'candidate_review_scope':review.get('candidate_observation_review'),
                'source_reading_suspended': bool(set(retired) & suspended),
                'review_status': 'Suspended source reading; review required' if set(retired) & suspended else row.get('review_status'),
                'documents_reviewed': row.get('documents_reviewed'),
                'limitations': row.get('review_limitations')})
    # Last executed table with the same schema is the most recent observation
    # of that schema, not a merged or clinically reconciled state. Aggregated
    # final-answer tables and earlier rejected summaries are not substituted.
    tables = {}
    for call in investigation.get('tool_calls') or []:
        out = call.get('result')
        if call.get('name') != 'query_sql' or not isinstance(out, dict) or out.get('error'):
            continue
        columns = out.get('columns') or []
        if 'patient_id' not in columns or not ({'drug_name', 'dose_status', 'assignment_status'} & set(columns)):
            continue
        rows = [dict(zip(columns, r)) if isinstance(r, (list, tuple)) else r
                for r in out.get('rows') or []]
        if any(not isinstance(r, dict) for r in rows):
            continue
        tables[tuple(columns)] = {'result_view': out.get('result_view'), 'columns': columns, 'rows': rows,
            'query': out.get('executed_query') or (call.get('input') or {}).get('sql'),
            'truncated': bool(out.get('truncated'))}
    return {'notes': notes, 'supporting_tables': list(tables.values()),
            'basis': 'Already executed supporting observations; no new clinical interpretation. Historical fills, documented dose statements and referrals retain their original meaning.'}
