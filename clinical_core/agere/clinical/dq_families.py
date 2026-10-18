"""Deterministic data-quality and feed families: questions about the deliveries, identities and record versions answered
by code from the delivery and identity tables (the cohort_families.py pattern: question + definition -> typed family and
parameters -> code-rendered SQL with a '-- DEFINITION:' first line -> validate).

Families (each renders one read-only SELECT over the semantic layer):

  file_count             delivered files (delivery_file, one row per file in raw/) with one condition: late (an original
                         file delivered after its normal cycle: after the date in its name, or, for files without a name
                         date, more than 7 days past its series' usual lag after the period end - the next-cycle catch-up
                         shows as ~30 days), resend_identical (a re-send marked in the name whose bytes equal an earlier
                         file; a whole-file resend, so every file except HL7 batches and X12 interchanges counts:
                         delimited, fixed-width, NCPDP and FHIR ndjson), truncated (delivery_file.truncated with its
                         evidence, every format incl. FHIR, NCPDP and CCLF fixed-width; a definition listing several
                         formats names the kinds of evidence, not a format filter), empty_expected (0 bytes or a single
                         complete header line while the manifest claims rows), empty_any, zero_byte, header_only,
                         v2_replacement; optional formats (flat = every parser except hl7/x12/ncpdp/fhir, incl. CCLF
                         fixed-width), a delivered year, a feed, or grouping by feed
  missing_periods        expected periodic files that never arrived (delivery_gap rows; expected cadence = the series'
                         usual step, scheduled series only, see delivery.DOC); optional missing-period year, feed, by feed
  duplicate_charts       EHR charts (epic / athena / ecw patient ids, FHIR Patient ids) of one person within one system:
                         'charts' = the extra charts (a person with 2 charts in one system has 1 duplicate), 'people' =
                         persons with at least one; a practice delivering both a vendor extract and its FHIR copy shows
                         the same duplicate twice, so a person counts max(vendor extras, FHIR extras)
  chart_renumbering      persons whose MRN changed at an EHR go-live: two MRN keys within a migrated practice (a practice
                         whose records come from one EHR vendor and then from a later one; go-live = first day of the
                         month of the new vendor's first delivery) and records from both vendors there
  corrected_results      lab results with result_status corrected (measurement, source labs); optional event year
  restated_records       received records that a later record supersedes (record_correction: supersedes_record_id or a lab
                         result's replaces link); optional streams
  roster_after_departure provider-practice affiliations still listed as active (no term date) on a monthly roster file
                         for a month the provider had already left: the departure (its term date on any roster row) is
                         on or before the first day of the roster month, so the provider is gone for all of that month
  conversion_duplicates  records an EHR data conversion created at a migrated practice that copy data already delivered
                         from the old EHR: new-vendor problem / medication-list / allergy / immunization entries stamped
                         with the go-live date, and new-vendor lab results and notes dated before go-live, for patients
                         whose old-EHR chart at that practice was delivered

Only questions a family fully represents are claimed; anything else (malformed rows, HL7/X12 message duplicates,
consent, unknown qualifiers) declines. AGERE_DQ_FAMILIES=off disables the rung. Synthetic data only; no model calls.
"""
from __future__ import annotations

import copy, os, re

from . import family_common as fc
from .family_common import q

VERSION = 'agere-dq-families/1'
ROUTE = 'dq_family'
FAMILIES = ('file_count', 'missing_periods', 'duplicate_charts', 'chart_renumbering', 'corrected_results',
            'restated_records', 'roster_after_departure', 'conversion_duplicates')
CONDITIONS = ('late', 'resend_identical', 'truncated', 'empty_expected', 'empty_any', 'zero_byte', 'header_only', 'v2_replacement')
FORMATS = ('flat', 'hl7', 'x12', 'ncpdp', 'fhir')
FEEDS = ('cms_alr', 'cms_cclf', 'cms_cclf_csv', 'ehr', 'fhir_bulk', 'hl7', 'ncpdp', 'payers', 'programs', 'rosters',
         'sdoh_vendor', 'x12', 'x12_834', 'x12_payer')
