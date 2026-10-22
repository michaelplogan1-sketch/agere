// Controlling High Blood Pressure
// Governed CQL for the cbp measure-library entry (an approximation of the public specification, not a certified measure).
// Reference for parity: config/agere/measures/cbp.json (check() compares this logic with that engine).
library ControllingHighBloodPressure version '1.0.0'

valueset "Hypertension": {"type": "condition", "concepts": ["hypertension"], "icd10_prefixes": ["I10", "I11", "I12", "I13", "I16"]}
valueset "Hospice": {"type": "service", "streams": [{"stream": "hospice_enrollments"}], "hcpcs": ["G9473", "G9474", "G9475", "G9476", "G9477", "G9478", "G9479", "Q5003", "Q5004", "Q5005", "Q5006", "Q5007", "Q5008", "Q5010", "S9126", "T2042", "T2043", "T2044", "T2045", "T2046"]}
valueset "Palliative care": {"type": "condition", "icd10_prefixes": ["Z51.5"]}
valueset "ESRD or dialysis": {"type": "condition", "icd10_prefixes": ["N18.6", "Z99.2"]}
valueset "Pregnancy": {"type": "condition", "icd10_prefixes": ["O0", "O1", "O2", "O3", "O4", "O6", "O7", "O8", "O9", "Z33", "Z34"]}
valueset "Blood pressure": {"type": "blood_pressure", "systolic_below": 140, "diastolic_below": 90}

parameter "Measurement Period" Interval<Date> default Interval[@2029-06-01, @2030-05-27]

define "Initial Population":
  AgeInYearsAt(end of "Measurement Period") between 18 and 85
  and EnrolledThroughout("Measurement Period", 1)
  and exists ([Condition: "Hypertension"] C where (C.date during Interval[start of "Measurement Period" - 50 years, start of "Measurement Period" + 6 months] or C.date is null))

define "Denominator": "Initial Population"

define "Denominator Exclusion":
  exists ([Procedure: "Hospice"] X where X.date during Interval[start of "Measurement Period", end of "Measurement Period"])
  or exists ([Condition: "Palliative care"] X where (X.date during Interval[start of "Measurement Period", end of "Measurement Period"] or X.date is null))
  or exists ([Condition: "ESRD or dialysis"] X where (X.date during Interval[start of "Measurement Period" - 12 months, end of "Measurement Period"] or X.date is null))
  or exists ([Condition: "Pregnancy"] X where (X.date during Interval[start of "Measurement Period", end of "Measurement Period"] or X.date is null))

define "Numerator":
  Last([Observation: "Blood pressure"] O where O.date during Interval[start of "Measurement Period", end of "Measurement Period"]).systolic < 140
  and Last([Observation: "Blood pressure"] O where O.date during Interval[start of "Measurement Period", end of "Measurement Period"]).diastolic < 90
