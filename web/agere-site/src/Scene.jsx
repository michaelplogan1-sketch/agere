import React, {useMemo, useRef, useState, useEffect, Component} from 'react';
import {Canvas, useFrame, useThree} from '@react-three/fiber';
import * as THREE from 'three';
import {RoundedBoxGeometry} from 'three/addons/geometries/RoundedBoxGeometry.js';

const ORANGE = '#f46b39';
function roundedSlab(w=4.7,h=3,d=.095) {
  const r=.12, s=new THREE.Shape();
  s.moveTo(-w/2+r,-h/2); s.lineTo(w/2-r,-h/2); s.quadraticCurveTo(w/2,-h/2,w/2,-h/2+r);
  s.lineTo(w/2,h/2-r); s.quadraticCurveTo(w/2,h/2,w/2-r,h/2); s.lineTo(-w/2+r,h/2);
  s.quadraticCurveTo(-w/2,h/2,-w/2,h/2-r); s.lineTo(-w/2,-h/2+r); s.quadraticCurveTo(-w/2,-h/2,-w/2+r,-h/2);
  const g=new THREE.ExtrudeGeometry(s,{depth:d,bevelEnabled:true,bevelSegments:2,steps:1,bevelSize:.025,bevelThickness:.018});
  g.rotateX(-Math.PI/2); return g;
}
function Trace({points,color=ORANGE,opacity=1}) {
  const geometry=useMemo(()=>new THREE.BufferGeometry().setFromPoints(points.map(p=>new THREE.Vector3(...p))),[points]);
  useEffect(()=>()=>geometry.dispose(),[geometry]);
  return <line geometry={geometry}><lineBasicMaterial color={color} transparent opacity={opacity}/></line>;
}
function Card({position,rotation=[0,0,0],orange=false,scale=1}) {
  return <group position={position} rotation={rotation} scale={scale}>
    <mesh><boxGeometry args={[.72,.035,.98]}/><meshStandardMaterial color={orange?'#d95528':'#dbe1da'} roughness={.55} metalness={.12}/></mesh>
    {[0,1,2,3,4].map(i=><mesh key={i} position={[-.08,.025,-.27+i*.13]}><boxGeometry args={[i===0?.35:.45-(i%3)*.07,.01,.014]}/><meshBasicMaterial color={orange?'#ffdbbb':'#627571'}/></mesh>)}
    <mesh position={[.22,.03,-.31]}><boxGeometry args={[.09,.015,.09]}/><meshBasicMaterial color={orange?'#f2dfca':'#ef7243'}/></mesh>
  </group>;
}
function Machine({active=-1,motion=true}) {
  const ref=useRef(); const particles=useRef();
  const geom=useMemo(()=>roundedSlab(),[]);
  const edge=useMemo(()=>new THREE.EdgesGeometry(geom,25),[geom]);
  const dummy=useMemo(()=>new THREE.Object3D(),[]);
  useFrame(({clock,pointer},dt)=>{
    if(ref.current){ref.current.rotation.y=THREE.MathUtils.damp(ref.current.rotation.y,motion?-.12+pointer.x*.10:-.12,3,dt);ref.current.position.y=motion?Math.sin(clock.elapsedTime*.4)*.055:0;}
    if(particles.current){for(let i=0;i<36;i++){const t=(i/36+(motion?clock.elapsedTime*.065:0))%1;dummy.position.set(-1.9+Math.sin(t*Math.PI*5)*.2,-2.5+t*5,.95);dummy.scale.setScalar(.025+(i%3)*.008);dummy.updateMatrix();particles.current.setMatrixAt(i,dummy.matrix);}particles.current.instanceMatrix.needsUpdate=true;}
  });
  useEffect(()=>()=>{geom.dispose();edge.dispose()},[geom,edge]);
  return <group ref={ref} rotation={[0,-.12,0]}>
    {[0,1,2,3,4,5].map(i=>{
      const y=(i-2.5)*.77, lit=active<0||active===5-i;
      return <group key={i} position={[0,y,0]}>
        <mesh geometry={geom}><meshStandardMaterial color={lit?'#243735':'#182724'} roughness={.35} metalness={.6}/></mesh>
        <lineSegments geometry={edge}><lineBasicMaterial color={lit?'#91a5a0':'#4c605b'} transparent opacity={.55}/></lineSegments>
        <Trace points={[[-2.15,.13,1.15],[1.82,.13,1.15],[2.1,.13,.87],[2.1,.13,-1.1]]} opacity={lit?.95:.2}/>
        <Trace points={[[-1.9,.14,.8],[-1.4,.14,.8],[-.9,.14,.2],[.65,.14,.2],[1.1,.14,-.4],[1.9,.14,-.4]]} color={lit?'#efa575':'#50746a'} opacity={.7}/>
        {Array.from({length:15},(_,j)=><mesh key={j} position={[-1.8+(j%5)*.78,.15,-.95+Math.floor(j/5)*.5]}>
          <boxGeometry args={[.47,.035,.2]}/><meshStandardMaterial color={j===i+3?'#ff8b53':lit?'#638b7d':'#354d45'} roughness={.55} metalness={.45}/>
        </mesh>)}
        <mesh position={[-2.34,.015,.83]}><boxGeometry args={[.035,.09,.4]}/><meshBasicMaterial color={ORANGE}/></mesh>
        <mesh position={[0,-.08,1.50]}><boxGeometry args={[1.2,.025,.03]}/><meshBasicMaterial color={lit?'#ee875a':'#58746a'}/></mesh>
      </group>
    })}
    <Trace points={[[-1.9,-2.3,.95],[-1.9,2.48,.95],[-1.4,2.7,.5],[.4,2.7,.5],[.4,2.9,-.5]]} opacity={.65}/>
    <Trace points={[[1.6,-2.3,-1.0],[1.6,2.2,-1.0]]} color="#688d7f" opacity={.5}/>
    <instancedMesh ref={particles} args={[null,null,36]}><sphereGeometry args={[1,6,6]}/><meshBasicMaterial color="#ffb781"/></instancedMesh>
    <Card position={[-2.25,3.2,-.8]} rotation={[.18,-.3,-.06]} scale={.95}/>
    <Card position={[.2,3.35,-.1]} rotation={[0,.15,-.03]} orange scale={.95}/>
    <Card position={[2.2,2.9,.8]} rotation={[-.12,.55,.05]} scale={.85}/>
    <Card position={[-3.4,1.8,.25]} rotation={[.2,-.4,-.25]} scale={.7}/>
    <Card position={[3.0,-1.8,-.3]} rotation={[.1,.3,.2]} scale={.65}/>
  </group>;
}
// Architectural outline: the same 144 blocks resolve into a home.
const homePoints=(()=>{
 const a=[];const add=(x,y,z)=>a.push([x*.51,(y-3)*.47,z*.57]);
 for(const x of [-4,4])for(const z of [-2,2])for(let y=0;y<6;y++)add(x,y,z);
 for(const z of [-2,2])for(let x=-3;x<=3;x++)for(const y of [0,5])add(x,y,z);
 for(const x of [-4,4])for(let z=-1;z<=1;z++)for(const y of [0,5])add(x,y,z);
 for(const z of [-2,2])for(let x=-4;x<=4;x++)add(x,5+(.85*(4-Math.abs(x))),z);
 for(let z=-1;z<=1;z++)add(0,8.4,z);
 for(let x=-3;x<=3;x++)for(let z=-1;z<=1;z++)add(x,0,z);
 for(const x of [-1,1])for(let y=1;y<=3;y++)add(x,y,2);
 for(let x=-1;x<=1;x++)add(x,3,2);
 for(const x of [-3,3])for(const y of [2,3])add(x,y,2);
 while(a.length<144){const i=a.length;add(((i*3)%7)-3,5+(.85*(4-Math.abs(((i*3)%7)-3))),((i%3)-1));}
 return a.slice(0,144);
})();
function EvidenceField({stage,motion,light=false,progress=0}) {
  const invalidate=useThree(state=>state.invalidate);
  const ref=useRef(); const cloud=useRef(); const state=useRef([]);
  const cubeGeometry=useMemo(()=>new RoundedBoxGeometry(1,1,1,2,.045),[]);
  useEffect(()=>()=>cubeGeometry.dispose(),[cubeGeometry]);
  const dummy=useMemo(()=>new THREE.Object3D(),[]);
  const color=useMemo(()=>new THREE.Color(),[]);
  const targets=useMemo(()=>Array.from({length:144},(_,i)=>{
    const x=((i*73)%149)/149, z=((i*47)%151)/151, lane=Math.floor(i/36), j=i%36;
    if(stage===8){
      if(i<48){const k=i;return {p:[-2.55+(k%3-1)*.32,Math.floor(k/12)*.32-.65,(Math.floor(k/3)%4-1.5)*.32],s:[.27,.27,.27],r:[0,0,0]};}
      if(i<120){const bank=Math.floor((i-48)/24),k=(i-48)%24;return {p:[2+(k%3-1)*.32,Math.floor(k/6)*.32-.65,(bank-1)*2+(Math.floor(k/3)%2-.5)*.32],s:[.27,.27,.27],r:[0,0,0]};}
      const bank=(i-120)%3,u=Math.floor((i-120)/3)/8;return {p:[-1.7+u*3.05,-.08+Math.sin(u*Math.PI)*.38,(bank-1)*2*u],s:[.2,.2,.2],r:[0,0,0]};
    }
    if(stage===6) return {p:homePoints[i],s:[.39,.39,.39],r:[0,0,0]};
    if(stage===0) {const size=.7+(i%7)*.12;return {p:[(x-.5)*6.7,(z-.5)*3.7,Math.sin(i*2)*2.3],s:[(.26+(i%3)*.07)*size,(i%4===0?.3:.1)*size,(.27+(i%5)*.045)*size],r:[z*2.5,x*3.1,Math.sin(i)*1.1]};}
    if(stage===7) {const day=i%36,layer=Math.floor(i/36);return {p:[(day%6-2.5)*.74,(layer-1.5)*.13,(Math.floor(day/6)-2.5)*.57],s:[.52,.1,.37],r:[0,0,0]};}
    if(stage===1) return {p:[(lane-1.5)*1.8+(j%3-1)*.43,Math.floor(j/12)*.51-.7,(Math.floor(j/3)%4-1.5)*.52],s:[.35,.12,.43],r:[0,0,0]};
    if(stage===2) return {p:[(lane-1.5)*1.8+(j%3-1)*.41,Math.floor(j/9)*.43-.6,(Math.floor(j/3)%3-1)*.44],s:[.35,.35,.35],r:[0,0,0]};
    if(stage===3) {const a=lane*Math.PI/2+.4;return {p:[Math.cos(a)*2.4+(j%3-1)*.32,Math.floor(j/9)*.28-.65,Math.sin(a)*2.4+(Math.floor(j/3)%3-1)*.32],s:[.26,.22,.26],r:[0,0,0]};}
    if(stage===4) return {p:[(j-17.5)*.19,Math.sin(j*.48+lane)*.48,(lane-1.5)*1.3],s:[.12,.19,.19],r:[0,0,0]};
    const a=(i%48)/48*Math.PI*2,ring=Math.floor(i/48);return {p:[Math.cos(a)*2.8,(ring-1)*.36+Math.sin(a*2)*.16,Math.sin(a)*2.8],s:[.23,.25,.23],r:[0,-a,0]};
  }).map(t=>({...t,p:new THREE.Vector3(...t.p),s:new THREE.Vector3(...t.s),r:new THREE.Vector3(...t.r)})),[stage]);
  useEffect(()=>{invalidate()},[targets,motion,light,invalidate]);
  useFrame(({clock,pointer},dt)=>{
    if(cloud.current)cloud.current.rotation.y=THREE.MathUtils.damp(cloud.current.rotation.y,motion?pointer.x*.06+Math.sin(progress*Math.PI*2)*.16:0,2,dt);
    if(!ref.current)return;
    for(let i=0;i<144;i++){
      const t=targets[i];
      if(stage===8&&i>=120){const bank=(i-120)%3,u=(Math.floor((i-120)/3)/8+(motion?clock.elapsedTime*.14:0))%1;t.p.set(-1.7+u*3.05,-.08+Math.sin(u*Math.PI)*.38,(bank-1)*2*u);}
      if(!state.current[i])state.current[i]={p:motion?new THREE.Vector3((i%12-5.5)*.65,Math.sin(i*2)*2,Math.floor(i/12-5.5)*.5):t.p.clone(),s:t.s.clone(),r:t.r.clone()};
      const v=state.current[i],alpha=motion?1-Math.exp(-dt*3.2):1;
      v.p.lerp(t.p,alpha);v.s.lerp(t.s,alpha);v.r.lerp(t.r,alpha);
      dummy.position.copy(v.p);dummy.scale.copy(v.s);dummy.rotation.set(v.r.x,v.r.y,v.r.z);
      if(motion){
        const time=clock.elapsedTime;
        if(stage===0){
          // Independent paths, speeds and rotations: fragments have not found their place yet.
          const speed=.3+(i%9)*.055,phase=i*2.399;
          dummy.position.x+=Math.sin(time*speed+phase)*(.35+(i%4)*.1);
          dummy.position.y+=Math.cos(time*(speed*.83)+phase)*(.3+(i%5)*.09);
          dummy.position.z+=Math.sin(time*(speed*.71)+phase*.7)*.5;
          dummy.rotation.x+=Math.sin(time*.43+phase)*.6;
          dummy.rotation.y+=Math.cos(time*.37+phase)*.8;
          dummy.rotation.z+=Math.sin(time*.31+phase)*.45;
        }else{
          dummy.position.y+=Math.sin(time*.65+Math.floor(i/36))*.07;
          if(stage===4){const phase=(i%36)*.48+Math.floor(i/36);dummy.position.y+=(Math.sin(phase+time*.8)-Math.sin(phase))*.35;}
        }
      }
      dummy.updateMatrix();ref.current.setMatrixAt(i,dummy.matrix);
      color.set(stage===8?(i<48?'#a9c2ae':i>=120?'#fa9157':['#f09a69','#9fbc94','#d8b682'][Math.floor((i-48)/24)]):(stage===1||stage===2||stage===4)?(light?['#df592b','#9eb4a9','#e2c4a0','#355e50']:['#ef794b','#b4c6b6','#dbbd94','#689e8b'])[Math.floor(i/36)]:stage===6?(i>105?'#e87849':'#668d73'):stage===7?([8,9,14,15].includes(i%36)?'#f28752':'#789b89'):stage===0?['#d1663d','#6b8e7e','#b6bfb0','#3b6555','#c4ad8c'][i%5]:i%5===0?'#ef7546':light?'#789b89':'#a9c2ae');ref.current.setColorAt(i,color);
    }ref.current.instanceMatrix.needsUpdate=true;ref.current.instanceColor.needsUpdate=true;
  });
  return <group ref={cloud}>
    <instancedMesh ref={ref} geometry={cubeGeometry} args={[null,null,144]}><meshStandardMaterial metalness={.15} roughness={.58}/></instancedMesh>
    {(stage===3||stage===5)&&<><mesh rotation={[-Math.PI/2,0,0]} position={[0,-.8,0]}><torusGeometry args={[2.9,.008,8,100]}/><meshBasicMaterial color={ORANGE}/></mesh><mesh position={[0,-.15,0]}><boxGeometry args={[.85,.85,.85]}/><meshStandardMaterial color="#ec6836" metalness={.25} roughness={.35}/></mesh>{[0,1,2,3].map(i=><Trace key={i} points={[[0,-.8,0],[Math.cos(i*Math.PI/2+.4)*2.4,-.8,Math.sin(i*Math.PI/2+.4)*2.4]]} opacity={.6}/>)}</>}
    {stage===8&&[-2,0,2].map(z=><Trace key={z} points={[[-1.8,-.35,0],[-.8,-.35,0],[1.5,-.35,z]]} color={light?'#b46a40':'#caa47b'} opacity={.65}/>)}
    {stage!==0&&<gridHelper args={[12,24,light?'#bdc9bb':'#456657',light?'#dde2d6':'#254335']} position={[0,-1.5,0]}/>}
  </group>;
}
class SceneBoundary extends Component {constructor(p){super(p);this.state={failed:false}}static getDerivedStateFromError(){return{failed:true}}render(){return this.state.failed?<div className="scene-fallback"><div className="fallback-layer">Sources</div><div className="fallback-layer">Clinical meaning</div><div className="fallback-layer">Complete populations</div><div className="fallback-layer">Models + mathematics</div><div className="fallback-layer">Traceable answers</div><p>Explore every layer below.</p></div>:this.props.children}}
function ResponsiveCamera({mode}) {
  const {camera,size}=useThree();
  useEffect(()=>{camera.zoom=Math.min(size.width/(mode==='hero'?9:9.4),size.height/(mode==='hero'?8.9:7.5));camera.updateProjectionMatrix()},[camera,size,mode]);
  return null;
}
export default function Scene({mode='hero',stage=0,motion=true,active=-1,light=false,progress=0}) {
  const [supported,setSupported]=useState(true),[visible,setVisible]=useState(true);
  const sceneRoot=useRef();
  useEffect(()=>{const observer=new IntersectionObserver(([entry])=>setVisible(entry.isIntersecting));if(sceneRoot.current)observer.observe(sceneRoot.current);return()=>observer.disconnect()},[]);
  useEffect(()=>{try{const c=document.createElement('canvas');setSupported(!!(c.getContext('webgl2')||c.getContext('webgl')))}catch{setSupported(false)}},[]);
  if(!supported)return <div className="scene-fallback">Sources → meaning → population → analysis → evidence</div>;
  return <div ref={sceneRoot} style={{width:"100%",height:"100%"}}><SceneBoundary><Canvas frameloop={visible&&motion?"always":"demand"} orthographic camera={{position:mode==='hero'?[8,6.1,10]:[5,6.5,9],zoom:mode==='hero'?64:66,near:.1,far:100}} dpr={[1,1.65]} gl={{antialias:true,alpha:true,powerPreference:'high-performance'}} aria-hidden="true">
    <ResponsiveCamera mode={mode}/>
    <ambientLight intensity={light?1.5:mode==='hero'?1.7:1.2}/><directionalLight position={[-4,8,7]} intensity={light?2.8:4.3} color="#fff3de"/><directionalLight position={[6,3,-6]} intensity={light?1.6:3} color="#9dc5c0"/>
    {mode==='hero'?<Machine motion={motion} active={active}/>:<EvidenceField stage={stage} motion={motion} light={light} progress={progress}/>}
  </Canvas></SceneBoundary></div>;
}
