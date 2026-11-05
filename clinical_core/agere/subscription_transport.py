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
from urllib.parse import urlsplit
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
    schema=output_schema(body)
    if schema is not None:
        system+='\nThe final value must satisfy this JSON Schema: '+json.dumps(schema)
    tools = allowed_tools(body.get('tools'),body.get('tool_choice'))
    if tools:
        listing = json.dumps([{'name': t.get('name'), 'description': t.get('description'), 'input_schema': t.get('input_schema')} for t in tools], indent=1)
        system += TOOL_PROTOCOL.format(tools=listing)
        choice=body.get('tool_choice')or{}
        if choice.get('type')in ('tool','any'):
            system+='\nThis turn requires a tool call. Return ONLY the tool_use object'
            if choice['type']=='tool':system+=' naming '+choice['name']
            system+=' with literal input arguments. Do not return final text or a repair_draft on this turn.'
    elif body.get('tools'):
        system += '\nTools are disabled for this turn. Return the final answer in the required contract. Tool requests will not execute.'
    turns = []
    for m in body.get('messages') or []:
        turns.append(('USER' if m.get('role') == 'user' else 'ASSISTANT') + ':\n' + _text(m.get('content')))
    user = turns[-1].split(':\n', 1)[1] if len(turns) == 1 else 'Conversation so far:\n\n' + '\n\n'.join(turns) + '\n\nReply as the ASSISTANT to the last USER message.'
    return system, user


def output_schema(body):
    config=body.get('output_config')or{}
    if not isinstance(config,dict):raise ValueError('output_config must be an object')
    form=config.get('format')
    if form is None:return None
    if not isinstance(form,dict)or form.get('type')!='json_schema' or not isinstance(form.get('schema'),dict):
        raise ValueError('Subscription output format requires a JSON Schema object')
    return form['schema']


def allowed_tools(tools,choice=None):
    """Honor the current turn's tool permission before advertising or parsing."""
    kind=(choice or {}).get('type','auto')
    if kind=='none':return []
    if kind=='tool':return [t for t in tools or [] if t.get('name')==choice.get('name')]
    if kind not in ('auto','any'):raise ValueError('Unsupported tool_choice')
    return tools or []


def decision_output_schema(body):
    """Constrain either a final value or currently permitted emulated actions."""
    final=output_schema(body)
    if final is None:return None
    tools=allowed_tools(body.get('tools'),body.get('tool_choice'))
    if not tools:return final
    def obj(properties):
        return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}
    calls={'anyOf':[obj({'name':{'enum':[t['name']]},'input':t.get('input_schema')or{'type':'object'}})for t in tools]}
    variants=[obj({'tool_use':calls}),obj({'tool_uses':{'type':'array','items':calls,'minItems':1}})]
    if (body.get('tool_choice')or{}).get('type','auto')not in ('tool','any'):
        variants.insert(0,final)
    # StructuredOutput requires a root object and rejects a top-level union.
    # Nest the decision, then remove only this transport-owned wrapper below.
    return obj({'decision':{'anyOf':variants}})


INVOKE = re.compile(r'<invoke\s+name="([^"]+)"\s*>(.*?)(?:</invoke>|$)', re.S)
PARAM = re.compile(r'<parameter\s+name="([^"]+)"\s*>(.*?)</parameter>', re.S)


def _xml_call(text, names):
    """The model's native tool-call markup (<invoke name="tool"><parameter name="p">value</parameter></invoke>), which it sometimes writes
    instead of the JSON protocol (2026-10-26: x1-25 failed this way) -> {'name', 'input'} or None. A JSON-looking value is decoded."""
    m = INVOKE.search(text or '')
    if not m or m.group(1) not in names | {'tool_use'}: return None
    args = {}
    for k, v in PARAM.findall(m.group(2)):
        v = v.strip()
        try:
            args[k] = json.loads(v) if v[:1] in '[{' or v in ('true', 'false', 'null') or re.fullmatch(r'-?\d+(?:\.\d+)?', v) else v
        except ValueError:
            args[k] = v
    if m.group(1)=='tool_use':
        if set(args)!={'name','input'} or not isinstance(args['name'],str) or args['name'] not in names or not isinstance(args['input'],dict):return None
        return args
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


