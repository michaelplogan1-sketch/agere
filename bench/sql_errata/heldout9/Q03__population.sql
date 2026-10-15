-- Population = every patient in the patients view (original deep-chart roster + population members).
-- Current records only: a record named in a visible same-stream record's supersedes link is replaced by it (releases with
-- supersession, e.g. v2.8), except adjudication chains, where the parent stays (payer_medical_claims child with another
-- adjudication_sequence or claim_id; payer_pharmacy_claims 'reversed'/'recouped' child; medical_claims 'reversed' child).
-- Inpatient stay, deduplicated across sources:
--  (1) EHR encounters type 'inpatient' (versions collapse by encounter_id; admit = earliest event_date; discharge = date of end_time;
--      timestamps start_time/end_time from the completed version);
--  (2) hospital ADT adt_messages patient_class 'I' per encounter_id (admit = A01 date, discharge = A03 date; times = A01/A03 event_time);
--  (3) payer_medical_claims inpatient facility claims (type_of_bill 011x, admission_date/discharge_date), voided claim_ids excluded,
--      one claim stay per patient+admission_date (latest discharge_date); claim stays nested in an earlier claim stay dropped.
--  Dedupe: a claim stay is the same stay as an EHR/ADT stay of the same patient when admission dates are within 1 day or the date
--  ranges overlap; an EHR/ADT stay without a discharge message takes the matched claim's discharge_date. Unmatched claim stays count
--  on their own (dates only, no timestamps).
-- Mean LOS (days) for stays discharged in 2029: elapsed (discharge time - admit time) when both timestamps exist, else
-- discharge_date - admission_date. 4 dp.
WITH r0 AS MATERIALIZED (SELECT id, supersedes, patient, stream, payload FROM records WHERE stream IN ('encounters','adt_messages','payer_medical_claims')),
sc AS MATERIALIZED (SELECT id, supersedes, stream, payload FROM r0 WHERE supersedes IS NOT NULL),
sup AS MATERIALIZED (SELECT c.supersedes id FROM sc c CROSS JOIN r0 p ON p.id=c.supersedes AND p.stream=c.stream
  WHERE NOT ((c.stream='payer_medical_claims' AND (json_extract(c.payload,'$.adjudication_sequence') IS NOT json_extract(p.payload,'$.adjudication_sequence')
               OR json_extract(c.payload,'$.payer_claim_id') IS NOT json_extract(p.payload,'$.payer_claim_id')))
          OR (c.stream='payer_pharmacy_claims' AND json_extract(c.payload,'$.claim_status') IN ('reversed','recouped'))
          OR (c.stream='medical_claims' AND json_extract(c.payload,'$.status')='reversed'))),
r AS MATERIALIZED (SELECT patient, stream, payload FROM r0 WHERE id NOT IN (SELECT id FROM sup)),
hs AS MATERIALIZED (
  SELECT patient, json_extract(payload,'$.encounter_id') k, min(json_extract(payload,'$.event_date')) ad, max(substr(json_extract(payload,'$.end_time'),1,10)) dd,
    max(CASE WHEN json_extract(payload,'$.status')='completed' THEN json_extract(payload,'$.start_time') END) ts, max(CASE WHEN json_extract(payload,'$.status')='completed' THEN json_extract(payload,'$.end_time') END) te
   FROM r WHERE stream='encounters' AND json_extract(payload,'$.type')='inpatient' GROUP BY 1,2
  UNION ALL
  SELECT patient, json_extract(payload,'$.encounter_id') k, coalesce(min(CASE WHEN json_extract(payload,'$.trigger_event')='A01' THEN json_extract(payload,'$.event_date') END), min(json_extract(payload,'$.event_date'))) ad,
    max(CASE WHEN json_extract(payload,'$.trigger_event')='A03' THEN json_extract(payload,'$.event_date') END) dd,
    min(CASE WHEN json_extract(payload,'$.trigger_event')='A01' THEN json_extract(payload,'$.event_time') END) ts, max(CASE WHEN json_extract(payload,'$.trigger_event')='A03' THEN json_extract(payload,'$.event_time') END) te
   FROM r WHERE stream='adt_messages' AND json_extract(payload,'$.patient_class')='I' GROUP BY 1,2),
void AS MATERIALIZED (SELECT DISTINCT json_extract(payload,'$.payer_claim_id') cid FROM r WHERE stream='payer_medical_claims' AND json_extract(payload,'$.claim_status')='voided'),
cc AS MATERIALIZED (SELECT patient, json_extract(payload,'$.admission_date') ad, max(json_extract(payload,'$.discharge_date')) dd FROM r
   WHERE stream='payer_medical_claims' AND json_extract(payload,'$.type_of_bill') LIKE '011%' AND json_extract(payload,'$.admission_date') IS NOT NULL
     AND json_extract(payload,'$.payer_claim_id') NOT IN (SELECT cid FROM void) GROUP BY 1,2),
cs AS MATERIALIZED (SELECT * FROM cc c WHERE NOT EXISTS (SELECT 1 FROM cc c2 WHERE c2.patient=c.patient AND c2.ad<c.ad AND c2.dd>=c.ad)),
stays AS MATERIALIZED (
  SELECT h.patient, h.k, h.ad, coalesce(h.dd, (SELECT max(c.dd) FROM cs c WHERE c.patient=h.patient
      AND (abs(julianday(c.ad)-julianday(h.ad))<=1 OR (c.ad<=coalesce(h.dd,h.ad) AND c.dd>=h.ad)))) dd, h.ts, h.te FROM hs h
  UNION ALL
  SELECT c.patient, 'claim:'||c.ad, c.ad, c.dd, NULL, NULL FROM cs c WHERE NOT EXISTS (SELECT 1 FROM hs h WHERE h.patient=c.patient
      AND (abs(julianday(c.ad)-julianday(h.ad))<=1 OR (c.ad<=coalesce(h.dd,h.ad) AND c.dd>=h.ad))))
SELECT ROUND(AVG(CASE WHEN ts IS NOT NULL AND te IS NOT NULL THEN julianday(te)-julianday(ts) ELSE julianday(dd)-julianday(ad) END),4) AS value FROM stays WHERE dd BETWEEN '2029-01-01' AND '2029-12-31';