STREAMS = ('adt', 'allergies', 'appointments', 'conditions', 'encounters', 'enrollment_spans', 'immunizations', 'labs',
           'medical_claims', 'medication_lists', 'medication_orders', 'notes', 'payer_medical_claims', 'payer_pharmacy_claims', 'vitals')
CONVERSION_STREAMS = {'problems': 'conditions', 'meds': 'medication_lists', 'allergies': 'allergies',
                      'immunizations': 'immunizations', 'labs': 'labs', 'notes': 'notes'}
LIST_STREAMS = ('conditions', 'medication_lists', 'allergies', 'immunizations')   # converted entries carry the go-live date
DATED_STREAMS = ('labs', 'notes')                                                # converted entries keep their clinical date
LATE_TOLERANCE_DAYS = 7
EHR_VENDORS = ('epic', 'athena', 'ecw')


def enabled():
    return os.environ.get('AGERE_DQ_FAMILIES', 'on').strip().lower() != 'off'


# ============================================================================================ SQL rendering
FORMAT_SQL = {'flat': "coalesce(parser, 'flat') NOT IN ('hl7', 'x12', 'ncpdp', 'fhir')", 'hl7': "parser = 'hl7'",
              'x12': "parser = 'x12'", 'ncpdp': "parser = 'ncpdp'", 'fhir': "parser = 'fhir'"}
# A resent file is a whole file sent again: every format except HL7 batches and X12 interchanges, whose re-sends are
# message/interchange duplicates (another question). 'Flat files re-sent' therefore covers NCPDP and FHIR ndjson too.
RESEND_FILE_SQL = "coalesce(parser, 'flat') NOT IN ('hl7', 'x12')"
CONDITION_SQL = {
    'late': (f"is_original = 1 AND ((name_date IS NOT NULL AND days_after_name_date >= 1) OR "
             f"(name_date IS NULL AND days_after_period_end - series_usual_lag_days > {LATE_TOLERANCE_DAYS}))"),
    'resend_identical': 'resend_by_name = 1 AND content_identical_to IS NOT NULL',
    'truncated': 'truncated = 1',
    'empty_expected': 'empty_where_expected = 1',
    'empty_any': 'empty_or_header_only = 1',
    'zero_byte': 'empty_file = 1',
    'header_only': 'header_only = 1',
    'v2_replacement': 'is_v2_replacement = 1',
}


def _grouped(table_sql, where, group, unit):
    w = ' AND '.join(where) if where else '1'
    if group:
        return f"SELECT {group} AS key, count(*) AS value FROM {table_sql} WHERE {w} GROUP BY {group} ORDER BY {group}"
    return f"SELECT count(*) AS {unit} FROM {table_sql} WHERE {w}"


def render_file_count(p, as_of):
    where = [CONDITION_SQL[p['condition']]]
    if p['condition'] == 'resend_identical' and set(p.get('formats') or ()) <= {'flat'}:
        where.append(RESEND_FILE_SQL)
    elif p.get('formats'): where.append('(' + ' OR '.join(FORMAT_SQL[f] for f in p['formats']) + ')')
    if p.get('year'): where.append(f"substr(delivered_date, 1, 4) = {q(p['year'])}")
    if p.get('feeds'): where.append(f"feed IN ({', '.join(q(f) for f in p['feeds'])})")
    where.append(f"(delivered_date IS NULL OR delivered_date <= {q(as_of)})")
    return _grouped('delivery_file', where, 'feed' if p.get('group_by') == 'feed' else None, 'files')


def render_missing_periods(p, as_of):
    where = []
    if p.get('year'): where.append(f"substr(missing_period, 1, 4) = {q(p['year'])}")
    if p.get('feeds'): where.append(f"feed IN ({', '.join(q(f) for f in p['feeds'])})")
    return _grouped('delivery_gap', where, 'feed' if p.get('group_by') == 'feed' else None, 'missing_files')


_KIND = "substr(source_key, 1, instr(source_key, ':') - 1)"


