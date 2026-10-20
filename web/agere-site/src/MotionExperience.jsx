import React,{lazy,Suspense,useState,useEffect,useRef} from 'react';
import {ArrowUpRight,ArrowDown,Pause,Play,MoveUpRight} from 'lucide-react';
const Scene=lazy(()=>import('./Scene.jsx'));

export function MotionJourney({motion,setMotion,onOpen,config}){
 const {stages,chapterIds:storyIds,labels:storyLabels,id:storyId,name:storyName}=config;const storyText=stages.map(x=>x.text);
 const [stage,setStage]=useState(0),[progress,setProgress]=useState(0);const visual=useRef();const chapters=useRef([]);
 useEffect(()=>{let frame;const update=()=>{const mobile=window.innerWidth<=760;const visualBottom=visual.current?.getBoundingClientRect().bottom??window.innerHeight*.5;const point=mobile?visualBottom+Math.max(24,(window.innerHeight-visualBottom)*.28):window.innerHeight*.52;let current=0;chapters.current.forEach((el,i)=>{if(el&&el.getBoundingClientRect().top<=point)current=i});setStage(current);const first=chapters.current[0]?.getBoundingClientRect().top??0;const last=chapters.current[5]?.getBoundingClientRect().top??1;setProgress(Math.max(0,Math.min(1,(point-first)/Math.max(1,last-first))))};const request=()=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(update)};update();window.addEventListener('scroll',request,{passive:true});window.addEventListener('resize',request);return()=>{cancelAnimationFrame(frame);window.removeEventListener('scroll',request);window.removeEventListener('resize',request)}},[]);
 const s=stages[stage];
 return <>
 <section className="motion-prologue"><div className="mono">{config.kicker}</div><h2>{config.title[0]}<br/><span>{config.title[1]}</span></h2><a href={'#'+storyId} aria-label={'Explore the six-step '+storyName.toLowerCase()}><ArrowDown size={26}/></a></section>
 <section className="motion-journey scrolling-story" id={storyId} aria-label={'Scroll through the six-step '+storyName.toLowerCase()}>
  <div className="motion-stage" ref={visual}>
   <div className="motion-stage-top"><span className="mono"><i/> SCROLL TO CONNECT THE INTELLIGENCE</span><button onClick={()=>setMotion(!motion)} aria-label={motion?'Pause all animation':'Enable all animation'}>{motion?<Pause size={12}/>:<Play size={12}/>}<span>{motion?'Motion on':'Motion off'}</span></button></div>
   <div className="motion-diagram"><div className="motion-scene"><Suspense fallback={null}><Scene mode="story" stage={stage} motion={motion} progress={progress}/></Suspense></div><div className="diagram-label dl-a" key={'a'+stage}><i/>{storyLabels[stage][0]}</div><div className="diagram-label dl-b" key={'b'+stage}><i/>{storyLabels[stage][1]}</div><div className="diagram-label dl-c" key={'c'+stage}><i/>{storyLabels[stage][2]}</div><div className="motion-readout"><strong>{s.metric}</strong><span>{s.unit}</span><span className="readout-cross">+</span></div><span className="diagram-disclaimer mono">ONE SYSTEM / EVERY PIECE CONNECTED</span></div>
   <nav className="motion-chapters" aria-label={storyName+' chapters'}>{stages.map((x,i)=><a key={x.n} href={'#'+storyIds[i]} className={stage===i?'active':''} aria-current={stage===i?'step':undefined}><span className="mono">{x.n}</span><b>{x.name}</b><MoveUpRight size={15}/></a>)}</nav>
   <div className="motion-progress" aria-hidden="true"><i style={{transform:`scaleX(${progress})`}}/></div>
  </div>
  <div className="motion-scroll-chapters">{stages.map((chapter,i)=><article id={storyIds[i]} className={'motion-narrative'+(stage===i?' active':'')} key={chapter.n} ref={el=>chapters.current[i]=el}>
   <span className="motion-chapter">{chapter.n}<span>/ {chapter.name.toUpperCase()}</span></span><h2>{chapter.title}</h2><p>{storyText[i]}</p><button className="text-link light-link" onClick={()=>onOpen(chapter.detail)}>Explore this layer <ArrowUpRight size={16}/></button><div className="motion-status"><i/>{chapter.status}</div>
  </article>)}</div>
 </section>
 </>
}