def run_cli(model, system, user, timeout=None, *, max_tokens=None, effort=None, json_schema=None):
    """-> the CLI's JSON result (dict). Raises RuntimeError on a CLI or authentication failure."""
    env = cli_env()  # never the API account: the subscription login / token only
    # Mike's explicit subscription policy: no custom response ceiling. Legacy
    # API callers still include max_tokens; validate and record it but do not
    # enforce it through the CLI or inherit an earlier custom output setting.
    if max_tokens is not None:
        if type(max_tokens) is not int or max_tokens <= 0:
            raise ValueError('max_tokens must be a positive integer')
    env.pop('CLAUDE_CODE_MAX_OUTPUT_TOKENS',None)
    if 'CLAUDE_CODE_EXTRA_BODY' in env:
        extra=json.loads(env['CLAUDE_CODE_EXTRA_BODY'])
        if not isinstance(extra, dict):
            raise ValueError('CLAUDE_CODE_EXTRA_BODY must be a JSON object')
        extra.pop('max_tokens',None)
        if extra:env['CLAUDE_CODE_EXTRA_BODY']=json.dumps(extra)
        else:env.pop('CLAUDE_CODE_EXTRA_BODY')
    if effort is not None:
        if effort not in ('low', 'medium', 'high', 'xhigh', 'max'):
            raise ValueError('Unsupported caller effort level')
        env['CLAUDE_CODE_EFFORT_LEVEL'] = effort
    if json_schema is not None and not isinstance(json_schema,dict):
        raise ValueError('json_schema must be an object')
    with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:
        f.write(system); path = f.name
    cmd = [cli_path(), '-p', '--model', model, '--system-prompt-file', path, '--output-format', 'json', '--tools', '',
           '--strict-mcp-config', '--no-session-persistence']
    if json_schema is not None:cmd+=['--json-schema',json.dumps(json_schema)]
    try:
        done = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired as exc:
        partial=exc.stdout or ''
        if isinstance(partial,bytes):partial=partial.decode('utf-8',errors='replace')
        exc.subscription_cli_result={'result':partial,'usage':None,'subtype':'timeout','partial_format':'cli_stdout'}
        raise
    finally:
        os.unlink(path)
    lines = [l for l in done.stdout.splitlines() if l.strip().startswith('{')]
    try:
        out=json.loads(lines[-1]) if lines else None
        if not isinstance(out,dict):raise ValueError('No JSON result object')
    except ValueError:
        error=RuntimeError(f'claude CLI returned no complete JSON result (exit {done.returncode})')
        error.subscription_cli_result={'result':done.stdout,'usage':None,'subtype':'invalid_cli_result',
            'exit_code':done.returncode,'partial_format':'cli_stdout'}
        raise error from None
    if done.returncode or out.get('is_error') or out.get('terminal_reason') == 'api_error':
        message = str(out.get('result') or '')
        from .gateway import ProviderUnavailable, subscription_limit
        error=(ProviderUnavailable('Claude subscription limit reached: ' + message[:300], kind='rate_limit')
               if subscription_limit(message) else RuntimeError('claude CLI error: ' + message[:300]))
        error.subscription_cli_result=out
        raise error
    return out


def strip_empty_unregistered_prefix(text, tools):
    """Remove only closed, empty, unknown leading tags; never discard an action.

    The subscription sometimes prefixes JSON with an empty formatting tag.
    Registered tools (even with no arguments), reserved envelopes, nonempty or
    malformed markup and tags inside JSON remain untouched.
    """
    names={t.get('name')for t in tools or []}|{'tool_use','tool_uses'}
    raw=text
    while True:
        padding=INVOKE.match(raw)
        if (not padding or not padding.group(0).endswith('</invoke>') or
                padding.group(1)in names or padding.group(2).strip()):return raw
        raw=raw[padding.end():].lstrip()


