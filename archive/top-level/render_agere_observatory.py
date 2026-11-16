"""Render a vector PDF presentation from the observed evidence packet."""
import json,math
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'output/pdf/agere-evidence-and-stack.pdf'
D=json.loads((ROOT/'artifacts/agere-observatory/evidence.json').read_text())
W,H=1200,780
P='#F4F2E9';I='#172B2B';M='#637371';G='#247767';L='#D5DDD4';DARK='#112C2C';MINT='#B9F2D1';AMBER='#DBAD67'
c=canvas.Canvas(str(OUT),pagesize=(W,H));c.setTitle('Agere | The stack, the evidence, the next experiment');c.setAuthor('Agere synthetic research')
def box(x,y,w,h,fill,stroke=None,r=0):
 c.setFillColor(HexColor(fill));c.setStrokeColor(HexColor(stroke or fill));c.roundRect(x,H-y-h,w,h,r,fill=1,stroke=bool(stroke))
def text(x,y,s,size=12,color=I,font='Helvetica'):
 c.setFillColor(HexColor(color));c.setFont(font,size);c.drawString(x,H-y-size,s)
def para(x,y,s,width=450,size=12,color=M,leading=None):
 style=ParagraphStyle('p',fontName='Helvetica',fontSize=size,leading=leading or size*1.45,textColor=HexColor(color))
 p=Paragraph(escape(s),style);a,b=p.wrap(width,700);p.drawOn(c,x,H-y-b);return b
def line(x,y,x2,y2,color=L,width=1):
 c.setStrokeColor(HexColor(color));c.setLineWidth(width);c.line(x,H-y,x2,H-y2)
def title(n,kicker,heading,sub=None,dark=False):
 box(0,0,W,H,DARK if dark else P)
 text(48,24,'agere.',30,MINT if dark else I,'Times-Roman')
 text(888,38,'SYNTHETIC RESEARCH  /  OCTOBER 2026',10,MINT if dark else G)
 line(48,76,1152,76,'#436052' if dark else L)
 text(48,96,kicker.upper(),10,MINT if dark else G)
 text(48,118,heading,43,'#EAF0E7' if dark else I,'Times-Roman')
 if sub:para(50,177,sub,1050,12,'#B9CEC4' if dark else M)
 text(48,746,'AGERE  /  LOCAL RESEARCH BUILD  /  ALL PATIENTS SYNTHETIC',9,'#ADC0B2' if dark else M)
 text(1110,744,f'{n:02d} / 05',10,MINT if dark else G)
def foot(s,dark=False):para(48,707,s,1080,9,'#ADC0B2' if dark else M,12)
def end():c.showPage()
# 1

title(1,'01 / Architecture','Fragmented records. Connected understanding.',
      'A local research stack that combines source integrity, selective models and explicit mathematics to produce reviewable signals.')
rows=[
('01','SOURCE INTEGRITY','Observed streams, source hashes, identity and arrival times.','SQLite / explicit crosswalk'),
('02','DOCUMENT + TIME','Recover PDF text; apply corrections at the right cutoff.','PDFKit / Apple Vision OCR'),
('03','EVIDENCE + RETRIEVAL','Connect sources; retrieve and rank supporting passages.','Local graph / BM25 / Granite / BGE'),
('04','SPECIALIST MODELS','Predict bounded source-workflow actions; expose uncertainty.','Lexical / BioClinical head / controls'),
('05','OBSERVATION LOGIC','Preserve unknowns; reconcile fills, holds and trajectories.','Three-valued rules / joint hypotheses'),
('06','POPULATION MATHEMATICS','Compare patterns and evaluate effective-dated rules.','Trends / process paths / research operators'),
('07','SELECTIVE ESCALATION','Send bounded evidence packets; reuse validated results.','Jev / Claude / persistent gateway')]
for j,(idx,head,desc,models) in enumerate(rows):
 y=231+j*54
 box(48,y,700,48,'#E6ECDC' if j%2==0 else '#ECEFE4')
 text(62,y+13,idx,17,G,'Times-Roman');text(98,y+6,head,10,G,'Helvetica-Bold');text(98,y+23,desc,12,I)
 if j<6:line(79,y+49,79,y+54,G)
 text(774,y+8,models,12,I)
 statuses=['Warehouse run','14 applied extracts','12 selected neural cases','Trained and held-out audited','Warehouse + shifted legacy model','Warehouse and separate scenarios','One live synthetic packet']
 text(774,y+27,statuses[j],10,M)
for j,(n,label) in enumerate([(10000,'synthetic patients'),(313586,'visible records'),(5,'review signal types'),(14,'applied PDF extracts')]):
 x=48+j*280;text(x,628,f'{n:,}',33,I,'Times-Roman');text(x,668,label,11,M)
foot('Source: warehouse-run-v4/run.json. Perplexity adapter is not live; Qwen is an earlier experiment. MedGemma and Chandra are candidates, not operating layers. Employer TextQL/ontology is not present. No execution agents.')
end()
# 2
title(2,'02 / Performance','The simplest model earned the first move.',
      'Held-out agreement with synthetic generator action labels. Source families are disjoint; simulator mechanisms and templates are shared.',True)
