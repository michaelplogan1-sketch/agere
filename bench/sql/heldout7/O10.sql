-- Patients with a substance use disorder condition (description 'Dependent drug abuse', 'Opioid use disorder', 'Alcoholism', 'AUD', 'Cannabis use disorder', or ICD-10 F10-F19) AND >=1 'dispensed' opioid (hydrocodone, oxycodone, fentanyl, morphine, hydromorphone, codeine, tramadol, meperidine).
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','pharmacy_dispenses')),
sud AS (SELECT DISTINCT patient FROM (SELECT patient, lower(coalesce(json_extract(payload,'$.description'),'')) d, coalesce(json_extract(payload,'$.icd10'),'') i FROM r WHERE stream='conditions')
        WHERE d LIKE '%drug abuse%' OR d LIKE '%use disorder%' OR d LIKE 'alcoholism%' OR d LIKE 'aud,%' OR substr(i,1,3) BETWEEN 'F10' AND 'F19'),
op AS (SELECT DISTINCT patient FROM (SELECT patient, lower(json_extract(payload,'$.medication_name')) m FROM r WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='dispensed')
       WHERE m LIKE '%hydrocodone%' OR m LIKE '%oxycodone%' OR m LIKE '%fentanyl%' OR m LIKE '%morphine%' OR m LIKE '%hydromorphone%' OR m LIKE '%codeine%' OR m LIKE '%tramadol%' OR m LIKE '%meperidine%')
SELECT patient AS patient_id FROM sud JOIN op USING(patient) ORDER BY 1;
