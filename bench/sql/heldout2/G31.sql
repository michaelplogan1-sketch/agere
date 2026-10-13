-- ADT discharges with destination home (home, home_with_arranged_assistance, home_with_guardian) followed by a
-- telephone_messages record 1..7 days after the discharge date for the same patient. Count discharges.
WITH dis AS MATERIALIZED (
  SELECT id, patient, event AS d FROM records WHERE stream='adt' AND json_extract(payload,'$.event')='discharge'
    AND json_extract(payload,'$.destination') IN ('home','home_with_arranged_assistance','home_with_guardian')
)
SELECT COUNT(*) AS count FROM dis WHERE EXISTS (
  SELECT 1 FROM records t WHERE t.stream='telephone_messages' AND t.patient=dis.patient
    AND julianday(t.event)-julianday(dis.d) BETWEEN 1 AND 7);
