#!/usr/bin/env python3
"""Reconcile the Tuva warehouse against independent SQL on the raw warehouse.

Every "raw" figure below is computed directly from raw_warehouse.records with
its own SQL; none of it reads the connector's staging or intermediate models.
Differences are reported with the reason, not forced to zero.

Run after `dbt build` (no dbt process may hold the DuckDB file):
    /Users/mikel/clinical-insights-local/.venv-tuva/bin/python scripts/reconcile.py [--markdown out.md]
"""
import argparse
import pathlib
import sys

import decimal

import duckdb

DUCKDB = "/Users/mikel/clinical-insights-local/artifacts/test-warehouse/tuva.duckdb"
RAW = "/Users/mikel/clinical-insights-local/artifacts/bench-db/v28-sample-indexed.sqlite3"

CHECKS = []


def check(title, note):
    def deco(fn):
        CHECKS.append((title, note, fn))
        return fn
    return deco


def rows(con, sql):
    rel = con.sql(sql)
    return rel.columns, rel.fetchall()


# --------------------------------------------------------------------------
# 1. Medical claims: final paid by service year
# --------------------------------------------------------------------------
@check("1a. Payer medical claims: final net paid by service year (all payer claims, before payer-copy removal)",
       "raw = latest-version method: per claim family (frequency-7 replacements fold into the claim they "
       "replace) the version with the latest arrival is current; current status voided / reversed / denied "
       "nets to zero, recouped nets to the parent paid plus the negative recoupment. Tuva side = "
       "connector_intermediate.int_payer_medical_claims (ledger method). Differences come from versions "
       "that arrive out of sequence.")
def payer_medical_by_year(con):
    return rows(con, """
    with r as (
        select id, arrival, payload::json j from raw_warehouse.records where stream = 'payer_medical_claims'
    ), f as (
        select id, arrival,
               j->>'payer_claim_id' pcid, j->>'original_payer_claim_id' orig, j->>'claim_frequency_code' freq,
               j->>'claim_status' st, try_cast(j->>'paid_amount' as double) paid,
               try_cast(j->>'adjudication_sequence' as int) seq, try_cast(j->>'service_from' as date) sf
        from r
    ), repl as (
        select distinct pcid, orig || case when pcid like '%-X' then '-X' else '' end as target
        from f where freq = '7'
    ), fam as (
        select f.*, coalesce(repl.target, f.pcid) fam, (repl.pcid is not null)::int is_r
        from f left join repl on repl.pcid = f.pcid
    ), cur as (
        select * from fam
        qualify row_number() over (partition by fam order by arrival desc, is_r desc, seq desc, id desc) = 1
    ), prev_paid as (
        select fam, max(paid) filter (where st = 'paid') last_paid from fam group by 1
    ), raw as (
        select c.fam,
               min(f2.sf) as sf,
               any_value(case when c.st in ('voided', 'denied', 'denied_upheld') then 0
                         when c.st = 'reversed' and c.paid < 0 then 0
                         when c.st = 'recouped' then coalesce(p.last_paid, 0) + c.paid
                         else c.paid end) as net
        from cur c join prev_paid p using (fam) join fam f2 on f2.fam = c.fam
        group by c.fam
    ), raw_y as (select year(sf) yr, count(*) n, round(sum(net), 2) paid from raw group by 1),
    tuva_y as (
        select year(service_from) yr, count(*) n, round(sum(net_paid_amount), 2) paid
        from connector_intermediate.int_payer_medical_claims group by 1
    )
    select coalesce(r.yr, t.yr) service_year, r.n raw_claims, t.n tuva_claims,
           r.paid raw_paid, t.paid tuva_paid, round(t.paid - r.paid, 2) diff
    from raw_y r full join tuva_y t on r.yr = t.yr order by 1
    """)


@check("1b. Medical claims in Tuva core: paid by service year and data_source",
       "core.medical_claim. payer_feed excludes the payer copies of provider claims "
       "(int_claim_crosswalk); provider_billing is the owned providers' adjudicated claims. "
       "raw_payer_copy_paid = the excluded payer copies' net paid, shown so the two views reconcile.")
