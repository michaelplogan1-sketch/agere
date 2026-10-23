"""Assemble labeled sets and compute agreement.

    python -m tools.note_labels.build_labels --p2 scratchpad/p2_agent{0..3}_labels.json

Pass 1 = p1_NN.txt (lines `id|status|field=a,b;field2=c|quote`), written by the set's author while reading the passages.
Pass 2 = blind JSON lists from separate fresh sessions that saw only the guide and the passages (no ids, strata or pass-1 labels).
A status label is `agreed` when both passes give the same status; a detail label is agreed when the field values match (multi: same
set). The calibration/measurement set keeps items whose STATUS agrees; detail fields are kept only where both passes agree on them.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MULTI = {('medication_event', 'reason'), ('condition', 'assessment'), ('social_need', 'domain'), ('preference_refusal', 'reason')}


def read_p1():
    out = {}
    for f in sorted(glob.glob(str(HERE / 'p1_*.txt'))):
        for line in Path(f).read_text().splitlines():
            if not line.strip():
                continue
            iid, status, detail, quote = (line.split('|', 3) + ['', '', ''])[:4]
            d = {}
            for part in filter(None, detail.split(';')):
                k, v = part.split('=', 1)
                d[k] = v.split(',') if ',' in v else v
            out[iid] = {'id': iid, 'status': status, 'detail': d, 'quote': quote}
    return out


def norm(frame, field, v):
    if (frame, field) in MULTI or isinstance(v, list):
        return sorted(v if isinstance(v, list) else [v])
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--p2', nargs='+', required=True)
    ap.add_argument('--p2b', default=None, help='blind relabel of some items under guide v2 (replaces pass 2 for them)')
    ap.add_argument('--out', default=str(HERE / 'labeled.jsonl'))
    a = ap.parse_args()
    items = {}
    for line in (HERE / 'items.jsonl').open():
        i = json.loads(line)
        items[i['id']] = i
    p1 = read_p1()
    p2 = {}
    for f in a.p2:
        for r in json.load(open(f)):
            r.setdefault('detail', {}); r['detail'] = r['detail'] or {}; r.setdefault('quote', '')
            p2[r['id']] = r
    v1_status = {i: r['status'] for i, r in p2.items()}  # pass 2 under guide v1, kept for the agreement report
    for r in (json.load(open(a.p2b)) if a.p2b else []):  # pass 2b: blind relabel under guide v2 replaces pass 2 for those items
        r.setdefault('detail', {}); r['detail'] = r['detail'] or {}; r.setdefault('quote', '')
        p2[r['id']] = r
    missing = [i for i in items if i not in p1 or i not in p2]
    if missing:
        sys.exit(f'missing labels for {len(missing)} items, e.g. {missing[:5]}')
    rows, per = [], collections.defaultdict(lambda: [0, 0, 0, 0])
    for iid, it in items.items():
        l1, l2 = p1[iid], p2[iid]
        frame = it['frame']
        agree = l1['status'] == l2['status']
        per[frame][0] += 1
        per[frame][1] += agree
        text = it['text']
        quote_ok = all((not q) or q in text for q in (l1['quote'], l2['quote']))
        det = {}
        if agree and l1['status'] in ('documented', 'possible', 'planned_not_done', 'historical'):
            for f in set(l1['detail']) & set(l2['detail']):
                v1, v2 = norm(frame, f, l1['detail'][f]), norm(frame, f, l2['detail'][f])
                per[frame][2] += 1
                if v1 == v2:
                    det[f] = l1['detail'][f]
                    per[frame][3] += 1
        rows.append({**it, 'pass1': l1, 'pass2': l2, 'pass2_guide_v1_status': v1_status[iid], 'status_agreed': agree, 'status': l1['status'] if agree else None,
                     'detail': det, 'quote': l1['quote'] if agree else None, 'quote_ok': quote_ok})
    Path(a.out).write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in rows))
    tot = [sum(v[k] for v in per.values()) for k in range(4)]
    print('frame                 n  status_agree  detail_agree(both labeled)')
    for fr, v in per.items():
        print(f'{fr:20s} {v[0]:3d}  {v[1] / v[0]:.3f}        {v[3]}/{v[2]}')
    print(f'ALL                  {tot[0]:3d}  {tot[1] / tot[0]:.3f}        {tot[3]}/{tot[2]}')
    v1 = [(r['frame'], r['pass1']['status'] == r['pass2_guide_v1_status']) for r in rows]
    print('status agreement under guide v1 (before the medication clarification): '
          f'{sum(x for _, x in v1) / len(v1):.3f}; medication_event {sum(x for f, x in v1 if f == "medication_event") / 44:.3f}')
    conf = collections.Counter((r['pass1']['status'], r['pass2']['status']) for r in rows if not r['status_agreed'])
    print('disagreements (pass1 -> pass2):', conf.most_common(12))


if __name__ == '__main__':
    main()
