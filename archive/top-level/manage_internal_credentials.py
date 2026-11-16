"""Local-only credential provisioning. Writes a secret once to a mode-600 new file.

No HTTP administration route; no clinician identity or role verification. Use a
separate identity-provider-backed authorization layer before any production use.
"""
import argparse
import json
import os
from pathlib import Path
from clinical_core.agere.clinical import internal_execution as execution


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stack', required=True)
    sub = parser.add_subparsers(dest='action', required=True)
    create = sub.add_parser('provision')
    for field in ('principal', 'buyer', 'service', 'expires', 'output'): create.add_argument('--'+field, required=True)
    create.add_argument('--role', action='append', choices=sorted(execution.ROLES), required=True)
    create.add_argument('--identity-kind', choices=['local_operator', 'synthetic_test'], required=True)
    revoke = sub.add_parser('revoke'); revoke.add_argument('--id', required=True); revoke.add_argument('--reason', required=True)
    args = parser.parse_args()
    if args.action == 'revoke':
        execution.revoke(args.stack, args.id, args.reason); print('Local credential revoked.'); return
    # Reserve the destination first: never overwrite or print a credential.
    fd = os.open(Path(args.output), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as file:
        result = execution.provision(args.stack, {'principal_id': args.principal, 'identity_kind': args.identity_kind,
            'buyer_id': args.buyer, 'service_id': args.service, 'roles': args.role, 'expires_at': args.expires})
        json.dump(result, file)
    print(json.dumps({'credential_id': result['credential']['id'], 'secret_written_to': args.output,
                      'scope': 'Local packet operations only; human identity unverified'}))


if __name__ == '__main__': main()