def render_duplicate_charts(p, as_of):
    kinds = ', '.join(q(k) for k in (*EHR_VENDORS, 'fhir'))
    body = (f"WITH k AS (SELECT person_id, {_KIND} AS kind, rtrim(source_key, replace(source_key, ':', '')) AS ns "
            f"FROM raw_stg_person_map WHERE person_id IS NOT NULL AND {_KIND} IN ({kinds})), "
            f"g AS (SELECT person_id, kind, ns, count(*) - 1 AS extra FROM k GROUP BY person_id, kind, ns), "
            f"pp AS (SELECT person_id, sum(CASE WHEN kind = 'fhir' THEN 0 ELSE extra END) AS vendor_extra, "
            f"sum(CASE WHEN kind = 'fhir' THEN extra ELSE 0 END) AS fhir_extra FROM g GROUP BY person_id) ")
    if p['unit'] == 'people':
        return body + "SELECT count(*) AS people FROM pp WHERE max(vendor_extra, fhir_extra) > 0"
    return body + "SELECT coalesce(sum(max(vendor_extra, fhir_extra)), 0) AS duplicate_charts FROM pp WHERE max(vendor_extra, fhir_extra) > 0"


_SS = "json_extract(payload, '$.source_system')"
_VENDOR = f"substr({_SS}, 1, instr({_SS}, ':') - 1)"
_ORG = "json_extract(payload, '$.organization_id')"


def _migration_ctes(streams=None):
    """v: EHR vendor-extract records (patient, practice, vendor, stream, dates); pov: one row per (patient, practice,
    vendor) with its first delivery; mig: migrated practices (an old vendor first delivered before a new vendor at the
    same practice; go-live = first day of the new vendor's first delivery month)."""
    st = f" AND stream IN ({', '.join(q(s) for s in streams)})" if streams else ''
    vend = ' OR '.join(f"{_SS} LIKE {q(v + ':%')}" for v in EHR_VENDORS)
    return (f"v AS MATERIALIZED (SELECT patient_id, stream, event_date, available_date, {_ORG} AS org, {_VENDOR} AS vendor "
            f"FROM record WHERE ({vend}){st}), "
            f"pov AS MATERIALIZED (SELECT patient_id, org, vendor, min(available_date) AS first_av FROM v "
            f"WHERE org IS NOT NULL AND patient_id IS NOT NULL GROUP BY patient_id, org, vendor), "
            f"pv AS (SELECT org, vendor, min(first_av) AS first_av FROM pov GROUP BY org, vendor), "
            f"mig AS MATERIALIZED (SELECT n.org, n.vendor AS new_vendor, o.vendor AS old_vendor, date(n.first_av, 'start of month') AS go_live "
            f"FROM pv n JOIN pv o ON o.org = n.org AND o.vendor <> n.vendor AND o.first_av < n.first_av)")


def render_chart_renumbering(p, as_of):
    return (f"WITH {_migration_ctes()}, "
            f"both_charts AS MATERIALIZED (SELECT DISTINCT a.patient_id, m.org FROM mig m JOIN pov a ON a.org = m.org AND a.vendor = m.new_vendor "
            f"JOIN pov b ON b.org = m.org AND b.vendor = m.old_vendor AND b.patient_id = a.patient_id), "
            f"mrns AS (SELECT s.person_id, c.org, count(DISTINCT s.source_key) AS n FROM both_charts c "
            f"JOIN raw_stg_person_map s ON s.person_id = c.patient_id AND substr(s.source_key, 1, length(c.org) + 5) = 'mrn:' || c.org || ':' "
            f"GROUP BY s.person_id, c.org) "
            f"SELECT count(DISTINCT person_id) AS patients FROM mrns WHERE n > 1")


def render_corrected_results(p, as_of):
    where = ["source_table = 'labs'", "result_status = 'corrected'"]
    if p.get('year'): where.append(f"substr(event_date, 1, 4) = {q(p['year'])}")
    return f"SELECT count(DISTINCT record_id) AS lab_results FROM measurement WHERE {' AND '.join(where)}"


def render_restated_records(p, as_of):
    where = f" WHERE stream IN ({', '.join(q(s) for s in p['streams'])})" if p.get('streams') else ''
    return f"SELECT count(*) AS records FROM record_correction{where}"


