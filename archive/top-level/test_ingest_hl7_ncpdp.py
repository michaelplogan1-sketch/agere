"""Tests for clinical_core.agere.ingest.hl7 and .ncpdp (python -m unittest test_ingest_hl7_ncpdp)."""
import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from clinical_core.agere.ingest import hl7, ncpdp

RAW = Path("/Users/mikel/synthetic-patient-ecosystem/releases/healthcare-warehouse-v2.12-sample-0.30-native/raw")
FS, RS, GS = "\x1c", "\x1e", "\x1d"


def _write(text, suffix, binary=None):
    fd, p = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(binary if binary is not None else text.encode("utf-8"))
    return Path(p)


def _rows(path, mod, ctx=None):
    out = {}
    for t, r in mod.parse(path, ctx or {"relpath": "t.hl7"}):
        out.setdefault(t, []).append(r)
    return out


def seg(name, fields, n=None):
    """Build a segment with fields placed at HL7 field numbers: seg("PV1", {2: "I", 10: "ED"})."""
    top = max(fields)
    return "|".join([name] + [fields.get(i, "") for i in range(1, top + 1)])


ADT = "\r".join([
    "MSH|^~\\&|ADT|HOSP1^FAC|AGERE|CLIENT|20290106081500-0500||ADT^A01^ADT_A01|MSG0001|P|2.5.1",
    "EVN|A01|20290106081500|||||20290106080000",
    "PID|1||MRN123^^^HOSP1^MR~SSN9^^^USA^SS~ALT77^^^OTHER^PI||SMITH\\T\\JONES^ANNA^Q||19721007|F||2106-3^White^HL70005"
    "|5806 River St^^Marietta^GA^30006^USA",
    seg("PV1", {1: "1", 2: "I", 3: "4W^401^B^HOSP1", 7: "1234567893^DOE^JOHN", 10: "ED", 19: "V0001",
                44: "20290106080000", 45: "20290108120000"}),
    "DG1|1||I50.9^Heart failure\\F\\unspecified^I10||20290106081500|A",
    "DG1|2||E11.9^^I10|Type 2 diabetes|20290106081500|W",
    seg("IN1", {1: "1", 2: "PLAN9^Gold PPO", 3: "CO55", 4: "Acme Health", 8: "GRP1", 9: "Group One", 36: "POL-36",
                49: "MEM-49"}),
    "",
])
ADT_A03 = ADT.replace("ADT^A01", "ADT^A03").replace("EVN|A01", "EVN|A03").replace("MSG0001", "MSG0002")
ADT_PV1_DISCHARGE = "\r".join([
    "MSH|^~\\&|ADT|HOSP1|AGERE|CLIENT|20290108120000||ADT^A03^ADT_A03|MSG0003|P|2.5.1",
    "PID|1||MRN123^^^HOSP1^MR||SMITH^ANNA||19721007|F",
    seg("PV1", {1: "1", 2: "I", 3: "4W", 19: "V0001", 36: "01", 44: "20290106080000", 45: "20290108120000"}),
])

ORU = "\r\n".join([
    "FHS|^~\\&|ORU|REFL",
    "BHS|^~\\&|ORU|REFL",
    "MSH|^~\\&|LIS|REFLAB|EHR|PRAC1|20290120003000||ORU^R01^ORU_R01|ORU0001|P|2.5.1",
    "PID|1||ICE-1^^^PRAC1^MR||DAVIS^KEITH||19870512|M",
    "OBR|1|PLC-1|FIL-1|2345-7^Glucose^LN~GLU^Glucose^L|||20290119083000|||||||20290119090000||||||||20290120003000|||F",
    "OBX|1|NM|2345-7^Glucose^LN~GLU^Glucose^L||102|mg/dL|70-99|H|||F|||20290119083000|||||||||Acme Lab",
    "OBX|2|NM|2160-0^Creatinine^LN||0.96|mg/dL|0.6-1.3||||C|||20290119083000",
    "OBX|3|ST|98979-8^eGFR^LN||>60 \\F\\ approx\\.br\\second line|mL/min/1.73m2|||||F",
    "NTE|1||Corrected result \\S\\ see lab",
    "MSH|^~\\&|LIS|REFLAB|EHR|PRAC1|20290120010000||ORU^R01^ORU_R01|ORU0002|P|2.5.1",
    "PID|1||ICE-2^^^PRAC1^MR||LEE^JO||19600101|F",
    "OBR|1|PLC-2||2951-2^Sodium^LN|||20290119100000" + "|" * 18 + "X",
    "OBX|1|NM|2951-2^Sodium^LN||137|mmol/L|||||X",
    "BTS|2",
    "FTS|1",
])