def core_medical_by_year(con):
    return rows(con, """
    with t as (
        select year(claim_start_date) yr, data_source, sum(paid_amount) paid
        from core.medical_claim group by 1, 2
    ), copies as (
        select year(service_from) yr, sum(net_paid_amount) paid
        from connector_intermediate.int_payer_medical_claims
        where claim_family_id in (select claim_family_id from connector_intermediate.int_claim_crosswalk)
        group by 1
    )
    select t.yr service_year,
           round(sum(t.paid) filter (where data_source = 'payer_feed'), 2) payer_feed_paid,
           round(sum(t.paid) filter (where data_source = 'provider_billing'), 2) provider_billing_paid,
           round(any_value(c.paid), 2) payer_copy_paid_excluded
    from t left join copies c on c.yr = t.yr group by 1 order by 1
    """)


@check("1c. Provider billing: net paid by service year, raw sum of all adjudication deltas vs Tuva",
       "raw = sum over every medical_claims adjudication row of financial_deltas_cents.payer "
       "(payer_paid_cents when no deltas), grouped by the earliest submitted service date (adjudication "
       "service date, else the remittance event date, when no submission exists); Tuva = core.medical_claim "
       "data_source provider_billing.")
def provider_by_year(con):
    return rows(con, """
    with a as (
        select payload::json j, try_cast(event as date) ev from raw_warehouse.records where stream = 'medical_claims'
    ), s as (
        select payload::json ->> 'claim_id' cid, min(try_cast(payload::json ->> 'service_date' as date)) sd
        from raw_warehouse.records where stream = 'medical_claim_submissions' group by 1
    ), raw as (
        select year(coalesce(s.sd, try_cast(a.j->>'service_date' as date), a.ev)) yr,
               sum(coalesce(try_cast(a.j->'financial_deltas_cents'->>'payer' as bigint),
                            try_cast(a.j->>'payer_paid_cents' as bigint))) / 100.0 paid
        from a left join s on s.cid = (a.j->>'claim_id') group by 1
    ), t as (
        select year(claim_start_date) yr, sum(paid_amount) paid from core.medical_claim
        where data_source = 'provider_billing' group by 1
    )
    select coalesce(raw.yr, t.yr) service_year, round(raw.paid, 2) raw_paid, round(t.paid, 2) tuva_paid,
           round(t.paid - raw.paid, 2) diff
    from raw full join t on raw.yr = t.yr order by 1
    """)


# --------------------------------------------------------------------------
# 2. Pharmacy paid total
# --------------------------------------------------------------------------
@check("2. Pharmacy paid total by source",
       "raw payer = sum of plan_paid on paid and recouped rows, minus the paid amount of every "
       "rx_claim_id that has a reversal; raw owned-pharmacy billing = sum of all "
       "pharmacy_claim_transactions payer_paid_delta_cents + pharmacy_claims payer_paid_cents. "
       "Tuva = core.pharmacy_claim. Owned-pharmacy fills whose last transaction is a reversal or "
       "rejection are not loaded to Tuva, so their (net zero) deltas are absent there.")
def pharmacy(con):
    return rows(con, """
    with p as (
        select payload::json j from raw_warehouse.records where stream = 'payer_pharmacy_claims'
    ), rev as (select distinct j->>'rx_claim_id' rx from p where j->>'claim_status' = 'reversed'),
    payer as (
        select sum(try_cast(j->>'plan_paid' as double)) filter (where j->>'claim_status' in ('paid', 'recouped'))
             - coalesce(sum(try_cast(j->>'plan_paid' as double)) filter (where j->>'claim_status' = 'paid'
                                                                     and (j->>'rx_claim_id') in (select rx from rev)), 0) as paid
        from p
    ), prov as (
        select (select sum(try_cast(payload::json->>'payer_paid_delta_cents' as bigint)) from raw_warehouse.records where stream = 'pharmacy_claim_transactions')/100.0
             + (select sum(try_cast(payload::json->>'payer_paid_cents' as bigint)) from raw_warehouse.records where stream = 'pharmacy_claims')/100.0 as paid
    ), t as (select data_source, sum(paid_amount) paid, count(*) n from core.pharmacy_claim group by 1)
    select 'payer_feed' as feed, round((select paid from payer), 2) raw_paid,
           round((select paid from t where data_source = 'payer_feed'), 2) tuva_paid,
           (select n from t where data_source = 'payer_feed') tuva_claims
    union all
    select 'pharmacy_billing', round((select paid from prov), 2),
           round((select paid from t where data_source = 'pharmacy_billing'), 2),
           (select n from t where data_source = 'pharmacy_billing')
    """)