text(48,237,f'{D["audit"]["models"]["lexical"]["accuracy"]*100:.1f}%',98,MINT,'Times-Roman')
para(52,347,'Full-record lexical head. This is not an estimate of clinical correctness.',310,15,'#C4D7C7')
text(52,432,'1,360 examples / 220 held-out families',12,MINT)
para(52,463,'Inputs and training recipes differ. BioClinical reads the latest note and document; the lexical model uses the full allowed observation. Base encoder weights were not fine-tuned.',300,12,'#BACFBE')
for j,(k,name) in enumerate([('lexical','Full-record lexical head'),('bioclinical','BioClinical note/document head'),('numeric_linear','Numeric linear control'),('tensor_train','Tensor train / 219 parameters')]):
 y=245+j*77;a=D['audit']['models'][k];text(435,y,name,13,'#EAF0E7');text(1015,y,f'{a["accuracy"]*100:.1f}% / {a["balanced_accuracy"]*100:.1f}%',12,MINT)
 box(435,y+26,670,10,'#2D463E');box(435,y+26,670*a['accuracy'],10,MINT)
 box(435,y+42,670,5,'#2D463E');box(435,y+42,670*a['balanced_accuracy'],5,AMBER)
text(435,563,'MINT: OVERALL ACCURACY     /     AMBER: BALANCED ACCURACY',10,'#BDCEBD')
box(48,608,1104,72,'#203B34');para(64,621,'Tensor-train overall agreement masks weak minority-class performance: 83.6% overall, 33.5% balanced. It was not promoted. A more complex model must earn its place through a fair task-level comparison.',1070,13,'#DDE8D7')
foot('Sources: heldout-audit-v1.json and action-specialists-v1/manifest.json. Labels are generator proposals, not clinician-adjudicated truth. No superiority over an LLM-only system has yet been demonstrated.',True)
end()
# 3
title(3,'03 / Mathematical layers','Borrow good ideas. Test every assumption.',
      'Established mathematics, assembled into a healthcare system. Warehouse signals, separate scenarios and unproven models are kept distinct.')
methods=[
('Robust statistics','Theil-Sen renal trends','WAREHOUSE','Median pairwise slopes retain dated measurements. 176 declining patterns.'),
('Logic / information theory','Decision-changing unknowns','WAREHOUSE + SCENARIO','Conditional completions identify decisive missing facts. EVSI needs supplied utilities and probabilities.'),
('Sensor fusion','Care / channel / documentation','DOMAIN SHIFT','Joint observation hypotheses separate possible care from captured evidence; current calibration unproven.'),
('Astronomy','Gaussian change points','SCENARIO','Piecewise signal changes require credible measurement-error estimates, absent from current lab feeds.'),
('Manufacturing','CUSUM / EWMA','SCENARIO','Flag source-volume shifts before interpreting them as patient deterioration.'),
('Actuarial science','Claims development','WAREHOUSE','Service month versus payment lag. No ultimate-cost estimate without maturity and enrollment.'),
('Process mining','Observed event paths','WAREHOUSE','Event adjacency and elapsed time. Not proof of referral completion or causality.'),
('Causal inference','Cross-fitted AIPW','SCENARIO','Toy generating effect: 2. Estimate: 1.995. Needs confounders, overlap and observed outcomes.'),
('Bayesian estimation','Shrinkage / facility O:E','SCENARIO','Small-denominator stabilization and adjusted comparisons. Not a causal best-provider ranking.'),
('Statistical discovery','False-discovery control','SCENARIO','Benjamini-Hochberg across valid tests; cannot repair biased cohorts or invalid p-values.'),
('Operations research','Constrained allocation','SCENARIO','MILP maximizes supplied benefit under capacity and cost. Benefits remain assumptions.'),
('Tensor methods','Low-rank interactions','NOT PROMOTED','Classical 219-parameter model. Weak balanced accuracy; no quantum hardware.'),
('Decision economics','Selective model routing','WAREHOUSE','Local proposals first; uncertainty escalates. Empirical routing is not an error guarantee.'),
('Build systems','Dependency caching','WAREHOUSE','Content, rules and model versions govern reuse. Replay still has orchestration cost.'),
('Policy engineering','Versioned programs','LOCAL EXAMPLES','Source-bound predicates update separately from model weights. Two configured screens run now.')]
for j,(origin,name,status,desc) in enumerate(methods):
 col=j%3;row=j//3;x=48+col*374;y=225+row*93
 box(x,y,356,85,'#FAF9F2',L)
 text(x+12,y+8,origin.upper(),8,G)
 text(x+12,y+22,name,17,I,'Times-Roman')
 text(x+12,y+43,status,7,G if status=='WAREHOUSE' else '#896129')
 para(x+12,y+55,desc,332,8.7,M,10.5)
foot('Sources: mathematical-experiments.json, warehouse-run-v4/population.json, patients.jsonl, run.json, research-catalog.json. These methods are not claimed as new mathematical inventions or validated clinical improvements.')
end()
# 4
title(4,'04 / Synthetic patient evidence','A threshold is a snapshot. A trajectory is a story.',
      '134 of 176 declining eGFR trajectories have a latest value at least 30. A single below-30 screen does not surface the same group.')
