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
 let age=paused?35:0,phase=0,px=0,py=0,rx=0,ry=0,reached=0,manualStage=false,nextGrowth=2,serial=0,lastReadout=-1;
 const TAU=Math.PI*2,clamp=x=>Math.max(0,Math.min(1,x)),mix=(a,b,t)=>a+(b-a)*t;
 const smooth=x=>{x=clamp(x);return x*x*(3-2*x)};
 const random=n=>{const x=Math.sin(n*127.1+311.7)*43758.5453;return x-Math.floor(x)};
 const branches=[],connections=[];
 // Original procedural morphology informed by fluorescence images and neuron anatomy.
 // This accelerated, bounded visual is not a biological simulation or live model telemetry.
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
  const points=curve(start,end,(random(seed+3)-.5)*length*.45,16);
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
   if(!manualStage&&stage<3&&age>7*(stage+1))selectStage(stage+1,false,false);
   if(age>=nextGrowth){
    nextGrowth=age+2.6;
    const active=cells.filter(c=>c.born!==null&&age-c.born>2);
    if(active.length>1){const from=active[serial%active.length],others=active.filter(c=>c!==from);connect(from,others[Math.floor(random(serial+1000)*others.length)])}
   }
   for(let i=connections.length-1;i>=0;i--){const c=connections[i];if(!c.permanent&&age-c.born>c.life+5)connections.splice(i,1)}
  }
  // These describe only this illustrated network and make continuity inspectable in the DOM.
  if(Math.floor(age)!==lastReadout){lastReadout=Math.floor(age);canvas.dataset.conceptAge=String(lastReadout);canvas.dataset.connectionsCreated=String(serial);canvas.dataset.activeConnections=String(connections.length)}
  ctx.clearRect(0,0,w,h);ctx.lineCap='round';ctx.lineJoin='round';
  const scale=Math.min(w*.245,h*.27),yaw=.12+Math.sin(phase*.12)*.10+rx*.15,pitch=ry*.1;
  const cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);
  function project(p){const x=p[0]*cy-p[2]*sy,z=p[0]*sy+p[2]*cy,y=p[1]*cp-z*sp,f=5/(5+z);return [w*.5+x*scale*f,h*.51+y*scale*f,f]}
  function line(points,color,alpha,width){if(points.length<2||alpha<.003)return;ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.strokeStyle=color;ctx.globalAlpha=alpha;ctx.lineWidth=width;ctx.stroke()}
  function glow(x,y,r,color,alpha){if(alpha<.003||r<=0)return;const g=ctx.createRadialGradient(x,y,0,x,y,r);g.addColorStop(0,color);g.addColorStop(1,color+'00');ctx.fillStyle=g;ctx.globalAlpha=alpha;ctx.fillRect(x-r,y-r,r*2,r*2)}
  function partial(points,t){const at=clamp(t)*(points.length-1),last=Math.floor(at),path=points.slice(0,last+1).map(project);if(last<points.length-1)path.push(project(points[last].map((v,k)=>mix(v,points[last+1][k],at-last))));return path}
  function tube(path,startWidth,endWidth,color,alpha){
   if(path.length<2)return;const edges=path.map((p,i)=>{const a=path[Math.max(0,i-1)],b=path[Math.min(path.length-1,i+1)],dx=b[0]-a[0],dy=b[1]-a[1],len=Math.hypot(dx,dy)||1,r=mix(startWidth,endWidth,i/(path.length-1))*.5;return [[p[0]-dy/len*r,p[1]+dx/len*r],[p[0]+dy/len*r,p[1]-dx/len*r]]});
   ctx.beginPath();edges.forEach((e,i)=>i?ctx.lineTo(...e[0]):ctx.moveTo(...e[0]));for(let i=edges.length-1;i>=0;i--)ctx.lineTo(...edges[i][1]);ctx.closePath();ctx.fillStyle=color;ctx.globalAlpha=alpha;ctx.fill();
  }
  function growthCone(path,strength=1){
   if(path.length<2)return;const p=path[path.length-1],q=path[Math.max(0,path.length-3)],angle=Math.atan2(p[1]-q[1],p[0]-q[0]);
   glow(p[0],p[1],8,'#8fc6a2',.15*strength);
   for(let i=-2;i<=2;i++){const a=angle+i*.34+.1*Math.sin(phase*2+i),length=4+2*Math.sin(i+phase);line([p,[p[0]+Math.cos(a)*length,p[1]+Math.sin(a)*length]],'#b6dca6',.65*strength,.65)}
   ctx.fillStyle='#bcdaa4';ctx.globalAlpha=.75*strength;ctx.beginPath();ctx.ellipse(p[0],p[1],2.1,1.2,angle,0,TAU);ctx.fill();
  }
  glow(w*.5,h*.49,scale*1.7,'#234b43',.12);
  for(let i=0;i<45;i++){ctx.globalAlpha=.05+random(i+20)*.09;ctx.fillStyle='#85a18e';ctx.beginPath();ctx.arc(random(i+40)*w,random(i+60)*h,.5+random(i+80),0,TAU);ctx.fill()}
  // Each cell has one thin axon trunk; new collaterals grow from it toward dendritic sites.
  for(const cell of cells){if(cell.born===null)continue;const progress=smooth((age-cell.born-.5)/2.8),path=partial(cell.axon,progress);line(path,'#385b58',.8,2.1);line(path,'#a6c6ac',.7,.85)}
  for(const c of connections){
   const t=age-c.born;if(t<=0)continue;const progress=smooth(t/c.duration),fade=c.permanent?1:1-smooth((t-c.life)/5),path=partial(c.points,progress);
   line(path,'#254b45',.65*fade,3.2);line(path,'#86bba9',.67*fade,1.1);
   if(progress<1)growthCone(path,fade);
   else{const tip=path[path.length-1];ctx.globalAlpha=.8*fade;ctx.fillStyle='#c0cf8d';ctx.beginPath();ctx.ellipse(tip[0],tip[1],2.0,1.4,c.id,0,TAU);ctx.fill()}
   const signal=(age*.23+c.id*.17)%1;
   if(progress===1&&stage>0){const at=Math.floor(signal*(path.length-1));line(path.slice(Math.max(0,at-3),at+1),stage===3?'#e1d9a0':'#bbe8cc',.85*fade,1.7)}
  }
  for(const b of branches){
   if(b.cell.born===null)continue;const progress=smooth((age-b.cell.born-b.delay)/b.duration);if(!progress)continue;
   const path=partial(b.points,progress),width=b.width*Math.min(1.25,Math.max(.8,scale/100));
   tube(path,width+.7,width*.35,'#25493a',.86);tube(path,width,width*.25,b.depth<2?'#81ae84':'#749d7c',.85);
   line(path,'#c0d5a3',b.depth<2?.34:.22,.5);
   // Small stalks and bouton-like heads replace the former clouds of star particles.
   if(b.depth>0)for(let j=3;j<path.length-1;j+=4){
    const p=path[j],q=path[j-1],a=Math.atan2(p[1]-q[1],p[0]-q[0])+(j%8?1:-1)*Math.PI*.5,len=2.2+random(b.seed+j)*2;
    const tip=[p[0]+Math.cos(a)*len,p[1]+Math.sin(a)*len];line([p,tip],'#90b88d',.64,.6);ctx.globalAlpha=.65;ctx.fillStyle='#adca98';ctx.beginPath();ctx.arc(tip[0],tip[1],.85,0,TAU);ctx.fill();
   }
   if(progress<1)growthCone(path,.65);
  }
  // Substantial asymmetric somas, a membrane, granular cytoplasm and an offset nucleus.
  // Source-over compositing preserves tissue detail instead of burning bodies into white stars.
  cells.forEach(cell=>{
   if(cell.born===null)return;const born=smooth((age-cell.born)/1.8);if(!born)return;
   const p=project(cell.p),r=Math.max(12,scale*.19)*cell.size*p[2]*born,points=[];
   glow(p[0],p[1],r*2.0,'#70946e',.10*born);
   for(let j=0;j<24;j++){const a=j/24*TAU,edge=r*(1+.13*Math.cos(a*3+cell.angle)+.08*Math.sin(a*2+cell.id));points.push([p[0]+Math.cos(a)*edge,p[1]+Math.sin(a)*edge*.84])}
   ctx.beginPath();const last=points[points.length-1];ctx.moveTo((last[0]+points[0][0])/2,(last[1]+points[0][1])/2);
   points.forEach((p,i)=>{const next=points[(i+1)%points.length];ctx.quadraticCurveTo(p[0],p[1],(p[0]+next[0])/2,(p[1]+next[1])/2)});ctx.closePath();
   const body=ctx.createRadialGradient(p[0]-r*.35,p[1]-r*.30,r*.1,p[0]+r*.12,p[1]+r*.1,r*1.2);
   body.addColorStop(0,'#9fbc86');body.addColorStop(.5,'#6f956c');body.addColorStop(1,'#294e43');ctx.globalAlpha=.97*born;ctx.fillStyle=body;ctx.fill();
   ctx.strokeStyle='#b9cfa1';ctx.globalAlpha=.65*born;ctx.lineWidth=.85;ctx.stroke();
   ctx.save();ctx.clip();
   for(let i=0;i<90;i++){const a=random(i+cell.id*131)*TAU,rr=Math.sqrt(random(i+450))*r;ctx.globalAlpha=(.08+random(i+220)*.15)*born;ctx.fillStyle=i%3?'#d5ddb0':'#243f39';ctx.beginPath();ctx.ellipse(p[0]+Math.cos(a)*rr,p[1]+Math.sin(a)*rr*.85,.6+random(i+180),.5,0,0,TAU);ctx.fill()}
   const nx=p[0]-r*.16,ny=p[1]-r*.07,nucleus=ctx.createRadialGradient(nx-r*.1,ny-r*.1,0,nx,ny,r*.5);
   nucleus.addColorStop(0,'#6d8093');nucleus.addColorStop(1,'#354c5a');ctx.globalAlpha=.95*born;ctx.fillStyle=nucleus;ctx.beginPath();ctx.ellipse(nx,ny,r*.45,r*.36,-.4+cell.angle*.1,0,TAU);ctx.fill();
   ctx.strokeStyle='#adbfaa';ctx.lineWidth=.65;ctx.globalAlpha=.45*born;ctx.stroke();
   ctx.fillStyle='#22343f';ctx.globalAlpha=.7*born;ctx.beginPath();ctx.ellipse(nx+r*.10,ny-r*.07,r*.11,r*.09,.3,0,TAU);ctx.fill();ctx.restore();
  });
  ctx.globalAlpha=1;
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
