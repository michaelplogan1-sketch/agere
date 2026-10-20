import React,{useEffect} from 'react';
import * as THREE from 'three';
import {useThree} from '@react-three/fiber';
import {RoundedBoxGeometry} from 'three/addons/geometries/RoundedBoxGeometry.js';
import {mergeGeometries} from 'three/addons/utils/BufferGeometryUtils.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';

export const evidencePalette=['#dbe0d2','#93aa9a','#355b4c','#c18a64'];
export function createEvidenceGeometry(){
 const body=new RoundedBoxGeometry(1,1,1,2,.075);
 const top=new THREE.BoxGeometry(.58,.008,.028).toNonIndexed().translate(0,.501,-.22);
 const front=new THREE.BoxGeometry(.58,.028,.008).toNonIndexed().translate(0,.28,.501);
 const inlay=mergeGeometries([top,front]);top.dispose();front.dispose();
 return {body,inlay};
}
export function EvidenceMaterial({wireframe=false}){return <meshStandardMaterial metalness={.24} roughness={.34} wireframe={wireframe}/>}
export function EvidenceInlay(){return <meshStandardMaterial color="#ba855c" metalness={.64} roughness={.3}/>}
export function StudioLight(){
 const {gl,scene,invalidate}=useThree();
 useEffect(()=>{
  const room=new RoomEnvironment(),generator=new THREE.PMREMGenerator(gl),environment=generator.fromScene(room,.04);
  const previous=scene.environment,previousIntensity=scene.environmentIntensity;
  scene.environment=environment.texture;scene.environmentIntensity=.6;room.dispose();generator.dispose();invalidate();
  return()=>{scene.environment=previous;scene.environmentIntensity=previousIntensity;environment.dispose()};
 },[gl,scene,invalidate]);
 return <><ambientLight intensity={.85}/><directionalLight position={[-4,7,8]} intensity={2.8} color="#fff6df"/><directionalLight position={[6,0,-3]} intensity={1.8} color="#b1cab9"/></>;
}
