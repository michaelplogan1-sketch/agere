"""Grammar-constrained decoding for the local MLX model (PICARD / XGrammar style, pure Python, no extra dependency).

Every local generation path (local_normalizer, the typed worker, the bottled SQL rung, the narrow slot rung and the
template rung) hands mlx_lm a logits processor built here. At each step the processor walks the model's own ranking
(top_k first, then wider) and keeps only tokens whose text keeps the output a valid PREFIX of the grammar; end-of-sequence
is allowed only when the output is complete. The model still chooses: the constraint removes impossible continuations,
it never writes text itself.

Grammars (immutable states, so one grammar instance serves every sequence in a batch):
  JsonGrammar(root='object', keys=None)   RFC 8259 JSON; optional root type and allowed top-level keys (narrow slots).
  SqlGrammar(tables=None, fenced=False)   one read-only SQLite SELECT/WITH statement: first word SELECT or WITH, no
                                          INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/ATTACH/DETACH/PRAGMA/VACUUM/REINDEX,
                                          balanced parentheses and quotes, nothing after a terminating ';'; with `tables`, a
                                          name read after FROM/JOIN must be a known table or a CTE defined in the query
                                          (schema-aware, PICARD-lite); fenced = inside ```sql ... ``` as the bottled rung writes.
  LineGrammar()                           one non-empty line (the normalizer's canonical rewording or UNSUPPORTED).
  TemplateGrammar(parts)                  a fixed template with typed slots (template_decoding / TeCoD).
Heal, never block: when no token in the whole vocabulary keeps the prefix valid, the processor leaves the logits
unchanged for that step and records a violation (the downstream parser/lint/literal checks still run). AGERE_CONSTRAINED_DECODING=off
turns the processors into no-ops (they still report).
"""
import math
import os
import re

import numpy as np

VERSION = 'constrained-decoding/1'
NEG_INF = -1e9


def enabled():
    return os.environ.get('AGERE_CONSTRAINED_DECODING', 'on') != 'off'


# ---- JSON ------------------------------------------------------------------------------------------------------------
_NUM_PREFIX = re.compile(r'-?(?:0|[1-9]\d*)?(?:(?<=\d)\.\d*)?(?:(?<=\d)[eE][+-]?\d*)?$')
_NUM_FULL = re.compile(r'-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$')
_WS = ' \t\n\r'


class JsonGrammar:
    """State: (mode, stack, buf, key). mode in value|after|key_or_close|key|colon|string|skey|escape|unicode|number|literal|done."""
    name = 'json'

    def __init__(self, root=None, keys=None):
        self.root, self.keys = root, (frozenset(keys) if keys else None)

    def start(self):
        return ('value', (), '', '')

    def complete(self, s):
        mode, stack, buf, _ = s
        return mode == 'done' or (mode == 'number' and not stack and bool(_NUM_FULL.match(buf)))

    def must_stop(self, s):
        # A complete JSON object has no remaining content to generate. Whitespace
        # after its closing delimiter must not keep a local model decoding.
        return s[0]=='done'

    def advance(self, s, text):
        for ch in text:
            s = self._char(s, ch)
            if s is None: return None
        return s

    def _close_value(self, stack):
        return ('after', stack, '', '') if stack else ('done', (), '', '')

    def _char(self, s, ch):
        mode, stack, buf, key = s
        if mode == 'done':
            return s if ch in _WS else None
        if mode == 'value':
            if ch in _WS: return s
            if not stack and self.root == 'object' and ch != '{': return None
            if not stack and self.root == 'array' and ch != '[': return None
            if ch == '{': return ('key_or_close', stack + ('{',), '', '')
            if ch == '[': return ('value_or_close', stack + ('[',), '', '')
            if ch == '"': return ('string', stack, '', '')
            if ch in '-0123456789': return ('number', stack, ch, '')
            for lit in ('true', 'false', 'null'):
                if ch == lit[0]: return ('literal', stack, lit[1:], '')
            return None
        if mode == 'value_or_close':
            if ch in _WS: return s
            if ch == ']': return self._close_value(stack[:-1])
            return self._char(('value', stack, '', ''), ch)
        if mode == 'literal':
            if ch != buf[0]: return None
            rest = buf[1:]
            return ('literal', stack, rest, '') if rest else self._close_value(stack)
        if mode == 'number':
            if _NUM_PREFIX.match(buf + ch): return ('number', stack, buf + ch, '')
            if not _NUM_FULL.match(buf): return None
            return self._char(self._close_value(stack), ch)
        if mode in ('string', 'skey'):
            if ch == '"':
                if mode == 'skey':
                    if self.keys is not None and len(stack) == 1 and buf not in self.keys: return None
                    return ('colon', stack, '', '')
                return self._close_value(stack)
            if ch == '\\': return ('escape', stack, buf, mode)
            if ord(ch) < 0x20: return None
            nb = buf + ch
            if mode == 'skey' and self.keys is not None and len(stack) == 1 and not any(k.startswith(nb) for k in self.keys):
                return None
            return (mode, stack, nb, key)
        if mode == 'escape':
            if ch == 'u': return ('unicode', stack, buf, key + '4')
            if ch in '"\\/bfnrt': return (key, stack, buf + '?', '')
            return None
        if mode == 'unicode':
            back, left = key[:-1], int(key[-1])
            if ch not in '0123456789abcdefABCDEF': return None
            return (back, stack, buf + '?', '') if left == 1 else ('unicode', stack, buf, back + str(left - 1))
        if mode == 'key_or_close':
            if ch in _WS: return s
            if ch == '}': return self._close_value(stack[:-1])
            if ch == '"': return ('skey', stack, '', '')
            return None
        if mode == 'key':
            if ch in _WS: return s
            return ('skey', stack, '', '') if ch == '"' else None
        if mode == 'colon':
            if ch in _WS: return s
            return ('value', stack, '', '') if ch == ':' else None
        if mode == 'after':
            if ch in _WS: return s
            top = stack[-1]
            if ch == ',': return ('key', stack, '', '') if top == '{' else ('value', stack, '', '')
            if (ch == '}' and top == '{') or (ch == ']' and top == '['): return self._close_value(stack[:-1])
            return None
        return None