class Hl7Tests(unittest.TestCase):
    def test_detect(self):
        a = _write(ADT, ".hl7")
        b = _write("", ".hl7", binary=b"\x0b" + ADT.encode() + b"\x1c\r")
        c = _write("hello", ".txt")
        d = _write("", ".txt", binary=b"00" + FS.encode() + b"T1")
        try:
            self.assertTrue(hl7.detect(a))
            self.assertTrue(hl7.detect(b))
            self.assertFalse(hl7.detect(c))
            self.assertFalse(hl7.detect(d))
        finally:
            for p in (a, b, c, d):
                p.unlink()

    def test_adt_a01_message(self):
        p = _write(ADT, ".hl7")
        try:
            r = _rows(p, hl7)
        finally:
            p.unlink()
        self.assertNotIn("hl7_parse_issue", r)
        m = r["hl7_message"][0]
        self.assertEqual(m["message_key"], "t.hl7:1")
        self.assertEqual(m["message_type"], "ADT^A01")
        self.assertEqual(m["message_control_id"], "MSG0001")
        self.assertEqual(m["version"], "2.5.1")
        self.assertEqual(m["sending_facility"], "HOSP1")
        self.assertEqual(m["message_datetime"], "2029-01-06T08:15:00-05:00")
        self.assertEqual(m["message_timezone_offset"], "-05:00")
        self.assertEqual(m["message_datetime_utc"], "2029-01-06T13:15:00Z")
        # PID-3 all repetitions
        import json
        ids = json.loads(m["patient_ids_json"])
        self.assertEqual([i["id"] for i in ids], ["MRN123", "SSN9", "ALT77"])
        self.assertEqual(ids[1]["id_type"], "SS")
        self.assertEqual(m["patient_id"], "MRN123")
        self.assertEqual(m["patient_id_assigning_authority"], "HOSP1")
        self.assertEqual(m["patient_id_count"], 3)
        # escape \T\ -> & in family name
        self.assertEqual(m["patient_name_family"], "SMITH&JONES")
        self.assertEqual(m["patient_name_given"], "ANNA")
        self.assertEqual(m["patient_dob"], "1972-10-07")
        self.assertEqual(m["patient_sex"], "F")
        self.assertEqual(m["patient_race_code"], "2106-3")
        self.assertEqual(m["patient_address_city"], "Marietta")
        self.assertEqual(m["patient_address_zip"], "30006")
        # PV1 / EVN
        self.assertEqual(m["patient_class"], "I")
        self.assertEqual(m["assigned_location"], "4W")
        self.assertEqual(m["assigned_location_bed"], "B")
        self.assertEqual(m["attending_npi"], "1234567893")
        self.assertEqual(m["attending_name"], "JOHN DOE")
        self.assertEqual(m["hospital_service"], "ED")
        self.assertEqual(m["visit_number"], "V0001")
        self.assertEqual(m["admit_datetime"], "2029-01-06T08:00:00")
        self.assertEqual(m["discharge_datetime"], "2029-01-08T12:00:00")
        self.assertEqual(m["event_type_code"], "A01")
        # DG1
        dx = r["hl7_diagnosis"]
        self.assertEqual(len(dx), 2)
        self.assertEqual(dx[0]["diagnosis_code"], "I50.9")
        self.assertEqual(dx[0]["diagnosis_text"], "Heart failure|unspecified")  # \F\ -> |
        self.assertEqual(dx[0]["coding_system"], "I10")
        self.assertEqual(dx[0]["diagnosis_type"], "A")
        self.assertEqual(dx[1]["diagnosis_type"], "W")
        self.assertEqual(dx[1]["diagnosis_description"], "Type 2 diabetes")
        self.assertTrue(all(d["message_key"] == "t.hl7:1" for d in dx))
        # IN1
        ins = r["hl7_insurance"][0]
        self.assertEqual(ins["plan_id"], "PLAN9")
        self.assertEqual(ins["company_id"], "CO55")
        self.assertEqual(ins["policy_number"], "POL-36")
        self.assertEqual(ins["insured_id"], "MEM-49")
        self.assertEqual(ins["member_id"], "MEM-49")

    def test_adt_a03_discharge_fields_and_multiple_messages(self):
        p = _write(ADT + "\r" + ADT_A03 + "\r" + ADT_PV1_DISCHARGE, ".hl7")
        try:
            r = _rows(p, hl7)
        finally:
            p.unlink()
        ms = r["hl7_message"]
        self.assertEqual([m["message_key"] for m in ms], ["t.hl7:1", "t.hl7:2", "t.hl7:3"])
        self.assertEqual(ms[1]["message_type"], "ADT^A03")
        self.assertEqual(ms[2]["discharge_disposition"], "01")
        self.assertEqual(ms[2]["discharge_datetime"], "2029-01-08T12:00:00")
        self.assertEqual(ms[2]["message_datetime_utc"], None)  # no offset sent
        self.assertEqual({d["message_key"] for d in r["hl7_diagnosis"]}, {"t.hl7:1", "t.hl7:2"})

    def test_oru_corrected_obx_escapes_and_nte(self):
        for text in (ORU, ORU.replace("\r\n", "\n"), ORU.replace("\r\n", "\r")):
            p = _write(text, ".hl7")
            try:
                r = _rows(p, hl7)
            finally:
                p.unlink()
            self.assertNotIn("hl7_parse_issue", r)
            self.assertEqual(len(r["hl7_message"]), 2)  # FHS/BHS/BTS/FTS are not messages
            self.assertEqual(len(r["hl7_order"]), 2)
            obs = r["hl7_observation"]
            self.assertEqual(len(obs), 4)
            o1, o2, o3, o4 = obs
            self.assertEqual(o1["observation_code"], "2345-7")
            self.assertEqual(o1["observation_text"], "Glucose")
            self.assertEqual(o1["observation_coding_system"], "LN")
            self.assertEqual(o1["loinc_code"], "2345-7")
            self.assertEqual(o1["local_code"], "GLU")
            self.assertEqual(o1["value"], "102")
            self.assertEqual(o1["value_numeric"], 102.0)
            self.assertEqual(o1["units"], "mg/dL")
            self.assertEqual(o1["reference_range"], "70-99")
            self.assertEqual(o1["abnormal_flags"], "H")
            self.assertEqual(o1["result_status"], "F")
            self.assertEqual(o1["is_corrected"], 0)
            self.assertEqual(o1["observation_datetime"], "2029-01-19T08:30:00")
            self.assertEqual(o1["performing_org"], "Acme Lab")
            # OBR context
            self.assertEqual(o1["placer_order_number"], "PLC-1")
            self.assertEqual(o1["filler_order_number"], "FIL-1")
            self.assertEqual(o1["obr_universal_service_code"], "2345-7")
            self.assertEqual(o1["obr_observation_datetime"], "2029-01-19T08:30:00")
            self.assertEqual(o1["obr_specimen_received_datetime"], "2029-01-19T09:00:00")
            self.assertEqual(o1["obr_result_status"], "F")
            self.assertEqual(o1["message_key"], "t.hl7:1")
            # corrected OBX
            self.assertEqual(o2["result_status"], "C")
            self.assertEqual(o2["result_status_label"], "corrected")
            self.assertEqual(o2["is_corrected"], 1)
            self.assertEqual(o2["is_final"], 0)
            # escapes in value + NTE attached
            self.assertEqual(o3["value"], ">60 | approx\nsecond line")
            self.assertEqual(o3["note"], "Corrected result ^ see lab")
            # second message: cancelled
            self.assertEqual(o4["message_key"], "t.hl7:2")
            self.assertEqual(o4["result_status"], "X")
            self.assertEqual(o4["is_cancelled"], 1)
            self.assertEqual(o4["order_ordinal"], 1)

    def test_mllp_framing(self):
        p = _write("", ".hl7", binary=b"\x0b" + ADT.encode() + b"\x1c\r\x0b" + ADT_A03.encode() + b"\x1c\r")
        try:
            r = _rows(p, hl7)
        finally:
            p.unlink()
        self.assertEqual(len(r["hl7_message"]), 2)
        self.assertNotIn("hl7_parse_issue", r)

    def test_malformed_input_reports_issues(self):
        bad = "garbage line\r" + "MSH|^~\\&|A|B\r" + "PID|1||X^^^A^MR\rthis is a continuation\r"
        p = _write(bad, ".hl7")
        try:
            r = _rows(p, hl7)
        finally:
            p.unlink()
        codes = {i["issue_code"] for i in r["hl7_parse_issue"]}
        self.assertTrue({"leading_garbage", "short_msh", "continuation_line"} <= codes)
        self.assertEqual(len(r["hl7_message"]), 1)  # still emitted, nothing lost
        p = _write("nothing here", ".hl7")
        try:
            r = _rows(p, hl7)
        finally:
            p.unlink()
        self.assertEqual([i["issue_code"] for i in r["hl7_parse_issue"]][-1], "no_msh")

    def test_norm_dt(self):
        self.assertEqual(hl7.norm_dt("20290106")[0], "2029-01-06")
        self.assertEqual(hl7.norm_dt("202901060815")[0], "2029-01-06T08:15:00")
        self.assertEqual(hl7.norm_dt("20290106081500+0100"), ("2029-01-06T08:15:00+01:00", "+01:00"))
        self.assertEqual(hl7.norm_dt("20291306"), (None, None))
        self.assertEqual(hl7.norm_dt(""), (None, None))

    def test_real_files_smoke(self):
        base = RAW / "hl7"
        if not base.is_dir():
            self.skipTest("sample feed not present")
        files = []
        for sub in ("adt", "oru"):
            fs = sorted((base / sub).glob("*/*.hl7"))
            files += fs[:: max(1, len(fs) // 12)][:12]
        self.assertGreater(len(files), 5)
        counts, issues = Counter(), Counter()
        for f in files:
            self.assertTrue(hl7.detect(f), f)
            for t, row in hl7.parse(f, {"relpath": str(f.relative_to(RAW))}):
                counts[t] += 1
                if t == "hl7_parse_issue":
                    issues[row["issue_code"]] += 1
                else:
                    self.assertTrue(row["message_key"])
        print("\nhl7 smoke:", dict(counts), "files", len(files))
        self.assertGreater(counts["hl7_message"], 0)
        self.assertGreater(counts["hl7_observation"], 0)
        self.assertGreater(counts["hl7_diagnosis"], 0)
        self.assertLess(counts["hl7_parse_issue"], 0.01 * counts["hl7_message"], dict(issues))


def _ncpdp_record(trace, txn="B1", status="P", rejects=(), paid="0", pay="17.4", rx="9520442693", qty="30000",
                  fill="0", ds="30", dos="20260911"):
    hdr = f"990001D0{txn}CMRX      1011167942964     {dos}"
    segs = [
        f"{FS}AM04{FS}C2C1227F77B3A{FS}C1GRPC1",
        f"{FS}AM01{FS}C419621225{FS}C52",
        f"{FS}AM07{FS}EM1{FS}D2{rx}{FS}E103{FS}D700093731405{FS}E7{qty}{FS}D3{fill}{FS}D5{ds}{FS}D80",
        f"{FS}AM03{FS}EZ01{FS}DB1241139314",
        f"{FS}AM11{FS}D916.18{FS}DC1.22{FS}DU17.4",
    ]
    resp = f"{FS}AM21{FS}AN{status}"
    if rejects:
        resp += f"{FS}FA{len(rejects)}" + "".join(f"{FS}FB{c}" for c in rejects)
    resp += f"{RS}{FS}AM23{FS}F5{pay}{FS}F9{paid}"
    return f"G1{FS}{trace}{GS}{hdr}{RS}" + RS.join(segs) + GS + RS + resp.lstrip(RS)


def _ncpdp_file(records, trailer=True):
    lines = [f"00{FS}T280148220{FS}20261006{FS}D0{FS}WH-PAYER-C1"] + records
    if trailer:
        lines.append(f"99{FS}{len(records)}")
    return "\n".join(lines) + "\n"


class NcpdpTests(unittest.TestCase):
    def _parse(self, text, name="C1_NCPDP_D0_20261006.txt"):
        p = _write(text, ".txt")
        try:
            return _rows(p, ncpdp, {"relpath": name})
        finally:
            p.unlink()

    def test_detect(self):
        p = _write(_ncpdp_file([_ncpdp_record("T1")]), ".txt")
        q = _write(ADT, ".hl7")
        try:
            self.assertTrue(ncpdp.detect(p))
            self.assertFalse(ncpdp.detect(q))
        finally:
            p.unlink()
            q.unlink()

    def test_paid_claim(self):
        r = self._parse(_ncpdp_file([_ncpdp_record("WHN-RXC-1")]))
        self.assertNotIn("ncpdp_parse_issue", r)
        c = r["ncpdp_claim"][0]
        self.assertEqual(c["claim_key"], "C1_NCPDP_D0_20261006.txt:1")
        self.assertEqual(c["claim_ref"], "WHN-RXC-1")
        self.assertEqual(c["transaction_code"], "B1")
        self.assertEqual(c["transaction_type"], "claim")
        self.assertEqual(c["response_status_code"], "P")
        self.assertEqual(c["response_status"], "paid")
        self.assertIsNone(c["reject_codes"])
        self.assertEqual((c["bin_number"], c["pcn"], c["group_id"]), ("990001", "CMRX", "GRPC1"))
        self.assertEqual(c["cardholder_id"], "C1227F77B3A")
        self.assertEqual(c["patient_dob"], "1962-12-25")
        self.assertEqual(c["patient_gender"], "F")
        self.assertEqual(c["date_of_service"], "2026-09-11")
        self.assertEqual(c["rx_service_ref_number"], "9520442693")
        self.assertEqual(c["rx_service_ref_qualifier"], "1")
        self.assertEqual(c["fill_number"], 0)
        self.assertEqual(c["product_service_id"], "00093731405")
        self.assertEqual(c["product_service_id_qualifier"], "03")
        self.assertEqual(c["product_service_id_type"], "NDC")
        self.assertEqual(c["quantity_dispensed"], 30)
        self.assertEqual(c["days_supply"], 30)
        self.assertEqual(c["daw_code"], "0")
        self.assertEqual(c["prescriber_id"], "1241139314")
        self.assertEqual(c["prescriber_id_type"], "NPI")
        self.assertEqual(c["service_provider_id"], "1167942964")
        self.assertEqual(c["service_provider_id_type"], "NPI")
        self.assertEqual(c["ingredient_cost_submitted"], 16.18)
        self.assertEqual(c["dispensing_fee_submitted"], 1.22)
        self.assertEqual(c["gross_amount_due"], 17.4)
        self.assertEqual(c["patient_pay_amount"], 17.4)
        self.assertEqual(c["total_amount_paid"], 0.0)
        self.assertEqual(c["plan_paid_amount"], 0.0)
        self.assertEqual(c["is_compound"], 0)
        self.assertEqual(c["file_batch_id"], "T280148220")
        self.assertEqual(c["file_trailer_count"], 1)
        import json
        x = json.loads(c["_extra"])
        self.assertEqual(x["AM07.D2"], "9520442693")
        self.assertEqual(x["AM07.E7"], "30000")
        self.assertEqual(x["AM21.AN"], "P")

    def test_rejected_claim_with_codes(self):
        r = self._parse(_ncpdp_file([_ncpdp_record("T2", status="R", rejects=("70", "75"), pay="0", paid="0")]))
        c = r["ncpdp_claim"][0]
        self.assertEqual(c["response_status"], "rejected")
        self.assertEqual(c["reject_codes"], "70,75")
        self.assertEqual(c["reject_count"], 2)
        self.assertNotIn("ncpdp_parse_issue", r)

    def test_reversal_and_rebill(self):
        r = self._parse(_ncpdp_file([_ncpdp_record("T3"), _ncpdp_record("T3", txn="B2", status="A"),
                                     _ncpdp_record("T3", txn="B3", paid="12.5", pay="4.9", qty="7000", ds="7")]))
        a, b, c = r["ncpdp_claim"]
        self.assertEqual(b["transaction_code"], "B2")
        self.assertEqual(b["transaction_type"], "reversal")
        self.assertEqual(b["response_status"], "approved")
        self.assertEqual(c["transaction_type"], "rebill")
        self.assertEqual(c["quantity_dispensed"], 7)
        self.assertEqual(c["plan_paid_amount"], 12.5)
        self.assertEqual({x["claim_ref"] for x in (a, b, c)}, {"T3"})
        self.assertEqual([x["file_record_ordinal"] for x in (a, b, c)], [1, 2, 3])

    def test_duplicate_status_and_fill_number(self):
        r = self._parse(_ncpdp_file([_ncpdp_record("T4", status="D", fill="154")]))
        c = r["ncpdp_claim"][0]
        self.assertEqual(c["response_status"], "duplicate")
        self.assertEqual(c["fill_number"], 154)

    def test_line_fold_repair_and_trailer_checks(self):
        rec = _ncpdp_record("T5")
        cut = rec.index("AM07") + 6
        folded = rec[:cut] + "\n" + rec[cut:]
        r = self._parse(_ncpdp_file([folded, _ncpdp_record("T6")], trailer=False))
        self.assertEqual(len(r["ncpdp_claim"]), 2)
        self.assertEqual(r["ncpdp_claim"][0]["response_status"], "paid")
        codes = [i["issue_code"] for i in r["ncpdp_parse_issue"]]
        self.assertIn("line_folding_repaired", codes)
        self.assertIn("missing_trailer", codes)

    def test_truncated_record_flagged(self):
        rec = _ncpdp_record("T7")
        r = self._parse(_ncpdp_file([rec[: rec.index("AM03")], _ncpdp_record("T8")]))
        codes = [i["issue_code"] for i in r["ncpdp_parse_issue"]]
        self.assertIn("missing_response", codes)
        self.assertEqual(len(r["ncpdp_claim"]), 2)

    def test_amount_overpunch_and_quantity(self):
        self.assertEqual(ncpdp._amount("1618{"), 161.80)
        self.assertEqual(ncpdp._amount("123J"), -12.31)
        self.assertEqual(ncpdp._qty("30000"), 30)
        self.assertEqual(ncpdp._qty("7500"), 7.5)

    def test_real_files_smoke(self):
        base = RAW / "ncpdp"
        if not base.is_dir():
            self.skipTest("sample feed not present")
        fs = sorted(base.glob("*/*/*.txt"))
        files = fs[:: max(1, len(fs) // 20)][:20]
        self.assertGreater(len(files), 5)
        counts, issues, status, txn = Counter(), Counter(), Counter(), Counter()
        for f in files:
            self.assertTrue(ncpdp.detect(f), f)
            for t, row in ncpdp.parse(f, {"relpath": str(f.relative_to(RAW))}):
                counts[t] += 1
                if t == "ncpdp_parse_issue":
                    issues[row["issue_code"]] += 1
                else:
                    status[row["response_status"]] += 1
                    txn[row["transaction_code"]] += 1
                    self.assertTrue(row["product_service_id"] or row["response_status"] is None)
        print("\nncpdp smoke:", dict(counts), dict(status), dict(txn), dict(issues), "files", len(files))
        self.assertGreater(counts["ncpdp_claim"], 0)
        self.assertLess(counts["ncpdp_parse_issue"], 0.01 * counts["ncpdp_claim"], dict(issues))
        self.assertIn("paid", status)


if __name__ == "__main__":
    unittest.main()
