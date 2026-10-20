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
 const ctx=canvas.getContext('2d'),motionPreference=matchMedia('(prefers-reduced-motion: reduce)');
 let w=0,h=0,visible=true,frame=null,paused=motionPreference.matches;
 let px=0,py=0,rx=0,ry=0,phase=0,age=paused?14:0,lastTime=0;
 const TAU=Math.PI*2,random=n=>{const x=Math.sin(n*127.1+311.7)*43758.5453;return x-Math.floor(x)};
 const clamp=x=>Math.max(0,Math.min(1,x)),smooth=x=>{x=clamp(x);return x*x*(3-2*x)};
 const mix=(a,b,t)=>a+(b-a)*t;
 const branches=[],samples=20;
 function grow(start,direction,length,depth,birth,seed){
  const norm=Math.hypot(...direction),dir=direction.map(v=>v/norm);
  const end=start.map((v,i)=>v+dir[i]*length),id=branches.length;
  const bend=[dir[1]*.28,-dir[0]*.28,(random(seed+9)-.5)*.45];
  const points=Array.from({length:samples+1},(_,i)=>{
   const t=i/samples,arc=Math.sin(t*Math.PI)*length;
   return start.map((v,k)=>mix(v,end[k],t)+bend[k]*arc);
  });
  branches.push({id,depth,birth,seed,points,duration:.8+length*1.8});
  if(depth===4)return;
  const count=depth===0?3:2;
  for(let k=0;k<count;k++){
   const s=seed*3+k+11,spread=depth===0?.85:.72;
   const next=dir.map((v,i)=>v*.76+(random(s+i*101)-.5)*spread*2);
   grow(end,next,length*(.62+random(s+7)*.16),depth+1,birth+length*1.6+.24,s);
  }
 }
 // A bounded, deterministic branching sculpture, independent of warehouse records.
 for(let i=0;i<7;i++){
  const a=i/7*TAU+.3;
  grow([0,0,0],[Math.cos(a),Math.sin(a)*.9,(random(i+60)-.5)*1.5],.58+random(i+70)*.17,0,.2+i*.075,i+1);
 }
 const dust=Array.from({length:420},(_,i)=>({a:random(i+800)*TAU,r:.7+random(i+1600)*2.5,z:random(i+2400)*2-1,speed:.07+random(i+3200)*.13,seed:i}));
 const palette=[['#61798f','#87b9cc','#d3edf2'],['#698bf6','#94c6e9','#bcf9f3'],['#a38aff','#8af2de','#e2ffad'],['#9ea66d','#d9f79b','#fff5ce']];
 let shape=2;
 function draw(time=0){
  frame=null;if(!ctx||!w||!h)return;
  const dt=lastTime?Math.min(40,time-lastTime):0;lastTime=time;
  if(!paused){phase+=dt*.0002;age+=dt*.001;rx+=(px-rx)*.035;ry+=(py-ry)*.035;shape+=(stage-shape)*.025}
  ctx.clearRect(0,0,w,h);
  const scale=Math.min(w*.25,h*.31),yaw=.24+Math.sin(phase*.22)*.30+rx*.3,pitch=-.13+ry*.22;
  const cy=Math.cos(yaw),sy=Math.sin(yaw),cx=Math.cos(pitch),sx=Math.sin(pitch);
  const colors=palette[stage],formation=smooth(age/7),breath=1+Math.sin(phase*1.2)*.035;
  function project(point,depth=0){
   let [x,y,z]=point;
   // The selected stage changes the same organism, rather than replacing it with a diagram.
   const focus=clamp(shape-2),order=1-Math.min(1,Math.abs(shape-1));
   x*=1.32-focus*.22;y*=.89+focus*.13;
   z+=Math.sin(x*2+phase*.7)*.055*depth;
   y+=Math.sin(x*2.4+phase*.8)*.024*depth;
   y=mix(y,Math.round(y*5)/5,order*.3);
   x*=breath;y*=breath;
   const xx=x*cy-z*sy,zz=x*sy+z*cy,yy=y*cx-zz*sx,zz2=y*sx+zz*cx,f=4.8/(4.8+zz2);
   return [w*.5+xx*scale*f,h*.51+yy*scale*f,f];
  }
  function line(points,color,alpha,width){
   if(points.length<2||alpha<.003)return;
   ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));
   ctx.strokeStyle=color;ctx.globalAlpha=alpha;ctx.lineWidth=width;ctx.stroke();
  }
  function glow(x,y,r,color,alpha){
   if(alpha<.003)return;
   const g=ctx.createRadialGradient(x,y,0,x,y,r);g.addColorStop(0,color);g.addColorStop(1,color+'00');
   ctx.fillStyle=g;ctx.globalAlpha=alpha;ctx.fillRect(x-r,y-r,r*2,r*2);
  }
  ctx.globalCompositeOperation='lighter';ctx.lineCap='round';ctx.lineJoin='round';
  // Deep, soft light reveals itself around the seed; the surrounding space stays quiet.
  glow(w*.49,h*.51,scale*1.35,'#6853bc',.12*formation);
  glow(w*.57,h*.48,scale*.85,'#4bba9b',.07*formation);
  for(let i=0;i<90;i++){
   const a=.035+random(i+510)*.12;
   ctx.globalAlpha=a;ctx.fillStyle='#b7cacf';ctx.fillRect(random(i+200)*w,random(i+470)*h,.7,.7);
  }
  // Fine matter spirals inward. The long exposure trails make direction visible.
  for(const d of dust){
   const cycle=(phase*d.speed+random(d.seed+4500))%1,r=.12+(1-cycle)*d.r;
   const a=d.a+cycle*1.5+phase*.05;
   const p=project([Math.cos(a)*r,Math.sin(a)*r*.8,d.z*(1-cycle)]);
   const q=project([Math.cos(a-.025)*r*1.025,Math.sin(a-.025)*r*.82,d.z*(1-cycle)]);
   const alpha=Math.sin(cycle*Math.PI)*(.12+random(d.seed+5200)*.3)*smooth(age/2);
   line([q,p],colors[1],alpha,.65);
   ctx.globalAlpha=alpha;ctx.fillStyle=d.seed%7===0?colors[2]:colors[1];ctx.fillRect(p[0],p[1],d.seed%9===0?1.6:.85,.85);
  }
  const fieldStrength=stage===0?.18:1;
  for(const b of branches){
   const initial=smooth((age-b.birth)/b.duration);
   // The outer growth front continues to sprout and recede after the first formation.
   const living=b.depth===4?.7+.3*Math.sin(phase*.65+b.seed*.13):1;
   const progress=initial*living;if(progress<=0)continue;
   const end=progress*samples,last=Math.floor(end),path=[];
   for(let j=0;j<=last;j++)path.push(project(b.points[j],b.depth));
   if(last<samples){const t=end-last;path.push(project(b.points[last].map((v,k)=>mix(v,b.points[last+1][k],t)),b.depth))}
   const tip=path[path.length-1],color=colors[b.depth<2?0:b.depth<4?1:2];
   const depthLight=clamp(tip[2]-.45),alpha=(.44+(4-b.depth)*.10)*depthLight*fieldStrength;
   // Wide low-opacity underpainting gives filaments volume without expensive per-line shadows.
   if(b.depth<3)line(path,color,alpha*.17,9-b.depth*1.8);
   // Parallel, moving hairline strands give each branch a living fibrous surface.
   if(b.depth<4)for(let strand=0;strand<3;strand++){
    const fiber=path.map((p,j)=>{const spread=Math.sin(j/samples*Math.PI)*(2+b.depth);return [p[0]+Math.sin(j*.34+strand*2.1+phase*.5+b.seed)*spread,p[1]+Math.cos(j*.30+strand*2.1+phase*.5+b.seed)*spread]});
    line(fiber,colors[1],alpha*.32,.45);
   }
   line(path,color,alpha,1.35-b.depth*.19);
   const pulse=(phase*.5-b.birth*.36)%2.6;
   if(pulse>0&&pulse<progress){
    const at=Math.floor(pulse*samples),p=path[Math.min(at,path.length-1)];
    line(path.slice(Math.max(0,at-3),at+1),colors[2],.76*fieldStrength,1.3);
    if(b.id%3===0)glow(p[0],p[1],4.5,colors[1],.24*fieldStrength);
   }
   if(b.depth>1){
    const light=(.4+.3*Math.sin(phase*1.7+b.seed))*initial*fieldStrength;
    ctx.globalAlpha=light;ctx.fillStyle=colors[2];ctx.beginPath();ctx.arc(tip[0],tip[1],b.depth===4?.85:1.25,0,TAU);ctx.fill();
    if(b.id%5===0)glow(tip[0],tip[1],8,colors[1],light*.38);
   }
   // Suspended grains gather around every branch, following its growth front.
   for(let j=2;j<last;j++){
    const p=path[j],s=b.id*31+j,offset=2.5+random(s)*6;
    ctx.globalAlpha=(.19+random(s+100)*.36)*fieldStrength;ctx.fillStyle=colors[b.depth>2?2:1];
    ctx.fillRect(p[0]+Math.sin(s+phase*.4)*offset,p[1]+Math.cos(s+phase*.3)*offset,.8,.8);
   }
  }
  // A restrained luminous nucleus, with a persistent heartbeat rather than flashing.
  const seed=smooth(age/.9),heart=.75+Math.sin(phase*1.2)*.12;
  glow(w*.5,h*.51,scale*.30,'#a5b6ff',.16*seed*heart);
  glow(w*.5,h*.51,scale*.10,'#cce8da',.48*seed*heart);
  glow(w*.5,h*.51,scale*.035,'#f1ffdf',.8*seed);
  ctx.globalAlpha=1;ctx.globalCompositeOperation='source-over';
  if(!paused&&visible&&document.visibilityState==='visible')frame=requestAnimationFrame(draw);
 }
 function start(){if(frame===null&&visible&&document.visibilityState==='visible'){lastTime=0;frame=requestAnimationFrame(draw)}}
 function stop(){if(frame!==null)cancelAnimationFrame(frame);frame=null;lastTime=0}
 function resize(){const r=scene.getBoundingClientRect();if(!r.width||!r.height)return;w=r.width;h=r.height;const dpr=Math.min(devicePixelRatio||1,2);canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);ctx?.setTransform(dpr,0,0,dpr,0,0);start()}
 function selectStage(n){stage=n;if(paused){shape=n;age=14}else age=0;document.querySelectorAll('[data-stage]').forEach((b,i)=>{b.setAttribute('aria-selected',String(i===n));b.tabIndex=i===n?0:-1});$('#stage-detail').setAttribute('aria-labelledby','stage-'+names[n].toLowerCase());$('#scene-word').textContent=names[n].toUpperCase();$('#stage-description').textContent=descriptions[n][0];$('#stage-action').innerHTML=descriptions[n][1]+' <span aria-hidden="true">↗</span>';start()}
 document.querySelectorAll('[data-stage]').forEach((b,i)=>{b.onclick=()=>selectStage(i);b.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const n=e.key==='Home'?0:e.key==='End'?3:(i+(e.key==='ArrowLeft'?3:1))%4;selectStage(n);document.querySelector('[data-stage="'+n+'"]').focus()}});
 $('#stage-action').onclick=()=>{if(stage===0){showPage('data',true);history.replaceState(null,'','/ask#data')}if(stage===1){$('#q').focus();$('#q').scrollIntoView({block:'center',behavior:paused?'instant':'smooth'})}if(stage===2){showPage('saved',true);history.replaceState(null,'','/ask#saved')}if(stage===3)location.href='/action-center'};
 const motion=$('#motion-toggle');function motionLabel(){motion.textContent=paused?'▷':'Ⅱ';motion.setAttribute('aria-label',paused?'Play visual animation':'Pause visual animation');motion.setAttribute('aria-pressed',String(paused))}
 motion.onclick=()=>{paused=!paused;motionLabel();start()};motionLabel();
 motionPreference.addEventListener('change',e=>{paused=e.matches;if(paused){age=14;shape=stage}motionLabel();start()});
 scene.addEventListener('pointermove',e=>{const r=scene.getBoundingClientRect();px=(e.clientX-r.left)/r.width-.5;py=(e.clientY-r.top)/r.height-.5});scene.addEventListener('pointerleave',()=>{px=py=0});
 if(ctx){new ResizeObserver(resize).observe(scene);new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;if(visible)start();else stop()}).observe(scene);document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')start();else stop()});resize()}
 selectStage(2);
 const drawer=$('#conversation-drawer'),historyButton=$('#history-button');function close(){drawer.hidden=true;historyButton.setAttribute('aria-expanded','false')}
 historyButton.onclick=()=>{drawer.hidden=!drawer.hidden;historyButton.setAttribute('aria-expanded',String(!drawer.hidden));if(!drawer.hidden)$('#close-history').focus()};$('#close-history').onclick=()=>{close();historyButton.focus()};document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!drawer.hidden){close();historyButton.focus()}});document.addEventListener('click',e=>{if(!drawer.hidden&&!drawer.contains(e.target)&&!historyButton.contains(e.target))close()});
 new MutationObserver(()=>$('#turn-count').textContent=String(state.turns.length)).observe($('#thread'),{childList:true});$('#turn-count').textContent=String(state.turns.length);
 loadSources().then(()=>{if(!sourceAtlas)return;$('#live-sources').textContent=fmt(sourceAtlas.source_categories,0);$('#live-records').textContent=new Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:1}).format(sourceAtlas.records);$('#live-records').title=fmt(sourceAtlas.records,0)+' received records';$('#live-patients').textContent=fmt(sourceAtlas.patients,0)});
}
