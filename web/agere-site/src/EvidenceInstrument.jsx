import React,{useState} from 'react';
import {ArrowUpRight,ArrowRight,FileText,MessageSquare,PackageSearch,HeartPulse,CornerDownRight} from 'lucide-react';
import {navigateTabs} from './tabs.js';

export function SignalCube({className=''}){
 return <svg className={'signal-cube '+className} viewBox="0 0 100 110" fill="none" aria-hidden="true"><path d="M50 6 94 31 50 57 6 31Z" fill="#f49a69"/><path d="M6 31 50 57 50 105 6 80Z" fill="#e36a39"/><path d="M94 31 50 57 50 105 94 80Z" fill="#b64a29"/><path d="M50 6 94 31 94 80 50 105 6 80 6 31Z" stroke="#ffba88" strokeWidth=".6"/><path d="m6 31 44 26 44-26M50 57v48" stroke="#ffd1a0" strokeWidth=".6"/></svg>
}
const signals=[
 {label:'EF history',icon:HeartPulse,source:'Clinical note',date:'SOURCE / JAN 22, 2029',value:'38',unit:'% EF',title:'A finding, with its history.',observation:'The received note reports an ejection fraction of 38%.',uncertainty:'The clinical acquisition date is unresolved. A source date is not automatically the measurement date.',question:'When was this EF measured?',action:'Verify the acquisition context and current phenotype before assessing a treatment opportunity.',state:'Acquisition date unresolved',sourceId:'WHL-R-082b891551053a2c1a19c8',received:'Received Jan 27, 2029'},
 {label:'Medication list',icon:FileText,source:'Historical record',date:'RECEIVED / AUG 29, 2026',value:'25',unit:'mg BID',title:'A dose on a list is a clue.',observation:'The historical medication list records carvedilol 25 mg twice daily.',uncertainty:'The clinical event date is unknown. This entry does not verify a current regimen or maximum tolerated dose.',question:'Is this still the actual regimen?',action:'Reconcile the historical entry with the patient’s report and available dispensing evidence.',state:'Historical dose only',sourceId:'WHR-55233654facd2f871d790fcc',received:'Clinical event date unresolved'},
 {label:'Patient interview',icon:MessageSquare,source:'Patient report',date:'SOURCE / JAN 30, 2029',value:'Not',unit:'taking',title:'Listen to the person.',observation:'The later interview reports that the patient is not taking carvedilol.',uncertainty:'Current dose, frequency and directions were not confirmed. The report alone does not establish why use changed.',question:'What is the patient taking now?',action:'Clarify actual use, access and clinical context with the patient and care team.',state:'Current use unverified',sourceId:'WHL-R-62bbf222249ebbc69895d0',received:'Received Jan 31, 2029'},
 {label:'Pharmacy response',icon:PackageSearch,source:'Scoped search',date:'SOURCE / FEB 06, 2029',value:'180',unit:'day window',title:'Keep the limits in the picture.',observation:'No matching dispensing record was found in the responding pharmacy during the requested window.',uncertainty:'Other pharmacies are not covered. A missing match does not establish that the patient is not taking the medication.',question:'What evidence is still outside view?',action:'Resolve the regimen with additional context. Keep any medication change pending clinical review.',state:'One pharmacy · limited scope',sourceId:'WHL-R-a6aec70ea75c55e46ade23',received:'Received Feb 11, 2029'}
];
export default function EvidenceInstrument({onOpen}){
 const [selected,setSelected]=useState(0);const s=signals[selected];
 return <div className="evidence-instrument">
  <div className="instrument-instruction"><span className="mono"><i/> SELECT A SOURCE. FOLLOW ITS MEANING.</span><span>Received synthetic evidence</span></div>
  <div className="signal-workspace">
   <div className="signal-inputs" role="tablist" aria-label="Evidence sources" aria-orientation="vertical" onKeyDown={navigateTabs}>{signals.map((item,i)=><button key={item.label} id={'signal-tab-'+i} role="tab" aria-selected={selected===i} aria-controls="signal-panel" tabIndex={selected===i?0:-1} onClick={()=>setSelected(i)}><item.icon size={18}/><span><small>0{i+1} / {item.source}</small><b>{item.label}</b></span><span className="signal-socket"/></button>)}</div>
   <div className="signal-core" aria-hidden="true"><svg className="signal-connectors" viewBox="0 0 300 330" preserveAspectRatio="none">{signals.map((_,i)=><path key={i} className={i===selected?'lit':''} d={`M0 ${42+i*82} H40 Q70 ${42+i*82} 85 ${Math.min(220,Math.max(110,42+i*82))} L130 165 H300`}/>)}</svg><div className="signal-orbit"><span/><span/><SignalCube/></div><span className="mono core-caption">ONE PATIENT CONTEXT</span><span className="core-coordinate">EVIDENCE → MEANING</span></div>
   <div className="signal-output" aria-live="polite"><span className="mono">THE REVIEW QUESTION</span><CornerDownRight size={21}/><h4>{s.question}</h4><span className="signal-state"><i/>{s.state}</span><p>Uncertainty travels with the finding.</p></div>
  </div>
  <div className="signal-panel" id="signal-panel" role="tabpanel" aria-labelledby={'signal-tab-'+selected}>
   <div className="signal-reading"><span className="mono">{s.date}</span><strong>{s.value}<small>{s.unit}</small></strong><span>{s.received}</span></div>
   <div className="signal-meaning" key={selected}><span className="mono">WHAT THIS SOURCE TELLS US</span><h4>{s.title}</h4><p>{s.observation}</p><div className="signal-limit"><span>CONTEXT TO RESOLVE</span><p>{s.uncertainty}</p></div></div>
   <div className="signal-action"><span className="mono">PROPOSED NEXT STEP</span><ArrowRight size={22}/><p>{s.action}</p><button onClick={()=>onOpen('hfCase')}>Open the source chain <ArrowUpRight size={15}/></button></div>
  </div>
  <div className="signal-receipt"><span className="mono">SOURCE / {s.sourceId}</span><span>Recorded review: regimen unverified. No prescribing instruction changed.</span></div>
 </div>
}
