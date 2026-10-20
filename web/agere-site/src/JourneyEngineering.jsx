import React from 'react';

export const clinicalSignals = [
 ['Source records', 'Resolve identity', 'Patient context'],
 ['Notes + history', 'Link documented EF', 'HFrEF review'],
 ['Meds + labs', 'Check therapy + dose', 'Prioritized gaps'],
 ['Patient context', 'Match eligibility', 'PharmD pathway'],
 ['Eligible patient', 'Coordinate outreach', 'Visit scheduled'],
 ['Latest response', 'Protocol + PharmD', 'Next dose step'],
 ['RPM + home labs', 'Detect change', 'Care-team response'],
 ['Care + follow-up', 'Track the response', 'Days at home'],
];
export const financialSignals = [
 ['Source records', 'Resolve the question', 'Financial context'],
 ['Claims + payments', 'Align dates + scope', 'Linked transactions'],
 ['Scoped transactions', 'Compute the total', 'Traceable answer'],
 ['Contract + costs', 'Model the exposure', 'Settlement range'],
 ['Practice contribution', 'Allocate savings', 'Physician payment'],
 ['Financial answer', 'Connect the context', 'Next decision'],
];

// Native linework supplies a coordinate system for the live data, not a scenery layer.
export function EngineeringField({disconnected=false,finale=false}) {
 return <div className={'engineering-field'+(disconnected?' is-disconnected':'')+(finale?' engineering-field-finale':'')} aria-hidden="true">
  <div className="engineering-grid"/>
  <svg viewBox="0 0 720 460" preserveAspectRatio="none">
   <g className="engineering-frame" fill="none">
    <path d="M18 54V18H70 M650 18H702V54 M18 406V442H70 M650 442H702V406"/>
    <path d="M14 230H30 M22 222V238 M690 230H706 M698 222V238 M352 18H368 M360 10V26 M352 442H368 M360 434V450"/>
    <path className="engineering-axis" d="M46 230H674 M360 40V420"/>
   </g>
   <g className="engineering-bus" fill="none">
    <path d="M80 382H220L250 412H470L500 382H640"/>
    <path d="M80 78H220L250 48H470L500 78H640"/>
   </g>
   <g className="engineering-packets" fill="none">
    <path d="M80 382H220L250 412H470L500 382H640"/>
    <path d="M640 78H500L470 48H250L220 78H80"/>
   </g>
   {[80,220,500,640].map((x,i)=><g key={x} className="engineering-terminal"><rect x={x-3} y={i%2?379:75} width="6" height="6"/></g>)}
  </svg>
 </div>
}

export function JourneySignalRail({signals}) {
 return <div className="journey-signal-rail" aria-label="How this step works">
  {signals.map((text,i)=><React.Fragment key={i}><div className={i===1?'signal-operation':''}><span className="mono">{['INPUT','AGERE','OUTPUT'][i]}</span><strong>{text}</strong></div>{i<2&&<span className="signal-arrow" aria-hidden="true">→</span>}</React.Fragment>)}
 </div>
}
