"""Reviewable offline report built exclusively from completed synthetic artifacts."""
from pathlib import Path
import json,html
from clinical_core.agere.clinical.query import query
from clinical_core.agere.clinical.workflows import NAMES
from clinical_core.agere.clinical.model_reader import score
from clinical_core.reviewed_training import inspect_export
ROOT=Path(__file__).resolve().parent
RUN=ROOT/'artifacts/agere-clinical-workflows-v2';PILOT=ROOT/'artifacts/agere-clinical-reader-v1'
report=json.loads((RUN/'run.json').read_text());examples=json.loads((RUN/'examples.json').read_text())
def compact(value):
 if isinstance(value,dict):return {k:({'total':len(v),'preview':v[:8],'full_list':'Download the corresponding workflow JSON below'} if k=='patient_ids' and len(v)>8 else compact(v)) for k,v in value.items()}
 if isinstance(value,list):return [compact(x) for x in value]
 return value
checks=[query(RUN,q) for q in ['Review heart failure treatment evidence','Review kidney trajectory and referrals','Review affordability and assistance evidence',
 'Find all HFrEF patients not on GDMT or maximum tolerated dose','Which CKD patients need nephrology referrals?','Which patients need financial assistance?',
 'Why are patients being readmitted for sepsis?']]
(RUN/'executed-questions.json').write_text(json.dumps(checks,indent=2))
training,_=inspect_export(ROOT/'artifacts/agere-stack/reviewed-export',ROOT/'artifacts/agere-stack/development-reviews.sqlite3')
(RUN/'training-readiness.json').write_text(json.dumps(training,indent=2))
# Score existing provider responses only: no new calls or retries.
items=json.loads((PILOT/'live-cases.json').read_text());scores={}
for provider in ('jev','claude','perplexity'):
 path=PILOT/('claude-revised-result.json' if provider=='claude' and (PILOT/'claude-revised-result.json').exists() else provider+'-result.json')
 r=json.loads(path.read_text());scores[provider]=score(items,r)
if (PILOT/'local-qwen/result.json').exists():scores['qwen3-4b-local']=score(items,json.loads((PILOT/'local-qwen/result.json').read_text()))
reference_review={'status':'needs_reference_adjudication','unchanged_reference_scores':scores,
 'issues':[{'case_id':k,'issue':'The question asks whether the evidence establishes a proposition. No may be a defensible answer despite the authored unknown reference. Do not treat disagreement as a validated clinical error.'} for k in ('order_only','renal_units','injection')],
 'training_use':'Exclude these challenges from supervised labels until their task/reference semantics are adjudicated; never tune references to favor a provider.'}
(PILOT/'reference-review.json').write_text(json.dumps(reference_review,indent=2))
sections=''
for k,name in NAMES.items():
 s=report['workflows'][k];count=s['review_candidates']
 rows=''.join('<tr><td>'+html.escape(status.replace('_',' '))+'</td><td>'+str(n)+'</td></tr>' for status,n in s['status_counts'].items())
 sample=''
 for status,e in examples[k].items():
  w=e['workflow'];ids=set(w['support_fact_ids']);facts=[f for f in e['facts'] if f['fact_id'] in ids]
  evidence=''
  for f in facts[:5]:
   for ev in f['evidence'][:1]:
    quote=ev.get('context',ev.get('quote',json.dumps(ev.get('value'))))
    evidence+='<blockquote>'+html.escape(quote)+'<cite>'+html.escape(ev['record_id']+' · '+ev['field']+' · '+ev['event_date'])+'</cite></blockquote>'
  missing=''.join('<li>'+html.escape(x)+'</li>' for x in w['missing_information'])
  actions=''.join('<li>'+html.escape(x)+'</li>' for x in w['proposed_actions']) or '<li>No supported action proposal from this workflow.</li>'
  sample+='<details><summary>'+html.escape(e['patient_id']+' / '+status.replace('_',' '))+'</summary>'+evidence+'<h4>Proposed next steps</h4><ul>'+actions+'</ul><h4>Still unknown</h4><ul>'+missing+'</ul><p>'+html.escape(w['question_boundary'])+'</p></details>'
 sections+='<section id="'+k+'"><span class="eyebrow">'+html.escape(k.replace('_',' '))+'</span><h2>'+html.escape(name)+'</h2><div class="count">'+f'{count:,}'+'<small> synthetic review candidates</small></div><table>'+rows+'</table>'+sample+'</section>'
