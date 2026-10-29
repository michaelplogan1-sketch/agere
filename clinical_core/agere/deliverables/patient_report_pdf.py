"""Deterministic PDF companion to any saved patient-level analysis; no model calls."""
from pathlib import Path
import json,re,html,sys,io,gzip,hashlib
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak,KeepTogether
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import HexColor,white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader,PdfWriter

def render(context,output,references=None,evidence=None):
 from .patient_evidence import RECOMMENDATION_FIELD_PATTERN
 output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
 if evidence is None:
  from .patient_evidence import projection
  evidence=projection(context)
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
 answer=context['answer'];allparts=list(parts(answer));rows=[r for a in allparts for r in a.get('table')or[]if isinstance(r,dict)]
 ids={r['patient_id']for r in rows if r.get('patient_id')};source=context.get('source')or{};cutoff=source.get('as_of','Not recorded');status=answer.get('status','Not recorded')
 story=[P('AGERE  /  PATIENT REVIEW',small),Spacer(1,12),P('Evidence into\nnext steps',title),P(context.get('question','Saved patient analysis')),Spacer(1,10)]
 kpis=Table([[P(str(len(ids)),head),P(str(len(rows)),head),P(status,head)], [P('Distinct patient IDs',small),P('Complete result rows',small),P('Saved analytical status',small)]],colWidths=[180]*3)
 kpis.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),paper),('LEFTPADDING',(0,0),(-1,-1),12),('BOTTOMPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),8)]));story+=[kpis,Spacer(1,17),P('Source and scope',head),P('Synthetic warehouse. Source snapshot: '+str(cutoff)+'. Clinical dates follow the simulated source clock. Report rendering adds no clinical interpretation, new care action or acceptance.'),P(answer.get('headline','No headline supplied'))]
 if context.get('retained_inspection'):story.extend([P('Retained analysis for inspection',head),P('Planning code changed after this saved execution. This report preserves the original analysis and source snapshot; rendering adds no new clinical interpretation, acceptance or care authorization.')])
 for index,a in enumerate(allparts):
  facts=a.get('facts')or{}
  if facts:
   story.append(P('Recorded findings'+(' / output '+str(index+1)if len(allparts)>1 else''),head))
   if isinstance(facts,dict):
    for k,v in facts.items():story.append(P(label(k),labelstyle));story.append(P(value(v)))
   else:story.append(P(value(facts)))
  for k in ['missing','limitations']:
   if a.get(k):
    story.append(P(label(k),head))
    for v in a[k] if isinstance(a[k],list)else[a[k]]:story.append(P(value(v)))
 story.extend([P('How to use the patient records',head),P('Each following record presents the saved clinical fields and any patient-specific recommendations the analysis supplied. A missing recommendation is stated explicitly. Dated findings remain dated findings; a recorded fill is not proof of ingestion, and a reference maximum is not a patient-specific tolerated dose. Source-bound quotations retain their source IDs, dates and assertion qualifications. Prior machine observations are indexed for audit. Complete, unmodified saved analysis, executed evidence, original observations and assertion metadata are embedded as Agere-saved-answer.json.gz and available in the interactive companion.')])
 groups=[('Recommended next steps',RECOMMENDATION_FIELD_PATTERN),('Medication evidence',r'medication|gdmt|pillar|drug|rasi|hf_bb|mra|sglt|beta.block'),('Dose assessment',r'dose'),('Care team',r'pcp|cardiol|clinician|provider|visit|care_team'),('Documented reasons and evidence',r'reason|finding|note|evidence|review|documentary|relation|correction'),('Other saved fields',r'.')]
 for index,row in enumerate(rows):
  story.append(PageBreak());pid=row.get('patient_id')or'Result '+str(index+1);record_title=P(pid,title)
  record_title.agere_record={'patient_id':str(pid),'record_index':index}
  story.extend([P('PATIENT RECORD  /  '+str(index+1),small),record_title])
  handled={'patient_id','review_json'}
  if not any(re.search(groups[0][1],k,re.I)for k in row):story.append(P('No individualized recommendation was supplied in this saved analytical row. This is an unresolved output requirement, not a recommendation to change medication.'))
  for group,pattern in groups:
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
   story.extend([P('Supporting observations / '+str(table.get('result_view')),head),P(evidence['basis'],small)])
   for observation in observations:
    story.append(P(observation.get('drug_name')or observation.get('drug_key')or observation.get('role')or'Source observation',labelstyle))
    story.extend(content({k:v for k,v in observation.items()if k!='patient_id'}))
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
    for field,findings in (review.get('findings')or{}).items():
     q=(review.get('questions')or{}).get(field)
     name=('Prior machine observations for audit / '+label(field)if field in review.get('audit_only_fields',[])else
           'Source-bound candidate relation / '+label(q['relation'])+' / '+q['entity']['id']if isinstance(q,dict)else label(field))
     inventory.setdefault(name,set()).update(json.dumps(f,sort_keys=True)for f in findings or[])
   story.extend([P('Complete narrative evidence inventory',head),P('; '.join(label(k)+': '+str(len(v))+' dated source findings'for k,v in inventory.items()),small),P('The full findings, their interpretations, exact quotations, source IDs, dates, coverage and limitations remain in the embedded compressed JSON and interactive companion. The final row above retains the analysis-selected examples and proposed actions.',small)])

 if references:
  story.extend([PageBreak(),P('Reference framework',title),P('These are source references for the report, not evidence that an individual meets a treatment indication or can tolerate a particular dose.')])
  for ref in references:
   story.append(P(ref['title']));story.append(P(ref['url'],small))
 buf=io.BytesIO()
 def page(c,doc):
  c.saveState();c.setFillColor(muted);c.setFont('AgereSans',7);c.drawString(36,20,'AGERE  |  SYNTHETIC DATA  |  SNAPSHOT '+str(cutoff));c.drawRightString(576,20,str(doc.page));c.setStrokeColor(line);c.line(36,31,576,31);c.restoreState()
 doc=SimpleDocTemplate(buf,pagesize=(612,792),leftMargin=36,rightMargin=36,topMargin=36,bottomMargin=43,title='Agere patient optimization report',author='Agere')
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
  parent=writer.add_outline_item('Patient records',record_pages[0]['page']-1)
  for entry in record_pages:writer.add_outline_item(entry['patient_id'],entry['page']-1,parent=parent)
 raw=json.dumps(context,ensure_ascii=False,indent=2).encode();packed=gzip.compress(raw,mtime=0);writer.add_attachment('Agere-saved-answer.json.gz',packed)
 with output.open('wb')as f:writer.write(f)
 return {'output':str(output),'pages':len(reader.pages),'rows':len(rows),'distinct_patient_ids':len(ids),'new_model_calls':0,'analytical_status':status,'source_as_of':cutoff,'attachment_bytes':len(packed),'uncompressed_context_bytes':len(raw),
         'attachment_name':'Agere-saved-answer.json.gz','attachment_encoding':'gzip',
         'uncompressed_context_sha256':hashlib.sha256(raw).hexdigest(),
         'patient_bookmarks':record_pages,
         'narrative_display':'Every patient; clinical fields and complete source-bound quotations with dates/qualifications; prior observations indexed for audit. Complete unmodified context and assertion metadata embedded.'}

if __name__=='__main__':
 context=json.loads(Path(sys.argv[1]).read_text());print(json.dumps(render(context,sys.argv[2],json.loads(Path(sys.argv[3]).read_text())if len(sys.argv)>3 else None)))