# ---- SQL -------------------------------------------------------------------------------------------------------------
# Only one statement can be written (first word SELECT/WITH, nothing after ';'), so these words are blocked as defence in
# depth; replace() and similar functions stay usable.
FORBIDDEN = frozenset({'insert', 'update', 'delete', 'drop', 'alter', 'create', 'attach', 'detach', 'pragma', 'vacuum', 'reindex'})
_WORD = re.compile(r'[A-Za-z0-9_]')
FENCE_OPEN = ('```sql\n', '```\n')


class SqlGrammar:
    """State tuple: (phase, quote, depth, word, first, after_from, ended, last_kw, ctes, with_state, fence_buf, comment)."""
    name = 'sql'

    def __init__(self, tables=None, fenced=False):
        self.tables = frozenset(t.lower() for t in tables) if tables else None
        self.fenced = fenced

    def start(self):
        return ('open' if self.fenced else 'body', '', 0, '', '', False, False, '', frozenset(), '', '', '')

    def must_stop(self, s):
        return s[0] == 'closed'

    def _body_complete(self, s):
        phase, quote, depth, word, first, after_from, ended, last_kw, ctes, with_state, fence_buf, comment = s
        if quote or depth or comment == 'block': return False
        w = word.lower()
        if w in FORBIDDEN: return False
        firstword = first or w
        if firstword not in ('select', 'with'): return False
        if after_from and w and self.tables is not None and w not in self.tables and w not in ctes: return False
        return True

    def complete(self, s):
        if self.fenced: return s[0] == 'closed'
        return s[0] == 'body' and self._body_complete(s)

    def advance(self, s, text):
        for ch in text:
            s = self._char(s, ch)
            if s is None: return None
        return s

    def _finish_word(self, s):
        """A word just ended: keyword bookkeeping and checks. -> state or None."""
        phase, quote, depth, word, first, after_from, ended, last_kw, ctes, with_state, fence_buf, comment = s
        if not word: return s
        w = word.lower()
        if w in FORBIDDEN: return None
        if not first:
            if w not in ('select', 'with'): return None
            first = w
        if after_from:
            if self.tables is not None and w not in self.tables and w not in ctes and not w.isdigit(): return None
            after_from = False
        if with_state == 'name':
            if w != 'recursive':
                ctes = ctes | {w}; with_state = 'as'
        elif with_state == 'as' and w == 'as':
            with_state = 'body'
        if w in ('from', 'join'): after_from = True
        if w == 'with' and first == 'with' and not last_kw: with_state = 'name'
        if w == 'select' and depth == 0 and with_state in ('after', 'name'): with_state = ''
        return (phase, quote, depth, '', first, after_from, ended, w, ctes, with_state, fence_buf, comment)

    def _partial_word_ok(self, s, word):
        if not s[4]:  # first word: must be a prefix of SELECT or WITH
            return any(k.startswith(word.lower()) for k in ('select', 'with'))
        if s[5] and self.tables is not None and not word.isdigit():
            w = word.lower()
            return any(t.startswith(w) for t in self.tables) or any(c.startswith(w) for c in s[8])
        return True

    def _char(self, s, ch):
        phase, quote, depth, word, first, after_from, ended, last_kw, ctes, with_state, fence_buf, comment = s
        if phase == 'closed': return s if ch in _WS else None
        if phase == 'open':
            nb = fence_buf + ch
            if any(f == nb for f in FENCE_OPEN): return ('body', '', 0, '', '', False, False, '', frozenset(), '', '', '')
            if any(f.startswith(nb) for f in FENCE_OPEN): return (phase, quote, depth, word, first, after_from, ended, last_kw, ctes, with_state, nb, comment)
            if not fence_buf and ch in _WS: return s
            return None
        if phase == 'closing':
            nb = fence_buf + ch
            if nb == '```': return ('closed',) + s[1:10] + ('', '')
            return (phase,) + s[1:10] + (nb, '') if '```'.startswith(nb) else None
        # body
        if comment == 'line':
            return s[:11] + ('',) if ch == '\n' else s
        if comment == 'block':
            if fence_buf == '*' and ch == '/': return s[:10] + ('', '')
            return s[:10] + ('*' if ch == '*' else '', 'block')
        if comment == 'dash?':
            if ch == '-': return s[:11] + ('line',)
            return self._char(s[:11] + ('',), ch)  # a lone '-' was an operator
        if comment == 'slash?':
            if ch == '*': return s[:10] + ('', 'block')
            s = s[:11] + ('',)
            return self._char(s, ch)
        if quote:
            if ch == quote: return (phase, '', depth, word, first, after_from, ended, last_kw, ctes, with_state, fence_buf, comment)
            if quote == '"' and after_from and self.tables is not None:  # quoted table name after FROM/JOIN
                nw = word + ch
                ok = any(t.startswith(nw.lower()) for t in self.tables) or any(c.startswith(nw.lower()) for c in ctes)
                return (phase, quote, depth, nw, first, after_from, ended, last_kw, ctes, with_state, fence_buf, comment) if ok else None
            return s
        if ended:
            if ch in _WS: return s
            if self.fenced and ch == '`': return ('closing',) + s[1:10] + ('`', '')
            return None
        if _WORD.match(ch):
            nw = word + ch
            return (phase, quote, depth, nw, first, after_from, ended, last_kw, ctes, with_state, fence_buf, comment) \
                if self._partial_word_ok(s, nw) else None
        # a non-word character ends the current word
        s = self._finish_word(s)
        if s is None: return None
        phase, quote, depth, word, first, after_from, ended, last_kw, ctes, with_state, fence_buf, comment = s
        if ch == '-' : return s[:11] + ('dash?',)
        if ch == '/': return s[:11] + ('slash?',)
        if ch in _WS: return s
        if not first: return None  # only comments and whitespace may precede SELECT/WITH
        if ch in "'\"":
            if ch == '"' and after_from:
                return (phase, '"', depth, '', first, after_from, ended, last_kw, ctes, with_state, fence_buf, comment)
            return (phase, ch, depth, '', first, False if ch == "'" else after_from, ended, last_kw, ctes, with_state, fence_buf, comment)
        if ch == '(':
            return (phase, quote, depth + 1, '', first, False, ended, last_kw, ctes, with_state, fence_buf, comment)
        if ch == ')':
            if depth == 0: return None
            ws = 'after' if (with_state == 'body' and depth == 1) else with_state
            return (phase, quote, depth - 1, '', first, False, ended, last_kw, ctes, ws, fence_buf, comment)
        if ch == ',':
            ws = 'name' if (with_state == 'after' and depth == 0) else with_state
            return (phase, quote, depth, '', first, False, ended, last_kw, ctes, ws, fence_buf, comment)
        if ch == ';':
            if depth: return None
            return (phase, quote, depth, '', first, False, True, last_kw, ctes, with_state, fence_buf, comment)
        if ch == '`':
            if self.fenced and depth == 0 and self._body_complete(s):
                return ('closing',) + s[1:10] + ('`', '')
            return None
        if ch == '.':  # alias.column / schema.table: the next word is a column, not a table
            return (phase, quote, depth, '', first, False, ended, last_kw, ctes, with_state, fence_buf, comment)
        return (phase, quote, depth, '', first, False, ended, last_kw, ctes, with_state, fence_buf, comment)