page='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Agere · Clinical evidence in practice</title><style>
*{box-sizing:border-box}body{margin:0;background:#f2f0e7;color:#183a32;font:16px/1.6 system-ui}main{max-width:1150px;margin:auto;padding:56px 28px}h1{font:500 clamp(42px,7vw,80px)/1.02 Georgia;letter-spacing:-.045em;max-width:900px;margin:24px 0}h2{font:500 34px/1.15 Georgia}p{max-width:850px}.eyebrow{text-transform:uppercase;font-size:12px;letter-spacing:.18em;color:#6b7168}.intro{font-size:21px}.metrics{display:flex;flex-wrap:wrap;gap:30px;padding:24px 0;border-block:1px solid #b8c3b8}.metrics b{font-size:30px;display:block}.metrics span{font-size:13px}nav{display:flex;flex-wrap:wrap;gap:12px;margin:25px 0}a,button{color:#184a3b}nav a,button{padding:10px 15px;border:1px solid #7d998b;border-radius:3px;background:transparent;text-decoration:none;cursor:pointer}section{padding:40px 0;border-top:1px solid #b8c3b8}.count{font-size:40px}small{font-size:14px}table{width:100%;border-collapse:collapse;max-width:780px;margin:20px 0}td{padding:8px;border-bottom:1px solid #d2d7cc}td:last-child{text-align:right}details{border-top:1px solid #c8d0c4;padding:14px 0}summary{cursor:pointer}blockquote{background:#fffdf4;margin:16px 0;padding:20px;border-left:3px solid #b79455;white-space:pre-wrap}cite{font:12px/1.6 system-ui;display:block;margin-top:12px;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#e5e8dd;padding:20px;max-height:550px;overflow:auto}input{font:inherit;padding:12px;width:min(780px,100%);border:1px solid #7d998b;background:#fffdf4}h4{margin-bottom:5px}.note{border-left:3px solid #aa7351;padding-left:18px}footer{padding:30px 0;font-size:13px}a:focus-visible,summary:focus-visible,button:focus-visible{outline:3px solid #a7623d;outline-offset:3px}</style><main><div class="eyebrow">Agere · executed evidence workflows · synthetic research</div><h1>What the record says.<br>What it cannot yet tell us.</h1><p class="intro">A patient fact ledger connects clinical text, source records and chronology to three inspectable review workflows. Every next step carries evidence and its unresolved questions.</p>'''
page+='<div class="metrics"><div><b>'+str(report['patients'])+'</b><span>synthetic patients</span></div><div><b>'+f"{report['snapshot']['visible_records']:,}"+'</b><span>visible source records</span></div><div><b>3</b><span>executed review workflows</span></div></div><nav>'+''.join('<a href="#'+k+'">'+html.escape(k.replace('_',' '))+'</a>' for k in NAMES)+'</nav>'
page+='<p class="note">The warehouse run uses structured adapters and bounded contextual rules. The model pilot below is separate. These findings are review candidates, not confirmed care gaps or treatment instructions.</p>'+sections
page+='<section><h2>Ask within a declared scope</h2><p>Use an exact workflow name for its defined result. Broader questions return related workflows and stay marked incomplete.</p><form id="query"><input id="q" aria-label="Clinical question" value="Review heart failure treatment evidence"><button>Inspect answer</button></form><pre id="answer">'+html.escape(json.dumps(compact(checks[0]),indent=2))+'</pre><p>Full review lists: '+ ' · '.join('<a href="'+k+'.json">'+html.escape(k.replace('_',' '))+'</a>' for k in NAMES)+'</p></section>'
perplexity_status='Perplexity completed the same frozen synthetic cases.' if scores['perplexity']['status']=='development_reference_agreement' else 'Perplexity has not completed a live comparison.'
page+='<section><h2>What the model pilot actually measured</h2><p>Authored synthetic challenges; reference labels are not clinician-reviewed. Three questions need adjudication because “no” versus “unknown” is ambiguous. The first Claude request returned HTTP 400; a revised request succeeded. '+perplexity_status+' Qwen followed an embedded instruction in one challenge and is not promoted. No default model was changed.</p><pre>'+html.escape(json.dumps(scores,indent=2))+'</pre></section>'
if (RUN/'model-proposals.json').exists():
 overlay=json.loads((RUN/'model-proposals.json').read_text())
 page+='<section><h2>Model reading of actual synthetic warehouse records</h2><p>Six selected cases received a bounded live frontier reading. These are unreviewed proposals over selected passages, not full-chart adjudications. They do not overwrite the source workflow.</p>'
 for row in overlay['patients']:
  answer=row['model_result']
  page+='<details><summary>'+html.escape(row['patient_id']+' / '+row['domain']+' / '+answer['label'])+'</summary><p>'+html.escape(row['selection'])+'</p>'
  for f in answer['extracted_facts']:
   page+='<blockquote>'+html.escape(f['quote'])+'<cite>'+html.escape(f['concept']+' · '+f['assertion']+' · '+f['source_id'])+'</cite></blockquote>'
  page+='</details>'
 page+='</section>'
student_path=ROOT/'artifacts/agere-context-student-v1/model-card.json'
if student_path.exists():
 student=json.loads(student_path.read_text());(RUN/'context-student.json').write_text(json.dumps(student,indent=2))
 page+='<section><span class="eyebrow">A trained specialist, tested beyond its training format</span><h2>Cheap to run.<br>Still learning to generalize.</h2><p>We trained character-based logistic-regression weights on '+str(student['training_examples'])+' synthetic excerpts after blind Jev agreement with the frontier-authored labels. Concept families were separated before training. This is machine supervision, not clinician review.</p><div class="metrics"><div><b>'+str(student['model_bytes'])+'</b><span>model bytes</span></div><div><b>'+str(round(student['metrics']['test']['agreement_with_authored_targets']*student['metrics']['test']['examples']))+' / '+str(student['metrics']['test']['examples'])+'</b><span>held-out synthetic label matches</span></div></div>'
 if (RUN/'context-shadow-summary.json').exists():
  shadow=json.loads((RUN/'context-shadow-summary.json').read_text());c=shadow['counts']
  page+='<p class="note">The warehouse exposed a generalization problem: '+f"{c['rule_student_disagreements']:,}"+' disagreements with rules across '+f"{c['text_fact_bindings']:,}"+' text facts. Disagreement is not an accuracy measure. The synthetic exercise does not establish clinical reliability.</p><p>'+f"{c['new_unique_predictions']:,}"+' unique model predictions supported this run through exact-input reuse. Runtime: '+f"{shadow['elapsed_seconds']:.2f}"+' seconds. No paid API calls. No workflow decisions changed.</p><a href="context-shadow-summary.json">Inspect warehouse shadow results</a>'
 page+='<p><a href="context-student.json">Inspect model card and limitations</a></p></section>'
page+='<section><h2>Human-reviewed training</h2><p>The existing reviewed-data export contains no reviewed training or calibration rows. The small specialist above uses explicitly labeled machine supervision. No model answer or rule output has been represented as a human review.</p><a href="training-readiness.json">Inspect human-review readiness</a></section>'
page+='<footer><a href="run.json">Run receipt</a> · <a href="source-audit.json">Complete source audit</a> · <a href="executed-questions.json">Executed questions</a> · <a href="examples.json">Source-backed examples</a><p>All data synthetic. No external clinical actions executed.</p></footer></main>'
page+='<script>const cases='+json.dumps([compact(x) for x in checks]).replace('<','\\u003c')+''';function compact(v){if(Array.isArray(v))return v.map(compact);if(v&&typeof v==='object')return Object.fromEntries(Object.entries(v).map(([k,x])=>[k,k==='patient_ids'&&x.length>8?{total:x.length,preview:x.slice(0,8),full_list:'Download the corresponding workflow JSON below'}:compact(x)]));return v;}document.getElementById('query').addEventListener('submit',async e=>{e.preventDefault();const q=document.getElementById('q').value;const panel=document.getElementById('answer');try{if(location.protocol==='file:'){const found=cases.find(x=>x.question.toLowerCase()===q.toLowerCase());panel.textContent=JSON.stringify(found||{status:'Offline preview: run the local service to query other phrases'},null,2)}else{const r=await fetch('/api/query?q='+encodeURIComponent(q));if(!r.ok)throw new Error('Query request failed');panel.textContent=JSON.stringify(compact(await r.json()),null,2)}}catch(err){panel.textContent=err.message}});</script></html>'''
(RUN/'index.html').write_text(page)
print(json.dumps({'report':str(RUN/'index.html'),'executed_questions':len(checks),'training':training['status']}))
