"""Write the sealed v2.8 evaluation scorer's prediction files from an Agere build, using Agere's own engines.

Every file is produced by an Agere module reading the build's semantic layer (<build>/stack/semantic.sqlite3) and record
store (<build>/stack/stack.sqlite3); nothing reads the generator's private/ folder, and nothing writes into the build.
Deterministic and local: no network, no model calls.

  conditions.jsonl        chronic_conditions (documented, CCW claims-confirmation rule) + condition_suspects (labs, vitals,
                          fills; ADA / KDIGO / ACC-AHA / ATA criteria; builds on risk_adjustment's suspect engine)
  hcc.jsonl               hcc_capture (risk_adjustment V28 crosswalk + hierarchies; chronic recapture; suspects)
  quality_measures.jsonl  measures library engine (config/agere/measures: bcs, col, ked, spd_statin_diabetes,
                          gsd_control_lt8, cbp, pdc_diabetes, pdc_rasa, pdc_statins), per patient and calendar year
  acute_events.jsonl      acute_episodes (semantic utilization_event + CMS Planned Readmission Algorithm structure)
  medications.jsonl       therapy_courses (line_of_therapy fills, episodes, PQA PDC, regimen events)
  deaths.jsonl            death records + payer enrollment terminations for death (semantic record / enrollment_span)
  duplicates.jsonl        person_linkage (deterministic member-ID + Fellegi-Sunter demographic linkage)
  sdoh.jsonl              sdoh_needs (corroborated: Z code, screens, notes, vendor)
plus provenance.json (module, version and source sha256 per file; parameters; build identity; row counts; runtimes)
and validation.json (contract check of the written files).

Usage:
  .venv/bin/python tools/eval_predictions.py --build artifacts/<agere build> --out <dir> [--as-of 2030-05-27]
      [--years 2027-2029] [--only conditions,hcc,...] [--config params.json] [--condition-min-claims N]
      [--sdoh-min-score N] [--link-threshold BITS] [--all-segments]
  .venv/bin/python tools/eval_predictions.py --validate <dir> [--build <build>]
"""
import argparse, hashlib, json, sqlite3, subprocess, sys, time
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clinical_core.agere.clinical import (acute_episodes, chronic_conditions, condition_suspects, eval_contract, hcc_capture, line_of_therapy,
                                          measures, person_linkage, risk_adjustment, sdoh_needs, semantic_layer, therapy_courses)

VERSION = 'agere-eval-predictions/1'
MEASURE_IDS = {'BCS': 'bcs', 'COL': 'col', 'KED': 'ked', 'SPD': 'spd_statin_diabetes', 'HBD': 'gsd_control_lt8', 'CBP': 'cbp',
               'PDC-DR': 'pdc_diabetes', 'PDC-RASA': 'pdc_rasa', 'PDC-STA': 'pdc_statins'}
DEFAULTS = {
    'feed_start': '2026-09-01',            # population data start (EVAL_SCORER.md)
    'years': [2027, 2028, 2029],           # population truth years
    'population_only': ['hcc', 'quality_measures', 'acute_events', 'medications', 'sdoh'],  # tasks whose truth is the population
    'deep_chart_segment': 'deep_chart',
    'conditions': dict(chronic_conditions.DEFAULTS),
    'suspects': dict(condition_suspects.SUSPECT_DEFAULTS),
    'hcc': dict(hcc_capture.DEFAULTS),
    'acute': {'orientation': 'readmission'},
    'therapy': dict(therapy_courses.DEFAULTS),
    'deaths': {'sources': ['death_records', 'enrollment_death']},
    'linkage': dict(person_linkage.DEFAULTS),
    'sdoh': dict(sdoh_needs.DEFAULTS),
}
PRODUCERS = {
    'conditions': [chronic_conditions, condition_suspects, risk_adjustment],
    'hcc': [hcc_capture, chronic_conditions, condition_suspects, risk_adjustment],
    'quality_measures': [measures],
    'acute_events': [acute_episodes],
    'medications': [therapy_courses, line_of_therapy],
    'deaths': [],
    'duplicates': [person_linkage],
    'sdoh': [sdoh_needs, chronic_conditions],
}


# ---- build access (read-only) ---------------------------------------------------------------------------------------------
def stack_dir(build):
    b = Path(build)
    return b / 'stack' if (b / 'stack' / 'semantic.sqlite3').exists() else b


def open_ro(path):
    try:
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True); db.execute('SELECT count(*) FROM sqlite_master').fetchone(); return db
    except sqlite3.Error:
        return sqlite3.connect(f'file:{path}?immutable=1', uri=True)


def semantic(build):
    target = stack_dir(build) / 'semantic.sqlite3'
    if not target.exists(): raise SystemExit(f'no semantic layer at {target} (this tool never builds one)')
    db = open_ro(target)
    meta = dict(db.execute('SELECT key, value FROM meta').fetchall())
    return db, meta


