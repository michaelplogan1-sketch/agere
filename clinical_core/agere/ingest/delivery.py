"""File-level delivery facts for native workspaces: semantic tables delivery_file and delivery_gap.

    build(semantic_db, landing_path, raw_dir=None) -> summary dict

Questions about the deliveries themselves (late files, files sent twice, cut-off or empty files, files that never
arrived) cannot be answered from the record-level tables. This pass joins three sources, one row per delivered file:
- landing ingest_files: relpath, feed, parser, bytes, parsed rows per landing table, parse error. ingest_files.mtime is
  the local copy time of the file, not when it was delivered; it is kept as local_copy_time and never used as delivery.
- parse-issue tables written by the parsers: x12_parse_issue, hl7_parse_issue, ncpdp_parse_issue, fhir_parse_error,
  flat_schema_drift, and flat_row_issue on any flat landing table (short/long rows).
- the delivery manifest <raw>/manifest.json when the delivery includes one: delivered date, sha256, sender, format and
  the record count the sender claims for the file.
delivery_gap lists expected periods with no file in a series (see DOC). Nothing here edits landing or the manifest.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import statistics

VERSION = 'delivery-file/2'
SCHEDULED_FILL = 0.9  # a series is on a schedule when it has a file in at least 90% of its expected periods
ISSUE_TABLES = ('x12_parse_issue', 'hl7_parse_issue', 'ncpdp_parse_issue', 'fhir_parse_error', 'ccda_parse_issue', 'flat_schema_drift')
# Landing tables holding one row per delivered record for parsers whose files also yield child rows.
PRIMARY = {'x12': ('x12_835_claim', 'x12_837_claim', 'x12_834_member'), 'hl7': ('hl7_message',), 'ncpdp': ('ncpdp_claim',), 'ccda': ('ccda_document',)}
X12_TRUNCATION = ('missing_iea_at_eof', 'missing_se_at_eof', 'missing_ge_at_eof')
# Parsers whose files are read byte-wise for record counts, header-only and cut-off evidence (one record per line);
# None = no parser read the file (CMS CCLF fixed-width). HL7 batches and X12 interchanges carry their own trailers.
LINE_FORMATS = {'flat': 'flat', 'fhir': 'fhir', 'ncpdp': 'ncpdp', None: 'fixed_width'}
# Activity-driven formats: HL7 batches, X12 interchanges and FHIR exports carry a file only when there was something to
# send, so their empty periods are never missing files.
ACTIVITY_PARSERS = ('fhir', 'hl7', 'x12', 'ccda')
MALFORMED_DRIFT = ('columns_added_removed', 'columns_removed')
EXT = re.compile(r'(?i)\.(csv|txt|x12|hl7|ndjson|json|dat|edi|psv|tsv|xml|zip|gz)$')
RESEND = re.compile(r'(?i)(?:^|[_\-. ])resend(?=$|[_\-. ])')
V2 = re.compile(r'(?i)(?:^|[_\-. ])v(\d+)(?=$|[_\-. ])')
P_MONTH, P_WEEK = re.compile(r'^(\d{4})-(\d{2})$'), re.compile(r'^(\d{4})-W(\d{2})$')
P_DAY, P_QUARTER, P_YEAR = re.compile(r'^(\d{4})-(\d{2})-(\d{2})$'), re.compile(r'^(\d{4})Q([1-4])$'), re.compile(r'^(\d{4})$')

DDL = """
CREATE TABLE delivery_file(relpath TEXT PRIMARY KEY, feed TEXT, instance TEXT, kind TEXT, series TEXT, file_name TEXT,
  period TEXT, cadence TEXT, sender TEXT, format TEXT, parser TEXT, bytes INTEGER, sha256 TEXT, delivered_date TEXT,
  name_date TEXT, days_after_name_date INTEGER, days_after_period_end INTEGER, series_usual_lag_days INTEGER,
  late_vs_name_date INTEGER, later_than_series_usual INTEGER, claimed_records INTEGER, parsed_records INTEGER,
  parsed_rows INTEGER, byte_records INTEGER, claimed_vs_parsed_rows INTEGER, parse_error TEXT, parse_issue_count INTEGER, parse_issue_codes TEXT,
  flat_row_issue_rows INTEGER, schema_drift TEXT, empty_file INTEGER, header_only INTEGER, empty_or_header_only INTEGER,
  empty_where_expected INTEGER, truncated INTEGER, truncation_evidence TEXT, is_resend INTEGER, resend_by_name INTEGER,
  content_identical_to TEXT, is_v2_replacement INTEGER, is_restatement INTEGER, is_original INTEGER,
  series_gap_before INTEGER, local_copy_time TEXT, in_manifest INTEGER);
