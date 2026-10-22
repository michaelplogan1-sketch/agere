"""Source sequence-gap and snapshot-recovery contracts: which feeds arrived incomplete, for which periods, and whether a later
delivery recovered them.

A feed series is one received stream from one sending organization (record.stream x record.organization_id). Its
arrival sequence is the record count per month of available_date (when Agere received the records); its coverage
sequence is the record count per month of event_date (what period the records describe). Contract per series:
  periodic         active in >= 6 months and >= 80% of the months between its first and last arrival, median >= 5 records
  missing_month    a month inside the span with no arrival, while the trailing baseline (median of up to 6 prior active
                   months) is >= 5 records
  partial_month    a month whose arrival is < 25% of that baseline (baseline >= 30): a partial file / truncated drop
  recovery         recovered_late_delivery: the period's event-month volume later reached >= 80% of its own baseline (late
                   or catch-up files filled the gap; recovered_in = last arrival month that contributed);
                   recovered_snapshot: records of the period were superseded by a later snapshot (record_correction
                   replacement delivered after the gap); unrecovered: the period's data is still short (shortfall stated)
The month containing the snapshot date is never judged (it is still arriving). Native raw-feed builds add the
delivery manifest's own evidence: delivery_gap rows (expected periodic files that never arrived, ingest/delivery.py) and,
when a raw/manifest.json is beside the build, months without any delivered file per feed (streaming/delivery_drop.py).

    detect(path, as_of=None, refresh=False) -> {'schema', 'source_digest', 'as_of', 'gaps': [...], 'series': n, ...}  (cached
                                               in stack/feed-gaps.json per source digest)
    annotate_tables(path, tables, periods, as_of) -> [message]   an answer reading a stream over a gap period
Engine 'feed_gaps' on the foundation-engine registry. No model calls; synthetic data only.
"""
import calendar
import json
import os
import re
import sqlite3
import statistics
import time
from datetime import date
from pathlib import Path

VERSION = 'feed-gaps/1'
CACHE = 'feed-gaps.json'
MIN_ACTIVE = 6
MIN_DENSITY = 0.8
MIN_MEDIAN = 5
MISSING_MIN_BASELINE = 10
PARTIAL_SHARE = 0.25
PARTIAL_MIN_BASELINE = 30
RECOVERED_SHARE = 0.8
KINDS = ('missing_month', 'partial_month', 'missing_file')
STATUSES = ('unrecovered', 'recovered_late_delivery', 'recovered_snapshot', 'reported_by_manifest')
# semantic tables -> received streams (annotation of answers that read them)
TABLE_STREAMS = {'claim': ('medical_claims',), 'claim_line': ('medical_claim_lines',), 'payer_claim': ('payer_medical_claims',),
                 'payer_claim_line': ('payer_medical_claim_lines',), 'payer_pharmacy_claim': ('payer_pharmacy_claims',),
                 'encounter': ('encounters',), 'visit': ('encounters',), 'encounter_diagnosis': ('encounters',), 'stay': ('adt', 'encounters'),
                 'adt_visit': ('adt', 'adt_messages'), 'utilization_event': ('adt', 'encounters', 'payer_medical_claims'),
                 'measurement': ('labs', 'vitals'), 'measurement_latest': ('labs', 'vitals'), 'fill': ('pharmacy_dispenses', 'payer_pharmacy_claims'),
                 'prescription_fill': ('pharmacy_dispenses',), 'prescription': ('medication_orders',), 'medication_list_entry': ('medication_lists',),
                 'condition': ('conditions',), 'problem_list_entry': ('conditions',), 'clinical_note': ('notes',), 'appointment': ('appointments',),
                 'immunization': ('immunizations',), 'scanned_document': ('scanned_documents',), 'hie_document': ('hie_documents',),
                 'enrollment_span': ('enrollment_spans',), 'member_month': ('enrollment_spans',), 'procedure_line': ('procedures',),
                 'allergy_entry': ('allergies',)}
