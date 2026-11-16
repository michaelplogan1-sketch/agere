"""Audit source-skill supervision or freeze explicit reviewed family partitions."""
import argparse
import json
from pathlib import Path
from clinical_core.agere.clinical.semantic_learning import audit, freeze


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--stack',type=Path,default=Path('artifacts/agere-population-stack-v3'))
    p.add_argument('--family-policy',type=Path)
    p.add_argument('--output',type=Path)
    args=p.parse_args()
    if bool(args.family_policy)!=bool(args.output):p.error('Freezing requires both --family-policy and --output; omit both for a read-only audit')
    result=freeze(args.stack,args.output,json.loads(args.family_policy.read_text())) if args.output else audit(args.stack)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
