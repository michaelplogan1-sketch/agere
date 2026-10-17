"""Canonical picked-up fills with class windows (docs/agere/CANONICAL_MODEL.md, picked-up fills; build log step 11).

One row per fill the patient actually received: payer pharmacy claims that count as fills (paid, not reversed or
recouped), pharmacy dispenses that were picked up (not returned to stock) and owned-pharmacy claims. A dispense and its
payer claim for the same person, class, ingredient(s) and fill date (+/- 1 day) are one fill seen twice; two drugs of one
class filled the same day (tramadol and oxycodone) stay two fills.

Per person and class (drug_class, else RxNorm ingredient, else drug name) the pass precomputes the window facts every
pharmacy and RWE question re-derives: sequence, days since the previous fill, the gap after the previous supply ended,
days of data before the fill, and new-user status under the standard 180-day washout. Thresholds that questions vary
(90 vs 180 days) stay computable from the stored day counts.
"""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3

VERSION = 'fill-events/2'  # /2: merging requires the same ingredient(s), not only the same class
SAME_FILL_DAYS = 1
WASHOUT_DAYS = 180

DDL = """
CREATE TABLE fill(fill_id INTEGER PRIMARY KEY, patient_id TEXT, fill_date TEXT, days_supply INTEGER, supply_end TEXT,
  class_key TEXT, drug_class TEXT, therapeutic_category TEXT, rxnorm_ingredient TEXT, drug_name TEXT, sources TEXT,
  record_ids TEXT, class_fill_seq INTEGER, days_since_prev_class_fill INTEGER, gap_after_prev_supply INTEGER,
  days_of_data_before INTEGER, new_user_180 INTEGER, last_class_fill INTEGER);
CREATE INDEX fill_patient_class ON fill(patient_id, class_key, fill_date);
CREATE INDEX fill_class_date ON fill(class_key, fill_date);
"""