FEEDS = {'claims': ('medical_claims', 'medical_claim_lines', 'payer_medical_claims', 'payer_medical_claim_lines', 'payer_pharmacy_claims'),
         'medical claims': ('medical_claims', 'payer_medical_claims'), 'pharmacy': ('pharmacy_dispenses', 'payer_pharmacy_claims', 'pharmacy_claims'),
         'pharmacy claims': ('payer_pharmacy_claims', 'pharmacy_claims'), 'labs': ('labs',), 'lab': ('labs',), 'vitals': ('vitals',),
         'adt': ('adt', 'adt_messages'), 'encounters': ('encounters',), 'notes': ('notes',), 'enrollment': ('enrollment_spans',),
         'eligibility': ('enrollment_spans',), 'appointments': ('appointments',), 'ehr': ('encounters', 'labs', 'vitals', 'notes', 'conditions'),
         'immunizations': ('immunizations',), 'conditions': ('conditions',), 'scanned documents': ('scanned_documents',)}


def _ro(path):
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True)


def _target(path):
    p = Path(path)
    return p if p.suffix == '.sqlite3' else p/'semantic.sqlite3'


def _month_add(m, k):
    y, mo = int(m[:4]), int(m[5:7]) - 1 + k
    return f'{y + mo // 12:04d}-{mo % 12 + 1:02d}'


def _months(a, b):
    out, m = [], a
    while m <= b:
        out.append(m); m = _month_add(m, 1)
    return out


def series_gaps(arrivals, coverage, as_of_month, superseded=None, *, min_median=MIN_MEDIAN, min_missing=MISSING_MIN_BASELINE):
    """One series. arrivals/coverage: {month: count} by arrival / event month; superseded: {event_month: [replacement months]}.
    -> (periodic: bool, [gap]) with gap = {month, kind, arrived, baseline, coverage, coverage_baseline, status, recovered_in, shortfall}."""
    months = sorted(m for m in arrivals if m < as_of_month)
    if len(months) < MIN_ACTIVE: return False, []
    span = _months(months[0], months[-1])
    if len(months) / len(span) < MIN_DENSITY or statistics.median(arrivals[m] for m in months) < min_median: return False, []
    gaps = []
    for i, m in enumerate(span):
        prior = [arrivals[x] for x in span[max(0, i - 6):i] if arrivals.get(x)]
        if len(prior) < 3: continue
        base, got = statistics.median(prior), arrivals.get(m, 0)
        kind = ('missing_month' if got == 0 and base >= min_missing else
                'partial_month' if 0 < got < PARTIAL_SHARE * base and base >= PARTIAL_MIN_BASELINE else None)
        if not kind: continue
        cprior = [coverage[x] for x in _months(_month_add(m, -6), _month_add(m, -1)) if coverage.get(x)]
        cbase = statistics.median(cprior) if cprior else None
        cov = coverage.get(m, 0)
        gap = {'month': m, 'kind': kind, 'arrived': got, 'baseline': base, 'coverage': cov, 'coverage_baseline': cbase,
               'status': 'unrecovered', 'recovered_in': None, 'shortfall': round(base - got)}
        later = sorted(r for r in (superseded or {}).get(m, ()) if r > m)
        if cbase and cov >= RECOVERED_SHARE * cbase:
            gap.update(status='recovered_late_delivery', shortfall=0,
                       recovered_in=max((x for x in months if x > m), default=None) if got == 0 else _month_add(m, 1))
        elif later:
            gap.update(status='recovered_snapshot', recovered_in=later[0], shortfall=0)
        elif cbase:
            gap['shortfall'] = round(cbase - cov)
        gaps.append(gap)
    return True, gaps


def manifest_gaps(raw_dir, as_of_month):
    """Native delivery: months without any delivered file per feed folder (delivery_drop.manifest_entries order)."""
    from ..streaming.delivery_drop import manifest_entries, _rel
    per = {}
    for e in manifest_entries(raw_dir):
        d = str(e.get('delivered') or '')[:7]
        if not re.fullmatch(r'\d{4}-\d{2}', d): continue
        feed = Path(_rel(e)).parts[0]
        per.setdefault(feed, {}).setdefault(d, 0); per[feed][d] += 1
    out = []
    for feed, months in per.items():
        periodic, gaps = series_gaps(months, months, as_of_month, min_median=1, min_missing=1)  # files, not records
        out += [{**g, 'stream': feed, 'organization_id': None, 'source': 'manifest', 'kind': 'missing_file' if g['kind'] == 'missing_month' else g['kind'],
                 'status': 'reported_by_manifest' if g['status'] == 'unrecovered' else g['status']} for g in gaps]
    return out


