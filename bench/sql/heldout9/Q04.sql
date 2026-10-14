-- 180-day inpatient readmission rate (%): index = inpatient encounters (encounters type 'inpatient', versions collapsed by encounter_id, discharge = date of end_time) discharged 2029-01-01..2029-12-31; readmitted when the same patient has a different inpatient encounter whose admission date (earliest event_date) is after the index discharge date and no more than 180 days later. 100*readmitted/index, 2 dp.
WITH e AS MATERIALIZED (SELECT patient, json_extract(payload,'$.encounter_id') eid, min(json_extract(payload,'$.event_date')) d, max(json_extract(payload,'$.end_time')) en FROM records
  WHERE stream='encounters' AND json_extract(payload,'$.type')='inpatient' GROUP BY 1,2),
idx AS (SELECT * FROM e WHERE en IS NOT NULL AND substr(en,1,10) BETWEEN '2029-01-01' AND '2029-12-31'),
flag AS (SELECT i.eid, EXISTS (SELECT 1 FROM e x WHERE x.patient=i.patient AND x.eid<>i.eid AND x.d>substr(i.en,1,10) AND julianday(x.d)-julianday(substr(i.en,1,10))<=180) AS readmit FROM idx i)
SELECT ROUND(100.0*SUM(readmit)/COUNT(*),2) AS value FROM flag;
