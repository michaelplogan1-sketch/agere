"""Persistent request reservations, validated replay, and bounded provider alternatives."""
from __future__ import annotations
import json,math,os,sqlite3,time,threading,urllib.request,urllib.error
from pathlib import Path
from datetime import date
from ..contracts import digest
from ..providers import JevProvider,ClaudeProvider,EvidenceTask
from .request_lineage import ledger_event

_DEFAULT_LEDGER=Path(__file__).resolve().parents[2]/'artifacts'/'agere-full'/'provider-budget.sqlite3'
# An observed account-level rejection applies to different live requests too.
# Successful exact cache reads stay available. A new process or credential can
# probe restored service; this never changes provider configuration or budgets.
_BILLING_FAILURES={}
_BILLING_LOCK=threading.Lock()
_THROUGHPUT_FAILURES={}
_THROUGHPUT_LOCK=threading.Lock()


def _throughput_admit(credential,provider,model,key):
    """Share observed backpressure across requests; allow one recovery probe.

    This is transport pacing, not a request, population or spending allowance.
    Exact validated cache reads occur before this check.
    """
    with _THROUGHPUT_LOCK:
        state=_THROUGHPUT_FAILURES.get(credential)
        if state is None:return None
        remaining=max(0.,state['until']-time.monotonic())
        if remaining or state['probing']:
            error=ProviderUnavailable(provider+' is cooling down after an observed rate limit; configured alternatives remain available.',kind='rate_limit')
            error.provider_receipt={'provider':provider,'request_hash':key,'attempted':False,
                'blocked_by_request_hash':state['request_hash'],'reason':'throughput_backpressure',
                'retry_after_seconds':remaining,'recovery_probe_in_flight':state['probing'],
                'model':model,'cost_usd':None,'usage':None}
            raise error
        state['probing']=True
        return state


def _throughput_rejected(credential,key,headers,probe):
    from email.utils import parsedate_to_datetime
    from datetime import timezone
    supplied=headers.get('Retry-After')if headers else None
    delay=None
    if supplied is not None:
        try:delay=float(supplied)
        except (TypeError,ValueError):
            try:
                at=parsedate_to_datetime(supplied)
                if at.tzinfo is None:at=at.replace(tzinfo=timezone.utc)
                delay=at.timestamp()-time.time()
            except (TypeError,ValueError,OverflowError):pass
        if delay is not None and (not math.isfinite(delay)or delay<=0):delay=None
    with _THROUGHPUT_LOCK:
        current=_THROUGHPUT_FAILURES.get(credential)
        # Other requests already in flight may fail too. They do not multiply
        # the cooldown; only a failed recovery probe increases it.
        if current is not None and current is not probe:return
        try:base=float(os.environ.get('AGERE_PROVIDER_BACKOFF_SECONDS','2'))
        except ValueError:base=2.
        if not math.isfinite(base)or base<=0:base=2.
        if delay is None:delay=max(base,probe['delay']*2 if probe else base)
        _THROUGHPUT_FAILURES[credential]={'request_hash':key,'until':time.monotonic()+delay,
            'delay':delay,'probing':False}


def _throughput_release(credential,probe,success):
    if probe is None:return
    with _THROUGHPUT_LOCK:
        if _THROUGHPUT_FAILURES.get(credential)is probe:
            if success:del _THROUGHPUT_FAILURES[credential]
            else:probe['probing']=False


def default_ledger_path():
    """The provider ledger every product call opens: AGERE_LEDGER_PATH when set (a study arm, a tenant, a test), else the
    shared ledger. Read at call time, so a process can point every caller at its own ledger before it asks anything."""
    return Path(os.environ.get('AGERE_LEDGER_PATH') or _DEFAULT_LEDGER)