def _merge(a, b):
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = _merge(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) else v
    return out


# ---- shared inputs ---------------------------------------------------------------------------------------------------------
def load_people(db):
    people = {}
    for pid, birth, sex, age, seg in db.execute('SELECT patient_id, birth_date, sex_reported, age_reported, data_segment FROM patient'):
        people[pid] = {'birth_date': birth, 'sex': 2 if str(sex or '').upper().startswith('F') else 1, 'age': age, 'segment': seg}
    return people


def load_deaths(db, sources):
    deaths = {}
    if 'death_records' in sources:
        for pid, d in db.execute("SELECT patient_id, json_extract(payload,'$.date_of_death') FROM record WHERE stream = 'death_records' ORDER BY event_date"):
            if d and pid not in deaths: deaths[pid] = str(d)[:10]
    if 'enrollment_death' in sources:
        for pid, end in db.execute("SELECT patient_id, max(coverage_end) FROM enrollment_span WHERE termination_reason = 'death' GROUP BY 1"):
            if end and pid not in deaths and end[:4] != '9999': deaths[pid] = end[:10]
    return deaths


def enrolled_years(db, years):
    out = defaultdict(set)
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'member_month' in tables:
        for pid, y in db.execute('SELECT DISTINCT patient_id, CAST(substr(month,1,4) AS INTEGER) FROM member_month'):
            if y in years: out[pid].add(y)
    return out


# ---- producers -------------------------------------------------------------------------------------------------------------
def conditions_rows(ctx):
    doc, sus = ctx['documented'], ctx['suspects']
    rows = []
    for pid in sorted(set(doc) | set(sus)):
        for cat in sorted(doc.get(pid, {})):
            rows.append({'patient_id': pid, 'icd10': cat, 'basis': 'documented'})
        for s in sus.get(pid, []):
            rows.append({'patient_id': pid, 'icd10': s['icd10'], 'basis': 'suspect'})
    return rows


def hcc_rows(ctx):
    cfg = ctx['cfg']
    res = hcc_capture.capture(ctx['dx'], ctx['people'], cfg['years'], cfg['feed_start'], ctx['suspects'], ctx['enrolled'], ctx['deaths'], cfg['hcc'])
    ctx['stats']['hcc'] = {'patient_years': len(res), 'recaptured_hccs': sum(len(v['recaptured']) for v in res.values()),
                           'suspected_hccs': sum(len(v['suspected']) for v in res.values()),
                           'hccs_not_on_paid_claims': sum(len(set(v['hccs']) - set(v['claims_hccs'])) for v in res.values())}
    return [{'patient_id': pid, 'year': y, 'hccs': v['hccs']} for (pid, y), v in sorted(res.items()) if ctx['in_scope']('hcc', pid)]


def quality_rows(ctx):
    db, cfg = ctx['db'], ctx['cfg']
    lib = measures.library()
    mctx = measures.Context(db)
    rows, declined = [], {}
    for y in cfg['years']:
        start, end = f'{y}-01-01', f'{y}-12-31'
        people = measures._people(db, ctx['as_of'], start, end, mctx)
        for name, mid in MEASURE_IDS.items():
            status, note = measures.patient_status(db, lib[mid], start, end, ctx['as_of'], ctx=mctx, people={k: dict(v) for k, v in people.items()})
            if status is None: declined[f'{name} {y}'] = note; continue
            for r in status:
                if not ctx['in_scope']('quality_measures', r['patient_id']): continue
                row = {'patient_id': r['patient_id'], 'year': y, 'measure': name, 'eligible': not r['excluded'], 'met': bool(r['met'])}
                if 'pdc' in r and r['pdc'] is not None: row['pdc'] = min(1.0, r['pdc'])
                rows.append(row)
    ctx['stats']['quality_measures'] = {'declined': declined}
    return rows


def acute_rows(ctx):
    cfg = ctx['cfg']
    lo, hi = f"{min(cfg['years'])}-01-01", f"{max(cfg['years'])}-12-31"
    out = []
    for e in acute_episodes.episodes(ctx['db'], lo, hi, cfg['acute']['orientation']):
        if not ctx['in_scope']('acute_events', e['patient_id']): continue
        r = {'patient_id': e['patient_id'], 'kind': e['kind'], 'start': e['start']}
        if e['kind'] == 'inpatient':
            r['planned'] = bool(e['planned']); r['unplanned_readmission_30d'] = bool(e['unplanned_readmission_30d'])
        out.append(r)
    seen, rows = set(), []
    for r in out:  # one event per patient, kind and start date
        k = (r['patient_id'], r['kind'], r['start'])
        if k not in seen: seen.add(k); rows.append(r)
    return rows


