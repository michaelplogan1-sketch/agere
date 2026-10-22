"""Sketch a large warehouse feed in one streaming pass and report the sketch against the exact counts.

    python tools/feed_sketch.py [stack_dir]    # default: the active warehouse build's stack/ semantic db

Records per stream (count-min) and distinct patients per stream (HyperLogLog) over the `record` table, with the exact
GROUP BY numbers beside them so the error is visible. The sketches are for feeds too large to count exactly on the question path
(or for merging shard summaries); this tool is where their accuracy is checked on the real warehouse."""
from pathlib import Path
import json
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clinical_core.agere.work_queue import profile_feed  # noqa: E402


def active_stack():
    return Path(json.loads((ROOT/'config/agere/active_warehouse.json').read_text())['build'])/'stack'


def run(stack=None, limit=None):
    db = sqlite3.connect(f'file:{Path(stack or active_stack())/"semantic.sqlite3"}?mode=ro', uri=True)
    t = time.time()
    sql = 'SELECT stream, patient_id FROM record' + (f' LIMIT {int(limit)}' if limit else '')
    cm, hll = profile_feed(db.execute(sql), lambda r: r[0], lambda r: r[1])
    sketch_s = time.time() - t
    exact = {s: (n, d) for s, n, d in db.execute(f'SELECT stream, count(*), count(distinct patient_id) FROM (SELECT stream, patient_id FROM record{" LIMIT "+str(int(limit)) if limit else ""}) GROUP BY stream')}
    rows = [{'stream': s, 'records_exact': n, 'records_cms': cm.count(s), 'distinct_exact': d, 'distinct_hll': hll[s].count(),
             'distinct_rel_err': round(abs(hll[s].count() - d) / d, 4) if d else 0} for s, (n, d) in sorted(exact.items(), key=lambda kv: -kv[1][0])]
    return {'rows': rows, 'sketch_seconds': round(sketch_s, 1), 'cms_bound': cm.error_bound(), 'hll_relative_error_bound': next(iter(hll.values())).relative_error()}


if __name__ == '__main__':
    out = run(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps({**out, 'rows': out['rows'][:12]}, indent=1))
