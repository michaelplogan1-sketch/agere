"""Deterministic PDF companion to saved patient or grouped analyses; no model calls."""
from pathlib import Path
import json,re,html,sys,io,gzip,hashlib
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak,KeepTogether
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import HexColor,white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader,PdfWriter

def _note_excerpt(quote,entity=None):
 """Copy relevant sentences plus adjacent qualifications, never paraphrase them.

 Entity aliases come from the existing assertion contract. This only chooses
 what to quote; it does not create a source finding or endorse its interpretation.
 """
 text=str(quote or '').strip()
 lines=[line.strip() for line in text.splitlines() if line.strip()]
 decision=[line for line in lines if re.match(r'^(?:\d+\.\s*)?(?:decision(?: and plan)?|patient report and decision|medication discussion)\s*:',line,re.I)]
 if decision:
  # Keep explicit qualifications even when supplied in a separate paragraph.
  qualifications=[line for line in lines if line not in decision and re.search(r'\b(?:reason|rationale|uncertain|undocumented|unknown|not established|not confirmed|pending clarification)\b',line,re.I)]
  text='\n'.join(decision+qualifications)
 else:
  reasons=[line for line in lines if re.search(r'\bbecause\b|\bnot (?:being )?used\b|\breason\b|\bwithheld\b|\bheld for\b',line,re.I)]
  text='\n'.join(reasons) if reasons else text
 sentences=[s.strip()for s in re.split(r'(?<=[.!?])\s+(?=[A-Z0-9])|\n+',text)if s.strip()]
 aliases=(entity or{}).get('aliases')or[]
 matches=lambda s:any(re.search(r'(?<!\w)'+re.escape(a)+r'(?!\w)',s,re.I)for a in aliases)
 chosen={i for i,s in enumerate(sentences)if matches(s)}if aliases else set()
 if not chosen:
  chosen={i for i,s in enumerate(sentences)if re.search(r'\bbecause\b|\b(?:did not|not) (?:increase|escalate)\b|\bheld for\b|\b(?:reason|rationale)\b',s,re.I)}
 if not chosen:return text
 from ..clinical.drug_classes import classify
 # Pronouns, denial of supply, missing rationale and pending clarification
 # remain beside their named medicine. Stop at another named medication.
 for i in sorted(chosen):
  j=i+1
  while j<len(sentences)and not classify(sentences[j]):chosen.add(j);j+=1
 # An omission marker makes noncontiguous selections explicit to the reader.
 ordered=sorted(chosen);out=[];prior=None
 for i in ordered:
  if prior is not None:out.append(' 'if i==prior+1 else'\n[...]\n')
  out.append(sentences[i]);prior=i
 return ''.join(out)


