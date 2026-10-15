"""Carry verified question -> SQL pairs from the prior stack to a new build, re-verified on the new release.

  python tools/carry_forward_verified.py --from artifacts/agere-healthcare-v123 --to artifacts/agere-healthcare-v23-sample-0.35 \
      --out /tmp/scratch/carry-v123-v23          # dry run into a scratch store (the real stores are only read)
  python tools/carry_forward_verified.py --from PRIOR --to NEW                 # write into NEW/stack/learning.sqlite3
  python tools/carry_forward_verified.py --from PRIOR --to NEW --replay-only   # count only, write nothing

Every prior verified pair is re-linted, re-run and re-checked (answer checks + deterministic oracles) on the new
release; passing pairs are carried with provenance, the rest quarantined with reasons (see
clinical_core/agere/verified_carry_forward.py). A JSON summary is written next to the store (carry-forward.json).
"""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clinical_core.agere import verified_carry_forward as cf  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--from', dest='prior', required=True, type=Path, help='prior build or stack (its learning.sqlite3 is read only)')
    p.add_argument('--to', dest='new', required=True, type=Path, help='new build or stack (read only unless --out is omitted)')
    p.add_argument('--out', type=Path, help='directory for the resulting learning.sqlite3 (scratch); default: the new stack')
    p.add_argument('--replay-only', action='store_true', help='replay and report; write no store')
    p.add_argument('--limit', type=int, help='replay only the first N pairs (oldest first)')
    p.add_argument('--shard', help='I/N: replay question slice I of N (run N processes, each with its own --out)')
    a = p.parse_args(argv)
    summary = cf.carry_forward(a.prior, a.new, a.out, limit=a.limit, dry_run=a.replay_only,
                               shard=tuple(map(int, a.shard.split('/'))) if a.shard else None,
                               progress=lambda i, n: print(f'  {i}/{n}', file=sys.stderr, flush=True))
    target = a.out or cf._stack(a.new)
    if not a.replay_only:
        (Path(target) / 'carry-forward.json').write_text(json.dumps(summary, indent=1, default=str) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k != 'outcomes'}, indent=1, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
