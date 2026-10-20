import React,{useEffect,useMemo,useRef} from 'react';
import {useFrame,useLoader,useThree} from '@react-three/fiber';
import * as THREE from 'three';
import cartridgeUrl from './assets/agere-data-cartridge.png';

const COUNT=72,TAU=Math.PI*2;
const fract=x=>x-Math.floor(x),smooth=x=>{x=Math.max(0,Math.min(1,x));return x*x*(3-2*x)};
const iso=(x,y,z)=>new THREE.Vector3((x-z)*.61,y*.65+(x+z)*.26,(x+z)*.1);
const tint=['#ffffff','#d3e7dd','#a7c2b0','#f4d4ae'];
function formation(stage,i){
 const lane=Math.floor(i/18),j=i%18,a=i/COUNT*TAU;
 let p,s=.68;
 if(stage===0){p=new THREE.Vector3((fract(Math.sin(i*127.1+4)*43758.5453)-.5)*7.1,(fract(Math.sin(i*311.7+13)*97121.412)-.5)*3.9,(fract(Math.sin(i*74.7)*19341.27)-.5)*2);s=i%19===0?1.42:.38+(i%7)*.09;}
 else if(stage===1){p=new THREE.Vector3((lane-1.5)*1.95+(j%4-1.5)*.35,(Math.floor(j/4)-2.5)*.49,0);s=.58;}
 else if(stage===2){p=iso(j%3-1,Math.floor(j/6)-1,Math.floor(j/3)%2-.5).multiplyScalar(.78);p.x+=(lane-1.5)*2.2;s=.79;}
 else if(stage===3){p=iso(j%3-1,Math.floor(j/6)-1.5,Math.floor(j/3)%2-.5).multiplyScalar(.72);p.x+=Math.cos(lane*TAU/4+.4)*2.65;p.y+=Math.sin(lane*TAU/4+.4)*1.42;s=.63;}
 else if(stage===4){p=new THREE.Vector3((j-11.5)*.32,(lane-1.5)*.88+Math.sin(j*.48+lane)*.24,0);s=.45;}
 else if(stage===5){const ring=Math.floor(i/24),t=(i%24)/24*TAU;p=new THREE.Vector3(Math.cos(t)*(2.45+ring*.3),Math.sin(t)*(1.45+ring*.23),Math.sin(t)*.2);s=.51;}
 else if(stage===6){
  // A home made from the same records: walls, roof, windows, and a doorway.
  const points=[];for(let y=0;y<5;y++)for(let x=-3;x<=3;x++)if(!(Math.abs(x)<1&&y<3)&&!(Math.abs(x)===2&&y===3))points.push([x*.55,(y-2)*.51,0]);
  for(let r=0;r<4;r++)for(let x=-3+r;x<=3-r;x++)points.push([x*.55,1.15+r*.43,0]);
  const q=points[i%points.length];p=new THREE.Vector3(q[0]+(i>=points.length?.19:0),q[1]-.4+(i>=points.length?.12:0),i>=points.length?-.3:0);s=.63;
 }
 else if(stage===7){p=iso(j%6-2.5,(lane-1.5)*.18,Math.floor(j/6)-1.5);p.x*=1.3;p.y*=1.3;s=.62;}
 else {const bank=Math.floor(i/18);p=iso(j%3-1,Math.floor(j/6)-1.5,Math.floor(j/3)%2-.5).multiplyScalar(.7);p.x+=bank===0?-2.9:2.1;p.y+=bank===0?0:(bank-2)*1.45;s=.63;}
 return {p,s};
}
export default function KineticBlocks({stage,motion,cinemaPointer}){
 const {camera,invalidate}=useThree(),body=useRef(),sparks=useRef(),group=useRef();
 const image=useLoader(THREE.TextureLoader,cartridgeUrl);
 const state=useRef([]),lastStage=useRef(stage),transition=useRef(3),time=useRef(0);
 const objects=useMemo(()=>({tile:new THREE.Object3D(),spark:new THREE.Object3D(),color:new THREE.Color(),p:new THREE.Vector3()}),[]);
 const targets=useMemo(()=>Array.from({length:COUNT},(_,i)=>formation(stage,i)),[stage]);
 useEffect(()=>{image.colorSpace=THREE.SRGBColorSpace;image.anisotropy=4;invalidate()},[image,invalidate]);
 useEffect(()=>{invalidate()},[stage,motion,invalidate]);
 useFrame((_,delta)=>{
  if(!body.current)return;const dt=Math.min(delta,.05);if(motion){time.current+=dt;transition.current+=dt;}
  const t=time.current,ptr=cinemaPointer?.current||{x:0,y:0};group.current.quaternion.copy(camera.quaternion);
  if(lastStage.current!==stage){state.current.forEach(v=>{v.from.copy(v.p);v.fromSize=v.s});transition.current=motion?0:3;lastStage.current=stage;}
  for(let i=0;i<COUNT;i++){
   const target=targets[i],phase=i*2.399,life=fract(t/(19+i%7)+i*.618),birth=smooth(life/.09),death=1-smooth((life-.94)/.06);
   if(!state.current[i])state.current[i]={p:target.p.clone(),from:target.p.clone(),s:target.s,fromSize:target.s};
   const v=state.current[i];objects.p.copy(target.p);let scale=target.s,turn=0,energy=0;
   const assembling=motion&&transition.current<2.5;
   if(assembling){
    const delay=(i%18)*.018+Math.floor(i/18)*.13,u=smooth((transition.current-delay)/1.65),arc=Math.sin(u*Math.PI);
    objects.p.copy(v.from).lerp(target.p,u);objects.p.x+=Math.sin(phase)*arc*.85;objects.p.y+=(.65+Math.cos(phase)*.4)*arc;
    scale=THREE.MathUtils.lerp(v.fromSize,target.s,u)*(1+arc*.12);turn=Math.sin(phase)*arc*.6;energy=arc;
   }else if(motion&&stage===0){
    // Each cartridge has its own birth, travel and retirement; the field never loops as one sheet.
    objects.p.x+=(life-.5)*1.65+Math.sin(t*.27+phase)*.38;
    objects.p.y+=Math.sin(t*(.25+i%5*.025)+phase)*.42;
    objects.p.z+=Math.cos(t*.3+phase)*.4;
    scale*=birth*death;turn=Math.sin(t*.24+phase)*.36+(1-birth)*.8;energy=1-birth;
   }else if(motion&&stage!==6){
    // Small waves of new evidence replenish the settled structure.
    const u=smooth(life/.16),incoming=1-u;
    objects.p.x+=(i%2?1:-1)*incoming*3.7;objects.p.y+=incoming*(1.7+Math.sin(phase));
    scale*=smooth(life/.035)*(1-smooth((life-.985)/.015));turn=incoming*Math.sin(phase)*.7;energy=incoming;
    if(stage===4)objects.p.y+=Math.sin(i*.48+t*.8)*.12;
    if(stage===5){const a=t*.08;const x=objects.p.x,y=objects.p.y;objects.p.x=x*Math.cos(a)-y*Math.sin(a);objects.p.y=x*Math.sin(a)+y*Math.cos(a);}
   }
   if(motion){objects.p.x+=ptr.x*(.08+Math.abs(target.p.z)*.12);objects.p.y-=ptr.y*.13;}
   v.p.copy(objects.p);v.s=scale;objects.tile.position.copy(objects.p);objects.tile.rotation.set(0,0,turn);objects.tile.scale.set(scale*1.08,scale,1);objects.tile.updateMatrix();body.current.setMatrixAt(i,objects.tile.matrix);
   objects.color.set(stage===2||stage===1?tint[Math.floor(i/18)]:i%13===0?tint[3]:'#ffffff');body.current.setColorAt(i,objects.color);
   objects.spark.position.copy(objects.p);objects.spark.position.z+=.1;objects.spark.scale.setScalar(motion?energy*.07:0);objects.spark.updateMatrix();sparks.current.setMatrixAt(i,objects.spark.matrix);
  }
  body.current.instanceMatrix.needsUpdate=true;body.current.instanceColor.needsUpdate=true;sparks.current.instanceMatrix.needsUpdate=true;
 });
 return <group ref={group}>
  <instancedMesh ref={body} args={[null,null,COUNT]} frustumCulled={false}><planeGeometry args={[1,1]}/><meshBasicMaterial map={image} transparent alphaTest={.13} depthWrite toneMapped={false}/></instancedMesh>
  <instancedMesh ref={sparks} args={[null,null,COUNT]} frustumCulled={false}><circleGeometry args={[1,8]}/><meshBasicMaterial color="#e6af6e" transparent opacity={.8} depthWrite={false} blending={THREE.AdditiveBlending}/></instancedMesh>
 </group>;
}