def render_roster_after_departure(p, as_of):
    body = ("WITH r AS MATERIALIZED (SELECT x.provider_npi AS npi, x.practice_id AS practice, nullif(trim(x.term_date), '') AS term_date, "
            "coalesce(substr(d.period, 1, 7), substr(coalesce(d.name_date, d.delivered_date), 1, 7)) || '-01' AS roster_month "
            "FROM raw_rosters_aco_provider_roster x LEFT JOIN delivery_file d ON d.relpath = x._file), "
            "t AS (SELECT npi, practice, min(term_date) AS departed FROM r WHERE term_date IS NOT NULL GROUP BY npi, practice), "
            "stale AS (SELECT r.* FROM r JOIN t USING (npi, practice) WHERE r.term_date IS NULL AND t.departed <= r.roster_month) ")
    if p['unit'] == 'rows':
        return body + "SELECT count(*) AS roster_rows FROM stale"
    if p['unit'] == 'providers':
        return body + "SELECT count(DISTINCT npi) AS providers FROM stale"
    return body + "SELECT count(*) AS affiliations FROM (SELECT DISTINCT npi, practice FROM stale)"


def render_conversion_duplicates(p, as_of):
    streams = p['streams']
    lists = [s for s in streams if s in LIST_STREAMS]
    dated = [s for s in streams if s in DATED_STREAMS]
    conds = []
    if lists: conds.append(f"(c.stream IN ({', '.join(q(s) for s in lists)}) AND c.event_date = m.go_live)")
    if dated: conds.append(f"(c.stream IN ({', '.join(q(s) for s in dated)}) AND c.event_date < m.go_live)")
    return (f"WITH {_migration_ctes()}, "
            f"old_chart AS MATERIALIZED (SELECT DISTINCT b.patient_id, m.org FROM mig m JOIN pov b ON b.org = m.org "
            f"AND b.vendor = m.old_vendor AND b.first_av < date(m.go_live, '+1 month')) "
            f"SELECT count(*) AS converted_records FROM mig m JOIN v c ON c.org = m.org AND c.vendor = m.new_vendor "
            f"JOIN old_chart o ON o.patient_id = c.patient_id AND o.org = m.org WHERE {' OR '.join(conds)}")


RENDER = {'file_count': render_file_count, 'missing_periods': render_missing_periods,
          'duplicate_charts': render_duplicate_charts, 'chart_renumbering': render_chart_renumbering,
          'corrected_results': render_corrected_results, 'restated_records': render_restated_records,
          'roster_after_departure': render_roster_after_departure, 'conversion_duplicates': render_conversion_duplicates}


def validate(spec):
    if not isinstance(spec, dict) or spec.get('family') not in FAMILIES: raise ValueError('unknown dq family')
    s = copy.deepcopy(spec)
    p, f = s['parameters'], s['family']
    if p.get('year') is not None and not (isinstance(p['year'], int) and 2000 <= p['year'] <= 2100): raise ValueError('bad year')
    for x in p.get('feeds') or []:
        if x not in FEEDS: raise ValueError(f'unknown feed {x!r}')
    if p.get('group_by') not in (None, 'feed'): raise ValueError('group_by must be feed')
    if f == 'file_count':
        if p.get('condition') not in CONDITIONS: raise ValueError('bad file condition')
        for x in p.get('formats') or []:
            if x not in FORMATS: raise ValueError(f'unknown format {x!r}')
    elif f == 'duplicate_charts':
        if p.get('unit') not in ('charts', 'people'): raise ValueError('unit must be charts or people')
    elif f == 'restated_records':
        for x in p.get('streams') or []:
            if x not in STREAMS: raise ValueError(f'unknown stream {x!r}')
    elif f == 'roster_after_departure':
        if p.get('unit') not in ('affiliations', 'rows', 'providers'): raise ValueError('bad roster unit')
    elif f == 'conversion_duplicates':
        if not p.get('streams') or set(p['streams']) - set(CONVERSION_STREAMS.values()): raise ValueError('bad conversion streams')
    want = 'table' if p.get('group_by') else 'count'
    if s.get('returns') != want: raise ValueError(f'returns must be {want}')
    return s


def render(spec, as_of):
    s = validate(spec)
    body = RENDER[s['family']](s['parameters'], as_of)
    return f"-- DEFINITION: {fc.statement(_module(), s, as_of)}\n{body}"


