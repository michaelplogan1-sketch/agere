-- Beta-blocker for heart failure: denominator = patients with a heart failure condition ('heart failure' in description or ICD-10 I50*); numerator = >=1 'dispensed' evidence-based beta-blocker (carvedilol, metoprolol succinate, bisoprolol; metoprolol tartrate and atenolol excluded) dated 2028-08-16..2029-08-15. Percent.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','pharmacy_dispenses')),
hf AS (SELECT DISTINCT patient FROM r WHERE stream='conditions' AND (lower(json_extract(payload,'$.description')) LIKE '%heart failure%' OR coalesce(json_extract(payload,'$.icd10'),'') LIKE 'I50%')),
bb AS (SELECT DISTINCT patient FROM (SELECT patient, lower(json_extract(payload,'$.medication_name')) m FROM r WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='dispensed' AND json_extract(payload,'$.event_date') BETWEEN '2028-08-16' AND '2029-08-15')
       WHERE m LIKE '%carvedilol%' OR m LIKE '%metoprolol succinate%' OR m LIKE '%bisoprolol%')
SELECT round(100.0*sum(hf.patient IN (SELECT patient FROM bb))/count(*),1) AS value FROM hf;
