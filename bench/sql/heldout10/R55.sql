SELECT ROUND(100.0 * SUM(CASE WHEN EXISTS (
    SELECT 1 FROM records f WHERE f.stream = 'behavioral_followup_reports' AND json_extract(f.payload,'$.plan_record_id') = p.id
      AND f.event >= p.event AND f.event <= date(p.event, '+' || CAST(json_extract(p.payload,'$.followup_requested_days') AS INTEGER) || ' days')
  ) THEN 1 ELSE 0 END) / COUNT(*), 1) AS value
FROM records p WHERE p.stream = 'behavioral_care_plans'