def _tool_calls(text, tools):
    """Recognize every unambiguous literal action, allowing surrounding prose.

    A complete answer JSON object is never searched for embedded actions. Mixed
    action/data objects, unknown actions and differing XML/JSON actions decline
    as a whole. Equivalent alternative encodings execute once. Ordinary
    introductory text or a trailing transport marker is inert.
    """
    if not tools: return []
    names = {t.get('name') for t in tools}
    raw = (text or '').strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw)
    # Observed CLI formatting wrapper; only its complete literal body is
    # eligible for the same registered-action parser and per-turn permissions.
    wrapper=re.fullmatch(r'<invoke_tool>\s*(.*?)\s*</invoke_tool>',raw,re.S)
    if wrapper:
        raw=wrapper.group(1).strip()
        try:
            _,end=json.JSONDecoder().raw_decode(raw)
            if not raw.startswith('{') or raw[end:].strip():return []
        except ValueError:return []
    def nonfinite(value):raise ValueError('Nonfinite tool argument')
    decoder=json.JSONDecoder(parse_constant=nonfinite)
    def call(value):
        if not isinstance(value,dict)or not {'name','input'}<=set(value)or set(value)-{'name','input','id','type'}:return None
        if not isinstance(value['name'],str)or value['name']not in names or not isinstance(value['input'],dict):return None
        if 'type'in value and value['type']!='tool_use':return None
        if 'id'in value and not isinstance(value['id'],str):return None
        result={'name':value['name'],'input':value['input']}
        try:json.dumps(result,allow_nan=False)
        except (TypeError,ValueError):return None
        return result
    def envelope(value):
        if not isinstance(value,dict):return None
        if set(value)=={'tool_use'}:values=[value['tool_use']]
        elif set(value)=={'tool_uses'}:values=value['tool_uses']
        elif value.get('type')=='tool_use':values=[value]
        else:return None
        if not isinstance(values,list)or not values:return None
        parsed=[call(v)for v in values]
        return parsed if all(v is not None for v in parsed)else None
    def same_calls(first, second):
        # Alternative encodings of one action must preserve literal types too:
        # Python equality alone would equate a boolean argument with 0 or 1.
        return second is not None and json.dumps(first,sort_keys=True,allow_nan=False)==json.dumps(second,sort_keys=True,allow_nan=False)
    # Do not find XML/JSON actions inside a literal final-answer object.
    if raw.startswith('['):return []
    if raw.startswith('{'):
        try:first,end=decoder.raw_decode(raw)
        except ValueError:return []
        first_calls=envelope(first)
        if first_calls is None:return []
        tail=raw[end:].strip()
        if tail.startswith('<invoke'):
            # The subscription can emit the same literal request in JSON and
            # XML. Execute that request once, without accepting differing or
            # unregistered companion actions as a partial valid batch.
            xml_calls=_tool_calls(tail,tools)
            return first_calls if same_calls(first_calls,xml_calls) else []
    raw=strip_empty_unregistered_prefix(raw,tools)
    xml_start=raw.find('<invoke');json_start=raw.find('{')
    if xml_start>=0 and (json_start<0 or xml_start<json_start):
        calls=[];cursor=0;remainder=[]
        for invocation in INVOKE.finditer(raw):
            remainder.append(raw[cursor:invocation.start()]);cursor=invocation.end()
            if not invocation.group(0).rstrip().endswith('</invoke>'):return []
            parameters=list(PARAM.finditer(invocation.group(2)))
            keys=[p.group(1)for p in parameters]
            if len(keys)!=len(set(keys)) or PARAM.sub('',invocation.group(2)).strip():return []
            if invocation.group(1)not in names|{'tool_use'}and not invocation.group(2).strip():continue
            parsed=call(_xml_call(invocation.group(0),names))
            if parsed is None:return []
            calls.append(parsed)
        remainder.append(raw[cursor:]);tail=''.join(remainder)
        if '<invoke'in tail:return []
        if not calls:return _tool_calls(tail,tools)
        if '{'in tail:
            try:
                alternative,end=decoder.raw_decode(tail.strip())
                if tail.strip()[end:].strip():return []
            except ValueError:return []
            return calls if same_calls(calls,envelope(alternative)) else []
        return calls
    calls=[];cursor=0
    while True:
        start=raw.find('{',cursor)
        if start<0:
            if '<invoke'in raw[cursor:]:return []
            break
        if '<invoke'in raw[cursor:start]:return []
        try:obj,end=decoder.raw_decode(raw,start)
        except ValueError:return []
        parsed=envelope(obj)
        if parsed is None:return []
        calls.extend(parsed);cursor=end
    return calls


