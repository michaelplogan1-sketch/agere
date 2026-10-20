/* Original computational field: noise → relationships → insight → action.
 * TextQL's fine-point spatial language informed the direction; no site assets or code are copied.
 * The interface name is retained for the existing scene controller and activity checks.
 * Three.js 0.180.0, locally bundled with tools/build_neural_field.sh.
 */
import * as T from 'three';
const rand=n=>{const x=Math.sin(n*127.1+311.7)*43758.5453;return x-Math.floor(x)};
const clamp=x=>Math.max(0,Math.min(1,x));
const V=(x=0,y=0,z=0)=>new T.Vector3(x,y,z);
export function activityChanges(previous,current){
 if(!previous||!current||previous.source_id!==current.source_id)return {baseline:true,sourceChanged:!!previous};
 const delta=key=>Number.isFinite(current[key])&&Number.isFinite(previous[key])?Math.max(0,current[key]-previous[key]):0;
 return {baseline:false,answers:delta('answered'),examples:delta('reusable_examples'),corrections:delta('corrections'),records:delta('records')};
}
export function createNeuralField(canvas){
 let renderer;try{renderer=new T.WebGLRenderer({canvas,alpha:true,antialias:true,powerPreference:'low-power'})}catch(_){canvas.dataset.renderer='unavailable';return null}
 const dpr=Math.min(devicePixelRatio||1,1.6);renderer.setPixelRatio(dpr);renderer.setClearColor(0x0c1010,0);canvas.dataset.renderer='webgl';
 const scene=new T.Scene(),world=new T.Group(),camera=new T.PerspectiveCamera(33,1,.1,30);scene.add(world);camera.position.set(0,2.0,8.1);camera.lookAt(.15,0,0);
 let age=0,reached=0,width=1,height=1,rx=0,ry=0,px=0,py=0,energy=0,serial=0,nextGrowth=1,activityCount=0,lastReadout=-1;
 const uniform={time:{value:0},reach:{value:0},energy:{value:0},pixelRatio:{value:dpr}};
 const attributes={position:[],scatter:[],size:[],seed:[],level:[],accent:[]};
 const sources=Array.from({length:9},(_,i)=>V(-2.25+(i%5)*.68,-1.25-rand(i+51)*.55,(Math.floor(i/5)-.5)*1.1+(rand(i+81)-.5)*.3));
 const anchors=Array.from({length:18},(_,i)=>V(-1.95+(i%6)*.66,-.35+Math.floor(i/6)*.32,(rand(i+101)-.5)*1.4));
 const canopy=(u,v)=>V(u*2.02,.70+.27*Math.sin(u*2.8+v*1.9)+v*.16,v*1.18+.22*Math.cos(u*3.7));
 const outputs=[V(2.48,1.18,.15),V(2.65,.22,.42),V(2.33,-.76,.65)];
 function point(p,size,level,accent=0,spread=1){const id=attributes.size.length;attributes.position.push(...p.toArray());attributes.scatter.push((rand(id+91)-.5)*6.5,-1.4+(rand(id+291)-.5)*2.1,(rand(id+491)-.5)*3.4);attributes.size.push(size);attributes.seed.push(rand(id+991));attributes.level.push(level);attributes.accent.push(accent)}
 // Received records retain some disorder. Most are small; occasional larger squares anchor the eye.
 sources.forEach((p,i)=>{for(let j=0;j<60;j++){const id=i*71+j;point(p.clone().add(V((rand(id+31)-.5)*.60,(rand(id+61)-.5)*.48,(rand(id+91)-.5)*.60)),j%14?1.0:3.2,0)}});
 // Fine regular fields emerge from the noisy records, bending like a living surface.
 for(let i=0;i<75;i++)for(let j=0;j<43;j++){const u=i/74*2-1,v=j/42*2-1;if(rand(i*71+j)<.10)continue;point(canopy(u,v),.9+rand(i*79+j)*.9,(i+j)%5===0?1:2,0)}
 anchors.forEach((p,i)=>{point(p,3.4,1);for(let j=0;j<11;j++)point(p.clone().add(V((rand(i*21+j)-.5)*.16,(rand(i*31+j)-.5)*.16,(rand(i*41+j)-.5)*.16)),1.1,1)});
 // Outputs resolve into three small spatial panels; their destinations are real workspace links.
 outputs.forEach((p,k)=>{for(let i=0;i<14;i++)for(let j=0;j<9;j++){const border=i===0||j===0||i===13||j===8;point(p.clone().add(V((i/13-.5)*.46,(j/8-.5)*.31,Math.sin(i*.3)*.025)),border?1.65:1,3,border?.9:.25)}});
 const geometry=new T.BufferGeometry();for(const [key,values] of Object.entries(attributes))geometry.setAttribute(key,new T.Float32BufferAttribute(values,['position','scatter'].includes(key)?3:1));
 const points=new T.Points(geometry,new T.ShaderMaterial({uniforms:uniform,transparent:true,depthWrite:false,blending:T.AdditiveBlending,
  vertexShader:`attribute vec3 scatter; attribute float size,seed,level,accent; uniform float time,reach,energy,pixelRatio; varying float opacity,tint;
   void main(){float allowed=1.-step(reach+.01,level);float formation=smoothstep(level*2.6+seed*2.,level*2.6+seed*2.+3.2,time);
    vec3 p=mix(scatter,position,formation);float wave=sin(p.x*2.+time*.65+seed)*.033+cos(p.z*2.4+time*.4)*.026;
    p.y+=wave*(.4+formation);p.x+=sin(time*.16+seed*6.28)*.018;
    vec4 mv=modelViewMatrix*vec4(p,1.);gl_Position=projectionMatrix*mv;
    gl_PointSize=size*pixelRatio*clamp(7.2/-mv.z,.65,1.8)*(1.+energy*.22);
    opacity=allowed*(.13+seed*.53)*smoothstep(0.,2.5,time)*(level>2.5?.95:1.);tint=accent; }`,
  fragmentShader:`varying float opacity,tint;void main(){vec3 color=mix(vec3(.66,.74,.73),vec3(.78,.94,.50),tint);gl_FragColor=vec4(color,opacity);}`
 }));world.add(points);
 // Bundled thin filaments connect sources to shared meaning, then to useful outputs.
 const linePositions=[],lineT=[],lineSeed=[],lineLevel=[];
 function curve(a,b,id,lift=.2){const mid=a.clone().lerp(b,.45);mid.x+=(rand(id+81)-.5)*.48;mid.z+=(rand(id+91)-.5)*.55;mid.y+=lift;return new T.CatmullRomCurve3([a,a.clone().lerp(mid,.55),mid,mid.clone().lerp(b,.55),b])}
 function line(path,level,id){const pts=path.getPoints(28);for(let i=0;i<28;i++)for(let j=0;j<2;j++){linePositions.push(...pts[i+j].toArray());lineT.push((i+j)/28);lineSeed.push(rand(id+41));lineLevel.push(level)}}
 for(let i=0;i<145;i++){const a=sources[i%sources.length].clone().add(V((rand(i+19)-.5)*.16,0,(rand(i+29)-.5)*.16)),b=canopy(rand(i+31)*2-1,rand(i+51)*2-1);line(curve(a,b,i,.05),i%4?1:2,i)}
 for(let i=0;i<45;i++)line(curve(anchors[i%anchors.length],anchors[(i*7+3)%anchors.length],i+200,.06),1,i+200);
 for(let i=0;i<52;i++)line(curve(canopy(rand(i+411)*2-1,rand(i+511)*2-1),outputs[i%3],i+500,.15),3,i+500);
 const lineGeometry=new T.BufferGeometry();lineGeometry.setAttribute('position',new T.Float32BufferAttribute(linePositions,3));lineGeometry.setAttribute('along',new T.Float32BufferAttribute(lineT,1));lineGeometry.setAttribute('seed',new T.Float32BufferAttribute(lineSeed,1));lineGeometry.setAttribute('level',new T.Float32BufferAttribute(lineLevel,1));
 world.add(new T.LineSegments(lineGeometry,new T.ShaderMaterial({uniforms:uniform,transparent:true,depthWrite:false,blending:T.AdditiveBlending,
 vertexShader:`attribute float along,seed,level; varying float vAlong,vSeed,vLevel;uniform float time;void main(){vAlong=along;vSeed=seed;vLevel=level;vec3 p=position;p.y+=sin(p.x*2.+time*.65+seed)*.012;gl_Position=projectionMatrix*modelViewMatrix*vec4(p,1.);}`,
 fragmentShader:`uniform float time,reach,energy;varying float vAlong,vSeed,vLevel;void main(){float allowed=1.-step(reach+.01,vLevel);float grow=clamp((time-vLevel*2.6-vSeed*2.)/3.5,0.,1.);float reveal=1.-smoothstep(grow-.035,grow,vAlong);float pulse=pow(max(0.,1.-abs(vAlong-fract(time*.085+vSeed))/.075),3.);float opacity=(.035+vSeed*.028+pulse*.15+energy*.035)*allowed*reveal;vec3 color=mix(vec3(.65,.73,.72),vec3(.75,.90,.52),step(2.5,vLevel)*.30);gl_FragColor=vec4(color,opacity);}`
 })));
 // A few larger packets visibly travel from source to insight to action.
 const routes=Array.from({length:60},(_,i)=>{const a=i<36?sources[i%9]:canopy(rand(i+210)*2-1,rand(i+310)*2-1),b=i<36?canopy(rand(i+410)*2-1,rand(i+510)*2-1):outputs[i%3];return curve(a,b,i+800,.12)});
 const packetPositions=new Float32Array(routes.length*3),packetGeometry=new T.BufferGeometry();packetGeometry.setAttribute('position',new T.BufferAttribute(packetPositions,3));
 const packets=new T.Points(packetGeometry,new T.PointsMaterial({color:0xc2d5c5,size:.025,transparent:true,opacity:.8,depthWrite:false}));world.add(packets);
 const labelsRoot=document.createElement('div');labelsRoot.className='field-labels';canvas.parentElement.append(labelsRoot);const labels=[];
 function label(text,p,level,action=false){const el=document.createElement(action?'a':'span');el.className=action?'field-map-label field-output':'field-map-label';el.textContent=text;if(action){el.href='/action-center';el.title='Open the synthetic action workspace and choose an analysis'}labelsRoot.append(el);labels.push({el,p,level})}
 label('CLAIMS',sources[0].clone().add(V(-.1,-.12,0)),0);label('CLINICAL NOTES',sources[3].clone().add(V(0,-.1,0)),0);label('OPERATIONS',sources[7].clone().add(V(0,-.16,0)),0);
 label('RELATIONSHIPS',V(-1.4,-.24,.6),1);label('EVIDENCE',V(.34,.33,1.05),2);label('SHARED MEANING',V(-.44,.98,-.3),2);
 label('BUILD A VIEW ↗',outputs[0].clone().add(V(.28,.03,0)),3,true);label('SIMULATE WORK ↗',outputs[1].clone().add(V(.28,.03,0)),3,true);label('MONITOR ↗',outputs[2].clone().add(V(.28,.03,0)),3,true);
 const sprouts=[];
 function connect(kind='ambient',instant=false){
  const id=serial++,a=anchors[id%anchors.length],b=kind==='source'?canopy(rand(id+772)*2-1,rand(id+872)*2-1):outputs[id%3];
  const path=curve(a,b,id+1000,.2+(rand(id+20)-.5)*.4),geo=new T.BufferGeometry().setFromPoints(path.getPoints(60));
  const mat=new T.LineBasicMaterial({color:kind==='example'?0xd7d5ee:0xb6ce86,transparent:true,opacity:0,depthWrite:false});const mesh=new T.Line(geo,mat);world.add(mesh);sprouts.push({mesh,born:instant?age-5:age,kind});if(sprouts.length>14)removeSprout(0);
 }
 function removeSprout(i){const [s]=sprouts.splice(i,1);world.remove(s.mesh);s.mesh.geometry.dispose();s.mesh.material.dispose()}
 function stage(n,instant=false){reached=Math.max(reached,n);uniform.reach.value=reached;if(instant)age=Math.max(age,14);canvas.dataset.reachedStage=String(reached)}
 function ingest(sample,previous){const d=activityChanges(previous,sample);canvas.dataset.answered=String(sample.answered??'');canvas.dataset.reusableExamples=String(sample.reusable_examples??'');canvas.dataset.sourceId=sample.source_id;
  if(d.baseline){if(d.sourceChanged){energy=1;connect('source')}return d}
  const count=Math.min(6,(d.answers||0)+(d.examples||0)+(d.records?2:0));for(let i=0;i<count;i++)connect(d.examples?'example':d.records?'source':'answer');if(count||d.corrections)energy=1;activityCount+=count;canvas.dataset.activityGrowth=String(activityCount);return d;
 }
 function resize(w,h){width=w;height=h;renderer.setSize(w,h,false);camera.aspect=w/h;camera.position.z=w/h<1.3?10.9:8.1;camera.lookAt(.15,0,0);camera.updateProjectionMatrix()}
 function render(dt=0){age+=Math.min(dt,.05);energy=Math.max(0,energy-dt*.12);uniform.time.value=age;uniform.energy.value=energy;if(dt){rx+=(px-rx)*.035;ry+=(py-ry)*.035}
  world.rotation.y=-.20+Math.sin(age*.065)*.28+rx*.18;world.rotation.x=.12+Math.sin(age*.10)*.04+ry*.10;world.rotation.z=Math.sin(age*.045)*.022;
  if(dt&&age>nextGrowth){nextGrowth=age+2.6;if(reached>1)connect()}
  routes.forEach((path,i)=>{const t=(age*(.035+rand(i+20)*.028)+rand(i+40))%1,p=path.getPointAt(t);if(age<3||(i>=36&&reached<3)||(i<36&&reached<1))p.z=99;packetPositions.set(p.toArray(),i*3)});packetGeometry.attributes.position.needsUpdate=true;
  for(let i=sprouts.length-1;i>=0;i--){const s=sprouts[i],t=age-s.born;if(t>39){removeSprout(i);continue}s.mesh.geometry.setDrawRange(0,Math.floor(clamp(t/4.5)*61));s.mesh.material.opacity=(s.kind==='ambient'?.16:.55)*Math.min(1,t)*clamp((39-t)/5)}
  world.updateMatrixWorld();labels.forEach(l=>{const p=world.localToWorld(l.p.clone()).project(camera),x=(p.x*.5+.5)*width,y=(-p.y*.5+.5)*height;const visible=l.level<=reached&&age>l.level*2.6+2&&x>5&&x<width-15&&y>35&&y<height-25;l.el.style.opacity=visible?'1':'0';l.el.style.visibility=visible?'visible':'hidden';l.el.style.transform=`translate(${x}px,${y}px)`});
  if(Math.floor(age)!==lastReadout){lastReadout=Math.floor(age);Object.assign(canvas.dataset,{conceptAge:String(lastReadout),connectionsCreated:String(serial),activeConnections:String(sprouts.length),rotation:world.rotation.y.toFixed(3),reachedStage:String(reached)})}
  renderer.render(scene,camera);
 }
 function still(){age=Math.max(age,14);sprouts.forEach(s=>s.born=Math.min(s.born,age-5));render(0)}
 return {stage,ingest,resize,render,still,pointer:(x,y)=>{px=x;py=y}};
}
