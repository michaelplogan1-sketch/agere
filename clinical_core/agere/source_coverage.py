"""Source coverage gate: every received stream must be mapped, read by an engine, or declared unused after review.

A stream Agere ingests but nothing reads is invisible: the v2.3 blind set answered 0 Medicare annual wellness visits
(truth 202) because the new wellness_visit_services stream was never mapped, and PHQ-9 in mood_questionnaires was missed
the same way. Each stream of a build's source profile is classified:

  mapped     (a) the semantic layer maps it into typed tables or a concept/metric binding: it is named in the semantic
                 layer modules, the concept library's stream maps, config/agere/semantic_model.json or a governed metric;
  engine     (b) an engine reads it directly by name (the modules / configs that name it are listed);
  declared   (c) config/agere/unmapped_streams.json declares it unused, with a reason, a reviewer and a review date;
  none       none of these: listed with its row and patient counts.

Classification is static over the repository (the live tree, or a git revision for a replay): a stream counts as named
when its exact name appears as a quoted literal. Ingestion adapters and generic catalogs that list every table
(INGESTION below) do not count: copying a stream into the record store is not reading it. The pinned received-source
catalog (config/agere/received-source-catalog-v123.json) does count as an engine binding: its tables are typed record
atoms for cohorts and qualified received-source accounts.
The gate is informational until unmapped_streams.json sets "enforce": true (or a caller passes enforce=True); then any
stream in state none fails activation with the list.
"""
from __future__ import annotations

import json, re, subprocess
from pathlib import Path

VERSION = 'source-coverage-gate/1'
ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'config' / 'agere' / 'unmapped_streams.json'
# (a) the semantic layer and the bindings planners and SQL writers see
MAPPED = ('clinical_core/agere/clinical/semantic_layer.py', 'clinical_core/agere/clinical/population_feeds.py',
          'clinical_core/agere/clinical/note_facts.py', 'clinical_core/agere/clinical/concepts.py',
          'config/agere/semantic_model.json', 'config/agere/metrics/')
# (b) engines: everything else under these roots
ENGINES = ('clinical_core/agere/', 'config/agere/')
# never evidence of reading: ingestion adapters, generic copies/profiles, question libraries, this guard family
INGESTION = ('clinical_core/agere/healthcare_warehouse', 'clinical_core/agere/warehouse.py', 'clinical_core/agere/warehouse_retraction.py',
             'clinical_core/agere/warehouse_activation.py', 'clinical_core/agere/local_release.py', 'clinical_core/agere/portable_source.py',
             'clinical_core/agere/clinical/record_store.py', 'clinical_core/agere/clinical/source_profile.py',
             'clinical_core/agere/onboarding/', 'clinical_core/agere/source_coverage.py', 'clinical_core/agere/release_diff.py',
             'clinical_core/agere/verified_carry_forward.py', 'config/agere/healthcare_questions.json', 'config/agere/vbc_questions.json',
             'config/agere/active_warehouse.json', 'config/agere/activation-history/',
             'config/agere/unmapped_streams.json', 'config/agere/benchmarks.json', 'config/agere/warehouse_v20.json')
SUFFIXES = ('.py', '.json', '.sql')


def _files(rev=None, root=ROOT):
    """{path: text} of the scanned repository files (working tree, or a git revision)."""
    out = {}
    if rev is None:
        for base in ENGINES:
            for p in (root / base).rglob('*'):
                rel = p.relative_to(root).as_posix()
                if p.is_file() and p.suffix in SUFFIXES and not rel.startswith(INGESTION):
                    try: out[rel] = p.read_text(errors='ignore')
                    except OSError: pass
        return out
    names = subprocess.run(['git', '-C', str(root), 'ls-tree', '-r', '--name-only', rev, '--', *ENGINES],
                           capture_output=True, text=True, check=True).stdout.split()
    for rel in names:
        if rel.endswith(SUFFIXES) and not rel.startswith(INGESTION):
            out[rel] = subprocess.run(['git', '-C', str(root), 'show', f'{rev}:{rel}'], capture_output=True, text=True,
                                      errors='ignore').stdout
    return out