# ============================================================================================ deterministic parser
_FILES = re.compile(r'\bfiles?\b|\bdeliver(?:y|ies|ed)\b|\btransmissions?\b|\bbatch(?:es)?\b|\binterchanges?\b')
_MALFORMED = re.compile(r'malformed|broken (?:or )?|bad delimiter|short rows|swapped columns|bad encoding|embedded line')
_MISSING = re.compile(r"never arrived|did not arrive|didn't arrive|not (?:yet )?(?:been )?received|missing (?:any )?expected|"
                      r"expected (?:periodic )?(?:files?|deliveries) (?:that|which)? ?(?:never|did not|didn't|are missing)|"
                      r"missing (?:files|periods|deliveries)|\bgaps? in (?:the )?deliver")
_COND = [
    ('late', re.compile(r"\blate\b|after (?:their|its|the) (?:normal|usual|expected|regular) (?:cycle|schedule|date)|caught up in the next cycle|behind schedule")),
    ('resend', re.compile(r"\bre-?sent\b|sent (?:to us )?twice|\bresends?\b|\bre-?sending\b|delivered twice|duplicate (?:files?|deliver)")),
    ('truncated', re.compile(r"truncat|\bcut off\b|incomplete|missing (?:its |their )?trailers?|trailer count")),
    ('empty', re.compile(r"header[- ]only|header only|\b0 bytes\b|zero[- ]bytes?|\bempty\b|came through empty|no (?:data )?rows")),
    ('v2', re.compile(r"\bv2\b|_vn\b|corrected (?:copy|copies|version)s?|replacement files?")),
]
_FORMAT_WORDS = [('flat', r'\bflat\b|\bcsv\b|fixed[- ]width'), ('hl7', r'\bhl7\b'), ('x12', r'\bx12\b|\b83[457][ip]?\b'),
                 ('ncpdp', r'\bncpdp\b'), ('fhir', r'\bfhir\b|ndjson')]
_GROUP_UNSUPPORTED = re.compile(r'\b(?:by|per|for each|broken down by|breakdown by) (?:sender|practice|payer|month|quarter|week|series|'
                                r'format|instance|vendor|provider|file type|kind|day)\b')
_DELIVERED_YEAR = re.compile(r'\b(?:delivered|received|arrived|sent) (?:in|during) (20\d\d)\b|\bin (20\d\d)\b')


# scope sentences ("duplicate charts counted as their person") describe counting units, not a duplicate-chart question
_SCOPE = re.compile(r"scope: [^.]*?duplicate charts? (?:are )?count(?:ed|s)? as their person\.|;? ?duplicate charts? (?:are )?count(?:ed|s)? as "
                    r"their person\.?|\(?duplicate charts? (?:are )?(?:not )?merged\)?")


def _formats(text):
    return [f for f, pat in _FORMAT_WORDS if re.search(pat, text)]


def _feeds(text):
    out = []
    for m in re.finditer(r'\b(?:from|in) the ([a-z0-9_]+) feed\b|\bfeed ([a-z0-9_]+)\b', text):
        name = m.group(1) or m.group(2)
        if name not in FEEDS: return None
        out.append(name)
    return out


def _year_filter(text, pattern=_DELIVERED_YEAR):
    """A single stated year filter, else None; more than one distinct year (or a year in another role) -> 'ambiguous'."""
    ys = fc.years_in(text)
    if not ys: return None
    found = {int(g) for m in pattern.finditer(text) for g in m.groups() if g}
    if len(set(ys)) == 1 and found == set(ys): return ys[0]
    return 'ambiguous'


