/* A conceptual, local canvas visual. It does not represent model execution or patient links. */
function setupExperience(){
 const descriptions=[
  ['Clinical, financial, and operational records. Start by seeing what’s actually there.','Explore your sources'],
  ['A population. A measure. A timeframe. Give your data a question worth answering.','Frame your question'],
  ['Inspect computed results, compare patterns, and see what the evidence still cannot tell you.','Explore saved analyses'],
  ['Turn a supported finding into a useful view or synthetic follow-up work.','Open action workspace']
 ];
 const names=['Data','Information','Intelligence','Wisdom'];let stage=2;
 const canvas=$('#intelligence-field'),scene=$('#intelligence-scene');
 const ctx=canvas.getContext('2d');
 let w=0,h=0,visible=true,frame=null,paused=matchMedia('(prefers-reduced-motion: reduce)').matches;
 let px=0,py=0,rx=0,ry=0,phase=0,lastTime=0;
 const points=Array.from({length:960},(_,i)=>({i,x:0,y:0,z:0}));
 const random=n=>{const x=Math.sin(n*127.1+311.7)*43758.5453;return x-Math.floor(x)};
 const targets=points.map(({i})=>{
  const u=i/960,a=i*2.39996,polar=Math.acos(1-2*(i+.5)/960);
  return [
   [(random(i+1)-.5)*2.35,(random(i+1200)-.5)*1.65,(random(i+3400)-.5)*1.65],
   [((i%16)-7.5)*.13,(Math.floor(i/16)%15-7)*.13,(Math.floor(i/240)-1.5)*.32],
   [Math.cos(a)*Math.sin(polar)*1.12,Math.cos(polar)*1.12,Math.sin(a)*Math.sin(polar)*1.12],
   [(1+.30*Math.cos(u*2*Math.PI*15))*Math.cos(u*2*Math.PI),.30*Math.sin(u*2*Math.PI*15),(1+.30*Math.cos(u*2*Math.PI*15))*Math.sin(u*2*Math.PI)]
  ];
 });
 points.forEach((p,i)=>[p.x,p.y,p.z]=targets[i][2]);
 function draw(time=0){
  frame=null;if(!ctx||!w||!h)return;
  const dt=Math.min(40,lastTime?time-lastTime:16);lastTime=time;
  if(!paused)phase+=dt*.00013;
  rx+=(px-rx)*.04;ry+=(py-ry)*.04;
  ctx.clearRect(0,0,w,h);
  const scale=Math.min(w*.29,h*.34),yaw=phase+rx*.5+.55,pitch=-.15+ry*.25;
  const cy=Math.cos(yaw),sy=Math.sin(yaw),cx=Math.cos(pitch),sx=Math.sin(pitch);
  const colors=['#d5fa71','#82d6d4','#b8a9fb','#e1fda0'];
  function project(x,y,z){const xx=x*cy-z*sy,zz=x*sy+z*cy,yy=y*cx-zz*sx,depth=y*sx+zz*cx,f=3.8/(3.8+depth);return [w*.5+xx*scale*f,h*.5+yy*scale*f]}
  // Orbital reference planes make the four abstract stages feel spatial.
  for(let ring=0;ring<5;ring++){ctx.beginPath();for(let j=0;j<=180;j++){const a=j/180*Math.PI*2,r=1.18+ring*.13,tilt=ring*.57+.2;const p=project(Math.cos(a)*r,Math.sin(a)*Math.sin(tilt)*r,Math.sin(a)*Math.cos(tilt)*r);j?ctx.lineTo(...p):ctx.moveTo(...p)}ctx.strokeStyle=colors[stage];ctx.globalAlpha=ring===0?.44:.19;ctx.lineWidth=ring===0?1:.65;ctx.stroke();}
  for(let i=0;i<75;i++){ctx.globalAlpha=.08+random(i+50)*.2;ctx.fillStyle='#e0f2e4';ctx.fillRect(random(i+80)*w,random(i+720)*h,1,1)}
  ctx.globalAlpha=1;
  const projected=points.map((p,i)=>{
   const t=targets[i][stage],ease=paused?1:1-Math.pow(.94,dt/16);
   p.x+=(t[0]-p.x)*ease;p.y+=(t[1]-p.y)*ease;p.z+=(t[2]-p.z)*ease;
   const x=p.x*cy-p.z*sy,z=p.x*sy+p.z*cy,y=p.y*cx-z*sx,depth=p.y*sx+z*cx;
   const perspective=3.8/(3.8+depth);
   return {x:w*.5+x*scale*perspective,y:h*.5+y*scale*perspective,z:depth,size:perspective*(i%9===0?2:1.05),i};
  });
  const glow=ctx.createRadialGradient(w*.5,h*.5,0,w*.5,h*.5,scale*1.5);
  glow.addColorStop(0,stage===3?'#d5fa712e':'#a3b5ff17');glow.addColorStop(1,'#d5fa7100');
  ctx.fillStyle=glow;ctx.fillRect(0,0,w,h);ctx.strokeStyle=colors[stage];ctx.lineWidth=.55;
  projected.forEach((p,i)=>{
   const q=projected[(i+(stage===1?14:stage===2?21:1))%960],dist=Math.hypot(p.x-q.x,p.y-q.y);
   if(dist<scale*(stage===3?.22:.36)&&dist>4){ctx.globalAlpha=stage===0?.10:stage===3?.23:.19;ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(q.x,q.y);ctx.stroke()}
  });
  projected.sort((a,b)=>b.z-a.z).forEach(p=>{
   ctx.globalAlpha=Math.max(.17,Math.min(1,(1.9-p.z)*.34));ctx.fillStyle=p.i%17===0?'#f5ffe2':colors[stage];
   if(stage===1)ctx.fillRect(p.x-p.size,p.y-p.size,p.size*1.5,p.size*1.5);
   else{ctx.beginPath();ctx.arc(p.x,p.y,p.size,0,Math.PI*2);ctx.fill()}
  });
  // Three bright satellites travel along separate conceptual orbits.
  for(let i=0;i<3;i++){const a=phase*(i%2?-1:1)+i*2.09,p=project(Math.cos(a)*1.5,Math.sin(a)*.45,Math.sin(a)*1.1);ctx.globalAlpha=.8;ctx.shadowBlur=14;ctx.shadowColor=colors[stage];ctx.fillStyle=colors[stage];ctx.beginPath();ctx.arc(p[0],p[1],2.4,0,Math.PI*2);ctx.fill();ctx.shadowBlur=0;ctx.globalAlpha=.45;ctx.font='8px ui-monospace,monospace';ctx.fillText(['SIGNAL','CONTEXT','NEXT STEP'][i],p[0]+9,p[1]-7);}
  ctx.globalAlpha=.25;ctx.strokeStyle='#a3ba83';ctx.lineWidth=.7;
  [[17,48],[w-17,48],[17,h-48],[w-17,h-48]].forEach(([x,y])=>{ctx.beginPath();ctx.moveTo(x-4,y);ctx.lineTo(x+4,y);ctx.moveTo(x,y-4);ctx.lineTo(x,y+4);ctx.stroke()});ctx.globalAlpha=1;
  if(!paused&&visible&&document.visibilityState==='visible')frame=requestAnimationFrame(draw);
 }
 function start(){if(frame===null&&visible&&document.visibilityState==='visible')frame=requestAnimationFrame(draw)}
 function resize(){const r=scene.getBoundingClientRect();if(!r.width||!r.height)return;w=r.width;h=r.height;const dpr=Math.min(devicePixelRatio||1,2);canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);ctx?.setTransform(dpr,0,0,dpr,0,0);start()}
 function selectStage(n){stage=n;document.querySelectorAll('[data-stage]').forEach((b,i)=>{b.setAttribute('aria-selected',String(i===n));b.tabIndex=i===n?0:-1});$('#stage-detail').setAttribute('aria-labelledby','stage-'+names[n].toLowerCase());$('#scene-word').textContent=names[n].toUpperCase();$('#stage-description').textContent=descriptions[n][0];$('#stage-action').innerHTML=descriptions[n][1]+' <span aria-hidden="true">↗</span>';start()}
 document.querySelectorAll('[data-stage]').forEach((b,i)=>{b.onclick=()=>selectStage(i);b.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const n=e.key==='Home'?0:e.key==='End'?3:(i+(e.key==='ArrowLeft'?3:1))%4;selectStage(n);document.querySelector('[data-stage="'+n+'"]').focus()}});
 $('#stage-action').onclick=()=>{if(stage===0){showPage('data',true);history.replaceState(null,'','/ask#data')}if(stage===1){$('#q').focus();$('#q').scrollIntoView({block:'center',behavior:paused?'instant':'smooth'})}if(stage===2){showPage('saved',true);history.replaceState(null,'','/ask#saved')}if(stage===3)location.href='/action-center'};
 const motion=$('#motion-toggle');function motionLabel(){motion.textContent=paused?'▷':'Ⅱ';motion.setAttribute('aria-label',paused?'Play visual animation':'Pause visual animation')}
 motion.onclick=()=>{paused=!paused;motionLabel();start()};motionLabel();
 scene.addEventListener('pointermove',e=>{const r=scene.getBoundingClientRect();px=(e.clientX-r.left)/r.width-.5;py=(e.clientY-r.top)/r.height-.5});scene.addEventListener('pointerleave',()=>{px=py=0});
 if(ctx){new ResizeObserver(resize).observe(scene);new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;if(visible)start();else if(frame!==null){cancelAnimationFrame(frame);frame=null}}).observe(scene);document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')start()});resize()}
 selectStage(2);
 const drawer=$('#conversation-drawer'),historyButton=$('#history-button');function close(){drawer.hidden=true;historyButton.setAttribute('aria-expanded','false')}
 historyButton.onclick=()=>{drawer.hidden=!drawer.hidden;historyButton.setAttribute('aria-expanded',String(!drawer.hidden));if(!drawer.hidden)$('#close-history').focus()};$('#close-history').onclick=()=>{close();historyButton.focus()};document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!drawer.hidden){close();historyButton.focus()}});document.addEventListener('click',e=>{if(!drawer.hidden&&!drawer.contains(e.target)&&!historyButton.contains(e.target))close()});
 new MutationObserver(()=>$('#turn-count').textContent=String(state.turns.length)).observe($('#thread'),{childList:true});$('#turn-count').textContent=String(state.turns.length);
 loadSources().then(()=>{if(!sourceAtlas)return;$('#live-sources').textContent=fmt(sourceAtlas.source_categories,0);$('#live-records').textContent=new Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:1}).format(sourceAtlas.records);$('#live-records').title=fmt(sourceAtlas.records,0)+' received records';$('#live-patients').textContent=fmt(sourceAtlas.patients,0)});
}
