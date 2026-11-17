"""Stale-feed marking and feed recovery for saved answers (candidate, off by default: AGERE_TIERED_FRESHNESS=off).

The problem: a claim reversal, a late or corrected lab or a retro-termination arrives after a board number was computed,
and the saved number is now wrong with nothing on it to say so; a feed that stops arriving goes unnoticed because nothing
changes. The streaming layer (clinical_core/agere/streaming) already keeps an answer dependency registry: each answer
records the tables it read, a delivery's exact per-table row diff is recorded as a tick, and replay.mark_stale() marks every
answer that read a changed table. It was never called for the tiered core's answers. Two pieces here:

  register(answer_id, question, sql, ws)   put a saved answer into the registry (kind 'sql': re-run by code when stale)
  feed_lag(db, days=LAG_DAYS)              the gap check the registry cannot make (a feed that did not arrive changes no
                                           row): each feed's latest event date against the warehouse's latest; a feed more
                                           than LAG_DAYS behind is reported, and answers reading it are marked as resting
                                           on an incomplete feed.

    python -m clinical_core.agere.tiered.freshness trial     the frozen trial (eval/tiered/trial_stale_v1.json)
"""
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from . import store

LAG_DAYS = 31
EVENT_DATE = {   # feeds with an event date; appointments are left out (scheduled dates run into the future)'encounter': 'event_date', 'utilization_event': 'event_date', 'measurement': 'event_date', 'claim': 'service_date',
              'payer_claim': 'service_date', 'prescription_fill': 'fill_date', 'fill': 'fill_date', 'visit': 'event_date', 'stay': 'admit_date',
              'condition': 'event_date', 'clinical_note': 'event_date'}


def enabled():
    return os.environ.get('AGERE_TIERED_FRESHNESS', 'off') == 'on'


def register(answer_id, question, sql, ws, as_of=None):
    from ..streaming import replay
    replay.register(ws, answer_id, question, kind='sql', semantic_path=str(store.path().resolve()), as_of=as_of or '', query=sql, as_of_mode='pinned')


def feed_lag(db, days=LAG_DAYS):
    """-> {table: days behind the latest feed}, for feeds more than `days` behind."""
    latest = {}
    for t, col in EVENT_DATE.items():
        try:
            if col not in {r[1] for r in db.execute(f'PRAGMA table_info("{t}")')}: continue
            v = db.execute(f'SELECT max(substr("{col}", 1, 10)) FROM "{t}"').fetchone()[0]
        except sqlite3.Error:
            continue
        if v: latest[t] = v
    if not latest: return {}
    from datetime import date
    top = max(date.fromisoformat(v) for v in latest.values())
    return {t: (top - date.fromisoformat(v)).days for t, v in latest.items() if (top - date.fromisoformat(v)).days > days}


# ---- the trial ----------------------------------------------------------------------------------------------------------
# Each delivery is applied to TEMP copies of the tables it touches (the warehouse file stays read-only). Derived tables are
# not rebuilt (a reversal in claim does not flow into utilization_event here); the truth is computed on the same simulated
# data, so every arm faces the same facts.

DELIVERIES = {
    'claim_reversal': {'table': 'claim', 'sql': ["UPDATE temp.claim SET final_status = 'reversed', net_payer_paid_cents = 0, live_payer_paid_cents = 0, "
                                                 "first_reversal_date = '2030-05-20' WHERE final_status = 'paid' AND substr(service_date, 1, 4) = '2029' AND rowid % 200 = 3"],
                       'said': '0.5% of 2029 paid claims were reversed (status reversed, paid amount zero); row count unchanged'},
    'late_lab': {'table': 'measurement', 'sql': ["INSERT INTO temp.measurement SELECT * FROM main.measurement WHERE substr(event_date, 1, 4) = '2029' AND rowid % 300 = 5"],
                 'said': 'lab and vital results dated in 2029 arrived late (copies of 1 in 300 of the 2029 results); row count grows'},
    'corrected_lab': {'table': 'measurement', 'sql': ["UPDATE temp.measurement SET value_canonical = value_canonical * 1.1, value = value * 1.1, result_status = 'corrected' "
                                                      "WHERE substr(event_date, 1, 4) = '2029' AND value_canonical IS NOT NULL AND rowid % 150 = 9"],
                      'said': 'corrected lab results: 1 in 150 of the 2029 results replaced by a value 10% higher; row count unchanged'},
    'retro_termination': {'table': 'enrollment_span', 'sql': ["UPDATE temp.enrollment_span SET coverage_end = '2029-06-30', termination_reason = 'retro' "
                                                              "WHERE coverage_end >= '2029-07-01' AND coverage_start <= '2029-06-30' AND rowid % 40 = 1"],
                          'said': '1 in 40 enrollment spans running past mid-2029 were terminated retroactively at 2029-06-30; row count unchanged'},
    'identical_redelivery': {'table': 'claim', 'sql': [],
                             'said': 'the 2029 claims file was delivered again with identical rows; nothing changed'},
}
# The feed that stopped: the warehouse as delivered lacks the EHR encounter feed after 2030-03-31; the truth is the full feed.
STOPPED = {'table': 'encounter', 'cut': '2030-04-01', 'said': 'the EHR encounter feed has delivered nothing dated after 2030-03-31; every other feed continues to 2030-05'}


