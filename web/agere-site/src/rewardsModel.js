export const careActions = [
  {title:'Review the HFrEF opportunity',tool:'Population intelligence',text:'Connect the reduced-EF finding to the patient’s history and confirm the care opportunity.',reward:20},
  {title:'Resolve the medication review',tool:'Medication reconciliation',text:'Review therapy, dose and tolerance. Document the appropriate next step, including a reason to hold when needed.',reward:40},
  {title:'Complete the coordinated care plan',tool:'Pathway matching',text:'Match the patient’s needs and preferences to an eligible program, with consent and a clear team handoff.',reward:60},
  {title:'Close the appointment loop',tool:'Scheduling agent',text:'Help the patient attend the PharmD visit and capture the resulting care plan.',reward:80},
  {title:'Review the home response',tool:'RPM + home labs',text:'Bring home readings, symptoms and lab results into a documented care-team review.',reward:100},
  {title:'Complete the follow-through',tool:'Titration + outcome review',text:'Verify weeks later that the fill or visit happened. Reopen the gap if needed, and record the patient’s response.',reward:100},
];

// Local, illustrative program arithmetic. No clinical or payment system is called.
export function rewardSummary(completedCount) {
  const count=Math.max(0,Math.min(careActions.length,Math.floor(Number(completedCount)||0)));
  const actions=careActions.slice(0,count).reduce((sum,action)=>sum+action.reward,0);
  const coordinationBonus=count>=3?50:0,followThroughBonus=count===6?150:0;
  return {count,actions,coordinationBonus,followThroughBonus,bonuses:coordinationBonus+followThroughBonus,total:actions+coordinationBonus+followThroughBonus};
}
