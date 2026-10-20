import React, {useEffect, useRef, useState} from 'react';
import {ArrowRight, ArrowUpRight, Stethoscope, Repeat2} from 'lucide-react';
import {CareRewards} from './CareRewards.jsx';
import './care-rewards.css';

const streams = [
  {
    name: 'Care payments', kicker: 'FUND THE WORK', title: 'Pay for care that happens between visits.',
    text: 'Give practices predictable support for the work that keeps people well: medication optimization, pharmacy coordination, remote monitoring and follow-through at home.',
    basis: 'The care model and contracted payment terms',
    contribution: 'Attributed patients, delivered care and coordinated support',
    timing: 'A clear recurring payment schedule',
  },
  {
    name: 'Quality rewards', kicker: 'RECOGNIZE BETTER CARE', title: 'Make quality worth investing in.',
    text: 'Connect quality incentives to meaningful measures of care. Give physicians a clear view of the contribution their practice makes—and how that contribution earns a reward.',
    basis: 'Agreed quality measures and performance rules',
    contribution: 'Documented care and measured performance',
    timing: 'The performance period and payment milestones',
  },
  {
    name: 'Shared savings', kicker: 'RETURN THE VALUE', title: 'Let physicians share in the value they create.',
    text: 'Reconcile earned savings against the contract, then make the allocation across physicians and practices explainable. Recognize patient complexity and team contributions. Show the calculation, deductions and path to payment.',
    basis: 'Contract settlement and an explicit allocation method',
    contribution: 'An explainable share for each physician and practice',
    timing: 'Earned, reconciled and paid—each status visible',
  },
];

// Conceptual funds flow. No transaction is initiated and no dollar amount is implied.
function FundsDiagram({selected, moving}) {
  return <div className="funds-diagram" role="img" aria-label={`Payer and contract funding flows through ${streams[selected].name.toLowerCase()} to physicians and practices.`}>
    <svg className="funds-connections" viewBox="0 0 1000 340" preserveAspectRatio="none" aria-hidden="true">
      {[70,170,270].map((y,i)=>{
        const d=`M 210 170 C 320 170 305 ${y} 420 ${y} L 580 ${y} C 695 ${y} 680 170 790 170`;
        return <g key={y} className={selected===i?'is-selected':''}>
          <path className="funds-track" d={d}/>
          {[0,1,2,3,4].map(n=><rect key={n} className="funds-packet" x="-4" y="-4" width="8" height="8" rx="1" opacity={selected===i?1:.27} transform={moving?undefined:`translate(${440+n*30} ${y})`}>
            {moving&&<animateMotion dur="7s" begin={`${n*-1.4}s`} repeatCount="indefinite" path={d}/>}
          </rect>)}
        </g>;
      })}
    </svg>
    <div className="funds-origin"><span className="funds-cube-stack" aria-hidden="true">{Array.from({length:9},(_,i)=><i key={i}/>)}</span><span className="mono">THE FUNDING</span><strong>Payer &amp;<br/>value-based contract</strong></div>
    <div className="funds-streams" aria-hidden="true">{streams.map((stream,i)=><div key={stream.name} className={selected===i?'is-selected':''}><i/><span>{stream.name}</span><ArrowRight size={13}/></div>)}</div>
    <div className="funds-destination"><Stethoscope size={25} strokeWidth={1.2}/><span className="mono">VALUE RETURNED</span><strong>Physicians<br/>&amp; practices</strong></div>
  </div>;
}

export function PhysicianValueSection({motion,onExploreCare}) {
  const [selected,setSelected]=useState(0),[visible,setVisible]=useState(false);
  const root=useRef(null),stream=streams[selected];
  useEffect(()=>{const observer=new IntersectionObserver(([entry])=>setVisible(entry.isIntersecting));if(root.current)observer.observe(root.current);return()=>observer.disconnect()},[]);
  return <section className="physician-value" id="physician-value" ref={root}>
    <span id="economics" className="funds-anchor"/>
    <div className="physician-value-top mono"><span>VBC 3.0 / COMPLETE THE VALUE LOOP</span><span>BETTER LIVES. STRONGER PRACTICES.</span></div>
    <div className="physician-value-heading"><h2>Pay for the work.<br/><em>Share the value.</em></h2><p>When physicians help people live better, the economics should work for them, too. Fund the care. Recognize the contribution. Make sure the value reaches the practice.<br/><a className="rewards-jump" href="#care-rewards">Try the care rewards experience <ArrowRight size={15}/></a></p></div>
    <div className="funds-instrument">
      <div className="funds-instrument-head mono"><span><i/>FOLLOW THE FUNDS</span><span>PAYER → CARE → PHYSICIAN</span></div>
      <FundsDiagram selected={selected} moving={motion&&visible}/>
      <div className="funds-return"><Repeat2 size={17}/><p>Stronger practices <ArrowRight size={13}/> More capacity to care <ArrowRight size={13}/> Better outcomes</p></div>
    </div>
    <CareRewards/>
    <div className="funds-explorer">
      <div className="funds-controls"><span className="mono">EXPLORE HOW VALUE REACHES THE PRACTICE</span><div role="group" aria-label="Explore physician payment streams">{streams.map((s,i)=><button key={s.name} onClick={()=>setSelected(i)} aria-pressed={selected===i}><span className="mono">0{i+1}</span>{s.name}<ArrowUpRight size={17}/></button>)}</div></div>
      <div className="funds-detail" aria-live="polite" aria-atomic="true" key={selected}><span className="mono">{stream.kicker}</span><h3>{stream.title}</h3><p>{stream.text}</p><dl><div><dt>Payment basis</dt><dd>{stream.basis}</dd></div><div><dt>Contribution</dt><dd>{stream.contribution}</dd></div><div><dt>Timing</dt><dd>{stream.timing}</dd></div></dl></div>
    </div>
    <div className="funds-promise"><span className="mono">EVERY PHYSICIAN SHOULD BE ABLE TO SEE</span><p>What I earned.<br/><span>Why I earned it.</span><br/><em>When I get paid.</em></p><a href="#journey" onClick={onExploreCare}>Back to the care &amp; value journey <ArrowUpRight size={16}/></a></div>
  </section>;
}