def _shadow(db, table, statements):
    db.execute('PRAGMA query_only=OFF')
    db.execute(f'CREATE TEMP TABLE "{table}" AS SELECT * FROM main."{table}"')
    for s in statements: db.execute(s)
    db.execute('PRAGMA query_only=ON')


def _diff(db, table):
    """Exact row diff between TEMP and main copies (what the streaming splice measures)."""
    return db.execute(f'SELECT (SELECT count(*) FROM (SELECT * FROM temp."{table}" EXCEPT SELECT * FROM main."{table}")) + '
                      f'(SELECT count(*) FROM (SELECT * FROM main."{table}" EXCEPT SELECT * FROM temp."{table}"))').fetchone()[0]


def _answers(path='eval/tiered/trial_stale_v1.json'):
    from . import bottles as bottle_store
    frozen = json.loads(Path(path).read_text())
    b = bottle_store.Bottles()
    out = []
    for item in frozen['answers']:
        sql, params = b.db.execute('SELECT sql, params_json FROM bottle WHERE id = ?', (item['bottle'],)).fetchone()
        question, values = b.db.execute('SELECT question, params_json FROM bottle_event WHERE id = ?', (item['check_event'],)).fetchone()
        out.append({**item, 'sql': sql, 'values': json.loads(values or '{}'), 'question': question})
    return frozen, out


