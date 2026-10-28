"""Receipts and the data bundle: every number a deliverable shows resolves to an executed tool result.

A Bundle holds tool results keyed by receipt id. A receipt names the tool, its arguments, the executed definition (cohort
family statement, routing digest, simulation id, engine parameters), the SQL with its fingerprint when SQL ran, the
workspace as_of and the data fingerprint (semantic layer size / mtime / source digest) the result was computed from.

    b = Bundle(meta)
    rid = b.add('plan', {'program_kind': 'pharmacy_gdmt'}, data, definition='routing digest ...', sql=None, as_of='2030-05-27')
    b.get(rid + ':summary.by_route.enroll')      # a data-bind path

numbers() indexes every numeric value with the receipts it came from, so the verifier can say which receipt a rendered
number resolves to.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import re

SCHEMA = 'agere-deliverable-bundle/1'
PATH_RE = re.compile(r'^(R-[0-9a-f]{10})(?::(.*))?$')


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def fingerprint(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def sql_fingerprint(sql):
    if not sql:
        return None
    return 'sql:' + hashlib.sha256(re.sub(r'\s+', ' ', str(sql)).strip().encode()).hexdigest()[:16]


def receipt_id(tool, args, data_fp):
    return 'R-' + fingerprint([tool, args, data_fp])[:10]


def clean(obj):
    """JSON-safe copy: numpy scalars/arrays to Python, NaN/inf to None, tuples to lists, sets to sorted lists."""
    try:
        import numpy as np
    except ImportError:  # pragma: no cover
        np = None
    if np is not None:
        if isinstance(obj, np.ndarray):
            return clean(obj.tolist())
        if isinstance(obj, np.generic):
            return clean(obj.item())
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, set):
        return [clean(v) for v in sorted(obj, key=str)]
    if isinstance(obj, (str, int, bool)) or obj is None:
        return obj
    return str(obj)


class Bundle:
    def __init__(self, meta=None, results=None, receipts=None):
        self.meta = dict(meta or {})
        self.results = dict(results or {})
        self.receipts = dict(receipts or {})

    # --- building -------------------------------------------------------------------------------------------------
    def add(self, tool, args, data, *, definition=None, sql=None, as_of=None, data_fp=None, source=None, cached=False,
            seconds=None, model_cost_usd=None):
        rid = receipt_id(tool, args, data_fp)
        self.results[rid] = clean(data)
        self.receipts[rid] = clean({
            'id': rid, 'tool': tool, 'args': args, 'definition': definition, 'sql': sql, 'sql_fingerprint': sql_fingerprint(sql),
            'as_of': as_of, 'data_fingerprint': data_fp, 'source': source, 'computed_at': _now(), 'cached': bool(cached),
            'seconds': seconds, 'model_cost_usd': model_cost_usd,
            'result_fingerprint': fingerprint(self.results[rid])[:16]})
        return rid

    def merge(self, other):
        for rid, v in other.results.items():
            self.results.setdefault(rid, v)
            self.receipts.setdefault(rid, other.receipts[rid])
        return self

    # --- reading --------------------------------------------------------------------------------------------------
    def get(self, path):
        """'R-xxxxxxxxxx:a.b.0.c' -> the value; KeyError when it does not resolve."""
        m = PATH_RE.match(str(path).strip())
        if not m or m.group(1) not in self.results:
            raise KeyError(path)
        cur = self.results[m.group(1)]
        for part in [p for p in (m.group(2) or '').split('.') if p != '']:
            if isinstance(cur, list):
                if not re.fullmatch(r'-?\d+', part):
                    raise KeyError(path)
                cur = cur[int(part)]
            elif isinstance(cur, dict):
                if part not in cur:
                    raise KeyError(path)
                cur = cur[part]
            else:
                raise KeyError(path)
        return cur

    def to_json(self):
        return {'schema': SCHEMA, 'meta': self.meta, 'results': self.results, 'receipts': self.receipts}

    @classmethod
    def from_json(cls, d):
        return cls(d.get('meta'), d.get('results'), d.get('receipts'))

    def numbers(self):
        """{float value: set(receipt ids)} over every numeric leaf (and fully numeric strings, and the years in dates)."""
        idx = {}

        def walk(v, rid):
            if isinstance(v, bool) or v is None:
                return
            if isinstance(v, (int, float)):
                idx.setdefault(float(v), set()).add(rid)
            elif isinstance(v, str):
                s = v.strip()
                if re.fullmatch(r'-?\d+(?:\.\d+)?', s):
                    idx.setdefault(float(s), set()).add(rid)
                for y in re.findall(r'\b((?:19|20|21)\d{2})-\d{2}(?:-\d{2})?\b', s):
                    idx.setdefault(float(y), set()).add(rid)
            elif isinstance(v, dict):
                for x in v.values():
                    walk(x, rid)
            elif isinstance(v, list):
                for x in v:
                    walk(x, rid)
        for rid, data in self.results.items():
            walk(data, rid)
        walk(self.meta, 'meta')
        return idx

    def digest_for_model(self, max_list=6, max_str=300, max_chars=60000, *, shared=False):
        return digest(self, max_list=max_list, max_str=max_str, max_chars=max_chars, shared=shared)


def trim(v, max_list=6, max_str=300):
    """Long lists cut to max_list items (count kept), long strings cut; dicts capped at 60 keys."""
    if isinstance(v, dict):
        items = v.items() if max_list is None else list(v.items())[:60]
        return {k: trim(x, max_list, max_str) for k, x in items}
    if isinstance(v, list):
        if max_list is None:
            return [trim(x, max_list, max_str) for x in v]
        head = [trim(x, max_list, max_str) for x in v[:max_list]]
        return head + ([f'... {len(v) - max_list} more items (length {len(v)})'] if len(v) > max_list else [])
    if isinstance(v, str) and max_str is not None and len(v) > max_str:
        return v[:max_str] + '...'
    return v


def digest(bundle, max_list=6, max_str=300, max_chars=60000, *, shared=False):
    """A compact view of the bundle for a model prompt: every receipt with its tool, args, definition and the data with
    long lists truncated (the item count kept). The artifact embeds the full data; this only teaches the shape."""
    out = []
    for rid, rec in bundle.receipts.items():
        definition = rec.get('definition') or ''
        out.append({'receipt': rid, 'tool': rec['tool'], 'args': rec['args'], 'definition': definition if max_str is None else definition[:400],
                    'data': trim(bundle.results[rid], max_list, max_str)})
    text = json.dumps(out, default=str, separators=(',', ':'))
    if max_chars is None:
        if shared and max_list is None and max_str is None:
            encoded=json.dumps(share_json(out),default=str,separators=(',',':'))
            return encoded if len(encoded)<len(text) else text
        return text
    if len(text) > max_chars and max_list > 2:
        return digest(bundle, max_list=max(2, max_list // 2), max_str=max_str // 2, max_chars=max_chars)
    return text[:max_chars * 2]


def share_json(value, min_chars=256):
    """Lossless JSON sharing for model context; native bundle and binding paths are unchanged.

    Only complete identical subtrees/strings are interned. No sampling,
    clipping, paraphrase, field dropping or semantic deduplication occurs.
    """
    seen={};references=[]
    def walk(v,path):
        if isinstance(v,(dict,list,str)):
            key=json.dumps(v,sort_keys=True,default=str,separators=(',',':'))
            if len(key)>=min_chars:
                if key in seen:
                    target=list(seen[key]);references.append({'at':list(path),'from':target})
                    return {'$agere_shared':target}
                seen[key]=path
        if isinstance(v,dict):return {k:walk(x,path+(k,)) for k,x in v.items()}
        if isinstance(v,list):return [walk(x,path+(i,)) for i,x in enumerate(v)]
        return v
    data=walk(value,())
    return {'encoding':'agere-lossless-shared-json/1',
            'instructions':'Every original JSON value is present. At each listed references.at path, substitute the complete value from references.from in data. Only listed paths are references; identical-looking source data elsewhere is literal. This encoding is only the model view. AGERE.get/data-bind use the original receipt:path, without encoding/data prefixes. Do not display reference markers.',
            'data':data,'references':references,'original_fingerprint':fingerprint(value)}


def unshare_json(encoded):
    """Round-trip verification of the model-only representation."""
    if encoded.get('encoding')!='agere-lossless-shared-json/1':raise ValueError('Unknown shared JSON encoding')
    refs={tuple(r['at']):tuple(r['from']) for r in encoded['references']}
    if len(refs)!=len(encoded['references']):raise ValueError('Duplicate reference path')
    root=encoded['data']
    def at(path):
        v=root
        for key in path:v=v[key]
        return v
    def walk(v,path,active):
        if path in refs:
            if path in active:raise ValueError('Cyclic shared JSON reference')
            target=refs[path]
            if v!={'$agere_shared':list(target)}:raise ValueError('Shared JSON marker differs')
            return walk(at(target),target,active|{path})
        if isinstance(v,dict):return {k:walk(x,path+(k,),active) for k,x in v.items()}
        if isinstance(v,list):return [walk(x,path+(i,),active) for i,x in enumerate(v)]
        return v
    value=walk(root,(),set())
    if fingerprint(value)!=encoded.get('original_fingerprint'):raise ValueError('Shared JSON fingerprint differs')
    return value
