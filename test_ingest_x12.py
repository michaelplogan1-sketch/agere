"""Tests for clinical_core.agere.ingest.x12 (run: .venv/bin/python -m unittest test_ingest_x12)."""
import os
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path

from clinical_core.agere.ingest import x12

RAW = Path("/Users/mikel/synthetic-patient-ecosystem/releases/healthcare-warehouse-v2.12-sample-0.30-native/raw")


def isa(ctl, sender="SENDER", receiver="RECEIVER"):
    return (f"ISA*00*          *00*          *ZZ*{sender:<15}*ZZ*{receiver:<15}*280620*0800*^*00501*{ctl}*0*P*:~")


def wrap(body, ctl="000000101", gs="HC", ver="005010X222A1", st="837", st02="0001", seg_extra=0, nl=False):
    n = body.count("~") + 2
    text = (isa(ctl) + f"GS*{gs}*SENDER*RECEIVER*20280620*0800*101*X*{ver}~ST*{st}*{st02}*{ver}~" + body
            + f"SE*{n}*{st02}~GE*1*101~IEA*1*{ctl}~")
    return text.replace("~", "~\n") if nl else text


P837 = (
    "BHT*0019*00*1*20280620*0800*CH~"
    "HL*1**20*1~NM1*85*2*ACME CLINIC*****XX*1234567893~N3*1 MAIN~N4*TOWN*ST*00000~REF*EI*123456789~"
    "HL*2*1*22*0~SBR*P*18*******CI~NM1*IL*1*DOE*JANE****MI*MEM001~DMG*D8*19800102*F~"
    "NM1*PR*2*PAYER ONE*****PI*PAYER1~"
    "CLM*PCN1*150.50***11:B:1*Y*A*Y*Y~DTP*434*D8*20280601~REF*F8*ORIGREF1~REF*EA*MRN9~REF*D9*CID9~"
    "HI*ABK:J069*ABF:E669~NM1*82*1*RENDER*DOC****XX*1111111112~"
    "LX*1~SV1*HC:99213:25*100.25*UN*1*11**1:2~DTP*472*D8*20280601~"
    "LX*2~SV1*HC:36415*50.25*UN*2***1~DTP*472*D8*20280601~"
    "HL*3*1*22*0~SBR*P*18*******CI~NM1*IL*1*ROE*RICK****MI*MEM002~DMG*D8*19700304*M~"
    "NM1*PR*2*PAYER ONE*****PI*PAYER1~"
    "CLM*PCN2*20***11:B:7*Y*A*Y*Y~HI*ABK:Z0000~LX*1~SV1*HC:99211*20*UN*1***1~DTP*472*D8*20280602~"
)

P837I = (
    "BHT*0019*00*1*20280620*0800*CH~"
    "HL*1**20*1~NM1*85*2*BIG HOSPITAL*****XX*1366540470~"
    "HL*2*1*22*0~SBR*P*18**MEDICAID******CI~NM1*IL*1*ORTIZ*DENISE****MI*MEM100~DMG*D8*19630127*F~"
    "NM1*PR*2*PAYER TWO*****PI*PAYER2~"
    "CLM*PCNI*39534.32***11:A:1**A*Y*Y~DTP*435*D8*20280613~CL1*3*1*01~DTP*434*RD8*20280613-20280614~"
    "HI*BK:K8020~HI*BF:E109:::::::Y~HI*DR:419~NM1*71*1*ATTEND*DOC****XX*2222222227~REF*EA*MRNI~"
    "LX*1~SV2*0120*HC:*9908.74*UN*3~DTP*472*D8*20280613~"
    "LX*2~SV2*0300*HC:85025*7856.55*UN*1~DTP*472*RD8*20280613-20280614~"
)

R835 = (
    "BPR*I*1234.56*C*ACH*CCP*01*999999999*DA*000000000*9c4b403c5a**01*999999999*DA*000000000*20280616~"
    "TRN*1*TRACE123*1974344930~DTM*405*20280617~N1*PR*PAYER ONE*PI*PAYER1~N1*PE*CLINIC*XX*1234567893~REF*TJ*123456789~"
    "CLP*PCN1*1*200.00*150.00*10.00*MC*PAYERCCN1*11*1~NM1*QC*1*DOE*JANE****MI*MEM001~NM1*IL*1*DOE*JOHN****MI*INS1~"
    "REF*1L*GRP1~REF*EA*MRN1~DTM*232*20280601~DTM*233*20280602~DTM*050*20280610~"
    "CAS*CO*45*40.00~CAS*PR*2*10.00*1~LQ*HE*M15~MOA*0.8**MA01~"
    "SVC*HC:99213:25*200.00*150.00**1~DTM*472*20280601~CAS*CO*45*40.00~LQ*HE*N130~AMT*B6*150~"
    "CLP*PCN2*22*-80.00*-60.00*0*MC*PAYERCCN2*11*1~NM1*QC*1*ROE*RICK****MI*MEM002~CAS*CO*45*-20.00~"
)

