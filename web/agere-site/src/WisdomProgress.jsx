import React from 'react';

export const wisdomPhases=[
 {name:'Data',question:'What do we have?',start:0,clinical:'Connect the fragments of a person’s story.',financial:'Bring records, transactions and contracts together.'},
 {name:'Intelligence',question:'What does it reveal?',start:1,clinical:'Find the patients and care gaps that need attention.',financial:'Reveal patterns and calculate what is happening.'},
 {name:'Knowledge',question:'What does it mean?',start:3,clinical:'Connect evidence and context to a plan that fits.',financial:'Understand the context, assumptions and consequences.'},
 {name:'Wisdom',question:'What should we do?',start:5,clinical:'Act with the care team. Learn from each response.',financial:'Choose the next move with the tradeoffs in view.'},
];

export const phaseForStage=stage=>stage>=5?3:stage>=3?2:stage>=1?1:0;

export function WisdomProgress({stage,ids,clinical}){
 const active=phaseForStage(stage),phase=wisdomPhases[active];
 return <nav className="wisdom-progress" aria-label="From data to wisdom" data-phase={phase.name.toLowerCase()}>
  <ol>{wisdomPhases.map((item,i)=><li key={item.name} className={i<active?'is-reached':''}>
   <a href={'#'+ids[item.start]} aria-current={active===i?'step':undefined}>
    <strong>{item.name}</strong><span>{item.question}</span>
   </a>{i<3&&<span className="wisdom-arrow" aria-hidden="true">→</span>}
  </li>)}</ol>
  <p>{phase[clinical?'clinical':'financial']}</p>
 </nav>
}
