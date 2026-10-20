"""Inspect, propose and ground procedural lessons using the existing learning store."""
import argparse
import json
from pathlib import Path

from clinical_core.agere.clinical import procedural_memory as M


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stack', required=True)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('inspect')
    propose = sub.add_parser('propose')
    propose.add_argument('--spec', required=True, help='JSON with context_id, instruction, probes and optional required_fields/supersedes')
    ground = sub.add_parser('ground')
    ground.add_argument('--id', required=True, help='Candidate revision to ground with read-only probes')
    args = p.parse_args(argv)
    if args.command == 'inspect':
        result = {'summary': M.summary(args.stack), 'lessons': M.records(args.stack)}
    elif args.command == 'propose':
        spec = json.loads(Path(args.spec).read_text())
        result = {'id': M.propose(args.stack, **spec), 'status': 'candidate'}
    else:
        result = {'id': M.ground(args.stack, args.id), 'status': 'active',
                  'qualification': 'Eligible only within checked source and family preconditions; not clinical qualification'}
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