E834 = (
    "BGN*00*REF1*20280405*0700****2~N1*P5*PLAN SPONSOR*FI*974344930~N1*IN*INSURER*FI*999999999~"
    "INS*Y*18*021*28*A***FT~REF*0F*SUB1~REF*1L*POLICY1~DTP*356*D8*20271101~NM1*IL*1*CHAVEZ*JACOB****34*MEM1~"
    "N3*5537 CEDAR ST~N4*COLUMBUS*OH*43085~DMG*D8*20251217*M~HD*021**HLT*MEDICAID*IND~DTP*348*D8*20271101~"
    "INS*Y*18*024*XN*A***FT~REF*0F*SUB2~REF*1L*POLICY1~DTP*357*D8*20271116~NM1*IL*1*MOORE*SUSAN****34*MEM2~"
    "N3*793 PARK ST~N4*CUMBERLAND*MD*21501~DMG*D8*20171017*F~HD*024**HLT*MEDICAID*IND~DTP*348*D8*20260901~"
    "DTP*349*D8*20271116~"
)


def collect(text, name=""):
    t = defaultdict(list)
    for table, row in x12.parse_text(text, name):
        t[table].append(row)
    return t


class Test837P(unittest.TestCase):
    def setUp(self):
        self.t = collect(wrap(P837, ctl="000000201"))

    def test_interchange(self):
        (i,) = self.t["x12_interchange"]
        self.assertEqual(i["isa_control_number"], "000000201")
        self.assertEqual(i["sender_id"], "SENDER")
        self.assertEqual(i["receiver_id"], "RECEIVER")
        self.assertEqual(i["interchange_date"], "2028-06-20")
        self.assertEqual(i["gs_functional_id"], "HC")
        self.assertEqual(i["gs_version"], "005010X222A1")
        self.assertEqual(i["transaction_types"], "837P")
        self.assertEqual(i["_segment_index"], 0)

    def test_claims(self):
        c1, c2 = self.t["x12_837_claim"]
        self.assertEqual(c1["claim_key"], "000000201:0001:1")
        self.assertEqual(c2["claim_key"], "000000201:0001:2")
        self.assertEqual(c1["transaction_type"], "837P")
        self.assertEqual(c1["billing_provider_npi"], "1234567893")
        self.assertEqual(c1["billing_provider_tin"], "123456789")
        self.assertEqual(c1["subscriber_member_id"], "MEM001")
        self.assertEqual(c1["subscriber_dob"], "1980-01-02")
        self.assertEqual(c1["subscriber_gender"], "F")
        self.assertEqual(c1["payer_id"], "PAYER1")
        self.assertEqual(c1["payer_name"], "PAYER ONE")
        self.assertEqual(c1["patient_control_number"], "PCN1")
        self.assertEqual(c1["total_charge"], 150.5)
        self.assertEqual(c1["facility_code"], "11")
        self.assertEqual(c1["place_of_service"], "11")
        self.assertEqual(c1["frequency_code"], "1")
        self.assertEqual(c1["claim_status_label"], "original")
        self.assertEqual(c2["frequency_code"], "7")
        self.assertEqual(c2["claim_status_label"], "replacement")
        self.assertEqual(c1["original_reference"], "ORIGREF1")
        self.assertEqual(c1["medical_record_number"], "MRN9")
        self.assertEqual(c1["claim_id"], "CID9")
        self.assertEqual(c1["statement_from"], "2028-06-01")
        self.assertEqual(c1["diagnosis_count"], 2)
        self.assertEqual(c1["principal_diagnosis"], "J069")
        self.assertEqual(c1["other_diagnosis_count"], 1)
        self.assertEqual(c1["rendering_npi"], "1111111112")
        self.assertEqual(c1["line_count"], 2)
        self.assertEqual(c1["isa_control_number"], "000000201")
        self.assertEqual(c1["st_control_number"], "0001")
        self.assertIsNone(c2["rendering_npi"])

    def test_dx_and_lines(self):
        dx = [d for d in self.t["x12_837_dx"] if d["claim_key"].endswith(":1")]
        self.assertEqual([(d["dx_qualifier"], d["dx_code"], d["dx_sequence"]) for d in dx],
                         [("ABK", "J069", 1), ("ABF", "E669", 2)])
        self.assertEqual(dx[0]["is_principal"], 1)
        lines = [l for l in self.t["x12_837_line"] if l["claim_key"] == "000000201:0001:1"]
        self.assertEqual(len(lines), 2)
        l1, l2 = lines
        self.assertEqual((l1["line_number"], l1["procedure_qualifier"], l1["procedure_code"], l1["modifier_1"]),
                         ("1", "HC", "99213", "25"))
        self.assertEqual((l1["charge"], l1["units"], l1["unit_type"], l1["place_of_service"]), (100.25, 1.0, "UN", "11"))
        self.assertEqual(l1["dx_pointers"], "1:2")
        self.assertEqual(l1["service_date"], "2028-06-01")
        self.assertEqual(l2["units"], 2.0)
        self.assertEqual(l2["dx_pointers"], "1")
        l3 = [l for l in self.t["x12_837_line"] if l["claim_key"] == "000000201:0001:2"]
        self.assertEqual(len(l3), 1)
        self.assertEqual(l3[0]["service_date"], "2028-06-02")

    def test_no_issues_and_newline_wrapped(self):
        self.assertEqual(self.t.get("x12_parse_issue", []), [])
        t2 = collect(wrap(P837, ctl="000000201", nl=True))
        self.assertEqual(len(t2["x12_837_claim"]), 2)
        self.assertEqual(t2.get("x12_parse_issue", []), [])


