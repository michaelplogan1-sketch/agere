SELECT COUNT(DISTINCT patient) AS count FROM records WHERE stream='immunizations'
 AND json_extract(payload,'$.vaccine')='Tdap' AND json_extract(payload,'$.administration_status')='completed'
 AND event BETWEEN '2028-08-01' AND '2028-08-31';
