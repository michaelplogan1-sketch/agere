-- Patients whose social_history 'lives' text says they live alone ('alone'), and who are age_reported >= 65 on the roster profile.
SELECT DISTINCT r.patient AS patient_id FROM records r JOIN patients p ON p.id=r.patient
WHERE r.stream='social_history' AND lower(json_extract(r.payload,'$.lives')) LIKE '%alone%'
  AND json_extract(p.payload,'$.age_reported') >= 65
ORDER BY 1;