class Test837I(unittest.TestCase):
    def test_institutional(self):
        t = collect(wrap(P837I, ctl="000000301", ver="005010X223A2"))
        (c,) = t["x12_837_claim"]
        self.assertEqual(c["transaction_type"], "837I")
        self.assertEqual(c["facility_code"], "11")
        self.assertIsNone(c["place_of_service"])
        self.assertEqual(c["frequency_code"], "1")
        self.assertEqual(c["admission_date"], "2028-06-13")
        self.assertEqual((c["statement_from"], c["statement_to"]), ("2028-06-13", "2028-06-14"))
        self.assertEqual((c["admission_type"], c["admission_source"], c["patient_status"]), ("3", "1", "01"))
        self.assertEqual(c["drg"], "419")
        self.assertEqual(c["diagnosis_count"], 2)
        self.assertEqual(c["attending_npi"], "2222222227")
        self.assertEqual(c["medical_record_number"], "MRNI")
        dx = t["x12_837_dx"]
        self.assertEqual([(d["dx_qualifier"], d["dx_code"], d["poa"]) for d in dx], [("BK", "K8020", None), ("BF", "E109", "Y")])
        l1, l2 = t["x12_837_line"]
        self.assertEqual((l1["revenue_code"], l1["procedure_qualifier"], l1["procedure_code"], l1["charge"], l1["units"]),
                         ("0120", "HC", None, 9908.74, 3.0))
        self.assertEqual(l2["procedure_code"], "85025")
        self.assertEqual((l2["service_date"], l2["service_date_to"]), ("2028-06-13", "2028-06-14"))
        self.assertTrue(all(l["claim_key"] == c["claim_key"] for l in (l1, l2)))
        self.assertEqual(t.get("x12_parse_issue", []), [])


