-- 2028 telephone/virtual vs in-person (primary_care/office/pediatric_office/wellness) encounters by encounter organization (WH-PRACTICE-*).
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters'),
e AS (SELECT json_extract(payload,'$.encounter_id') eid, patient, min(json_extract(payload,'$.event_date')) d, max(json_extract(payload,'$.type')) t,
             max(json_extract(payload,'$.organization_id')) org FROM r GROUP BY 1,2),
x AS (SELECT org, sum(t IN ('telephone','virtual')) tele, sum(t IN ('primary_care','office','pediatric_office','wellness')) inp FROM e
      WHERE org LIKE 'WH-PRACTICE-%' AND d BETWEEN '2028-01-01' AND '2028-12-31' GROUP BY 1)
SELECT org AS practice, tele AS telehealth_contacts, inp AS in_person_contacts, round(100.0*tele/(tele+inp),1) AS telehealth_pct FROM x ORDER BY 1;