p=D['examples']['declining_egfr'];series=p['clinical']['egfr'];t=p['trends']['egfr']
box(48,237,653,405,'#FFFDF7',L);text(68,251,'SYNTHETIC PATIENT  /  '+p['patient_id'],10,G)
text(69,280,'eGFR / mL/min/1.73 m2',11,M)
from datetime import date
xs=[date.fromisoformat(v['date']).toordinal() for v in series];ys=[v['value'] for v in series];lo=min(30,*ys)-5;hi=max(ys)+5
xx=lambda n:90+(n-min(xs))/max(1,max(xs)-min(xs))*550
yy=lambda n:536-(n-lo)/(hi-lo)*210
line(90,yy(30),650,yy(30),'#C1A36D');text(507,yy(30)+5,'single threshold: 30',10,'#9B7D42')
for j,a in enumerate(series):
 if j:line(xx(xs[j-1]),yy(ys[j-1]),xx(xs[j]),yy(ys[j]),G,2)
 c.setFillColor(HexColor(G));c.circle(xx(xs[j]),H-yy(ys[j]),4,fill=1,stroke=0)
 text(xx(xs[j])-10,yy(ys[j])-24,str(ys[j]),12,G)
text(90,575,series[0]['date'],11,M);text(576,575,series[-1]['date'],11,M)
text(735,246,f'{t["slope_per_year"]:.2f}',55,G,'Times-Roman');text(738,306,'eGFR units per year / Theil-Sen slope',12,M)
para(738,347,'Interval: '+ ' to '.join(f'{v:.2f}' for v in t['slope_interval_per_year'])+'. Descriptive pattern; not a validated forecast or diagnosis.',390,14,I)
para(738,426,'Next useful action: reconcile measurement context and current specialist care. Missing documentation does not establish that care is absent.',390,14,I)
para(738,511,'Each plotted value retains its source record, event date and arrival date. No frontier call was needed to calculate this trajectory.',390,13,M)
text(48,661,'REVIEW SIGNALS',9,G)
labels=[('kidney_specialist_review','kidney review'),('reduced_ef_documentation','low-EF text'),('declining_egfr','eGFR decline'),('financial_assistance_screen','financial screen'),('goals_of_care_context','goals mentions')]
for j,(k,l) in enumerate(labels):text(48+j*222,679,f'{D["run_summary"]["candidate_counts"][k]:,}  {l}',12,I)
foot('Sources: warehouse-run-v4/patients.jsonl and insights.json; new cross-signal calculation in artifacts/agere-observatory/evidence.json. Groups overlap. Review candidates are not confirmed care gaps.')
end()
# 5
title(5,'05 / Economics + the next experiments','The next layer should fix a demonstrated failure.',
      'The stack already ran on the synthetic warehouse. The next step is disciplined stress testing and improvement, not another architectural promise.')
for j,(n,l,desc) in enumerate([(f'{D["compute"]["elapsed_seconds"]:.1f}s','LOCAL BATCH','Encoder caches already warm. Not a cold-start benchmark.'),(f'{D["verification"]["replay_compute"]["elapsed_seconds"]:.1f}s','CACHED REPLAY','10,000 patient cache hits; only modest wall-time improvement.'),('0','NEW PAID BATCH CALLS','Total machine, storage and review costs remain unmeasured.')]):
 x=48+j*374;box(x,232,356,133,'#E5EBDC');text(x+17,242,n,43,G,'Times-Roman');text(x+17,296,l,9,G);para(x+17,314,desc,324,11,M)
text(48,393,'WHAT THE NEW PROBES EXPOSED',10,G)
para(48,420,'Open failure: native EF candidates still appear for a family member or an explicitly corrected historical value. Assertion, experiencer and temporal adjudication are the next extraction fixes.',515,15,I)
para(48,516,'Fixed interface problem: complex GDMT, eligibility and causal requests now disclose partial answers and missing inputs instead of claiming that a related screening card answers the whole question.',515,13,M)
text(626,393,'WHAT TO RUN NEXT',10,G)
nexts=[('Repair context','Test unseen negation, relatives, corrections and current-versus-historical evidence.'),('Perturb the warehouse','Remove feeds, alter arrival times and replay corrections while checking provenance.'),('Add outcome cohorts','Bring treatment, severity, follow-up, attribution and contract data to the math operators.'),('Measure total economics','Compare a frozen task set against LLM-only inference, including errors and human review.')]
for j,(head,desc) in enumerate(nexts):
 y=420+j*59;text(626,y,f'0{j+1}',18,G,'Times-Roman');text(662,y,head,13,I,'Helvetica-Bold');para(662,y+21,desc,468,11,M)
foot('Sources: verification.json and artifacts/agere-observatory/evidence.json. New query suite: '+str(len(D['queries']))+' requests. New adversarial probes: '+str(len(D['stress_probes']))+', including '+str(sum(not p['passed'] for p in D['stress_probes']))+' open failures. These selected checks are not population-level accuracy estimates.')
end();c.save();print(OUT)
