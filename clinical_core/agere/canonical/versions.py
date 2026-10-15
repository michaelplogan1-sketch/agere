"""Global current-version resolution for the raw warehouse (canonical/1).

Every canonical table (and the Tuva input layer built from the raw streams) must read only current record versions and
settle multi-version entities (claims, pharmacy claims, fills) the same way. This module is that single rule set:

1. Record level. A record is superseded when a later record names it through a version pointer:
   - `supersedes_record_id` (all streams; mirrored in records.supersedes);
   - `replaces_result_record_id` (labs: corrected / recalculated results);
   - `original_submission_record_id` (medical_claim_submissions: a corrected claim replaces the original submission);
   - `original_transaction_record_id` (pharmacy_claim_transactions: a reversal replaces the paid transaction);
   - claim_corrections: `replacement_adjudication_record_id` replaces `original_adjudication_record_id` (medical_claims).
   A record is current when nothing in the window supersedes it.
2. Entity level (one row per claim / fill). Versions are grouped by the entity key; the final version is the current
   version of the entity, ordered by (adjudication_sequence, arrival, event date, record_id) when more than one is
   current. Claim frequency 7 (replacement) supersedes the original under a NEW payer_claim_id: when every version of
   a claim is superseded by another claim's record, the claim's final status is `replaced` and `replaced_by` names
   the new claim. Frequency 8 (void) is a version of the same claim with status `voided`.
3. Money. `net_amount` is the final net: the final version's amount when it is paid; 0 for denied, denied_upheld,
   voided, reversed, rejected and replaced; for a recoupment, the nearest preceding paid amount on the supersedes
   chain minus the recovered amount (a recoupment with no paid version in the window is a negative net). Pharmacy
   transactions carry signed deltas, so their net is the sum of deltas over all versions.
4. Fills. A dispense fill_id is picked up when one of its records has status `dispensed` and none has
   `returned_to_stock`; `prepared`-only fills are not picked up.

`python -m clinical_core.agere.canonical.versions --source <raw records db> --out <versions db>` writes
`superseded_record` and `entity_version` tables plus a receipt, streaming one stream at a time.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import json
from pathlib import Path
import resource
import sqlite3
import time

from clinical_core.agere.canonical import VERSION

RULES = [
    ('record_current', 'A record is current when no record in the window supersedes it (supersedes_record_id, '
                       'replaces_result_record_id, original_submission_record_id, original_transaction_record_id, '
                       'claim_corrections replacement_adjudication_record_id).'),
    ('entity_final', 'Per entity key the final version is the current one; ties order by adjudication_sequence, '
                     'arrival, event date, record_id (rule multiple_current_latest).'),
    ('freq7_replacement', 'Claim frequency 7 supersedes the original record under a new claim id; a claim whose every '
                          'version is superseded by another claim is final_status=replaced with replaced_by set.'),
    ('freq8_void', 'Claim frequency 8 is a voiding version of the same claim (final_status=voided, net 0).'),
    ('net_amount', 'Final net: paid -> final amount; denied/denied_upheld/voided/reversed/rejected/replaced -> 0; '
                   'recouped -> preceding paid amount minus the recovery; transaction deltas -> sum.'),
    ('denied_then_paid', 'An entity whose version path holds a denial followed later by a paid final version.'),
    ('fill_picked_up', "A dispense fill_id is picked up when a record has status 'dispensed' and none has "
                       "'returned_to_stock'."),
    ('as_of', 'With --as-of, only records with arrival <= as_of take part, so supersessions arriving later are ignored.'),
]

# Payload fields that name an older version of a record in the same stream ('*' = every stream).
POINTER_FIELDS = {
    '*': ('supersedes_record_id',),
    'labs': ('replaces_result_record_id',),
    'medical_claim_submissions': ('original_submission_record_id',),
    'pharmacy_claim_transactions': ('original_transaction_record_id',),
}
# (stream carrying the correction, target stream, old-record field, new-record field)
CROSS_STREAM = (('claim_corrections', 'medical_claims', 'original_adjudication_record_id',
                 'replacement_adjudication_record_id'),)

ZERO_STATUSES = {'denied', 'denied_upheld', 'voided', 'reversed', 'rejected', 'replaced'}
RECOVERY_STATUSES = {'recouped'}


@dataclass
class Version:
    record_id: str
    replaces: tuple = ()
    arrival: str = ''
    event: str = ''
    key: str | None = None
    status: str | None = None
    sequence: int = 0
    frequency: str | None = None
    amount: float | None = None
    service_date: str | None = None
    person_id: str | None = None


@dataclass
class Settlement:
    key: str
    final_record_id: str | None
    final_status: str | None
    net_amount: float | None
    final_amount: float | None
    version_count: int
    status_path: str
    replaced_by: str | None = None
    rule: str = 'single_version'
    denied_then_paid: int = 0
    available_date: str | None = None
    service_date: str | None = None
    person_id: str | None = None
    picked_up: int | None = None
    flags: list = field(default_factory=list)


def order_key(v):
    return (v.sequence or 0, v.arrival or '', v.event or '', v.record_id)


def superseded_by(versions):
    """old record_id -> sorted list of record_ids that name it as an older version (pointers to unknown ids kept)."""
    out = defaultdict(list)
    for v in versions:
        for old in v.replaces:
            if old and old != v.record_id:
                out[old].append(v.record_id)
    return {k: sorted(set(x)) for k, x in out.items()}


def current_ids(versions, successors=None):
    successors = superseded_by(versions) if successors is None else successors
    return {v.record_id for v in versions if v.record_id not in successors}


def _net(final, by_id, mode):
    if mode == 'sum':
        return round(sum(v.amount or 0 for v in by_id.values()), 6)
    if final is None or final.status in ZERO_STATUSES:
        return 0.0
    if final.status in RECOVERY_STATUSES:
        recovered = -abs(final.amount or 0)
        base, seen, cur = 0.0, set(), final
        while cur is not None and cur.record_id not in seen:
            seen.add(cur.record_id)
            prior = next((by_id[r] for r in cur.replaces if r in by_id), None)
            if prior is None:
                break
            if prior.status == 'paid':
                base = prior.amount or 0
                break
            if prior.status in ZERO_STATUSES:
                break
            cur = prior
        return round(base + recovered, 6)
    return round(final.amount or 0, 6)


def settle(versions, *, net='final_head', key_of=None):
    """Settle versions (any order) into one Settlement per entity key. key_of maps record_id -> entity key for records
    outside `versions` (cross-entity replacements); records without a key form their own entity."""
    successors = superseded_by(versions)
    entities = defaultdict(list)
    keys = dict(key_of or {})
    for v in versions:
        k = v.key if v.key is not None else v.record_id
        entities[k].append(v)
        keys[v.record_id] = k
    out = []
    for k, group in entities.items():
        group.sort(key=order_key)
        by_id = {v.record_id: v for v in group}
        live = [v for v in group if v.record_id not in successors]
        flags = []
        replaced_by = None
        if live:
            final = live[-1]
            rule = 'single_version' if len(group) == 1 else 'version_chain'
            if len(live) > 1:
                rule = 'multiple_current_latest'
        else:
            final = None
            outside = sorted({keys.get(s, s) for v in group for s in successors.get(v.record_id, ())} - {k})
            rule = 'freq7_replacement' if outside else 'all_versions_superseded'
            replaced_by = ','.join(outside) or None
        statuses = [v.status or '' for v in group]
        status = final.status if final else 'replaced'
        if final is not None and final.frequency == '8':
            rule = 'freq8_void' if status == 'voided' else rule
        denied_then_paid = int(status == 'paid' and any(s in ('denied', 'denied_upheld') for s in statuses[:-1]))
        if final is not None and any(v.frequency == '7' for v in group) and rule == 'single_version':
            rule = 'freq7_replacement_claim'
        out.append(Settlement(
            key=k, final_record_id=final.record_id if final else None, final_status=status,
            net_amount=_net(final, by_id, net) if net else None,
            final_amount=final.amount if final else None, version_count=len(group), status_path='>'.join(statuses),
            replaced_by=replaced_by, rule=rule, denied_then_paid=denied_then_paid,
            available_date=max((v.arrival for v in group if v.arrival), default=None),
            service_date=(final or group[-1]).service_date, person_id=(final or group[-1]).person_id, flags=flags))
    return out


def settle_fills(versions):
    """Dispense records grouped by fill_id (record_id when absent) -> picked_up per the fill rule."""
    groups = defaultdict(list)
    for v in versions:
        groups[v.key if v.key is not None else v.record_id].append(v)
    out = []
    for k, group in groups.items():
        group.sort(key=order_key)
        statuses = {v.status for v in group}
        picked = int('dispensed' in statuses and 'returned_to_stock' not in statuses)
        final_status = 'returned_to_stock' if 'returned_to_stock' in statuses else (
            'dispensed' if 'dispensed' in statuses else (group[-1].status or 'unknown'))
        final = next((v for v in reversed(group) if v.status == final_status), group[-1])
        out.append(Settlement(
            key=k, final_record_id=final.record_id, final_status=final_status, net_amount=None, final_amount=None,
            version_count=len(group), status_path='>'.join(v.status or '' for v in group),
            rule='fill_picked_up' if picked else 'fill_not_picked_up', picked_up=picked,
            available_date=max((v.arrival for v in group if v.arrival), default=None),
            service_date=final.service_date, person_id=final.person_id))
    return out


# --- raw warehouse reader --------------------------------------------------------------------------------------------

# stream -> how its versions settle: payload fields for key/status/sequence/frequency/amount/service_date, net mode.
ENTITY_SPECS = {
    'payer_medical_claims': dict(key='payer_claim_id', status='claim_status', sequence='adjudication_sequence',
                                 frequency='claim_frequency_code', amount='paid_amount', service_date='service_from',
                                 net='final_head', unit='USD'),
    'payer_pharmacy_claims': dict(key='rx_claim_id', status='claim_status', amount='plan_paid',
                                  service_date='event_date', net='final_head', unit='USD'),
    'pharmacy_claim_transactions': dict(key='claim_id', status='transaction_status', amount='payer_paid_delta_cents',
                                        service_date='event_date', net='sum', unit='cents'),
    'medical_claim_submissions': dict(key='claim_id', status='submission_kind', amount='billed_cents',
                                      service_date='service_date', net='final_head', unit='cents'),
    'pharmacy_dispenses': dict(key='fill_id', status='status', service_date='event_date', fill=True),
}


def connect_raw(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.execute('PRAGMA query_only=ON')
    return db


def release_of(db, release=None):
    if release:
        return release
    rows = [r[0] for r in db.execute('SELECT id FROM releases')]
    if len(rows) != 1:
        raise ValueError(f'Source holds {len(rows)} releases; pass --release')
    return rows[0]


def stream_versions(db, release, stream, spec=None, as_of=None):
    """Yield Version objects for one stream, extracting only the needed payload fields in SQL (no full payload load)."""
    spec = spec or {}
    pointer_fields = POINTER_FIELDS['*'] + POINTER_FIELDS.get(stream, ())
    fields = [spec.get(k) for k in ('key', 'status', 'sequence', 'frequency', 'amount', 'service_date')]
    extract = ', '.join(f"json_extract(payload, '$.{f}')" if f else 'NULL' for f in fields + list(pointer_fields[1:]))
    sql = (f'SELECT id, patient, arrival, event, supersedes, {extract} FROM records '
           'WHERE stream = ? AND release = ?' + (' AND arrival <= ?' if as_of else ''))
    params = (stream, release) + ((as_of,) if as_of else ())
    for row in db.execute(sql, params):
        rid, patient, arrival, event, sup = row[:5]
        key, status, seq, freq, amount, service = row[5:11]
        extra = row[11:]
        yield Version(record_id=rid, replaces=tuple(x for x in (sup, *extra) if x), arrival=arrival or '',
                      event=event or '', key=key, status=status, sequence=int(seq or 0),
                      frequency=None if freq is None else str(freq), amount=amount, service_date=service,
                      person_id=patient)


SCHEMA = '''
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE superseded_record(record_id TEXT, stream TEXT, superseded_by TEXT, rule TEXT, superseded_by_arrival TEXT);
CREATE TABLE entity_version(stream TEXT, entity_key TEXT, person_id TEXT, final_record_id TEXT, final_status TEXT,
  net_amount REAL, final_amount REAL, amount_unit TEXT, version_count INTEGER, status_path TEXT, replaced_by TEXT,
  rule TEXT, denied_then_paid INTEGER, picked_up INTEGER, service_date TEXT, available_date TEXT,
  PRIMARY KEY(stream, entity_key));
'''

TABLE_DOCS = {
    'superseded_record': {
        'description': 'Every raw record that a later record replaces. A raw record is CURRENT iff its record_id is '
                       'absent here. One row per (record, successor).',
        'columns': {'record_id': 'raw record id that is no longer current', 'stream': 'raw stream of that record',
                    'superseded_by': 'record id of the newer version', 'rule': 'pointer field that names it',
                    'superseded_by_arrival': 'arrival date of the newer version'}},
    'entity_version': {
        'description': 'One row per multi-version entity (payer medical claim, payer pharmacy claim, pharmacy '
                       'transaction claim, provider claim submission, dispense fill) with its settled final status.',
        'columns': {'stream': 'raw stream', 'entity_key': 'payer_claim_id / rx_claim_id / claim_id / fill_id',
                    'person_id': 'warehouse patient id', 'final_record_id': 'record id of the final version',
                    'final_status': 'settled final status (see rules)', 'net_amount': 'final net amount (amount_unit)',
                    'final_amount': 'amount on the final version as received', 'amount_unit': 'USD or cents',
                    'version_count': 'versions received', 'status_path': 'statuses in version order, > separated',
                    'replaced_by': 'entity key of the frequency-7 replacement', 'rule': 'settlement rule applied',
                    'denied_then_paid': '1 when a denial was later paid', 'picked_up': 'fills: 1 when picked up',
                    'service_date': 'service_from (medical), fill/event date (pharmacy), service_date (provider)',
                    'available_date': 'latest arrival among the versions'}},
    'meta': {'description': 'Build metadata (version, release, as_of, source).', 'columns': {}},
}


def build(source, out, *, release=None, as_of=None, receipt_path=None):
    t0 = time.perf_counter()
    db = connect_raw(source)
    release = release_of(db, release)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix('.tmp')
    tmp.unlink(missing_ok=True)
    w = sqlite3.connect(tmp)
    w.executescript(SCHEMA)
    timings, audit = {}, Counter()

    # 1. Record-level supersessions. The records.supersedes column is indexed (release, supersedes).
    t = time.perf_counter()
    sql = ('SELECT supersedes, stream, id, arrival FROM records WHERE release = ? AND supersedes IS NOT NULL'
           + (' AND arrival <= ?' if as_of else ''))
    rows = [(old, stream, new, 'supersedes_record_id', arr) for old, stream, new, arr in
            db.execute(sql, (release, as_of) if as_of else (release,))]
    for stream, fields in POINTER_FIELDS.items():
        if stream == '*':
            continue
        for f in fields:
            q = (f"SELECT json_extract(payload, '$.{f}'), id, arrival FROM records WHERE stream = ? AND release = ? "
                 f"AND json_extract(payload, '$.{f}') IS NOT NULL" + (' AND arrival <= ?' if as_of else ''))
            rows += [(old, stream, new, f, arr) for old, new, arr in
                     db.execute(q, (stream, release) + ((as_of,) if as_of else ()))]
    for carrier, target, old_f, new_f in CROSS_STREAM:
        q = (f"SELECT json_extract(payload, '$.{old_f}'), json_extract(payload, '$.{new_f}'), arrival FROM records "
             f"WHERE stream = ? AND release = ? AND json_extract(payload, '$.{new_f}') IS NOT NULL"
             + (' AND arrival <= ?' if as_of else ''))
        rows += [(old, target, new, f'{carrier}.{new_f}', arr) for old, new, arr in
                 db.execute(q, (carrier, release) + ((as_of,) if as_of else ())) if old]
    w.executemany('INSERT INTO superseded_record VALUES (?,?,?,?,?)', rows)
    for r in rows:
        audit[f'superseded:{r[1]}:{r[3]}'] += 1
    del rows
    timings['superseded_record'] = round(time.perf_counter() - t, 2)

    # 2. Entity settlement, one stream at a time.
    for stream, spec in ENTITY_SPECS.items():
        t = time.perf_counter()
        versions = list(stream_versions(db, release, stream, spec, as_of))
        settled = settle_fills(versions) if spec.get('fill') else settle(versions, net=spec.get('net'))
        w.executemany('INSERT INTO entity_version VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', [
            (stream, s.key, s.person_id, s.final_record_id, s.final_status, s.net_amount, s.final_amount,
             spec.get('unit'), s.version_count, s.status_path, s.replaced_by, s.rule, s.denied_then_paid,
             s.picked_up, s.service_date, s.available_date) for s in settled])
        for s in settled:
            audit[f'entity:{stream}:{s.rule}'] += 1
        w.commit()
        timings[stream] = round(time.perf_counter() - t, 2)
        del versions, settled

    w.execute('CREATE INDEX ix_superseded ON superseded_record(record_id)')
    w.execute('CREATE INDEX ix_entity_status ON entity_version(stream, final_status)')
    t = time.perf_counter()
    checks = reconcile(db, w, release, as_of)
    timings['reconciliation'] = round(time.perf_counter() - t, 2)
    counts = {name: w.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0] for name in ('superseded_record', 'entity_version')}
    counts.update({f'entity_version:{s}': n for s, n in w.execute(
        'SELECT stream, COUNT(*) FROM entity_version GROUP BY stream')})
    status_counts = {f'{s}:{st}': n for s, st, n in w.execute(
        'SELECT stream, final_status, COUNT(*) FROM entity_version GROUP BY 1, 2 ORDER BY 1, 2')}
    timings['total'] = round(time.perf_counter() - t0, 2)
    receipt = {'version': VERSION, 'module': 'versions', 'release': release, 'as_of': as_of, 'source': str(source),
               'row_counts': counts, 'final_status_counts': status_counts, 'rule_audit': dict(sorted(audit.items())),
               'reconciliation': checks, 'reconciled': all(c['match'] for c in checks if 'match' in c),
               'timings_s': timings, 'peak_rss_mb': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20, 1)}
    meta = {'version': VERSION, 'release': release, 'as_of': as_of or _release_as_of(db, release), 'source': str(source),
            'built_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'receipt': json.dumps(receipt)}
    w.executemany('INSERT INTO meta VALUES (?,?)', meta.items())
    w.commit()
    w.close()
    db.close()
    tmp.replace(out)
    if receipt_path:
        Path(receipt_path).write_text(json.dumps(receipt, indent=1) + '\n')
    return receipt


def _release_as_of(db, release):
    try:
        return json.loads(db.execute('SELECT manifest FROM releases WHERE id = ?', (release,)).fetchone()[0]).get('as_of')
    except (TypeError, ValueError):
        return None


def reconcile(raw, out, release, as_of=None):
    """Independent SQL over the raw records vs the settled entity_version rows. Mismatches are reported, never forced."""
    cut = ' AND {a}.arrival <= :as_of' if as_of else ''
    p = {'rel': release, 'as_of': as_of}
    not_superseded = ('NOT EXISTS (SELECT 1 FROM records s WHERE s.release = r.release AND s.supersedes = r.id'
                      + cut.format(a='s') + ')')
    checks = []

    def add(name, raw_value, canonical_value, note=''):
        match = (abs(raw_value - canonical_value) < 0.005) if isinstance(raw_value, float) else raw_value == canonical_value
        checks.append({'check': name, 'raw': raw_value, 'canonical': canonical_value, 'match': bool(match),
                       **({'note': note} if note else {})})

    raw_paid = raw.execute(
        "SELECT ROUND(COALESCE(SUM(json_extract(r.payload, '$.paid_amount')), 0), 2) FROM records r "
        "WHERE r.release = :rel AND r.stream = 'payer_medical_claims' "
        "AND json_extract(r.payload, '$.claim_status') = 'paid' "
        "AND substr(json_extract(r.payload, '$.service_from'), 1, 4) = '2029' AND " + not_superseded + cut.format(a='r'),
        p).fetchone()[0]
    can_paid = out.execute("SELECT ROUND(COALESCE(SUM(net_amount), 0), 2) FROM entity_version WHERE stream = "
                           "'payer_medical_claims' AND final_status = 'paid' AND substr(service_date, 1, 4) = '2029'").fetchone()[0]
    add('payer_medical_paid_2029_final_paid_status', float(raw_paid), float(can_paid))
    can_net = out.execute("SELECT ROUND(COALESCE(SUM(net_amount), 0), 2) FROM entity_version WHERE stream = "
                          "'payer_medical_claims' AND substr(service_date, 1, 4) = '2029'").fetchone()[0]
    checks.append({'check': 'payer_medical_net_2029_all_statuses', 'canonical': float(can_net),
                   'note': 'final net including recoupments (informational; no raw single-query equivalent)'})

    for stream, key, status in (('payer_medical_claims', 'payer_claim_id', 'claim_status'),
                                ('payer_pharmacy_claims', 'rx_claim_id', 'claim_status')):
        distinct = raw.execute(f"SELECT COUNT(DISTINCT json_extract(payload, '$.{key}')) FROM records r "
                               f"WHERE r.release = :rel AND r.stream = '{stream}'" + cut.format(a='r'), p).fetchone()[0]
        add(f'{stream}_distinct_claims', distinct,
            out.execute('SELECT COUNT(*) FROM entity_version WHERE stream = ?', (stream,)).fetchone()[0])
        heads = dict(raw.execute(
            f"SELECT json_extract(r.payload, '$.{status}'), COUNT(DISTINCT json_extract(r.payload, '$.{key}')) "
            f"FROM records r WHERE r.release = :rel AND r.stream = '{stream}' AND " + not_superseded + cut.format(a='r')
            + ' GROUP BY 1', p).fetchall())
        head_claims = raw.execute(
            f"SELECT COUNT(DISTINCT json_extract(r.payload, '$.{key}')) FROM records r WHERE r.release = :rel AND "
            f"r.stream = '{stream}' AND " + not_superseded + cut.format(a='r'), p).fetchone()[0]
        if distinct - head_claims:
            heads['replaced'] = distinct - head_claims
        settled = dict(out.execute('SELECT final_status, COUNT(*) FROM entity_version WHERE stream = ? GROUP BY 1',
                                   (stream,)).fetchall())
        for st in sorted(set(heads) | set(settled)):
            add(f'{stream}_final_status:{st}', heads.get(st, 0), settled.get(st, 0),
                'raw = distinct claims with a non-superseded record in that status' if st != 'replaced'
                else 'raw = claims with no non-superseded record')

    # Data finding: a secondary-payer replacement (<base>-R-X, frequency 7) whose supersedes pointer names the PRIMARY
    # payer's original instead of the secondary copy (<base>-X). The secondary original then stays current, so both the
    # original and the replacement count as paid. Reported, not corrected.
    row = raw.execute(
        "SELECT COUNT(*), ROUND(COALESCE(SUM(json_extract(r.payload, '$.paid_amount')), 0), 2), "
        "ROUND(COALESCE(SUM(CASE WHEN substr(json_extract(r.payload, '$.service_from'), 1, 4) = '2029' "
        "THEN json_extract(r.payload, '$.paid_amount') END), 0), 2) "
        "FROM records r JOIN records o ON o.release = r.release AND o.id = r.supersedes "
        "WHERE r.release = :rel AND r.stream = 'payer_medical_claims' "
        "AND json_extract(r.payload, '$.claim_frequency_code') = '7' "
        "AND json_extract(o.payload, '$.payer_id') != json_extract(r.payload, '$.payer_id')" + cut.format(a='r'),
        p).fetchone()
    checks.append({'check': 'payer_medical_freq7_cross_payer_pointer', 'replacements': row[0],
                   'replacement_paid': row[1], 'replacement_paid_2029': row[2],
                   'note': 'frequency-7 replacements whose supersedes pointer names another payer\'s record; the '
                           'same-payer original stays current, so its paid amount is also counted (raw and canonical '
                           'agree; flagged for the source feed)'})
    picked = raw.execute(
        "SELECT COUNT(*) FROM (SELECT COALESCE(json_extract(payload, '$.fill_id'), id) f, "
        "MAX(json_extract(payload, '$.status') = 'dispensed') d, MAX(json_extract(payload, '$.status') = 'returned_to_stock') x "
        "FROM records r WHERE r.release = :rel AND r.stream = 'pharmacy_dispenses'" + cut.format(a='r') +
        ' GROUP BY 1) WHERE d = 1 AND x = 0', p).fetchone()[0]
    add('pharmacy_dispenses_picked_up_fills', picked, out.execute(
        "SELECT COUNT(*) FROM entity_version WHERE stream = 'pharmacy_dispenses' AND picked_up = 1").fetchone()[0])
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--source', required=True, type=Path, help='raw records SQLite (records/releases tables)')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--release')
    parser.add_argument('--as-of', help='only records with arrival <= this date (YYYY-MM-DD)')
    parser.add_argument('--receipt', type=Path, help='also write the receipt JSON here (default <out>.receipt.json)')
    args = parser.parse_args(argv)
    receipt = build(args.source, args.out, release=args.release, as_of=args.as_of,
                    receipt_path=args.receipt or args.out.with_suffix('.receipt.json'))
    print(json.dumps({k: receipt[k] for k in ('row_counts', 'reconciled', 'timings_s', 'peak_rss_mb')}, indent=1))
    for c in receipt['reconciliation']:
        if not c.get('match', True):
            print('MISMATCH', json.dumps(c))
    return 0 if receipt['reconciled'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
