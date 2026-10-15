SELECT COUNT(*) AS prerequisite_available FROM records
WHERE lower(stream) LIKE '%infection%' OR lower(stream) LIKE '%device%' OR lower(stream) LIKE '%line%' AND lower(stream) NOT LIKE '%claim_lines%'
   OR (stream IN ('procedures','nursing_observations','hospital_fluid_balance','inpatient_fluid_charts','hospital_illness_assessments','inpatient_volume_assessments','medication_administrations','encounters','adt_messages')
       AND (lower(payload) LIKE '%clabsi%' OR lower(payload) LIKE '%central line%' OR lower(payload) LIKE '%central_line%' OR lower(payload) LIKE '%picc%'
            OR lower(payload) LIKE '%line_days%' OR lower(payload) LIKE '%device day%' OR lower(payload) LIKE '%bloodstream infection%' OR lower(payload) LIKE '%central venous%'))
