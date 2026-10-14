# Usage: PYTHONPATH=. .venv/bin/python tools/notes_extraction/v22_evaluate.py test [--show fact_type|all] [--json out.json]
#        PYTHONPATH=. .venv/bin/python tools/notes_extraction/v22_evaluate.py heldout [--show fact_type]
"""Per-fact-type precision/recall of the rule extractor on v2.2 text units against hand labels, and the no-regression check
on the earlier (v1.23 stack) held-out split.

test: units from v22_split_sample.py (<v22 dir>/test_units.jsonl); labels from tools/notes_extraction/v22_test_labels.txt
  (one line per unit index: `idx|fact_type=value[-]; ...`, '-' = negated). The test labels were written by reading every unit
  before any rule change. A fact matches on (fact_type, value, negated), multiset per unit (evaluate.py's key).
heldout: the earlier sample's test split with Sonnet labels + Opus adjudication (artifacts/notes-extraction, read only).
  Extractor-only facts the old adjudication never saw are listed and counted as errors.
admit: rewrite clinical_core/agere/clinical/note_facts_admission.json (see admit()). Dev-half units were used to write rules
  by reading extractor output (no dev labels); the test half was only scored after the rules were final.
"""
import argparse, json, sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT, read_jsonl, norm_value, note_facts  # noqa: E402

HERE = Path(__file__).resolve().parent
V22 = OUT / 'v22'


def parse_labels(path):
    out = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.startswith('#'): continue
        idx, _, rest = line.partition('|')
        facts = []
        for item in filter(None, (x.strip() for x in rest.split(';'))):
            ft, _, v = item.partition('=')
            neg = v.endswith('-')
            facts.append((ft.strip(), norm_value(ft.strip(), v.rstrip('-')), neg))
        out[int(idx)] = facts
    return out


def key(f):
    return (f['fact_type'], norm_value(f['fact_type'], f['value']), bool(f['negated']))


def group_of(u):
    return 'hie' if u['stream'] == 'hie_documents' else ('problem_list_update' if u['note_type'] == 'problem_list_update' else 'signed_note')


def compare(split):
    units = read_jsonl(V22 / f'{split}_units.jsonl')
    labels = parse_labels(HERE / f'v22_{split}_labels.txt')
    rows = []
    for i, u in enumerate(units):
        if i not in labels: continue
        ext = note_facts.extract(u['text'], u['stream'], 'text')
        ek, gk = Counter(map(key, ext)), Counter(labels[i])
        agreed = ek & gk
        rows.append({'i': i, 'unit': u, 'ext': ext, 'agreed': agreed, 'ext_only': ek - agreed, 'gold_only': gk - agreed})
    return rows


def table(rows):
    s = defaultdict(Counter)
    for r in rows:
        for k, n in r['agreed'].items(): s[k[0]].update(tp=n, ext=n, gold=n)
        for k, n in r['ext_only'].items(): s[k[0]].update(ext=n)
        for k, n in r['gold_only'].items(): s[k[0]].update(gold=n)
    res = {}
    for ft, c in sorted(s.items()):
        res[ft] = {'extractor_facts': c['ext'], 'reference_facts': c['gold'], 'true_positive': c['tp'],
                   'precision': round(c['tp'] / c['ext'], 3) if c['ext'] else None,
                   'recall': round(c['tp'] / c['gold'], 3) if c['gold'] else None}
    tot = Counter()
    for c in s.values(): tot.update(c)
    res['_overall'] = {'extractor_facts': tot['ext'], 'reference_facts': tot['gold'], 'true_positive': tot['tp'],
                       'precision': round(tot['tp'] / tot['ext'], 3) if tot['ext'] else None,
                       'recall': round(tot['tp'] / tot['gold'], 3) if tot['gold'] else None}
    return res


def show(rows, ft):
    for r in rows:
        eo = [k for k in r['ext_only'] if ft in ('all', k[0])]; go = [k for k in r['gold_only'] if ft in ('all', k[0])]
        if not eo and not go: continue
        u = r['unit']
        print('=' * 50, r['i'], u['stream'], u['note_type'], u.get('template_style'))
        for k in eo:
            f = next(f for f in r['ext'] if key(f) == k)
            print(f'  EXT ONLY {k} :: ...{u["text"][max(0, f["span_start"] - 60):f["span_end"] + 30]!r}')
        for k in go: print(f'  LABEL ONLY {k}')


