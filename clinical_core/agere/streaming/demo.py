"""Streaming checks and demo on a scratch copy of a native delivery (never writes into an existing workspace).

    .venv/bin/python -m clinical_core.agere.streaming.demo equivalence --raw <native raw> --root <scratch dir> \
        --base 2026-09-15 --through 2026-09-30
        full rebuild of every file delivered through --through  vs  build through --base + one delta of the rest;
        prints the table-by-table comparison (equivalence.compare_workspaces) and timings.

    .venv/bin/python -m clinical_core.agere.streaming.demo stream --raw <native raw> --root <scratch dir> \
        --cutoff 2030-04-01 --cadence week [--reference-ws <full workspace dir>]
        batch build of every file delivered before the cutoff, Gold 10 answered through the deterministic cohort-family
        path, then the remaining files streamed in delivery order in daily / weekly batches with refresh after each.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
GOLD = ROOT / 'docs' / 'agere' / 'gold10'


def _ws(name, drop, root, raw_mode='all'):
    from ..onboarding.workspace import Workspace
    return Workspace.create(name, 'native://' + str(Path(drop).resolve()), root=root, raw=raw_mode)


def batch_build(name, raw, entries, root, *, through='docs', raw_mode='all'):
    from . import delivery_drop as dd
    root = Path(root)
    drop = root / f'{name}-drop'
    shutil.rmtree(drop, ignore_errors=True)
    dd.make_drop(raw, drop, entries)
    ws = _ws(name, drop, root, raw_mode)
    from ..ingest import flat
    flat.reset_schema_registry()  # a full build is one fresh process: no header registry carried over from another build
    t0 = time.time()
    ws.run(through=through)
    return ws, round(time.time() - t0, 1)


def equivalence(raw, root, base, through, *, cadence=None, keep=False):
    from . import delivery_drop as dd, equivalence as eq
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    entries = dd.manifest_entries(raw)
    full_e = [e for e in entries if e['delivered'] <= through]
    base_e = [e for e in entries if e['delivered'] <= base]
    delta_e = [e for e in full_e if e['delivered'] > base]
    for n in ('eq-full', 'eq-delta'):
        shutil.rmtree(root / n, ignore_errors=True)
    full, full_s = batch_build('eq-full', raw, full_e, root)
    delta, base_s = batch_build('eq-delta', raw, base_e, root)
    from . import pipeline
    boot = pipeline.bootstrap(delta)
    groups = dd.batches(delta_e, cadence) if cadence else {'all': delta_e}
    ticks_out = []
    t0 = time.time()
    for label, batch in groups.items():
        rels = dd.add_files(raw, root / 'eq-delta-drop', batch)
        t1 = time.time()
        tick = delta.ingest_delta(rels)
        ticks_out.append({'batch': label, 'files': len(batch), 'seconds': round(time.time() - t1, 1), 'touched': tick['touched_patients'],
                          'rebuilt': tick['patients_rebuilt'], 'as_of': [tick['as_of_before'], tick['as_of']], 'flags': tick['flags']})
    delta_s = round(time.time() - t0, 1)
    rep = eq.compare_workspaces(full.dir, delta.dir)
    out = {'files_full': len(full_e), 'files_base': len(base_e), 'files_delta': len(delta_e), 'full_build_seconds': full_s,
           'base_build_seconds': base_s, 'bootstrap': boot, 'delta_seconds': delta_s, 'ticks': ticks_out,
           'last_tick': {k: v for k, v in tick.items() if k not in ('raw_tables', 'stale_answers')},
           'equal': rep['equal'], 'summary': eq.summarize(rep), 'report': rep}
    return out


# ----------------------------------------------------------------------------------------------------------- stream demo
def gold_questions():
    return [json.loads(l) for l in (GOLD / 'questions.jsonl').read_text().splitlines() if l.strip()]


def answer_gold(ws):
    """Gold 10 through the deterministic cohort-family path (no model); registered for dependent replay."""
    from . import replay
    out = {}
    as_of = ws.state['as_of']
    for q in gold_questions():
        text = q['question'] + ' Definition: ' + q['definition']
        res = replay.run_cohort_family(text, as_of, ws.semantic_path)
        if res is None:
            out[q['id']] = None
            continue
        spec, sql, ids = res
        replay.register(ws.dir, 'gold10:' + q['id'], text, kind='cohort_family', semantic_path=ws.semantic_path, as_of=as_of,
                        patient_ids=ids, spec=spec, query=sql, as_of_mode='rolling')
        out[q['id']] = len(ids)
    return out


def gold_counts(ws):
    from . import replay
    db = replay.connect(ws.dir)
    try:
        return {aid.split(':', 1)[1]: (n, st) for aid, n, st in db.execute("SELECT answer_id, count, status FROM answers WHERE answer_id LIKE 'gold10:%'")}
    finally:
        db.close()


def stream(raw, root, cutoff, cadence='week', *, limit=None, log=print, lean=False, resume=False):
    """lean (small disks): workspace raw='none' (its semantic layer becomes a copy-on-write clone of the canonical one),
    no stream-records speed copy, profile from json_each per touched stream (no value-count state)."""
    from . import delivery_drop as dd
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    entries = dd.manifest_entries(raw)
    first = [e for e in entries if e['delivered'] < cutoff]
    rest = [e for e in entries if e['delivered'] >= cutoff]
    prior = {}
    if resume and (root / 'stream' / 'workspace.json').is_file():
        from ..onboarding.workspace import Workspace
        ws = Workspace('stream', root)
        steps = ws.state['steps']
        initial_s = None
        if (root / 'stream-report.json').is_file():
            prior = json.loads((root / 'stream-report.json').read_text())
            initial_s = prior.get('initial_build_seconds')
        if initial_s is None:
            from datetime import datetime
            t = lambda k: datetime.fromisoformat(steps[k]['at'].replace('Z', '+00:00'))
            created = json.loads((ws.dir / 'events.jsonl').read_text().splitlines()[0])['at']
            initial_s = round((t('pipeline') - datetime.fromisoformat(created.replace('Z', '+00:00'))).total_seconds(), 1)
        log(f'resuming workspace built in {initial_s}s')
    else:
        shutil.rmtree(root / 'stream', ignore_errors=True)
        log(f'initial build: {len(first)} files delivered before {cutoff}')
        ws, initial_s = batch_build('stream', raw, first, root, raw_mode='none' if lean else 'all')
    log(f'initial build {initial_s}s as_of {ws.state["as_of"]}')
    from . import pipeline
    if lean:
        (ws.dir / 'stack' / 'stream-records.sqlite3').unlink(missing_ok=True)
        (ws.dir / 'pipeline' / 'stream-records.sqlite3').unlink(missing_ok=True)
        pipeline.clone_workspace_semantic(ws, ws.state['as_of'])
    t0 = time.time()
    boot = pipeline.bootstrap(ws, profile_counts=not lean)
    boot['seconds'] = round(time.time() - t0, 1)
    log(f'streaming bootstrap {boot}')
    t0 = time.time()
    done = {b['batch'] for b in prior.get('batches', [])}
    initial = prior.get('initial_gold') if done else answer_gold(ws)
    log(f'gold10 initial {initial} ({round(time.time() - t0, 1)}s)')
    rows = list(prior.get('batches', [])) if done else []
    groups = dd.batches(rest, cadence)
    for i, (label, batch) in enumerate(groups.items()):
        if label in done:
            continue
        if limit is not None and i >= limit:
            break
        rels = dd.add_files(raw, root / 'stream-drop', batch)
        before = gold_counts(ws)
        t0 = time.time()
        tick = ws.ingest_delta(rels, refresh=True, record_store=not lean)
        secs = round(time.time() - t0, 1)
        after = gold_counts(ws)
        changes = {r['answer_id'].split(':', 1)[1]: [r['count_before'], r['count_after'], r['added'], r['removed']]
                   for r in (tick.get('refresh') or {}).get('answers', []) if r.get('status') == 'refreshed' and r['answer_id'].startswith('gold10:')}
        row = {'batch': label, 'files': len(batch), 'parsed_files': tick['files'], 'seconds': secs, 'tick': tick['tick'],
               'patients_touched': tick['touched_patients'], 'patients_rebuilt': tick['patients_rebuilt'],
               'as_of': tick['as_of'], 'as_of_before': tick['as_of_before'], 'flags': tick['flags'],
               'records': [tick['records_added'], tick['records_removed'], tick['records_changed']],
               'gold_stale': sorted(s['answer_id'].split(':', 1)[1] for s in tick['stale_answers'] if s['answer_id'].startswith('gold10:')),
               'gold_changed': {k: v for k, v in changes.items() if v[0] != v[1] or v[2] or v[3]},
               'timings': tick['timings']}
        rows.append(row)
        log(json.dumps({k: v for k, v in row.items() if k != 'timings'}, default=str))
        (root / 'stream-report.json').write_text(json.dumps({'cutoff': cutoff, 'cadence': cadence, 'initial_build_seconds': initial_s,
                                                              'bootstrap': boot,
                                                              'initial_gold': initial, 'batches': rows}, indent=1, default=str))
    final = {k: v[0] for k, v in gold_counts(ws).items()}
    ref = json.loads((GOLD / 'agere_reference.json').read_text())['questions']
    match = {k: (final.get(k), ref[k]['count'], final.get(k) == ref[k]['count']) for k in ref}
    report = {'cutoff': cutoff, 'cadence': cadence, 'initial_build_seconds': initial_s, 'bootstrap': boot,
              'initial_gold': initial, 'batches': rows, 'final_as_of': ws.state['as_of'], 'final_gold': final, 'reference_match': match}
    (root / 'stream-report.json').write_text(json.dumps(report, indent=1, default=str))
    return ws, report


def reference(raw, root, through=None, *, keep=False):
    """Full batch build (fresh registry) of every file delivered through `through` (default: all), fingerprinted, with
    Gold 10 counts from the deterministic family path; the workspace is deleted unless keep (disk)."""
    from . import delivery_drop as dd, equivalence as eq, replay
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    entries = [e for e in dd.manifest_entries(raw) if through is None or e['delivered'] <= through]
    shutil.rmtree(root / 'reference', ignore_errors=True)
    ws, secs = batch_build('reference', raw, entries, root)
    t0 = time.time()
    fp = eq.fingerprint_workspace(ws.dir)
    gold = {}
    for q in gold_questions():
        res = replay.run_cohort_family(q['question'] + ' Definition: ' + q['definition'], ws.state['as_of'], ws.semantic_path)
        gold[q['id']] = sorted(res[2]) if res else None
    out = {'files': len(entries), 'full_build_seconds': secs, 'as_of': ws.state['as_of'], 'fingerprint_seconds': round(time.time() - t0, 1),
           'stage_seconds': (json.loads((ws.dir / 'stage.json').read_text()) or {}).get('seconds'), 'gold': gold, 'fingerprint': fp,
           'steps': {k: v.get('at') for k, v in ws.state['steps'].items()}}
    (root / 'reference.json').write_text(json.dumps(out, default=str))
    if not keep:
        shutil.rmtree(ws.dir, ignore_errors=True)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['equivalence', 'stream', 'reference'])
    ap.add_argument('--reference-json')
    ap.add_argument('--keep', action='store_true')
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--lean', action='store_true', help='stream: small-disk mode (see stream())')
    ap.add_argument('--raw', required=True)
    ap.add_argument('--root', required=True)
    ap.add_argument('--base')
    ap.add_argument('--through')
    ap.add_argument('--cutoff', default='2030-04-01')
    ap.add_argument('--cadence', default='week', choices=['day', 'week'])
    ap.add_argument('--limit', type=int)
    ap.add_argument('--batches', action='store_true', help='equivalence: stream the delta in --cadence batches')
    a = ap.parse_args(argv)
    if a.cmd == 'equivalence':
        out = equivalence(a.raw, a.root, a.base, a.through, cadence=a.cadence if a.batches else None)
        (Path(a.root) / 'equivalence.json').write_text(json.dumps(out, indent=1, default=str))
        print(out['summary'])
        print(json.dumps({k: v for k, v in out.items() if k not in ('report', 'summary')}, indent=1, default=str))
    elif a.cmd == 'reference':
        out = reference(a.raw, a.root, a.through, keep=a.keep)
        print(json.dumps({k: v for k, v in out.items() if k not in ('fingerprint', 'gold')}, indent=1, default=str))
        print({k: len(v) if v is not None else None for k, v in out['gold'].items()})
    else:
        ws, rep = stream(a.raw, a.root, a.cutoff, a.cadence, limit=a.limit, lean=a.lean, resume=a.resume)
        if a.reference_json:
            from . import equivalence as eq, replay
            ref = json.loads(Path(a.reference_json).read_text())
            t0 = time.time()
            cmp = eq.compare_fingerprints(ref['fingerprint'], eq.fingerprint_workspace(ws.dir))
            db = replay.connect(ws.dir)
            try:
                ids = {aid.split(':', 1)[1]: json.loads(p) for aid, p in db.execute("SELECT answer_id, patient_ids FROM answers WHERE answer_id LIKE 'gold10:%'")}
            finally:
                db.close()
            rep['equivalence_vs_full_build'] = {'equal': cmp['equal'], 'seconds': round(time.time() - t0, 1),
                                                'differing': {k: v['differing_tables'] for k, v in cmp.items() if isinstance(v, dict) and 'differing_tables' in v},
                                                'source_profile_equal': cmp['source_profile']['equal'],
                                                'gold_ids_equal_full_build': {k: ids.get(k) == v for k, v in ref['gold'].items()},
                                                'full_build_seconds': ref.get('full_build_seconds'), 'full_stage_seconds': ref.get('stage_seconds')}
            (Path(a.root) / 'stream-report.json').write_text(json.dumps(rep, indent=1, default=str))
        print(json.dumps({k: v for k, v in rep.items() if k != 'batches'}, indent=1, default=str))


if __name__ == '__main__':
    sys.exit(main())