def detect(path, as_of=None, refresh=False, cache_dir=None):
    target = _target(path)
    cache = Path(cache_dir or target.parent)/CACHE
    db = _ro(target)
    started = time.perf_counter()
    try:
        meta = dict(db.execute('SELECT key, value FROM meta')) if db.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone() else {}
        digest = meta.get('source_digest') or str(target.stat().st_mtime_ns)
        as_of = str(as_of or meta.get('as_of') or date.today().isoformat())[:10]
        if not refresh and cache.exists():
            try:
                found = json.loads(cache.read_text())
                if found.get('source_digest') == digest and found.get('version') == VERSION: return found
            except (OSError, ValueError):
                pass
        present = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        arrivals, coverage = {}, {}
        for stream, org, am, em, n in db.execute(
                "SELECT stream, coalesce(organization_id, ''), substr(available_date,1,7), substr(event_date,1,7), count(*) FROM record "
                "WHERE available_date IS NOT NULL GROUP BY 1, 2, 3, 4"):
            key = (stream, org)
            if am: arrivals.setdefault(key, {}).setdefault(am, 0); arrivals[key][am] += n
            if em and re.fullmatch(r'\d{4}-\d{2}', em): coverage.setdefault(key, {}).setdefault(em, 0); coverage[key][em] += n
        superseded = {}
        if 'record_correction' in present:
            for stream, em, rm in db.execute("SELECT c.stream, substr(c.event_date,1,7), substr(c.replacement_available_date,1,7) FROM "
                                             "record_correction c WHERE c.replacement_available_date IS NOT NULL GROUP BY 1, 2, 3"):
                superseded.setdefault(stream, {}).setdefault(em, []).append(rm)
        gaps, periodic = [], 0
        for (stream, org), arr in arrivals.items():
            ok, found = series_gaps(arr, coverage.get((stream, org), {}), as_of[:7], superseded.get(stream))
            periodic += ok
            gaps += [{**g, 'stream': stream, 'organization_id': org or None, 'source': 'record_arrival'} for g in found]
        if 'delivery_gap' in present:
            for series, feed, kind, period in db.execute('SELECT series, feed, kind, missing_period FROM delivery_gap'):
                gaps.append({'month': str(period)[:7], 'kind': 'missing_file', 'stream': feed, 'organization_id': None, 'series': series,
                             'source': 'delivery_gap', 'status': 'reported_by_manifest', 'arrived': 0, 'baseline': None,
                             'coverage': None, 'coverage_baseline': None, 'recovered_in': None, 'shortfall': None, 'cadence_kind': kind})
    finally:
        db.close()
    raw = next((p/'manifest.json' for p in (target.parent/'raw', target.parent.parent/'raw') if (p/'manifest.json').exists()), None)
    if raw:
        try: gaps += manifest_gaps(raw.parent, as_of[:7])
        except (OSError, ValueError, KeyError): pass
    gaps.sort(key=lambda g: (g['status'] != 'unrecovered', g['month'], g['stream']))
    report = {'schema': 'agere-feed-gaps/1', 'version': VERSION, 'source_digest': digest, 'as_of': as_of, 'series': len(arrivals),
              'periodic_series': periodic, 'gaps': gaps, 'seconds': round(time.perf_counter() - started, 2)}
    try:
        tmp = cache.with_suffix('.tmp'); tmp.write_text(json.dumps(report, indent=1, default=str)); os.replace(tmp, cache)
    except OSError:
        pass
    return report


_MEMO = {}


def cached(path):
    cache = _target(path).parent/CACHE
    try: st = cache.stat()
    except OSError: return None
    key = (str(cache), st.st_mtime_ns)
    if key not in _MEMO:
        try: _MEMO.clear(); _MEMO[key] = json.loads(cache.read_text())
        except (OSError, ValueError): return None
    return _MEMO[key]


