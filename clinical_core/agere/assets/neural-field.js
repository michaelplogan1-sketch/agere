/* Original procedural neuron sculpture. No reference image is embedded.
 * Three.js 0.180.0; bundle with tools/build_neural_field.sh.
 * Geometry is illustrative. Only explicit activity deltas represent warehouse events.
 */
import * as T from 'three';
import {mergeGeometries} from 'three/addons/utils/BufferGeometryUtils.js';
const clamp=x=>Math.max(0,Math.min(1,x));
const smooth=x=>{x=clamp(x);return x*x*(3-2*x)};
const rand=n=>{const x=Math.sin(n*127.1+311.7)*43758.5453;return x-Math.floor(x)};
const V=(x=0,y=0,z=0)=>new T.Vector3(x,y,z);

export function activityChanges(previous,current){
 if(!previous||!current||previous.source_id!==current.source_id)return {baseline:true,sourceChanged:!!previous};
 const delta=key=>Number.isFinite(current[key])&&Number.isFinite(previous[key])?Math.max(0,current[key]-previous[key]):0;
 return {baseline:false,answers:delta('answered'),examples:delta('reusable_examples'),corrections:delta('corrections'),records:delta('records')};
}

export function createNeuralField(canvas){
 let renderer;
 try{renderer=new T.WebGLRenderer({canvas,alpha:true,antialias:true,powerPreference:'low-power'})}
 catch(_){canvas.dataset.renderer='unavailable';return null}
 renderer.setClearColor(0x080c15,0);renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.6));
 renderer.outputColorSpace=T.SRGBColorSpace;renderer.toneMapping=T.ACESFilmicToneMapping;renderer.toneMappingExposure=1.25;
 canvas.dataset.renderer='webgl';
 const scene=new T.Scene(),camera=new T.PerspectiveCamera(34,1,.1,40),world=new T.Group();scene.add(world);
 camera.position.set(0,0,9);camera.lookAt(0,0,0);
 scene.add(new T.AmbientLight(0x6d6da7,1.5));
 const key=new T.DirectionalLight(0xa9d6ff,4.1);key.position.set(-3,5,5);scene.add(key);
 const rim=new T.DirectionalLight(0x9870ff,3.2);rim.position.set(4,-1,-3);scene.add(rim);
 const fill=new T.DirectionalLight(0x50cfc8,1.5);fill.position.set(1,-3,3);scene.add(fill);
 const material=(color,extra={})=>new T.MeshPhongMaterial({color,shininess:80,specular:0xc6cfff,...extra});
 const dendrite=material(0x687eab,{emissive:0x141627});
 const nucleusMat=material(0x9660c5,{emissive:0x35164e,shininess:55});
 const terminalMat=material(0x86bbca,{emissive:0x153a43});
 const signalMat=new T.MeshBasicMaterial({color:0xc7ffde,transparent:true,opacity:.95});
 const sphere=new T.SphereGeometry(1,16,12),signalGeo=new T.SphereGeometry(.024,8,6);
 let age=0,reached=0,serial=0,nextSprout=3,activityCount=0,energy=0,lastReadout=-1;
 let seeded=false,px=0,py=0,rx=0,ry=0;
 const cells=[],links=[];
 // Tubes taper in three dimensions; the same geometry is used for arbors and myelin.
 function tube(path,r0,r1,steps=18,sides=8,bulge=0){
  const geometry=new T.TubeGeometry(path,steps,1,sides,false),p=geometry.attributes.position;
  const centers=path.getSpacedPoints(steps);
  for(let i=0;i<=steps;i++){
   const t=i/steps,r=(r0+(r1-r0)*t)+bulge*Math.pow(Math.sin(Math.PI*t),.45),c=centers[i];
   for(let j=0;j<=sides;j++){const k=i*(sides+1)+j;p.setXYZ(k,c.x+(p.getX(k)-c.x)*r,c.y+(p.getY(k)-c.y)*r,c.z+(p.getZ(k)-c.z)*r)}
  }
  geometry.computeVertexNormals();return geometry;
 }
 function curve(points){return new T.CatmullRomCurve3(points)}
 function growable(geo,start,duration){
  const n=geo.attributes.position.count,at=new Float32Array(n),uv=geo.attributes.uv;
  for(let i=0;i<n;i++)at[i]=start+(uv?uv.getX(i):0)*duration;
  geo.setAttribute('birth',new T.BufferAttribute(at,1));return geo;
 }
 const growMat=dendrite.clone(),clock={value:0};
 growMat.onBeforeCompile=shader=>{
  shader.uniforms.fieldAge=clock;
  shader.vertexShader='attribute float birth; varying float vBirth;\n'+shader.vertexShader;
  shader.vertexShader=shader.vertexShader.replace('#include <begin_vertex>','#include <begin_vertex>\nvBirth = birth;');
  shader.fragmentShader='uniform float fieldAge; varying float vBirth;\n'+shader.fragmentShader;
  shader.fragmentShader=shader.fragmentShader.replace('#include <clipping_planes_fragment>','#include <clipping_planes_fragment>\nif(vBirth > fieldAge) discard;');
 };
 function arbor(group,start,direction,length,radius,depth,seed,delay,parts,tips){
  const end=start.clone().addScaledVector(direction,length),side=V(-direction.y,direction.x,0).multiplyScalar((rand(seed)-.5)*length*.5);
  const path=curve([start,start.clone().lerp(end,.35).add(side),start.clone().lerp(end,.72).sub(side.clone().multiplyScalar(.35)),end]);
  parts.push(growable(tube(path,radius,radius*(depth===3?.22:.37),depth===0?24:12),delay,1.3));
  if(depth===3){tips.push({p:end,birth:delay+1.3});return}
  for(let fork=0;fork<2;fork++){
   const angle=(fork?1:-1)*(.32+rand(seed+fork+15)*.4),d=direction.clone().applyAxisAngle(V(0,0,1),angle);
   d.z+=(rand(seed+fork+28)-.5)*.65;d.normalize();
   arbor(group,end,d,length*(.50+rand(seed+fork+38)*.21),radius*.38,depth+1,seed*3+fork+1,delay+1.0,parts,tips);
  }
 }
 function cell(position,size,angle,unlock,id){
  const group=new T.Group();group.position.copy(position);group.scale.setScalar(size);group.rotation.set(.12*(id%2?1:-1),id*.31,angle);world.add(group);
  const arms=[.68,1.92,2.75,3.65,4.7];
  const bodyGeo=new T.SphereGeometry(.33,48,32),pos=bodyGeo.attributes.position;
  for(let i=0;i<pos.count;i++){
   const p=V().fromBufferAttribute(pos,i),d=p.clone().normalize();let r=1+.05*Math.sin(d.x*17+d.y*12)*Math.sin(d.z*14);
   for(const a of arms)r+=.18*Math.pow(Math.max(0,d.dot(V(Math.cos(a),Math.sin(a),0))),16);
   p.multiplyScalar(r);p.z*=.88;pos.setXYZ(i,p.x,p.y,p.z);
  }bodyGeo.computeVertexNormals();
  const body=new T.Mesh(bodyGeo,material(0x7789bd,{transparent:true,opacity:.42,depthWrite:false,side:T.DoubleSide,shininess:100}));group.add(body);
  const ng=new T.SphereGeometry(.185,40,28),np=ng.attributes.position;
  for(let i=0;i<np.count;i++){const p=V().fromBufferAttribute(np,i),n=1+.05*Math.sin(p.x*93)*Math.cos(p.y*86)*Math.sin(p.z*78);p.multiplyScalar(n);np.setXYZ(i,p.x,p.y,p.z)}ng.computeVertexNormals();
  const nucleus=new T.Mesh(ng,nucleusMat);nucleus.position.z=.025;group.add(nucleus);
  const granules=new T.InstancedMesh(sphere,material(0x9e75c0,{shininess:35}),58),dummy=new T.Object3D();
  for(let i=0;i<58;i++){const a=rand(i+id*91)*Math.PI*2,z=rand(i+61)*2-1,r=Math.sqrt(1-z*z);dummy.position.set(Math.cos(a)*r*.181,Math.sin(a)*r*.181,z*.181+.025);dummy.scale.setScalar(.010+rand(i+90)*.012);dummy.updateMatrix();granules.setMatrixAt(i,dummy.matrix)}group.add(granules);
  const parts=[],tips=[];
  arms.forEach((a,j)=>{const d=V(Math.cos(a),Math.sin(a),(rand(j+id*13)-.5)*.25).normalize();arbor(group,d.clone().multiplyScalar(.22),d,.38+rand(j+id*7)*.25,.105,0,id*47+j+1,j*.18,parts,tips)});
  const branchGeo=mergeGeometries(parts),branches=new T.Mesh(branchGeo,growMat);parts.forEach(g=>g.dispose());group.add(branches);
  const axon=curve([V(.20,-.02,0),V(.55,-.1,-.04),V(.99,.06,.06),V(1.47,.21,.12)]);
  const axonPieces=[growable(tube(axon,.045,.021,48),.8,3.5)];
  for(let k=0;k<4;k++){
   const lo=.16+k*.20,hi=lo+.168,points=Array.from({length:9},(_,j)=>axon.getPointAt(lo+(hi-lo)*j/8));
   axonPieces.push(growable(tube(curve(points),.029,.029,16,12,.030),1.6+k*.39,.38));
  }
  const axonGeo=mergeGeometries(axonPieces),axonMesh=new T.Mesh(axonGeo,growMat);axonPieces.forEach(g=>g.dispose());group.add(axonMesh);
  const terminals=[];
  arbor(group,axon.getPoint(1),V(.9,.4,.05).normalize(),.23,.018,1,id*83+9,4,terminals,tips);
  const terminalGeo=mergeGeometries(terminals);group.add(new T.Mesh(terminalGeo,growMat));terminals.forEach(g=>g.dispose());
  const bulbs=new T.InstancedMesh(sphere,terminalMat,tips.length);
  tips.forEach((t,i)=>{dummy.position.copy(t.p);dummy.scale.setScalar(.014);dummy.updateMatrix();bulbs.setMatrixAt(i,dummy.matrix)});group.add(bulbs);
  const c={group,body,nucleus,bulbs,position,size,unlock,id,born:null,axon,branches,tips,growing:[branchGeo,axonGeo,terminalGeo]};cells.push(c);group.visible=false;return c;
 }
 cell(V(-.78,-.02,.50),1.10,.07,0,0);
 cell(V(1.38,.82,-.20),.63,1.85,1,1);
 cell(V(-1.56,1.08,-.70),.52,-.8,0,2);
 cell(V(.74,-1.20,-.25),.66,-1.4,2,3);
 cell(V(-1.65,-1.18,-1.05),.44,.6,3,4);
 cell(V(1.84,-.64,-1.35),.40,2.4,3,5);
 // A quiet dust field supplies depth without competing with the neuron silhouettes.
 const dustGeo=new T.BufferGeometry(),dust=new Float32Array(360*3);
 for(let i=0;i<360;i++){dust[i*3]=(rand(i+321)-.5)*7;dust[i*3+1]=(rand(i+521)-.5)*4.2;dust[i*3+2]=(rand(i+721)-.5)*5}
 dustGeo.setAttribute('position',new T.BufferAttribute(dust,3));
 const particles=new T.Points(dustGeo,new T.PointsMaterial({color:0x86a8d4,size:.011,transparent:true,opacity:.38,depthWrite:false}));world.add(particles);
 function worldPoint(c,p){return p.clone().multiplyScalar(c.size).applyEuler(c.group.rotation).add(c.position)}
 function connect(kind='ambient',instant=false){
  const active=cells.filter(c=>c.born!==null);if(active.length<2)return;
  const id=serial++,from=active[id%active.length],to=active[(id+1+Math.floor(rand(id+17)*(active.length-1)))%active.length];
  const a=worldPoint(from,from.axon.getPoint(1)),tip=to.tips[Math.floor(rand(id+33)*to.tips.length)],b=worldPoint(to,tip.p);
  const mid=a.clone().lerp(b,.5);mid.z+=.2+rand(id+93)*.4;mid.y+=(rand(id+73)-.5)*.9;
  const path=curve([a,a.clone().lerp(mid,.55),mid,mid.clone().lerp(b,.6),b]);
  const geo=tube(path,.008,.003,64,5),mat=material(kind==='example'?0xc2a0ff:0x6fa5c9,{transparent:true,opacity:.48,emissive:kind==='example'?0x4a2468:0x16384c});
  const mesh=new T.Mesh(geo,mat),pulse=new T.Mesh(signalGeo,signalMat);world.add(mesh,pulse);
  links.push({mesh,pulse,path,born:instant?age-9:age,id,kind,life:kind==='ambient'?35:100});
  if(links.length>30)removeLink(0);
 }
 function removeLink(i){const [l]=links.splice(i,1);world.remove(l.mesh,l.pulse);l.mesh.geometry.dispose();l.mesh.material.dispose()}
 function stage(n,instant=false){reached=Math.max(reached,n);cells.forEach(c=>{if(c.born===null&&c.unlock<=reached){c.born=instant?age-9:age;c.growing.forEach(g=>{const a=g.attributes.birth;for(let i=0;i<a.count;i++)a.setX(i,a.getX(i)+c.born);a.needsUpdate=true});c.group.visible=true;connect('stage',instant)}})}
 function ingest(sample,previous){
  const d=activityChanges(previous,sample);canvas.dataset.answered=String(sample.answered??'');canvas.dataset.reusableExamples=String(sample.reusable_examples??'');canvas.dataset.sourceId=sample.source_id;
  if(!seeded){seeded=true;const memory=Math.min(7,Math.floor(Math.log2(1+(sample.answered||0))*.5));for(let i=0;i<memory;i++)connect('memory',true)}
  if(d.baseline){if(d.sourceChanged){energy=1;if(cells.length<9){const id=cells.length;cell(V(-2.05+(id-6)*1.5,.2+(id%2)*.75,-.9),.35,.4+id,0,id);stage(reached)}connect('source')}return d}
  const count=Math.min(6,(d.answers||0)+(d.examples||0)+(d.records?2:0));
  for(let i=0;i<count;i++)connect(d.examples?'example':d.records?'source':'answer');
  if(count||d.corrections)energy=1;
  activityCount+=count;canvas.dataset.activityGrowth=String(activityCount);return d;
 }
 function resize(w,h){renderer.setSize(w,h,false);camera.aspect=w/h;camera.position.z=w/h<1.3?10.4:7.5;camera.updateProjectionMatrix()}
 function render(dt=0){
  age+=Math.min(dt,.05);clock.value=age;energy=Math.max(0,energy-dt*.12);if(dt){rx+=(px-rx)*.04;ry+=(py-ry)*.04;}
  // Continuous orbit: foreground and background cells exchange depth over time.
  world.rotation.y=Math.sin(age*.075)*.48+rx*.20;world.rotation.x=Math.sin(age*.10)*.13+ry*.18;world.rotation.z=Math.sin(age*.06)*.045;
  world.position.y=Math.sin(age*.35)*.035;particles.rotation.y=age*.016;
  cells.forEach(c=>{if(c.born===null)return;const growth=smooth((age-c.born+.2)/2);c.group.scale.setScalar(c.size*growth*(1+Math.sin(age*1.1+c.id)*.013));c.body.material.opacity=.42+Math.sin(age*.8+c.id)*.04+energy*.06;c.bulbs.visible=age-c.born>4.5});
  if(dt&&age>nextSprout){nextSprout=age+4;connect()}
  for(let i=links.length-1;i>=0;i--){const l=links[i],t=age-l.born;if(t>l.life+5){removeLink(i);continue}
   const growth=smooth(t/5),fade=1-smooth((t-l.life)/5);l.mesh.geometry.setDrawRange(0,Math.floor(growth*64)*5*6);l.mesh.material.opacity=.48*fade;
   const travel=(age*(.18+energy*.17)+l.id*.17)%1;l.pulse.visible=travel<growth;l.pulse.position.copy(l.path.getPointAt(Math.min(travel,growth)));l.pulse.scale.setScalar(l.kind==='ambient'?.7:1.25);
  }
  if(Math.floor(age)!==lastReadout){lastReadout=Math.floor(age);canvas.dataset.conceptAge=String(lastReadout);canvas.dataset.connectionsCreated=String(serial);canvas.dataset.activeConnections=String(links.length);canvas.dataset.reachedStage=String(reached);canvas.dataset.rotation=world.rotation.y.toFixed(3)}
  renderer.render(scene,camera);
 }
 function still(){age=Math.max(age,10);cells.forEach(c=>{if(c.born!==null)c.born=Math.min(c.born,age-9)});links.forEach(l=>l.born=Math.min(l.born,age-9));render(0)}
 stage(0);return {stage,ingest,resize,render,still,pointer:(x,y)=>{px=x;py=y}};
}
