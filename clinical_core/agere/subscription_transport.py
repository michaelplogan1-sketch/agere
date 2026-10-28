"""Run Agere's Claude calls through the local Claude Code CLI on the user's subscription, for TEST runs only (AGERE_CLAUDE_TRANSPORT=subscription).

Every Claude call in Agere is an HTTP POST of a Messages API body to api.anthropic.com/v1/messages (gateway, analysts, the bench competitor). install()
wraps urllib.request.urlopen once: such a request is answered by `claude-anthropic -p` (headless Claude Code on the logged-in subscription) and returned
as a Messages API response, so no caller changes. Every other request is untouched.

  * system + messages become the CLI's system prompt (a file) and one user prompt (the transcript, on stdin); built-in Claude Code tools are off.
  * Tool use (the analyst's SQL tool, the competitor's tools) is emulated: the tools are listed in the system prompt, the model answers with ONE JSON
    object {"tool_use": {"name": ..., "input": {...}}} to call a tool, or with its final answer; the reply is returned as tool_use / text content
    blocks. This is an emulation of the API tool protocol, so results are close to, not identical with, API runs.
  * usage (input/output tokens) is passed through, so the ledger's cost figures are an API-equivalent estimate; the subscription bills nothing per call.

Not for the live product: subscription use is subject to the plan's usage limits and terms.
"""
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.request
import uuid

MESSAGES_URL = 'api.anthropic.com/v1/messages'
_installed = threading.Lock()
_original = {}


def enabled():
    return os.environ.get('AGERE_CLAUDE_TRANSPORT', '').strip().lower() == 'subscription'


def cli_path():
    """The real Anthropic CLI (not a wrapper that cannot read the login): AGERE_CLAUDE_CLI, else the newest ~/.local/share/claude/versions/<v>,
    else ~/.local/bin/claude-anthropic, else `claude`."""
    versions = os.path.expanduser('~/.local/share/claude/versions')
    newest = None
    if os.path.isdir(versions):
        names = [n for n in os.listdir(versions) if all(p.isdigit() for p in n.split('.'))]
        if names: newest = os.path.join(versions, max(names, key=lambda n: tuple(int(p) for p in n.split('.'))))
    for candidate in (os.environ.get('AGERE_CLAUDE_CLI'), newest, os.path.expanduser('~/.local/bin/claude-anthropic'), shutil.which('claude')):
        if candidate and os.path.exists(candidate): return candidate
    raise RuntimeError('No Claude Code CLI found for the subscription transport (set AGERE_CLAUDE_CLI)')


TOKEN_FILE = os.path.expanduser(os.environ.get('AGERE_CLAUDE_TOKEN_FILE', '~/.agere-claude-oauth-token'))