def load_declarations(path=CONFIG):
    """(enforce, {stream: entry}) - only complete entries (reason, reviewer, reviewed_on) count as declared."""
    path = Path(path)
    if not path.exists(): return False, {}, {}
    data = json.loads(path.read_text())
    valid, invalid = {}, {}
    for stream, e in (data.get('streams') or {}).items():
        ok = isinstance(e, dict) and all(str(e.get(k) or '').strip() for k in ('reason', 'reviewer', 'reviewed_on')) \
            and re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(e.get('reviewed_on')))
        (valid if ok else invalid)[stream] = e
    return bool(data.get('enforce')), valid, invalid


def classify(profile, files, declared):
    """{stream: {'state', 'records', 'patients', 'mapped_by', 'read_by', 'declared'}} for every profile stream."""
    out = {}
    for s, st in sorted(profile['streams'].items()):
        rx = re.compile(r"""['"]""" + re.escape(s) + r"""['"]""")
        hits = [f for f, text in files.items() if rx.search(text)]
        mapped = sorted(f for f in hits if f.startswith(MAPPED))
        engines = sorted(f for f in hits if f not in mapped)
        state = 'mapped' if mapped else 'engine' if engines else 'declared' if s in declared else 'none'
        out[s] = {'state': state, 'records': int(st.get('records') or 0),
                  'patients': int(((st.get('fields') or {}).get('patient_id') or {}).get('distinct') or 0),
                  'mapped_by': mapped, 'read_by': engines[:12], 'declared': declared.get(s)}
    return out


def check(build, *, rev=None, config=CONFIG, enforce=None, root=ROOT):
    """Coverage report for a build (read-only: its saved source-profile.json). enforce=None follows the config file."""
    stack = Path(build) / 'stack' if (Path(build) / 'stack' / 'run.json').exists() else Path(build)
    profile = json.loads((stack / 'source-profile.json').read_text())
    cfg_enforce, declared, invalid = load_declarations(config)
    enforce = cfg_enforce if enforce is None else enforce
    streams = classify(profile, _files(rev, root), declared)
    none = {s: v for s, v in streams.items() if v['state'] == 'none'}
    counts = {k: sum(v['state'] == k for v in streams.values()) for k in ('mapped', 'engine', 'declared', 'none')}
    stale = sorted(s for s in declared if s in streams and streams[s]['state'] != 'declared')
    # read only by an engine: the semantic layer (what the SQL writer and the compiler see) does not have it
    engine_only = sorted(({'stream': s, 'records': v['records'], 'read_by': v['read_by'][:4]} for s, v in streams.items()
                          if v['state'] == 'engine'), key=lambda x: -x['records'])
    return {'schema': VERSION, 'build': str(stack.parent), 'code': rev or 'working tree', 'enforce': enforce,
            'passed': not none or not enforce, 'counts': counts,
            'unread': sorted(({'stream': s, 'records': v['records'], 'patients': v['patients']} for s, v in none.items()),
                             key=lambda x: -x['records']),
            'unread_records': sum(v['records'] for v in none.values()),
            'invalid_declarations': sorted(invalid), 'declared_but_read': stale, 'engine_only_not_in_semantic_layer': engine_only,
            'streams': streams}


def message(report):
    u = report['unread']
    head = (f"Source coverage: {report['counts']['mapped']} mapped, {report['counts']['engine']} read by engines, "
            f"{report['counts']['declared']} declared unused, {len(u)} in none ({report['unread_records']:,} rows ingested but ignored)")
    head += (f"; {len(report['engine_only_not_in_semantic_layer'])} more are read only by an engine, not mapped into the "
             f"semantic layer the SQL writer sees")
    if not u: return head
    lines = [head + (' - activation blocked.' if not report['passed'] else ' - informational (unmapped_streams.json enforce=false).'),
             'Map each stream into the semantic layer, read it in an engine, or declare it in config/agere/unmapped_streams.json '
             '(reason, reviewer, reviewed_on):']
    lines += [f"  {x['stream']}: {x['records']:,} rows, {x['patients']:,} patients" for x in u]
    return '\n'.join(lines)


def gate(build, **kw):
    """Raise ValueError with the list when enforced and any stream is in state none; return the report otherwise."""
    report = check(build, **kw)
    if not report['passed']: raise ValueError(message(report))
    return report