# ---- one line --------------------------------------------------------------------------------------------------------
class LineGrammar:
    name = 'line'

    def start(self): return ('', False)

    def advance(self, s, text):
        buf, done = s
        for ch in text:
            if done:
                if ch in _WS: continue
                return None
            if ch == '\n':
                if not buf.strip(): return None
                done = True; continue
            if ch == '<' and not buf.strip(): return None  # no <think> preamble
            buf += ch
        return (buf, done)

    def complete(self, s): return bool(s[0].strip())

    def must_stop(self, s): return s[1]


# ---- templates (TeCoD) -----------------------------------------------------------------------------------------------
SLOT_CHARS = {'number': r'[0-9.\-]', 'integer': r'[0-9\-]', 'date': r'[0-9\-]', 'string': r"[^'\n]", 'word': r'[A-Za-z0-9_ ./%+-]'}
SLOT_FULL = {'number': r'-?\d+(?:\.\d+)?', 'integer': r'-?\d+', 'date': r'\d{4}-\d{2}-\d{2}', 'string': r"[^'\n]+", 'word': r'[A-Za-z0-9_ ./%+-]+'}


class TemplateGrammar:
    """parts: [('lit', text) | ('slot', name, kind[, choices])]. State: frozenset of (part_index, offset_or_buf)."""
    name = 'template'

    def __init__(self, parts):
        self.parts = [tuple(p) for p in parts]
        self._char_re = {i: re.compile(SLOT_CHARS[p[2]]) for i, p in enumerate(self.parts) if p[0] == 'slot'}
        self._full_re = {i: re.compile(SLOT_FULL[p[2]] + '$') for i, p in enumerate(self.parts) if p[0] == 'slot'}

    def _slot_ok_full(self, i, buf):
        p = self.parts[i]
        if len(p) > 3 and p[3]: return buf in p[3]
        return bool(self._full_re[i].match(buf))

    def _slot_ok_prefix(self, i, buf):
        p = self.parts[i]
        if len(p) > 3 and p[3]: return any(c.startswith(buf) for c in p[3])
        return all(self._char_re[i].match(c) for c in buf)

    def _closure(self, states):
        """Epsilon moves: a completed slot may hand over to the next part."""
        out, todo = set(states), list(states)
        while todo:
            i, pos = todo.pop()
            if i < len(self.parts) and self.parts[i][0] == 'slot' and isinstance(pos, str) and pos and self._slot_ok_full(i, pos):
                nxt = (i + 1, 0 if i + 1 < len(self.parts) and self.parts[i + 1][0] == 'lit' else '')
                if nxt not in out: out.add(nxt); todo.append(nxt)
        return frozenset(out)

    def start(self):
        if not self.parts: return frozenset({(0, 0)})
        return self._closure({(0, 0 if self.parts[0][0] == 'lit' else '')})

    def advance(self, s, text):
        for ch in text:
            nxt = set()
            for i, pos in s:
                if i >= len(self.parts): continue
                p = self.parts[i]
                if p[0] == 'lit':
                    if p[1][pos] == ch:
                        if pos + 1 == len(p[1]): nxt.add((i + 1, 0 if i + 1 < len(self.parts) and self.parts[i + 1][0] == 'lit' else ''))
                        else: nxt.add((i, pos + 1))
                else:
                    if self._slot_ok_prefix(i, pos + ch): nxt.add((i, pos + ch))
            if not nxt: return None
            s = self._closure(nxt)
        return s

    def complete(self, s):
        return any(i >= len(self.parts) for i, _ in s)

    def must_stop(self, s):
        return self.complete(s) and all(i >= len(self.parts) for i, _ in s)

    def slots(self, text):
        """Parse a full output back into {slot: value} (first consistent parse), or None."""
        pattern = ''.join(re.escape(p[1]) if p[0] == 'lit' else f'(?P<{p[1]}>{SLOT_FULL[p[2]]})' for p in self.parts)
        m = re.fullmatch(pattern, text)
        return m.groupdict() if m else None