def medication_rows(ctx):
    cfg = ctx['cfg']
    rows = therapy_courses.courses(ctx['db'], cfg['years'], ctx['as_of'], cfg['feed_start'], ctx['deaths'], cfg['therapy'])
    out = []
    for r in rows:
        if r['pdc'] is None or not ctx['in_scope']('medications', r['patient_id']): continue
        x = {'patient_id': r['patient_id'], 'drug_class': r['drug_class'], 'year': r['year'], 'pdc': min(1.0, r['pdc'])}
        if r.get('therapy_event'): x['therapy_event'] = r['therapy_event']; x['first_fill'] = r['first_fill']
        out.append(x)
    return out


def death_rows(ctx):
    return [{'patient_id': p, 'date_of_death': d} for p, d in sorted(ctx['deaths'].items())]


def duplicate_rows(ctx):
    people = person_linkage.load_identities(ctx['stack'], ctx['db'])
    assign, report, links = person_linkage.link(people, ctx['cfg']['linkage'])
    ctx['stats']['duplicates'] = {k: v for k, v in report.items() if k not in ('parameters',)}
    return [{'patient_id': p, 'person_id': g} for p, g in sorted(assign.items())]


def sdoh_rows(ctx):
    res = sdoh_needs.needs(ctx['db'], ctx['dx'], ctx['cfg']['sdoh'])
    return [{'patient_id': p, 'needs': sorted(n)} for p, n in sorted(res.items()) if ctx['in_scope']('sdoh', p)]


WRITERS = {'conditions': conditions_rows, 'hcc': hcc_rows, 'quality_measures': quality_rows, 'acute_events': acute_rows,
           'medications': medication_rows, 'deaths': death_rows, 'duplicates': duplicate_rows, 'sdoh': sdoh_rows}


# ---- provenance ------------------------------------------------------------------------------------------------------------
def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_identity(modules):
    files = [Path(m.__file__).resolve() for m in modules] + [Path(__file__).resolve()]
    rel = [str(f.relative_to(ROOT)) for f in files]
    try:
        head = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(['git', '-C', str(ROOT), 'status', '--porcelain', '--', *rel], capture_output=True, text=True, timeout=10).stdout.split('\n')
        dirty = sorted(x[3:] for x in dirty if x.strip())
    except (OSError, subprocess.SubprocessError):
        head, dirty = None, None
    return {'git_head': head, 'uncommitted_files': dirty,
            'modules': {m.__name__: {'version': getattr(m, 'VERSION', None), 'file': str(Path(m.__file__).resolve().relative_to(ROOT)),
                                     'sha256': _sha(m.__file__)} for m in modules},
            'tool': {'version': VERSION, 'file': str(Path(__file__).resolve().relative_to(ROOT)), 'sha256': _sha(__file__)}}