# --------------------------------------------------------------------------
# 3. Distinct members with enrollment
# --------------------------------------------------------------------------
@check("3. Distinct members with enrollment",
       "raw = distinct patient ids on enrollment_spans rows that no other row supersedes; "
       "Tuva = distinct person_id in core.eligibility and core.member_month. person_id is "
       "MPI-resolved, so merged duplicate charts can only lower the Tuva count.")
def members(con):
    return rows(con, """
    with e as (select id, patient, supersedes from raw_warehouse.records where stream = 'enrollment_spans')
    select (select count(distinct patient) from e where id not in (select supersedes from e where supersedes is not null)) raw_members,
           (select count(distinct person_id) from core.eligibility) tuva_eligibility_persons,
           (select count(distinct person_id) from core.member_month) tuva_member_month_persons,
           (select count(*) from core.member_month) tuva_member_months
    """)


# --------------------------------------------------------------------------
# 4. ADT visit numbers -> exactly one clinical encounter
# --------------------------------------------------------------------------
@check("4. Every ADT visit lands in exactly one clinical encounter",
       "adt_messages visit_number (HL7 PV1-19) against core.encounter data_source hl7_adt; owned-hospital "
       "adt encounter_id against core.encounter data_source ehr.")
def adt(con):
    return rows(con, """
    with v as (
        select distinct payload::json->>'visit_number' vn from raw_warehouse.records where stream = 'adt_messages'
    ), o as (
        select distinct payload::json->>'encounter_id' eid from raw_warehouse.records where stream = 'adt'
    ), cv as (
        select v.vn, count(e.encounter_id) n from v left join core.encounter e
          on e.encounter_id = v.vn and e.data_source = 'hl7_adt' group by 1
    ), co as (
        select o.eid, count(e.encounter_id) n from o left join core.encounter e
          on e.encounter_id = o.eid and e.data_source = 'ehr' group by 1
    )
    select 'adt_messages visit_number' as feed, count(*) visits,
           count(*) filter (where n = 1) exactly_one, count(*) filter (where n = 0) as no_encounter, count(*) filter (where n > 1) as multiple
    from cv
    union all
    select 'adt (owned hospital) encounter_id', count(*), count(*) filter (where n = 1),
           count(*) filter (where n = 0), count(*) filter (where n > 1)
    from co
    """)


# --------------------------------------------------------------------------
# 5. Acute inpatient and ED encounters by year
# --------------------------------------------------------------------------
@check("5a. Acute inpatient and ED encounters by year: Tuva core.encounter",
       "Claims-derived encounters come from Tuva's encounter grouper over core.medical_claim; clinical "
       "encounters from the input-layer encounter table. Claims and clinical encounters of the same "
       "visit are both present (Tuva does not link them; see connector_gap_fill.claims_clinical_encounter_link).")
def tuva_enc(con):
    return rows(con, """
    select year(encounter_start_date) yr, encounter_source_type src, data_source,
           count(*) filter (where encounter_type = 'acute inpatient') acute_inpatient,
           count(*) filter (where encounter_type = 'emergency department') emergency_department
    from core.encounter
    where encounter_type in ('acute inpatient', 'emergency department')
    group by all order by 1, 2, 3
    """)


@check("5b. Acute inpatient and ED by year: independent raw per-source tally",
       "hl7_adt = distinct visit_number by the patient class on its latest message (I / E); ehr = distinct "
       "encounter_id whose latest version is type inpatient / emergency (or POS 21 / 23); payer = distinct "
       "patient + admission date on non-voided institutional TOB 011x claims (inpatient) and distinct "
       "patient + service date on claims with an ED revenue line 045x or POS 23 (ED); provider = same "
       "rules on medical_claim_submissions / lines. ED visits that become admissions are inpatient in "
       "Tuva (one encounter), but count in both raw columns, so raw ED >= Tuva ED.")
