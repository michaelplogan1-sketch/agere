"""Sandbox worker for evolved outreach policies. Run as a separate process with `python -I sandbox_worker.py`; reads one JSON
request on stdin and writes one JSON reply on stdout. Self-contained on purpose (standard library only, no package
imports): the parent validates the code first (sandbox.validate) and this process validates it again before executing.

request: {"code": str, "rows": [feature dict, ...], "programs": [program dict, ...], "cpu_seconds": int}
reply:   {"ok": true, "scores": [float | null, ...], "choices": [program id | null, ...]} or {"ok": false, "error": str}
"""
import ast
import json
import math
import statistics
import sys

ALLOWED_MODULES = {'math': math, 'statistics': statistics}
DENIED_NAMES = {'open', 'eval', 'exec', 'compile', 'globals', 'locals', 'vars', 'getattr', 'setattr', 'delattr', 'hasattr',
                'input', 'breakpoint', 'help', 'exit', 'quit', 'memoryview', 'type', 'object', 'super', 'dir', 'id', 'print',
                'classmethod', 'staticmethod', 'property', 'bytearray', 'bytes', 'iter', 'next', 'aiter', 'anext', 'callable',
                'issubclass', 'hash', 'format', 'ascii', 'repr', 'chr', 'ord', 'sys', 'os', 'builtins', 'importlib', 'subprocess',
                'socket', 'io', 'pathlib', 'shutil'}
DENIED_ATTRS = {'format', 'format_map', 'mro', 'gi_frame', 'gi_code', 'f_globals', 'f_locals', 'f_back', 'tb_frame', 'cr_frame',
                'co_code', 'func_globals'}
DENIED_NODES = (ast.Global, ast.Nonlocal, ast.ClassDef, ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith, ast.With,
                ast.Yield, ast.YieldFrom)
SAFE_BUILTINS = {k: __builtins__[k] if isinstance(__builtins__, dict) else getattr(__builtins__, k) for k in (
    'abs', 'min', 'max', 'sum', 'len', 'range', 'sorted', 'reversed', 'enumerate', 'zip', 'any', 'all', 'float', 'int', 'bool',
    'round', 'isinstance', 'list', 'dict', 'tuple', 'set', 'frozenset', 'str', 'pow', 'divmod', 'map', 'filter',
    'ValueError', 'TypeError', 'KeyError', 'IndexError', 'ZeroDivisionError', 'ArithmeticError', 'Exception')}
MAX_CODE_CHARS = 20000


class SandboxError(ValueError):
    pass


def validate(code):
    """Static checks. Raises SandboxError with the first violation; returns the parsed module."""
    if not isinstance(code, str) or not code.strip():
        raise SandboxError('empty code')
    if len(code) > MAX_CODE_CHARS:
        raise SandboxError(f'code longer than {MAX_CODE_CHARS} characters')
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise SandboxError(f'syntax error: {e.msg} (line {e.lineno})') from None
    for node in ast.walk(tree):
        if isinstance(node, DENIED_NODES):
            raise SandboxError(f'{type(node).__name__} is not allowed')
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name not in ALLOWED_MODULES:
                    raise SandboxError(f'import of {a.name!r} is not allowed (only math, statistics)')
        if isinstance(node, ast.ImportFrom):
            if node.module not in ALLOWED_MODULES or node.level:
                raise SandboxError(f'import from {node.module!r} is not allowed (only math, statistics)')
            for a in node.names:
                if a.name == '*' or a.name.startswith('_'):
                    raise SandboxError('star / private imports are not allowed')
        if isinstance(node, ast.Name) and (node.id.startswith('_') or node.id in DENIED_NAMES):
            raise SandboxError(f'name {node.id!r} is not allowed')
        if isinstance(node, ast.Attribute) and (node.attr.startswith('_') or node.attr in DENIED_ATTRS):
            raise SandboxError(f'attribute {node.attr!r} is not allowed')
        if isinstance(node, (ast.FunctionDef, ast.arg)) and getattr(node, 'name', getattr(node, 'arg', '')).startswith('_'):
            raise SandboxError('private function / argument names are not allowed')
        if isinstance(node, ast.alias) and (node.asname or '').startswith('_'):
            raise SandboxError('private aliases are not allowed')
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    if 'prioritize' not in names:
        raise SandboxError('the code must define prioritize(patient)')
    return tree


def _importer(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name not in ALLOWED_MODULES:
        raise ImportError(f'import of {name!r} is not allowed')
    return ALLOWED_MODULES[name]


def load(code):
    validate(code)
    env = {'__builtins__': {**SAFE_BUILTINS, '__import__': _importer}, '__name__': 'policy'}
    exec(compile(code, '<policy>', 'exec'), env)
    return env


def _num(x):
    if x is None:
        return None
    if isinstance(x, bool):
        x = float(x)
    if not isinstance(x, (int, float)):
        raise SandboxError(f'prioritize must return a number or None, not {type(x).__name__}')
    x = float(x)
    if not math.isfinite(x):
        return None
    return x


def execute(code, rows, programs):
    env = load(code)
    pri, choose = env['prioritize'], env.get('choose_program')
    ids = {p['id'] for p in programs}
    scores, choices = [], []
    for row in rows:
        scores.append(_num(pri(dict(row))))
        if choose is None:
            choices.append(row.get('routed_program'))
            continue
        c = choose(dict(row), [dict(p) for p in programs])
        if c is not None and c not in ids:
            raise SandboxError(f'choose_program returned {c!r}, not one of {sorted(ids)} or None')
        choices.append(c)
    return scores, choices


def main():
    req = json.loads(sys.stdin.read())
    try:
        import resource
        cpu = int(req.get('cpu_seconds') or 10)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    except Exception:
        pass
    try:
        scores, choices = execute(req['code'], req['rows'], req['programs'])
        out = {'ok': True, 'scores': scores, 'choices': choices}
    except RecursionError:
        out = {'ok': False, 'error': 'RecursionError'}
    except MemoryError:
        out = {'ok': False, 'error': 'MemoryError'}
    except Exception as e:  # noqa: BLE001 -- any failure of untrusted code discards the candidate
        out = {'ok': False, 'error': f'{type(e).__name__}: {str(e)[:300]}'}
    sys.stdout.write(json.dumps(out))


if __name__ == '__main__':
    main()