# ---- main ------------------------------------------------------------------------------------------------------------------
def export(build, out, as_of=None, only=None, cfg=None, log=print):
    cfg = _merge(DEFAULTS, cfg or {})
    t0 = time.time()
    db, meta = semantic(build)
    if meta.get('version') != semantic_layer.VERSION:
        log(f"warning: semantic layer {meta.get('version')} differs from code {semantic_layer.VERSION}; reading it as built")
    as_of = as_of or meta.get('as_of') or date.today().isoformat()
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    wanted = [n for n in eval_contract.FILES if not only or n in only]
    people = load_people(db)
    pop_only = set(cfg['population_only'])

    def in_scope(task, pid):
        return task not in pop_only or (people.get(pid) or {}).get('segment') != cfg['deep_chart_segment']
    ctx = {'db': db, 'cfg': cfg, 'as_of': as_of[:10], 'stack': stack_dir(build), 'people': people, 'in_scope': in_scope, 'stats': {}}
    timings = {}
    t = time.time()
    ctx['deaths'] = load_deaths(db, cfg['deaths']['sources'])
    if set(wanted) & {'conditions', 'hcc', 'sdoh'}:
        ctx['dx'] = list(chronic_conditions.diagnosis_rows(db, '0000-01-01', as_of[:10]))
        ctx['documented'] = chronic_conditions.documented(ctx['dx'], cfg['conditions']['min_claim_dates'])
        any_code = chronic_conditions.documented(ctx['dx'], 1)  # suspects are suppressed by ANY documented code, confirmed or not
        ages = {}
        for pid, info in people.items():
            ages[pid] = hcc_capture._age(info['birth_date'], date.fromisoformat(as_of[:10])) if info['birth_date'] else (info['age'] or 70)
        ctx['suspects'] = condition_suspects.suspects(db, sorted(people), {p: set(c) for p, c in any_code.items()}, cfg['feed_start'],
                                                      as_of[:10], cfg['suspects'], ages, {p: i['sex'] for p, i in people.items()})
        ctx['enrolled'] = enrolled_years(db, cfg['years'])
        timings['shared_diagnoses_and_suspects'] = round(time.time() - t, 1)
        ctx['stats']['shared'] = {'diagnosis_rows': len(ctx['dx']), 'patients_with_suspects': len(ctx['suspects']),
                                  'suspects_by_condition': dict(sorted(_count(s['condition'] for v in ctx['suspects'].values() for s in v).items()))}
    counts = {}
    for name in wanted:
        t = time.time()
        rows = WRITERS[name](ctx)
        with (out / f'{name}.jsonl').open('w') as fh:
            for r in rows: fh.write(json.dumps(r, sort_keys=True) + '\n')
        counts[name] = len(rows)
        timings[name] = round(time.time() - t, 1)
        log(f'{name:18s} {len(rows):>9,d} rows  {timings[name]:>7.1f}s')
    run_meta = {'semantic_version': meta.get('version'), 'source_digest': meta.get('source_digest'), 'as_of': meta.get('as_of')}
    conn = Path(build) / 'connection.json'
    if conn.exists():
        c = json.loads(conn.read_text())
        run_meta['release'] = c.get('release'); run_meta['source_sha256'] = ((c.get('snapshot') or {}).get('source_dataset') or {}).get('source_sha256')
    validation = eval_contract.validate_dir(out, set(people))
    (out / 'validation.json').write_text(json.dumps(validation, indent=1))
    prov = {'version': VERSION, 'contract': eval_contract.VERSION, 'build': str(Path(build).resolve()), 'build_identity': run_meta,
            'as_of': as_of[:10], 'parameters': cfg, 'row_counts': counts, 'runtime_seconds': {**timings, 'total': round(time.time() - t0, 1)},
            'producers': {n: [m.__name__ for m in PRODUCERS[n]] or ['tools.eval_predictions (semantic record / enrollment_span)'] for n in wanted},
            'statistics': ctx['stats'], 'code': code_identity(sorted({m for n in wanted for m in PRODUCERS[n]}, key=lambda m: m.__name__)),
            'deterministic': True, 'paid_api_calls': 0, 'reads_private': False, 'validation_ok': validation['ok']}
    (out / 'provenance.json').write_text(json.dumps(prov, indent=1, default=str))
    db.close()
    log(f"{'total':18s} {sum(counts.values()):>9,d} rows  {time.time() - t0:>7.1f}s   validation {'OK' if validation['ok'] else 'FAILED'}")
    return prov, validation


def _count(it):
    out = defaultdict(int)
    for x in it: out[x] += 1
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--build')
    ap.add_argument('--out')
    ap.add_argument('--as-of')
    ap.add_argument('--years', help='e.g. 2027-2029')
    ap.add_argument('--only', help='comma-separated file names (default: all)')
    ap.add_argument('--config', help='JSON file of parameter overrides (same shape as DEFAULTS)')
    ap.add_argument('--condition-min-claims', type=int, help='claims-only categories need this many service dates (CCW default 2)')
    ap.add_argument('--sdoh-min-score', type=int, help='SDOH corroboration score needed (default 2)')
    ap.add_argument('--link-threshold', type=float, help='Fellegi-Sunter link threshold in bits (default 15)')
    ap.add_argument('--all-segments', action='store_true', help='also predict population-only tasks for deep charts')
    ap.add_argument('--validate', help='validate an existing prediction directory and exit')
    a = ap.parse_args(argv)
    if a.validate:
        ids = None
        if a.build:
            db, _ = semantic(a.build); ids = {r[0] for r in db.execute('SELECT patient_id FROM patient')}; db.close()
        rep = eval_contract.validate_dir(a.validate, ids)
        print(json.dumps(rep, indent=1))
        return 0 if rep['ok'] else 1
    if not a.build or not a.out: ap.error('--build and --out are required')
    cfg = json.loads(Path(a.config).read_text()) if a.config else {}
    if a.years:
        lo, _, hi = a.years.partition('-'); cfg['years'] = list(range(int(lo), int(hi or lo) + 1))
    if a.condition_min_claims is not None: cfg.setdefault('conditions', {})['min_claim_dates'] = a.condition_min_claims
    if a.sdoh_min_score is not None: cfg.setdefault('sdoh', {})['min_score'] = a.sdoh_min_score
    if a.link_threshold is not None: cfg.setdefault('linkage', {})['threshold'] = a.link_threshold
    if a.all_segments: cfg['population_only'] = []
    only = set(a.only.split(',')) if a.only else None
    if only and only - set(eval_contract.FILES): ap.error(f'--only takes {eval_contract.FILES}')
    _, validation = export(a.build, a.out, a.as_of, only, cfg)
    return 0 if validation['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
