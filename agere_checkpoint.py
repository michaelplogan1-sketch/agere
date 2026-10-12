"""Create, verify, or restore an offline same-machine Agere checkpoint."""
import argparse
import json
from clinical_core.agere.local_release import ROOT, checkpoint, restore, verify_checkpoint

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest='command', required=True)
    make = commands.add_parser('create')
    make.add_argument('--config', default=str(ROOT / 'config/agere/active_warehouse.json'))
    make.add_argument('--output', required=True)
    check = commands.add_parser('verify'); check.add_argument('checkpoint')
    recover = commands.add_parser('restore'); recover.add_argument('checkpoint'); recover.add_argument('--output', required=True)
    args = p.parse_args()
    if args.command == 'create': result = checkpoint(args.config, args.output)
    elif args.command == 'restore': result = restore(args.checkpoint, args.output)
    else:
        m = verify_checkpoint(args.checkpoint)
        result = {'status': 'verified', 'files': len(m['files']), 'databases': len(m['databases']), 'scope': m['scope']}
    print(json.dumps(result, indent=2))