class Test835(unittest.TestCase):
    def setUp(self):
        self.t = collect(wrap(R835, ctl="000000401", gs="HP", st="835", ver="005010X221A1"))

    def test_payment(self):
        (p,) = self.t["x12_835_payment"]
        self.assertEqual(p["payment_amount"], 1234.56)
        self.assertEqual(p["payment_method"], "ACH")
        self.assertEqual(p["trace_number"], "TRACE123")
        self.assertEqual(p["payer_name"], "PAYER ONE")
        self.assertEqual(p["payee_npi"], "1234567893")
        self.assertEqual(p["payee_tin"], "123456789")
        self.assertEqual(p["production_date"], "2028-06-17")
        self.assertEqual(p["effective_date"], "2028-06-16")
        self.assertEqual(p["claim_count"], 2)
        self.assertEqual(p["payment_key"], "000000401:0001")

    def test_claims(self):
        c1, c2 = self.t["x12_835_claim"]
        self.assertEqual(c1["claim_key"], "000000401:0001:1")
        self.assertEqual((c1["claim_status_code"], c1["is_reversal"]), ("1", 0))
        self.assertEqual((c1["total_charge"], c1["paid_amount"], c1["patient_responsibility"]), (200.0, 150.0, 10.0))
        self.assertEqual((c1["claim_filing_indicator"], c1["payer_claim_control_number"], c1["facility_code"],
                          c1["frequency_code"]), ("MC", "PAYERCCN1", "11", "1"))
        self.assertEqual((c1["patient_id"], c1["insured_id"]), ("MEM001", "INS1"))
        self.assertEqual((c1["group_policy_number"], c1["medical_record_number"]), ("GRP1", "MRN1"))
        self.assertEqual((c1["statement_from"], c1["statement_to"], c1["received_date"]),
                         ("2028-06-01", "2028-06-02", "2028-06-10"))
        self.assertEqual((c1["cas_count"], c1["cas_total"], c1["cas_co_total"], c1["cas_pr_total"]), (2, 50.0, 40.0, 10.0))
        self.assertEqual(c1["remark_codes"], "HE:M15")
        self.assertEqual(c1["moa_reimbursement_rate"], 0.8)
        self.assertEqual(c1["line_count"], 1)
        self.assertEqual((c2["claim_status_code"], c2["is_reversal"]), ("22", 1))
        self.assertEqual(c2["paid_amount"], -60.0)
        self.assertEqual(c2["claim_key"], "000000401:0001:2")

    def test_adjustments_and_lines(self):
        adj = self.t["x12_835_adjustment"]
        self.assertEqual(len(adj), 4)
        claim_level = [a for a in adj if a["level"] == "claim" and a["claim_key"].endswith(":1")]
        self.assertEqual([(a["group_code"], a["reason_code"], a["amount"], a["quantity"]) for a in claim_level],
                         [("CO", "45", 40.0, None), ("PR", "2", 10.0, 1.0)])
        line_level = [a for a in adj if a["level"] == "line"]
        self.assertEqual(len(line_level), 1)
        self.assertEqual(line_level[0]["line_ordinal"], 1)
        self.assertEqual(line_level[0]["claim_key"], "000000401:0001:1")
        rev = [a for a in adj if a["claim_key"].endswith(":2")]
        self.assertEqual(rev[0]["amount"], -20.0)
        (ln,) = self.t["x12_835_line"]
        self.assertEqual((ln["procedure_qualifier"], ln["procedure_code"], ln["modifier_1"]), ("HC", "99213", "25"))
        self.assertEqual((ln["charge"], ln["paid_amount"], ln["units"]), (200.0, 150.0, 1.0))
        self.assertEqual(ln["service_date"], "2028-06-01")
        self.assertEqual(ln["remark_codes"], "HE:N130")
        self.assertEqual(ln["allowed_amount"], 150.0)
        self.assertEqual(ln["claim_key"], "000000401:0001:1")
        self.assertEqual(self.t.get("x12_parse_issue", []), [])


class Test834(unittest.TestCase):
    def test_add_and_term(self):
        t = collect(wrap(E834, ctl="000000501", gs="BE", st="834", ver="005010X220A1"), "MCD2_834_CHG_20280405.x12")
        add, term = t["x12_834_member"]
        self.assertEqual(add["member_key"], "000000501:0001:1")
        self.assertEqual((add["maintenance_type_code"], add["maintenance_type_label"]), ("021", "add"))
        self.assertEqual((add["subscriber_indicator"], add["relationship_code"], add["maintenance_reason_code"],
                          add["benefit_status_code"]), ("Y", "18", "28", "A"))
        self.assertEqual((add["subscriber_id"], add["group_policy_number"], add["member_id"]), ("SUB1", "POLICY1", "MEM1"))
        self.assertEqual((add["last_name"], add["first_name"]), ("CHAVEZ", "JACOB"))
        self.assertEqual((add["address_1"], add["city"], add["state"], add["postal_code"]),
                         ("5537 CEDAR ST", "COLUMBUS", "OH", "43085"))
        self.assertEqual((add["dob"], add["gender"]), ("2025-12-17", "M"))
        self.assertEqual(add["eligibility_begin_date"], "2027-11-01")
        self.assertEqual(add["benefit_begin_date"], "2027-11-01")
        self.assertEqual((add["insurance_line_code"], add["plan_coverage_description"], add["coverage_level_code"]),
                         ("HLT", "MEDICAID", "IND"))
        self.assertEqual(add["file_type"], "change")
        self.assertEqual(add["sponsor_name"], "PLAN SPONSOR")
        self.assertEqual((term["maintenance_type_code"], term["maintenance_type_label"]), ("024", "cancel_term"))
        self.assertEqual(term["maintenance_reason_code"], "XN")
        self.assertEqual(term["eligibility_end_date"], "2027-11-16")
        self.assertEqual(term["benefit_end_date"], "2027-11-16")
        self.assertEqual(term["member_key"], "000000501:0001:2")
        self.assertEqual(t.get("x12_parse_issue", []), [])

    def test_full_file_type_from_bgn08(self):
        t = collect(wrap(E834.replace("****2~", "****RX~"), ctl="000000502", gs="BE", st="834", ver="005010X220A1"))
        self.assertEqual({m["file_type"] for m in t["x12_834_member"]}, {"full"})


