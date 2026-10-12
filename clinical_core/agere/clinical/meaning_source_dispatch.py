"""New contexts use source meaning; any prior stage/progress keeps its exact route."""
from . import source_synthesis as S, availability_source_stage as A
from . import availability_reconciliation as R, meaning_source_stage as M
from pathlib import Path
import json, sqlite3, re
from ...contracts import digest

PIN = 'source-meaning-cutover.json'

def pinned(path):
    file = Path(path)/PIN
    if not file.exists(): return set()
    row = json.loads(file.read_text())
    if row.get('schema') != 'source-meaning-cutover/1' or row.get('id') != digest({k:v for k,v in row.items() if k != 'id'}):
        raise ValueError('Source interpretation cutover integrity changed')
    ids = row['historical_context_ids']
    if not isinstance(ids, list) or ids != sorted(set(ids)) or any(not isinstance(k,str) or not re.fullmatch('[a-f0-9]{64}',k) for k in ids):
        raise ValueError('Exact historical context identities required')
    return set(ids)

def pin_existing(path, *, new_context_ids=()):
    """Preserve even unsent/approval-blocked historical requests at deployment.

    new_context_ids is for a deliberate first migration of newly prepared scopes;
    it cannot change an existing pin manifest and never sends any request.
    """
    file = Path(path)/PIN
    if file.exists(): return pinned(path)
    ids = set()
    for directory in ('record-contexts', 'structured-contexts'):
        ids.update(p.stem for p in (Path(path)/directory).glob('*.json') if re.fullmatch('[a-f0-9]{64}',p.stem))
    dbfile = Path(path)/'contexts.sqlite3'
    if dbfile.exists():
        db=sqlite3.connect(dbfile.resolve().as_uri()+'?mode=ro',uri=True)
        try: ids.update(r[0] for r in db.execute('SELECT id FROM contexts'))
        finally: db.close()
    if set(new_context_ids)-ids: raise ValueError('New cutover contexts must already be saved')
    row={'schema':'source-meaning-cutover/1','historical_context_ids':sorted(ids-set(new_context_ids)),
         'new_prepared_context_ids':sorted(set(new_context_ids)),
         'scope':'Existing contexts retain original request contracts, including unsent and approval-blocked scopes. New scopes use the meaning reader.'}
    row['id']=digest(row)
    try:
        with file.open('x') as stream: json.dump(row,stream,indent=2)
    except FileExistsError: pass
    return pinned(path)

def install(path=None):
    if path is not None: pin_existing(path)
    if getattr(S.stages, 'source_meaning_adapter', False): return
    prior_stages, prior_inspect = S.stages, S.inspect
    def stages(path, key):
        original = prior_stages(path, key)
        if original[0] is not A: return original
        if key in pinned(path): return original
        *_, old_key = A.prepare(path, key)
        target = A.location(path, old_key)
        if target.exists() or target.with_name(old_key+'-progress.json').exists(): return original
        return M, R
    def inspect(path, key):
        return M.inspect(path, key) if M.exists(path, key) else prior_inspect(path, key)
    stages.source_meaning_adapter = True
    S.stages, S.inspect = stages, inspect
