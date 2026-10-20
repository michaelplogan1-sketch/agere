export const patientMoments = [
  {
    day: 0, label: 'Find her', title: 'A goal worth organizing care around.',
    story: 'Elena is 67. In this fictional story, heart failure has made ordinary days harder: breathlessness, interrupted sleep and two recent hospital stays. She wants to walk to her garden without stopping so often.',
    lived: 'The garden feels farther away.', signal: 'A baseline, including what matters to Elena',
    actions: [
      ['Agent prepares', 'Connect the EF history, admissions and medication evidence.', 'Case assembled'],
      ['Care team reviews', 'Confirm the HFrEF phenotype and identify unresolved therapy questions.', 'Review needed'],
      ['Elena participates', 'Record her symptoms, actual medication use and personal goal.', 'Listen first'],
    ],
    medications: [ ['ARNI / ACEi / ARB', 'Reconcile the regimen', 'review'], ['Evidence-based β-blocker', 'Confirm actual use', 'review'], ['MRA', 'Review eligibility and labs', 'review'], ['SGLT2 inhibitor', 'Review eligibility', 'review'] ],
    checkpoints: ['Confirm the heart-failure phenotype and the timing of EF evidence.', 'Reconcile actual use, formulation, dose, access and prior intolerance.', 'Agree on a personal goal and record symptoms before comparing change.'],
  },
  {
    day: 7, label: 'Make a plan', title: 'One plan she can actually follow.',
    story: 'Her pharmacist reconciles what she takes, resolves an access barrier and reviews the clinical context with the prescribing team. Elena leaves with an agreed medication plan and a clear way to ask for help.',
    lived: 'The plan starts to make sense.', signal: 'Home monitoring connected in this illustration',
    actions: [
      ['Agent prepares', 'Bring therapy gaps, access needs and monitoring into one review packet.', 'Proposal ready'],
      ['Care team authorizes', 'Agree on appropriate therapy and record approved prescriptions.', 'Plan approved'],
      ['Agent follows through', 'Coordinate access, explain the agreed plan and arrange follow-up.', 'Follow-up set'],
    ],
    medications: [ ['ARNI / ACEi / ARB', 'Reviewed regimen started', 'start'], ['Evidence-based β-blocker', 'Stable before initiation', 'start'], ['MRA', 'Start approved after review', 'start'], ['SGLT2 inhibitor', 'Start approved after review', 'start'] ],
    checkpoints: ['Confirm clinical stability, contraindications and relevant vital signs and labs.', 'Clinical decisions are authorized through the care team’s prescribing arrangements.', 'Teach device use and explain symptom reporting, escalation and laboratory follow-up.'],
  },
  {
    day: 21, label: 'Adjust therapy', title: 'Titration, with someone watching.',
    story: 'The agent brings the latest symptoms, home readings and laboratory results into the next review. It prepares a beta-blocker dose-increase proposal. The pharmacist reviews it; an authorized change and follow-up plan are recorded.',
    lived: 'A check-in becomes a supported next step.', signal: 'Illustrative review: tolerability supports the next step',
    actions: [
      ['Agent proposes', 'Prepare the next beta-blocker step, with the supporting evidence.', 'Increase proposed'],
      ['Care team authorizes', 'Review stability and tolerance; approve the appropriate change.', 'Change approved'],
      ['Agent coordinates', 'Communicate the authorized plan and schedule a tolerance check.', 'Check-in scheduled'],
    ],
    medications: [ ['ARNI / ACEi / ARB', 'Continue; assess tolerance', 'maintain'], ['Evidence-based β-blocker', 'Dose increase authorized', 'titrate'], ['MRA', 'Laboratory follow-up reviewed', 'maintain'], ['SGLT2 inhibitor', 'Continue; assess tolerance', 'maintain'] ],
    checkpoints: ['Review blood pressure, pulse, symptoms, volume status and relevant laboratory evidence.', 'Consider prior intolerance, patient preferences and the drug-specific regimen.', 'An agent proposal does not change a prescription. An authorized clinician must approve it.'],
  },
  {
    day: 35, label: 'Listen & adapt', title: 'A symptom changes the next step.',
    story: 'Elena reports lightheadedness. The agent flags it and brings the planned next increase back for review. That increase waits while the care team checks symptoms, readings and medicines and agrees on closer follow-up.',
    lived: 'Someone notices when the plan needs to slow down.', signal: 'Tolerance concern → review before further escalation',
    actions: [
      ['Agent flags', 'Connect the new symptom report with the recent medication change.', 'Review requested'],
      ['Care team reassesses', 'Defer the proposed increase and review the current regimen.', 'Increase deferred'],
      ['Agent coordinates', 'Arrange the agreed check-in and track the unresolved concern.', 'Closer follow-up'],
    ],
    medications: [ ['ARNI / ACEi / ARB', 'Review symptom context', 'review'], ['Evidence-based β-blocker', 'Further increase deferred', 'pause'], ['MRA', 'Safety context reviewed', 'review'], ['SGLT2 inhibitor', 'Tolerance reviewed', 'review'] ],
    checkpoints: ['The proposed escalation is paused; the medication is not automatically stopped.', 'The care team evaluates severity and urgency and directs any treatment changes.', 'A symptom report cannot be dismissed because a single device reading looks reassuring.'],
  },
  {
    day: 60, label: 'Build a routine', title: 'Care starts fitting into her life.',
    story: 'The plan reflects Elena’s response. Her team reviews an adjustment to the ARNI regimen while maintaining the currently tolerated beta-blocker plan. Monitoring and follow-up continue. She reports fewer pauses around the house.',
    lived: 'Ordinary tasks begin to feel more manageable.', signal: 'Symptoms and everyday function remain part of the review',
    actions: [
      ['Agent prepares', 'Summarize response, medication history and remaining review opportunities.', 'Response reviewed'],
      ['Care team authorizes', 'Record a patient-specific adjustment and maintain the tolerated plan.', 'Plan updated'],
      ['Elena participates', 'Report what is easier, what is still difficult and what she prefers.', 'Goals revisited'],
    ],
    medications: [ ['ARNI / ACEi / ARB', 'Adjustment authorized', 'titrate'], ['Evidence-based β-blocker', 'Maintain tolerated plan', 'maintain'], ['MRA', 'Continue with monitoring', 'maintain'], ['SGLT2 inhibitor', 'Continue with monitoring', 'maintain'] ],
    checkpoints: ['The trajectory is individual; real timing and treatment response vary.', 'Current tolerated therapy is not automatically the maximum tolerated dose.', 'Medication changes and monitoring requirements differ by drug and class.'],
  },
  {
    day: 90, label: 'Live more', title: 'Back to something that matters.',
    story: 'By day 90 in this fictional story, Elena says the walk to her garden feels manageable again. Compared with the fictional prior period, these 90 days include fewer hospital stays and more days at home. The care team keeps following her; the story does not end at a dose target.',
    lived: 'A walk to the garden. A little more of her own life.', signal: 'The goal: feeling better and spending more time at home',
    actions: [
      ['Agent assembles', 'Bring medication changes, symptom reports and care events into review.', 'Follow-up summarized'],
      ['Care team reviews', 'Assess response and agree on the next ongoing-care plan.', 'Review continues'],
      ['Elena defines progress', 'Return to the personal goal that started the journey.', 'Life beyond the chart'],
    ],
    medications: [ ['ARNI / ACEi / ARB', 'Maintain reviewed regimen', 'maintain'], ['Evidence-based β-blocker', 'Maintain tolerated plan', 'maintain'], ['MRA', 'Continue with monitoring', 'maintain'], ['SGLT2 inhibitor', 'Continue with monitoring', 'maintain'] ],
    checkpoints: ['All dates, decisions and outcomes in Elena’s story are invented for this illustration.', 'The source-backed synthetic patient later on this page is a separate case.', 'These outcomes are not an Agere effectiveness result, a forecast or a promise for an individual.'],
  },
];
export const illustrativeOutcomes = {
  before: {label:'Previous 90 days',home:82,hospital:8,admissions:2,symptoms:'Frequent pauses',description:'Breathlessness interrupts the walk to her garden.',hospitalDays:[8,9,10,11,61,62,63,64]},
  after: {label:'Illustrative next 90 days',home:90,hospital:0,admissions:0,symptoms:'A manageable walk',description:'Elena reports that the garden feels within reach again.',hospitalDays:[]},
};