class BudgetLedger:
    def __init__(self,path,*,cap_usd=None,legacy_summary=None):
        if cap_usd is not None and (not math.isfinite(cap_usd) or cap_usd<0):raise ValueError('Invalid cap')
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.path=path
        self.db=sqlite3.connect(path,timeout=30,check_same_thread=False)  # one connection per ledger object; never shared concurrently
        self.db.executescript('''CREATE TABLE IF NOT EXISTS authorization (id INTEGER PRIMARY KEY, cap REAL);
        CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY,provider TEXT,model TEXT,reserved REAL,status TEXT,response TEXT,error TEXT,seconds REAL);
        CREATE TABLE IF NOT EXISTS imports (id TEXT PRIMARY KEY,reserved REAL);
        CREATE TABLE IF NOT EXISTS request_retries (seq INTEGER PRIMARY KEY,request_key TEXT,attempt_key TEXT UNIQUE,payload TEXT);
        CREATE INDEX IF NOT EXISTS request_retries_request ON request_retries(request_key,seq);
        CREATE TRIGGER IF NOT EXISTS request_retries_immutable BEFORE UPDATE ON request_retries BEGIN SELECT RAISE(ABORT,'Immutable retry authorization');END;
        CREATE TRIGGER IF NOT EXISTS request_retries_no_delete BEFORE DELETE ON request_retries BEGIN SELECT RAISE(ABORT,'Immutable retry authorization');END;''')
        with self.db:
            # New ledgers are uncapped unless a caller passes an explicit cap (Mike: no budget caps).
            self.db.execute('INSERT OR IGNORE INTO authorization VALUES (1,?)',(cap_usd,))
            existing=self.db.execute('SELECT cap FROM authorization WHERE id=1').fetchone()[0]
            if cap_usd is not None and existing!=cap_usd:raise ValueError('Cannot silently change existing authorization')
            if legacy_summary and Path(legacy_summary).exists():
                legacy=json.loads(Path(legacy_summary).read_text())
                self.db.execute('INSERT OR IGNORE INTO imports VALUES (?,?)',(digest(str(Path(legacy_summary).resolve())),float(legacy['reserved_usd'])))
        self.cap=existing
    def resolve_request_key(self,key):
        """Follow explicit retry authorizations; never alter the failed attempt."""
        seen=set()
        while True:
            if key in seen:raise ValueError('Cyclic request retry authorization')
            seen.add(key)
            row=self.db.execute('SELECT attempt_key FROM request_retries WHERE request_key=? ORDER BY seq DESC LIMIT 1',(key,)).fetchone()
            if row is None:return key
            key=row[0]
    def authorize_retry(self,key,authorization):
        """Create one auditable new attempt for a terminal failure, explicitly.

        An unused authorization or a pending/validated request cannot spawn a
        duplicate. Original reservations, responses and errors remain intact.
        """
        if not isinstance(authorization,str)or not authorization.strip():raise ValueError('Retry authorization evidence required')
        from datetime import datetime,timezone
        from uuid import uuid4
        self.db.execute('BEGIN IMMEDIATE')
        try:
            prior=self.resolve_request_key(key)
            row=self.db.execute('SELECT * FROM calls WHERE id=?',(prior,)).fetchone()
            if row is None or row[4]!='failed':raise ValueError('Explicit retry requires a terminal failed attempt; pending, validated and unused authorizations stay protected')
            payload={'request_key':key,'prior_attempt_key':prior,'prior_attempt_digest':digest(row),
                     'authorization':authorization,'at':datetime.now(timezone.utc).isoformat(),'nonce':str(uuid4())}
            attempt=digest(payload)
            self.db.execute('INSERT INTO request_retries(request_key,attempt_key,payload) VALUES (?,?,?)',(key,attempt,json.dumps(payload)))
            self.db.commit()
            return {'logical_request_hash':key,'request_hash':attempt,'prior_attempt_key':prior,'prior_failed_attempt_preserved':True}
        except Exception:self.db.rollback();raise
    def cached_equivalent(self,key,provider,model):
        """Read an exact producer-proven cache binding; never replace an attempt.

        Actual requests and explicit retries take precedence. The target stays
        the original validated, priced attempt, including its immutable bytes.
        This index is not a reservation, retry authorization or clinical label.
        """
        if self.db.execute('SELECT 1 FROM calls WHERE id=?',(key,)).fetchone():return None
        if self.db.execute('SELECT 1 FROM request_retries WHERE attempt_key=?',(key,)).fetchone():return None
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table'AND name='request_equivalences'").fetchone():return None
        row=self.db.execute('SELECT payload FROM request_equivalences WHERE request_key=?',(key,)).fetchone()
        if not row:return None
        saved=json.loads(row[0]);proof={k:v for k,v in saved.items()if k!='payload_digest'}
        if(saved.get('schema')!='native-query-timing-cache/1'or saved.get('request_key')!=key
           or saved.get('provider')!=provider or saved.get('model')!=model
           or saved.get('payload_digest')!=digest(proof)):
            raise ValueError('Equivalent request cache binding changed')
        original=self.db.execute('SELECT * FROM calls WHERE id=?',(saved.get('attempt_key'),)).fetchone()
        if(not original or original[1]!=provider or original[2]!=model or original[4]!='validated'
           or not original[5]or digest(original)!=saved.get('attempt_digest')):
            raise ValueError('Equivalent request cache target changed or is not validated')
        return {'raw':json.loads(original[5]),'request_hash':original[0],
                'cache_compatibility':{'schema':saved['schema'],'canonical_request_hash':key,
                    'original_request_hash':original[0],'proof_digest':saved['proof_digest']}}
    def remember_equivalent(self,key,attempt,provider,model,proof):
        """Append a checked cache index without creating or editing billable rows."""
        with self.db:
            self.db.execute('''CREATE TABLE IF NOT EXISTS request_equivalences(
                request_key TEXT PRIMARY KEY,payload TEXT NOT NULL)''')
            # A real failed/pending request or retry authorization is never
            # hidden by a successful older equivalent response.
            if self.db.execute('SELECT 1 FROM calls WHERE id=?',(key,)).fetchone():return False
            if self.db.execute('SELECT 1 FROM request_retries WHERE attempt_key=?',(key,)).fetchone():return False
            row=self.db.execute('SELECT * FROM calls WHERE id=?',(attempt,)).fetchone()
            if(not row or row[1]!=provider or row[2]!=model or row[4]!='validated'or not row[5]):
                raise ValueError('Equivalent cache needs a validated same-provider/model attempt')
            value={'schema':'native-query-timing-cache/1','request_key':key,'attempt_key':attempt,
                   'provider':provider,'model':model,'attempt_digest':digest(row),'proof_digest':digest(proof)}
            value['payload_digest']=digest(value)
            self.db.execute('INSERT OR IGNORE INTO request_equivalences VALUES (?,?)',(key,json.dumps(value)))
        return True
    def receipt(self):
        from .economics import usage_cost
        self.cap=self.db.execute('SELECT cap FROM authorization WHERE id=1').fetchone()[0]
        estimates=[usage_cost(model,json.loads(raw).get('usage')) for model,raw in self.db.execute("SELECT model,response FROM calls WHERE status='validated'")]
        imports=self.db.execute('SELECT COALESCE(SUM(reserved),0) FROM imports').fetchone()[0]
        current=self.db.execute('SELECT COALESCE(SUM(reserved),0) FROM calls').fetchone()[0]
        return {'authorized_cap_usd':self.cap,'legacy_reserved_usd':imports,'current_reserved_usd':current,
                'spending_limit':'uncapped' if self.cap is None else 'capped',
                'remaining_reservation_usd':None if self.cap is None else self.cap-imports-current,
                'basis':'Usage and reservations are tracked without a spending cap' if self.cap is None else 'Conservative reservations, not an invoice; failed attempts retain reservation',
                'new_estimated_api_usd':sum(x['estimated_usd'] for x in estimates if x),'unpriced_successes':sum(x is None for x in estimates),
                'calls':dict(self.db.execute('SELECT status,COUNT(*) FROM calls GROUP BY status'))}
    def reserve(self,key,provider,model,reservation=.5):
        if type(reservation) not in (int,float) or not math.isfinite(reservation) or reservation<0:
            raise ValueError('Finite nonnegative accounting reservation required')
        ledger_event(self,'reservation_requested',key,provider=provider,model=model)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT status,response FROM calls WHERE id=?',(key,)).fetchone()
            if row and row[0]=='abandoned':
                # Reserved by a process that died before any response: a new attempt is explicit.
                self.db.execute('DELETE FROM calls WHERE id=?',(key,));row=None
                ledger_event(self,'abandoned_reservation_replaced',key,provider=provider,model=model)
            if row:
                self.db.commit()
                if row[0]=='validated':
                    ledger_event(self,'cache_reused',key,provider=provider,model=model)
                    from .clinical.cost_telemetry import tag;tag(self,key,reused=True)  # per-question attribution
                    return json.loads(row[1])
                ledger_event(self,'retry_blocked',key,provider=provider,model=model,status=row[0])
                raise ValueError('Prior request pending/failed; explicit new attempt required, no silent paid retry')
            # Authorization uses conservative reservations, not the separately
            # reported API estimates. Do not price every historical response
            # while holding the write lock for each new request.
            self.cap=self.db.execute('SELECT cap FROM authorization WHERE id=1').fetchone()[0]
            if self.cap is not None:
                imports=self.db.execute('SELECT COALESCE(SUM(reserved),0) FROM imports').fetchone()[0]
                current=self.db.execute('SELECT COALESCE(SUM(reserved),0) FROM calls').fetchone()[0]
                if self.cap-imports-current<reservation:raise ValueError('Shared authorization exhausted')
            self.db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?,?)',(key,provider,model,reservation,'pending',None,None,0))
            self.db.commit()
            ledger_event(self,'reserved',key,provider=provider,model=model,status='pending')
            from .clinical.cost_telemetry import tag;tag(self,key)  # per-question attribution (cost_telemetry)
            return None
        except Exception:self.db.rollback();raise
    def abandon_pending(self):
        """At service start, requests still pending belong to a dead process; mark them abandoned."""
        with self.db:
            n=self.db.execute("UPDATE calls SET status='abandoned',error='process ended before response' WHERE status='pending'").rowcount
        return n
    def finish(self,key,response=None,error=None,seconds=0):
        status='validated' if response is not None and error is None else 'failed'
        with self.db:self.db.execute('UPDATE calls SET status=?,response=?,error=?,seconds=? WHERE id=?',
            (status,json.dumps(response) if response is not None else None,error,seconds,key))
        ledger_event(self,'request_finished',key,status=status)
    def extend_authorization(self,new_cap,authorization):
        """Record an explicit user authorization; never invoked by ordinary inference."""
        if self.cap is None or type(new_cap) not in (int,float) or not math.isfinite(new_cap) or new_cap<=self.cap:
            raise ValueError('A higher finite operating cap is required')
        if not isinstance(authorization,str) or not authorization.strip():raise ValueError('Authorization evidence required')
        self.db.execute('CREATE TABLE IF NOT EXISTS authorization_changes (id TEXT PRIMARY KEY,payload TEXT NOT NULL)');self.db.commit()
        entry={'prior_cap_usd':self.cap,'operating_cap_usd':new_cap,'authorization':authorization,
               'scope':'Conservative local operating guardrail; no claim the user specified this exact new cap'}
        with self.db:
            current=self.db.execute('SELECT cap FROM authorization WHERE id=1').fetchone()[0]
            if current!=self.cap:raise ValueError('Stale authorization state')
            self.db.execute('INSERT INTO authorization_changes VALUES (?,?)',(digest(entry),json.dumps(entry)))
            self.db.execute('UPDATE authorization SET cap=? WHERE id=1',(new_cap,))
        self.cap=new_cap;return entry

    def remove_spending_cap(self,authorization):
        """Persist explicit user authorization without erasing accounting history."""
        if not isinstance(authorization,str) or not authorization.strip():raise ValueError('Authorization evidence required')
        self.db.execute('CREATE TABLE IF NOT EXISTS authorization_changes (id TEXT PRIMARY KEY,payload TEXT NOT NULL)');self.db.commit()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            prior=self.db.execute('SELECT cap FROM authorization WHERE id=1').fetchone()[0]
            entry={'prior_cap_usd':prior,'operating_cap_usd':None,'spending_limit':'uncapped',
                   'authorization':authorization,'changed':prior is not None,
                   'scope':'User-authorized removal of the local spending cap; usage, reservations and request protections retained'}
            if prior is not None:
                self.db.execute('INSERT INTO authorization_changes VALUES (?,?)',(digest(entry),json.dumps(entry)))
                self.db.execute('UPDATE authorization SET cap=NULL WHERE id=1')
            self.db.commit();self.cap=None;return entry
        except Exception:self.db.rollback();raise

    def settle_completed(self):
        """Explicit, auditable reservation settlement; never release failed/pending calls.

        Keep twice the priced returned usage plus one cent, rounded UP to a cent.
        This is a conservative spending estimate, not an invoice reconciliation.
        Authorization, imported legacy holds and unsuccessful attempts stay unchanged.
        """
        from .economics import usage_cost
        self.db.execute('CREATE TABLE IF NOT EXISTS settlements (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        self.db.commit();self.db.execute('BEGIN IMMEDIATE');settled=[]
        try:
            for key,model,reserved,raw in self.db.execute("SELECT id,model,reserved,response FROM calls WHERE status='validated'").fetchall():
                if self.db.execute('SELECT 1 FROM settlements WHERE id=?',(key,)).fetchone():continue
                response=json.loads(raw)
                if response.get('model')!=model:continue
                usage=response.get('usage',{})
                counters=[usage.get(k,0) for k in ('cache_creation_input_tokens','cache_read_input_tokens')]
                counters+=list(usage.get('cache_creation',{}).values())
                if any(type(n) is not int or n<0 for n in counters):continue
                price=usage_cost(model,usage)
                if not price or not math.isfinite(price['estimated_usd']) or price['estimated_usd']<0:continue
                held=math.ceil((2*price['estimated_usd']+.01)*100)/100
                if held>=reserved:continue
                entry={'request_hash':key,'prior_reservation_usd':reserved,'held_usd':held,
                       'usage':usage,'price':price,'method':'2x token-price estimate plus $0.01, rounded up to cents',
                       'invoice_verified':False}
                self.db.execute('INSERT INTO settlements VALUES (?,?)',(key,json.dumps(entry)))
                self.db.execute('UPDATE calls SET reserved=? WHERE id=?',(held,key));settled.append(entry)
            self.db.commit();return settled
        except Exception:self.db.rollback();raise

    def close(self):self.db.close()

class PersistentGateway:
    ENDPOINTS={'jev':('https://api.typesafe.ai/v1/systemone','TYPESAFE_API_KEY'),
               'claude':('https://api.anthropic.com/v1/messages','ANTHROPIC_API_KEY'),
               'perplexity':('https://api.perplexity.ai/v1/decisions','PERPLEXITY_API_KEY'),
               # Agent API (web-grounded answers with search results); preset-routed, model chosen server-side.
               'perplexity_agent':('https://api.perplexity.ai/v1/responses','PERPLEXITY_API_KEY')}
    MODELS={'jev':'jev-1.13.0','claude':'claude-opus-5-5','perplexity':'pplx-decider-v1.1-27b'}
    def __init__(self,ledger,allow_live=False,transport=None):
        self.ledger=ledger;self.allow_live=allow_live;self.transport=transport or urllib.request.urlopen
        self.events=[]
    def classify(self,provider,task):
        # Future source dates cannot enter any provider request.
        date.fromisoformat(task.as_of)
        for evidence in task.evidence:
            if evidence['date']!='unknown':
                date.fromisoformat(evidence['date'])
                if evidence['date']>task.as_of:raise ValueError('Evidence beyond cutoff')
        parser=ClaudeProvider(None) if provider=='claude' else JevProvider(None,model=self.MODELS[provider])
        if provider=='claude':
            from .model_policy import claude_model
            parser.model=claude_model(parser.model,track=True)  # Opus only for deliverables (model_policy)
        body=parser.request(task)
        parser.name=provider  # Perplexity Decisions and Jev share this typed Choice contract.
        identity={'provider':provider,'body':body,'contract':2}
        long_context=provider=='jev' and getattr(task,'long_context',False)
        if long_context:identity['transport']={'TypeSafe-Danger-Zone-Long-Context':'true'}
        key=self.ledger.resolve_request_key(digest(identity))
        old=self.ledger.db.execute('SELECT status,response FROM calls WHERE id=?',(key,)).fetchone()
        if old and old[0]=='validated':
            from .clinical.cost_telemetry import tag
            tag(self.ledger,key,reused=True)
            raw=json.loads(old[1]);return {**self._parse(parser,task,raw),'request_hash':key,'cache_hit':True,'new_paid_api_usd':0,'usage':raw.get('usage')}
        if not self.allow_live: return {'status':'prepared','provider':provider,'request_hash':key,'request':body}
        endpoint,variable=self.ENDPOINTS[provider];secret=os.environ.get(variable)
        if not secret:raise ValueError('Missing '+variable)
        credential=digest([provider,endpoint,secret])
        with _BILLING_LOCK:blocked=_BILLING_FAILURES.get(credential)
        if blocked:
            error=ProviderUnavailable(provider+' billing is unavailable after a recorded quota rejection in this process; use the configured fallback. Restored credits can be probed in a new process.',kind='billing')
            error.provider_receipt={'provider':provider,'request_hash':key,'attempted':False,
                'blocked_by_request_hash':blocked,'reason':'billing_circuit_open',
                'model':body['model'],'cost_usd':None,'usage':None}
            raise error
        encoded=json.dumps(body,allow_nan=False).encode()
        # The old 20 KB pilot quota prevented whole-document and compound-answer review before a provider was called.
        # Use the actual TypeSafe transport limit; other providers report their own context/transport constraints.
        # https://docs.typesafe.ai/api/errors (verified 2026-10-26)
        if provider=='jev' and len(encoded)>32*1024*1024:
            raise ValueError('TypeSafe request exceeds its 32 MiB transport limit; partition the evidence explicitly')
        probe=_throughput_admit(credential,provider,body['model'],key)
        try:cached=self.ledger.reserve(key,provider,body['model'])
        except Exception:
            _throughput_release(credential,probe,False)
            raise
        if cached is not None:
            _throughput_release(credential,probe,False)
            return {**self._parse(parser,task,cached),'request_hash':key,'cache_hit':True,'new_paid_api_usd':0,'usage':cached.get('usage')}
        headers={'Content-Type':'application/json'}
        if long_context:headers['TypeSafe-Danger-Zone-Long-Context']='true'
        if provider=='claude':headers.update({'x-api-key':secret,'anthropic-version':'2023-06-01'})
        else:headers['Authorization']='Bearer '+secret
        start=time.perf_counter()
        raw=None;success=False
        try:
            with self.transport(urllib.request.Request(endpoint,data=encoded,headers=headers),timeout=45) as response:raw=json.loads(response.read())
            if raw.get('model')!=body['model']:raise ValueError('Unexpected actual model revision')
            parsed=self._parse(parser,task,raw)
            self.ledger.finish(key,response=raw,seconds=time.perf_counter()-start)
            success=True
            from .economics import usage_cost
            return {**parsed,'request_hash':key,'usage':raw.get('usage'),'cost_estimate':usage_cost(body['model'],raw.get('usage'))}
        except Exception as e:
            if raw is None and isinstance(getattr(e,'provider_response',None),dict):raw=e.provider_response
            error=type(e).__name__+': '+str(e)
            if isinstance(e,urllib.error.HTTPError):
                try:detail=e.read().decode('utf-8',errors='replace')
                except Exception:detail='Error body unavailable'
                raw={'provider_error':{'http_status':e.code,'body':detail}}
                if e.headers and e.headers.get('Retry-After')is not None:
                    raw['provider_error']['retry_after']=e.headers.get('Retry-After')
                error='HTTP '+str(e.code)+': '+detail
                if e.code==429:_throughput_rejected(credential,key,e.headers,probe)
            self.ledger.finish(key,response=raw,error=error,seconds=time.perf_counter()-start)
            quota=isinstance(e,urllib.error.HTTPError)and e.code==402
            if isinstance(e,urllib.error.HTTPError)and e.code==401:
                try:quota=json.loads(detail).get('error',{}).get('type')=='insufficient_quota'
                except (ValueError,AttributeError):pass
            if quota:
                e.billing_quota=True
                with _BILLING_LOCK:_BILLING_FAILURES[credential]=key
            from .economics import usage_cost
            price=usage_cost(body['model'],raw.get('usage')) if isinstance(raw,dict) and raw.get('model')==body['model'] else None
            e.provider_receipt={'attempt_id':key,'request_hash':key,'attempted':True,'model':body['model'],
                                'cost_usd':(price or {}).get('estimated_usd'),'usage':raw.get('usage') if isinstance(raw,dict) else None}
            raise
        finally:_throughput_release(credential,probe,success)
    def cascade(self,task,*,primary='jev',acceptance_threshold=None):
        first=self.classify(primary,task)
        if first.get('status')=='prepared':return {'status':'prepared_cascade','primary':first,'frontier':self.classify('claude',task)}
        probability=first.get('probabilities',{}).get(first.get('label'),0)
        if acceptance_threshold is not None and probability>=acceptance_threshold and not first.get('uncertain',True):
            return {**first,'route':'typed_specialist','threshold_basis':'caller-supplied calibration; task-specific validation required'}
        return {**self.classify('claude',task),'route':'frontier','prior':first,'reason':'Typed specialist uncertain or no calibrated acceptance threshold'}

    @staticmethod
    def _parse(parser,task,raw):
        class Replay:
            def send(self,*args):return raw
        if raw.get('model') != parser.model: raise ValueError('Unexpected cached model revision')
        parser.gateway=Replay();return parser.classify(task)


def supported_task(subject,cutoff,records,options):
    from .warehouse import text_of
    evidence=[]
    for r in records:
        text=text_of(r)
        if not text:continue
        evidence.append({'id':r['record_id'],'note_id':r['record_id'],'date':r['event_date'],'text':text})
    return EvidenceTask(subject,cutoff,{'unknown':'Insufficient, contradictory or unavailable evidence',**options},evidence)


# -- token economics: model tiers, stable cache prefixes, provider fallback ----------------------------------------------
# Claude 5 family, most capable first. The question path is one ladder, [Haiku, Sonnet] (model_policy.ladder): every role starts on
# Haiku and Sonnet is the escalation after a Haiku failure (Mike 2026-10-24). Opus is deliverable-only.
from .model_policy import HAIKU,SONNET,OPUS
CLAUDE_FAMILY=(OPUS,SONNET,HAIKU)
PLANNER_MODEL=HAIKU
ESCALATION_MODEL=SONNET


def model_tiers():
    """{'planner','judge','escalation'} model ids. AGERE_PLANNER_MODEL drafts (default Haiku 5.5), AGERE_SHARED_JUDGE_MODEL
    reviews (default: the planner model, so a Haiku review), AGERE_ESCALATION_MODEL re-plans and re-reviews after a failed review
    (default Sonnet 5.5; inside a question it runs only on the cascade's escalation rung, model_policy.escalation())."""
    planner=os.environ.get('AGERE_PLANNER_MODEL') or PLANNER_MODEL
    return {'planner':planner,'judge':os.environ.get('AGERE_SHARED_JUDGE_MODEL') or planner,
            'escalation':os.environ.get('AGERE_ESCALATION_MODEL') or ESCALATION_MODEL}


def cache_control():
    """Ephemeral cache marker; 1h TTL by default (AGERE_PROMPT_CACHE_TTL=5m for the 5-minute cache)."""
    ttl=os.environ.get('AGERE_PROMPT_CACHE_TTL','1h')
    return {'type':'ephemeral','ttl':'1h'} if ttl!='5m' else {'type':'ephemeral'}


def _canonical(value):
    if isinstance(value,dict):return {str(k):_canonical(v) for k,v in value.items()}
    if isinstance(value,(set,frozenset)):return sorted((_canonical(v) for v in value),key=lambda v:json.dumps(v,sort_keys=True,default=str))
    if isinstance(value,(list,tuple)):return [_canonical(v) for v in value]
    return value


def stable_json(value):
    """Byte-identical serialization for cached prefixes: sorted keys, sorted sets, compact separators (hash-seed independent)."""
    return json.dumps(_canonical(value),ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str)


class ProviderUnavailable(RuntimeError):
    """The provider could not serve the request (billing/credit, rate limit, overload, 5xx, transport) after one retry
    and the fallback model. .kind names the class; .attempts lists [{'model','kind','error'}]."""
    def __init__(self,message,*,kind=None,attempts=()):
        super().__init__(message);self.kind=kind;self.attempts=list(attempts)


_PROVIDER_PATTERNS=(('cancelled',('request_cancelled:',)),
                    ('billing',('credit balance','billing','insufficient credit','insufficient_quota','payment required','http 402')),
                    ('rate_limit',('http 429','rate limit','rate_limit','too many requests','claude subscription limit reached')),
                    ('overloaded',('http 529','overloaded')),
                    ('server',('http 500','http 502','http 503','http 504','internal server error','service unavailable','bad gateway','api_error')),
                    ('timeout',('timed out','timeout','connection reset','connection refused','remote end closed','urlerror')))


def subscription_limit(message):
    """Recognize subscription quotas, including the CLI's session-reset wording.

    Context-window and output-token bounds are request failures, not a plan reset.
    """
    text = str(message).lower().replace('\u2019', "'")
    return any(cue in text for cue in ("you've hit your limit", 'session limit',
               'weekly limit', 'usage limit', 'subscription limit', 'rate limit', 'rate_limit'))


def classify_provider_error(exc):
    """Provider failure class, explicit 'cancelled' request stop, or None.
    Request errors (400 schema, parse failures, size bounds) return None and are never retried on another model."""
    if isinstance(exc,ProviderUnavailable):return exc.kind
    if getattr(exc,'billing_quota',False):return 'billing'
    code=getattr(exc,'code',None)
    if isinstance(exc,urllib.error.HTTPError) and isinstance(code,int):
        if code==402:return 'billing'
        if code==429:return 'rate_limit'
        if code==529:return 'overloaded'
        if code>=500:return 'server'
    elif isinstance(exc,(TimeoutError,ConnectionError,urllib.error.URLError)):return 'timeout'
    text=(type(exc).__name__+': '+str(exc)).lower()
    if subscription_limit(text):return 'rate_limit'
    for kind,needles in _PROVIDER_PATTERNS:
        if any(n in text for n in needles):return kind
    return None


def fallback_model(model):
    """AGERE_FALLBACK_MODEL (alias AGERE_INTERPRETER_FALLBACK) when set and different, else the next CHEAPER model in the Claude 5
    family (Opus -> Sonnet -> Haiku), never a more expensive one: Haiku has no Claude fallback, so a failed Haiku call is never moved
    to Sonnet here. That move is the ladder's escalation (model_policy), which the cascade makes only after every Haiku attempt has
    failed; System One takes over in the cascade otherwise. None for models outside the family."""
    chosen=os.environ.get('AGERE_FALLBACK_MODEL') or os.environ.get('AGERE_INTERPRETER_FALLBACK')
    if chosen:return chosen if chosen!=model else None
    if model not in CLAUDE_FAMILY:return None
    i=CLAUDE_FAMILY.index(model)
    return CLAUDE_FAMILY[i+1] if i+1<len(CLAUDE_FAMILY) else None


def call_with_fallback(call,model,*,sleep=time.sleep,backoff=None,fallback=None):
    """call(model) -> result. A provider-unavailable failure is retried once on the same model after a backoff
    (AGERE_PROVIDER_BACKOFF_SECONDS, default 2; billing errors skip the retry), then once on the fallback model; when that
    also fails a typed ProviderUnavailable is raised. Request errors propagate unchanged on the first attempt."""
    backoff=float(os.environ.get('AGERE_PROVIDER_BACKOFF_SECONDS','2')) if backoff is None else backoff
    alternate=fallback_model(model) if fallback is None else fallback
    if alternate is not None:
        from .model_policy import claude_model,in_question
        if in_question() and claude_model(alternate)==claude_model(model):alternate=None  # inside a question both resolve to the same rung: a repeat, not a fallback
    attempts=[]
    for target in (model,alternate):
        if target is None:continue
        for retry in (False,True):
            try:return call(target)
            except Exception as exc:
                kind=classify_provider_error(exc)
                if kind is None:raise
                attempts.append({'model':target,'kind':kind,'error':str(exc)[:300]})
                if kind=='cancelled':raise  # an interrupted request is not an invitation to retry another model
                if kind=='rate_limit' and os.environ.get('AGERE_CLAUDE_TRANSPORT')=='subscription' and str(target).startswith('claude'):
                    raise ProviderUnavailable('Claude subscription limit reached; stopped without retry or API fallback: '+str(exc)[:200],
                                              kind='rate_limit',attempts=attempts) from None
                if retry or kind=='billing' or target!=model:break
                if backoff>0:sleep(backoff)
    last=attempts[-1]
    raise ProviderUnavailable(f"provider_unavailable ({last['kind']}) after {len(attempts)} attempt(s): "+'; '.join(
        f"{a['model']}: {a['error'][:120]}" for a in attempts),kind=last['kind'],attempts=attempts) from None
