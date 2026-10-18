"""Restricted execution of an evolved policy.

A policy is a small Python program that defines

    def prioritize(patient) -> float | None      # higher = contacted earlier; None = never contact
    def choose_program(patient, programs) -> program id | None   # optional; default = the program the router chose

`patient` is a dict of de-identified features (evolve/features.py; never a patient id). The code may import only math and
statistics, may not touch names or attributes that start with an underscore, may not call open / eval / exec / getattr /
print and similar, and runs in a separate `python -I` process (sandbox_worker.py) with a wall-clock timeout and a CPU
limit, receiving its inputs on stdin and returning scores on stdout: no file, network or process access from the policy.
Invalid, failing or slow candidates raise SandboxError and are discarded by the evolution loop.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .sandbox_worker import SandboxError, validate  # noqa: F401  (re-exported)

WORKER = Path(__file__).resolve().parent / 'sandbox_worker.py'
DEFAULT_TIMEOUT_S = 5.0


def run(code, rows, programs, *, timeout_s=DEFAULT_TIMEOUT_S):
    """-> (scores, choices) for every feature row. Raises SandboxError on any violation, failure or timeout."""
    validate(code)
    req = json.dumps({'code': code, 'rows': rows, 'programs': programs, 'cpu_seconds': max(1, int(timeout_s) + 1)})
    try:
        p = subprocess.run([sys.executable, '-I', '-S', str(WORKER)], input=req, capture_output=True, text=True,
                           timeout=timeout_s, cwd=str(WORKER.parent), env={'PATH': '/usr/bin:/bin'})
    except subprocess.TimeoutExpired:
        raise SandboxError(f'timeout after {timeout_s}s') from None
    if p.returncode != 0 or not p.stdout.strip():
        raise SandboxError(f'worker exited with code {p.returncode}: {p.stderr.strip()[-300:]}')
    out = json.loads(p.stdout)
    if not out.get('ok'):
        raise SandboxError(out.get('error') or 'policy failed')
    if len(out['scores']) != len(rows) or len(out['choices']) != len(rows):
        raise SandboxError('policy returned the wrong number of results')
    return out['scores'], out['choices']