def raw_enc(con):
    return rows(con, """
    with m as (
        select payload::json j, try_strptime(left(payload::json->>'message_datetime', 14), '%Y%m%d%H%M%S') ts
        from raw_warehouse.records where stream = 'adt_messages'
    ), hv as (
        select j->>'visit_number' vn, arg_max(j->>'patient_class', ts) cls, max((j->>'patient_class') = 'E') had_e, min(ts) t0
        from m group by 1
    ), hl7 as (
        select year(t0) yr, 'hl7_adt' src, count(*) filter (where cls = 'I') ip, count(*) filter (where had_e) ed from hv group by 1
    ), e as (
        select payload::json j, arrival from raw_warehouse.records where stream = 'encounters'
    ), ev as (
        select j->>'encounter_id' eid, arg_max(j->>'type', arrival) typ, arg_max(j->>'place_of_service', arrival) pos,
               min(try_cast(left(coalesce(j->>'start_time', j->>'event_date'), 10) as date)) d
        from e group by 1
    ), ehr as (
        select year(d) yr, 'ehr' src,
               count(*) filter (where typ ilike '%inpatient%' or pos = '21' or typ = 'L&D admission'),
               count(*) filter (where typ in ('emergency', 'ED', 'ED stroke alert') or pos = '23')
        from ev group by 1
    ), pc as (
        select payload::json j from raw_warehouse.records where stream = 'payer_medical_claims'
    ), pl as (
        select distinct payload::json->>'payer_claim_id' pcid from raw_warehouse.records
        where stream = 'payer_medical_claim_lines' and (payload::json->>'revenue_code') like '045%'
    ), payer as (
        select year(try_cast(coalesce(j->>'admission_date', j->>'service_from') as date)) yr, 'payer_claims' src,
               count(distinct case when (j->>'type_of_bill') like '011%' then (j->>'patient_id') || (j->>'admission_date') end),
               count(distinct case when (j->>'place_of_service') = '23' or (j->>'payer_claim_id') in (select pcid from pl)
                                   then (j->>'patient_id') || (j->>'service_from') end)
        from pc where (j->>'claim_status') not in ('voided', 'reversed', 'recouped') group by 1
    ), ps as (
        select payload::json j from raw_warehouse.records where stream = 'medical_claim_submissions'
    ), psl as (
        select distinct payload::json->>'claim_id' cid from raw_warehouse.records
        where stream = 'medical_claim_lines' and (payload::json->>'revenue_code') like '045%'
    ), prov as (
        select year(try_cast(coalesce(j->>'admission_date', j->>'service_date') as date)) yr, 'provider_claims' src,
               count(distinct case when (j->>'type_of_bill') like '011%' then (j->>'patient_id') || (j->>'admission_date') end),
               count(distinct case when (j->>'place_of_service') = '23' or (j->>'claim_id') in (select cid from psl)
                                   then (j->>'patient_id') || (j->>'service_date') end)
        from ps group by 1
    )
    select * from hl7 union all select * from ehr union all select * from payer union all select * from prov
    order by 1, 2
    """)


def fmt(col, v):
    if v is None:
        return ""
    if isinstance(v, (float, decimal.Decimal)):
        return f"{v:,.2f}"
    if isinstance(v, int) and not isinstance(v, bool) and not col.endswith(("year", "yr")):
        return f"{v:,}"
    return str(v)


def to_markdown(cols, data):
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in data:
        out.append("| " + " | ".join(fmt(c, v) for c, v in zip(cols, r)) + " |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown", help="write the report as Markdown to this path")
    args = ap.parse_args()
    con = duckdb.connect(DUCKDB, read_only=True)
    con.execute(f"install sqlite; load sqlite; attach if not exists '{RAW}' as raw_warehouse (type sqlite, read_only)")
    md = ["# Reconciliation: Tuva warehouse vs independent raw SQL", ""]
    for title, note, fn in CHECKS:
        cols, data = fn(con)
        print("\n== " + title)
        print(note)
        print(to_markdown(cols, data))
        md += [f"## {title}", "", note, "", to_markdown(cols, data), ""]
    if args.markdown:
        pathlib.Path(args.markdown).write_text("\n".join(md) + "\n")
        print("\nwrote", args.markdown, file=sys.stderr)


if __name__ == "__main__":
    main()
