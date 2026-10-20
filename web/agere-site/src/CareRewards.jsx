import React, {useEffect,useRef,useState} from 'react';
import {ArrowRight,Check,LockKeyhole,RotateCcw,Sparkles,Trophy} from 'lucide-react';
import {careActions,rewardSummary} from './rewardsModel.js';

const money=value=>`$${value.toLocaleString('en-US')}`;

export function CareRewards(){
  const [completed,setCompleted]=useState(0),[announcement,setAnnouncement]=useState('');
  const buttons=useRef([]),reset=useRef(null),restoreFocus=useRef(false);
  const earned=rewardSummary(completed),finished=completed===careActions.length;
  const nextBonus=completed<3?50:150,nextMilestone=completed<3?3:6;
  useEffect(()=>{if(restoreFocus.current){(finished?reset.current:buttons.current[completed])?.focus({preventScroll:true});restoreFocus.current=false}},[completed,finished]);
  function complete(index){
    if(index!==completed)return;
    const next=rewardSummary(completed+1),bonusDelta=next.bonuses-earned.bonuses;
    setAnnouncement(`${careActions[index].title} complete. ${money(careActions[index].reward)} action reward${bonusDelta?`, plus ${money(bonusDelta)} milestone bonus`:''}. Example total ${money(next.total)}.`);
    restoreFocus.current=true;
    setCompleted(count=>count===index?count+1:count);
  }
  function restart(){restoreFocus.current=true;setCompleted(0);setAnnouncement('Example restarted. Rewards reset to zero.')}
  return <section className="care-rewards" id="care-rewards" aria-labelledby="care-rewards-title">
    <div className="rewards-heading"><div><span className="mono">TURN FOLLOW-THROUGH INTO FORWARD MOMENTUM</span><h3 id="care-rewards-title">Better care.<br/><em>Rewarded at every step.</em></h3></div><p>Rewards are tied to completed, verified care steps recorded by the agents—a fill, a visit, a closed gap—not to clicks. Completed work builds reimbursement and unlocks bonuses for seeing the pathway through.</p></div>
    <div className="rewards-board">
      <div className="rewards-board-head"><span className="mono">AGERE / CARE REWARDS</span><span className="rewards-example mono">INTERACTIVE EXAMPLE · ILLUSTRATIVE AMOUNTS</span></div>
      <div className="rewards-mobile-progress"><span className="mono">EXAMPLE REWARDS</span><strong>{money(earned.total)}</strong><span>{completed} / 6 complete</span></div>
      <div className="rewards-board-body">
        <div className="rewards-actions">
          <div className="rewards-path-heading"><div><span className="mono">HEART-FAILURE PATHWAY</span><h4>Six steps. One connected journey.</h4></div><span className="rewards-count mono">{completed} / 6</span></div>
          <p className="rewards-instruction">Use the example controls to explore rewards for verified care steps.</p>
          <ol>{careActions.map((action,index)=>{
            const done=index<completed,active=index===completed;
            return <li key={action.title} className={done?'is-done':active?'is-next':'is-locked'}>
              <span className="reward-step-number" aria-hidden="true">{done?<Check size={14}/>:String(index+1).padStart(2,'0')}</span>
              <div className="reward-action-copy"><span className="mono">{action.tool}</span><h5>{action.title}</h5><p>{action.text}</p></div>
              <div className="reward-action-complete"><strong>+{money(action.reward)}</strong><button ref={el=>buttons.current[index]=el} onClick={()=>complete(index)} disabled={!active} aria-label={done?`${action.title} completed`:`Complete example step ${index+1}: ${action.title}`}><span>{done?'Completed':active?'Complete':'Up next'}</span>{done?<Check size={12}/>:active?<ArrowRight size={13}/>:<LockKeyhole size={11}/>}</button></div>
            </li>;
          })}</ol>
        </div>
        <aside className="rewards-wallet" aria-label="Example practice rewards">
          <div className="rewards-level"><span className="mono">{finished?'PATHWAY COMPLETE':completed>=3?'CARE CONNECTED':completed?'MOMENTUM BUILDING':'YOUR NEXT OPPORTUNITY'}</span>{finished?<Trophy size={17}/>:<Sparkles size={17}/>}</div>
          <div className="reward-cubes" aria-hidden="true">{Array.from({length:36},(_,i)=><i className={i<completed*6?'is-earned':''} key={i} style={{'--tile-delay':`${i%6*35}ms`}}/>)}</div>
          <span className="rewards-wallet-label mono">YOUR EXAMPLE REWARDS</span>
          <strong className="rewards-total" key={earned.total}>{money(earned.total)}</strong>
          <p className="rewards-total-caption">{finished?'A complete pathway. A visible reward.':'Every completed care step moves you forward.'}</p>
          <dl className="rewards-ledger"><div><dt>Action rewards</dt><dd>{money(earned.actions)}</dd></div><div><dt>Milestone bonuses</dt><dd>{money(earned.bonuses)}</dd></div><div><dt>Example total</dt><dd>{money(earned.total)}</dd></div></dl>
          <div className="rewards-goal"><div><span className="mono">PATHWAY PROGRESS</span><span>{completed} of 6 complete</span></div><progress max="6" value={completed} aria-label="Care pathway completion"/><p>{finished?<><Trophy size={15}/> All milestones unlocked</>:<><LockKeyhole size={14}/>{nextMilestone-completed} more {nextMilestone-completed===1?'step':'steps'} to a {money(nextBonus)} bonus</>}</p></div>
          <div className="reward-milestones"><div className={completed>=3?'is-unlocked':''}>{completed>=3?<Check size={14}/>:<LockKeyhole size={13}/>}<span>Care connected <small>3 steps complete</small></span><strong>+$50</strong></div><div className={finished?'is-unlocked':''}>{finished?<Check size={14}/>:<LockKeyhole size={13}/>}<span>Follow-through complete <small>6 steps complete</small></span><strong>+$150</strong></div></div>
          <button className="rewards-reset" ref={reset} onClick={restart} disabled={completed===0}><RotateCcw size={13}/>Restart example</button>
        </aside>
      </div>
      <div className="rewards-feedback" role="status" aria-live="polite" aria-atomic="true"><span className="reward-feedback-dot"/>{announcement||'Illustrative walkthrough. In the product, agent-recorded evidence verifies each completed step.'}</div>
    </div>
    <div className="rewards-principle"><span className="mono">THE AGERE REWARDS VISION</span><p>Clear next actions. Visible progress. Timely rewards.<br/><strong>A stronger practice every time the care loop closes.</strong></p></div>
  </section>;
}
