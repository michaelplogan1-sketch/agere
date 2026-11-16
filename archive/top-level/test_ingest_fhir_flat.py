"""Tests for clinical_core.agere.ingest.fhir and .flat (run: .venv/bin/python -m unittest test_ingest_fhir_flat)."""
import base64
import json
import random
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path

from clinical_core.agere.ingest import fhir, flat

RAW = Path("/Users/mikel/synthetic-patient-ecosystem/releases/healthcare-warehouse-v2.12-sample-0.30-native/raw")


def write(root: Path, rel: str, content, binary=False) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8", newline="")
    return p


def ndjson(*objs, extra_lines=()):
    return "\n".join([json.dumps(o) for o in objs] + list(extra_lines)) + "\n"


def run_parse(mod, path, rel=None, root=None):
    ctx = {"relpath": str(Path(path).relative_to(root))} if root else {}
    out = defaultdict(list)
    for table, row in mod.parse(Path(path), ctx):
        out[table].append(row)
    return out


class FhirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_detect(self):
        self.assertTrue(fhir.detect(Path("raw/fhir_bulk/P1/2028-01/Patient.ndjson")))
        self.assertTrue(fhir.detect(Path("raw/ehr/fhir/P1/2028-01/Observation_RESEND.ndjson")))
        self.assertFalse(fhir.detect(Path("raw/ehr/epic/EPIC-TX/2028-01/patient.txt")))
        self.assertFalse(fhir.detect(Path("raw/private/fhir_bulk/P1/Patient.ndjson")))

    def test_patient(self):
        res = {
            "resourceType": "Patient", "id": "pt1",
            "meta": {"lastUpdated": "2028-01-02T03:04:05-05:00"},
            "identifier": [
                {"type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0203", "code": "MR"}]},
                 "value": "MRN-1"},
                {"system": "http://hl7.org/fhir/sid/us-mbi", "value": "1EG4TE5MK73"},
                {"system": "urn:payer:member", "value": "MEM9"},
            ],
            "name": [{"family": "Li", "given": ["Olivia", "J"]}],
            "gender": "female", "birthDate": "1990-05-06",
            "address": [{"line": ["1 Main St", "Apt 2"], "city": "Dodge City", "state": "KS", "postalCode": "67801"}],
            "extension": [
                {"url": "http://hl7.org/fhir/us/core/StructureDefinition/us-core-race",
                 "extension": [{"url": "ombCategory", "valueCoding": {"code": "2106-3", "display": "White"}},
                               {"url": "text", "valueString": "White"}]},
                {"url": "http://hl7.org/fhir/us/core/StructureDefinition/us-core-ethnicity",
                 "extension": [{"url": "text", "valueString": "Not Hispanic or Latino"}]},
            ],
            "deceasedDateTime": "2029-01-01T00:00:00Z",
        }
        p = write(self.root, "raw/fhir_bulk/P1/2028-01/Patient.ndjson", ndjson(res))
        out = run_parse(fhir, p)
        self.assertEqual(list(out), ["fhir_patient"])
        r = out["fhir_patient"][0]
        self.assertEqual(r["id"], "pt1")
        self.assertEqual(r["patient_id"], "pt1")
        self.assertEqual(r["mrn"], "MRN-1")
        self.assertEqual(r["mbi"], "1EG4TE5MK73")
        self.assertEqual(r["member_id"], "MEM9")
        self.assertEqual(len(json.loads(r["identifiers"])), 3)
        self.assertEqual(r["name_family"], "Li")
        self.assertEqual(r["name_given"], "Olivia J")
        self.assertEqual(r["address_state"], "KS")
        self.assertEqual(r["address_zip"], "67801")
        self.assertEqual(r["race"], "White")
        self.assertEqual(r["ethnicity"], "Not Hispanic or Latino")
        self.assertTrue(r["deceased"])
        self.assertEqual(r["meta_last_updated"], "2028-01-02T03:04:05-05:00")  # offset kept
        self.assertEqual(r["meta_last_updated_iso"], "2028-01-02T08:04:05Z")  # normalized
        self.assertEqual(json.loads(r["_resource"]), res)

    def test_observation_encounter_condition_medication(self):
        obs = {
            "resourceType": "Observation", "id": "o1", "status": "amended",
            "meta": {"lastUpdated": "2028-02-01T00:00:00Z"},
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                      "code": "laboratory"}]}],
            "code": {"coding": [{"system": "http://snomed.info/sct", "code": "999"},
                                {"system": "http://loinc.org", "code": "2345-7", "display": "Glucose"}],
                     "text": "Glucose"},
            "subject": {"reference": "Patient/pt1"}, "encounter": {"reference": "Encounter/e1"},
            "effectiveDateTime": "2028-01-31T08:00:00-06:00", "issued": "2028-01-31T15:00:00Z",
            "valueQuantity": {"value": 87, "unit": "mg/dL", "code": "mg/dL"},
            "interpretation": [{"coding": [{"code": "N"}]}],
            "referenceRange": [{"low": {"value": 70}, "high": {"value": 99}, "text": "70-99"}],
        }
        obs2 = {"resourceType": "Observation", "id": "o2", "status": "final", "subject": {"reference": "Patient/pt1"},
                "code": {"text": "Smoking"}, "valueString": ">60", "effectiveDateTime": None}
        enc = {
            "resourceType": "Encounter", "id": "e1", "status": "finished",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
            "type": [{"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": "99213"}]}],
            "subject": {"reference": "Patient/pt1"},
            "period": {"start": "2028-01-31T08:00:00-06:00", "end": "2028-01-31T09:00:00-06:00"},
            "serviceProvider": {"reference": "Organization/org7"},
            "participant": [{"individual": {"identifier": {"system": "http://hl7.org/fhir/sid/us-npi",
                                                          "value": "1234567893"}}}],
            "hospitalization": {"admitSource": {"coding": [{"code": "emd"}]},
                                "dischargeDisposition": {"coding": [{"code": "home"}]}},
            "reasonCode": [{"coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "J06.9"}]}],
        }
        cond = {
            "resourceType": "Condition", "id": "c1", "subject": {"reference": "Patient/pt1"},
            "category": [{"coding": [{"code": "problem-list-item"}]}],
            "clinicalStatus": {"coding": [{"code": "active"}]},
            "verificationStatus": {"coding": [{"code": "confirmed"}]},
            "code": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "E11.9",
                                 "display": "T2DM"}]},
            "onsetDateTime": "2020-03-04", "recordedDate": "2020-03-05",
        }
        med = {
            "resourceType": "MedicationRequest", "id": "m1", "status": "active", "intent": "order",
            "subject": {"reference": "Patient/pt1"}, "encounter": {"reference": "Encounter/e1"},
            "authoredOn": "2028-01-31T08:10:00-06:00",
            "medicationCodeableConcept": {"coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                                                      "code": "314076"}], "text": "lisinopril 10 mg"},
            "dosageInstruction": [{"text": "Take 1 tablet daily",
                                   "timing": {"repeat": {"frequency": 1, "period": 1, "periodUnit": "d"}},
                                   "doseAndRate": [{"doseQuantity": {"value": 1, "unit": "tablet"}}]}],
            "dispenseRequest": {"quantity": {"value": 30, "unit": "tablet"},
                                "expectedSupplyDuration": {"value": 30, "unit": "days"},
                                "numberOfRepeatsAllowed": 3},
            "requester": {"reference": "Practitioner/dr1"},
        }
        root = self.root
        po = write(root, "raw/fhir_bulk/P1/2028-02/Observation.ndjson", ndjson(obs, obs2))
        pe = write(root, "raw/fhir_bulk/P1/2028-02/Encounter.ndjson", ndjson(enc))
        pc = write(root, "raw/fhir_bulk/P1/2028-02/Condition.ndjson", ndjson(cond))
        pm = write(root, "raw/fhir_bulk/P1/2028-02/MedicationRequest.ndjson", ndjson(med))

        o = run_parse(fhir, po)["fhir_observation"]
        r = o[0]
        self.assertEqual((r["code_system"], r["code_code"], r["code_display"]), ("http://loinc.org", "2345-7", "Glucose"))
        self.assertEqual(r["loinc_code"], "2345-7")
        self.assertEqual(len(json.loads(r["code_codings"])), 2)
        self.assertEqual((r["value_quantity_value"], r["value_quantity_unit"]), (87, "mg/dL"))
        self.assertEqual(r["status"], "amended")
        self.assertTrue(r["is_corrected"])
        self.assertEqual(r["patient_id"], "pt1")
        self.assertEqual(r["encounter_id"], "e1")
        self.assertEqual(r["category_code"], "laboratory")
        self.assertEqual(r["effective_datetime_iso"], "2028-01-31T14:00:00Z")
        self.assertEqual(r["issued"], "2028-01-31T15:00:00Z")
        self.assertEqual((r["reference_range_low"], r["reference_range_high"]), (70, 99))
        self.assertEqual(r["interpretation_code"], "N")
        self.assertEqual(o[1]["value_string"], ">60")
        self.assertIsNone(o[1]["effective_datetime"])  # explicit null survives

        r = run_parse(fhir, pe)["fhir_encounter"][0]
        self.assertEqual(r["class_code"], "AMB")
        self.assertEqual(r["type_code"], "99213")
        self.assertEqual(r["period_end"], "2028-01-31T09:00:00-06:00")
        self.assertEqual(r["service_provider_id"], "org7")
        self.assertEqual(r["participant_npi"], "1234567893")
        self.assertEqual((r["admit_source"], r["discharge_disposition"]), ("emd", "home"))
        self.assertEqual(r["reason_code"], "J06.9")
        self.assertEqual(r["encounter_id"], "e1")

        r = run_parse(fhir, pc)["fhir_condition"][0]
        self.assertEqual((r["icd10_code"], r["clinical_status"], r["verification_status"]), ("E11.9", "active", "confirmed"))
        self.assertTrue(r["is_problem_list_item"])
        self.assertEqual(r["onset_datetime"], "2020-03-04")
        self.assertEqual(r["onset_datetime_iso"], "2020-03-04")

        r = run_parse(fhir, pm)["fhir_medicationrequest"][0]
        self.assertEqual((r["rxnorm_code"], r["medication_display"]), ("314076", "lisinopril 10 mg"))
        self.assertEqual(r["dosage_text"], "Take 1 tablet daily")
        self.assertEqual((r["dose_quantity_value"], r["dose_quantity_unit"]), (1, "tablet"))
        self.assertEqual((r["dispense_quantity_value"], r["supply_duration_value"], r["refills_allowed"]), (30, 30, 3))
        self.assertEqual(r["requester_id"], "dr1")
        self.assertEqual(r["encounter_id"], "e1")

    def test_documentreference_base64_and_url(self):
        text = "CC: cough\nHPI: 61 y/o male — here for follow up"
        d1 = {"resourceType": "DocumentReference", "id": "d1", "status": "current",
              "type": {"coding": [{"system": "http://loinc.org", "code": "11506-3", "display": "Progress note"}]},
              "subject": {"reference": "Patient/pt1"}, "date": "2028-03-01T10:00:00-05:00",
              "author": [{"reference": "Practitioner/dr1"}],
              "context": {"encounter": [{"reference": "Encounter/e1"}]},
              "content": [{"attachment": {"contentType": "text/plain",
                                          "data": base64.b64encode(text.encode()).decode()}}]}
        d2 = {"resourceType": "DocumentReference", "id": "d2", "status": "current",
              "subject": {"reference": "Patient/pt1"},
              "content": [{"attachment": {"contentType": "application/pdf", "url": "https://x.test/doc.pdf"}}]}
        p = write(self.root, "raw/fhir_bulk/P1/2028-03/DocumentReference.ndjson", ndjson(d1, d2))
        rows = run_parse(fhir, p)["fhir_documentreference"]
        self.assertEqual(rows[0]["text"], text)
        self.assertEqual(rows[0]["attachment_content_type"], "text/plain")
        self.assertEqual((rows[0]["type_code"], rows[0]["type_display"]), ("11506-3", "Progress note"))
        self.assertEqual(rows[0]["author_id"], "dr1")
        self.assertEqual(rows[0]["encounter_id"], "e1")
        self.assertIsNone(rows[0]["attachment_url"])
        self.assertIsNone(rows[1]["text"])
        self.assertEqual(rows[1]["attachment_url"], "https://x.test/doc.pdf")
        self.assertEqual(rows[1]["attachment_content_type"], "application/pdf")

    def test_appointment_others_and_generic(self):
        appt = {"resourceType": "Appointment", "id": "a1", "status": "noshow",
                "start": "2028-04-01T09:00:00-04:00", "end": "2028-04-01T09:20:00-04:00", "minutesDuration": 20,
                "created": "2028-03-20T00:00:00", "serviceType": [{"coding": [{"code": "124", "display": "GP"}]}],
                "participant": [{"actor": {"reference": "Patient/pt9"}, "status": "accepted"},
                                {"actor": {"reference": "Practitioner/dr1"}, "status": "accepted"}]}
        alg = {"resourceType": "AllergyIntolerance", "id": "al1", "patient": {"reference": "Patient/pt1"},
               "clinicalStatus": {"coding": [{"code": "active"}]}, "code": {"text": "Sulfa"}, "recordedDate": "2028-01-01"}
        imm = {"resourceType": "Immunization", "id": "i1", "status": "completed", "patient": {"reference": "Patient/pt1"},
               "vaccineCode": {"coding": [{"system": "http://hl7.org/fhir/sid/cvx", "code": "140"}], "text": "influenza"},
               "occurrenceDateTime": "2028-01-13"}
        proc = {"resourceType": "Procedure", "id": "pr1", "status": "completed", "subject": {"reference": "Patient/pt1"},
                "code": {"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": "45378"}]},
                "performedDateTime": "2028-02-02T10:00:00Z"}
        cov = {"resourceType": "Coverage", "id": "cv1", "status": "active", "beneficiary": {"reference": "Patient/pt1"},
               "period": {"start": "2028-01-01", "end": "2028-12-31"},
               "type": {"coding": [{"code": "HMO"}]}, "identifier": [{"value": "COV-1"}]}
        p = write(self.root, "raw/fhir_bulk/P1/2028-04/Mixed.ndjson", ndjson(appt, alg, imm, proc, cov))
        out = run_parse(fhir, p)
        self.assertEqual(
            sorted(out),
            ["fhir_allergyintolerance", "fhir_appointment", "fhir_coverage", "fhir_immunization", "fhir_procedure"],
        )
        a = out["fhir_appointment"][0]
        self.assertEqual((a["status"], a["minutes_duration"], a["service_type_code"]), ("noshow", 20, "124"))
        self.assertEqual(a["patient_id"], "pt9")
        self.assertEqual(a["start_iso"], "2028-04-01T13:00:00Z")
        self.assertEqual(out["fhir_allergyintolerance"][0]["code_text"], "Sulfa")
        self.assertEqual(out["fhir_immunization"][0]["cvx_code"], "140")
        self.assertEqual(out["fhir_procedure"][0]["cpt_code"], "45378")
        c = out["fhir_coverage"][0]
        self.assertEqual(c["patient_id"], "pt1")
        self.assertEqual((c["period_start"], c["period_end"], c["type_code"]), ("2028-01-01", "2028-12-31", "HMO"))
        self.assertEqual(c["identifier_value"], "COV-1")
        self.assertIn("_resource", c)

    def test_truncated_and_bad_lines_are_kept_not_dropped(self):
        good = {"resourceType": "Patient", "id": "p1", "birthDate": "2000-01-01"}
        cut = '{"resourceType":"Patient","id":"p2","name":[{"family":"Gre'
        p = write(self.root, "raw/fhir_bulk/P1/2028-05/Patient.ndjson",
                  ndjson(good, extra_lines=[cut, '{"no_type": 1}']))
        out = run_parse(fhir, p)
        self.assertEqual(len(out["fhir_patient"]), 1)
        errs = out["fhir_parse_error"]
        self.assertEqual([e["line_no"] for e in errs], [2, 3])
        self.assertTrue(errs[0]["raw_prefix"].startswith('{"resourceType":"Patient","id":"p2"'))

    def test_bundle_and_bom(self):
        bundle = {"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "Patient", "id": "b1"}},
                                                      {"resource": {"resourceType": "Condition", "id": "b2"}}]}
        p = write(self.root, "raw/fhir_bulk/P1/2028-06/Bundle.ndjson", b"\xef\xbb\xbf" + ndjson(bundle).encode())
        out = run_parse(fhir, p)
        self.assertEqual(sorted(out), ["fhir_condition", "fhir_patient"])

    def test_norm_dt(self):
        self.assertEqual(fhir.norm_dt("2028-01-31T08:00:00-06:00"), "2028-01-31T14:00:00Z")
        self.assertEqual(fhir.norm_dt("2028-01-31T08:00:00"), "2028-01-31T08:00:00")
        self.assertEqual(fhir.norm_dt("2028-01-31"), "2028-01-31")
        self.assertEqual(fhir.norm_dt("2028-01"), "2028-01")
        self.assertIsNone(fhir.norm_dt("garbage"))
        self.assertIsNone(fhir.norm_dt(None))


class FlatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "raw"
        flat.reset_schema_registry()

    def tearDown(self):
        self.tmp.cleanup()

    def parse(self, rel):
        return run_parse(flat, self.root / rel, root=self.root)

    def test_kind_naming_from_real_paths(self):
        cases = {
            "ehr/athena/2881/2027-11/PATIENT.csv": "ehr_athena_patient",
            "ehr/athena/2881/2027-11/PATIENT_RESEND.csv": "ehr_athena_patient",
            "ehr/athena/2881/2027-11/DOCUMENT_v2.csv": "ehr_athena_document",
            "ehr/ecw/WHN-PRACTICE-CA-9/2027-03/patients.csv": "ehr_ecw_patients",
            "ehr/epic/EPIC-TX/2027-04/hno_note_text.txt": "ehr_epic_hno_note_text",
            "ehr/epic/EPIC-DEEPCHART/2027-04/pat_enc_2.txt": "ehr_epic_pat_enc_2",
            "ehr/epic/EPIC-OH/2027-04/patient_RESEND.txt": "ehr_epic_patient",
            "payers/WH-PAYER-MCD2/2027-12/MCD2_MEDICAL_20271205.csv": "payers_medical",
            "payers/WH-PAYER-C1/2027-12/C1_MEDICAL_FULL_RESTATEMENT_20271205.txt": "payers_medical",
            "payers/WH-PAYER-DSNP1/2027-12/DSNP1_PHARMACY_20271205_v2.txt": "payers_pharmacy",
            "payers/WH-PAYER-X1/2027-12/X1_ELIGIBILITY_20271205_RESEND.csv": "payers_eligibility",
            "cms_cclf_csv/partd_claims/P.A9999.ACO.ZC7Y26.D261012.T2151975.csv": "cclf_partd_claims",
            "cms_alr/2030/P.A9999.ACO.AALR.D300115.T8118695_1-1.csv": "alr_aalr1_assigned_beneficiaries",
            "cms_alr/2030Q1/P.A9999.ACO.QALR.2030Q1.D300410.T1234567_1-5.csv": "alr_aalr5_beneficiary_turnover",
            "cms_alr/2029/P.A9999.ACO.AALR.D290115.T1_1-9_RESEND.csv": "alr_aalr9_beneficiaries_underserved",
            "programs/WH-PAYER-X1/2030Q1/X1_CARE_GAPS_2030Q1_RESEND.csv": "programs_care_gaps",
            "programs/WH-PAYER-0/2030Q1/0_CARE_GAPS_2030Q1.csv": "programs_care_gaps",
            "programs/WH-PAYER-MA1/2030Q1/MA1_RISK_SCORES_2030Q1.csv": "programs_risk_scores",
            "programs/mssp/2030Q1/P.A9999.ACO.QALR.2030Q1.csv": "programs_mssp_qalr",
            "programs/mssp/PY2028/P.A9999.ACO.FINRPT.PY2028.csv": "programs_mssp_finrpt",
            "rosters/2026-09/ACO_PROVIDER_ROSTER_20260910_v2.csv": "rosters_aco_provider_roster",
            "sdoh_vendor/2027-01/SDOH_VENDOR_20270115.csv": "sdoh_vendor",
        }
        for rel, table in cases.items():
            info = flat.classify(Path("/x/raw") / rel, {"relpath": rel})
            self.assertEqual(info["table"], table, rel)
        self.assertEqual(
            flat.classify(Path("/x/raw/payers/WH-PAYER-MCD2/2027-12/MCD2_MEDICAL_20271205.csv"))["meta"]["payer"], "MCD2")
        self.assertEqual(
            flat.classify(Path("/x/raw/payers/WH-PAYER-C1/2027-12/C1_MEDICAL_FULL_RESTATEMENT_N.txt"))["variant"],
            "full_restatement")
        self.assertEqual(
            flat.classify(Path("/x/raw/cms_alr/2030Q1/P.A9999.ACO.QALR.2030Q1.D1.T1_1-4.csv"))["meta"]["alr_type"], "QALR")

    def test_detect(self):
        self.assertTrue(flat.detect(Path("/x/raw/ehr/athena/2881/2027-11/PATIENT.csv")))
        self.assertTrue(flat.detect(Path("/x/raw/payers/WH-PAYER-C1/2027-11/C1_MEDICAL_20271105.txt")))
        self.assertFalse(flat.detect(Path("/x/raw/cms_cclf/2027-11/P.A9999.ACO.ZC1Y27.D271112.T1833843.csv")))
        self.assertFalse(flat.detect(Path("/x/raw/cms_cclf/2027-11/P.A9999.ACO.ZC1Y27.D271112.T1833843")))
        self.assertFalse(flat.detect(Path("/x/raw/x12/837p/P1/f.txt")))
        self.assertFalse(flat.detect(Path("/x/raw/ncpdp/MA1/2027-11/MA1_NCPDP_D0_20271105.txt")))
        self.assertFalse(flat.detect(Path("/x/raw/hl7/adt/2027-11/FAC_20271105.hl7")))
        self.assertFalse(flat.detect(Path("/x/raw/private/payers/WH-PAYER-C1/2027-11/C1_MEDICAL_20271105.txt")))
        self.assertFalse(flat.detect(Path("/x/raw/ehr/fhir/P1/2027-11/Patient.ndjson")))
        self.assertFalse(flat.detect(Path("/x/raw/manifest.json")))
        # the skip also applies when the orchestrator passes a relpath
        self.assertIsNone(flat.classify(Path("/x/raw/cms_cclf/2027-11/f.csv"), {"relpath": "cms_cclf/2027-11/f.csv"}))
        self.assertEqual(list(flat.parse(Path("/nonexistent/raw/cms_cclf/2027-11/a.csv"))), [])

    def test_delimiters_bom_quotes(self):
        root = self.root
        write(root, "payers/WH-PAYER-A1/2028-01/A1_MEDICAL_20280105.csv",
              '﻿CLAIM_ID,MEMBER_ID,NOTE\r\nc1,m1,"hello, world"\r\nc2,m2,"say ""hi"""\r\n'.encode("utf-8"))
        write(root, "payers/WH-PAYER-A2/2028-01/A2_MEDICAL_20280105.txt",
              "CLAIM_ID|MEMBER_ID|NOTE\nc3|m3|\"quoted | inside\"\nc4|m4|5\" tall\n")
        write(root, "payers/WH-PAYER-A3/2028-01/A3_MEDICAL_20280105.txt",
              "claimId\tmemberId\tnote\nc5\tm5\t\"tab\there\"\nc6\tm6\t\n")
        a1 = self.parse("payers/WH-PAYER-A1/2028-01/A1_MEDICAL_20280105.csv")["payers_medical"]
        self.assertEqual(
            a1, [{"claim_id": "c1", "member_id": "m1", "note": "hello, world", "payer": "A1", "file_variant": None},
                 {"claim_id": "c2", "member_id": "m2", "note": 'say "hi"', "payer": "A1", "file_variant": None}])
        a2 = self.parse("payers/WH-PAYER-A2/2028-01/A2_MEDICAL_20280105.txt")["payers_medical"]
        self.assertEqual(a2[0]["note"], "quoted | inside")  # quote at field start protects the delimiter
        self.assertEqual(a2[1]["note"], '5" tall')
        a3 = self.parse("payers/WH-PAYER-A3/2028-01/A3_MEDICAL_20280105.txt")["payers_medical"]
        self.assertEqual([r["claim_id"] for r in a3], ["c5", "c6"])  # camelCase header -> claim_id
        self.assertEqual(a3[0]["note"], "tab\there")
        self.assertEqual(a3[1]["note"], "")  # empty stays empty text
        self.assertEqual(a3[0]["payer"], "A3")

    def test_values_stay_text_exactly(self):
        write(self.root, "ehr/athena/2881/2028-01/PATIENT.csv",
              "contextid,patientid,dob,zip,amount\n2881,0061789,02/07/1955,01059, 1.50 \n")
        row = self.parse("ehr/athena/2881/2028-01/PATIENT.csv")["ehr_athena_patient"][0]
        self.assertEqual(row, {"contextid": "2881", "patientid": "0061789", "dob": "02/07/1955", "zip": "01059",
                               "amount": " 1.50 ", "file_variant": None})

    def test_cp1252_fallback(self):
        write(self.root, "rosters/2028-01/ACO_PROVIDER_ROSTER_20280105.csv",
              "PROVIDER_LAST_NAME,PROVIDER_FIRST_NAME\nMu\xf1oz,Jos\xe9\n".encode("cp1252"))
        row = self.parse("rosters/2028-01/ACO_PROVIDER_ROSTER_20280105.csv")["rosters_aco_provider_roster"][0]
        self.assertEqual((row["provider_last_name"], row["provider_first_name"]), ("Mu\xf1oz", "Jos\xe9"))

    def test_header_drift_union_and_spelling(self):
        root = self.root
        write(root, "payers/WH-PAYER-MCD2/2028-01/MCD2_MEDICAL_20280105.csv",
              "CLAIM_ID,RENDERING_NPI,PAID\nc1,111,5\n")
        write(root, "payers/WH-PAYER-C2/2028-01/C2_MEDICAL_20280105.csv",
              "Claim Id,Rendering Npi,Paid\nc2,222,6\n")
        write(root, "payers/WH-PAYER-MA1/2028-01/MA1_MEDICAL_20280105.csv",
              "claim_id,rendering_provider_npi,paid,adjudication_reason\nc3,333,7,X\n")
        out = defaultdict(list)
        for rel in ("payers/WH-PAYER-MCD2/2028-01/MCD2_MEDICAL_20280105.csv",
                    "payers/WH-PAYER-C2/2028-01/C2_MEDICAL_20280105.csv",
                    "payers/WH-PAYER-MA1/2028-01/MA1_MEDICAL_20280105.csv"):
            for t, r in self.parse(rel).items():
                out[t].extend(r)
        self.assertEqual([r["claim_id"] for r in out["payers_medical"]], ["c1", "c2", "c3"])  # nothing failed
        union = set().union(*[set(r) for r in out["payers_medical"]])
        self.assertTrue({"rendering_npi", "rendering_provider_npi", "adjudication_reason"} <= union)
        drift = {d["relpath"].split("/")[1]: d for d in out["flat_schema_drift"]}
        self.assertEqual(drift["WH-PAYER-C2"]["issue"], "raw_header_spelling")
        self.assertEqual(drift["WH-PAYER-MA1"]["issue"], "columns_added_removed")
        self.assertEqual(json.loads(drift["WH-PAYER-MA1"]["added_columns"]), ["rendering_provider_npi", "adjudication_reason"])
        self.assertEqual(json.loads(drift["WH-PAYER-MA1"]["removed_columns"]), ["rendering_npi"])
        self.assertNotIn("WH-PAYER-MCD2", drift)  # baseline file is not drift

    def test_ragged_rows_kept(self):
        write(self.root, "ehr/epic/EPIC-TX/2028-01/patient.txt",
              "pat_id|first_name|last_name\nZ1|Ann|Lee\nZ2|Bob\nZ3|Cy|Day|extra\n   \n\n")
        rows = self.parse("ehr/epic/EPIC-TX/2028-01/patient.txt")["ehr_epic_patient"]
        self.assertEqual(len(rows), 3)  # whitespace-only padding line skipped
        self.assertEqual(rows[1]["flat_row_issue"], "short_row:2/3")
        self.assertNotIn("last_name", rows[1])
        self.assertEqual(rows[2]["flat_row_issue"], "long_row:4/3")
        self.assertEqual(json.loads(rows[2]["flat_extra"]), ["extra"])

    def test_free_text_note_file(self):
        write(self.root, "ehr/ecw/WHN-PRACTICE-CA-9/2028-01/note_20280105_5551212.txt",
              "Patient seen today, doing well.\nPlan: follow up in 3 months.\n")
        out = self.parse("ehr/ecw/WHN-PRACTICE-CA-9/2028-01/note_20280105_5551212.txt")
        self.assertEqual(list(out), ["ehr_ecw_note_text"])
        r = out["ehr_ecw_note_text"][0]
        self.assertEqual(r["file_date"], "2028-01-05")
        self.assertEqual(r["note_id"], "5551212")
        self.assertEqual(r["instance"], "WHN-PRACTICE-CA-9")
        self.assertEqual(r["drop"], "2028-01")
        self.assertTrue(r["text"].startswith("Patient seen today, doing well."))

    def test_truncated_tiny_delimited_file_is_not_a_note(self):
        write(self.root, "ehr/epic/EPIC-TX/2028-01/order_med_2.txt", "order_med_id|order_class_c|description\n1|2")
        out = self.parse("ehr/epic/EPIC-TX/2028-01/order_med_2.txt")
        self.assertEqual(list(out), ["ehr_epic_order_med_2"])
        self.assertEqual(out["ehr_epic_order_med_2"][0]["flat_row_issue"], "short_row:2/3")

    def test_empty_and_header_only(self):
        write(self.root, "cms_cclf_csv/parta_diagnosis_code/P.A9999.ACO.ZC4Y27.D1.T1.csv", "")
        write(self.root, "cms_cclf_csv/parta_demo_codes/P.A9999.ACO.ZCAY27.D1.T1.csv", "a,b,c\n")
        self.assertEqual(self.parse("cms_cclf_csv/parta_diagnosis_code/P.A9999.ACO.ZC4Y27.D1.T1.csv"), {})
        self.assertEqual(self.parse("cms_cclf_csv/parta_demo_codes/P.A9999.ACO.ZCAY27.D1.T1.csv"), {})