def _d(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def ingredient_key(name, ingredients_col=None, rxnorm_ingredient=None):
    """The active ingredient(s) a fill row names, comparable across sources whose names differ in case, salt and brand
    ('Oxycontin' and 'oxycodone hydrochloride 10 MG' -> 'oxycodone'). Falls back to the delivered ingredient columns, then
    to the first word of the name."""
    from .drug_classes import generic_name, ingredients
    found = ingredients(name) or generic_name(name)
    if not found and ingredients_col:
        found = [x.strip().lower() for x in str(ingredients_col).split(',') if x.strip()]
    if not found and rxnorm_ingredient:
        return f'rxnorm:{rxnorm_ingredient}'
    if not found:
        return f"name:{(name or '').lower().split(' ')[0]}"
    return '+'.join(sorted(set(found)))


def build(out):
    supply = dict(out.execute('SELECT record_id, days_supply FROM payer_pharmacy_claim'))
    payload_supply = {}
    for rid, p in out.execute("SELECT record_id, payload FROM record WHERE stream IN ('pharmacy_dispenses', 'pharmacy_claims')"):
        try:
            payload_supply[rid] = json.loads(p).get('days_supply_reported')
        except ValueError:
            pass
    raw = []
    ing_keys = {}
    for rid, pid, src, d, cls, cat, ing, name, ings in out.execute(
            "SELECT record_id, patient_id, source_stream, event_date, drug_class, therapeutic_category, rxnorm_ingredient, "
            "drug_name, ingredients FROM medication WHERE picked_up = 1 AND source_stream IN "
            "('payer_pharmacy_claims', 'pharmacy_dispenses', 'pharmacy_claims')"):
        fd = _d(d)
        if fd is None:
            continue
        ds = _int(supply.get(rid) if src == 'payer_pharmacy_claims' else payload_supply.get(rid))
        key = cls or (f'ingredient:{ing}' if ing else f"name:{(name or '').lower().split(' ')[0]}")
        ik = ing_keys.get((name, ings, ing))
        if ik is None:
            ik = ing_keys[(name, ings, ing)] = ingredient_key(name, ings, ing)
        raw.append((pid, key, fd, ds, cls, cat, ing, name, {'payer_pharmacy_claims': 'payer', 'pharmacy_dispenses': 'dispense',
                                                             'pharmacy_claims': 'pharmacy_claim'}[src], rid, ik))
    data_start = min((r[2] for r in raw), default=None)
    # Merge within person, class and ingredient(s); then order by class and date for the window facts.
    raw.sort(key=lambda r: (r[0], r[1], r[10], r[2], r[8]))
    fills = []  # merged fills
    for r in raw:
        last = fills[-1] if fills else None
        if (last and last['pid'] == r[0] and last['key'] == r[1] and last['ing_key'] == r[10]
                and (r[2] - last['date']).days <= SAME_FILL_DAYS and r[8] not in last['sources']):
            last['sources'].append(r[8]); last['rids'].append(r[9])
            last['days'] = last['days'] or r[3]
            continue
        fills.append({'pid': r[0], 'key': r[1], 'ing_key': r[10], 'date': r[2], 'days': r[3], 'cls': r[4], 'cat': r[5],
                      'ing': r[6], 'name': r[7], 'sources': [r[8]], 'rids': [r[9]]})
    fills.sort(key=lambda f: (f['pid'], f['key'], f['date'], f['ing_key'], f['rids'][0]))
    rows = []
    prev = None
    for i, f in enumerate(fills):
        same = prev is not None and prev['pid'] == f['pid'] and prev['key'] == f['key']
        f['seq'] = prev['seq'] + 1 if same else 1
        since = (f['date'] - prev['date']).days if same else None
        prev_end = prev['date'] + timedelta(days=max((prev['days'] or 1) - 1, 0)) if same else None
        gap = (f['date'] - prev_end).days - 1 if same else None
        before = (f['date'] - data_start).days if data_start else None
        new_user = int((not same or since > WASHOUT_DAYS) and before is not None and before >= WASHOUT_DAYS)
        nxt = fills[i + 1] if i + 1 < len(fills) else None
        last_of_class = int(not (nxt and nxt['pid'] == f['pid'] and nxt['key'] == f['key']))
        end = f['date'] + timedelta(days=max((f['days'] or 1) - 1, 0))
        rows.append((i + 1, f['pid'], f['date'].isoformat(), f['days'], end.isoformat(), f['key'], f['cls'], f['cat'], f['ing'],
                     f['name'], ','.join(sorted(f['sources'])), ','.join(f['rids']), f['seq'], since, gap, before, new_user,
                     last_of_class))
        prev = f
    out.execute('DROP TABLE IF EXISTS fill')
    out.executescript(DDL)
    out.executemany('INSERT INTO fill VALUES (' + ','.join('?' * 18) + ')', rows)
    return {'version': VERSION, 'fills': len(rows), 'source_rows': len(raw), 'merged': len(raw) - len(rows),
            'data_start': data_start.isoformat() if data_start else None}


def apply(semantic_path):
    out = sqlite3.connect(Path(semantic_path))
    try:
        summary = build(out)
        out.execute("DELETE FROM meta WHERE key = 'fill_events'")
        out.execute("INSERT INTO meta VALUES ('fill_events', ?)", (json.dumps(summary),))
        out.commit()
        return summary
    finally:
        out.close()


def reclassify(out):
    """Give unclassified medication rows the classes the current drug_classes vocabulary assigns (one row per class, as
    the semantic build does) and refresh drug_class_vocabulary. Build log step 13."""
    from .drug_classes import CLASSES, GROUPS, classify
    cols = [r[1] for r in out.execute('PRAGMA table_info(medication)')]
    ci = cols.index('drug_class')
    rows = out.execute('SELECT rowid, * FROM medication WHERE drug_class IS NULL').fetchall()
    updates, inserts = [], []
    for row in rows:
        rowid, vals = row[0], list(row[1:])
        classes = classify(vals[cols.index('drug_name')])
        if not classes:
            continue
        updates.append((classes[0], rowid))
        for c in classes[1:]:
            extra = list(vals); extra[ci] = c; inserts.append(extra)
    out.executemany('UPDATE medication SET drug_class = ? WHERE rowid = ?', updates)
    out.executemany(f"INSERT INTO medication VALUES ({','.join('?' * len(cols))})", inserts)
    out.execute('DROP TABLE IF EXISTS drug_class_vocabulary')
    out.execute('CREATE TABLE drug_class_vocabulary(drug_class TEXT, class_group TEXT)')
    voc = [(c, g) for g, members in GROUPS.items() for c in members]
    voc += [(c, None) for c in CLASSES if not any(c in m for m in GROUPS.values())]
    out.executemany('INSERT INTO drug_class_vocabulary VALUES (?,?)', sorted(voc, key=lambda r: (r[0], r[1] or '')))
    return {'unclassified_rows_seen': len(rows), 'classified_now': len(updates), 'extra_class_rows': len(inserts)}


def apply_reclassify(semantic_path):
    out = sqlite3.connect(Path(semantic_path))
    try:
        summary = reclassify(out)
        summary['fill'] = build(out)
        out.execute("DELETE FROM meta WHERE key = 'drug_reclassify'")
        out.execute("INSERT INTO meta VALUES ('drug_reclassify', ?)", (json.dumps(summary),))
        out.commit()
        return summary
    finally:
        out.close()
