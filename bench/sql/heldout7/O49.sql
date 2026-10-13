-- Diagnostic: patient geography. ED encounters exist, but no patient address, ZIP/postal code, county, census tract or area deprivation index is recorded anywhere. Expect 0.
SELECT (SELECT COUNT(*) FROM records WHERE payload LIKE '%zip%' OR payload LIKE '%postal%' OR payload LIKE '%"address%' OR payload LIKE '%county%' OR payload LIKE '%census tract%' OR payload LIKE '%census_tract%' OR payload LIKE '%deprivation%')
     + (SELECT COUNT(*) FROM patients WHERE payload LIKE '%zip%' OR payload LIKE '%address%' OR payload LIKE '%postal%') AS prerequisite_available;
