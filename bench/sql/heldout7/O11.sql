-- Patients aged 65+ (roster age_reported) with >=1 'dispensed' high-risk-in-elderly drug: first-generation antihistamines (diphenhydramine, chlorpheniramine), benzodiazepines (diazepam, lorazepam, clonazepam, alprazolam), meperidine, muscle relaxants (cyclobenzaprine), at any date.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='pharmacy_dispenses'),
b AS (SELECT DISTINCT patient FROM (SELECT patient, lower(json_extract(payload,'$.medication_name')) m FROM r WHERE json_extract(payload,'$.status')='dispensed')
      WHERE m LIKE '%diphenhydramine%' OR m LIKE '%chlorpheniramine%' OR m LIKE '%diazepam%' OR m LIKE '%lorazepam%' OR m LIKE '%clonazepam%' OR m LIKE '%alprazolam%' OR m LIKE '%meperidine%' OR m LIKE '%cyclobenzaprine%')
SELECT COUNT(*) AS count FROM patients p JOIN b ON b.patient=p.id WHERE json_extract(p.payload,'$.age_reported')>=65;