def trial(path='eval/tiered/trial_stale_v1.json', out_dir='artifacts/agere-tiered-core/trials'):
    """Code only. Journals one line per (delivery, answer) as scored; a rerun skips lines already written."""
    from ..streaming import replay, ticks
    from . import upkeep
    frozen, answers = _answers(path)
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    journal = out / 'stale-journal.jsonl'
    done = {(r['delivery'], r['bottle']) for r in map(json.loads, journal.read_text().splitlines())} if journal.exists() else set()
    ws = out / 'stale-ws'
    shutil.rmtree(ws, ignore_errors=True); ws.mkdir(parents=True)
    names = None
    base_db = store.connect()
    names = {r[0] for r in base_db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    base, read = {}, {}
    for a in answers:
        read[a['bottle']] = set(replay.tables_read(a['sql'], str(store.path().resolve())))
        try: base[a['bottle']] = store.run(a['sql'], a['values'], base_db)[1]
        except store.RecipeError as exc: base[a['bottle']] = ('error', str(exc)[:80])
        register(f"B{a['bottle']}", a['question'], a['sql'], ws)
    counts = {t: base_db.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in ('claim', 'measurement', 'enrollment_span', 'encounter')}
    base_db.close()
    tick = 0
    scenarios = list(DELIVERIES.items()) + [('feed_stopped', STOPPED)]
    for name, d in scenarios:
        tick += 1
        db = store.connect()
        t0 = time.monotonic()
        if name == 'feed_stopped':
            # As delivered: the feed lacks its last weeks. Truth: the full feed (the base result). Answers are computed on the
            # delivered data, so an answer is wrong when the delivered result differs from the full one.
            _shadow(db, d['table'], [f"DELETE FROM temp.\"{d['table']}\" WHERE event_date >= '{d['cut']}'"])
            lagging = feed_lag(db)
            changed_tables, count_changed = set(), set()      # nothing arrived: no row changed since the last delivery
        else:
            _shadow(db, d['table'], d['sql'])
            rows = _diff(db, d['table'])
            changed_tables = {d['table']} if rows else set()
            n = db.execute(f"SELECT count(*) FROM temp.\"{d['table']}\"").fetchone()[0]
            count_changed = {d['table']} if n != counts[d['table']] else set()
            lagging = {}
        # The candidate: a tick with the exact table diff, then the registry's own stale marking.
        rdb = replay.connect(ws)
        with rdb:
            rdb.execute('INSERT INTO ticks(tick_id, started, as_of_before, as_of) VALUES (?, ?, ?, ?)', (tick, time.strftime('%Y-%m-%d'), 'x', 'x'))
            for t in changed_tables: rdb.execute('INSERT INTO tick_table VALUES (?, ?, 1, 0, 0, 0)', (tick, t))
            rdb.execute("UPDATE answers SET status = 'fresh', stale_reason = NULL")
        rdb.close()
        stale = {s['answer_id'] for s in replay.mark_stale(ws, tick)}
        for a in answers:
            if (name, a['bottle']) in done: continue
            tables = read[a['bottle']]
            try: now = store.run(a['sql'], a['values'], db)[1]
            except store.RecipeError as exc: now = ('error', str(exc)[:80])
            changed = not (isinstance(now, list) and isinstance(base[a['bottle']], list) and upkeep._same(now, base[a['bottle']], loose=False))
            rec = {'delivery': name, 'bottle': a['bottle'], 'tables': sorted(tables), 'answer_changed': changed,
                   'candidate_flag': f"B{a['bottle']}" in stale or bool(tables & set(lagging)),
                   'rowcount_flag': bool(tables & count_changed), 'nightly_flag': False,
                   'lagging_feeds': lagging or None}
            with open(journal, 'a') as f: f.write(json.dumps(rec) + '\n')
        db.close()
        print(name, round(time.monotonic() - t0, 1), 's', flush=True)
    return report(journal)


def report(journal):
    from collections import defaultdict
    rs = [json.loads(l) for l in Path(journal).read_text().splitlines() if l.strip()]
    by = defaultdict(list)
    for r in rs: by[r['delivery']].append(r)
    lines = ['STALE-FEED TRIAL (code only): per delivery, saved answers whose number changed and how many each arm flagged',
             'arm: candidate = streaming registry stale marks + feed lag check; row count = a row-count check per feed; nightly = full re-run at night (no flag during the day)', '']
    tot = defaultdict(int)
    for name, xs in by.items():
        ch = [x for x in xs if x['answer_changed']]
        row = {'answers': len(xs), 'changed': len(ch)}
        for arm in ('candidate', 'rowcount', 'nightly'):
            row[arm + '_missed'] = sum(1 for x in ch if not x[arm + '_flag'])
            row[arm + '_false'] = sum(1 for x in xs if x[arm + '_flag'] and not x['answer_changed'])
            tot[arm + '_missed'] += row[arm + '_missed']; tot[arm + '_false'] += row[arm + '_false']
        tot['changed'] += row['changed']; tot['answers'] += row['answers']
        if name == 'feed_stopped': tot['nightly_never'] += row['changed']   # a night re-run on the same incomplete feed repeats the wrong number
        lines.append(f"{name:21} {row['changed']:4} of {row['answers']} changed | candidate missed {row['candidate_missed']}, false flags {row['candidate_false']} | "
                     f"row count missed {row['rowcount_missed']}, false {row['rowcount_false']} | nightly missed until night {row['nightly_missed']}")
    lines += ['', f"all deliveries: {tot['changed']} changed answers of {tot['answers']}; candidate missed {tot['candidate_missed']} (false {tot['candidate_false']}); "
                  f"row count missed {tot['rowcount_missed']} (false {tot['rowcount_false']}); nightly re-run: all {tot['nightly_missed']} unflagged until the night run, "
                  f"and {tot['nightly_never']} (the stopped feed) still wrong after it"]
    return '\n'.join(lines)


if __name__ == '__main__':
    if sys.argv[1:2] == ['trial']: print(trial())
    elif sys.argv[1:2] == ['report']: print(report('artifacts/agere-tiered-core/trials/stale-journal.jsonl'))
    else: print(__doc__)
