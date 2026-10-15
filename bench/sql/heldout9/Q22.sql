-- Patients vaccinated against influenza for the 2029-2030 season: distinct patients with an immunizations row vaccine 'influenza', administration_status 'completed', season '2029-2030' (orders, decisions, outreach and history streams are not administrations).
SELECT COUNT(DISTINCT patient) AS count FROM records WHERE stream='immunizations' AND json_extract(payload,'$.vaccine')='influenza'
  AND json_extract(payload,'$.administration_status')='completed' AND json_extract(payload,'$.season')='2029-2030';
