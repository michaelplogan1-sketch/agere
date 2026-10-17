# Request to the generator: 10 clinical workflow tasks with sealed patient-level truth

**From:** Agere. **For:** the v2.8 sample (artifacts/healthcare-warehouse-v2.8-sample-0.30) first, then later releases.
**Approved by:** Mike, 2026-10-17.

## Why
The aggregate-count questions have kept failing on unstated conventions: counting units, labels, which version counts.
Clinical care-gap workflows are defined by guidelines, scored per patient (precision/recall, so one edge case doesn't
zero a whole question), and they exercise the layers that matter: structured meds and doses, free-text notes, guideline
knowledge and multi-step reasoning. They are also what buyers want.

## Ask
For each task, sealed truth computed from the simulation, at two levels:
- **truth:** what is true in the simulated patient (e.g. a true intolerance);
- **delivered:** what is recoverable from the delivered data (structured fields or note text). Mark the answer
  `not_recoverable` where the truth was never documented.

Agere is scored against **delivered**; **truth** is context only. Patient ids are warehouse patient_ids, with duplicate
charts mapped to their person.

Each task is a numbered set of steps. Scoring is per step: a patient set gets precision/recall/F1; per-patient
attributes (missing pillar, reason category, drug, dose) get per-patient accuracy over the true positives.

| # | Task | Steps and outputs |
|---|---|---|
| 1 | HFrEF GDMT gaps | (a) HFrEF patients (HF diagnosis + EF ≤ 40% or HFrEF code) as of as_of. (b) For each, which of the 4 pillars they are NOT on: evidence-based beta-blocker (carvedilol, bisoprolol, metoprolol succinate), ACEi/ARB/ARNI, MRA, SGLT2i. "On" = an active picked-up fill covering as_of or within the last 90 days. |
| 2 | Documented reasons for GDMT gaps | For each patient-pillar gap from task 1: is a reason documented, and its category: intolerance/adverse effect, hyperkalemia, hypotension, AKI/renal, bradycardia, patient refusal, cost/access, contraindication (other), none documented. Note where the reason appears (structured allergy/problem vs note text only). |
| 3 | GDMT under-dosing | HFrEF patients on a GDMT drug below the ACC/AHA target dose (state the target table used), with no documented reason for not titrating. Output: patient, drug, current daily dose, target daily dose. |
| 4 | Diabetes cardio-renal protection gap | Type 2 diabetes with ASCVD or CKD not on an SGLT2i or GLP-1 RA, plus the documented reason category, as in task 2. |
| 5 | AFib anticoagulation gap | AFib with CHA₂DS₂-VASc ≥ 2 (state the components used) not on an oral anticoagulant, plus the documented contraindication category (bleeding, falls, other, none). |
| 6 | CKD protection gap | CKD stage 3+ with albuminuria (UACR ≥ 30 mg/g) not on an ACEi/ARB, and separately not on an SGLT2i. |
| 7 | Statin gap | Statin-indicated patients (ASCVD; diabetes aged 40–75; LDL ≥ 190) not on a statin, and the subset with documented statin intolerance. |
| 8 | Post-MI secondary prevention | MI in the prior 12 months not on a beta-blocker, and/or not on a high-intensity statin (atorvastatin 40–80 / rosuvastatin 20–40). |
| 9 | Hyperkalemia safety | On an active MRA with latest potassium ≥ 5.5 mmol/L. Output: patient, latest K, date. |
| 10 | Notes-versus-orders safety conflict | A drug with a documented intolerance or allergy (structured or in a note) that is still being ordered or filled after the documentation date. Output: patient, drug, documentation date, latest fill or order date. |

## Format
- **Public:** `releases/<rel>/questions/clinical_tasks.jsonl`. One task per line, with plain-language task text, the
  definition per step and the output schema.
- **Sealed:** answers in `private/eval_questions/clinical_tasks_answers.jsonl`.
- **Scoring:** `python3 -m ecosystem.eval_scorer --release <rel> --clinical-tasks <answers.jsonl> --sample-release <dir>`
  returns per-task, per-step aggregates (P/R/F1, attribute accuracy) and nothing patient-level beyond correct or
  incorrect.
- **Independence:** the generator writes the truth from its own simulation. Agere never sees it and writes no part of
  it. If a task needs a convention the data doesn't support, say so in the definition rather than leaving it implicit.
