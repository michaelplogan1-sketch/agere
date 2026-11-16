"""Development instrumentation of the unchanged retriever; no evaluation labels."""
import json
import time
from pathlib import Path
from collections import defaultdict
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import warehouse_retrieval as W, retrieval_comparison as R

OUT = Path('artifacts/agere-retrieval-source-plan-v1')


class Cursor:
    def __init__(self, cursor, timing): self.cursor, self.timing = cursor, timing
    def run(self, name, *args):
        t = time.perf_counter()
        try: return getattr(self.cursor, name)(*args)
        finally: self.timing['seconds'] += time.perf_counter() - t
    def fetchone(self): return self.run('fetchone')
    def fetchall(self): return self.run('fetchall')
    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None: break
            yield row


class DB:
    def __init__(self, db): self.db, self.timings = db, defaultdict(lambda: {'calls': 0, 'seconds': 0})
    def execute(self, sql, args=()):
        timing = self.timings[sql]; timing['calls'] += 1; t = time.perf_counter()
        try: c = self.db.execute(sql, args)
        finally: timing['seconds'] += time.perf_counter() - t
        return Cursor(c, timing)
    def __getattr__(self, name): return getattr(self.db, name)
    def __enter__(self): self.db.__enter__(); return self
    def __exit__(self, *args): return self.db.__exit__(*args)


def main():
    r = W.Retriever(stack_path()); original_db = r.db; proxy = DB(original_db); r.db = proxy
    reports = []
    try:
        for workflow in ('medication', 'payer', 'appointment'):
            prior = json.loads(Path('artifacts/agere-retrieval-comparison-v1', workflow+'.json').read_text())
            request = prior['request']; trace = R.Recorder(r); proxy.timings.clear()
            before = dict(r.reranker.stats)
            try:
                r.reranker = trace
                output = r.search(request['question'], patient=request['patient'], patient_ids=[request['patient']], analysis_window=request['analysis_window'])
            finally: r.reranker = trace.delegate
            assert [x['id'] for x in output['evidence']] == prior['variants']['bge_reranked']
            row = {'workflow': workflow, 'patient': request['patient'], 'elapsed_seconds': output['elapsed_seconds'],
                   'sql': sorted([{'sql': sql, **v} for sql, v in proxy.timings.items()], key=lambda v: -v['seconds']),
                   'reranker_delta': {k: r.reranker.stats[k]-v for k, v in before.items()},
                   'exact_final_evidence_preserved': True}
            reports.append(row)
            print(json.dumps({'workflow': workflow, 'elapsed': row['elapsed_seconds'], 'top_sql': row['sql'][:3]}), flush=True)
        (OUT/'retrieval-stage-profile.json').write_text(json.dumps({'status': 'measured', 'cases': reports,
            'limits': ['Same three development questions; source ranking unchanged; no clinical quality evaluation.',
                       'Fresh result-cache namespaces, existing exact encoder/reranker score caches retained.',
                       'SQL timings include execute and cursor consumption; unmeasured time includes Python, vector math and other DB connections.'],
            'new_paid_api_calls': 0}, indent=2)+'\n')
    finally:
        r.db = original_db; r.close()


if __name__ == '__main__': main()
