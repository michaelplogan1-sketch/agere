# Gold-50 independent-audit handoff — October 19, 2026

Prepared for the generator owner. **Diagnostics only: no new model run, no key access, no scoring, no validation claim.**
All SQL and source metadata are in [source_breakdowns.json](../../../artifacts/study/reference-audit-2026-10-19/source_breakdowns.json).
Reproduce with `.venv/bin/python bench/study/audit_gold50.py` from the Agere repository. This reads only the
pilot's delivered-data database. It imports no Agere compiler, specialist, scoring or provider code.

## Five pending disputes

- **QUA-G3:** the modeled claim denominator contains 64 qualifying
  claim rows but only 52 distinct person/admission dates.
  **12 rows lack admission dates.** Substituting service-from
  produces 64 stays. Raw 837 headers independently
  show 64 claim IDs and the same
  12 missing admission dates, all in deep-chart payers.
  This isolates the 52-versus-64 difference. Decide whether the definition explicitly licenses service-from as an
  admission-date fallback; do not silently treat it as a received admission date. No follow-up percentage is rescored.
- **HOS-G2:** raw HL7 A01/class-I messages yield 240 distinct
  person/date/facility admissions in 2029, with 0 unresolved
  identity mappings. 13 have no non-voided institutional
  claim admission within one day; allowing service-from fallback leaves that count unchanged. The packet reports
  each sending facility. Facility-group totals are diagnostic; final scoring must deduplicate across facilities if
  the question specifies only person/date.
- **VBC-G1:** active attributed denominators are MA-1=33, MD-0=28, TN-0=28, IN-0=27, OR-0=27. The disputed TN-0
  member remains present under modeled inclusive span ends. IN-0: 14 members have both EHR-office and payer E/M evidence,
  8 payer-only, 1 EHR-only, and 4 neither. TN-0: 25 payer-only, 3 neither. All five practices' splits are included.
  Provider-billing claims and raw span termination/delivery order still require independent reconciliation.
- **VBC-G3:** the modeled primary member-month table yields 3512 members
  with six months in each year and a top-decile size of 352.
  This differs from the generator's supplied 3,550/355 and should be resolved before a percentage comparison.
  Medigap/COB amounts are reported separately and excluded from the diagnostic eligibility filter. The packet also
  reports 423 latest-paid, in-metric DRG claims with zero
  modeled allowed amount in 2028-2029; 0 match raw 835
  payer-control IDs under the literal join. No remittance amount is imputed. Confirm delivered join keys/source
  coverage before using CLP04 + CAS PR to repair pricing.
- **QUA-G1:** the diagnostic diabetic cohort unions condition, encounter and payer diagnoses and applies delivered
  death records and birth-date age. No-A1c patients split as 137 claims-only, 11 claims-plus-EHR, 51 deep-chart
  (199 total). The packet includes source-specific A1c evidence for excluded patients (HL7, Epic, athena, eCW, FHIR).
  A patient may have multiple evidence sources; these source rows are not additive denominators.

## Requested independent disposition

Reconcile each discrepancy against delivered native records. Return rule/definition decisions, affected question IDs,
public digest metadata and correct/incorrect scorer access; do not disclose reference values, private files or code
that writes Agere answers. Previously upheld BH-G3/CLN-G1 corrections and REV-G3/BH-G2 wording disputes remain pending
sealing. RX-G3 remains a product interpretation error per the earlier verdict. Preserve all raw study answers.

All 41 questions need independent derivation and reconciliation before the set is eligible for a run or rescore.
Resolving these five disputes alone does not validate the remaining questions. Apply exclusions symmetrically, retain
failed derivations as disputes, and never select the final set according to which system wins.
