SELECT COUNT(*) AS count FROM records WHERE stream='vitals' AND event IS NULL AND (
 (json_extract(payload,'$.measurement')='bp_systolic' AND json_type(payload,'$.value') IN ('integer','real')) OR
 json_type(payload,'$.blood_pressure_systolic') IN ('integer','real'));