def heldout(show_ft=None):
    from evaluate import compare as old_compare, load_judged, table as old_table
    rows = old_compare('test')
    judged = load_judged()
    unseen = [(r['unit']['unit_id'], k) for r in rows for k in r['ext_only'] if (r['unit']['unit_id'], 'ext', k) not in judged]
    adj = old_table(rows, judged)
    if show_ft:
        for r in rows:
            uid = r['unit']['unit_id']
            for side, ks, facts, ev in (('ext', r['ext_only'], r['ext'], 'evidence_text'), ('gold', r['gold_only'], r['gold'], 'quote')):
                for k in ks:
                    if show_ft not in ('all', k[0]): continue
                    j = judged.get((uid, side, k))
                    f = next(f for f in facts if key(f) == k)
                    print(f'{side.upper()} judged={j} {k} :: {f[ev][:100]!r}  [{uid}]')
    return adj, unseen


def admit(before_json=None):
    """Rewrite note_facts_admission.json for the current extractor. A fact type is admitted when its precision on the v2.2
    test half is >= BAR; a type the v2.2 text never states (no extractor or reference facts on the test half) keeps its
    admission from the earlier adjudicated held-out split, which must itself still clear BAR with >= MIN_N facts."""
    from evaluate import ADMISSION, BAR, MIN_N, compare as old_compare, table as old_table
    test = table(compare('test'))
    adj, unseen = heldout()
    old = json.loads(ADMISSION.read_text())
    admitted, basis = [], {}
    for ft in note_facts.FACT_DEFINITIONS:
        t, h = test.get(ft), adj.get(ft)
        if t and t['extractor_facts']:
            ok = t['precision'] >= BAR and (h is None or h['extractor_facts'] == 0 or h['precision'] >= BAR)
            basis[ft] = 'v2.2 test precision' + ('' if h is None else ' + held-out no regression')
        elif h and h['extractor_facts']:
            ok = h['precision'] >= BAR and h['extractor_facts'] >= MIN_N
            basis[ft] = 'not stated in v2.2 test text; earlier adjudicated held-out'
        else:
            ok = False; basis[ft] = 'no evidence'
        if ok: admitted.append(ft)
    report = {'schema': 'agere-note-facts-admission/2', 'extractor': note_facts.EXTRACTOR, 'precision_bar': BAR,
              'min_extractor_facts': MIN_N, 'admitted': sorted(admitted), 'admission_basis': basis,
              **{k: v for k, v in old.items() if k not in ('schema', 'admitted', 'extractor', 'precision_bar', 'min_extractor_facts',
                                                          'admission_basis', 'v22_test')},
              'v22_test': {'units': len(compare('test')), 'labels': 'tools/notes_extraction/v22_test_labels.txt (hand labels written '
                           'before rule changes; split = sha256(unit_id) odd, original development sample excluded)',
                           'after': test, 'before': (json.loads(Path(before_json).read_text())['per_fact_type'] if before_json else None)},
              'held_out_vs_label_model': old_table(old_compare('test')), 'held_out_adjudicated': adj,
              'held_out_unadjudicated_extractor_facts': len(unseen)}
    ADMISSION.write_text(json.dumps(report, indent=1) + '\n')
    print('admitted:', sorted(admitted))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('split', choices=['test', 'heldout', 'admit'])
    ap.add_argument('--before', help='admit: test_before.json from the run before the rule changes')
    ap.add_argument('--show')
    ap.add_argument('--json')
    a = ap.parse_args()
    if a.split == 'admit':
        admit(a.before)
    elif a.split == 'heldout':
        adj, unseen = heldout(a.show)
        for ft, m in adj.items(): print(f'{ft:26s} adj P={m["precision"]} R={m["recall"]} n_ext={m["extractor_facts"]}')
        print('extractor-only facts never adjudicated (counted wrong):', len(unseen))
        for u in unseen: print('  ', u)
        if a.json: Path(a.json).write_text(json.dumps({'adjudicated': adj, 'unadjudicated_extractor_facts': [list(map(str, x)) for x in unseen]}, indent=1))
    else:
        rows = compare(a.split)
        if a.show: show(rows, a.show)
        res = table(rows)
        for ft, m in res.items(): print(f'{ft:26s} P={m["precision"]} R={m["recall"]} ext={m["extractor_facts"]} ref={m["reference_facts"]} tp={m["true_positive"]}')
        by = defaultdict(lambda: Counter())
        for r in rows:
            g = group_of(r['unit'])
            by[g].update(tp=sum(r['agreed'].values()), ext=sum(r['agreed'].values()) + sum(r['ext_only'].values()),
                         gold=sum(r['agreed'].values()) + sum(r['gold_only'].values()))
        for g, c in sorted(by.items()): print(f'  [{g}] P={c["tp"] / max(1, c["ext"]):.3f} R={c["tp"] / max(1, c["gold"]):.3f} ext={c["ext"]} ref={c["gold"]}')
        if a.json: Path(a.json).write_text(json.dumps({'units': len(rows), 'per_fact_type': res,
                                                       'by_source': {g: dict(c) for g, c in by.items()}}, indent=1))
