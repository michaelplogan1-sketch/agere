// Statin therapy for patients with diabetes: received statin therapy
// Governed CQL for the spd_statin_diabetes measure-library entry (an approximation of the public specification, not a certified measure).
// Reference for parity: config/agere/measures/spd_statin_diabetes.json (check() compares this logic with that engine).
library StatinTherapyDiabetes version '1.0.0'

valueset "Diabetes": {"type": "condition", "concepts": ["diabetes", "type_2_diabetes", "type_1_diabetes"], "icd10_prefixes": ["E10", "E11", "E13"]}
valueset "ASCVD": {"type": "condition", "concepts": ["coronary_artery_disease", "myocardial_infarction", "stroke"], "icd10_prefixes": ["I20", "I21", "I22", "I23", "I24", "I25", "I63", "I65", "I66", "I70", "I73.9", "I74", "Z95.1", "Z95.5", "Z98.61"]}
valueset "Hospice": {"type": "service", "streams": [{"stream": "hospice_enrollments"}], "hcpcs": ["G9473", "G9474", "G9475", "G9476", "G9477", "G9478", "G9479", "Q5003", "Q5004", "Q5005", "Q5006", "Q5007", "Q5008", "Q5010", "S9126", "T2042", "T2043", "T2044", "T2045", "T2046"]}
valueset "Palliative care": {"type": "condition", "icd10_prefixes": ["Z51.5"]}
valueset "ESRD or dialysis": {"type": "condition", "icd10_prefixes": ["N18.6", "Z99.2"]}
valueset "Statin": {"type": "medication", "classes": ["statin"], "evidence": ["dispensed", "claim", "payer_claim"]}

parameter "Measurement Period" Interval<Date> default Interval[@2029-06-01, @2030-05-27]

define "Initial Population":
  AgeInYearsAt(end of "Measurement Period") between 40 and 75
  and EnrolledThroughout("Measurement Period", 1)
  and exists ([Condition: "Diabetes"] C where (C.date during Interval[start of "Measurement Period" - 12 months, end of "Measurement Period"] or C.date is null))

define "Denominator": "Initial Population"

define "Denominator Exclusion":
  exists ([Condition: "ASCVD"] X where (X.date during Interval[start of "Measurement Period" - 12 months, end of "Measurement Period"] or X.date is null))
  or exists ([Procedure: "Hospice"] X where X.date during Interval[start of "Measurement Period", end of "Measurement Period"])
  or exists ([Condition: "Palliative care"] X where (X.date during Interval[start of "Measurement Period", end of "Measurement Period"] or X.date is null))
  or exists ([Condition: "ESRD or dialysis"] X where (X.date during Interval[start of "Measurement Period" - 12 months, end of "Measurement Period"] or X.date is null))

define "Numerator":
  exists ([MedicationDispense: "Statin"] M where M.date during Interval[start of "Measurement Period", end of "Measurement Period"])