def cli_env():
    """The CLI's environment: never the API key; the subscription token from the user's private token file (`claude setup-token`) when present.
    The token is passed only to the CLI process and is never logged or returned."""
    env = {k: v for k, v in os.environ.items() if k != 'ANTHROPIC_API_KEY'}
    if not env.get('CLAUDE_CODE_OAUTH_TOKEN') and os.path.isfile(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            token = f.read().strip()
        if token: env['CLAUDE_CODE_OAUTH_TOKEN'] = token
    return env


def _text(content):
    if isinstance(content, str): return content
    out = []
    for block in content or []:
        if not isinstance(block, dict): continue
        kind = block.get('type')
        if kind == 'text': out.append(block.get('text', ''))
        elif kind == 'tool_use': out.append('[tool call] ' + json.dumps({'tool_use': {'name': block.get('name'), 'input': block.get('input')}}))
        elif kind == 'tool_result':
            body = block.get('content')
            out.append('[tool result for ' + str(block.get('tool_use_id')) + '] ' + (_text(body) if not isinstance(body, str) else body))
    return '\n'.join(out)


TOOL_PROTOCOL = '''

TOOLS (emulated). You may call these tools:
{tools}
To call a tool, reply with ONLY one JSON object and nothing else: {{"tool_use": {{"name": "<tool name>", "input": {{...arguments...}}}}}}
When several read-only actions and all their exact arguments are already resolved, you may return
{{"tool_uses": [{{"name": "<tool name>", "input": {{...literal arguments...}}}}, ...]}}.
Do not batch an action whose arguments require new inference from an earlier result. The executor checks source and scope guards between actions.
Tool results will come back in the next message. When you are done, reply with your final answer in the format the instructions above require
(not a tool_use object).'''


def build_prompt(body):
    """-> (system text, user text) for one Messages API body."""
    system = body.get('system') or ''
    system = _text(system) if not isinstance(system, str) else system
    tools = body.get('tools') or []
    if tools:
        listing = json.dumps([{'name': t.get('name'), 'description': t.get('description'), 'input_schema': t.get('input_schema')} for t in tools], indent=1)
        system += TOOL_PROTOCOL.format(tools=listing)
    turns = []
    for m in body.get('messages') or []:
        turns.append(('USER' if m.get('role') == 'user' else 'ASSISTANT') + ':\n' + _text(m.get('content')))
    user = turns[-1].split(':\n', 1)[1] if len(turns) == 1 else 'Conversation so far:\n\n' + '\n\n'.join(turns) + '\n\nReply as the ASSISTANT to the last USER message.'
    return system, user


INVOKE = re.compile(r'<invoke\s+name="([^"]+)"\s*>(.*?)(?:</invoke>|$)', re.S)
PARAM = re.compile(r'<parameter\s+name="([^"]+)"\s*>(.*?)</parameter>', re.S)


def _xml_call(text, names):
    """The model's native tool-call markup (<invoke name="tool"><parameter name="p">value</parameter></invoke>), which it sometimes writes
    instead of the JSON protocol (2026-10-26: x1-25 failed this way) -> {'name', 'input'} or None. A JSON-looking value is decoded."""
    m = INVOKE.search(text or '')
    if not m or m.group(1) not in names: return None
    args = {}
    for k, v in PARAM.findall(m.group(2)):
        v = v.strip()
        try:
            args[k] = json.loads(v) if v[:1] in '[{' or v in ('true', 'false', 'null') or re.fullmatch(r'-?\d+(?:\.\d+)?', v) else v
        except ValueError:
            args[k] = v
    return {'name': m.group(1), 'input': args}


def _tool_call(text, tools):
    """The reply as a tool call ({"tool_use": {...}}, or the model's native <invoke> markup) when it is one, else None."""
    if not tools: return None
    xml = _xml_call(text, {t.get('name') for t in tools})
    if xml: return xml
    s = (text or '').strip()
    if s.startswith('```'): s = s.strip('`').split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    start = s.find('{"tool_use"')
    if start < 0: start = s.find('{') if s.startswith('{') else -1
    if start < 0: return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(s[start:])  # the first object: a reply may run on past it (prose, a second call)
    except ValueError:
        return None
    call = obj.get('tool_use') if isinstance(obj, dict) else None
    names = {t.get('name') for t in tools}
    if isinstance(call, dict) and call.get('name') in names and isinstance(call.get('input'), dict): return call
    return None


def run_cli(model, system, user, timeout=None):
    """-> the CLI's JSON result (dict). Raises RuntimeError on a CLI or authentication failure."""
    with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:
        f.write(system); path = f.name
    env = cli_env()  # never the API account: the subscription login / token only
    cmd = [cli_path(), '-p', '--model', model, '--system-prompt-file', path, '--output-format', 'json', '--tools', '',
           '--strict-mcp-config', '--no-session-persistence']
    try:
        done = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=timeout, env=env)
    finally:
        os.unlink(path)
    lines = [l for l in done.stdout.splitlines() if l.strip().startswith('{')]
    if not lines: raise RuntimeError(f'claude CLI returned no JSON (exit {done.returncode}): {(done.stderr or done.stdout)[-300:]}')
    out = json.loads(lines[-1])
    if out.get('is_error') or out.get('terminal_reason') == 'api_error':
        message = str(out.get('result') or '')
        if any(cue in message.lower() for cue in ("you've hit your limit", 'weekly limit', 'usage limit', 'rate limit', 'rate_limit')):
            from .gateway import ProviderUnavailable
            raise ProviderUnavailable('Claude subscription limit reached: ' + message[:300], kind='rate_limit')
        raise RuntimeError('claude CLI error: ' + str(out.get('result'))[:300])
    return out


