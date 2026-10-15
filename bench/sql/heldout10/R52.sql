SELECT DISTINCT h.patient AS patient_id FROM records h
WHERE h.stream = 'health_risk_assessments' AND CAST(json_extract(h.payload,'$.phq2_total') AS INTEGER) >= 3
  AND NOT EXISTS (
    SELECT 1 FROM records m WHERE m.stream = 'mood_questionnaires' AND json_extract(m.payload,'$.screen_record_id') = h.id)
