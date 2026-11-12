"""Measure whether the subscription CLI can reuse a stable prompt prefix across analyst turns.

Why: every analyst turn currently sends the whole compiled execution state as one new
user string through a fresh CLI process, so the state is a cache WRITE on every turn
(saved runs: ~175-245K cache-write tokens per turn, ~41K read). The compiled catalog is
prefix-stable across turns, so it could be read from cache if the wire kept a boundary.

This makes at most six small calls (default model: the entry analyst model) and prints
reported usage. It does not touch the warehouse, ledger or any saved investigation.
Run only when the subscription is actually available:

    python -B tools/probe_subscription_cache.py --run

Variants (each: turn 1 then turn 2 whose prompt extends turn 1's):
  flat     today's wire: one user string per turn (control; expect no prefix read)
  blocks   stream-json input, per-entry text blocks, cache_control on the last stable block
  session  turn 2 resumes turn 1's CLI session and sends only the new content
A variant helps if turn 2 reports cache_read_input_tokens covering the shared prefix.
"""
import argparse
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clinical_core.agere import subscription_transport as T  # noqa: E402


def filler(tag, lines=420):
    # Distinct, incompressible-enough synthetic entries; no patient or source content.
    return '\n'.join(f'{tag} entry {i}: synthetic execution receipt {uuid.uuid5(uuid.NAMESPACE_OID, tag + str(i))}' for i in range(lines))


def run(args, stdin, system, model):
    with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:
        f.write(system); path = f.name
    cmd = [T.cli_path(), '-p', '--model', model, '--system-prompt-file', path, '--tools', '', '--strict-mcp-config', *args]
    try:
        done = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=600, env=T.cli_env())
    finally:
        Path(path).unlink()
    lines = [l for l in done.stdout.splitlines() if l.strip().startswith('{')]
    out = json.loads(lines[-1]) if lines else {}
    if done.returncode or out.get('is_error') or not out:
        raise RuntimeError(f'CLI failed (exit {done.returncode}): {(out.get("result") or done.stderr or done.stdout)[:400]}')
    usage = out.get('usage') or {}
    return {k: usage.get(k) for k in ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')}


def user_line(blocks):
    return json.dumps({'type': 'user', 'message': {'role': 'user', 'content': blocks}}) + '\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true', help='Actually make the provider calls')
    parser.add_argument('--model', default='claude-sonnet-5-5')
    parser.add_argument('--variants', default='flat,blocks,session')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if not args.run:
        print(__doc__); return
    nonce = uuid.uuid4().hex
    system = f'Probe {nonce}. Reply with the single word OK.\n' + filler('system-' + nonce, 120)
    first, second = filler('first-' + nonce), filler('second-' + nonce, 60)
    ask1, ask2 = 'Turn 1: reply OK.', 'Turn 2: reply OK.'
    stream = ['--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose']
    results = {'model': args.model, 'nonce': nonce, 'approx_prefix_characters': len(first)}
    for variant in args.variants.split(','):
        try:
            if variant == 'flat':
                plain = ['--output-format', 'json', '--no-session-persistence']
                a = run(plain, first + '\n' + ask1, system + 'flat', args.model)
                b = run(plain, first + '\n' + second + '\n' + ask2, system + 'flat', args.model)
            elif variant == 'blocks':
                mark = {'cache_control': {'type': 'ephemeral'}}
                a = run([*stream, '--no-session-persistence'],
                        user_line([{'type': 'text', 'text': first, **mark}, {'type': 'text', 'text': ask1}]), system + 'blocks', args.model)
                b = run([*stream, '--no-session-persistence'],
                        user_line([{'type': 'text', 'text': first}, {'type': 'text', 'text': second, **mark},
                                   {'type': 'text', 'text': ask2}]), system + 'blocks', args.model)
            elif variant == 'session':
                session = str(uuid.uuid4())
                a = run(['--output-format', 'json', '--session-id', session], first + '\n' + ask1, system + 'session', args.model)
                b = run(['--output-format', 'json', '--resume', session], second + '\n' + ask2, system + 'session', args.model)
            else:
                raise ValueError('unknown variant')
            results[variant] = {'turn1': a, 'turn2': b}
        except Exception as exc:  # report each variant; a limit error stops the rest
            results[variant] = {'error': str(exc)[:500]}
            if T and 'limit' in str(exc).lower(): break
    text = json.dumps(results, indent=2)
    print(text)
    if args.out: args.out.write_text(text + '\n')


if __name__ == '__main__': main()
