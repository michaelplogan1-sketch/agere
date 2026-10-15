"""Source coverage gate: list received streams that are neither mapped, read by an engine, nor declared unused.

  python tools/source_coverage_gate.py --build artifacts/agere-healthcare-v28 [--out DIR] [--gate] [--rev GIT_REV]

Writes <out>/source-coverage.json (out defaults to the build). Read-only on the build (its source-profile.json).
Informational unless --gate or config/agere/unmapped_streams.json "enforce": true; then exit 2 when any stream is in
state none. --rev classifies with the code of a git revision (replay of an older build). See clinical_core/agere/source_coverage.py.
"""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clinical_core.agere import source_coverage as SC  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--build', required=True, type=Path)
    p.add_argument('--out', type=Path, help='output directory (default: the build directory)')
    p.add_argument('--gate', action='store_true', help='enforce regardless of the config flag')
    p.add_argument('--config', type=Path, default=SC.CONFIG)
    p.add_argument('--rev', help='classify with the code at this git revision')
    a = p.parse_args(argv)
    report = SC.check(a.build, rev=a.rev, config=a.config, enforce=True if a.gate else None)
    out = a.out or a.build
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / 'source-coverage.json').write_text(json.dumps(report, indent=1, default=str) + '\n')
    print(SC.message(report))
    return 0 if report['passed'] else 2


if __name__ == '__main__':
    sys.exit(main())
