// Glycemic status assessment for patients with diabetes: control (<8.0%)
// Governed CQL for the gsd_control_lt8 measure-library entry (an approximation of the public specification, not a certified measure).
// Reference for parity: config/agere/measures/gsd_control_lt8.json (check() compares this logic with that engine).
library GlycemicStatusControlUnder8 version '1.0.0'

valueset "Diabetes": {"type": "condition", "concepts": ["diabetes", "type_2_diabetes", "type_1_diabetes"], "icd10_prefixes": ["E10", "E11", "E13"]}
valueset "Hospice": {"type": "service", "streams": [{"stream": "hospice_enrollments"}], "hcpcs": ["G9473", "G9474", "G9475", "G9476", "G9477", "G9478", "G9479", "Q5003", "Q5004", "Q5005", "Q5006", "Q5007", "Q5008", "Q5010", "S9126", "T2042", "T2043", "T2044", "T2045", "T2046"]}
valueset "Palliative care": {"type": "condition", "icd10_prefixes": ["Z51.5"]}
valueset "HbA1c": {"type": "measurement", "concept": "hba1c", "loinc": ["4548-4", "4549-2", "17855-8", "17856-6"]}

parameter "Measurement Period" Interval<Date> default Interval[@2029-06-01, @2030-05-27]

define "Initial Population":
  AgeInYearsAt(end of "Measurement Period") between 18 and 75
  and EnrolledThroughout("Measurement Period", 1)
  and exists ([Condition: "Diabetes"] C where (C.date during Interval[start of "Measurement Period" - 12 months, end of "Measurement Period"] or C.date is null))

define "Denominator": "Initial Population"

define "Denominator Exclusion":
  exists ([Procedure: "Hospice"] X where X.date during Interval[start of "Measurement Period", end of "Measurement Period"])
  or exists ([Condition: "Palliative care"] X where (X.date during Interval[start of "Measurement Period", end of "Measurement Period"] or X.date is null))

define "Numerator":
  Last([Observation: "HbA1c"] O where O.date during Interval[start of "Measurement Period", end of "Measurement Period"]).value < 8.0