def _render_clinical(context,output,cards,references):
 """Curated patient-facing reading path over the unchanged saved evidence."""
 from collections import Counter
 from datetime import date
 for name,filename in [('AgereSans','Arial.ttf'),('AgereBold','Arial Bold.ttf')]:
  font=Path('/System/Library/Fonts/Supplemental')/filename
  if not font.is_file():
   import reportlab
   font=Path(reportlab.__file__).parent/'fonts'/('Vera.ttf'if name=='AgereSans'else'VeraBd.ttf')
  pdfmetrics.registerFont(TTFont(name,str(font)))
 navy=HexColor('#173442');teal=HexColor('#177D79');muted=HexColor('#52636E');rule=HexColor('#DCE5E8');pale=HexColor('#F1F6F7');amber=HexColor('#8A5917')
 styles={
  'body':ParagraphStyle('ClinicalBody',fontName='AgereSans',fontSize=10.2,leading=14.2,textColor=navy,spaceAfter=5),
  'small':ParagraphStyle('ClinicalSmall',fontName='AgereSans',fontSize=8.7,leading=11.8,textColor=muted,spaceAfter=3),
  'title':ParagraphStyle('ClinicalTitle',fontName='AgereBold',fontSize=30,leading=34,textColor=navy,spaceAfter=14),
  'name':ParagraphStyle('ClinicalName',fontName='AgereBold',fontSize=23,leading=27,textColor=navy,spaceAfter=4),
  'head':ParagraphStyle('ClinicalHeading',fontName='AgereBold',fontSize=12,leading=16,textColor=teal,spaceBefore=8,spaceAfter=4,keepWithNext=True),
  'label':ParagraphStyle('ClinicalLabel',fontName='AgereBold',fontSize=9.2,leading=12.5,textColor=navy,spaceAfter=3,keepWithNext=True),
  'quote':ParagraphStyle('ClinicalQuote',fontName='AgereSans',fontSize=9.5,leading=13.2,textColor=navy,spaceAfter=9,leftIndent=9,allowWidows=0,allowOrphans=0),
  'notice':ParagraphStyle('ClinicalNotice',fontName='AgereBold',fontSize=9.2,leading=12.5,textColor=amber,spaceAfter=4),
 }
 def P(text,style='body'):
  return Paragraph(html.escape(str(text)).replace('\n','<br/>'),styles[style])
 def human(value):return str(value or '').replace('_',' ')
 def D(value):
  try:return date.fromisoformat(str(value)[:10]).strftime('%d %b %Y').lstrip('0')
  except ValueError:return str(value or 'Date not recorded')
 def N(value):
  if isinstance(value,(int,float)):return f'{value:g}'
  return str(value)
 def group_dates(group):
  dates=sorted({str(s['date']) for s in group.get('sources',[]) if s.get('date')})
  return (D(dates[0])+' to '+D(dates[-1]) if len(dates)>1 else D(dates[0])) if dates else 'Date not recorded'
 def box(rows,widths):
  t=Table(rows,colWidths=widths,hAlign='LEFT')
  t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,-1),pale),('LEFTPADDING',(0,0),(-1,-1),11),('RIGHTPADDING',(0,0),(-1,-1),11),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
  return t
 def team_text(c,role):
  team=next((t for t in c['care_team'] if t['role']==role),{})
  if team.get('current'):
   return '\n'.join(a['name']+'\nAssignment recorded '+D(a.get('source_date')) for a in team['current'])
  others=team.get('other_records')or[]
  text='Current assignment not established'
  if others:
   seen=set()
   for a in sorted(others,key=lambda a:str(a.get('source_date')or''),reverse=True):
    key=(a.get('name'),a.get('assignment_status'))
    if key in seen:continue
    seen.add(key);text+='\n'+str(a.get('name')or'Name not recorded')+' - '+human(a.get('assignment_status'))+' ('+D(a.get('source_date'))+')'
  dated={}
  wanted={'cardiology','cardiologist'}if role=='Cardiology'else{'primary care','primary care physician','pcp'}
  for a in c.get('dated_correspondence_roles',[]):
   if human(a.get('documented_role')).casefold()in wanted:
    dated.setdefault(str(a.get('name')or'Name not recorded'),set()).add(str(a.get('date')or''))
  for name,dates in dated.items():text+='\n'+name+' - signed '+role.lower()+' correspondence ('+D(max(dates))+'); assignment unconfirmed'
  return text
 def observed_pillars(c):return sum(t['fill_observed']is True for t in c['therapy'])
 ordered=list(cards.values());cutoff=(context.get('source')or{}).get('as_of');answer=context['answer']
 count=len(ordered);distribution=Counter(observed_pillars(c)for c in ordered)
 dose_patients=sum(bool(c.get('all_dose_observations'))for c in ordered)
 pcp=sum(any(t['role']=='Primary care'and t['current']for t in c['care_team'])for c in ordered)
 cardio=sum(any(t['role']=='Cardiology'and t['current']for t in c['care_team'])for c in ordered)
 story=[P('CLINICAL REVIEW  /  SYNTHETIC HEALTHCARE WAREHOUSE','label'),Spacer(1,16),P('Medication optimization\nPatient review','title'),P('A clinical discussion brief for the theoretical GDMT titration program.'),P('Source snapshot: '+D(cutoff)+'  |  '+str(count)+' patients','small'),Spacer(1,14)]
 from .patient_evidence import source_reading_notice
 notice=source_reading_notice(context)
 if notice:story.append(P(notice,'notice'))
 story.append(box([[ [P(str(count),'name'),P('Patients for review','small')], [P(str(dose_patients),'name'),P('Patients with a dated dose/state record','small')], [P(str(pcp),'name'),P('Patients with a recorded current PCP assignment','small')] ]],[180]*3))
 story.extend([P('The opportunity to review','head'),P('The saved cohort identifies patients with HFrEF evidence and incomplete four-pillar fill observations. These findings support medication reconciliation and a discussion of program suitability; they do not establish that a patient is untreated or eligible for titration.')])
 fill_rows=[[P('Pillars with a fill observation','label'),P('Patients','label'),P('Review focus','label')]]
 for k in range(5):
  fill_rows.append([P(str(k)+' of 4'),P(distribution[k]),P('Reconcile medicines and the documented reasons for gaps' if k<4 else 'Confirm the current regimen and individual tolerated doses','small')])
 t=Table(fill_rows,colWidths=[170,70,300],repeatRows=1)
 t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),pale),('LINEBELOW',(0,0),(-1,-1),.4,rule),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]));story.append(t)
 scopes={t.get('fill_scope_display',t['fill_scope'])for c in ordered for t in c['therapy']}
 story.append(P('Fill window: '+('; '.join(sorted(scopes)))+'.','small'))
 story.extend([P('Decisions to resolve with the clinical team','head'),P('1. Confirm each patient\'s current medicines, doses and access barriers.\n2. Review dated non-use or titration accounts against present clinical findings.\n3. Confirm the responsible PCP/cardiologist and individual tolerated dose before discussing enrollment.'),P('Care-team coverage: '+str(pcp)+' current PCP assignments and '+str(cardio)+' current cardiology assignments documented. A dated mention or referral is shown separately from current responsibility.','small'),P('Reading the patient reviews','head'),P('Each review brings together clinical observations, care-team information, medication fills and relevant dated chart excerpts. A missing fill is not proof of non-use. A reference maximum is not an individual tolerated maximum. Unsettled chart interpretations are marked for review.','small')])
 for c in ordered:
  story.append(PageBreak());profile=c['profile'];pid=c['patient_id']
  story.append(P('PATIENT REVIEW','label'));heading=P(c['patient_name'],'name');heading.agere_record={'patient_id':str(pid),'record_index':len([x for x in story if hasattr(x,'agere_record')])};story.append(heading)
  identity=[]
  if profile.get('age_at_snapshot')is not None:identity.append(str(profile['age_at_snapshot'])+' years at snapshot')
  if profile.get('sex_reported'):identity.append({'F':'Female','M':'Male'}.get(profile['sex_reported'],profile['sex_reported']))
  identity.append(str(pid));story.append(P('  |  '.join(identity),'small'))
  if profile.get('interpreter_needed'):story.append(P('Interpreter needed'+(' - '+str(profile['preferred_language'])if profile.get('preferred_language')else''),'label'))
  n=observed_pillars(c)
  story.extend([P('Why review this patient','head'),P('HFrEF evidence in the saved cohort; '+str(n)+' of 4 pillars have a fill observation in the saved analysis. '+('Reconcile the remaining pillar observations and assess program suitability.'if n<4 else'Confirm current doses and whether further optimization is appropriate.'))])
  reconciliation=(c.get('documentary_reconciliation')or{}).get('summary')
  if reconciliation:story.append(P(reconciliation,'notice'))
  verification=c.get('source_verification')
  if verification and(verification.get('label')!='supported'or not verification.get('decided_by')):
   story.append(P(verification.get('notice')or'The saved source-support check is unresolved for this patient. Confirm the recorded findings against the source records before using the interpretation.','notice'))
  observations=[]
  clinical={f['label']:f for f in c['clinical_context']}
  for key in ['Lowest documented EF','Latest qualifying low-EF observation','creatinine','egfr','potassium','systolic bp','diastolic bp','heart rate']:
   f=clinical.get(key)
   if not f:continue
   names={'Lowest documented EF':'Lowest recorded EF (historical)','Latest qualifying low-EF observation':'Latest qualifying low-EF note','egfr':'eGFR','creatinine':'Creatinine','potassium':'Potassium','systolic bp':'Systolic BP','diastolic bp':'Diastolic BP','heart rate':'Heart rate'}
   v=D(f['value'])if key=='Latest qualifying low-EF observation'else str(f.get('value_censor')or'')+N(f.get('value'))+(' '+str(f['unit'])if f.get('unit')else'')
   detail=D(f.get('date'))if f.get('date')else('Current EF needs confirmation'if key=='Lowest documented EF'else'')
   if(f.get('same_day_values')or 0)>1:detail+='; '+str(f['same_day_values'])+' values recorded that day; minimum '+N(f.get('same_day_min'))
   observations.append([P(names[key]+': '+v,'label'),P(detail,'small')] if detail else [P(names[key]+': '+v,'label')])
  if observations:
   grid=[observations[i:i+3]for i in range(0,len(observations),3)]
   while len(grid[-1])<3:grid[-1].append('')
   t=Table(grid,colWidths=[180]*3);t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,-1),pale),('LEFTPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),2)]));story.append(t)
  story.extend([P('Care team','head'),box([[[P('PRIMARY CARE','label'),P(team_text(c,'Primary care'))],[P('CARDIOLOGY','label'),P(team_text(c,'Cardiology'))]]],[270,270])])
  matrix=[[P('Medication pillar','label'),P('Fill observation and named medicines','label'),P('Dose information','label')]]
  for therapy in c['therapy']:
   from .patient_evidence import fill_name
   fills=therapy['fills'];fill_text=(therapy['observed_status']+('; medicine name not supplied'if therapy['fill_observed']else''))if not fills else'\n'.join(fill_name(f)+'\nLatest fill '+D(f.get('latest_fill_date_in_window')or f.get('latest_fill_12m'))for f in fills)
   doses=[]
   for d in therapy['dose_observations']:
    v=d.get('documented_daily_mg');text=human(d.get('drug_key'))+': '+(N(v)+' mg/day recorded'if v is not None else'daily dose not established')
    if d.get('dose_date'):text+=' ('+D(d['dose_date'])+')'
    if d.get('statement_state')or d.get('documented_statement_state'):text+='; '+human(d.get('statement_state')or d.get('documented_statement_state'))
    if d.get('target_max_daily_mg')is not None:text+='; reference maximum '+N(d['target_max_daily_mg'])+' mg/day'
    if not d.get('current_use_established'):text+='; confirm current use'
    doses.append(text)
   matrix.append([P(therapy['label'],'label'),P(fill_text,'small'),P('\n'.join(doses)or'Current dose unconfirmed','small')])
  story.append(P('Medication review','head'));t=Table(matrix,colWidths=[135,205,200],repeatRows=1)
  t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),pale),('LINEBELOW',(0,0),(-1,-1),.4,rule),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]));story.append(t)
  tolerance='Dated tolerance account follows; the current individual maximum needs confirmation'if c['individual_tolerance_statements']else c['individual_max_tolerated_dose']
  story.append(P('Individual maximum tolerated dose: '+str(tolerance)+'.','small'))
  if c.get('regimen_statements'):
   story.append(P('Dated therapy-status history is retained in the interactive regimen view. It does not establish current medication use.','small'))
  # One dated clinical account can support several fields. Print it once with
  # all its uses, rather than repeating the same whole note under each pillar.
  accounts={}
  additional=[(human(g['entity']['id'])+' / dated chart account',[g])for g in c.get('additional_documentary_accounts')or[]]
  limitations=[(human(g['entity']['id'])+' / recorded therapy limitation (non-use or dose, as documented)',[g])for g in c.get('therapy_limitation_accounts')or[]]
  for label,groups in [(t['label'],t['documented_reasons'])for t in c['therapy']]+[('Titration',c['dose_limiting_statements']),('Individual tolerated maximum',c['individual_tolerance_statements'])]+limitations+additional:
   for g in groups:
    entity=next((f.get('entity')for f in g.get('findings',[])if f.get('entity')),None)
    excerpt=_note_excerpt(g.get('quote'),entity);key=excerpt
    a=accounts.setdefault(key,{'text':excerpt,'labels':[],'groups':[]})
    if label not in a['labels']:a['labels'].append(label)
    a['groups'].append(g)
  story.append(P('Documented explanations to discuss','head'))
  if limitations:
   story.append(P('Recorded therapy limitations may concern non-use or a dose change. Their relevance to a current missing-pillar or titration question remains to be confirmed.','small'))
  if not accounts:story.append(P(c.get('empty_source_reason') or 'No explicit non-use or titration explanation was returned in the reviewed source scope. Clarify the reason with the patient and responsible clinician.'))
  else:
   missing=[t['label']for t in c['therapy']if not t['documented_reasons']]
   if additional:
    story.append(P('Review the dated accounts below alongside the medication table. These accounts have not been assigned as explanations for a specific current treatment gap.','small'))
   elif missing:
    note=P((c.get('empty_source_reason') + ' Review: ' if c.get('empty_source_reason') else 'No separate non-use reason returned for: ')+', '.join(missing)+'.','small');note.keepWithNext=True;story.append(note)
  # A new, otherwise-unrecognised relation keeps its exact question meaning,
  # once per patient/contract rather than repeated under every source account.
  questions={q['question']for g in c.get('additional_documentary_accounts')or[]for q in g.get('source_questions')or[]if q.get('question')}
  for question in sorted(questions):story.append(P('Additional account scope: '+question,'small'))
  for a in accounts.values():
   dates=sorted({str(s['date']) for g in a['groups']for s in g.get('sources',[])if s.get('date')})
   dated=D(dates[0])+' to '+D(dates[-1])if len(dates)>1 else D(dates[0])if dates else'Date not recorded'
   account=[P(' / '.join(a['labels'])+'  |  '+dated,'label')]
   if any(g.get('review_status')for g in a['groups']):account.append(P('Unresolved chart interpretation - confirm before using this account.','notice'))
   qualifications=sorted({human(g.get('polarity')) for g in a['groups']if g.get('polarity')not in [None,'affirmed']})
   if qualifications:account.append(P('The source account is '+', '.join(qualifications)+'.','small'))
   account.append(P(a['text'],'quote'));story.append(KeepTogether(account))
 if references:
  story.extend([PageBreak(),P('Clinical reference sources','title')])
  for ref in references:story.extend([P(ref['title']),P(ref['url'],'small')])
 buf=io.BytesIO();record_pages=[]
 def page(canvas,doc):
  canvas.saveState();canvas.setStrokeColor(rule);canvas.line(36,36,576,36);canvas.setFillColor(muted);canvas.setFont('AgereSans',8)
  active=record_pages[-1]if record_pages else None
  canvas.drawString(36,23,(cards[active['patient_id']]['patient_name']+'  |  'if active else'')+'SYNTHETIC REVIEW  |  '+D(cutoff));canvas.drawRightString(576,23,str(doc.page))
  if active and doc.page>active['page']:
   canvas.setFont('AgereBold',9);canvas.drawString(36,773,cards[active['patient_id']]['patient_name']+' / continued')
  canvas.restoreState()
 doc=SimpleDocTemplate(buf,pagesize=(612,792),leftMargin=36,rightMargin=36,topMargin=36,bottomMargin=48,title='Medication optimization - patient review',author='Clinical review')
 def after(flowable):
  if hasattr(flowable,'agere_record'):record_pages.append({**flowable.agere_record,'page':doc.page})
 doc.afterFlowable=after
 doc.afterPage=lambda:page(doc.canv,doc)
 from reportlab.pdfgen.canvas import Canvas
 def deterministic_canvas(*args,**kwargs):kwargs['invariant']=1;return Canvas(*args,**kwargs)
 doc.build(story,canvasmaker=deterministic_canvas)
 reader=PdfReader(io.BytesIO(buf.getvalue()));writer=PdfWriter();writer.append(reader);writer.add_outline_item('Cohort brief',0)
 parent=writer.add_outline_item('Patient reviews',record_pages[0]['page']-1)
 for entry in record_pages:writer.add_outline_item(cards[entry['patient_id']]['patient_name'],entry['page']-1,parent=parent)
 with Path(output).open('wb')as f:writer.write(f)
 return {'output':str(output),'pages':len(reader.pages),'rows':len(ordered),'distinct_patient_ids':count,'new_model_calls':0,'analytical_status':answer.get('status'),'source_as_of':cutoff,'patient_bookmarks':record_pages,'narrative_display':'Curated clinical cohort brief and named patient reviews. No technical companion or execution archive in the PDF. Original evidence remains in the saved application records.','presentation_version':'curated-clinical-review/1'}

