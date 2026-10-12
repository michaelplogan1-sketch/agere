function addQuestionLearning(box,candidate){
 const area=N('details'),body=N('div'),load=N('button','Check source-learning eligibility'),status=N('p','','meta');
 area.append(N('summary','Create a source-learning task'),body);body.append(N('p','Select an exact phrase to inspect or correct with the statement readers. The current family policy is checked before acquisition; this does not label the source or resolve this question.','meta'),load,status);box.append(area);
 load.onclick=async()=>{load.disabled=true;try{
  const r=await workspaceFetch('/api/stack/question-learning/preview?candidate_id='+candidate.id);
  if(!r.eligible_for_acquisition){status.textContent='Excluded from learning acquisition: '+human(r.exclusion_reason)+'.';return}
  if(!r.passages.length){status.textContent='No supported received text field in these candidate passages. Structured display representations cannot become source-language targets.';return}
  status.textContent='Eligible acquisition training family. Choose a received passage and an exact phrase.';
  const passage=N('select');passage.setAttribute('aria-label','Learning candidate passage');for(const p of r.passages){const o=N('option','Rank '+p.rank+' · '+p.record_id+' · '+p.field);o.value=p.rank;passage.append(o)}
  const text=N('blockquote',r.passages[0].text);passage.onchange=()=>{text.textContent=r.passages.find(p=>p.rank===Number(passage.value)).text};
  const quote=N('input');quote.placeholder='Exact phrase from this passage';quote.maxLength=2000;quote.setAttribute('aria-label','Learning source phrase');
  const concept=N('input');concept.placeholder='Concept to interpret';concept.maxLength=150;concept.setAttribute('aria-label','Learning target concept');
  const occurrence=N('input');occurrence.type='number';occurrence.min=1;occurrence.max=500;occurrence.value=1;occurrence.setAttribute('aria-label','Learning phrase occurrence within passage');
  const label=N('label','Occurrence within passage (first = 1) ');label.append(occurrence);
  const save=N('button','Prepare source-learning task'),message=N('p','','meta');save.onclick=async()=>{save.disabled=true;try{
   const task=await workspaceFetch('/api/stack/question-learning',{candidate_id:candidate.id,passage_rank:Number(passage.value),quote:quote.value,occurrence:Number(occurrence.value)-1,concept:concept.value});
   message.textContent='Saved exact source task. No review label or model call was created.';const link=N('a','Open task in learning lab →');link.href='/learn?task='+task.id;body.append(link);
  }catch(e){message.textContent=e.message}finally{save.disabled=false}};
  body.append(passage,text,quote,concept,label,save,message);
  addSourceSpanProposals(body,candidate,passage);
 }catch(e){status.textContent=e.message;load.disabled=false}};
}

function addSourceSpanProposals(body,candidate,passage){
 const area=N('details'),content=N('div'),check=N('button','Preview specialist source scope'),status=N('p','','meta');
 area.append(N('summary','Suggest phrases and concepts for this question'),content);content.append(N('p','A bounded specialist can suggest exact phrases across clinical, functional, access and other source concepts. Suggestions remain machine proposals. Selecting one creates an unreviewed learning task.','meta'),check,status);body.append(area);
 let generation=0;
 function show(row,box){
  box.append(N('p',human(row.question_relation)+(row.abstained?' · no target proposed':'')),N('p',row.scope_reason));
  for(const p of row.proposals){const item=N('div',undefined,'source');item.append(N('h4',p.concept),N('blockquote',p.quote),N('p',p.reason,'meta'));const select=N('button','Create task from this machine proposal'),msg=N('p','','meta');select.onclick=async()=>{select.disabled=true;try{const result=await workspaceFetch('/api/stack/question-span-availability/select',{receipt_id:row.id,proposal_id:p.id});msg.textContent='Saved unreviewed source task. The original question remains unresolved.';const link=N('a','Open suggested task in learning lab →');link.href='/learn?task='+result.task_id;item.append(link)}catch(e){msg.textContent=e.message}finally{select.disabled=false}};item.append(select,msg);box.append(item)}
  if(row.unresolved.length)box.append(N('p','Still unresolved: '+row.unresolved.join(' '),'missing'));
  const raw=N('details');raw.append(N('summary','Source, provider usage and proposal provenance'),N('pre',JSON.stringify(row,null,2)));box.append(raw);
 }
 check.onclick=async()=>{const request={candidate_id:candidate.id,passage_rank:Number(passage.value)},mine=++generation;check.disabled=true;try{
  const preview=await workspaceFetch('/api/stack/question-span-availability/preview?candidate_id='+request.candidate_id+'&passage_rank='+request.passage_rank);if(mine!==generation)return;
  content.replaceChildren(check,status);status.textContent='One received '+preview.source.source_table+' record · '+preview.request_bytes+' request bytes · full '+preview.field+' field and reported siblings. Other records are outside this request.';
  content.append(N('blockquote',preview.selected_passage.text));if(preview.source_availability){const av=N('details');av.append(N('summary','Received records versus submitted content'),N('pre',JSON.stringify(preview.source_availability,null,2)));content.append(av)}const box=N('div');content.append(box);
  if(preview.saved){show(preview.saved,box);return}
  if(preview.attempt){box.append(N('p','An earlier attempt has no completed receipt. Inspect its failure record; automatic retries are disabled.','missing'));return}
  const run=N('button','Suggest source phrases with specialist');run.onclick=async()=>{run.disabled=true;try{const row=await workspaceFetch('/api/stack/question-span-availability',request);if(mine===generation)show(row,box)}catch(e){if(mine===generation)box.append(N('p',e.message,'missing'))}};box.append(run);
 }catch(e){status.textContent=e.message}finally{check.disabled=false}};
 passage.addEventListener('change',()=>{generation++;content.replaceChildren(check,status);status.textContent='Passage changed. Preview the selected source before requesting suggestions.'});
}