def _detect(text):
    if re.search(r'\bopt(?:ed)?[- ]out|consent|data[- ]sharing', text): return None
    if re.search(r'\broster\b', text) and re.search(r"after (?:the |their |its )?(?:provider'?s? )?(?:departure|depart|left|leaving|term)|"
                                                    r"after they left|after (?:they|he|she) (?:left|departed)", text):
        return 'roster_after_departure'
    if re.search(r'\bdata conversion|\bconverted records?|\bconversion\b', text) and re.search(r'\bcop(?:y|ies)\b|duplicat', text):
        return 'conversion_duplicates'
    if re.search(r"\b(?:mrns?|chart numbers?|medical record numbers?)\b.{0,30}\bchang|new (?:chart number|mrn|medical record number)", text) \
            and re.search(r'go-?live|ehr (?:migration|change|switch|conversion)|changed ehrs?|switched ehrs?|migrat', text):
        return 'chart_renumbering'
    if re.search(r'duplicate (?:patient )?charts?|more than one chart|second chart|duplicate (?:patient )?(?:records|ids) for the same', text):
        return 'duplicate_charts'
    if re.search(r'\blab', text) and re.search(r'result_status corrected|corrected after|\bcorrected\b', text) and not re.search(r'supersedes_record_id', text):
        return 'corrected_results'
    if re.search(r'records? (?:that )?a (?:later|newer) record supersedes|supersedes_record_id points|\brestated\b|'
                 r'superseded by a (?:later|newer) (?:received )?(?:record|version)', text) and \
            not re.search(r'a span (?:version )?superseded', text):
        # Native feeds do not deliver version links (supersedes_record_id); Agere's rebuilt correction lineage is an
        # inference, so the question is left to the paths that can say it is not supported by the delivered data.
        return 'restated_records' if os.environ.get('AGERE_DQ_RESTATED_FROM_REBUILT_LINEAGE', 'off') == 'on' else None
    if _FILES.search(text):
        if _MISSING.search(text): return 'missing_periods'
        return 'file_count'
    return None


