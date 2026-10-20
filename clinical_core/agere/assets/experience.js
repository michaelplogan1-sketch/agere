/* The sculpture visualizes recorded activity; ambient motion is illustrative. */
function setupExperience(){
 const descriptions=[
  ['Clinical, financial, and operational records. Start by seeing what’s actually there.','Explore your sources'],
  ['A population. A measure. A timeframe. Give your data a question worth answering.','Frame your question'],
  ['Inspect computed results, compare patterns, and see what the evidence still cannot tell you.','Explore saved analyses'],
  ['Turn a supported finding into a useful view or synthetic follow-up work.','Open action workspace']
 ];
 const names=['Data','Information','Intelligence','Wisdom'];let stage=0,reached=0,manual=false;
 const canvas=$('#intelligence-field'),scene=$('#intelligence-scene'),motion=$('#motion-toggle');
 const preference=matchMedia('(prefers-reduced-motion: reduce)');
 let paused=preference.matches,visible=true,frame=null,last=0,age=0,previous=null,refreshing=false;
 const field=AgereNeural.createNeuralField(canvas);
 if(!field){scene.dataset.renderer='unavailable';canvas.hidden=true;$('#scene-event').textContent='3D preview unavailable on this device';motion.hidden=true}
 function selectStage(n,user=true,redraw=true){stage=n;reached=Math.max(reached,n);manual=manual||user;field?.stage(n,paused);
  document.querySelectorAll('[data-stage]').forEach((b,i)=>{b.setAttribute('aria-selected',String(i===n));b.dataset.reached=String(i<=reached);b.tabIndex=i===n?0:-1});
  $('#stage-detail').setAttribute('aria-labelledby','stage-'+names[n].toLowerCase());$('#scene-word').textContent=names[n].toUpperCase();
  $('#stage-description').textContent=descriptions[n][0];$('#stage-action').innerHTML=descriptions[n][1]+' <span aria-hidden="true">↗</span>';canvas.dataset.reachedStage=String(reached);if(paused)field?.still();if(redraw)start();
 }
 function draw(time){frame=null;if(!field)return;if(!paused&&last&&time-last<30){frame=requestAnimationFrame(draw);return}
  const dt=paused||!last?0:Math.min(.05,(time-last)/1000);last=time;age+=dt;
  if(!manual&&stage<3&&age>4*(stage+1))selectStage(stage+1,false,false);
  field.render(dt);if(!paused&&visible&&document.visibilityState==='visible')frame=requestAnimationFrame(draw);
 }
 function start(){if(field&&frame===null&&visible&&document.visibilityState==='visible'){last=0;frame=requestAnimationFrame(draw)}}
 function stop(){if(frame!==null)cancelAnimationFrame(frame);frame=null;last=0}
 function motionLabel(){motion.textContent=paused?'▷':'Ⅱ';motion.setAttribute('aria-label',paused?'Play visual animation':'Pause visual animation');motion.setAttribute('aria-pressed',String(paused))}
 motion.onclick=()=>{paused=!paused;motionLabel();if(paused)stop();else start()};motionLabel();
 preference.addEventListener('change',e=>{paused=e.matches;if(paused){selectStage(3,false);field?.still()}motionLabel();start()});
 scene.addEventListener('pointermove',e=>{const r=scene.getBoundingClientRect();field?.pointer((e.clientX-r.left)/r.width-.5,(e.clientY-r.top)/r.height-.5)});scene.addEventListener('pointerleave',()=>field?.pointer(0,0));
 if(field){new ResizeObserver(()=>{const r=scene.getBoundingClientRect();if(r.width&&r.height){field.resize(r.width,r.height);start()}}).observe(scene);new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;if(visible){start();refreshActivity()}else stop()}).observe(scene)}
 document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'){start();refreshActivity()}else stop()});
 document.querySelectorAll('[data-stage]').forEach((b,i)=>{b.onclick=()=>selectStage(i);b.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const n=e.key==='Home'?0:e.key==='End'?3:(i+(e.key==='ArrowLeft'?3:1))%4;selectStage(n);document.querySelector('[data-stage="'+n+'"]').focus()}});
 $('#stage-action').onclick=()=>{if(stage===0){showPage('data',true);history.replaceState(null,'','/ask#data')}if(stage===1){$('#q').focus();$('#q').scrollIntoView({block:'center',behavior:paused?'instant':'smooth'})}if(stage===2){showPage('saved',true);history.replaceState(null,'','/ask#saved')}if(stage===3)location.href='/action-center'};
 async function refreshActivity(){
  if(refreshing)return;refreshing=true;
  try{
   const response=await fetch('/api/stack/intelligence-activity');if(!response.ok)throw Error('unavailable');const sample=await response.json();
   if(!previous){try{previous=JSON.parse(localStorage.getItem('agere-field-activity')||'null')}catch(_){}}
   const changes=field?.ingest(sample,previous)||AgereNeural.activityChanges(previous,sample);
   const compact=n=>n==null?'—':new Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:1}).format(n);
   $('#field-answers').textContent=compact(sample.answered);$('#field-memory').textContent=compact(sample.reusable_examples);$('#field-records').textContent=compact(sample.records);
   const message=changes.sourceChanged?'A new warehouse snapshot has arrived':changes.examples?'New checked examples joined the learning memory':changes.answers?'A new answer. Another connection.':changes.corrections?'Feedback recorded · flagged answers excluded from reuse':changes.records?'New data is joining the network':'Evolving with your warehouse';
   $('#scene-event').textContent=message+(field?'':' · 3D preview unavailable');$('#field-activity').dataset.status='current';
   if(changes.sourceChanged){sourceAtlas=null;sourceError=null;loadSources()}
   previous=sample;try{localStorage.setItem('agere-field-activity',JSON.stringify(sample))}catch(_){}
   if(paused)field?.still();start();
  }catch(_){$('#scene-event').textContent=previous?'Activity update unavailable · showing last observation':'Activity unavailable · illustrative motion';$('#field-activity').dataset.status='unavailable'}
  finally{refreshing=false}
 }
 document.addEventListener('agere-activity-changed',refreshActivity);
 setInterval(()=>{if(document.visibilityState==='visible')refreshActivity()},20000);
 selectStage(paused?3:0,false);refreshActivity();
 const drawer=$('#conversation-drawer'),historyButton=$('#history-button');function close(){drawer.hidden=true;historyButton.setAttribute('aria-expanded','false')}
 historyButton.onclick=()=>{drawer.hidden=!drawer.hidden;historyButton.setAttribute('aria-expanded',String(!drawer.hidden));if(!drawer.hidden)$('#close-history').focus()};$('#close-history').onclick=()=>{close();historyButton.focus()};document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!drawer.hidden){close();historyButton.focus()}});document.addEventListener('click',e=>{if(!drawer.hidden&&!drawer.contains(e.target)&&!historyButton.contains(e.target))close()});
 new MutationObserver(()=>$('#turn-count').textContent=String(state.turns.length)).observe($('#thread'),{childList:true});$('#turn-count').textContent=String(state.turns.length);
 loadSources().then(()=>{if(!sourceAtlas)return;$('#live-sources').textContent=fmt(sourceAtlas.source_categories,0);$('#live-records').textContent=new Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:1}).format(sourceAtlas.records);$('#live-records').title=fmt(sourceAtlas.records,0)+' received records';$('#live-patients').textContent=fmt(sourceAtlas.patients,0)});
}