@unittest.skipUnless(RAW.exists(), "synthetic release sample not present")
class RealSampleSmoke(unittest.TestCase):
    """Parse a few real files from each folder; every folder must yield rows."""

    def sample(self, pattern, n=3, seed=11):
        files = [p for p in RAW.glob(pattern) if "private" not in p.parts]
        self.assertTrue(files, pattern)
        random.Random(seed).shuffle(files)
        return files[:n]

    def rows_for(self, pattern, n=3):
        flat.reset_schema_registry()
        tables = Counter()
        for p in self.sample(pattern, n):
            self.assertTrue(flat.detect(p), p)
            ctx = {"relpath": str(p.relative_to(RAW))}
            for t, row in flat.parse(p, ctx):
                self.assertIsInstance(t, str)
                self.assertRegex(t, r"^[a-z0-9_]+$")
                self.assertTrue(all(isinstance(k, str) for k in row))
                tables[t] += 1
        return tables

    def test_ehr_epic(self):
        t = self.rows_for("ehr/epic/EPIC-*/*/patient.txt")
        self.assertGreater(t["ehr_epic_patient"], 0)
        t = self.rows_for("ehr/epic/EPIC-*/*/hno_note_text.txt")
        self.assertGreater(t["ehr_epic_hno_note_text"], 0)
        t = self.rows_for("ehr/epic/EPIC-*/*/order_results.txt")
        self.assertGreater(t["ehr_epic_order_results"], 0)

    def test_ehr_athena(self):
        self.assertGreater(self.rows_for("ehr/athena/*/*/PATIENT.csv")["ehr_athena_patient"], 0)
        self.assertGreater(self.rows_for("ehr/athena/*/*/CLINICALRESULTOBSERVATION.csv")["ehr_athena_clinicalresultobservation"], 0)

    def test_ehr_ecw(self):
        self.assertGreater(self.rows_for("ehr/ecw/*/*/patients.csv")["ehr_ecw_patients"], 0)
        self.assertGreater(self.rows_for("ehr/ecw/*/*/labdatadetail.csv")["ehr_ecw_labdatadetail"], 0)

    def test_payers_each_payer(self):
        for payer_dir in sorted(p for p in (RAW / "payers").iterdir() if p.is_dir()):
            payer = payer_dir.name[len("WH-PAYER-"):]
            for kind in ("MEDICAL", "ELIGIBILITY"):
                files = [p for p in payer_dir.glob(f"*/{payer}_{kind}_*") if "RESEND" not in p.name]
                self.assertTrue(files, (payer, kind))
                flat.reset_schema_registry()
                seen = 0
                for p in files[:2]:
                    for t, row in flat.parse(p, {"relpath": str(p.relative_to(RAW))}):
                        if t == f"payers_{kind.lower()}":
                            seen += 1
                            self.assertEqual(row["payer"], payer)
                            self.assertIn("member_id", row)
                self.assertGreater(seen, 0, (payer, kind))

    def test_payer_headers_unify_across_styles(self):
        flat.reset_schema_registry()
        cols = {}
        for payer in ("MCD2", "C2", "C3", "MCD1"):  # snake csv, "Title Case" csv, camelCase txt, UPPER pipe txt
            p = sorted((RAW / "payers" / f"WH-PAYER-{payer}").glob(f"2027-*/{payer}_MEDICAL_*"))[0]
            for t, row in flat.parse(p, {"relpath": str(p.relative_to(RAW))}):
                if t == "payers_medical":
                    cols[payer] = set(row)
                    break
        for payer, c in cols.items():
            self.assertIn("claim_id", c, payer)
            self.assertIn("member_id", c, payer)
            self.assertIn("dx1", c, payer)
        self.assertEqual(len(cols), 4)

    def test_cclf_alr_programs_rosters_sdoh(self):
        t = self.rows_for("cms_cclf_csv/partd_claims/*.csv")
        self.assertGreater(t["cclf_partd_claims"], 0)
        t = self.rows_for("cms_cclf_csv/beneficiary_demographics/*.csv")
        self.assertGreater(t["cclf_beneficiary_demographics"], 0)
        t = self.rows_for("cms_alr/*/*_1-1.csv")
        self.assertGreater(t["alr_aalr1_assigned_beneficiaries"], 0)
        t = self.rows_for("cms_alr/*/*_1-4.csv")
        self.assertGreater(t["alr_aalr4_assigned_beneficiaries_tin_npi"], 0)
        t = self.rows_for("programs/mssp/*/*QALR*.csv")
        self.assertGreater(t["programs_mssp_qalr"], 0)
        t = self.rows_for("programs/WH-PAYER-*/*/*CARE_GAPS*.csv")
        self.assertGreater(t["programs_care_gaps"], 0)
        t = self.rows_for("rosters/*/*.csv")
        self.assertGreater(t["rosters_aco_provider_roster"], 0)
        t = self.rows_for("sdoh_vendor/*/*.csv")
        self.assertGreater(t["sdoh_vendor"], 0)

    def test_fixed_width_cclf_folder_skipped(self):
        for p in self.sample("cms_cclf/*/*", 5):
            self.assertFalse(flat.detect(p))
            self.assertEqual(list(flat.parse(p, {"relpath": str(p.relative_to(RAW))})), [])

    def test_fhir_each_resource_type(self):
        want = {
            "Patient": "fhir_patient", "Encounter": "fhir_encounter", "Observation": "fhir_observation",
            "Condition": "fhir_condition", "MedicationRequest": "fhir_medicationrequest",
            "DocumentReference": "fhir_documentreference", "Appointment": "fhir_appointment",
            "AllergyIntolerance": "fhir_allergyintolerance", "Immunization": "fhir_immunization",
        }
        for rtype, table in want.items():
            n = 0
            files = [p for p in RAW.glob(f"fhir_bulk/DEEPCHART/*/{rtype}.ndjson")] or self.sample(f"fhir_bulk/*/*/{rtype}.ndjson", 5)
            files += self.sample(f"ehr/fhir/*/*/{rtype}.ndjson", 2) if rtype in ("Patient", "Observation") else []
            for p in files[:6]:
                self.assertTrue(fhir.detect(p))
                for t, row in fhir.parse(p, {}):
                    if t == table:
                        n += 1
                        self.assertIn("id", row)
                        self.assertIn("_resource", row)
                        self.assertEqual(json.loads(row["_resource"])["resourceType"], rtype)
                    else:
                        self.assertEqual(t, "fhir_parse_error")
            self.assertGreater(n, 0, rtype)

    def test_fhir_documentreference_text_decoded(self):
        p = self.sample("fhir_bulk/*/*/DocumentReference.ndjson", 1)[0]
        rows = [r for t, r in fhir.parse(p, {}) if t == "fhir_documentreference"]
        self.assertTrue(rows)
        self.assertTrue(any(r["text"] and len(r["text"]) > 20 for r in rows))
        self.assertTrue(all(r["text_decode_error"] is None for r in rows))

    def test_fhir_truncated_delivery_lands_in_parse_error(self):
        p = RAW / "fhir_bulk/WHN-PRACTICE-PA-4/2027-06/Immunization.ndjson"
        if not p.exists():
            self.skipTest("known truncated sample file not present")
        out = Counter(t for t, _ in fhir.parse(p, {}))
        self.assertGreater(out["fhir_parse_error"], 0)


if __name__ == "__main__":
    unittest.main()