def parse(question, as_of):
    """question (optionally "... Definition: ...") -> {'family', 'parameters', 'returns', 'defaults', 'has_definition'} or None."""
    qtext, dtext = fc.split_definition(fc.clean(question))
    ql, dl = qtext.lower(), _SCOPE.sub(' ', dtext.lower()).strip()
    has_def = bool(dl)
    if has_def and re.search(r'\bnot answerable\b', dl): return None
    family = _detect(dl) if has_def else None
    if has_def and family is None:
        return None  # the stated definition is not one of these shapes
    if family is None:
        family = _detect(ql)
        if family is None: return None
    if has_def and _detect(ql) not in (None, family) and family not in ('missing_periods', 'file_count'):
        return None  # question and definition point at different shapes
    text = dl or ql
    if _GROUP_UNSUPPORTED.search(text) or _GROUP_UNSUPPORTED.search(ql): return None
    defaults, params = [], {}
    group = 'feed' if re.search(r'\b(?:by|per|for each) feed\b', text + ' ' + ql) else None
    if group: params['group_by'] = group

    if family in ('file_count', 'missing_periods'):
        feeds = _feeds(text)
        if feeds is None: return None
        if feeds: params['feeds'] = feeds
    if family == 'file_count':
        if _MALFORMED.search(text): return None
        hits = [name for name, pat in _COND if pat.search(text)]
        if not hits and has_def:
            hits = [name for name, pat in _COND if pat.search(ql)]
        if len(hits) != 1: return None
        name = hits[0]
        formats = _formats(text)
        if name == 'late':
            cond = 'late'
            if not has_def: defaults.append('late = after the name date, or > 7 days past the series usual lag')
        elif name == 'resend':
            if set(formats) & {'hl7', 'x12'}: return None  # HL7/X12 message-level duplicates are another question
            if re.search(r'identical|same (?:content|bytes)|byte[- ]for[- ]byte', text):
                cond = 'resend_identical'
            elif has_def:
                return None
            else:
                cond = 'resend_identical'; defaults.append('re-send = resend marker in the name with identical bytes')
        elif name == 'truncated':
            cond = 'truncated'
            if len(formats) > 1: formats = []  # 'flat files cut mid-record, HL7 batches ..., X12 ...' lists evidence kinds
            if not has_def: defaults.append('truncated = delivery_file truncation evidence')
        elif name == 'empty':
            header = bool(re.search(r'header[- ]only|header only', text))
            zero = bool(re.search(r'\b0 bytes\b|zero[- ]bytes?', text))
            expected = bool(re.search(r'(?:where|when) (?:rows|records|data) (?:were|was) expected|expected (?:rows|records|data)|'
                                      r'claims? (?:rows|records)|should have (?:had )?(?:rows|records|data)', text))
            if expected: cond = 'empty_expected'
            elif header and zero: cond = 'empty_any'
            elif zero: cond = 'zero_byte'
            elif header: cond = 'header_only'
            elif has_def: return None
            else:
                cond = 'empty_expected'; defaults.append('empty = 0 bytes or header only where the manifest claims rows')
        else:
            cond = 'v2_replacement'
            if not has_def: defaults.append('v2 = a _v2/_vN corrected copy')
        params['condition'] = cond
        if formats: params['formats'] = formats
        y = _year_filter(text)
        if y == 'ambiguous': return None
        if y: params['year'] = y
    elif family == 'missing_periods':
        y = _year_filter(text, re.compile(r'\b(?:periods?|months?|files?) (?:in|of|during|for) (20\d\d)\b|\bin (20\d\d)\b'))
        if y == 'ambiguous': return None
        if y: params['year'] = y
        if not has_def: defaults.append('expected = the series usual cadence, scheduled series only')
    elif family == 'duplicate_charts':
        if fc.years_in(text): return None
        if re.search(r'^(?:people|persons?|patients?|members?) (?:with|who have|having) (?:at least one|more than one|a duplicate|duplicate)', text) or \
                (not has_def and re.search(r'how many (?:patients|people|persons|members) have more than one', ql)):
            params['unit'] = 'people'
        else:
            params['unit'] = 'charts'
            if not re.search(r'patient_ids? that belong|charts? that belong|duplicate charts? present', text):
                defaults.append('duplicate charts = extra charts of one person within one EHR system')
    elif family == 'chart_renumbering':
        if fc.years_in(text): return None
        if not has_def: defaults.append('MRN change = two MRNs within a migrated practice')
    elif family == 'corrected_results':
        y = _year_filter(text, re.compile(r'\b(?:in|during|dated) (20\d\d)\b'))
        if y == 'ambiguous': return None
        if y: params['year'] = y
        if not has_def: defaults.append('corrected = result_status corrected')
    elif family == 'restated_records':
        if fc.years_in(text): return None
        streams = []
        for pat, st in ((r'\blab', 'labs'), (r'\bclaims?\b', 'payer_medical_claims'), (r'enrollment|spans?\b', 'enrollment_spans'),
                        (r'\bencounters?\b', 'encounters'), (r'\bconditions?\b|\bproblems?\b', 'conditions'),
                        (r'medication', 'medication_lists'), (r'pharmacy', 'payer_pharmacy_claims')):
            if re.search(pat, text): streams.append(st)
        if streams: params['streams'] = streams
        if not has_def: defaults.append('restated = superseded by a later received record')
    elif family == 'roster_after_departure':
        if fc.years_in(text): return None
        params['unit'] = ('rows' if re.search(r'roster rows|\brows\b', text) else
                          'providers' if re.search(r'distinct providers|how many providers', text) and not re.search(r'affiliation', text)
                          else 'affiliations')
        if not has_def: defaults.append('stale = listed active on a roster dated after the term date')
    elif family == 'conversion_duplicates':
        if fc.years_in(text): return None
        named = [CONVERSION_STREAMS[k] for k, pat in (('problems', r'\bproblems?\b|conditions?'), ('meds', r'\bmeds?\b|medications?'),
                                                      ('allergies', r'allerg'), ('immunizations', r'immuni[sz]ations?|vaccin'),
                                                      ('labs', r'\blabs?\b|lab results?'), ('notes', r'\bnotes?\b'))
                 if re.search(pat, text)]
        if not named:
            named = list(CONVERSION_STREAMS.values()); defaults.append('converted streams = problems, meds, allergies, immunizations, labs, notes')
        params['streams'] = named
    returns = 'table' if params.get('group_by') else 'count'
    return {'family': family, 'parameters': params, 'returns': returns, 'defaults': defaults, 'has_definition': has_def}


# ============================================================================================ runtime operation
def _module():
    import sys
    return sys.modules[__name__]


def plan(path, question, as_of, **_):
    return fc.plan(_module(), question, as_of)


def reuse(path, question):
    return fc.reuse(_module(), path, question)


def record(path, question, planner_receipt, answer):
    return fc.record(_module(), path, question, planner_receipt, answer)
