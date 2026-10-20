/* A conceptual, local canvas visual. It does not represent model execution or patient links. */
function setupExperience(){
 const descriptions=[
  ['Clinical, financial, and operational records. Start by seeing what’s actually there.','Explore your sources'],
  ['A population. A measure. A timeframe. Give your data a question worth answering.','Frame your question'],
  ['Inspect computed results, compare patterns, and see what the evidence still cannot tell you.','Explore saved analyses'],
  ['Turn a supported finding into a useful view or synthetic follow-up work.','Open action workspace']
 ];
 const names=['Data','Information','Intelligence','Wisdom'];let stage=0;
 const canvas=$('#intelligence-field'),scene=$('#intelligence-scene'),ctx=canvas.getContext('2d');
 const motionPreference=matchMedia('(prefers-reduced-motion: reduce)');
 let paused=motionPreference.matches,w=0,h=0,visible=true,frame=null,lastTime=0;
 let age=paused?35:0,phase=0,px=0,py=0,rx=0,ry=0,reached=0,manualStage=false,nextGrowth=1.5,serial=0,lastReadout=-1;
 const TAU=Math.PI*2,clamp=x=>Math.max(0,Math.min(1,x)),mix=(a,b,t)=>a+(b-a)*t;
 const smooth=x=>{x=clamp(x);return x*x*(3-2*x)};
 const random=n=>{const x=Math.sin(n*127.1+311.7)*43758.5453;return x-Math.floor(x)};
 const branches=[],connections=[];
 // An abstract neural sculpture with cumulative stages and bounded continuing growth.
 // It is a conceptual illustration, not biological simulation or live model telemetry.
 const cells=[
  {p:[-.08,.02,.13],size:1.18,angle:-1.12,unlock:0},
  {p:[-1.06,-.57,-.18],size:.84,angle:-2.15,unlock:0},
  {p:[.91,-.60,.12],size:1,angle:-.74,unlock:1},
  {p:[-.97,.69,.20],size:.91,angle:2.12,unlock:1},
  {p:[1.02,.68,-.22],size:.90,angle:.91,unlock:2},
  {p:[.13,-1.05,-.30],size:.73,angle:-1.70,unlock:3}
 ].map((cell,id)=>({...cell,id,born:null,branches:[],spines:[],axon:null}));
 function curve(a,b,bend,steps=20){
  const dx=b[0]-a[0],dy=b[1]-a[1],len=Math.hypot(dx,dy)||1;
  return Array.from({length:steps+1},(_,j)=>{const t=j/steps,s=Math.sin(t*Math.PI);return [mix(a[0],b[0],t)-dy/len*bend*s,mix(a[1],b[1],t)+dx/len*bend*s,mix(a[2],b[2],t)+Math.sin(t*TAU)*bend*.25]});
 }
 function arbor(cell,start,angle,length,depth,delay,seed){
  const end=[start[0]+Math.cos(angle)*length,start[1]+Math.sin(angle)*length,start[2]+(random(seed+19)-.5)*length*.7];
  const points=curve(start,end,(random(seed+3)-.5)*length*.95,16);
  const branch={cell,points,depth,delay,seed,duration:1.5+length*3,width:(4.8-depth*1.2)*cell.size};
  branches.push(branch);cell.branches.push(branch);
  if(depth>0)cell.spines.push(points[12]);
  if(depth===3)return;
  // Unequal bifurcations and a dominant apical branch avoid radial star symmetry.
  const split=.28+random(seed+40)*.42;
  arbor(cell,end,angle-split,length*(.54+random(seed+30)*.17),depth+1,delay+.7+length,seed*3+1);
  arbor(cell,end,angle+split*.8,length*(.61+random(seed+50)*.15),depth+1,delay+.85+length,seed*3+2);
 }
 cells.forEach(cell=>{
  const a=cell.angle;
  [[a,.39],[a+1.65,.27],[a+3.00,.25],[a+4.25,.30]].forEach(([angle,length],arm)=>arbor(cell,cell.p,angle,length*cell.size,0,.2+arm*.13,cell.id*47+arm+1));
  const direction=a+2.6;
  cell.axon=curve(cell.p,[cell.p[0]+Math.cos(direction)*.36,cell.p[1]+Math.sin(direction)*.36,cell.p[2]-.10],.06,20);
 });
 function connect(from,to,permanent=false,born=age){
  const id=serial++,a=from.axon[from.axon.length-1];
  const target=to.spines[Math.floor(random(id+600)*to.spines.length)],b=target;
  connections.push({id,from,to,points:curve(a,b,(random(id+700)-.5)*.55,44),born,duration:4.5+random(id+800)*3,permanent,life:35+random(id+900)*25});
 }
 function activate(){
  cells.forEach(cell=>{
   if(cell.born!==null||cell.unlock>reached)return;
   cell.born=paused?age-15:age+cell.id*.14;
   const available=cells.filter(other=>other!==cell&&other.born!==null);
   if(available.length)connect(available[available.length-1],cell,true,paused?age-12:cell.born+1.4);
  });
 }
 function selectStage(n,manual=true,redraw=true){
  stage=n;reached=Math.max(reached,n);manualStage=manualStage||manual;activate();
  document.querySelectorAll('[data-stage]').forEach((b,i)=>{b.setAttribute('aria-selected',String(i===n));b.dataset.reached=String(i<=reached);b.tabIndex=i===n?0:-1});
  $('#stage-detail').setAttribute('aria-labelledby','stage-'+names[n].toLowerCase());$('#scene-word').textContent=names[n].toUpperCase();
  $('#stage-description').textContent=descriptions[n][0];$('#stage-action').innerHTML=descriptions[n][1]+' <span aria-hidden="true">↗</span>';
  canvas.dataset.reachedStage=String(reached);if(redraw)start();
 }
 function draw(time=0){
  frame=null;if(!ctx||!w||!h)return;
  // Cap work at approximately 60 fps, including on high-refresh displays.
  if(!paused&&lastTime&&time-lastTime<16){frame=requestAnimationFrame(draw);return}
  const dt=lastTime?Math.min(50,time-lastTime):0;lastTime=time;
  if(!paused){
   age+=dt*.001;phase+=dt*.00016;rx+=(px-rx)*.04;ry+=(py-ry)*.04;
   if(!manualStage&&stage<3&&age>5*(stage+1))selectStage(stage+1,false,false);
   if(age>=nextGrowth){
    nextGrowth=age+2.3;
    const active=cells.filter(c=>c.born!==null&&age-c.born>2);
    if(active.length>1){const from=active[serial%active.length],others=active.filter(c=>c!==from);connect(from,others[Math.floor(random(serial+1000)*others.length)])}
   }
   for(let i=connections.length-1;i>=0;i--){const c=connections[i];if(!c.permanent&&age-c.born>c.life+5)connections.splice(i,1)}
  }
  // These describe only this illustrated network and make continuity inspectable in the DOM.
  if(Math.floor(age)!==lastReadout){lastReadout=Math.floor(age);canvas.dataset.conceptAge=String(lastReadout);canvas.dataset.connectionsCreated=String(serial);canvas.dataset.activeConnections=String(connections.length)}
  ctx.clearRect(0,0,w,h);ctx.lineCap='round';ctx.lineJoin='round';
  const scale=Math.min(w*.255,h*.315),yaw=.18+Math.sin(phase*.22)*.26+rx*.25,pitch=-.12+ry*.18;
  const cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);
  function project(p){const breath=1+Math.sin(phase*.9)*.018,xx=p[0]*breath,yy=p[1]*breath+Math.sin(p[0]*2+phase*.5)*.045,zz=p[2]+Math.sin(p[1]*2+phase*.4)*.08,x=xx*cy-zz*sy,z=xx*sy+zz*cy,y=yy*cp-z*sp,f=5/(5+z);return [w*.5+x*scale*f,h*.51+y*scale*f,f]}
  function line(points,color,alpha,width){if(points.length<2||alpha<.003)return;ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.strokeStyle=color;ctx.globalAlpha=alpha;ctx.lineWidth=width;ctx.stroke()}
  function glow(x,y,r,color,alpha){if(alpha<.003||r<=0)return;const g=ctx.createRadialGradient(x,y,0,x,y,r);g.addColorStop(0,color);g.addColorStop(1,color+'00');ctx.fillStyle=g;ctx.globalAlpha=alpha;ctx.fillRect(x-r,y-r,r*2,r*2)}
  function partial(points,t){const at=clamp(t)*(points.length-1),last=Math.floor(at),path=points.slice(0,last+1).map(project);if(last<points.length-1)path.push(project(points[last].map((v,k)=>mix(v,points[last+1][k],at-last))));return path}
  function growthTip(path,strength=1){
   if(path.length<2)return;const p=path[path.length-1];
   glow(p[0],p[1],12,'#8bf4df',.25*strength);
   ctx.fillStyle='#d8ffe9';ctx.globalAlpha=.88*strength;ctx.beginPath();ctx.arc(p[0],p[1],1.25,0,TAU);ctx.fill();
  }
  ctx.globalCompositeOperation='lighter';
  glow(w*.48,h*.5,scale*1.65,'#7160bd',.11);
  glow(w*.66,h*.43,scale*1.05,'#3a9b91',.08);
  for(let i=0;i<60;i++){ctx.globalAlpha=.04+random(i+20)*.12;ctx.fillStyle='#acc7d4';ctx.fillRect(random(i+40)*w,random(i+60)*h,.7,.7)}
  // Matter drifts into the network; everything shares the same continuous clock.
  for(let i=0;i<260;i++){
   const travel=(age*(.018+random(i+600)*.02)+random(i+700))%1,a=random(i+800)*TAU+travel*.75;
   const radius=2.2*(1-travel)+.18,p=project([Math.cos(a)*radius,Math.sin(a)*radius*.78,(random(i+900)-.5)*1.5]);
   const opacity=Math.sin(travel*Math.PI)*(.12+random(i+1000)*.2)*smooth(age/3);
   ctx.globalAlpha=opacity;ctx.fillStyle=i%5?'#91c8e2':'#c7fbbd';ctx.fillRect(p[0],p[1],i%11?.7:1.3,.7);
  }
  function filament(path,color,alpha,width=1){
   line(path,color,alpha*.12,width*7);
   line(path,color,alpha,width);
   for(let strand=0;strand<2;strand++){
    const fiber=path.map((p,j)=>{const spread=Math.sin(j/(path.length-1)*Math.PI)*2.8;return [p[0]+Math.sin(j*.42+strand*2.4+phase)*spread,p[1]+Math.cos(j*.32+strand*2.4+phase)*spread]});
    line(fiber,'#b8caf4',alpha*.28,.45);
   }
  }
  for(const cell of cells){if(cell.born===null)continue;const progress=smooth((age-cell.born-.5)/2.8),path=partial(cell.axon,progress);filament(path,'#b5a1f6',.55,.9)}
  for(const c of connections){
   const t=age-c.born;if(t<=0)continue;const progress=smooth(t/c.duration),fade=c.permanent?1:1-smooth((t-c.life)/5),path=partial(c.points,progress);
   filament(path,c.id%3?'#7bdacb':'#b7a4f6',.48*fade,.75);
   if(progress<1)growthTip(path,fade);
   const signal=(age*.17+c.id*.17)%1;
   if(progress===1){const at=Math.floor(signal*(path.length-1)),p=path[at];line(path.slice(Math.max(0,at-5),at+1),'#dbfbd4',.9*fade,1.3);glow(p[0],p[1],5,'#9be7da',.28*fade)}
  }
  for(const b of branches){
   if(b.cell.born===null)continue;const progress=smooth((age-b.cell.born-b.delay)/b.duration);if(!progress)continue;
   const path=partial(b.points,progress),color=b.depth<1?'#a798ec':b.depth<3?'#80dfd3':'#d6ecc0';
   filament(path,color,.5-b.depth*.05,1.3-b.depth*.23);
   // Tiny moving grains gather on the arbors, rather than a solid illustrated outline.
   for(let j=1;j<path.length;j++){
    const p=path[j],seed=b.seed*31+j,spread=2+random(seed)*5;
    ctx.globalAlpha=.16+random(seed+400)*.38;ctx.fillStyle=j%5?color:'#e5ffde';
    ctx.fillRect(p[0]+Math.sin(seed+phase*.7)*spread,p[1]+Math.cos(seed+phase*.5)*spread,.75,.75);
    if(j%4===0){const q=path[j-1];line([q,p],color,.2,.5)}
   }
   if(progress<1)growthTip(path,.75);
   const tip=path[path.length-1];if(b.depth===3){glow(tip[0],tip[1],5,'#83cfbf',.10);ctx.globalAlpha=.4;ctx.fillStyle='#d8f9c4';ctx.fillRect(tip[0],tip[1],1,1)}
  }
  // Woven light and a granular core bring back the abstract neural aesthetic.
  // No anatomical oval, iris, lens flare or star-shaped center.
  cells.forEach(cell=>{
   if(cell.born===null)return;const born=smooth((age-cell.born)/1.8);if(!born)return;
   const p=project(cell.p),r=Math.max(8,scale*.115)*cell.size*p[2]*born;
   glow(p[0],p[1],r*3.4,'#8274c8',.16*born);
   glow(p[0],p[1],r*1.7,'#83d9ca',.18*born);
   for(let strand=0;strand<11;strand++){
    const path=[],tilt=strand*.79+cell.angle;
    for(let j=0;j<=36;j++){
     const a=j/36*TAU,wave=1+.17*Math.sin(a*3+phase+strand),x=Math.cos(a)*r*wave,y=Math.sin(a)*r*(.35+random(strand+cell.id)*.48),z=Math.sin(a*2+phase*.4+strand)*r*.25;
     path.push([p[0]+x*Math.cos(tilt)-y*Math.sin(tilt),p[1]+x*Math.sin(tilt)+y*Math.cos(tilt)+z]);
    }
    line(path,strand%3?'#97dfcf':'#b8a9ee',.14*born,.5);
   }
   for(let i=0;i<125;i++){
    const a=random(i+cell.id*211)*TAU+phase*.09,rr=Math.sqrt(random(i+1200))*r,warp=1+.16*Math.sin(a*3+phase*.3);
    const x=p[0]+Math.cos(a)*rr*warp,y=p[1]+Math.sin(a)*rr*.72;
    ctx.globalAlpha=(.15+random(i+1300)*.5)*born;ctx.fillStyle=i%9?'#b7e8d7':'#e8f7dc';ctx.fillRect(x,y,i%7?.7:1.1,.7);
   }
  });
  ctx.globalAlpha=1;ctx.globalCompositeOperation='source-over';
  if(!paused&&visible&&document.visibilityState==='visible')frame=requestAnimationFrame(draw);
 }
 function start(){if(frame===null&&visible&&document.visibilityState==='visible'){lastTime=0;frame=requestAnimationFrame(draw)}}
 function stop(){if(frame!==null)cancelAnimationFrame(frame);frame=null;lastTime=0}
 function resize(){const r=scene.getBoundingClientRect();if(!r.width||!r.height)return;w=r.width;h=r.height;const dpr=Math.min(devicePixelRatio||1,2);canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);ctx?.setTransform(dpr,0,0,dpr,0,0);start()}
 document.querySelectorAll('[data-stage]').forEach((b,i)=>{b.onclick=()=>selectStage(i);b.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const n=e.key==='Home'?0:e.key==='End'?3:(i+(e.key==='ArrowLeft'?3:1))%4;selectStage(n);document.querySelector('[data-stage="'+n+'"]').focus()}});
 $('#stage-action').onclick=()=>{if(stage===0){showPage('data',true);history.replaceState(null,'','/ask#data')}if(stage===1){$('#q').focus();$('#q').scrollIntoView({block:'center',behavior:paused?'instant':'smooth'})}if(stage===2){showPage('saved',true);history.replaceState(null,'','/ask#saved')}if(stage===3)location.href='/action-center'};
 const motion=$('#motion-toggle');function motionLabel(){motion.textContent=paused?'▷':'Ⅱ';motion.setAttribute('aria-label',paused?'Play visual animation':'Pause visual animation');motion.setAttribute('aria-pressed',String(paused))}
 motion.onclick=()=>{paused=!paused;motionLabel();if(paused)stop();else start()};motionLabel();
 motionPreference.addEventListener('change',e=>{paused=e.matches;if(paused){selectStage(3,false);cells.forEach(c=>c.born=age-15);connections.forEach(c=>c.born=age-12)}motionLabel();start()});
 scene.addEventListener('pointermove',e=>{const r=scene.getBoundingClientRect();px=(e.clientX-r.left)/r.width-.5;py=(e.clientY-r.top)/r.height-.5});scene.addEventListener('pointerleave',()=>{px=py=0});
 if(ctx){new ResizeObserver(resize).observe(scene);new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;if(visible)start();else stop()}).observe(scene);document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')start();else stop()});resize()}
 selectStage(paused?3:0,false);
 const drawer=$('#conversation-drawer'),historyButton=$('#history-button');function close(){drawer.hidden=true;historyButton.setAttribute('aria-expanded','false')}
 historyButton.onclick=()=>{drawer.hidden=!drawer.hidden;historyButton.setAttribute('aria-expanded',String(!drawer.hidden));if(!drawer.hidden)$('#close-history').focus()};$('#close-history').onclick=()=>{close();historyButton.focus()};document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!drawer.hidden){close();historyButton.focus()}});document.addEventListener('click',e=>{if(!drawer.hidden&&!drawer.contains(e.target)&&!historyButton.contains(e.target))close()});
 new MutationObserver(()=>$('#turn-count').textContent=String(state.turns.length)).observe($('#thread'),{childList:true});$('#turn-count').textContent=String(state.turns.length);
 loadSources().then(()=>{if(!sourceAtlas)return;$('#live-sources').textContent=fmt(sourceAtlas.source_categories,0);$('#live-records').textContent=new Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:1}).format(sourceAtlas.records);$('#live-records').title=fmt(sourceAtlas.records,0)+' received records';$('#live-patients').textContent=fmt(sourceAtlas.patients,0)});
}