CREATE INDEX delivery_file_series ON delivery_file(series, period);
CREATE TABLE delivery_gap(series TEXT, feed TEXT, instance TEXT, kind TEXT, cadence TEXT, missing_period TEXT,
  position TEXT, previous_period TEXT, next_period TEXT, previous_file TEXT, next_file TEXT);
"""

COLUMNS = [c.split()[0] for c in DDL.split('CREATE TABLE delivery_file(', 1)[1].split(');', 1)[0].replace('\n', ' ').split(', ')]

DOC = {
    'delivery_file': (
        'One row per file in the native delivery (every file in raw/, parsed or not), for questions about the deliveries '
        'themselves rather than their records. relpath is the path under raw/; feed = first folder; instance = the folders '
        'between feed and period; kind = the file name with dates, resend/v2 markers and extension removed; series = '
        'feed|instance|kind; period = the delivery-period folder (YYYY-MM, YYYY-Www, YYYYQn, YYYY or YYYY-MM-DD, else the '
        'month of the date in the file name); cadence = month | week | quarter | year. From the delivery manifest '
        '(raw/manifest.json, in_manifest = 1): delivered_date, sha256, sender, format, claimed_records (record count the '
        'sender states). From parsing: parser (NULL = no parser read the file), parsed_records (records parsed: claims for '
        'X12, messages for HL7, claims for NCPDP, resources for FHIR, data rows for flat files), parsed_rows (all rows '
        'written including child segments), byte_records (records counted from the file bytes for line-per-record files: '
        'complete lines in the file\'s own line terminator, minus the header for delimited files and the 00/99 lines for '
        'NCPDP; NULL for HL7/X12 or when raw/ is not readable), parse_error, parse_issue_count / parse_issue_codes (rows in the x12/hl7/ncpdp '
        'parse-issue tables, fhir_parse_error and flat short/long rows; X12 se_segment_count_mismatch warnings are '
        'excluded as parser noise), flat_row_issue_rows, schema_drift. Derived: claimed_vs_parsed_rows = claimed_records - '
        'parsed_records; empty_file (0 bytes); header_only (a delimited file holding exactly one complete, terminated '
        'header line and nothing else, whatever the manifest claims); empty_or_header_only; '
        'empty_where_expected (empty or header-only while the manifest claims records > 0); truncated (cut off: X12 '
        'missing IEA/SE/GE at end of file, HL7 batch trailer declaring more messages than found, NCPDP missing 99 '
        'trailer, an unparseable last FHIR line; for flat, FHIR ndjson, NCPDP and CCLF fixed-width files a last record '
        'cut off (no final line terminator, or a CRLF file ending in a lone CR), a delimited file that stops inside its '
        'header line, or fewer byte_records than claimed - never on a malformed file or on a byte-identical copy of '
        'another delivered file); '
        'truncation_evidence names which); is_resend (resend marker in the name, or byte-identical by sha256 to an '
        'earlier-delivered non-empty file of the same series and period; content_identical_to '
        '= that file, else the earliest file with identical bytes of any series or period); resend_by_name; is_v2_replacement '
        '(a _v2/_vN corrected copy; compare content_identical_to to tell an identical v2 from a corrected one); '
        'is_restatement (full restatement files); is_original (none of resend/v2/restatement). Lateness: name_date = the '
        'date in the file name (YYYYMMDD or CMS Dyymmdd); days_after_name_date = delivered_date - name_date; '
        'days_after_period_end = delivered_date - last day of the period; series_usual_lag_days = median '
        'days_after_period_end of the series\' original files; late_vs_name_date = delivered after its name date; '
        'later_than_series_usual = days_after_period_end exceeds the series usual lag by more than 7 days. '
        'series_gap_before = expected periods missing just before this file in its series (see delivery_gap). '
        'local_copy_time is when the file was copied to local disk (file mtime), never the delivery date.'),
    'delivery_gap': (
        'Expected periods with no delivered file, per series (feed|instance|kind of delivery_file). Expected cadence = the '
        'most common step between consecutive periods of the series (so a quarterly series in monthly folders is not '
        'flagged for its off months). HL7, X12 and FHIR files are activity-driven (sent only when there is something to '
        'send) and never checked; resend copies never stand for a period (v2 and restatement files do). A sender writes a '
        'period only when it has something to deliver (an EHR instance with no activity that month writes no folder), so '
        'a period its sender skipped altogether is not a gap. Only scheduled series are checked: files in at least 90% of '
        'the periods (on the step) in which the sender delivered. position = inside (a period between two files of the '
        'series in which the same sender delivered its other files but not this one) or trailing (the delivery\'s final '
        'cycle for the feed: the series delivered the cycle before, its usual delivery date - period end + the series\' '
        'usual lag - has passed by the last delivered date, and the sender delivers that cycle or has never skipped one). '
        'A series that starts late is not a gap before its first file. previous/next_period and _file are the neighbouring files.'),
}


def _period(token):
    """-> (cadence, ordinal, label) for a period folder token, else None."""
    if not token:
        return None
    m = P_MONTH.match(token)
    if m and 1 <= int(m[2]) <= 12:
        return 'month', int(m[1]) * 12 + int(m[2]) - 1, token
    m = P_WEEK.match(token)
    if m:
        try:
            return 'week', date.fromisocalendar(int(m[1]), int(m[2]), 1).toordinal() // 7, token
        except ValueError:
            return None
    m = P_DAY.match(token)
    if m:
        try:
            date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
        return 'month', int(m[1]) * 12 + int(m[2]) - 1, token[:7]
    m = P_QUARTER.match(token)
    if m:
        return 'quarter', int(m[1]) * 4 + int(m[2]) - 1, token
    m = P_YEAR.match(token)
    if m and 1990 < int(m[1]) < 2100:
        return 'year', int(m[1]), token
    return None


def _label(cadence, ordinal):
    if cadence == 'month':
        return f'{ordinal // 12:04d}-{ordinal % 12 + 1:02d}'
    if cadence == 'week':
        y, w, _ = date.fromordinal(ordinal * 7 + 1).isocalendar()
        return f'{y:04d}-W{w:02d}'
    if cadence == 'quarter':
        return f'{ordinal // 4:04d}Q{ordinal % 4 + 1}'
    return f'{ordinal:04d}'


def _period_end(cadence, ordinal):
    if cadence == 'month':
        y, m = divmod(ordinal + 1, 12)
        return date(y, m + 1, 1) - timedelta(days=1)
    if cadence == 'week':
        return date.fromordinal(ordinal * 7 + 1) + timedelta(days=6)
    if cadence == 'quarter':
        y, q = divmod(ordinal + 1, 4)
        return date(y, q * 3 + 1, 1) - timedelta(days=1)
    return date(ordinal, 12, 31)


def _name_date(name):
    for m in re.finditer(r'(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)', name):
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            continue
    m = re.search(r'(?:^|\.)D(\d{2})(\d{2})(\d{2})(?:\.|_|$)', name)
    if m:
        try:
            return date(2000 + int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    return None


def kind_of(name):
    """The file name with dates, resend/vN markers and extension removed (stable across a series' files)."""
    stem = EXT.sub('', name)
    stem = RESEND.sub('', stem)
    stem = V2.sub('', stem)
    stem = re.sub(r'\d{4}-W\d{2}', '#', stem)
    stem = re.sub(r'\d{4}-\d{2}(-\d{2})?', '#', stem)
    stem = re.sub(r'\d{4}Q[1-4]', '#', stem)
    stem = re.sub(r'(?<=Y)\d{2}(?=\.)', '#', stem)
    return re.sub(r'\d{4,}', '#', stem)  # a CMS ALR table suffix (_1-1, _1-2 ...) stays: it names the table


def describe_path(relpath):
    """-> dict(feed, instance, kind, series, period, cadence, ordinal, name) for a path under raw/."""
    parts = Path(relpath).parts
    name = parts[-1]
    feed = parts[0] if len(parts) > 1 else None
    folders = list(parts[1:-1])
    per, at = None, None
    for i in range(len(folders) - 1, -1, -1):
        per = _period(folders[i])
        if per:
            at = i
            break
    instance = '/'.join(f for i, f in enumerate(folders) if i != at) or None
    if per is None:
        nd = _name_date(name)
        if nd:
            per = ('month', nd.year * 12 + nd.month - 1, f'{nd.year:04d}-{nd.month:02d}')
    kind = kind_of(name)
    return {'feed': feed, 'instance': instance, 'kind': kind, 'series': f'{feed}|{instance or ""}|{kind}',
            'period': per[2] if per else None, 'cadence': per[0] if per else None, 'ordinal': per[1] if per else None,
            'name': name}


def load_manifest(raw_dir):
    """{relpath under raw/: manifest entry} from <raw>/manifest.json, or {} when the delivery has none."""
    if not raw_dir:
        return {}
    path = Path(raw_dir) / 'manifest.json'
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    files = data.get('files') if isinstance(data, dict) else data
    out = {}
    for f in files or []:
        p = str(f.get('path') or '')
        if not p:
            continue
        out[p[4:] if p.startswith('raw/') else p] = f
    return out


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _issues(land, tables):
    """{relpath: Counter(issue code)}, {relpath: truncation evidence list}, {relpath: flat_row_issue rows},
    {relpath: last flat row short?}, {relpath: drift issue}."""
    issues, trunc, flat_rows, drift = defaultdict(Counter), defaultdict(list), Counter(), {}
    if 'x12_parse_issue' in tables:
        for f, reason, n in land.execute('SELECT _file, reason, count(*) FROM x12_parse_issue GROUP BY 1, 2'):
            code = (reason or '').split(':')[0]
            if code == 'se_segment_count_mismatch':
                continue  # off-by-one warnings from the segment counter, not a delivery defect
            issues[f]['x12:' + code] += n
            if code in X12_TRUNCATION and 'x12_' + code not in trunc[f]:
                trunc[f].append('x12_' + code)
    if 'hl7_parse_issue' in tables:
        for f, code, detail in land.execute('SELECT _file, issue_code, detail FROM hl7_parse_issue'):
            issues[f]['hl7:' + str(code)] += 1
            m = re.search(r'declares (\d+) messages?, found (\d+)', detail or '')
            if code == 'batch_count_mismatch' and m and int(m[2]) < int(m[1]) and 'hl7_batch_short' not in trunc[f]:
                trunc[f].append('hl7_batch_short')
    if 'ncpdp_parse_issue' in tables:
        for f, code, n in land.execute('SELECT _file, issue_code, count(*) FROM ncpdp_parse_issue GROUP BY 1, 2'):
            issues[f]['ncpdp:' + str(code)] += n
            if code == 'missing_trailer':
                trunc[f].append('ncpdp_missing_trailer')
    if 'ccda_parse_issue' in tables:
        for f, code, n in land.execute('SELECT _file, code, count(*) FROM ccda_parse_issue GROUP BY 1, 2'):
            issues[f]['ccda:' + str(code)] += n
    fhir_last = {}
    if 'fhir_parse_error' in tables:
        for f, n, last in land.execute('SELECT _file, count(*), max(line_no) FROM fhir_parse_error GROUP BY 1'):
            issues[f]['fhir:parse_error'] += n
            fhir_last[f] = last
    if 'flat_schema_drift' in tables:
        for f, issue in land.execute('SELECT _file, issue FROM flat_schema_drift'):
            issues[f]['flat:' + str(issue)] += 1
            drift[f] = issue
    short_last = {}
    for t in sorted(tables):
        cols = {r[1] for r in land.execute(f'PRAGMA table_info("{t}")')}
        if 'flat_row_issue' not in cols or '_file' not in cols:
            continue
        hit = land.execute(f'SELECT _file, count(*) FROM "{t}" WHERE flat_row_issue IS NOT NULL GROUP BY 1').fetchall()
        for f, n in hit:
            flat_rows[f] += n
            issues[f]['flat:row_issue'] += n
        if hit:
            q = (f'SELECT t._file, t.flat_row_issue FROM "{t}" t JOIN (SELECT _file, max(_row) r FROM "{t}" '
                 f'WHERE _file IN (SELECT _file FROM "{t}" WHERE flat_row_issue IS NOT NULL) GROUP BY _file) m '
                 'ON m._file = t._file AND m.r = t._row')
            for f, issue in land.execute(q):
                if issue and str(issue).startswith('short_row'):
                    short_last[f] = True
    return issues, trunc, flat_rows, drift, fhir_last, short_last


def scan_lines(data, fmt):
    """Byte-level facts of a line-per-record file -> dict(complete, records, cut, header_only, cut_in_header).

    The line terminator is the file's own (CRLF when its first line feed follows a CR, else LF; CR-only files are read
    on CR), so a bare LF inside a CRLF file is an embedded line break, not a record. complete = terminated non-blank
    lines; a non-blank tail after the last terminator, or a CRLF file ending in a lone CR, is a record cut off (cut).
    records = complete lines that are records: minus the header line for delimited (flat) files, minus the 00 header
    and 99 trailer lines for NCPDP. header_only = a delimited file holding exactly one complete header line and nothing
    else; cut_in_header = a delimited file that stops inside its header line (truncated, not header-only)."""
    if not data:
        return {'complete': 0, 'records': 0, 'cut': False, 'header_only': False, 'cut_in_header': False}
    nl = data.find(b'\n')
    if nl >= 0:
        term = b'\r\n' if nl > 0 and data[nl - 1:nl] == b'\r' else b'\n'
    elif data.count(b'\r') > 1 or (data.count(b'\r') == 1 and not data.endswith(b'\r')):
        term = b'\r'
    else:
        term = None  # no complete line at all
    if term is None:
        parts, tail = [], data
    else:
        parts = data.split(term)
        parts, tail = parts[:-1], parts[-1]
    lines = [x for x in parts if x.strip()]
    cut = bool(tail.strip()) or (term != b'\n' and tail.endswith(b'\r'))
    complete = len(lines)
    if fmt == 'flat':
        records = max(complete - 1, 0)
    elif fmt == 'ncpdp':
        records = sum(1 for i, x in enumerate(lines) if not (i == 0 and x.startswith(b'00')) and not x.startswith(b'99'))
    else:
        records = complete
    return {'complete': complete, 'records': records, 'cut': cut,
            'header_only': fmt == 'flat' and complete == 1 and not cut,
            'cut_in_header': fmt == 'flat' and complete == 0 and cut}


def _scan_file(raw_dir, rel, fmt):
    """scan_lines of a readable raw file, None when the raw delivery is not readable here."""
    if not raw_dir:
        return None
    try:
        data = (Path(raw_dir) / rel).read_bytes()
    except OSError:
        return None
    return scan_lines(data, fmt)


def build(out, landing_path, raw_dir=None):
    """Write delivery_file and delivery_gap into an open semantic-layer connection. -> summary."""
    land = sqlite3.connect(Path(landing_path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        tables = _tables(land)
        if 'ingest_files' not in tables:
            raise RuntimeError('landing database has no ingest_files')
        files = land.execute('SELECT relpath, feed, parser, bytes, mtime, rows, tables, error FROM ingest_files').fetchall()
        issues, trunc, flat_rows, drift, fhir_last, short_last = _issues(land, tables)
    finally:
        land.close()
    manifest = load_manifest(raw_dir)
    rows = {}
    for rel, feed, parser, size, mtime, nrows, tjson, error in files:
        d = describe_path(rel)
        if d['feed'] is None and rel in ('manifest.json', 'README.md', 'NATIVE_INDEX.md'):
            continue  # delivery documentation at the root of raw/, not a data file
        try:
            per_table = json.loads(tjson or '{}')
        except ValueError:
            per_table = {}
        data = {t: n for t, n in per_table.items() if t not in ISSUE_TABLES}
        prim = PRIMARY.get(parser)
        parsed = sum(n for t, n in data.items() if t in prim) if prim else sum(data.values())
        m = manifest.get(rel) or {}
        claimed = m.get('records') if isinstance(m.get('records'), int) else None
        size = m.get('bytes') if size is None else size
        empty = int((size or 0) == 0)
        ev = list(trunc.get(rel, []))
        if parser == 'fhir' and rel in fhir_last and fhir_last[rel] is not None and fhir_last[rel] >= parsed + issues[rel]['fhir:parse_error']:
            ev.append('fhir_last_line_unparseable')
        fmt = LINE_FORMATS.get(parser) if parser in LINE_FORMATS else None
        scan = _scan_file(raw_dir, rel, fmt) if fmt and not empty else None
        if scan is not None:
            header_only = int(scan['header_only'])
            byte_records = scan['records']
            if scan['cut_in_header']:
                ev.append('flat_cut_in_header')  # stops inside its header line
            elif scan['cut']:
                ev.append(f'{fmt}_no_final_newline')  # last record cut off (incl. a CRLF file ending in a lone CR)
        else:
            header_only = int(parser == 'flat' and not empty and parsed == 0 and not error)
            byte_records = None
            if parser == 'flat' and not empty and short_last.get(rel):
                ev.append('flat_short_last_row')  # raw file not readable here: a short last row is the best evidence
        # A malformed file (parse error, dropped/added columns, bad rows other than a cut last row) has no reliable count.
        malformed = bool(error and parser) or drift.get(rel) in MALFORMED_DRIFT or \
            flat_rows.get(rel, 0) - (1 if short_last.get(rel) else 0) > 0
        name = d['name']
        nd = _name_date(name)
        delivered = m.get('delivered')
        try:
            dd = date.fromisoformat(str(delivered)[:10]) if delivered else None
        except ValueError:
            dd = None
        pe = _period_end(d['cadence'], d['ordinal']) if d['cadence'] else None
        resend_name = int(bool(RESEND.search(name.rsplit('.', 1)[0] if '.' in name else name)))
        v2 = int(bool(V2.search(name.rsplit('.', 1)[0] if '.' in name else name)))
        restat = int(bool(re.search(r'(?i)restatement', name)))
        codes = issues.get(rel) or Counter()
        rows[rel] = {
            'relpath': rel, 'feed': d['feed'], 'instance': d['instance'], 'kind': d['kind'], 'series': d['series'],
            'file_name': name, 'period': d['period'], 'cadence': d['cadence'], '_ord': d['ordinal'],
            'sender': m.get('sender'), 'format': m.get('format'), 'parser': parser, 'bytes': size, 'sha256': m.get('sha256'),
            'delivered_date': dd.isoformat() if dd else None, 'name_date': nd.isoformat() if nd else None,
            'days_after_name_date': (dd - nd).days if dd and nd else None,
            'days_after_period_end': (dd - pe).days if dd and pe else None, 'series_usual_lag_days': None,
            'late_vs_name_date': int(dd > nd) if dd and nd else None, 'later_than_series_usual': None,
            'claimed_records': claimed, 'parsed_records': parsed, 'parsed_rows': nrows, 'byte_records': byte_records,
            'claimed_vs_parsed_rows': (claimed - parsed) if claimed is not None else None, 'parse_error': error,
            'parse_issue_count': sum(codes.values()), 'parse_issue_codes': json.dumps(dict(sorted(codes.items()))) if codes else None,
            'flat_row_issue_rows': flat_rows.get(rel, 0), 'schema_drift': drift.get(rel),
            'empty_file': empty, 'header_only': header_only, 'empty_or_header_only': int(bool(empty or header_only)),
            'empty_where_expected': int(bool((empty or header_only) and (claimed or 0) > 0)) if claimed is not None else None,
            'truncated': int(bool(ev)), 'truncation_evidence': ','.join(ev) or None, '_ev': ev, '_fmt': fmt,
            '_malformed': malformed,
            'is_resend': resend_name, 'resend_by_name': resend_name, 'content_identical_to': None,
            'is_v2_replacement': v2, 'is_restatement': restat, 'is_original': int(not (resend_name or v2 or restat)),
            'series_gap_before': 0,
            'local_copy_time': (datetime.fromtimestamp(mtime, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                                if isinstance(mtime, (int, float)) else None),
            'in_manifest': int(bool(m))}
    for rel, m in manifest.items():  # files the manifest lists that never reached landing (not copied, or skipped)
        if rel not in rows:
            d = describe_path(rel)
            rows[rel] = dict.fromkeys(COLUMNS)
            rows[rel].update({'relpath': rel, 'feed': d['feed'], 'instance': d['instance'], 'kind': d['kind'],
                              'series': d['series'], 'file_name': d['name'], 'period': d['period'], 'cadence': d['cadence'],
                              '_ord': d['ordinal'], 'sender': m.get('sender'), 'format': m.get('format'), 'bytes': m.get('bytes'),
                              'sha256': m.get('sha256'), 'delivered_date': str(m.get('delivered') or '')[:10] or None,
                              'claimed_records': m.get('records'), 'in_manifest': 1, 'series_gap_before': 0,
                              'is_resend': 0, 'resend_by_name': 0, 'is_v2_replacement': 0, 'is_restatement': 0, 'is_original': 1})
    # Content identity: the earliest-delivered non-empty file with the same sha256.
    by_hash = defaultdict(list)
    for r in rows.values():
        if r.get('sha256') and (r.get('bytes') or 0) > 0:
            by_hash[r['sha256']].append(r)
    for group in by_hash.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda r: (r.get('delivered_date') or '9999', r['is_original'] == 0, r['relpath']))
        for i, r in enumerate(group[1:], 1):
            first = group[0]
            r['content_identical_to'] = first['relpath']
            same = next((x for x in group[:i] if x['series'] == r['series'] and x['period'] == r['period']), None)
            # A resend is the same file sent again for the same series and period; a reference table re-delivered
            # unchanged in every monthly drop is a new monthly file, not a resend.
            if same is not None:
                r['is_resend'] = 1
                r['content_identical_to'] = same['relpath']
    # Record shortfall: fewer records in the bytes than the manifest claims. Counted from the bytes (the parser's row
    # count drops or merges malformed rows), and never on a malformed file or on a byte-identical copy of another
    # delivered file (a stale copy re-sent under a new name or period: its claim describes data that was not sent).
    for r in rows.values():
        ev = r.pop('_ev', None)
        fmt, malformed = r.pop('_fmt', None), r.pop('_malformed', False)
        if ev is None:
            continue
        claimed, n = r.get('claimed_records'), r.get('byte_records')
        if n is None and fmt == 'flat':
            n = r.get('parsed_records')  # raw file not readable here: the parsed rows are the best count
        if (claimed is not None and n and claimed > n and not malformed and not r.get('content_identical_to')
                and not r.get('header_only')):
            ev.append('flat_fewer_rows_than_claimed' if fmt == 'flat' else f'{fmt or r.get("parser")}_fewer_records_than_claimed')
        r['truncated'], r['truncation_evidence'] = int(bool(ev)), ','.join(ev) or None
        r['empty_or_header_only'] = int(bool(r.get('empty_file') or r.get('header_only')))
    # Lateness against the series' usual lag, and period gaps per series.
    series = defaultdict(list)
    for r in rows.values():
        series[r['series']].append(r)
    gaps = []
    feed_last = {}
    for r in rows.values():
        if r.get('_ord') is not None:
            k = (r['feed'], r['cadence'])
            feed_last[k] = max(feed_last.get(k, r['_ord']), r['_ord'])
    for key, group in series.items():
        lags = [r['days_after_period_end'] for r in group if r.get('is_original') and r.get('days_after_period_end') is not None]
        usual = int(statistics.median(lags)) if lags else None
        for r in group:
            r['series_usual_lag_days'] = usual
            if usual is not None and r.get('days_after_period_end') is not None:
                r['later_than_series_usual'] = int(r['days_after_period_end'] > usual + 7)
    # Missing periods. Only scheduled file types are checked: HL7 batches, X12 interchanges and FHIR exports are
    # activity-driven (a file only when there was something to send), and resend copies never stand for a period. A
    # sender writes a period's folder only when it has something to deliver (an EHR instance with no activity that
    # month writes no folder), so a period the sender skipped altogether is not a missing file. A file type is missing
    # (a) inside its series when its sender delivered its other files that period but not this one, or (b) in the final
    # cycle, when the series delivered through the previous cycle, its usual delivery date (period end + the series'
    # usual lag) has passed by the delivery's last delivered date, and the sender still delivers that cycle (or is a
    # sender that has never skipped a cycle).
    def _sender(r):
        return (r.get('sender') or r.get('instance') or r.get('feed'), r.get('feed'), r.get('cadence'))

    def _counts(r):
        return r.get('_ord') is not None and not r.get('is_resend') and r.get('parser') not in ACTIVITY_PARSERS

    active = defaultdict(set)  # (sender, feed, cadence) -> ordinals in which the sender delivered a scheduled file
    for r in rows.values():
        if _counts(r):
            active[_sender(r)].add(r['_ord'])
    cutoff = max((r['delivered_date'] for r in rows.values() if r.get('delivered_date')), default=None)
    for key, group in series.items():
        g0 = group[0]
        if any(r.get('parser') in ACTIVITY_PARSERS for r in group):
            continue
        cad = {r['cadence'] for r in group if r.get('cadence')}
        if len(cad) != 1:
            continue
        cadence = cad.pop()
        present = defaultdict(list)
        for r in group:
            if _counts(r):
                present[r['_ord']].append(r)
        ords = sorted(present)
        if len(ords) < 3:
            continue
        steps = Counter(b - a for a, b in zip(ords, ords[1:]))
        step = max(steps.items(), key=lambda kv: (kv[1], -kv[0]))[0]
        if step <= 0:
            continue
        snd = _sender(next(iter(present[ords[0]])))
        sender_on = active[snd]
        grid = range(ords[0], ords[-1] + 1, step)
        expected = [o for o in grid if o in present or o in sender_on]
        if len(ords) / max(len(expected), 1) < SCHEDULED_FILL:
            continue  # activity-driven series (files only when there is something to send): absence is not a gap
        for a, b in zip(ords, ords[1:]):
            missing = [o for o in range(a + step, b, step) if o in sender_on]
            if not missing:
                continue
            for r in present[b]:
                r['series_gap_before'] = len(missing)
            prev_f, next_f = sorted(x['relpath'] for x in present[a])[0], sorted(x['relpath'] for x in present[b])[0]
            for o in missing:
                gaps.append((key, g0['feed'], g0['instance'], g0['kind'], cadence, _label(cadence, o), 'inside',
                             _label(cadence, a), _label(cadence, b), prev_f, next_f))
        last = feed_last.get((g0['feed'], cadence))
        if last is None or last - ords[-1] != step or cutoff is None:
            continue  # only the one final cycle: a longer silence is a series that ended, not a missing file
        usual = g0.get('series_usual_lag_days')
        due = _period_end(cadence, last) + timedelta(days=max(usual or 0, 0))
        if due.isoformat() > cutoff:
            continue  # not yet due when the delivery was cut
        steady = all(o in sender_on for o in range(min(sender_on), last, step))
        if last not in sender_on and not steady:
            continue  # a sender that writes only active periods and sent nothing this cycle
        prev_f = sorted(x['relpath'] for x in present[ords[-1]])[0]
        gaps.append((key, g0['feed'], g0['instance'], g0['kind'], cadence, _label(cadence, last), 'trailing',
                     _label(cadence, ords[-1]), None, prev_f, None))
    cols = COLUMNS
    out.execute('DROP TABLE IF EXISTS delivery_file')
    out.execute('DROP TABLE IF EXISTS delivery_gap')
    out.executescript(DDL)
    out.executemany(f'INSERT INTO delivery_file({", ".join(cols)}) VALUES ({", ".join("?" * len(cols))})',
                    [tuple(r.get(c) for c in cols) for r in rows.values()])
    out.executemany('INSERT INTO delivery_gap VALUES (?,?,?,?,?,?,?,?,?,?,?)', gaps)
    vals = list(rows.values())
    return {'version': VERSION, 'files': len(vals), 'manifest': bool(manifest), 'in_manifest': sum(r.get('in_manifest') or 0 for r in vals),
            'resends': sum(r.get('is_resend') or 0 for r in vals), 'content_identical': sum(1 for r in vals if r.get('content_identical_to')),
            'v2': sum(r.get('is_v2_replacement') or 0 for r in vals), 'truncated': sum(r.get('truncated') or 0 for r in vals),
            'empty_or_header_only': sum(r.get('empty_or_header_only') or 0 for r in vals),
            'empty_where_expected': sum(r.get('empty_where_expected') or 0 for r in vals), 'gaps': len(gaps),
            'note': 'ingest_files.mtime is the local copy time, not the delivery time; delivered_date comes from the manifest'}
