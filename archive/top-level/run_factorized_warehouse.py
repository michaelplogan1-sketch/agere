from pathlib import Path
import argparse
import json
from clinical_core.agere.clinical.reader_warehouse import annotate

ROOT = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--model', type=Path, default=ROOT/'artifacts/agere-evidence-reader-v2')
    p.add_argument('--output', type=Path)
    p.add_argument('--limit', type=int)
    args = p.parse_args()
    if args.limit is not None and args.limit < 1:
        raise ValueError('Positive patient limit required')
    result = annotate(ROOT, args.model,
                      ROOT/'artifacts/agere-clinical-workflows-v2', args.output or args.model/'warehouse', limit=args.limit,
                      progress=lambda event: print(json.dumps(event), flush=True))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