# ============================================================================== engine
OUTPUTS = ('gaps', 'by_stream')
PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'FEED COMPLETENESS / SEQUENCE GAPS: which received feeds (stream x sending organization) arrived incomplete: '
                  'months with no delivery or a partial delivery against the feed\'s own trailing baseline, missing periodic files '
                  '(native manifests), and whether a later late/catch-up delivery or a superseding snapshot recovered the period, '
                  'with the remaining shortfall.'}
CONTRACT = {'inputs': ['optional earlier_set'], 'parameters': {'streams': [], 'period': {'start': '', 'end': ''}, 'kinds': [], 'statuses': [],
                                                               'output': 'gaps|by_stream'},
            'meaning': 'Feed sequence-gap and snapshot-recovery contracts (feed_gaps.py): kinds ' + '|'.join(KINDS) + '; statuses '
                       + '|'.join(STATUSES) + '. Use for incomplete / partial / missing deliveries, missing months of data, feed gaps.'}
NATIVE_SCALARS = ('count',)


def parameter_schema(catalog, obj, string, array):
    return obj({'streams': array(string(None, 'received stream name'), 'Streams to report; empty = all'),
                'period': obj({'start': {'type': 'string'}, 'end': {'type': 'string'}}),
                'kinds': array(string(KINDS)), 'statuses': array(string(STATUSES)), 'output': string(OUTPUTS)})


def validate(p):
    if not isinstance(p, dict) or set(p) - {'streams', 'period', 'kinds', 'statuses', 'output'}:
        raise ValueError('feed_gaps parameters: streams, period, kinds, statuses, output')
    period = p.get('period') or {}
    if not isinstance(period, dict) or set(period) - {'start', 'end'} or any(v and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(v)) for v in period.values()):
        raise ValueError('period: {start, end} as YYYY-MM-DD')
    kinds, statuses = list(p.get('kinds') or []), list(p.get('statuses') or [])
    if set(kinds) - set(KINDS) or set(statuses) - set(STATUSES): raise ValueError(f'kinds in {KINDS}; statuses in {STATUSES}')
    output = p.get('output') or 'gaps'
    if output not in OUTPUTS: raise ValueError(f'output: one of {OUTPUTS}')
    return {'streams': list(p.get('streams') or []), 'period': {'start': period.get('start') or '', 'end': period.get('end') or ''},
            'kinds': kinds, 'statuses': statuses, 'output': output}


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    report = detect(path, as_of)
    lo, hi = p['period']['start'][:7], p['period']['end'][:7]
    rows = [g for g in report['gaps'] if (not p['streams'] or g['stream'] in p['streams']) and (not p['kinds'] or g['kind'] in p['kinds'])
            and (not p['statuses'] or g['status'] in p['statuses']) and (not lo or g['month'] >= lo) and (not hi or g['month'] <= hi)]
    return {'parameters': p, 'gaps': rows, 'series': report['series'], 'periodic_series': report['periodic_series'],
            'source_digest': report['source_digest'], 'as_of': report['as_of'], 'detector_version': VERSION}


def answer(report, plan, question):
    p, rows = report['parameters'], report['gaps']
    span = f"{p['period']['start'] or 'first arrival'} to {p['period']['end'] or report['as_of']}"
    if p['output'] == 'by_stream':
        agg = {}
        for g in rows:
            a = agg.setdefault((g['stream'], g['organization_id']), {'stream': g['stream'], 'organization_id': g['organization_id'],
                                                                    'gap_months': [], 'unrecovered': 0, 'recovered': 0})
            a['gap_months'].append(g['month']); a['unrecovered' if g['status'] in ('unrecovered', 'reported_by_manifest') else 'recovered'] += 1
        table = sorted(agg.values(), key=lambda a: (-a['unrecovered'], a['stream']))
    else:
        table = [{k: g.get(k) for k in ('stream', 'organization_id', 'month', 'kind', 'arrived', 'baseline', 'status', 'recovered_in', 'shortfall', 'source')}
                 for g in rows]
    open_ = [g for g in rows if g['status'] in ('unrecovered', 'reported_by_manifest')]
    feeds = sorted({(g['stream'], g['organization_id']) for g in rows})
    head = (f"{len(rows)} incomplete feed-month(s) across {len(feeds)} feed(s) ({span}); {len(open_)} not recovered by a later delivery"
            + (f"; e.g. {rows[0]['stream']} from {rows[0]['organization_id'] or 'all senders'} {rows[0]['month']} ({rows[0]['kind'].replace('_', ' ')}, "
               f"{rows[0]['status'].replace('_', ' ')})" if rows else '') if rows else
            f"No incomplete feed months found ({span}) among {report['periodic_series']} periodic feed series.")
    method = (f"{VERSION}: each stream x sending organization's monthly arrival sequence (available_date) against its trailing 6-month "
              'baseline; missing = no arrival, partial = under 25% of baseline; recovery read from the period\'s later event-month volume '
              '(late / catch-up files) and from superseding snapshots (record_correction).')
    return {'status': 'answered', 'answer_type': 'table', 'table': table, 'count': len(rows), 'value': head, 'headline': head,
            'patient_ids': [], 'method': method, 'limitations': [method, 'A gap is judged against the feed\'s own history; '
                                                                  'a feed that legitimately stopped (contract end) also shows as a gap.'],
            'definitions': [{'term': 'incomplete feed month', 'basis': method}]}


