"""Carry already executed patient evidence into every saved report format.

These are attributed observations, never new medication/reason adjudications.
Keep the answer intact; the complete native context remains the download.
"""
import json,re


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
    def decoded(value):
        if isinstance(value,str)and value[:1]in('[','{'):
            try:return json.loads(value)
            except ValueError:pass
        return value
    def items(value):
        value=decoded(value);return value if isinstance(value,list)else[]
    def person_name(cid):return(clinicians.get(cid)or{}).get('display_name')or cid or'Name unavailable'
    def reasons(findings,entity,relations=None):
        groups={}
        for f in findings:
            if not isinstance(f,dict)or(f.get('entity')or{}).get('id')!=entity:continue
            if relations is not None and f.get('relation')not in relations:continue
            key=tuple(str(f.get(k)or'')for k in ('quote','relation','polarity','temporality','subject'))
            group=groups.setdefault(key,{'quote':f.get('quote'),'relation':f.get('relation'),'polarity':f.get('polarity'),
                'temporality':f.get('temporality'),'subject':f.get('subject'),'sources':[],'findings':[]})
            source={'record_id':f.get('record_id'),'date':f.get('date')}
            if source not in group['sources']:group['sources'].append(source)
            group['findings'].append(f)
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
            aliases={'on_rasi_pillar':('on_raas_inhibitor','raas_pillar'),
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
            findings=items(row.get('documentary_findings'));fills=items(row.get('recent_gdmt_fill_observations'));doses=items(row.get('gdmt_dose_rows'))
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
                    'current_use':'Confirm current regimen','fills':observed,'dose_observations':ds,
                    'documented_reasons':reasons(findings,entity)})
            rationale=[]
            if row.get('hfref_basis'):rationale.append('Source-defined HFrEF cohort: '+str(row['hfref_basis']).replace('_',' '))
            missing=row.get('pillars_missing')
            if 'pillars_missing'not in row:missing=', '.join(label for group,label,field,entity,classes in pillars if row.get(field)==0)
            if missing:rationale.append(('Reconcile pillars without a fill in the returned history: 'if historical_fill else'Reconcile pillars without a recent-fill observation: ')+str(missing).replace('_',' '))
            if doses:rationale.append('Review dated dose/state observations and their reference-dose comparisons')
            regimen_statements=reasons(findings,'gdmt',{'documented_medication_regimen'})
            dose_limiting=reasons(findings,'gdmt',{'dose_titration_reason'})+reasons(findings,'gdmt_dose')
            tolerance_statements=reasons(findings,'gdmt',{'individual_maximum_tolerated_dose'})
            if row.get('max_dose_reason_findings')or dose_limiting:rationale.append('Review the documented dose-limiting statements below with the responsible clinician')
            program={'title':'Theoretical GDMT titration program'if therapy else'Clinical review',
                'status':'Potential review opportunity; eligibility requires clinical assessment',
                'rationale':rationale,'proposed_steps':row.get('proposed_review_steps'),
                'eligibility_established':False,
                'confirm':['Current medication use and doses','Individual tolerated maximum and current contraindications',
                    'Responsible clinician and patient preferences','Program availability and coverage']}if therapy else None
            cards[pid]={'patient_id':pid,'patient_name':profile.get('display_name')or'Name unavailable in received profile',
                'profile':profile,'clinical_context':clinical,'care_team':[care('Primary care'),care('Cardiology')],
                'cardiology_statements':reasons(findings,'cardiology'),
                'dated_correspondence_roles':narrative_roles,'therapy':therapy,'all_dose_observations':doses,
                'regimen_statements':regimen_statements,'dose_limiting_statements':dose_limiting,
                'individual_tolerance_statements':tolerance_statements,'program_review':program,
                'individual_max_tolerated_dose':('Dated source statements are retained below; the current individual maximum requires verification'
                    if tolerance_statements else'Not established by the reviewed evidence'),
                'source_reviews':evidence.get('notes',{}).get(pid,[]),'original_result_row':original_row}
    return cards


def projection(context):
    from ...contracts import digest
    investigation = context.get('investigation') or {}
    notes = {}
    replacements={}
    for review in investigation.get('document_reviews')or[]:
        recheck=review.get('candidate_observation_review')or{}
        if recheck.get('source_rows_digest'):
            replacements.setdefault(recheck['source_rows_digest'],set()).update(recheck.get('checked_fields')or[])
    for review in investigation.get('document_reviews') or []:
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
                'source_scope':review.get('source_scope'),
                'evidence_scope':scope,'unresolved_source_fields':unresolved,
                'questions': questions, 'findings': found,
                'audit_only_fields':audit_fields,
                'candidate_review_scope':review.get('candidate_observation_review'),
                'review_status': row.get('review_status'),
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
