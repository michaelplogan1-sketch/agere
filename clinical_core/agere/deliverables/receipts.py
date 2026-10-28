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


def render_projection(bundle):
    """All saved clinical values; executable SQL travels as native provenance refs.

    This is an explicit artifact-role projection, not clinical evidence filtering.
    Raw SQL remains in the immutable/embedded bundle, available by original path.
    Only listed server-owned executable fields can become references. Source rows,
    quotes, findings, questions, review outcomes and uncertainty are untouched.
    """
    import copy
    data=[];refs=[];current=[]
    def sql(parent,key,path):
        value=parent.get(key)
        if not isinstance(value,str) or not re.match(r'^\s*(?:WITH|SELECT)\b',value,re.I):return
        ref={'native_path':path,'sha256':hashlib.sha256(value.encode()).hexdigest(),'characters':len(value),'kind':'executed_SQL_provenance'}
        refs.append(ref);parent[key]={'$agere_native_provenance':ref}
    def plans(value,path):
        # This visitor is confined to compiler/planner metadata, never source rows.
        if isinstance(value,list):
            for i,v in enumerate(value):plans(v,path+'.'+str(i))
        elif isinstance(value,dict):
            for k in list(value):
                if k in ('query','sql','executed_query'):sql(value,k,path+'.'+k)
                elif k not in ('answer','rows','table','findings','review_json'):plans(value[k],path+'.'+k)
    for rid,receipt in bundle.receipts.items():
        native=bundle.results[rid];value=copy.deepcopy(native)
        if receipt['tool']=='saved_answer' and isinstance(value,dict):
            a=value.get('answer') or {}
            current.append({'receipt':rid,'context_id':value.get('id'),'original_question':value.get('original_question') or value.get('question'),
                            'source':value.get('source'),'answer_status':a.get('status'),'verification':a.get('verification'),
                            'investigation_review':a.get('investigation_review')})
            for key in ('graph','planner_receipt'):plans(value.get(key),rid+':'+key)
            binding=(value.get('answer') or {}).get('table_binding')
            if isinstance(binding,dict):sql(binding,'query',rid+':answer.table_binding.query')
            for i,call in enumerate((value.get('investigation') or {}).get('tool_calls') or []):
                base=rid+':investigation.tool_calls.'+str(i)
                if call.get('name')=='query_sql' and isinstance(call.get('input'),dict):sql(call['input'],'sql',base+'.input.sql')
                sql(call,'sql',base+'.sql')
                if isinstance(call.get('result'),dict):sql(call['result'],'executed_query',base+'.result.executed_query')
        data.append({'receipt':rid,'tool':receipt['tool'],'args':receipt['args'],'definition':receipt.get('definition') or '', 'data':value})
    packet={'projection':'agere-artifact-render-view/1','instructions':
            'All native clinical rows, full findings, source quotes, review outcomes and uncertainty are supplied unchanged. '
            'Only executable SQL provenance strings at the listed native paths are represented by hash references; '
            'the full original SQL remains in the immutable embedded bundle. Native AGERE.get/data-bind paths are unchanged; '
            'do not use projection/data prefixes in bindings. Do not turn a hash or past machine report into a clinical fact. '
            'current_saved_status names the CURRENT answer and machine review; older critiques and prior checks in the history '
            'are observations to investigate, not the current support label or physician truth. Preserve partial/unverified status.',
            'current_saved_status':current,'data':data,'sql_provenance':refs,'native_results_fingerprint':fingerprint(bundle.results)}
    # Reconstruct against the independently retained native bundle. This ensures
    # that projection changes ONLY the declared executable fields.
    for entry in data:
        rid=entry['receipt'];restored=copy.deepcopy(entry['data'])
        for ref in refs:
            prefix=rid+':'
            if not ref['native_path'].startswith(prefix):continue
            parts=ref['native_path'][len(prefix):].split('.');parent=restored
            for key in parts[:-1]:parent=parent[int(key)] if isinstance(parent,list) else parent[key]
            original=bundle.get(ref['native_path'])
            if hashlib.sha256(original.encode()).hexdigest()!=ref['sha256']:raise ValueError('Native SQL provenance changed')
            parent[parts[-1]]=original
        if restored!=bundle.results[rid]:raise ValueError('Artifact projection changed a native clinical/source value')
    return packet