def normalize_tool_response(response, tools, tool_choice=None):
    """Free literal normalization also applies to cached or non-CLI text responses."""
    blocks = response.get('content') or []
    permitted=allowed_tools(tools,tool_choice);names={t.get('name')for t in permitted}
    if any(b.get('type')=='tool_use' and b.get('name')not in names for b in blocks):
        # Native/cached actions have the same permissions as emulated text.
        # Preserve every block as inert text; reject the whole mixed batch.
        return {**response,'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(
            {'tool_uses':[{'name':b.get('name'),'input':b.get('input')}for b in blocks if b.get('type')=='tool_use'],
             'unexecuted_blocks':blocks})}], 'tool_permission_rejected':True}
    tools=permitted
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
    output_config = body.get('output_config') or {}
    if not isinstance(output_config, dict):
        raise ValueError('output_config must be an object')
    try:
        schema=decision_output_schema(body)
        schema_kw={'json_schema':schema} if schema is not None else {}
        wrapped_decision=bool(schema is not None and allowed_tools(body.get('tools'),body.get('tool_choice')))
        if wrapped_decision:
            system+='\nFor the native StructuredOutput response, put the final value or permitted tool envelope under its decision property. This transport wrapper does not authorize any extra tool or mixed action.'
        if schema_kw:
            system+='\nNative output transport: submit the required value once through the provided StructuredOutput tool. Do not print the complete value in assistant text first or repeat it afterwards. The tool is the serialization channel for this response; all declared tool permissions and evidence requirements still apply.'
        out = run_cli(model, system, user, timeout=timeout,
                      max_tokens=body.get('max_tokens'), effort=output_config.get('effort'),**schema_kw)
    except Exception as exc:
        raw=getattr(exc,'subscription_cli_result',None)
        if isinstance(raw,dict):
            exc.provider_response={'model':model,'transport':'claude-code-subscription',
                'usage':raw.get('usage'),'subscription_cli_result':raw,'status':'failed'}
        raise
    text = out.get('result') or ''
    if schema_kw and out.get('stop_reason')!='max_tokens'and 'structured_output'in out:
        structured=out['structured_output']
        if wrapped_decision and isinstance(structured,dict)and set(structured)=={'decision'}:
            structured=structured['decision']
        text=json.dumps(structured,ensure_ascii=False)
    reported_usage = out.get('usage') or {}
    usage = {k: reported_usage.get(k) for k in ('input_tokens', 'output_tokens')}
    usage.update({k:reported_usage.get(k,0)for k in ('cache_creation_input_tokens','cache_read_input_tokens')})
    if 'cache_creation' in reported_usage:
        # Preserve reported cache TTLs for pricing. Never turn a one-hour write
        # into a cheaper five-minute write by dropping the provider breakdown.
        usage['cache_creation'] = reported_usage['cache_creation']
    truncated = out.get('stop_reason') == 'max_tokens'
    calls = [] if truncated else _tool_calls(text, allowed_tools(body.get('tools'),body.get('tool_choice')))
    if calls:
        content, stop = [{'type': 'tool_use', 'id': 'toolu_' + uuid.uuid4().hex[:20], 'name': call['name'], 'input': call['input']} for call in calls], 'tool_use'
    else:
        content, stop = [{'type': 'text', 'text': text}], 'max_tokens' if truncated else 'end_turn'
    return {'id': 'msg_sub_' + uuid.uuid4().hex[:16], 'type': 'message', 'role': 'assistant', 'model': model, 'content': content,
            'stop_reason': stop, 'stop_sequence': None, 'usage': usage, 'transport': 'claude-code-subscription',
            'transport_details': {
                'schema': 'subscription-cli-receipt/1',
                'caller_max_tokens': body.get('max_tokens'),
                'caller_max_tokens_enforced':False,
                'output_policy':'Service boundary only; no custom output ceiling',
                'caller_effort': output_config.get('effort'),
                'structured_output_mode':'native_cli_decision_schema'if wrapped_decision else 'native_cli_schema'if schema_kw else None,
                'cli': {k: out[k] for k in ('num_turns', 'duration_ms', 'duration_api_ms', 'subtype', 'stop_reason', 'modelUsage','structured_output') if k in out},
                **({'native_text':out['result']}if schema_kw and out.get('result')and 'structured_output'in out else {}),
                'usage_scope': 'CLI aggregate, including reported internal recovery requests; internal API attempt count is unknown unless explicitly reported. No subscription allocation or invoice inferred.'}}


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
                address = urlsplit(full)
                if address.scheme != 'https' or (address.hostname or '') + address.path.rstrip('/') != MESSAGES_URL:
                    raise ValueError('The configured subscription transport supports generation only; count_tokens and other Messages subroutes must not invoke generation')
                payload = data if data is not None else (url.data if isinstance(url, urllib.request.Request) else None)
                body = json.loads(payload.decode() if isinstance(payload, (bytes, bytearray)) else payload)
                return _Response(json.dumps(respond(body, timeout=timeout)).encode())
            return real(url, data, timeout, *args, **kwargs) if timeout is not None else real(url, data, *args, **kwargs)
        urllib.request.urlopen = urlopen
        os.environ.setdefault('ANTHROPIC_API_KEY', 'subscription-transport')  # Claude tiers check for a key; it is never sent
    return True