# ---- the mlx_lm logits processor -------------------------------------------------------------------------------------
class GrammarProcessor:
    """mlx_lm logits processor: processor(tokens, logits) -> logits with every grammar-invalid candidate masked.

    One instance per sequence (it carries the sequence's grammar state). Works on mlx arrays and on numpy arrays (tests).
    Receipt in .report(): steps, tokens checked, masked steps, violations (steps where nothing valid existed)."""

    def __init__(self, tokenizer, grammar, *, top_k=48, wide_k=2048, eos_ids=None):
        self.tok, self.grammar, self.top_k, self.wide_k = tokenizer, grammar, top_k, wide_k
        ids = eos_ids if eos_ids is not None else (getattr(tokenizer, 'eos_token_ids', None) or
                                                    [getattr(tokenizer, 'eos_token_id', None)])
        self.eos = {int(i) for i in ids if i is not None}
        self.state = grammar.start()
        self.started = False
        self.text = ''
        self.steps = self.checked = self.violations = 0
        self.active = enabled()
        self._cache = {}

    def _piece(self, tid):
        if tid not in self._cache:
            self._cache[tid] = self.tok.decode([tid])
        return self._cache[tid]

    def _consume(self, tid):
        if tid in self.eos: return
        piece = self._piece(tid)
        nxt = self.grammar.advance(self.state, piece)
        self.text += piece
        if nxt is not None: self.state = nxt
        # an unmasked (healed) step that broke the grammar keeps the last valid state; later steps continue from it

    def allowed(self, order):
        """First tokens in `order` (best first) that keep the prefix valid -> list of ids (at most top_k of them)."""
        out = []
        complete, stop = self.grammar.complete(self.state), self.grammar.must_stop(self.state)
        for tid in order:
            tid = int(tid)
            self.checked += 1
            if tid in self.eos:
                if complete: out.append(tid)
            elif not stop and self.grammar.advance(self.state, self._piece(tid)) is not None:
                out.append(tid)
            if len(out) >= self.top_k: break
        if not out and complete:
            out = sorted(self.eos)
        return out

    def __call__(self, tokens, logits):
        if self.started:
            self._consume(int(np.asarray(tokens).reshape(-1)[-1]) if not hasattr(tokens, 'tolist') else int(tokens.tolist()[-1]))
        self.started = True
        self.steps += 1
        if not self.active: return logits
        is_mx = type(logits).__module__.startswith('mlx')
        if is_mx:
            import mlx.core as mx
            row = np.array(logits.astype(mx.float32)).reshape(-1)
        else:
            row = np.asarray(logits, dtype=np.float64).reshape(-1)
        k = min(self.wide_k, row.size)
        head = np.argpartition(-row, k - 1)[:k]
        head = head[np.argsort(-row[head])]
        ids = self.allowed(head)
        if not ids and k < row.size:  # rare: search the rest of the vocabulary in rank order
            rest = np.argsort(-row)[k:]
            ids = self.allowed(rest)
        if not ids:
            self.violations += 1
            return logits  # heal: never block generation; downstream checks still run
        mask = np.full(row.size, NEG_INF, dtype=np.float32)
        mask[np.asarray(ids)] = 0.0
        if is_mx:
            import mlx.core as mx
            return logits + mx.array(mask).reshape(logits.shape).astype(logits.dtype)
        return np.asarray(logits) + mask.reshape(np.shape(logits))

    def report(self):
        return {'version': VERSION, 'grammar': self.grammar.name, 'active': self.active, 'steps': self.steps,
                'tokens_checked': self.checked, 'violations': self.violations,
                'complete': bool(self.grammar.complete(self.state))}


def processors(tokenizer, grammar, **kw):
    """[GrammarProcessor] for mlx_lm generate(..., logits_processors=...); [] when disabled."""
    return [GrammarProcessor(tokenizer, grammar, **kw)] if enabled() else []


def semantic_tables(stack_path=None):
    """Table and view names of the semantic layer (schema-aware SQL constraint); empty -> table names unconstrained."""
    import sqlite3
    from pathlib import Path
    if not stack_path: return None
    target = Path(stack_path) / 'semantic.sqlite3'
    if not target.exists(): return None
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    finally:
        db.close()
    # SQLite table-valued functions and the json1 helpers may appear after FROM
    return names | {'json_each', 'json_tree', 'generate_series', 'sqlite_master'}


def check(grammar, text):
    """Whole-output check (tests/receipts): True when `text` is a complete sentence of the grammar."""
    s = grammar.advance(grammar.start(), text)
    return s is not None and grammar.complete(s)
