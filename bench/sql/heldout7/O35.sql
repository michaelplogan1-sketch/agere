-- Tdap catch-up among adults: denominator = patients aged 19+ (roster age_reported) with a tetanus/diphtheria/pertussis immunization history review (immunization_history record); numerator = those with a Tdap immunizations record with administration_status 'completed'. Patient-recalled history is not an administration. Percent.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('immunization_history','immunizations')),
den AS (SELECT DISTINCT r.patient FROM r JOIN patients p ON p.id=r.patient WHERE r.stream='immunization_history' AND json_extract(r.payload,'$.vaccine_family')='tetanus_diphtheria_pertussis' AND json_extract(p.payload,'$.age_reported')>=19),
num AS (SELECT DISTINCT patient FROM r WHERE stream='immunizations' AND json_extract(payload,'$.vaccine')='Tdap' AND json_extract(payload,'$.administration_status')='completed')
SELECT round(100.0*sum(den.patient IN (SELECT patient FROM num))/count(*),1) AS value FROM den;