def render_for_model(bundle):
    """Compact artifact-role packet; the source-bound native bundle is untouched."""
    packet=render_projection(bundle);encoded=compact_json(packet)
    if uncompact_json(encoded)!=packet:raise ValueError('Artifact-role packet did not round-trip')
    encoded['receipt_index']=[{'receipt':rid,'tool':r['tool']} for rid,r in bundle.receipts.items()]
    return json.dumps(encoded,default=str,separators=(',',':'))


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
            encoded=compact_json(out)
            if uncompact_json(encoded)!=out:raise ValueError('Frozen artifact evidence did not round-trip')
            encoded['receipt_index']=[{'receipt':r['receipt'],'tool':r['tool']} for r in out]
            candidates=[text,json.dumps(share_json(out),default=str,separators=(',',':')),
                        json.dumps(encoded,default=str,separators=(',',':'))]
            return min(candidates,key=len)
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
    if encoded.get('encoding')=='agere-lossless-compact-json/1':return uncompact_json(encoded)
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


def compact_json(value):
    """Lossless model view: share record layouts and repeated complete values.

    Table layouts apply only at listed paths, so source arrays and marker-like
    objects remain literal. Native receipts/bindings keep their original shape.
    """
    tables=[]
    def walk(v,path):
        if isinstance(v,list):
            if len(v)>1 and isinstance(v[0],dict) and all(isinstance(r,dict) and list(r)==list(v[0]) for r in v):
                keys=list(v[0]);tables.append({'at':list(path),'columns':keys})
                return [[walk(row[k],path+(i,j)) for j,k in enumerate(keys)] for i,row in enumerate(v)]
            return [walk(row,path+(i,)) for i,row in enumerate(v)]
        if isinstance(v,dict):return {k:walk(x,path+(k,)) for k,x in v.items()}
        return v
    packed=walk(value,())
    return {'encoding':'agere-lossless-compact-json/1',
            'instructions':'This is the complete evidence, with no omitted rows or text. First resolve the shared JSON references. In the resulting payload, each tables.at path is a list of records stored as arrays: pair every row value with tables.columns in order. Other arrays and objects are literal. Native sources and bindings are unchanged. Judge values and their column meaning, never reference markers.',
            'shared':share_json({'payload':packed,'tables':tables}),
            'original_fingerprint':fingerprint(value)}


def uncompact_json(encoded):
    """Verify exact restoration of the complete model view before use."""
    if encoded.get('encoding')!='agere-lossless-compact-json/1':raise ValueError('Unknown compact JSON encoding')
    body=unshare_json(encoded['shared']);tables={tuple(t['at']):t['columns'] for t in body['tables']}
    if len(tables)!=len(body['tables']):raise ValueError('Duplicate table layout')
    used=set()
    def walk(v,path):
        if path in tables:
            keys=tables[path];used.add(path)
            if not isinstance(v,list) or len(keys)!=len(set(keys)) or any(not isinstance(k,str) for k in keys):
                raise ValueError('Invalid table layout')
            if any(not isinstance(row,list) or len(row)!=len(keys) for row in v):raise ValueError('Invalid compact row')
            return [{k:walk(row[j],path+(i,j)) for j,k in enumerate(keys)} for i,row in enumerate(v)]
        if isinstance(v,list):return [walk(x,path+(i,)) for i,x in enumerate(v)]
        if isinstance(v,dict):return {k:walk(x,path+(k,)) for k,x in v.items()}
        return v
    value=walk(body['payload'],())
    if used!=set(tables) or fingerprint(value)!=encoded.get('original_fingerprint'):raise ValueError('Compact JSON fingerprint/layout differs')
    return value
