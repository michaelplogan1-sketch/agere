"""Persistent request reservations, validated replay, and bounded provider alternatives."""
from __future__ import annotations
import json,math,os,sqlite3,time,urllib.request,urllib.error
from pathlib import Path
from datetime import date
from ..contracts import digest
from ..providers import JevProvider,ClaudeProvider,EvidenceTask
from .request_lineage import ledger_event

class BudgetLedger:
    def __init__(self,path,*,cap_usd=None,legacy_summary=None):
        if cap_usd is not None and (not math.isfinite(cap_usd) or cap_usd<0):raise ValueError('Invalid cap')
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,timeout=30)
        self.db.executescript('''CREATE TABLE IF NOT EXISTS authorization (id INTEGER PRIMARY KEY, cap REAL);
        CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY,provider TEXT,model TEXT,reserved REAL,status TEXT,response TEXT,error TEXT,seconds REAL);
        CREATE TABLE IF NOT EXISTS imports (id TEXT PRIMARY KEY,reserved REAL);''')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO authorization VALUES (1,?)',(10. if cap_usd is None else cap_usd,))
            existing=self.db.execute('SELECT cap FROM authorization WHERE id=1').fetchone()[0]
            if cap_usd is not None and existing!=cap_usd:raise ValueError('Cannot silently change existing authorization')
            if legacy_summary and Path(legacy_summary).exists():
                legacy=json.loads(Path(legacy_summary).read_text())
                self.db.execute('INSERT OR IGNORE INTO imports VALUES (?,?)',(digest(str(Path(legacy_summary).resolve())),float(legacy['reserved_usd'])))
        self.cap=existing
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
                    return json.loads(row[1])
                ledger_event(self,'retry_blocked',key,provider=provider,model=model,status=row[0])
                raise ValueError('Prior request pending/failed; explicit new attempt required, no silent paid retry')
            remaining=self.receipt()['remaining_reservation_usd']
            if remaining is not None and remaining<reservation:raise ValueError('Shared authorization exhausted')
            self.db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?,?)',(key,provider,model,reservation,'pending',None,None,0))
            self.db.commit()
            ledger_event(self,'reserved',key,provider=provider,model=model,status='pending')
            return None
        except Exception:self.db.rollback();raise
    def abandon_pending(self):
        """At service start, requests still pending belong to a dead process; mark them abandoned."""
        with self.db:
            n=self.db.execute("UPDATE calls SET status='abandoned',error='process ended before response' WHERE status='pending'").rowcount
        return n
    def finish(self,key,response=None,error=None,seconds=0):
        with self.db:self.db.execute('UPDATE calls SET status=?,response=?,error=?,seconds=? WHERE id=?',
            ('validated' if response is not None else 'failed',json.dumps(response) if response is not None else None,error,seconds,key))
        ledger_event(self,'request_finished',key,status='validated' if response is not None else 'failed')
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
               'perplexity':('https://api.perplexity.ai/v1/decisions','PERPLEXITY_API_KEY')}
    MODELS={'jev':'jev-1.13.0','claude':'claude-opus-5-5','perplexity':'pplx-decider-v1.1-27b'}
    def __init__(self,ledger,allow_live=False,transport=None):
        self.ledger=ledger;self.allow_live=allow_live;self.transport=transport or urllib.request.urlopen
        self.events=[]
    def classify(self,provider,task):
        # Future source dates cannot enter any provider request.
        date.fromisoformat(task.as_of)
        for evidence in task.evidence:
            date.fromisoformat(evidence['date'])
            if evidence['date']>task.as_of:raise ValueError('Evidence beyond cutoff')
        parser=ClaudeProvider(None) if provider=='claude' else JevProvider(None,model=self.MODELS[provider])
        body=parser.request(task)
        parser.name=provider  # Perplexity Decisions and Jev share this typed Choice contract.
        key=digest({'provider':provider,'body':body,'contract':2})
        old=self.ledger.db.execute('SELECT status,response FROM calls WHERE id=?',(key,)).fetchone()
        if old and old[0]=='validated':
            raw=json.loads(old[1]);return {**self._parse(parser,task,raw),'cache_hit':True,'new_paid_api_usd':0}
        if not self.allow_live: return {'status':'prepared','provider':provider,'request_hash':key,'request':body}
        endpoint,variable=self.ENDPOINTS[provider];secret=os.environ.get(variable)
        if not secret:raise ValueError('Missing '+variable)
        encoded=json.dumps(body,allow_nan=False).encode()
        if len(encoded)>20000:raise ValueError('Bounded request exceeds 20000 bytes; chunk explicitly')
        cached=self.ledger.reserve(key,provider,body['model'])
        if cached is not None:return self._parse(parser,task,cached)
        headers={'Content-Type':'application/json'}
        if provider=='claude':headers.update({'x-api-key':secret,'anthropic-version':'2023-06-01'})
        else:headers['Authorization']='Bearer '+secret
        start=time.perf_counter()
        try:
            with self.transport(urllib.request.Request(endpoint,data=encoded,headers=headers),timeout=45) as response:raw=json.loads(response.read())
            if raw.get('model')!=body['model']:raise ValueError('Unexpected actual model revision')
            parsed=self._parse(parser,task,raw)
            self.ledger.finish(key,response=raw,seconds=time.perf_counter()-start)
            from .economics import usage_cost
            return {**parsed,'request_hash':key,'usage':raw.get('usage'),'cost_estimate':usage_cost(body['model'],raw.get('usage'))}
        except Exception as e:
            error='HTTP '+str(e.code) if isinstance(e,urllib.error.HTTPError) else type(e).__name__
            self.ledger.finish(key,error=error,seconds=time.perf_counter()-start)
            raise
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
