"""Release-diff guard: compare a candidate warehouse build with the prior active build before activation.

  python tools/release_diff_guard.py --candidate artifacts/agere-healthcare-v28            # prior = the active build
  python tools/release_diff_guard.py --candidate B --prior P --out DIR                      # explicit prior, output dir
  python tools/release_diff_guard.py --candidate B --gate [--accept FILE]                   # exit 2 on unacknowledged flags
  python tools/release_diff_guard.py --candidate B --prior P --concepts-rev 3882c76 --resolver pre-c3c6f59   # replay

Writes <out>/release-diff.json and <out>/release-diff.md (out defaults to the candidate build). Read-only on both
builds' stacks (source-profile.json, semantic.sqlite3). Informational unless --gate: then flags not listed in the
--accept file ({"accepted": [finding id or kind, ...]}) fail the step. See clinical_core/agere/release_diff.py.
"""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clinical_core.agere import release_diff  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--candidate', required=True, type=Path, help='candidate build (directory holding stack/)')
    p.add_argument('--prior', type=Path, help='prior build; default: the build in config/agere/active_warehouse.json')
    p.add_argument('--config', type=Path, default=ROOT / 'config/agere/active_warehouse.json')
    p.add_argument('--out', type=Path, help='output directory (default: the candidate build directory)')
    p.add_argument('--gate', action='store_true', help='exit 2 when flags remain that the --accept file does not acknowledge')
    p.add_argument('--accept', type=Path, help='JSON {"accepted": [finding ids or kinds]} reviewed and accepted')
    p.add_argument('--tolerance', type=float, default=release_diff.TOLERANCE)
    p.add_argument('--min-patients', type=int, default=release_diff.MIN_PATIENTS)
    p.add_argument('--concepts-rev', help='git revision whose concepts.py defines the families (default: live code)')
    p.add_argument('--resolver', default='live', choices=sorted(release_diff.RESOLVERS),
                   help='field-form take order (default: the live compiler; pre-c3c6f59 replays older builds)')
    p.add_argument('--segments', default='auto', choices=['auto', 'identity', 'roster'],
                   help='identity: compare patients present in both releases; roster: whole rosters (use for a regenerated '
                        'population / --rebaseline); auto: identity when half the prior roster is still present')
    a = p.parse_args(argv)
    prior = a.prior or Path(json.loads(a.config.read_text())['build'])
    report, g = release_diff.run(a.candidate, prior, a.out or a.candidate, accept_file=a.accept, gate_on=a.gate,
                                 concepts_rev=a.concepts_rev, resolver=a.resolver, tolerance=a.tolerance,
                                 min_patients=a.min_patients, segment_mode=a.segments)
    print(release_diff.markdown(report, g if a.gate else None, limit=25))
    print(f"written: {(a.out or a.candidate) / 'release-diff.json'}")
    return 2 if a.gate and not g['passed'] else 0


if __name__ == '__main__':
    sys.exit(main())