def render(context,output,references=None,evidence=None):
 from .patient_evidence import RECOMMENDATION_FIELD_PATTERN,review_cards
 output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
 if evidence is None:
  from .patient_evidence import projection
  evidence=projection(context)
 cards=review_cards(context,evidence)
 if cards and all(len(c.get('therapy',[]))==4 and any(f['label']=='Heart failure evidence'and f.get('value')for f in c['clinical_context'])and all(type(t.get('fill_observed'))is bool for t in c['therapy'])for c in cards.values()):
  return _render_clinical(context,output,cards,references)
 for name,filename in [('AgereSans','Arial.ttf'),('AgereBold','Arial Bold.ttf')]:
  font=Path('/System/Library/Fonts/Supplemental')/filename
  if not font.is_file():
   import reportlab
   font=Path(reportlab.__file__).parent/'fonts'/('Vera.ttf'if name=='AgereSans'else'VeraBd.ttf')
  pdfmetrics.registerFont(TTFont(name,str(font)))
 ink=HexColor('#183d35');teal=HexColor('#2b7363');muted=HexColor('#52645f');line=HexColor('#d6ded9');paper=HexColor('#f0f4ef')
 body=ParagraphStyle('Body',fontName='AgereSans',fontSize=8.5,leading=12,spaceAfter=7,textColor=ink,wordWrap='CJK')
 small=ParagraphStyle('Small',parent=body,fontSize=7,leading=9.5)
 title=ParagraphStyle('Title',parent=body,fontName='AgereBold',fontSize=26,leading=31,spaceAfter=17)
 head=ParagraphStyle('Head',parent=body,fontName='AgereBold',fontSize=13,leading=17,spaceBefore=12,spaceAfter=7,textColor=teal,keepWithNext=True)
 labelstyle=ParagraphStyle('Label',parent=small,fontName='AgereBold',keepWithNext=True)
 def P(v,style=body):return Paragraph(html.escape(str(v)).replace('\n','<br/>'),style)
 def value(v):
  if v is None:return 'Unknown / not supplied'
  if isinstance(v,(dict,list)):return json.dumps(v,ensure_ascii=False,indent=2)
  return str(v)
 def label(v):return str(v).replace('_',' ')
 def decoded(v):
  if isinstance(v,str)and v[:1]in ('[','{'):
   try:
    parsed=json.loads(v)
    if isinstance(parsed,(dict,list)):return parsed
   except ValueError:pass
  return v
 def content(v):
  """Readable native collections, retaining every field and source object."""
  v=decoded(v)
  if isinstance(v,list):
   if not v:return [P('No recorded items',small)]
   out=[]
   for i,item in enumerate(v):
    out.extend([P('Entry '+str(i+1),labelstyle),*content(item),Spacer(1,5)])
   return out
  if isinstance(v,dict):
   if not v:return [P('No recorded fields',small)]
   assertion=v.get('assertion')
   if isinstance(assertion,dict)and isinstance(v.get('quote'),str)and v.get('record_id'):
    # The source passage and its clinical qualifications belong in print.
    # Repeating the same passage in value/evidence/assertion plus internal
    # hashes adds no clinical information. Exact bindings stay embedded.
    entity=v.get('entity')or{};clocks=assertion.get('clocks')or{}
    out=[P(label(v.get('relation'))+' / '+str(entity.get('id')or'Unspecified entity'),labelstyle),
         P('Polarity: '+str(v.get('polarity'))+'; source timing: '+str(v.get('temporality'))+'; subject: '+str(v.get('subject')),small),
         P(v['quote'],small),P('Source: '+str(v['record_id'])+'; source date: '+str(v.get('date'))+
             '; received: '+str(clocks.get('arrival_date')or'Not recorded'),small)]
    for clock in ('clinical_date','reference_date'):
     if clocks.get(clock):out.append(P(label(clock)+': '+str(clocks[clock]),small))
    out.append(P('Current clinical applicability: '+('not established'if not assertion.get('current_clinical_state_established')else'refer to the saved source assessment')+'. Complete assertion bindings are in the embedded evidence.',small))
    return out
   out=[];same_quote=isinstance(v.get('quote'),str)and v.get('value')==v['quote']
   scalar=[];nested=[]
   for k,item in v.items():
    if same_quote and k=='value':continue
    if k=='review_unresolved_json'and isinstance(decoded(item),list):
     gaps=decoded(item)
     from collections import Counter
     counts=Counter(g.get('field','unspecified')for g in gaps if isinstance(g,dict))
     out.extend([P('Unresolved source-field checks',labelstyle),
       P(str(len(gaps))+' addressable checks remain unresolved: '+('; '.join(label(field)+': '+str(n)for field,n in counts.items())or'none')+'. Complete source IDs, spans and fingerprints remain in the embedded evidence and interactive review; these are not negative clinical findings.',small)])
     continue
    if k in ('value','quote')and isinstance(item,str):
     out.extend([P('Recorded value and source quote'if same_quote else label(k),labelstyle),P(item,small)])
    elif isinstance(decoded(item),(dict,list)):nested.append((k,item))
    else:scalar.append(label(k)+': '+value(item))
   if scalar:out.append(P('; '.join(scalar),small))
   for k,item in nested:
    out.append(P(label(k),labelstyle));out.extend(content(item))
   return out
  return [P(value(v),small)]
 def parts(a):
  yield a
  for s in a.get('sections')or[]:
   if isinstance(s,dict)and isinstance(s.get('answer'),dict):yield from parts(s['answer'])
 def clinical_story(c):
  """The clinical reading path; original analytical rows stay embedded intact."""
  out=[];profile=c['profile']
  age=str(profile['age_at_snapshot'])+' years'if profile.get('age_at_snapshot')is not None else'Age not established'
  out.append(P(age+' / '+str(profile.get('sex_reported')or'Sex not recorded')+' / '+c['patient_id'],small))
  reconciliation=(c.get('documentary_reconciliation')or{}).get('summary')
  if reconciliation:out.extend([P('Source accounts to reconcile',head),P(reconciliation,small)])
  out.append(P('Clinical context',head))
  observations=[]
  for f in c['clinical_context']:
   text=f['label']+': '+str(f.get('value_censor')or'')+value(f.get('value'))+(' '+str(f['unit'])if f.get('unit')else'')
   if f.get('date'):text+=' / '+str(f['date'])
   if f.get('interpretation'):text+=' / '+f['interpretation']
   if(f.get('same_day_values')or 0)>1:text+=' / '+str(f['same_day_values'])+' values that day; minimum '+str(f.get('same_day_min'))+'; review source values'
   observations.append(P(text,small))
  if observations:
   cells=[observations[i:i+2]for i in range(0,len(observations),2)]
   if len(cells[-1])<2:cells[-1].append('')
   grid=Table(cells,colWidths=[270,270]);grid.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,-1),paper),('BOTTOMPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),6)]));out.append(grid)
  out.append(P('Responsible clinicians',head))
  for team in c['care_team']:
   out.append(P(team['role']+': '+team['status'],labelstyle))
   for a in team['current']:
    out.append(P(a['name']+' / '+str(a.get('specialty')or a.get('role')or'Role not supplied')+' / effective '+str(a.get('effective_start'))+' / source '+str(a.get('source_date'))))
   for a in team['other_records']:
    out.append(P('Other record: '+a['name']+' / '+str(a.get('assignment_status'))+' / source '+str(a.get('source_date')),small))
  program=c['program_review']
  if program:
   out.extend([P('Why review for '+program['title'],head),P(program['status'],labelstyle)])
   out.extend(P('• '+reason)for reason in program['rationale'])
   out.append(P('Before enrollment or titration: '+'; '.join(program['confirm'])+'.',small))
  out.extend([P('Four-pillar medication and dose review',head),P('Fills describe the analysis window and require current-regimen confirmation. A reference maximum does not establish this patient’s maximum tolerated dose.',small)])
  def grouped_reasons(groups):
   text=[]
   if not groups:return[P(c.get('empty_source_reason') or 'No explicit reason returned in the reviewed scope. Confirm with the patient and responsible clinician.',small)]
   for g in groups:
    if g.get('review_status'):
     text.extend([P(g['review_status'],labelstyle),P(g['review_qualification'],small)])
    text.extend([P('Documented account / '+str(g.get('polarity'))+' / '+str(g.get('temporality'))+' / '+str(g.get('subject')),labelstyle),P(g.get('quote'),small)])
    text.append(P('Dated sources: '+'; '.join(str(s.get('date'))+' / '+str(s.get('record_id'))for s in g['sources']),small))
   return text
  if c['cardiology_statements']:
   out.extend([P('Dated cardiology follow-up accounts',head),*grouped_reasons(c['cardiology_statements'])])
  matrix=[[P(v,labelstyle)for v in('Pillar','Observed therapy','Dose / reference evidence','Documented reasons')]]
  for t in c['therapy']:
   fill=t['observed_status']+'\n'+t['fill_scope']+'\n'+'\n'.join(str(f.get('drug_name'))+'; latest '+str(f.get('latest_fill_date_in_window'))for f in t['fills'])
   doses=[]
   for d in t['dose_observations']:
    dose='not established'if d.get('documented_daily_mg')is None else str(d['documented_daily_mg'])+' mg/day'
    reference='not supplied'if d.get('target_max_daily_mg')is None else str(d['target_max_daily_mg'])+' mg/day'
    doses.append(label(d.get('drug_key'))+'; recorded '+dose+'; '+label(d.get('statement_state')or d.get('dose_status'))+'; reference comparison '+label(d.get('documented_reference_max_status')or d.get('reference_max_status'))+'; reference max '+reference+'; dose date '+str(d.get('dose_date')or'unknown')+'; latest note '+str(d.get('latest_note_date')or'unknown'))
   reasons=str(len(t['documented_reasons']))+' distinct source account(s); see quotations below'if t['documented_reasons']else c.get('empty_source_reason') or 'No explicit reason returned in reviewed scope'
   matrix.append([P(t['label'],labelstyle),P(fill,small),P('\n'.join(doses)or'No dose/state observation supplied; confirm current regimen',small),P(reasons,small)])
  table=Table(matrix,colWidths=[100,120,180,140],repeatRows=1)
  table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),paper),('LINEBELOW',(0,0),(-1,-1),.25,line),('BOTTOMPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),7)]));out.append(table)
  for t in c['therapy']:
   if t['documented_reasons']:out.extend([P(t['label']+' / documented accounts',head),*grouped_reasons(t['documented_reasons'])])
  if c.get('regimen_statements'):
   out.extend([P('Dated regimen source index',head),P('The complete quotations are available in the interactive regimen section and embedded evidence. This index retains every source record and its statement timing; it does not establish current medication use.',small)])
   for g in c['regimen_statements']:
    if g.get('review_status'):out.extend([P(g['review_status'],labelstyle),P(g['review_qualification'],small)])
    out.append(P(str(g.get('polarity'))+' / '+str(g.get('temporality'))+' / '+str(g.get('subject'))+' / '+'; '.join(str(s.get('date'))+' / '+str(s.get('record_id'))for s in g['sources']),small))
  out.extend([P('Individual tolerated maximum',head),P(c['individual_max_tolerated_dose'],labelstyle)])
  if c.get('individual_tolerance_statements'):out.extend(grouped_reasons(c['individual_tolerance_statements']))
  out.extend([P('Dated titration explanations',head),*grouped_reasons(c['dose_limiting_statements'])])
  if c.get('therapy_limitation_accounts'):
   out.extend([P('Recorded therapy limitations',head),P('These dated accounts may concern non-use or a dose change; current applicability remains unconfirmed.',small),*grouped_reasons(c['therapy_limitation_accounts'])])
  if c['dated_correspondence_roles']:
   out.extend([P('Dated correspondence / additional clinician names',head),P('These source roles do not establish current assignment. Complete correspondence quotations and bindings remain in the embedded evidence.',small)])
   roles={}
   for r in c['dated_correspondence_roles']:
    key=(r.get('name'),r.get('documented_role'),r.get('relationship'))
    roles.setdefault(key,[]).append(str(r.get('date'))+' / '+str(r.get('record_id')))
   for(name,role,relationship),sources in roles.items():
    out.extend([P(str(name)+' / '+str(role)+' / '+str(relationship),labelstyle),P('; '.join(sources),small)])
  out.append(P('Source-bound observations retain the original focused review scope and unresolved items. Complete original result fields, original analysis-proposed steps, all dose observations, source IDs and assertion metadata remain in the embedded evidence and interactive companion.',small))
  return out
 answer=context['answer'];allparts=list(parts(answer));rows=[r for a in allparts for r in a.get('table')or[]if isinstance(r,dict)]
 cards=review_cards(context,evidence)
 ids={r['patient_id']for r in rows if r.get('patient_id')};source=context.get('source')or{};cutoff=source.get('as_of','Not recorded');status=answer.get('status','Not recorded')
 patient_level=bool(ids)
 first_metric=(len(ids),'Distinct patient IDs')if patient_level else(len({k for row in rows for k in row}),'Returned fields')
 story=[P('AGERE  /  '+('PATIENT REVIEW'if patient_level else'ANALYSIS REVIEW'),small),Spacer(1,12),P('Evidence into\nnext steps',title),P(context.get('question','Saved analysis')),Spacer(1,10)]
 from .patient_evidence import source_reading_notice
 notice=source_reading_notice(context)
 if notice:story.extend([P('Source interpretation requires review',head),P(notice)])
 story.append(P('This is the generated PDF companion. Any pending-delivery statements in the original saved analysis describe the earlier analytical stage; the complete original analysis remains embedded unchanged.',small))
 kpis=Table([[P(str(first_metric[0]),head),P(str(len(rows)),head),P(status,head)], [P(first_metric[1],small),P('Complete result rows',small),P('Saved analytical status',small)]],colWidths=[180]*3)
 kpis.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),paper),('LEFTPADDING',(0,0),(-1,-1),12),('BOTTOMPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),8)]));story+=[kpis,Spacer(1,17),P('Source and scope',head),P('Synthetic warehouse. Source snapshot: '+str(cutoff)+'. Dates follow the simulated source clock. Report rendering adds no clinical interpretation, new care action or acceptance.')]
 # A typed headline specification is retained in the exact attached context;
 # it is not a resolved sentence to print as a clinical summary.
 if isinstance(answer.get('headline'),str):story.append(P(answer['headline']))
 if context.get('retained_inspection'):story.extend([P('Retained analysis for inspection',head),P('Planning code changed after this saved execution. This report preserves the original analysis and source snapshot; rendering adds no new clinical interpretation, acceptance or care authorization.')])
 for index,a in enumerate(allparts):
  facts=a.get('facts')or{}
  if isinstance(facts,dict):
   facts={k:v for k,v in facts.items() if not (isinstance(v,dict)and 'template'in v and 'bindings'in v)}
  if facts:
   story.append(P('Recorded findings'+(' / output '+str(index+1)if len(allparts)>1 else''),head))
   if isinstance(facts,dict):
    for k,v in facts.items():story.append(P(label(k),labelstyle));story.append(P(value(v)))
   else:story.append(P(value(facts)))
  for k in ['missing','limitations']:
   if a.get(k):
    story.append(P(label(k),head))
    for v in a[k] if isinstance(a[k],list)else[a[k]]:story.append(P(value(v)))
 story.extend([P('How to use the saved records',head),P(
  ('Patient records present the saved clinical fields and any patient-specific recommendations the analysis supplied. Dated findings remain dated findings; a recorded fill is not proof of ingestion, and a reference maximum is not a patient-specific tolerated dose. 'if patient_level else'Each result record presents the saved analytical fields. Group counts and recorded amounts retain the scope and limitations of the analysis. ')
  +'Source-bound quotations retain their source IDs, dates and assertion qualifications. Complete, unmodified saved analysis, executed evidence, original observations and assertion metadata are embedded as Agere-saved-answer.json.gz and available in the interactive companion.')])
 groups=[('Recommended next steps',RECOMMENDATION_FIELD_PATTERN),('Medication evidence',r'medication|gdmt|pillar|drug|rasi|hf_bb|mra|sglt|beta.block'),('Dose assessment',r'dose'),('Care team',r'pcp|cardiol|clinician|provider|visit|care_team'),('Documented reasons and evidence',r'reason|finding|note|evidence|review|documentary|relation|correction'),('Other saved fields',r'.')]
 for index,row in enumerate(rows):
  story.append(PageBreak());pid=row.get('patient_id')or'Result '+str(index+1);card=cards.get(pid);record_title=P(card['patient_name']if card else pid,title)
  record_title.agere_record={'patient_id':str(pid),'record_index':index}
  is_patient=bool(row.get('patient_id'))
  story.extend([P(('PATIENT RECORD'if is_patient else'RESULT RECORD')+'  /  '+str(index+1),small),record_title])
  if card and card['therapy']:
   story.extend(clinical_story(card));continue
  handled={'patient_id','review_json'}
  if is_patient and not any(re.search(groups[0][1],k,re.I)for k in row):story.append(P('No individualized recommendation was supplied in this saved analytical row.'))
  for group,pattern in groups if is_patient else[('Saved analytical fields',r'.')]:
   selected=[(k,v)for k,v in row.items()if k not in handled and re.search(pattern,k,re.I)]
   if not selected:continue
   story.append(P(group,head))
   cells=[]
   for k,v in selected:
    handled.add(k)
    audit=decoded(v)
    if k.startswith(('legacy_','initial_source_'))and isinstance(audit,list)and all(isinstance(item,dict)and item.get('record_id')for item in audit):
     if cells:
      t=Table(cells,colWidths=[145,395]);t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),5)]));story.append(t);cells=[]
     story.extend([P(label(k)+' / audit index',labelstyle),
        P(str(len(audit))+' original machine observations. These prior labels are not endorsed clinical reasons. Full original quotations, labels and metadata remain unchanged in the embedded evidence and interactive audit view.',small)])
     for item in audit:story.append(P(str(item.get('date')or'Unknown date')+' / '+str(item['record_id']),small))
     continue
    if isinstance(decoded(v),(dict,list))or len(value(v))>2000:
     if cells:
      t=Table(cells,colWidths=[145,395]);t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),5)]));story.append(t);cells=[]
     story.extend([P(label(k),labelstyle),*content(v)])
    else:cells.append([P(label(k),small),P(value(v),small)])
   if cells:
    t=Table(cells,colWidths=[145,395]);t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),5),('LINEBELOW',(0,0),(-1,-1),.25,line)]));story.append(t)
   story.append(Spacer(1,3))
  for table in evidence.get('supporting_tables')or[]:
   observations=[r for r in table['rows']if r.get('patient_id')==pid]
   if not observations:continue
   story.extend([P('Native supporting-evidence index / '+str(table.get('result_view')),head),P(evidence['basis'],small),
    P('Every supporting observation is indexed below. Complete original fields, calculations, source links and qualification metadata remain in the embedded evidence and interactive companion. These may include a wider medication domain than the final answer.',small)])
   for item_index,observation in enumerate(observations):
    name=observation.get('drug_name')or observation.get('drug_key')or observation.get('role')or'Source observation'
    details=[label(key)+': '+value(observation[key])for key in
       ('dose_status','reference_max_status','assignment_status','assigned_at_snapshot','dose_date','source_date','latest_note_date')if key in observation]
    story.append(P(str(item_index+1)+'. '+str(name)+(' / '+'; '.join(details)if details else''),small))
  reviews=evidence.get('notes',{}).get(pid,[])
  roles={json.dumps(r,sort_keys=True):r for review in reviews for r in review.get('source_clinicians')or[]}
  if roles:
   story.extend([P('Additional source-linked clinician names',head),P('Historical correspondence roles and explicit labels only. These do not establish current care assignment or active follow-up. An addressee is not automatically a PCP.',small)])
   for r in roles.values():
    story.append(P(str(r.get('name'))+' / '+str(r.get('documented_role'))+' / '+str(r.get('relationship')),labelstyle))
    story.append(P(str(r.get('date'))+' / '+str(r.get('record_id'))+' / '+str(r.get('current_assignment')),small))
    story.append(P('Source: '+str(r.get('quote')),small))
  if reviews:
   inventory={}
   for review in reviews:
    scope=review.get('evidence_scope')
    if review.get('source_scope')=='focused'and isinstance(scope,dict):
     story.append(P('Focused source selection: '+str(scope.get('selected_documents'))+' of '+str(scope.get('inventory_documents'))+' source documents selected. Unmatched source/field combinations were not interpreted. Selected sources may still contain unresolved fields. This does not establish chart-wide absence.',small))
    for field,findings in (review.get('findings')or{}).items():
     q=(review.get('questions')or{}).get(field)
     name=('Prior machine observations for audit / '+label(field)if field in review.get('audit_only_fields',[])else
           'Source-bound candidate relation / '+label(q['relation'])+' / '+q['entity']['id']if isinstance(q,dict)else label(field))
     inventory.setdefault(name,set()).update(json.dumps(f,sort_keys=True)for f in findings or[])
   heading='Narrative evidence within selected scope'if any(r.get('source_scope')=='focused'for r in reviews)else'Complete narrative evidence inventory'
   story.extend([P(heading,head),P('; '.join(label(k)+': '+str(len(v))+' dated source findings'for k,v in inventory.items()),small),P('The full findings, their interpretations, exact quotations, source IDs, dates, coverage and limitations remain in the embedded compressed JSON and interactive companion. The final row above retains the analysis-selected examples and proposed actions.',small)])

 if references:
  story.extend([PageBreak(),P('Reference framework',title),P('These are source references for the report, not evidence that an individual meets a treatment indication or can tolerate a particular dose.')])
  for ref in references:
   story.append(P(ref['title']));story.append(P(ref['url'],small))
 buf=io.BytesIO()
 def page(c,doc):
  c.saveState();c.setFillColor(muted);c.setFont('AgereSans',7);c.drawString(36,20,'AGERE  |  SYNTHETIC DATA  |  SNAPSHOT '+str(cutoff));c.drawRightString(576,20,str(doc.page));c.setStrokeColor(line);c.line(36,31,576,31);c.restoreState()
 doc=SimpleDocTemplate(buf,pagesize=(612,792),leftMargin=36,rightMargin=36,topMargin=36,bottomMargin=43,title='Agere saved analysis report',author='Agere')
 record_pages=[]
 def after(flowable):
  if hasattr(flowable,'agere_record'):record_pages.append({**flowable.agere_record,'page':doc.page})
 doc.afterFlowable=after
 from reportlab.pdfgen.canvas import Canvas
 def deterministic_canvas(*args,**kwargs):
  kwargs['invariant']=1
  return Canvas(*args,**kwargs)
 doc.build(story,onFirstPage=page,onLaterPages=page,canvasmaker=deterministic_canvas)
 reader=PdfReader(io.BytesIO(buf.getvalue()));writer=PdfWriter();writer.append(reader)
 writer.add_outline_item('Source, scope and findings',0)
 if record_pages:
  parent=writer.add_outline_item('Saved records',record_pages[0]['page']-1)
  for entry in record_pages:
   c=cards.get(entry['patient_id']);writer.add_outline_item((c['patient_name']+' / 'if c and c['profile'].get('display_name')else'')+entry['patient_id'],entry['page']-1,parent=parent)
 raw=json.dumps(context,ensure_ascii=False,indent=2).encode();packed=gzip.compress(raw,mtime=0);writer.add_attachment('Agere-saved-answer.json.gz',packed)
 with output.open('wb')as f:writer.write(f)
 return {'output':str(output),'pages':len(reader.pages),'rows':len(rows),'distinct_patient_ids':len(ids),'new_model_calls':0,'analytical_status':status,'source_as_of':cutoff,'attachment_bytes':len(packed),'uncompressed_context_bytes':len(raw),
         'attachment_name':'Agere-saved-answer.json.gz','attachment_encoding':'gzip',
         'uncompressed_context_sha256':hashlib.sha256(raw).hexdigest(),
         'patient_bookmarks':record_pages,
         'narrative_display':'Named clinical reviews with dated care, therapy, dose and grouped source quotations; generic fields for other analyses. Complete unmodified context, all original rows and assertion metadata embedded.'}

if __name__=='__main__':
 context=json.loads(Path(sys.argv[1]).read_text());print(json.dumps(render(context,sys.argv[2],json.loads(Path(sys.argv[3]).read_text())if len(sys.argv)>3 else None)))
