"""System One questions for note frames, and the code that combines passage readings into one patient value.

For each candidate passage (note_evidence.retrieve) one typed STATUS question is sent, whose options are exactly
note_frames.STATUSES with their precise definitions. Passages whose status can satisfy the predicate then get DETAIL questions,
only for the detail fields the predicate constrains (or every field for answer_patient): a closed choice per choice field, a
yes/no per wanted option of a multi field, and "which of these numbers/dates/times" over candidates that code extracted from the
passage for number/date/time/count fields. Every question goes through system_one.decide (Jev first; Perplexity when Jev's
calibrated probability is under the field's threshold or it says unknown). Per-(frame, field) thresholds and log-odds shifts come
from config/agere/note_frames_calibration.json (AGERE_NOTE_FRAMES_CALIBRATION overrides the path); with no entry the shift is the
identity and the threshold is note_frames.DEFAULT_THRESHOLD. A reading under its threshold is UNDECIDED, never "no".

combine() is pure code (no model): any | latest | first | all | conflict | trend over the passage readings. An undecided passage
that could change the answer makes the patient `unknown` (holds None); a count over patients is then a range.
"""
from __future__ import annotations

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date
from pathlib import Path

from . import note_evidence, note_frames
from . import system_one

VERSION = 'note-questions/1'
ROOT = Path(__file__).resolve().parents[3]
ORDER = ('jev', 'perplexity')
KIND_PHRASE = {'condition': 'condition or diagnosis', 'symptom': 'symptom', 'medication': 'medicine',
               'procedure': 'procedure, test or study', 'measurement': 'measurement or score',
               'adverse_event': 'adverse event, allergy or intolerance', 'care_process': 'care process',
               'social_need': 'social need or barrier', 'function': 'function, cognition or safety topic',
               'decision': 'decision or preference', 'plan': 'plan or follow-up', 'proposition': 'statement'}
MONTHS = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}
_LOCK = threading.Lock()
_CAL = {}


# -- calibration file --------------------------------------------------------------------------------------------------
def calibration_path():
    return Path(os.environ.get('AGERE_NOTE_FRAMES_CALIBRATION') or ROOT / 'config/agere/note_frames_calibration.json')


def load_calibration(path=None):
    path = Path(path or calibration_path())
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {'schema': 'note-frames-calibration/1', 'tasks': {}}
    with _LOCK:
        hit = _CAL.get(str(path))
        if hit and hit[0] == mtime:
            return hit[1]
        data = json.loads(path.read_text())
        _CAL[str(path)] = (mtime, data)
        return data


def params_for(task, provider, cal=None):
    """{'threshold','shift'} for (task, provider); identity shift and the default threshold when unfit."""
    cal = cal if cal is not None else load_calibration()
    p = (cal.get('tasks', {}).get(task) or {}).get(provider) or {}
    return {'threshold': float(p.get('threshold', note_frames.DEFAULT_THRESHOLD)), 'shift': p.get('shift') or {}}


# -- question text -----------------------------------------------------------------------------------------------------
def humanize(option):
    return option.replace('_', ' ')


def status_question(predicate, passage):
    f = note_frames.FRAMES[_read_frame(predicate)]
    kind = KIND_PHRASE[f['concept_kind']]
    term = predicate['concept']['term']
    return (f"About the PATIENT only: what does this {passage['note_type']} passage dated {passage['date']} say regarding the "
            f"{kind} \"{term}\"? {f['stem']} \"{term}\"? Choose the one best description of what the passage states.")


def _read_frame(predicate):
    """The frame whose status/detail questions are asked: the base frame for a contradiction predicate."""
    if predicate['frame'] == 'note_contradiction':
        return predicate['detail']['base_frame']
    return predicate['frame']


