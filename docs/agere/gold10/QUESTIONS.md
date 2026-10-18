# Gold 10: the locked customer question set (draft for agreement, 2026-10-18)

Data: v2.12 native sample (the files a customer delivers). as_of = the slice's as_of date. Every answer is a patient
list in the customer's own identifiers plus, where asked, a routed action plan. Nothing counts toward a score until
Agere's derivation and the generator's derivation agree on the patient list.

Shared building blocks
- HF: any delivered diagnosis I50.* (problem list, encounter or claim).
- Active medication: on the delivered medication list (not discontinued) OR a paid fill whose days supply covers any day
  in the 120 days before as_of.
- GDMT pillars: (1) evidence-based HF beta blocker (carvedilol, metoprolol succinate, bisoprolol); (2) ACEi, ARB or
  sacubitril/valsartan; (3) MRA (spironolactone, eplerenone); (4) SGLT2 inhibitor (dapagliflozin, empagliflozin,
  sotagliflozin).
- ED visit: payer claim with place of service 23, revenue code 045x, or CPT 99281-99285; or an EHR emergency encounter.
  Discharged home = not followed by an inpatient admission starting the same or next day.
- PT: CPT 97161-97164, 97110, 97112, 97140, 97530 on any delivered claim.
- Active coverage: an enrollment span (primary, non-medigap, non-COB) covering as_of.

1. HF-GDMT. Heart failure patients alive at as_of, each with which of the 4 GDMT pillars they are on; list those
   missing one or more pillars.
2. LBP-PT. Patients with an ED visit in the 12 months before as_of with a low back pain diagnosis (M54.5*) on that
   visit, discharged home, and no PT code with service in the 60 days after the visit.
3. PLAN. For the patients in 1 and 2: route each to the pharmacy GDMT program (1) or a virtual PT vendor in the CMS
   ACCESS model (2), with active coverage, payer/product, attributed PCP or practice, and the specific gap (missing
   pillars; days since ED visit). Patients without active coverage go to a coverage-check queue instead. Checked fields:
   routing, coverage, payer, attribution, gap.
4. A1C-9. Patients with diabetes (E10/E11) whose latest A1c in the 12 months before as_of is > 9.0%, resulted at least
   30 days before as_of, and who had no visit (completed office/virtual encounter or billed CPT 99202-99215) on days 1-30.
5. ED-HIGH. Patients with 4 or more distinct ED visit dates in the 12 months before as_of, with their top 3 ED
   diagnoses.
6. TCM-GAP. Inpatient discharges (home or home health) in the 6 months before as_of with no office or virtual visit
   within 14 days after discharge.
7. SPD. Patients 40-75 at as_of with diabetes and no active statin.
8. PHQ-FU. Patients with a PHQ-9 total >= 10 in the 12 months before as_of with no office, virtual or behavioral
   health visit within 30 days after it.
9. BCS. Women 52-74 at as_of with active coverage and no mammogram (claim or EHR result) in the 27 months before as_of.
10. PDC. Patients with 2 or more fills of a statin in the calendar year before as_of whose statin PDC for that year is
   below 80% (each class separately, overlaps not shifted, prior-year supply counts for days it covers).


Current wording lives in questions.jsonl (agreed with the generator 2026-10-18). Locked: LBP-PT, TCM-GAP, BCS, PDC (locked.json).
