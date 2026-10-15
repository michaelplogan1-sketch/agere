WITH pa AS (SELECT DISTINCT json_extract(payload,'$.visit_number') v FROM records WHERE stream='adt_messages' AND json_extract(payload,'$.trigger_event')='A01' AND json_extract(payload,'$.patient_class')='I'),
da AS (SELECT DISTINCT json_extract(payload,'$.encounter_id') v FROM records WHERE stream='adt' AND json_extract(payload,'$.event') IN ('admission','inpatient_admission')),
dis AS (
  SELECT patient, json_extract(payload,'$.visit_number') v, max(event) d FROM records WHERE stream='adt_messages' AND json_extract(payload,'$.trigger_event')='A03' AND json_extract(payload,'$.visit_number') IN (SELECT v FROM pa) GROUP BY 1,2
  UNION ALL
  SELECT patient, json_extract(payload,'$.encounter_id'), max(event) FROM records WHERE stream='adt' AND json_extract(payload,'$.event')='discharge' AND json_extract(payload,'$.encounter_id') IN (SELECT v FROM da) GROUP BY 1,2),
adm AS (
  SELECT patient, json_extract(payload,'$.visit_number') v, event d FROM records WHERE stream='adt_messages' AND json_extract(payload,'$.trigger_event')='A01' AND json_extract(payload,'$.patient_class')='I'
  UNION SELECT patient, json_extract(payload,'$.encounter_id'), event FROM records WHERE stream='adt' AND json_extract(payload,'$.event') IN ('admission','inpatient_admission'))
SELECT COUNT(*) AS count FROM dis WHERE d BETWEEN '2029-01-01' AND '2029-12-31'
  AND EXISTS (SELECT 1 FROM adm WHERE adm.patient=dis.patient AND adm.v <> dis.v AND julianday(adm.d) - julianday(dis.d) BETWEEN 1 AND 30)
