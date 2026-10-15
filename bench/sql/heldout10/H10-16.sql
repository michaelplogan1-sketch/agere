SELECT COUNT(*) AS prerequisite_available FROM records
WHERE stream IN ('encounters','adt','adt_messages','hospital_care_transitions','hospital_illness_assessments','transported_handoffs','nursing_observations')
  AND (lower(payload) LIKE '%provider_seen%' OR lower(payload) LIKE '%seen_by_provider%' OR lower(payload) LIKE '%first_provider%'
       OR lower(payload) LIKE '%door_to%' OR lower(payload) LIKE '%door-to%' OR lower(payload) LIKE '%provider_evaluation_time%' OR lower(payload) LIKE '%provider evaluation%')