# ------------------------------------------------------------------------------ exact local routing
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_WORDS = set('''a an the and or of in on at to for from by with our my we any there are is was were do does did have has which what
how many much show list find me all every each where that this these those get give tell be been data feed feeds source sources
received file files delivery deliveries records record our arrive arrived arriving come came incomplete partial partially missing
gaps gap months month periods period dropped drop drops volume short sequence late recovered recover recovery not never have has'''.split())
_GAP = re.compile(r'\b(arriv\w* (?:incomplete|partial\w*|late)|incomplete (?:feeds?|deliver\w*|files?|data)|partial (?:feeds?|deliver\w*|files?|loads?)|'
                  r'missing (?:months?|periods?|files?|deliver\w*)|(?:feed|data|delivery|sequence) gaps?|gaps? in (?:the |our )?(?:[a-z0-9_]+ ){0,2}?(?:data|feeds?|deliver\w*)|'
                  r'(?:volume|feed) drops?|dropped (?:volume|feeds?)|never arrived)\b', re.I)


def _period(text, as_of):
    """Time qualifier -> ({start, end}, remaining text). Unknown qualifiers stay in the text (and the question is not claimed)."""
    a = date.fromisoformat(str(as_of)[:10]) if as_of else date.today()
    m = re.search(r'\b(?:last|previous|prior) quarter\b', text, re.I)
    if m:
        q = (a.month - 1) // 3
        y, q = (a.year - 1, 3) if q == 0 else (a.year, q - 1)
        s = date(y, 3 * q + 1, 1); e = date(y, 3 * q + 3, calendar.monthrange(y, 3 * q + 3)[1])
        return {'start': s.isoformat(), 'end': e.isoformat()}, text[:m.start()] + text[m.end():]
    m = re.search(r'\b(?:last|previous|prior) month\b', text, re.I)
    if m:
        y, mo = (a.year - 1, 12) if a.month == 1 else (a.year, a.month - 1)
        return {'start': date(y, mo, 1).isoformat(), 'end': date(y, mo, calendar.monthrange(y, mo)[1]).isoformat()}, text[:m.start()] + text[m.end():]
    m = re.search(r'\b(?:last|previous|prior) year\b', text, re.I)
    if m:
        return {'start': f'{a.year - 1}-01-01', 'end': f'{a.year - 1}-12-31'}, text[:m.start()] + text[m.end():]
    m = re.search(r'\b(?:in the )?(?:last|past) (\d{1,2}|six|twelve|three) months\b', text, re.I)
    if m:
        k = {'six': 6, 'twelve': 12, 'three': 3}.get(m.group(1).lower()) or int(m.group(1))
        mo = a.month - k; y = a.year + (mo - 1) // 12; mo = (mo - 1) % 12 + 1
        return {'start': date(y, mo, 1).isoformat(), 'end': a.isoformat()}, text[:m.start()] + text[m.end():]
    m = re.search(r'\bQ([1-4]) (\d{4})\b', text, re.I)
    if m:
        q, y = int(m.group(1)), int(m.group(2))
        return {'start': date(y, 3 * q - 2, 1).isoformat(), 'end': date(y, 3 * q, calendar.monthrange(y, 3 * q)[1]).isoformat()}, text[:m.start()] + text[m.end():]
    m = re.search(r'\b(' + '|'.join(_MONTHS) + r') (\d{4})\b', text, re.I)
    if m:
        mo, y = _MONTHS[m.group(1).lower()], int(m.group(2))
        return {'start': date(y, mo, 1).isoformat(), 'end': date(y, mo, calendar.monthrange(y, mo)[1]).isoformat()}, text[:m.start()] + text[m.end():]
    m = re.search(r'\b(?:in|during) (\d{4})\b', text, re.I)
    if m:
        return {'start': f'{m.group(1)}-01-01', 'end': f'{m.group(1)}-12-31'}, text[:m.start()] + text[m.end():]
    return {'start': '', 'end': ''}, text