class TestRobustness(unittest.TestCase):
    def test_malformed_counted_not_crashing(self):
        body = ("HL*1**20*1~NM1*85*2*X*****XX*1234567893~HL*2*1*22*0~SBR*P*18*******CI~NM1*IL*1*A*B****MI*M1~"
                "CLM*PCN*notanumber***11:B:1~LX*1~SV1*HC:99213*abc*UN*1~")
        txt = wrap(body, ctl="000000601").replace("SE*", "SE*999*", 1).replace("SE*999*", "SE*999*", 1)
        t = collect(txt)
        self.assertEqual(len(t["x12_837_claim"]), 1)
        reasons = [i["reason"] for i in t["x12_parse_issue"]]
        self.assertTrue(any(r.startswith("bad_amount") for r in reasons), reasons)

    def test_truncated_file(self):
        t = collect(isa("000000701") + "GS*HP*A*B*20280101*0800*1*X*005010X221A1~ST*835*0001~BPR*I*5*C*ACH~"
                    "CLP*P1*1*10*5*0*MC*C1*11*1~")
        self.assertEqual(len(t["x12_835_claim"]), 1)
        self.assertTrue(any(i["reason"].startswith("missing_se") for i in t["x12_parse_issue"]))

    def test_detect(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.x12"
            p.write_text("\n  " + wrap(P837))
            q = Path(d) / "b.txt"
            q.write_text("hello")
            self.assertTrue(x12.detect(p))
            self.assertFalse(x12.detect(q))
            self.assertEqual(Counter(t for t, _ in x12.parse(p, {})).get("x12_837_claim"), 2)

    def test_chunk_boundaries(self):
        text = wrap(P837, ctl="000000801", nl=True)
        whole = list(x12.parse_stream(iter([text])))
        tiny = list(x12.parse_stream(iter([text[i:i + 7] for i in range(0, len(text), 7)])))
        self.assertEqual(whole, tiny)


@unittest.skipUnless(RAW.is_dir(), "sample data not present")
class TestSmokeRealSamples(unittest.TestCase):
    def files(self, folder, pattern, n=3):
        out = []
        for p in sorted((RAW / folder).rglob(pattern)):
            if "private" in p.parts:
                continue
            out.append(p)
            if len(out) >= n:
                break
        return out

    def run_files(self, files, expect):
        counts, issues, rows, warnings = Counter(), 0, 0, 0
        for f in files:
            self.assertTrue(x12.detect(f), f)
            for table, row in x12.parse(f, {}):
                counts[table] += 1
                if table == "x12_parse_issue":
                    if row["severity"] == "error":
                        issues += 1
                    else:
                        warnings += 1
                else:
                    rows += 1
        for tbl in expect:
            self.assertGreater(counts[tbl], 0, (tbl, dict(counts)))
        self.assertLessEqual(issues, 0.01 * max(rows, 1), (issues, rows))
        print(f"\n[smoke] {len(files)} files: {dict(counts)} error_issues={issues} warnings={warnings}")

    def test_provider_837p(self):
        self.run_files(self.files("x12/837p", "*.x12"),
                       ["x12_interchange", "x12_837_claim", "x12_837_dx", "x12_837_line"])

    def test_provider_835(self):
        self.run_files(self.files("x12/835", "*.x12"),
                       ["x12_interchange", "x12_835_payment", "x12_835_claim", "x12_835_adjustment"])

    def test_payer_837i_837p_835(self):
        files = (self.files("x12_payer", "*837I*.x12", 2) + self.files("x12_payer", "*837P*.x12", 2)
                 + self.files("x12_payer", "*835*.x12", 2))
        self.run_files(files, ["x12_837_claim", "x12_837_dx", "x12_837_line", "x12_835_claim", "x12_835_payment"])

    def test_834(self):
        self.run_files(self.files("x12_834", "*FULL*.x12", 2) + self.files("x12_834", "*CHG*.x12", 2),
                       ["x12_interchange", "x12_834_member"])


if __name__ == "__main__":
    unittest.main()
