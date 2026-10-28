"""Deterministic PDF companion to any saved patient-level analysis; no model calls."""
from pathlib import Path
import json,re,html,sys,io
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak,KeepTogether
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import HexColor,white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader,PdfWriter

def render(context,output,references=None,evidence=None):
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
 def parts(a):
  yield a
  for s in a.get('sections')or[]:
   if isinstance(s,dict)and isinstance(s.get('answer'),dict):yield from parts(s['answer'])
 answer=context['answer'];allparts=list(parts(answer));rows=[r for a in allparts for r in a.get('table')or[]if isinstance(r,dict)]
 ids={r['patient_id']for r in rows if r.get('patient_id')};source=context.get('source')or{};cutoff=source.get('as_of','Not recorded');status=answer.get('status','Not recorded')
 story=[P('AGERE  /  PATIENT REVIEW',small),Spacer(1,12),P('Evidence into\nnext steps',title),P(context.get('question','Saved patient analysis')),Spacer(1,10)]
 kpis=Table([[P(str(len(ids)),head),P(str(len(rows)),head),P(status,head)], [P('Distinct patient IDs',small),P('Complete result rows',small),P('Saved analytical status',small)]],colWidths=[180]*3)
 kpis.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),paper),('LEFTPADDING',(0,0),(-1,-1),12),('BOTTOMPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),8)]));story+=[kpis,Spacer(1,17),P('Source and scope',head),P('Synthetic warehouse. Source snapshot: '+str(cutoff)+'. Clinical dates follow the simulated source clock. Report rendering adds no clinical interpretation, new care action or acceptance.'),P(answer.get('headline','No headline supplied'))]
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
 story.extend([P('How to use the patient records',head),P('Each following record presents the saved fields and any patient-specific recommendations the analysis supplied. A missing recommendation is stated explicitly. Dated findings remain dated findings; a recorded fill is not proof of ingestion, and a reference maximum is not a patient-specific tolerated dose. Full saved answer and row data are embedded in this PDF as Agere-saved-answer.json.')])
 groups=[('Recommended next steps',r'recommend|next_step|action_plan|proposed_action'),('Medication evidence',r'medication|gdmt|pillar|drug|rasi|hf_bb|mra|sglt|beta.block'),('Dose assessment',r'dose'),('Care team',r'pcp|cardiol|clinician|provider|visit'),('Documented reasons and evidence',r'reason|finding|note|evidence|review'),('Other saved fields',r'.')]
 for index,row in enumerate(rows):
  story.append(PageBreak());pid=row.get('patient_id')or'Result '+str(index+1);story.extend([P('PATIENT RECORD  /  '+str(index+1),small),P(pid,title)])
  handled={'patient_id','review_json'}
  if not any(re.search(groups[0][1],k,re.I)for k in row):story.append(P('No individualized recommendation was supplied in this saved analytical row. This is an unresolved output requirement, not a recommendation to change medication.'))
  for group,pattern in groups:
   selected=[(k,v)for k,v in row.items()if k not in handled and re.search(pattern,k,re.I)]
   if not selected:continue
   story.append(P(group,head))
   cells=[]
   for k,v in selected:
    handled.add(k)
    if len(value(v))>2000:
     if cells:
      t=Table(cells,colWidths=[145,395]);t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),5)]));story.append(t);cells=[]
     story.extend([P(label(k),labelstyle),P(value(v))])
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
    story.append(P('; '.join(label(k)+': '+value(v)for k,v in observation.items()if k!='patient_id'),small))
  for review in evidence.get('notes',{}).get(pid,[]):
   story.extend([P('Whole-document note findings',head),P(str(review['review_status'])+' / '+str(review['documents_reviewed'])+' received documents. A documented mention does not automatically establish a causal treatment reason.',small)])
   for field,question in review['questions'].items():
    story.extend([P(label(field),labelstyle),P(question,small)])
    found=(review.get('findings')or{}).get(field)or[]
    if not found:story.append(P('No documented finding returned. See coverage and limitations.',small))
    for finding in found:
     story.extend([P(finding['value']),P(str(finding.get('date'))+' / '+str(finding.get('record_id')),small),P('Source: '+str(finding.get('quote')),small)])
   if review.get('limitations'):story.append(P('Review limitations: '+value(review['limitations']),small))
 if references:
  story.extend([PageBreak(),P('Reference framework',title),P('These are source references for the report, not evidence that an individual meets a treatment indication or can tolerate a particular dose.')])
  for ref in references:
   story.append(P(ref['title']));story.append(P(ref['url'],small))
 buf=io.BytesIO()
 def page(c,doc):
  c.saveState();c.setFillColor(muted);c.setFont('AgereSans',7);c.drawString(36,20,'AGERE  |  SYNTHETIC DATA  |  SNAPSHOT '+str(cutoff));c.drawRightString(576,20,str(doc.page));c.setStrokeColor(line);c.line(36,31,576,31);c.restoreState()
 doc=SimpleDocTemplate(buf,pagesize=(612,792),leftMargin=36,rightMargin=36,topMargin=36,bottomMargin=43,title='Agere patient optimization report',author='Agere')
 from reportlab.pdfgen.canvas import Canvas
 def deterministic_canvas(*args,**kwargs):
  kwargs['invariant']=1
  return Canvas(*args,**kwargs)
 doc.build(story,onFirstPage=page,onLaterPages=page,canvasmaker=deterministic_canvas)
 reader=PdfReader(io.BytesIO(buf.getvalue()));writer=PdfWriter();writer.append(reader)
 raw=json.dumps(context,ensure_ascii=False,indent=2).encode();writer.add_attachment('Agere-saved-answer.json',raw)
 with output.open('wb')as f:writer.write(f)
 return {'output':str(output),'pages':len(reader.pages),'rows':len(rows),'distinct_patient_ids':len(ids),'new_model_calls':0,'analytical_status':status,'source_as_of':cutoff,'attachment_bytes':len(raw)}

if __name__=='__main__':
 context=json.loads(Path(sys.argv[1]).read_text());print(json.dumps(render(context,sys.argv[2],json.loads(Path(sys.argv[3]).read_text())if len(sys.argv)>3 else None)))
