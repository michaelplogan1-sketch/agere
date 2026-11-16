"""Provider keys: one loader, one rule.

Keys for the model and classifier services (TypeSafe/Jev, Perplexity, OpenAI, Anthropic) are read from files outside the
repository and put in this process's environment. Where they come from:
  AGERE_KEYS_FILE   a key file: a bare first line is the TypeSafe (Jev) key, then 'Label: key' lines
                    (default ~/Desktop/typesafe_key.txt, the owner's file)
  AGERE_ENV_FILE    an optional KEY=value file that only fills keys the first file lacks (no default)
Nothing is printed. The rule, enforced here and nowhere else: the paid Anthropic API key is kept only on the hosted server
using the API transport (AGERE_HOSTED=1 and AGERE_CLAUDE_TRANSPORT=api). Everywhere else it is removed, so a workstation,
a test, an evaluation or a proof run reaches Claude through the subscription transport only. AGERE_CLAUDE=off removes it
too. Until 2026-11-16 this lived in the root script run_integrated_stack.py, which the hosted image did not contain, and about
twenty callers removed the key by hand (setup review).

    load_keys()
"""
import os
from pathlib import Path

LABELS = {'typesafe': 'TYPESAFE_API_KEY', 'jev': 'TYPESAFE_API_KEY', 'perplexity': 'PERPLEXITY_API_KEY',
          'anthropic': 'ANTHROPIC_API_KEY', 'open ai': 'OPENAI_API_KEY', 'openai': 'OPENAI_API_KEY'}
ENV_NAMES = ('TYPESAFE_API_KEY', 'PERPLEXITY_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY')
LEGACY_ENV_FILE = Path('/Users/mikel/insight-engine/.env')   # read only when it exists and AGERE_ENV_FILE is not set


def _keys_file(path):
    if not path.is_file(): return
    lines = [l.strip() for l in path.read_text().splitlines() if l.strip()]
    for i, line in enumerate(lines):
        label, sep, value = line.partition(':')
        name = LABELS.get(label.strip().lower()) if sep else ('TYPESAFE_API_KEY' if i == 0 else None)
        value = value.strip() if sep else line
        if name and value: os.environ[name] = value.strip('"\'')


def _env_file(path):
    if not path or not path.is_file(): return
    for line in path.read_text().splitlines():
        line = line.strip().removeprefix('export ')
        if '=' not in line: continue
        key, value = line.split('=', 1)
        if key.strip() in ENV_NAMES: os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def anthropic_key_allowed():
    return os.environ.get('AGERE_HOSTED') == '1' and os.environ.get('AGERE_CLAUDE_TRANSPORT', '').strip().lower() == 'api' \
        and os.environ.get('AGERE_CLAUDE', 'on') != 'off'


def load_keys():
    hosted_key = os.environ.get('ANTHROPIC_API_KEY') if anthropic_key_allowed() else None   # the hosted server's own key, placed by its operator
    _keys_file(Path(os.environ.get('AGERE_KEYS_FILE') or Path.home() / 'Desktop' / 'typesafe_key.txt'))
    _env_file(Path(os.environ['AGERE_ENV_FILE']) if os.environ.get('AGERE_ENV_FILE') else LEGACY_ENV_FILE)
    if anthropic_key_allowed():
        if hosted_key: os.environ['ANTHROPIC_API_KEY'] = hosted_key
    elif os.environ.get('AGERE_CLAUDE_TRANSPORT') == 'subscription' and os.environ.get('AGERE_CLAUDE', 'on') != 'off':
        os.environ['ANTHROPIC_API_KEY'] = 'subscription-transport'   # a marker the older gateway reads as "Claude is on"; not a key
    else:
        os.environ.pop('ANTHROPIC_API_KEY', None)
