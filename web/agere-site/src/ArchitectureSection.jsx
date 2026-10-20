import React,{useEffect,useState} from 'react';
import {ArrowDown,ArrowRight,ArrowUpRight,Check,Pause,Play,RotateCcw} from 'lucide-react';
import {stackLayers,architectureLenses,architectureTraces} from './architectureData.js';
import {navigateTabs} from './tabs.js';
import './architecture.css';
const modeCopy=['Meaning comes first.','The right method.','Intelligence compounds.'];
export function ArchitectureSection({motion,setMotion}){
 const[layer,setLayer]=useState(1),[lens,setLens]=useState(0),[trace,setTrace]=useState(0),[running,setRunning]=useState(false);
 const current=stackLayers[layer],view=architectureLenses[lens],journey=architectureTraces[trace];
 useEffect(()=>{if(!running||!motion)return;const timer=setTimeout(()=>{if(layer<5)setLayer(layer+1);else setRunning(false)},2400);return()=>clearTimeout(timer)},[running,motion,layer]);
 function select(i){setLayer(i);setRunning(false)}
 function chooseLens(i){setLens(i);select(architectureLenses[i].layer)}
 function run(){setLayer(0);setMotion(true);setRunning(true)}
 return <section className="agere-architecture" id="architecture" aria-labelledby="architecture-title">
  <div className="intelligence-theatre architecture-overview" id="architecture-engine">
   <div className="theatre-topline mono"><span><i/> AGERE / ENGINEERING INTELLIGENCE</span><div><button onClick={()=>setMotion(!motion)} aria-label={motion?'Pause architecture animation':'Enable architecture animation'}>{motion?<Pause size={13}/>:<Play size={13}/>}<span>{motion?'Motion on':'Motion off'}</span></button></div></div>
   <div className="theatre-copy"><div><span className="mono theatre-eyebrow">THE ENGINE BEHIND THE UNDERSTANDING</span><h2 id="architecture-title">From data.<br/><em>To wiser decisions.</em></h2></div><div className="overview-description"><p>Six connected systems turn data into intelligence, build knowledge with context, and carry that understanding into the next decision.</p><a href="#engine-detail">Follow the architecture <ArrowDown size={15}/></a></div></div>
   <div className="engine-mode-switch" role="tablist" aria-label="Explore Agere’s engineering advantages" onKeyDown={navigateTabs}>{architectureLenses.map((item,i)=><button key={item.id} role="tab" id={'architecture-tab-'+item.id} aria-controls="architecture-advantage-panel" aria-selected={lens===i} tabIndex={lens===i?0:-1} onClick={()=>chooseLens(i)}><span className="mono">0{i+1} / {item.name.toUpperCase()}</span><strong>{modeCopy[i]}</strong><ArrowUpRight size={20}/><span className="mode-progress"/></button>)}</div>
  </div>
  <div className="engine-detail-band" id="engine-detail">
   <div className="system-index"><span className="mono">INSPECT THE SYSTEM</span><div role="group" aria-label="Select an architecture layer">{stackLayers.map((item,i)=><button key={item.id} aria-pressed={layer===i} onClick={()=>select(i)} aria-label={`${i+1}. ${item.name}`}><span className="mono">0{i+1}</span><span>{item.name}</span><ArrowUpRight size={13}/></button>)}</div></div>
   <div className="system-description" aria-live="polite"><span className="mono">0{layer+1} / {current.code} / {current.short.toUpperCase()}</span><h3>{current.headline}</h3><p>{current.description}</p><ul>{current.parts.map(part=><li key={part}><Check size={12}/>{part}</li>)}</ul></div>
   <div className="system-logic"><span className="mono">UNDER THE SURFACE</span><div>{current.example.map(line=><code key={line}>{line}</code>)}</div><p>{current.payoff}</p></div>
  </div>
  <div className="engine-question-lab">
   <div className="question-lab-heading"><div><span className="mono">PUT THE ARCHITECTURE TO WORK</span><h3>One question.<br/><em>The whole engine.</em></h3></div><div role="group" aria-label="Choose an architecture example">{architectureTraces.map((t,i)=><button key={t.id} aria-pressed={trace===i} onClick={()=>{setTrace(i);select(0)}}>{t.label}<ArrowUpRight size={13}/></button>)}</div></div>
   <div className="question-lab-query"><p>“{journey.question}”</p><button onClick={running?()=>setRunning(false):run}>{running?<Pause size={15}/>:<Play size={15}/>} {running?'Pause trace':'Trace this question'}</button></div>
   <div className={'question-lab-track '+(running&&motion?'is-running':'')} aria-label="Question trace progress" style={{'--step':layer}}>{stackLayers.map((l,i)=><button key={l.id} className={i===layer?'is-current':i<layer?'is-past':''} onClick={()=>select(i)} aria-label={`Trace stage ${i+1}: ${l.short}`} aria-current={i===layer?'step':undefined}><span className="trace-node"><i/></span><span className="mono">0{i+1}</span><strong>{l.short}</strong></button>)}</div>
   <div className="question-lab-caption" aria-live="polite"><span><i/>{journey.steps[layer]}</span><button onClick={()=>select((layer+1)%6)}>{layer===5?'Restart':'Next system'}{layer===5?<RotateCcw size={13}/>:<ArrowRight size={14}/>}</button></div>
  </div>
  <div className="architecture-case" id="architecture-advantage-panel" role="tabpanel" aria-labelledby={'architecture-tab-'+view.id}>
   <div><span className="mono">THE ENGINEERING ADVANTAGE / {view.name.toUpperCase()}</span><h3>{view.title}</h3><p>{view.description}</p>{view.id==='cost'&&<div className="architecture-local-path"><strong>0</strong><span>MODEL API CALLS<br/>ON A LOCAL COMPILED PATH</span></div>}</div>
   <dl>{view.points.map(([name,text])=><div key={name}><dt>{name}</dt><dd>{text}</dd></div>)}</dl>
  </div>
  <div className="architecture-exit"><span>From computational precision<br/><strong>to better care and better economics.</strong></span><a href="#physician-value">Follow the value <ArrowRight size={18}/></a><a href="#difference">Explore the landscape <ArrowUpRight size={15}/></a></div>
 </section>;
}