def detail_question(predicate, passage, field, option=None, asked=''):
    f = note_frames.FRAMES[_read_frame(predicate)]
    term = predicate['concept']['term']
    head = f"About the PATIENT only, regarding \"{term}\" in this {passage['note_type']} passage dated {passage['date']}: "
    if option is not None:
        return head + f"{f['details'][field]['ask'].rstrip('?')}? Is \"{humanize(option)}\" stated or clearly meant?"
    return head + (asked or f['details'][field]['ask'])


YES_NO = {'yes': 'The passage states or clearly means this for the patient', 'no': 'The passage states or clearly means otherwise'}


def candidates(kind, text, note_date):
    """Closed candidate lists a model can choose from for number/count/date/time fields: [(label, value, description)]."""
    out = []
    if kind in ('number', 'count'):
        for m in re.finditer(r'(?<![\w./:-])-?\d+(?:\.\d+)?(?![\w:/-]*\d{2}\b)', text):
            ctx = text[max(0, m.start() - 30):m.end() + 20].replace('\n', ' ')
            val = m.group()
            if kind == 'count' and '.' in val:
                continue
            out.append((val, f'{val}   (…{ctx}…)'))
    elif kind == 'date':
        for m in re.finditer(r'\b(\d{4})-(\d{2})-(\d{2})\b|\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b|'
                             r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? (\d{1,2}),? (\d{4})\b', text, re.I):
            try:
                if m.group(1):
                    iso = _date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
                elif m.group(4):
                    y = int(m.group(6)); y += 2000 if y < 100 else 0
                    iso = _date(y, int(m.group(4)), int(m.group(5))).isoformat()
                else:
                    iso = _date(int(m.group(9)), MONTHS[m.group(7)[:3].lower()], int(m.group(8))).isoformat()
            except ValueError:
                continue
            out.append((iso, f'{iso}   (…{text[max(0, m.start() - 25):m.end() + 10]}…)'.replace('\n', ' ')))
        if note_date:
            out.append((str(note_date)[:10], f'{str(note_date)[:10]}   (the date of the note itself)'))
    elif kind == 'time':
        for m in re.finditer(r'\b([01]?\d|2[0-3]):([0-5]\d)\b', text):
            out.append((f'{int(m.group(1)):02d}:{m.group(2)}', f'{m.group()}   (…{text[max(0, m.start() - 25):m.end() + 10]}…)'.replace('\n', ' ')))
    seen, uniq = set(), []
    for v, d in out:
        if v not in seen:
            seen.add(v)
            uniq.append((v, d))
    return uniq[:8]


# -- one typed read ----------------------------------------------------------------------------------------------------
def read(question, options, passage, task, *, as_of, ledger, allow_live, threshold=None, decide=None, cal=None):
    """Jev, then Perplexity when Jev is unsure. -> {label, p, accepted, provider, probabilities, cost_usd, steps}.
    `p` is the calibrated probability of the label under that task's per-provider shift. Never raises."""
    decide = decide or system_one.decide
    ev = [{'id': passage['passage_id'], 'text': passage['text'], 'date': passage['date'], 'note_id': passage['record_id']}]
    best, steps, cost = None, [], 0.0
    for prov in ORDER:
        prm = params_for(task, prov, cal)
        thr = prm['threshold'] if threshold is None else threshold
        try:
            r = decide(question, options, ev, as_of=as_of, order=(prov,), threshold=0.0, task_key=None, ledger=ledger,
                       allow_live=allow_live)
        except Exception as exc:  # noqa: BLE001 - one provider failing never walls the reader out
            steps.append({'provider': prov, 'status': 'error', 'error': f'{type(exc).__name__}: {str(exc)[:160]}'})
            continue
        cost += r.get('cost_usd') or 0.0
        label, probs = r.get('label'), r.get('probabilities') or {}
        if not label or r.get('status') in ('prepared', 'providers_failed'):
            steps.append({'provider': prov, 'status': r.get('status', 'no_label')})
            continue
        cal_probs = system_one.calibrate(task, probs, params={'shift': prm['shift'], 'source': {'provider': prov}} if prm['shift'] else None,
                                         provider=prov) if prm['shift'] else dict(probs)
        label = max(cal_probs, key=cal_probs.get) if cal_probs else label
        p = cal_probs.get(label, 0.0)
        steps.append({'provider': prov, 'status': 'answered', 'label': label, 'p': round(p, 4), 'cache_hit': r.get('cache_hit')})
        cand = {'label': label, 'p': p, 'provider': prov, 'probabilities': cal_probs, 'raw_probabilities': probs}
        if best is None or (best['label'] == 'unknown') > (label == 'unknown') or \
                ((best['label'] == 'unknown') == (label == 'unknown') and p > best['p']):
            best = cand
        if label != 'unknown' and p >= thr:
            return {**cand, 'accepted': True, 'cost_usd': cost, 'steps': steps}
    if best is None:
        return {'label': None, 'p': None, 'accepted': False, 'provider': None, 'probabilities': {}, 'cost_usd': cost, 'steps': steps}
    return {**best, 'accepted': False, 'cost_usd': cost, 'steps': steps}


def read_status(predicate, passage, *, as_of, ledger, allow_live, decide=None, cal=None):
    frame = _read_frame(predicate)
    task = note_frames.task_key(frame)
    r = read(status_question(predicate, passage), dict(note_frames.STATUSES), passage, task, as_of=as_of, ledger=ledger,
             allow_live=allow_live, threshold=predicate.get('min_p'), decide=decide, cal=cal)
    return r


def read_detail(predicate, passage, field, wanted_options, *, as_of, ledger, allow_live, decide=None, cal=None):
    """-> (value, accepted, p, cost). value: str | [str] | None. wanted_options limits a multi field to those options (None = all)."""
    frame = _read_frame(predicate)
    d = note_frames.FRAMES[frame]['details'][field]
    task = note_frames.task_key(frame, field)
    kw = dict(as_of=as_of, ledger=ledger, allow_live=allow_live, threshold=predicate.get('min_p'), decide=decide, cal=cal)
    kind = d['kind']
    if kind == 'choice':
        r = read(detail_question(predicate, passage, field), {o: humanize(o) for o in d['options']}, passage, task, **kw)
        return (r['label'] if r['accepted'] and r['label'] != 'unknown' else None), r['accepted'] and r['label'] != 'unknown', r['p'], r['cost_usd']
    if kind == 'multi':
        got, ok, cost, ps = [], True, 0.0, []
        for o in (wanted_options if wanted_options is not None else d['options']):
            r = read(detail_question(predicate, passage, field, option=o), YES_NO, passage, task, **kw)
            cost += r['cost_usd']
            if not r['accepted']:
                ok = False
            elif r['label'] == 'yes':
                got.append(o)
            ps.append(r['p'])
        return got, ok, min([p for p in ps if p is not None] or [None]) if ps else None, cost
    if kind in ('number', 'count', 'date', 'time'):
        cands = candidates(kind, passage['text'], passage['date'])
        if not cands:
            return None, True, None, 0.0  # nothing to pick from: a decided "not stated"
        opts = {f'c{i}': desc for i, (_, desc) in enumerate(cands)}
        opts['none'] = 'None of these is it'
        r = read(detail_question(predicate, passage, field), opts, passage, task, **kw)
        if not r['accepted'] or r['label'] in (None, 'unknown'):
            return None, False, r['p'], r['cost_usd']
        if r['label'] == 'none':
            return None, True, r['p'], r['cost_usd']
        return cands[int(r['label'][1:])][0], True, r['p'], r['cost_usd']
    if kind == 'quote':
        sents = [s for _, _, s in note_evidence.sentences(passage['text'])][:8]
        if not sents:
            return None, True, None, 0.0
        opts = {f's{i}': s[:300] for i, s in enumerate(sents)}
        opts['none'] = 'No sentence gives it'
        r = read(detail_question(predicate, passage, field), opts, passage, task, **kw)
        if not r['accepted'] or r['label'] in (None, 'unknown'):
            return None, False, r['p'], r['cost_usd']
        return (None if r['label'] == 'none' else sents[int(r['label'][1:])]), True, r['p'], r['cost_usd']
    raise ValueError(kind)


# -- reading one passage -----------------------------------------------------------------------------------------------
def read_passage(predicate, passage, *, as_of, ledger, allow_live, all_details, decide=None, cal=None):
    st = read_status(predicate, passage, as_of=as_of, ledger=ledger, allow_live=allow_live, decide=decide, cal=cal)
    reading = {'passage_id': passage['passage_id'], 'record_id': passage['record_id'], 'date': passage['date'],
               'note_type': passage['note_type'], 'source': passage['source'], 'quote': passage['text'],
               'start': passage['start'], 'end': passage['end'], 'duplicates': passage.get('duplicates', []),
               'status': st['label'] if st['accepted'] else None, 'p': st['p'], 'provider': st['provider'],
               'best_guess': st['label'], 'detail': {}, 'detail_undecided': [], 'cost_usd': st['cost_usd']}
    if not st['accepted']:
        reading['undecided'] = True
        return reading
    reading['undecided'] = False
    if reading['status'] not in note_frames.POSITIVE:
        return reading
    frame = _read_frame(predicate)
    spec = note_frames.FRAMES[frame]['details']
    want = predicate.get('detail') or {}
    base_axis = None
    if predicate['frame'] == 'note_contradiction':
        want = {}
        base_axis = predicate['detail'].get('axis', 'any')
    fields = list(spec) if all_details else [f for f in spec if f in want or f in (predicate.get('return_detail') or [])]
    if base_axis in ('value', 'any'):
        fields += [f for f in ('value', 'amount') if f in spec and f not in fields]
    if base_axis in ('date', 'any'):
        fields += [f for f in ('date', 'performed_on', 'done_on', 'due_on') if f in spec and f not in fields]
    for field in fields:
        if field == 'trend' or field not in spec:
            continue
        w = want.get(field)
        wanted_opts = None
        if spec[field]['kind'] == 'multi' and w is not None and not all_details:
            wanted_opts = w if isinstance(w, list) else [w]
        value, ok, p, cost = read_detail(predicate, passage, field, wanted_opts, as_of=as_of, ledger=ledger, allow_live=allow_live,
                                         decide=decide, cal=cal)
        reading['cost_usd'] += cost
        if value is not None and value != []:
            reading['detail'][field] = value
        if not ok:
            reading['detail_undecided'].append(field)
    return reading


# -- combining ---------------------------------------------------------------------------------------------------------
def _matches(predicate, r):
    """accepted reading satisfies status+detail -> True/False; None when a constrained detail is undecided."""
    if r.get('undecided') or r['status'] is None:
        return None
    if r['status'] not in predicate['statuses']:
        return False
    want = {k: v for k, v in (predicate.get('detail') or {}).items() if k != 'trend'}
    if predicate['frame'] == 'note_contradiction' or not want:
        return True
    if r['status'] not in note_frames.POSITIVE:
        return True  # a denied/other_person/not_mentioned status carries no detail to check
    if note_frames.detail_matches(predicate['frame'], want, r['detail']):
        return True
    if any(f in r['detail_undecided'] for f in want):
        return None
    return False


def _rank(r):
    return note_frames.PRECEDENCE.index(r['status']) if r.get('status') in note_frames.PRECEDENCE else 99


def _ev(r, supports):
    return {'record_id': r['record_id'], 'date': r['date'], 'note_type': r['note_type'], 'source': r['source'], 'quote': r['quote'],
            'start': r['start'], 'end': r['end'], 'status': r['status'], 'p': None if r['p'] is None else round(r['p'], 4),
            'detail': r['detail'], 'supports': supports, 'duplicates': r.get('duplicates', [])}


def _num(x):
    try:
        return float(str(x).replace('%', ''))
    except ValueError:
        return None


def combine(predicate, readings):
    """Reduce passage readings to one patient value. -> {status, detail, evidence, p, holds, undecided, combine}."""
    rule = predicate['combine']
    statuses = predicate['statuses']
    undecided = [r for r in readings if r.get('undecided')]
    seen = [r for r in readings if not r.get('undecided') and r['status'] != 'not_mentioned']  # passages that address it
    out = {'combine': rule, 'undecided': len(undecided), 'passages_read': len(readings)}

    def done(status, holds, p, detail, evid):
        return {**out, 'status': status, 'holds': holds, 'p': None if p is None else round(p, 4), 'detail': detail, 'evidence': evid}

    if not readings:
        return done('not_mentioned', 'not_mentioned' in statuses, None, {}, [])
    if rule == 'any':
        hits = [(r, _matches(predicate, r)) for r in seen]
        win = sorted([r for r, m in hits if m is True], key=lambda r: (-(r['p'] or 0), _rank(r)))
        if win:
            return done(win[0]['status'], True, max(r['p'] or 0 for r in win), win[0]['detail'], [_ev(r, True) for r in win])
        if undecided or any(m is None for _, m in hits):
            return done('unknown', None, None, {}, [_ev(r, False) for r in seen])
        if not seen:
            return done('not_mentioned', 'not_mentioned' in statuses, min((r['p'] or 0) for r in readings), {}, [])
        top = sorted(seen, key=lambda r: (_rank(r), -(r['p'] or 0)))[0]
        return done(top['status'], False, min(r['p'] or 0 for r in seen), top['detail'], [_ev(r, False) for r in seen])
    if rule in ('latest', 'first'):
        dated = sorted(seen, key=lambda r: (r['date'] or '', -_rank(r)))
        if not dated:
            if undecided:
                return done('unknown', None, None, {}, [])
            return done('not_mentioned', 'not_mentioned' in statuses, min((r['p'] or 0) for r in readings), {}, [])
        pick = dated[-1] if rule == 'latest' else dated[0]
        later = [u for u in undecided if (u['date'] or '') >= (pick['date'] or '')] if rule == 'latest' else \
                [u for u in undecided if (u['date'] or '') <= (pick['date'] or '')]
        m = _matches(predicate, pick)
        if later or m is None:
            return done('unknown', None, None, {}, [_ev(r, False) for r in dated])
        return done(pick['status'], m, pick['p'], pick['detail'], [_ev(r, r is pick and m) for r in dated])
    if rule == 'all':
        ms = [(r, _matches(predicate, r)) for r in seen]
        if any(m is False for _, m in ms):
            bad = [r for r, m in ms if m is False]
            return done(bad[0]['status'], False, min(r['p'] or 0 for r in bad), bad[0]['detail'], [_ev(r, m is True) for r, m in ms])
        if not seen or undecided or any(m is None for _, m in ms):
            return done('unknown', None, None, {}, [_ev(r, False) for r in seen]) if (undecided or any(m is None for _, m in ms)) \
                else done('not_mentioned', 'not_mentioned' in statuses, None, {}, [])
        return done(ms[0][0]['status'], True, min(r['p'] or 0 for r in seen), ms[0][0]['detail'], [_ev(r, True) for r in seen])
    if rule == 'conflict':
        axis = (predicate.get('detail') or {}).get('axis', 'any')
        pos = [r for r in seen if r['status'] in ('documented', 'historical')]
        neg = [r for r in seen if r['status'] == 'denied']
        diff = False
        for f in ('value', 'amount', 'date', 'performed_on', 'done_on', 'due_on'):
            vals = {str(r['detail'][f]) for r in pos if f in r['detail']}
            if len(vals) > 1 and axis in ('any', 'value' if f in ('value', 'amount') else 'date'):
                diff = True
        by_status = bool(pos and neg) and axis in ('any', 'status')
        found = by_status or diff
        if found:
            ev = pos + neg
            return done('documented', True, min(r['p'] or 0 for r in ev), {'conflict': True, 'by': 'status' if by_status else 'value'},
                        [_ev(r, True) for r in ev])
        if undecided:
            return done('unknown', None, None, {}, [_ev(r, False) for r in seen])
        return done('not_mentioned', 'not_mentioned' in statuses, min([r['p'] or 0 for r in seen] or [None]) if seen else None,
                    {'conflict': False}, [_ev(r, False) for r in seen])
    if rule == 'trend':
        pts = sorted(((r['date'], _num(r['detail'].get('value')), r) for r in seen if r['status'] == 'documented'
                      and _num(r['detail'].get('value')) is not None), key=lambda t: t[0] or '')
        if len(pts) < 2:
            if undecided or any(r['detail_undecided'] for r in seen):
                return done('unknown', None, None, {}, [_ev(r, False) for r in seen])
            return done('not_mentioned', 'not_mentioned' in statuses, None, {'trend': None, 'n': len(pts)}, [_ev(p[2], False) for p in pts])
        first, last = pts[0][1], pts[-1][1]
        rel = (last - first) / abs(first) if first else (last - first)
        direction = 'stable' if abs(rel) < 0.05 else 'increased' if rel > 0 else 'decreased'
        wanted = (predicate.get('detail') or {}).get('trend')
        wanted = wanted if isinstance(wanted, list) else [wanted] if wanted else None
        holds = True if not wanted else direction in wanted
        if undecided:
            holds = None if holds is not False else holds
        return done('documented', holds, min(p[2]['p'] or 0 for p in pts),
                    {'trend': direction, 'first': first, 'last': last, 'delta': round(last - first, 4), 'n': len(pts)},
                    [_ev(p[2], True) for p in pts])
    raise ValueError(rule)


# -- the executor ------------------------------------------------------------------------------------------------------
def _ledger_arg(ledger):
    return str(getattr(ledger, 'path', ledger)) if ledger is not None else None


def evaluate(stack, predicate, patient_ids, *, as_of, ledger=None, workers=4, allow_live=True, all_details=False,
             decide=None, max_passages=None):
    """See note_frames.evaluate. `predicate` is already validated. Passage reads run concurrently (workers, at most 4 so
    Perplexity's 429s stay rare); each worker opens the ledger by path."""
    as_of = str(as_of)[:10]
    path = _ledger_arg(ledger)
    patterns = note_evidence.concept_patterns(predicate['concept'])
    cal = load_calibration()
    db = note_evidence.connect(stack)
    window = predicate.get('window')
    base = predicate if predicate['frame'] != 'note_contradiction' else predicate
    jobs = []
    for pid in patient_ids:
        ps = note_evidence.retrieve(db, pid, predicate['concept'], window=window, sources=predicate.get('sources'), as_of=as_of,
                                    patterns=patterns, **({'max_passages': max_passages} if max_passages else {}))
        jobs += [(pid, p) for p in ps]
    db.close()

    def run(job):
        pid, passage = job
        return pid, read_passage(base, passage, as_of=as_of, ledger=path, allow_live=allow_live, all_details=all_details,
                                 decide=decide, cal=cal)
    workers = max(1, min(int(workers), 4))
    if workers == 1 or len(jobs) <= 1:
        results = [run(j) for j in jobs]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(run, jobs))
    by = {pid: [] for pid in patient_ids}
    for pid, r in results:
        by[pid].append(r)
    out = {}
    for pid in patient_ids:
        c = combine(predicate, by[pid])
        c['cost_usd'] = round(sum(r['cost_usd'] for r in by[pid]), 6)
        c['retrieved'] = len(by[pid])
        out[pid] = c
    return out