def _tool_calls(text, tools):
    """Recognize literal protocol actions; empty unknown XML padding carries no action.

    Never discard a nonempty unknown invocation, accept an unknown batch member,
    or mix a native invocation with a second JSON action envelope.
    """
    if not tools: return []
    names = {t.get('name') for t in tools}
    raw = (text or '').strip()
    while True:
        padding = INVOKE.match(raw)
        if not padding or padding.group(1) in names or padding.group(2).strip(): break
        raw = raw[padding.end():].lstrip()
    leading = INVOKE.match(raw)
    if leading and leading.group(1) in names:
        rest = raw[leading.end():].strip()
        try: extra = json.loads(rest)
        except ValueError: extra = None
        if isinstance(extra, dict) and any(k in extra for k in ('tool_use', 'tool_uses')): return []
    single = _tool_call(raw, tools)
    if single:
        try: json.dumps(single, allow_nan=False)
        except (TypeError, ValueError): return []
        return [single]
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw)
    def nonfinite(value): raise ValueError('Nonfinite tool argument')
    try: obj = json.loads(raw, parse_constant=nonfinite)
    except ValueError: return []
    calls = obj.get('tool_uses') if isinstance(obj, dict) and set(obj) == {'tool_uses'} else None
    if not isinstance(calls, list) or not calls or any(not isinstance(c, dict) or set(c) != {'name', 'input'}
            or c['name'] not in names or not isinstance(c['input'], dict) for c in calls):
        return []
    return calls


def normalize_tool_response(response, tools):
    """Free literal normalization also applies to cached or non-CLI text responses."""
    blocks = response.get('content') or []
    if not tools or response.get('stop_reason') == 'max_tokens' or any(b.get('type') == 'tool_use' for b in blocks): return response
    calls = _tool_calls('\n'.join(b.get('text', '') for b in blocks if b.get('type') == 'text'), tools)
    if not calls: return response
    return {**response, 'stop_reason': 'tool_use',
            'content': [{'type': 'tool_use', 'id': 'toolu_normalized_' + uuid.uuid4().hex[:20],
                         'name': c['name'], 'input': c['input']} for c in calls]}


def respond(body, *, timeout=None):
    """A Messages API body -> a Messages API response dict, answered by the CLI."""
    model = body.get('model') or 'claude-haiku-5-5'
    system, user = build_prompt(body)
    # Preserve the caller's timeout instead of silently imposing a shorter CLI
    # limit. An explicit environment override remains available. A timeout is
    # reported once: replaying an interrupted generation can duplicate usage.
    configured = os.environ.get('AGERE_SUBSCRIPTION_TIMEOUT')
    if configured:
        timeout = float(configured)
    out = run_cli(model, system, user, timeout=timeout)
    text = out.get('result') or ''
    usage = out.get('usage') or {}
    usage = {k: usage.get(k, 0) for k in ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')}
    calls = _tool_calls(text, body.get('tools'))
    if calls:
        content, stop = [{'type': 'tool_use', 'id': 'toolu_' + uuid.uuid4().hex[:20], 'name': call['name'], 'input': call['input']} for call in calls], 'tool_use'
    else:
        content, stop = [{'type': 'text', 'text': text}], 'end_turn'
    return {'id': 'msg_sub_' + uuid.uuid4().hex[:16], 'type': 'message', 'role': 'assistant', 'model': model, 'content': content,
            'stop_reason': stop, 'stop_sequence': None, 'usage': usage, 'transport': 'claude-code-subscription'}


class _Response(io.BytesIO):
    status = 200
    code = 200
    headers = {'content-type': 'application/json'}

    def getcode(self): return 200
    def info(self): return self.headers
    def __enter__(self): return self
    def __exit__(self, *a): self.close(); return False


def install():
    """Wrap urllib.request.urlopen once (only when enabled()). Returns True when the subscription transport is active."""
    if not enabled(): return False
    with _installed:
        if _original: return True
        real = urllib.request.urlopen
        _original['urlopen'] = real

        def urlopen(url, data=None, timeout=None, *args, **kwargs):
            full = url.full_url if isinstance(url, urllib.request.Request) else str(url)
            if MESSAGES_URL in full:
                payload = data if data is not None else (url.data if isinstance(url, urllib.request.Request) else None)
                body = json.loads(payload.decode() if isinstance(payload, (bytes, bytearray)) else payload)
                return _Response(json.dumps(respond(body, timeout=timeout)).encode())
            return real(url, data, timeout, *args, **kwargs) if timeout is not None else real(url, data, *args, **kwargs)
        urllib.request.urlopen = urlopen
        os.environ.setdefault('ANTHROPIC_API_KEY', 'subscription-transport')  # Claude tiers check for a key; it is never sent
    return True
