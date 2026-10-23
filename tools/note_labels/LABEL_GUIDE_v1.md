# Labeling guide for note-frame passages

You read one passage from a (synthetic) clinical record and say what it states about the **patient** regarding a named concept.
Use only the passage. Do not use outside medical knowledge to fill gaps. Every item is labeled with:

- `status`: exactly one of the seven below.
- `detail`: only when status is documented, possible, planned_not_done or historical: the fields listed for the item's frame.
  Give a field only when the passage states it; omit it otherwise. Use the exact option names.
- `quote`: an exact, contiguous substring of the passage (at most 200 characters) that supports the status. Empty for not_mentioned.

## Statuses (one per passage; pick the single best)
- documented: The passage states that the PATIENT has, had in this encounter, did, or received the thing now: an affirmative statement
  about the patient that is current as of the note date (or an event the note reports as having occurred at or just before the note date).
- denied: The passage explicitly states the thing is absent or did not happen for the patient: "denies", "no", "not taking",
  "negative for", "does not recognize this as a current medicine", "NKDA". Silence is not denial.
- possible: The passage is uncertain whether the patient has or did it: suspected, probable, rule-out pending, differential,
  "concern for", "may have", "cannot exclude", a question that remains open.
- planned_not_done: The passage recommends, orders, schedules, offers, requests or intends the thing for the patient but does not show
  it carried out, including offered-and-declined, due, overdue, or deferred.
- historical: The passage places the thing in the patient's past and not as current at the note date: "history of", "resolved",
  "previously", "in 2027", "was on X until March", a closed episode.
- other_person: The passage is about someone other than the patient (family member, relative, caregiver, household member).
- not_mentioned: The passage says nothing about this concept for the patient: it only matched a word by accident, or it is only a
  hypothetical or conditional mention ("return if fever", "evaluate for X", risk teaching) that does not state the patient has it.

Tie rules: if the passage contains several statements about the concept, label the one that answers "what does this passage say about
the patient and the concept at the note's date" - the most specific, most recent statement wins. A drug or test merely listed with no
statement about the concept's event (e.g. a medication list line when the concept is a medication event) is `documented` for a
medication/condition list (the patient has it listed) only if the frame asks about having it; for an EVENT frame (stopped, started,
changed) a bare list line with no event is `documented` with no event detail. A problem-list or "history on file" line naming a chronic
current condition without "history of ... resolved" is `documented`. A prior-encounter recollection ("Prior entry dated 2026-04-09:
..., plan then ...") is `historical`.

## Detail fields by frame (closed options; multi = list, may hold several)
- condition: course {new,in_remission,persistent,worsening,improving,stable,resolved,intermittent,unspecified};
  assessment (multi) {monitored,evaluated,assessed,treated}
- symptom: severity {mild,moderate,severe,unspecified}; course {as above}
- medication_event: event {started,stopped,changed_dose,held,switched,continued};
  reason (multi) {side_effect,cost,ineffective,patient_choice,contraindication,course_completed,clinician_decision,no_longer_indicated,other_with_quote};
  adherence {taking_as_directed,taking_differently,not_taking,not_stated}
- procedure_test: category {imaging,laboratory,pathology_biopsy,endoscopy,cardiac_test,surgery_procedure,screening,other};
  result {normal,abnormal,indeterminate,pending,not_reported}
- finding_value: value (the number as written, e.g. "7.4"); interpretation {normal,abnormal,high,low,at_goal,not_at_goal,not_stated}
- adverse_event: kind {allergy,intolerance,side_effect,procedural_complication,fall,medication_error,hospital_acquired,other};
  severity {mild,moderate,severe,life_threatening,unspecified}
- care_process: kind {referral,education_counseling,goals_of_care_discussion,advance_directive,code_status,care_plan,care_coordination,
  transition_of_care,medication_reconciliation,discharge_instruction,follow_up_visit,palliative_hospice,home_health,rehabilitation,
  screening_performed,immunization_given,case_management,interpreter_used,other};
  outcome {completed,ordered_or_referred,declined,unable,deferred}
- social_need: domain (multi) {food_insecurity,housing_instability,homelessness,inadequate_housing,transportation_insecurity,
  utility_insecurity,financial_strain,medical_cost_burden,material_hardship,employment,education_literacy,social_isolation,stress,
  interpersonal_violence,elder_abuse,digital_access,language_barrier,legal,child_care,caregiver_strain,caregiver_role,incarceration,
  veteran_status,substance_use,health_insurance,discrimination,other};
  response {referral_made,resources_provided,counseling,screened_only,patient_declined,none_documented}
- function_cognition: domain {mobility_gait_balance,self_care_adl,instrumental_adl,falls,memory_cognition,vision,hearing,
  swallowing_speech,continence,frailty,home_safety,mood,caregiver_support,other};
  level {independent,needs_some_help,dependent,impaired,unspecified}
- preference_refusal: decision {accepted,declined,deferred,undecided,requested};
  reason (multi) {cost,side_effect_concern,belief_or_values,logistics_transport,prefers_alternative,not_ready,family_decision,
  comfort_focused_goals,other_with_quote}
- plan_followup: item {follow_up_visit,referral,test_order,medication_plan,monitoring,call_back,return_precautions,education,disposition};
  disposition {home,home_health,skilled_nursing,inpatient_rehab,hospice,admitted,transferred,left_against_advice,expired,other,not_stated}

## Output
A JSON list, one object per item, same order as the input:
`[{"id": "cond-001", "status": "historical", "detail": {"course": "unspecified"}, "quote": "Chronic congestive heart failure (disorder)"}, ...]`
