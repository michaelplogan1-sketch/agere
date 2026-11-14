"""Stop a running question on the local app without losing its work.

The app already handles an interrupted Claude call: the subscription transport reports it as cancelled, the review
scheduler stops, and the request returns a saved partial with its supported verdicts and draft. Terminating the app
itself discards all of that (2026-11-12: a stopped run lost 38 supported patient reviews). This signals only the app's
own in-flight `claude -p` children, repeatedly, until the app reports no active request. It never signals the app.

    python -B tools/stop_live_request.py --port 8908            # show what would be signalled
    python -B tools/stop_live_request.py --port 8908 --stop     # interrupt and wait for the app to save
"""
import argparse
import json
import os
import signal
import subprocess
import time
import urllib.request

APP = 'serve_current_warehouse.py'


def processes(ps_output):
    """`ps -axo pid=,ppid=,command=` text -> [{'pid','ppid','command'}]."""
    rows = []
    for line in ps_output.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            rows.append({'pid': int(parts[0]), 'ppid': int(parts[1]), 'command': parts[2]})
    return rows


def claude_children(rows, server_pid, cli):
    """Headless runs of the transport's own Claude CLI that are direct children of this app, and nothing else."""
    server = next((r for r in rows if r['pid'] == server_pid), None)
    if server is None or APP not in server['command']: raise ValueError('The listener is not the local Agere app')
    return [r for r in rows if r['ppid'] == server_pid and ' -p ' in ' ' + r['command'] + ' '
            and '--system-prompt-file' in r['command'] and r['command'].split()[0] == cli]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--wait', type=float, default=300)
    a = parser.parse_args()
    listener = subprocess.run(['lsof', '-tiTCP:%d' % a.port, '-sTCP:LISTEN'], capture_output=True, text=True).stdout.split()
    if len(listener) != 1: raise SystemExit('No single listener on that port')
    server = int(listener[0]); signalled = []; started = time.monotonic()
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from clinical_core.agere.subscription_transport import cli_path
    cli = cli_path()
    def active():
        return json.load(urllib.request.urlopen('http://127.0.0.1:%d/api/stack/meta' % a.port, timeout=10))['request_locks']['active']
    while True:
        rows = processes(subprocess.run(['ps', '-axo', 'pid=,ppid=,command='], capture_output=True, text=True).stdout)
        children = claude_children(rows, server, cli)
        if not a.stop:
            print(json.dumps({'server_pid': server, 'active_requests': active(), 'claude_children': [c['pid'] for c in children]})); return
        for child in children:
            if child['pid'] not in signalled:
                os.kill(child['pid'], signal.SIGTERM); signalled.append(child['pid'])
        if not active() or time.monotonic() - started > a.wait: break
        time.sleep(2)
    print(json.dumps({'server_pid': server, 'signalled_claude_children': signalled, 'app_signalled': False,
                      'active_requests_after': active(), 'seconds': round(time.monotonic() - started, 1)}))


if __name__ == '__main__': main()