def local_question(question, as_of=None):
    text = ' '.join(str(question).replace('?', ' ').replace(',', ' ').split())
    m = _GAP.search(text)
    if not m: return None
    rest = text[:m.start()] + ' ' + text[m.end():]
    period, rest = _period(rest, as_of)
    streams = []
    for feed in sorted(FEEDS, key=len, reverse=True):
        rx = re.compile(r'\b' + re.escape(feed) + r'\b', re.I)
        if rx.search(rest) or rx.search(m.group(0)): streams += FEEDS[feed]; rest = rx.sub(' ', rest)
    statuses = []
    if re.search(r'\b(?:still|not|never|un)\s*recovered\b|\bstill missing\b', rest, re.I): statuses = ['unrecovered', 'reported_by_manifest']
    rest = re.sub(r'\b(?:still|not yet|never|un)\s*recovered\b|\bstill\b', ' ', rest, flags=re.I)
    leftover = [w for w in re.findall(r"[a-z0-9']+", rest.lower()) if w not in _WORDS]
    if leftover: return None
    by_stream = bool(re.search(r'\bwhich (?:sources?|feeds?)\b', text, re.I))
    from .foundation_engines import graph
    return graph(question, 'feed_gaps', {'streams': sorted(set(streams)), 'period': period, 'kinds': [], 'statuses': statuses,
                                         'output': 'by_stream' if by_stream else 'gaps'}, 'Feed sequence gaps and recovery (feed_gaps)')


# ------------------------------------------------------------------------------ answer annotation
_YEAR = re.compile(r'\b(20\d\d)(?:-(\d\d))?')


def periods_of(texts, as_of=None):
    """Months an answer reads, from dates / years in its SQL and question. None = no explicit period (whole history)."""
    months = set()
    for t in texts:
        for y, m in _YEAR.findall(t or ''):
            months |= {f'{y}-{m}'} if m else {f'{y}-{k:02d}' for k in range(1, 13)}
    return months or None


def annotate_tables(path, tables, texts, as_of=None, limit=3):
    report = cached(path)
    if not report or not tables: return []
    streams = {s for t in tables for s in TABLE_STREAMS.get(t, ())}
    if not streams: return []
    months = periods_of(texts, as_of)
    recent = None
    if months is None:  # no stated period: only unrecovered gaps of the last 12 months can bias a current answer
        a = str(report.get('as_of') or as_of or '')[:7]
        recent = {_month_add(a, -k) for k in range(0, 13)} if a else set()
    hits = [g for g in report['gaps'] if g['stream'] in streams and g['status'] in ('unrecovered', 'reported_by_manifest')
            and g['month'] in (months if months is not None else recent)]
    if not hits: return []
    desc = '; '.join(f"{g['stream']} from {g['organization_id'] or 'all senders'} {g['month']} ({g['kind'].replace('_', ' ')}, short ~{g['shortfall']} records)"
                     if g.get('shortfall') else f"{g['stream']} {g['month']} ({g['kind'].replace('_', ' ')})" for g in hits[:limit])
    return [f'Feed gap in a period this answer reads: {desc}' + (f' (+{len(hits) - limit} more)' if len(hits) > limit else '')
            + '; not recovered by a later delivery, so counts for that period may be low.']
