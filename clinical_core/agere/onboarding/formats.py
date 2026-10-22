"""What kind of source a workspace was pointed at: a native feed delivery (X12, HL7 v2, FHIR bulk, NCPDP, CCLF, EHR CSV
extracts, scanned documents), an OMOP CDM database, a Tuva input layer, or another analytics warehouse.

    detect_source(spec) -> {'schema', 'kind', 'path', 'formats', 'standards', 'feeds', 'files', 'sampled', 'tables', 'signature'}

kind: native_delivery | omop | tuva_input_layer | warehouse | unknown. A native delivery is read by the same parsers the
native ingest uses (ingest/landing.parsers(), first detect() wins), sampled per feed folder and file extension (at most
SAMPLE files per group, so a 100k-file delivery is classified in seconds); each group's file count is attributed to the
parser most of its sample matched. Standards are read from the first bytes: the X12 transaction set (ST01: 837, 835, 834,
...), the HL7 message type (MSH-9), the FHIR resourceType. Databases are classified by table names only (no data read).
Synthetic data only; read-only; folders named private are never read.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
import sqlite3

VERSION = 'source-format-detection/1'
SAMPLE = 12
NATIVE = 'native://'
OMOP_TABLES = {'person', 'visit_occurrence', 'condition_occurrence', 'measurement', 'drug_exposure', 'procedure_occurrence',
               'observation_period', 'payer_plan_period', 'care_site', 'provider', 'death', 'observation', 'cost'}
TUVA_TABLES = {'eligibility', 'medical_claim', 'pharmacy_claim', 'patient', 'encounter', 'lab_result', 'condition',
               'medication', 'practitioner', 'location', 'procedure', 'observation', 'immunization', 'appointment'}
FEED_LABEL = {'cms_cclf': 'CMS CCLF (fixed width)', 'cms_cclf_csv': 'CMS CCLF (CSV)', 'cms_alr': 'CMS ALR (CSV)',
              'payers': 'payer flat extract', 'programs': 'program flat file', 'rosters': 'provider roster',
              'sdoh_vendor': 'SDOH vendor file', 'ehr': 'EHR CSV extract'}
X12_NAMES = {'837': 'X12 837 claim', '835': 'X12 835 remittance', '834': 'X12 834 enrollment', '270': 'X12 270 eligibility inquiry',
             '271': 'X12 271 eligibility response', '276': 'X12 276 claim status inquiry', '277': 'X12 277 claim status',
             '278': 'X12 278 authorization', '820': 'X12 820 premium payment', '999': 'X12 999 acknowledgment'}


def _path_of(spec):
    s = str(spec)
    if s.startswith(NATIVE): s = s[len(NATIVE) - 1:] if s.startswith(NATIVE + '/') else s[len(NATIVE):]
    for prefix in ('sqlite:///', 'duckdb:///', 'file://'):
        if s.startswith(prefix): s = '/' + s[len(prefix):].lstrip('/') if prefix != 'file://' else s[len(prefix):]
    return Path(s)


def _head(path, n=2048):
    try:
        with open(path, 'rb') as fh:
            return fh.read(n)
    except OSError:
        return b''


def standard(parser, path, feed=None):
    """Plain-language standard of one file: 'X12 837 claim', 'HL7 v2 ADT^A01', 'FHIR Patient (bulk ndjson)', ..."""
    head = _head(path)
    if parser == 'x12':
        m = re.search(rb'ST[*|^~:](\d{3})', head)
        return X12_NAMES.get(m.group(1).decode(), f'X12 {m.group(1).decode()}') if m else 'X12'
    if parser == 'hl7':
        i = head.find(b'MSH')  # batches open with FHS/BHS; the first message header carries the type
        seg = re.split(rb'[\r\n]', head[i:])[0] if i >= 0 else b''
        if len(seg) > 4:
            fields = seg.split(seg[3:4])
            if len(fields) > 8 and fields[8]:
                comp = seg[4:5].decode('latin-1') or '^'
                return 'HL7 v2 ' + comp.join(fields[8].decode('latin-1').split(comp)[:2])
        return 'HL7 v2'
    if parser == 'fhir':
        m = re.search(rb'"resourceType"\s*:\s*"([A-Za-z]+)"', head)
        return f"FHIR {m.group(1).decode()} (bulk ndjson)" if m else 'FHIR bulk ndjson'
    if parser == 'ncpdp': return 'NCPDP telecom/SCRIPT'
    if parser == 'documents': return 'scanned document (PDF/image, OCR)'
    if parser == 'flat':
        label = FEED_LABEL.get(feed, 'delimited flat file')
        if feed == 'ehr':
            vendor = next((p for p in Path(path).parts if p.lower() in ('epic', 'athena', 'ecw', 'cerner', 'meditech')), None)
            if vendor: label += f' ({vendor})'
        return label
    return FEED_LABEL.get(feed, 'unrecognized')


def detect_delivery(root, sample=SAMPLE):
    from ..ingest import landing
    root = Path(root)
    mods = landing.parsers()
    groups = defaultdict(list)
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d.lower() != 'private')
        for f in sorted(files):
            p = Path(dirpath) / f
            if f.startswith('.') or f in ('manifest.json', 'README.md', 'NATIVE_INDEX.md'): continue
            rel = p.relative_to(root)
            feed = rel.parts[0] if len(rel.parts) > 1 else None
            groups[(feed, p.suffix.lower())].append(p)
    formats, standards, feeds, sampled, total = Counter(), Counter(), defaultdict(Counter), 0, 0
    for (feed, ext), paths in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        total += len(paths)
        step = max(1, len(paths) // sample)
        picks = paths[::step][:sample]
        votes, labels = Counter(), Counter()
        for p in picks:
            ctx = landing.context_for(p, root)
            parser = next((m.__name__.rsplit('.', 1)[-1] for m in mods if landing._safe_detect(m, p, ctx)), None)
            votes[parser or 'unparsed'] += 1
            labels[standard(parser, p, feed) if parser else
                   (FEED_LABEL[feed] + ' (not parsed natively; CSV copy used)' if feed == 'cms_cclf' else 'unrecognized')] += 1
        sampled += len(picks)
        parser = votes.most_common(1)[0][0]
        formats[parser] += len(paths)
        feeds[feed or '(root)'][parser] += len(paths)
        for label, n in labels.items():
            standards[label] += round(len(paths) * n / len(picks))
    return {'formats': dict(formats), 'standards': dict(standards.most_common()), 'feeds': {k: dict(v) for k, v in feeds.items()},
            'files': total, 'sampled': sampled}


def _tables(path):
    p = Path(path)
    head = _head(p, 16)
    if head.startswith(b'SQLite format 3'):
        db = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
        try:
            return {r[0].lower() for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        finally:
            db.close()
    import duckdb
    db = duckdb.connect(str(p), read_only=True)
    try:
        return {f'{s}.{t}'.lower() for s, t in db.execute('SELECT schema_name, table_name FROM duckdb_tables()').fetchall()} | \
               {t.lower() for _, t in db.execute('SELECT schema_name, table_name FROM duckdb_tables()').fetchall()}
    finally:
        db.close()


def classify_tables(tables):
    names = {t.rsplit('.', 1)[-1] for t in tables}
    omop, tuva = names & OMOP_TABLES, names & TUVA_TABLES
    if {'person', 'visit_occurrence'} <= names and len(omop) >= 4:
        return 'omop', sorted(omop)
    if {'eligibility', 'medical_claim'} <= names or (any(t.startswith('input_layer.') for t in tables) and len(tuva) >= 4):
        return 'tuva_input_layer', sorted(tuva)
    return 'warehouse', []


def detect_source(spec):
    """Classify a workspace source spec (native://dir, a plain directory, sqlite:///file, duckdb:///file, a db file path or a
    remote warehouse URI). Never raises: an unreadable source is kind 'unknown' with the reason."""
    out = {'schema': VERSION, 'spec': re.sub(r'//[^/@]*@', '//***@', str(spec)), 'kind': 'unknown', 'formats': {}, 'standards': {},
           'feeds': {}, 'files': 0, 'sampled': 0, 'tables': 0, 'signature': []}
    s = str(spec)
    if re.match(r'^(snowflake|bigquery|databricks|postgres(ql)?|mysql)://', s):
        out.update(kind='warehouse', reason='remote analytics warehouse (classified by its mapping, not sniffed)'); return out
    p = _path_of(spec)
    out['path'] = str(p)
    try:
        if p.is_dir():
            out.update(kind='native_delivery', **detect_delivery(p))
            if not out['files'] or set(out['formats']) <= {'unparsed'}:
                out['kind'] = 'unknown'; out['reason'] = 'no file any native parser recognises'
            return out
        if p.is_file():
            tables = _tables(p)
            kind, sig = classify_tables(tables)
            out.update(kind=kind, tables=len(tables), signature=sig)
            return out
        out['reason'] = 'source path does not exist'
    except Exception as exc:  # noqa: BLE001 - detection annotates; it never blocks onboarding
        out['reason'] = f'{type(exc).__name__}: {exc}'[:300]
    return out


def normalize_source(spec):
    """A plain directory holding a native delivery becomes native://<dir> (the workspace then ingests it with the native
    parsers instead of field-mapping a database). -> (spec, detection)."""
    det = detect_source(spec)
    s = str(spec)
    if det['kind'] == 'native_delivery' and not s.startswith(NATIVE):
        s = NATIVE + str(_path_of(spec))
    return s, det


def summary(det):
    return json.dumps({k: det.get(k) for k in ('kind', 'files', 'formats', 'standards')}, default=str)[:600]
