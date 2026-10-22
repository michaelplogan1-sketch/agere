"""Full observed-snapshot retrieval: offline embeddings, FTS5, RRF and reranking.

Relevance scores select evidence for inspection. They do not establish patient
state. Text windows preserve source offsets; structured renderings name their
fields. Neither simulator truth nor original/clean document text is eligible.
"""
from collections import Counter
from pathlib import Path
import hashlib, json, re, sqlite3, time
import numpy as np
from ...contracts import digest
from ...local_encoders import LocalEncoder, LocalReranker
from ..warehouse import Warehouse
from ..narrative_sources import text_fields, FORBIDDEN

VERSION = 'warehouse-hybrid-retrieval/1'
ROOT = Path(__file__).resolve().parents[3]
ALIASES = {
 'kidney': ['renal', 'ckd', 'egfr', 'creatinine', 'nephrology'],
 'heart failure': ['hfref', 'chf', 'ejection fraction', 'ef'],
 'financial': ['afford', 'bills', 'cost', 'assistance'],
 'beta blocker': ['metoprolol', 'carvedilol', 'bisoprolol', 'bb'],
 'sglt2': ['empagliflozin', 'dapagliflozin'],
}

def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False))

def representations(r):
    """Only explicitly mapped discrete data become searchable renderings."""
    if r.get('_warehouse_source',{}).get('adapter') == 'original-cohort-warehouse-adapter/1':
        # Every received source payload gets lexical coverage. Dense retrieval is
        # reserved for its narrative fields; identifiers/numeric ledgers do not
        # require expensive embeddings to remain searchable.
        values={k:r[k] for k in r['_warehouse_fields']}
        yield {'field':'structured_fields','text':r['source_table']+' '+json.dumps(values,ensure_ascii=False,sort_keys=True),
               'kind':'structured_rendering','fields':list(values),'dense':False}
        for part in text_fields(r):
            yield {**part,'kind':'received_text','dense':True}
        return
    fields = {
      'labs': ('test','code','value','unit','specimen','assay','status'),
      'medication_orders': ('drug','drug_class','dose','status','order_context'),
      'pharmacy_claims': ('drug','drug_class','dose','status','days_supply','transaction_type','dispense_id','fill_date'),
      'medication_history': ('drug','drug_class','dose','status','days_supply','medication_taking_observed'),
      'medication_administration': ('drug','drug_class','dose','status'),
      'problem_lists': ('problems','assertion_status'),
      'referrals': ('service','status','destination'),
      'financial': ('difficulty_paying_medical_bills','income_band_usd'),
      'adt': ('event','reason','organization_id'),
      'coverage': ('plan_id','coverage_type','effective_start','effective_end','coverage_scope'),
      'vitals': ('measurement','value','unit'),
    }.get(r['source_table'], ())
    values = {k:r[k] for k in fields if k in r}
    if values:
        yield {'field':'structured_fields', 'text':r['source_table']+' '+json.dumps(values, ensure_ascii=False, sort_keys=True),
               'kind':'structured_rendering', 'fields':list(values)}
    for part in text_fields(r):
        yield {**part, 'kind':'received_text'}

def build(warehouse, clinical_run, output, *, document_extractions=None, encoder_path=None, reuse_model_cache=None):
    output, clinical_run = Path(output), Path(clinical_run)
    if output.exists(): raise ValueError('Fresh stack output required')
    meta = json.loads((clinical_run/'run.json').read_text())
    if meta.get('synthetic') is not True: raise ValueError('Synthetic snapshot required')
    output.mkdir(parents=True); started = time.perf_counter()
    w = Warehouse(warehouse)
    try: patients, snapshot = w.snapshot(meta['snapshot']['release'], meta['snapshot']['as_of'])
    finally: w.close()
    if snapshot != meta['snapshot']: raise ValueError('Clinical snapshot no longer matches warehouse')
    if document_extractions:
        bindings = {x['record_id']:x for x in json.loads(Path(document_extractions).read_text())['records']}
        for pid,p in patients.items():
            for r in p['records']:
                if r['record_id'] in bindings:
                    b = bindings[r['record_id']]
                    if r['source_table']!='documents' or b['patient_id']!=pid: raise ValueError('OCR identity mismatch')
                    r.update(extracted_text=b['text'], extraction_method=b['method'])
    if reuse_model_cache:
        src=sqlite3.connect(Path(reuse_model_cache).resolve().as_uri()+'?mode=ro',uri=True)
        dst=sqlite3.connect(output/'model-cache.sqlite3')
        try:src.backup(dst)
        finally:src.close();dst.close()
    encoder = LocalEncoder(encoder_path or ROOT/'models/minilm-l6-v2', pooling='mean', cache=output/'model-cache.sqlite3')
    db = sqlite3.connect(output/'stack.sqlite3')
    db.executescript('''
      CREATE TABLE patients(id TEXT PRIMARY KEY,payload TEXT);
      CREATE TABLE records(id TEXT PRIMARY KEY,patient TEXT,stream TEXT,payload TEXT);
      CREATE INDEX patient_records ON records(patient,stream);
      CREATE TABLE contents(id INTEGER PRIMARY KEY,hash TEXT UNIQUE,text TEXT);
      CREATE TABLE passages(id INTEGER PRIMARY KEY,patient TEXT,content INTEGER,source TEXT);
      CREATE INDEX patient_passages ON passages(patient,content);
      CREATE INDEX content_passages ON passages(content,patient);
      CREATE VIRTUAL TABLE search USING fts5(text,tokenize='unicode61');
      CREATE TABLE result_cache(id TEXT PRIMARY KEY,payload TEXT);
      CREATE TABLE analyses(patient TEXT,operator TEXT,payload TEXT,PRIMARY KEY(patient,operator));
      CREATE TABLE proposals(id TEXT PRIMARY KEY,payload TEXT);
    ''')
    counts = Counter(); contents = {}; dense_contents=set(); source_digest = hashlib.sha256()
    try:
        with db:
            for pid,p in patients.items():
                db.execute('INSERT INTO patients VALUES (?,?)',(pid,json.dumps(p['patient'])))
                for original in p['records']:
                    r = {k:v for k,v in original.items() if k not in FORBIDDEN}
                    source_digest.update(digest(r).encode())
                    db.execute('INSERT INTO records VALUES (?,?,?,?)',(r['record_id'],pid,r['source_table'],json.dumps(r)))
                    for part in representations(r):
                        for chunk in encoder.windows(part['text'], size=160, overlap=24):
                            h = digest(chunk['text'])
                            if h not in contents:
                                contents[h] = len(contents)+1
                                db.execute('INSERT INTO contents VALUES (?,?,?)',(contents[h],h,chunk['text']))
                            if part.get('dense',True): dense_contents.add(contents[h]-1)
                            source = {'record_id':r['record_id'],'patient_id':pid,'source_table':r['source_table'],
                              'source_system':r.get('source_system'),'organization_id':r.get('organization_id'),
                              'field':part['field'],'kind':part['kind'],'event_date':r['event_date'],
                              'available_date':r['available_date'],'record_hash':digest(r),
                              'text_field_hash':digest(part['text']), 'start':chunk['start'],'end':chunk['end'],
                              'quote':chunk['text'],'structured_fields':part.get('fields'),
                              'clinical_time':'Not inferred from source event date'}
                            cur = db.execute('INSERT INTO passages(patient,content,source) VALUES (?,?,?)',(pid,contents[h],json.dumps(source)))
                            db.execute('INSERT INTO search(rowid,text) VALUES (?,?)',(cur.lastrowid,chunk['text']))
                            counts[part['kind']] += 1
                if len(patients)>100 and (sum(counts.values())//10000 != (sum(counts.values())-1)//10000):
                    print('Indexed passages:',sum(counts.values()),flush=True)
        # Keep the existing content-ID contract, but stream a memory-mapped array
        # rather than holding the full warehouse and every vector twice in RAM.
        vectors=np.lib.format.open_memmap(output/'vectors.npy',mode='w+',dtype=np.float32,shape=(len(contents),384))
        vectors[:]=0; eligible=sorted(dense_contents)
        for start in range(0,len(eligible),512):
            batch=eligible[start:start+512]
            texts=[db.execute('SELECT text FROM contents WHERE id=?',(i+1,)).fetchone()[0] for i in batch]
            vectors[batch]=encoder.encode(texts,batch_size=32)
            if start%5120==0: print('Embedded unique passages:',min(start+512,len(eligible)),'/',len(eligible),flush=True)
        vectors.flush();del vectors
        np.save(output/'dense-content-indices.npy',np.array(eligible,dtype=np.int64))
        report = {'schema':VERSION,'synthetic':True,'snapshot':snapshot,'source_run':meta['cache_key_digest'],
          'source_digest':source_digest.hexdigest(),'patients':len(patients),'visible_records':snapshot['visible_records'],
          'passages':sum(counts.values()),'unique_passage_texts':len(contents),'passages_by_kind':dict(counts),
          'dense_passage_texts':len(eligible),'dense_indices_sha256':hashlib.sha256((output/'dense-content-indices.npy').read_bytes()).hexdigest(),
          'retrieval_policy':'All transported source fields use lexical indexing; original-cohort narrative fields additionally use dense vectors. Structured-only rows are excluded from dense ranking.',
          'encoder_identity':encoder.identity,'encoder_path':str(encoder.path.resolve()),'encoder_stats':encoder.stats,
          'embedding_role':'Pinned MiniLM mean-pooled retrieval vectors; not a clinical interpretation model',
          'window_tokens':160,'overlap_tokens':24,'elapsed_seconds':time.perf_counter()-started,
          'vector_file_sha256':hashlib.sha256((output/'vectors.npy').read_bytes()).hexdigest(),
          'new_paid_api_calls':0,'complete_evidence_guarantee':False,
          'code_hashes':{'warehouse_retrieval.py':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}}
        write(output/'run.json',report); return report
    finally: db.close(); encoder.close()

COMMON_TERM_PASSAGES=100000  # lexical terms in more passages than this are dropped from the MATCH (term_frequency)

class Retriever:
    def __init__(self, path, *, rerank=True):
        self.path=Path(path); self.meta=json.loads((self.path/'run.json').read_text())
        if self.meta.get('schema')!=VERSION or self.meta.get('synthetic') is not True: raise ValueError('Completed synthetic retrieval index required')
        self.db=sqlite3.connect(self.path/'stack.sqlite3',check_same_thread=False)
        self.encoder=LocalEncoder(self.meta['encoder_path'],pooling='mean',cache=self.path/'model-cache.sqlite3')
        if self.encoder.identity!=self.meta['encoder_identity']:raise ValueError('Encoder identity changed')
        self.vectors=np.load(self.path/'vectors.npy',mmap_mode='r')
        self.dense_indices=None
        if self.meta.get('dense_indices_sha256'):
            p=self.path/'dense-content-indices.npy'
            if hashlib.sha256(p.read_bytes()).hexdigest()!=self.meta['dense_indices_sha256']:raise ValueError('Dense content mapping changed')
            self.dense_indices=np.load(p)
        if self.vectors.shape[0]!=self.meta['unique_passage_texts']:raise ValueError('Vector index mismatch')
        if not self.meta.get('vector_file_sha256') or hashlib.sha256((self.path/'vectors.npy').read_bytes()).hexdigest()!=self.meta['vector_file_sha256']:
            raise ValueError('Vector file content changed')
        self.reranker=LocalReranker(ROOT/'models/bge-reranker-v2-m3',cache=self.path/'model-cache.sqlite3') if rerank else None
        self._installed_reranker=self.reranker
        if (self.path/'clinical-events.json').exists():
            from .clinical_events import code_identity
            events=json.loads((self.path/'clinical-events.json').read_text())
            if events['source_digest']!=self.meta['source_digest'] or events['code_identity']!=code_identity():
                raise ValueError('Clinical-event bindings changed; rebuild event index')
        # The local HTTP service serializes model/DB operations with one lock.
        # Allow those serialized calls to arrive from distinct request threads.
        self.encoder.db.close()
        self.encoder.db=sqlite3.connect(self.path/'model-cache.sqlite3',check_same_thread=False)
        if self.reranker:
            self.reranker.db.close()
            self.reranker.db=sqlite3.connect(self.path/'model-cache.sqlite3',check_same_thread=False)
        self.annotation_db=None
        annotation_meta=self.path/'annotation-link.json'
        if annotation_meta.exists():
            link=json.loads(annotation_meta.read_text())
            source_meta=json.loads((Path(link['path'])/'run.json').read_text())
            if source_meta.get('source_run')!=self.meta['source_run'] or source_meta.get('model_identity')!=link['model_identity']:
                raise ValueError('Attached chart reader changed or belongs to another snapshot')
            self.annotation_db=sqlite3.connect((Path(link['path'])/'evidence.sqlite3').resolve().as_uri()+'?mode=ro',uri=True,check_same_thread=False)
            self.annotation_identity=link['model_identity']
    def close(self):
        if getattr(self,'_qwen',None):self._qwen.close()
        if getattr(self,'_graph',None):self._graph.close()
        self.db.close();self.encoder.close()
        if self.reranker:self.reranker.close()
        if self.annotation_db:self.annotation_db.close()
    def policy(self,question):
        """Adopted reranker / graph fallback for this question's type (reranker_bakeoff.choice) and the identities that key the cache."""
        from . import reranker_bakeoff as RB, protected_channels as PC, med_graph_rag as MG, evidence_completeness as EC
        c=RB.choice(question) if self.reranker else {'type':RB.query_type(question),'reranker':None,'graph_fallback':False,'basis':'reranking disabled'}
        if c['reranker']=='qwen' and not (RB.QWEN_PATH/'agere-model.json').exists():
            c={**c,'reranker':'bge','basis':c['basis']+'; Qwen3 reranker not installed: incumbent BGE'}
        graph=(self.path/MG.FILE).exists()
        ident={'bge':self.reranker.identity if self.reranker else None,'qwen':'qwen3-reranker-0.6b:'+RB.VERSION}.get(c['reranker'])
        code=digest([hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in (RB,PC,MG,EC)])
        return {**c,'reranker_identity':ident,'graph_identity':MG.VERSION if graph else None,'code':code}
    def _reranker(self,name):
        if not self.reranker or name is None:return None
        if self.reranker is not getattr(self,'_installed_reranker',self.reranker):return self.reranker  # an inspector's explicitly installed reranker
        if name=='qwen':
            if getattr(self,'_qwen',None) is None:
                from .reranker_bakeoff import QwenReranker,QWEN_PATH
                self._qwen=QwenReranker(QWEN_PATH,cache=self.path/'model-cache.sqlite3')
            return self._qwen
        return self.reranker
    def candidates(self,question,*,patient=None,patient_ids=None,analysis_window=None):
        """The fused candidate passages before reranking (reranker comparison and diagnostics)."""
        from .clinical_events import validate_window
        window=validate_window(analysis_window,self.meta['snapshot']['as_of'])
        membership=sorted(set(patient_ids)) if patient_ids is not None else None
        scoped=patient_ids is not None or window['basis']!='all_observed'
        return self._candidates(question,patient,membership,window,scoped)[0]
    def _exception_channel(self,question,patient,scope_join,have):
        """Protected exception channel: subject terms AND exception cues, same scope, outside the fused top 24."""
        from . import protected_channels as PC
        expr=PC.channel_query(question)
        if not expr:return []
        params=[expr];where=''
        if patient:where=' AND p.patient=?';params.append(patient)
        try:
            ids=[r[0] for r in self.db.execute('SELECT p.id FROM search JOIN passages p ON p.id=search.rowid'+scope_join+' WHERE search MATCH ?'+where+' ORDER BY bm25(search),p.id LIMIT 40',params)]
        except sqlite3.OperationalError:
            return []
        out=[]
        for pid in ids:
            if str(pid) in have:continue
            content,source=self.db.execute('SELECT c.text,p.source FROM passages p JOIN contents c ON c.id=p.content WHERE p.id=?',(pid,)).fetchone()
            if not PC.kinds(content):continue
            source=json.loads(source)
            out.append({'id':str(pid),'text':content,'source':source,'note_id':source['record_id'],'start':source['start'],'protected':False,
                        'rrf_score':0.0,'protected_channel':{'channel':'exception','kinds':PC.kinds(content),'terms':PC.subject_terms(question)}})
            if len(out)>=PC.CHANNEL_CANDIDATES:break
        return out
    def _graph_fallback(self,question,patient,membership,candidates):
        """Concept-graph candidates (med_graph_rag) fused with the hybrid candidates by reciprocal rank."""
        from . import med_graph_rag as MG
        if not (self.path/MG.FILE).exists():return None
        if getattr(self,'_graph',None) is None:
            self._graph=MG.GraphRetriever(self.path,stack_db=self.db)
        found=self._graph.search(question,patient=patient,patient_ids=membership,limit=24)
        if not found['candidates']:return None
        fusion=Counter();byid={}
        for ranking in (candidates,found['candidates']):
            for rank,c in enumerate(ranking,1):
                if c['id'] in byid and ranking is not candidates:
                    byid[c['id']]['graph_score']=c.get('graph_score')
                byid.setdefault(c['id'],c);fusion[c['id']]+=1/(60+rank)
        merged=[byid[i] for i,_ in fusion.most_common(max(24,len(candidates)))]
        return {'candidates':merged,'nodes':found['nodes']}
    def _candidates(self,question,patient,membership,window,scoped):
        """Scope -> lexical + dense -> reciprocal-rank fusion: the 24 fused candidates every reranker reorders."""
        members=None
        scope_join=''
        if scoped:
            # Temporary tables avoid SQL parameter-count limits and filter BEFORE ranking.
            self.db.execute('CREATE TEMP TABLE IF NOT EXISTS query_scope_patients(id TEXT PRIMARY KEY)')
            self.db.execute('CREATE TEMP TABLE IF NOT EXISTS query_scope_passages(id INTEGER PRIMARY KEY)')
            self.db.execute('DELETE FROM query_scope_patients');self.db.execute('DELETE FROM query_scope_passages')
            members=membership if membership is not None else [r[0] for r in self.db.execute('SELECT id FROM patients')]
            if patient:members=[p for p in members if p==patient]
            self.db.executemany('INSERT INTO query_scope_patients VALUES (?)',((p,) for p in members))
            sql='INSERT INTO query_scope_passages SELECT p.id FROM query_scope_patients sp CROSS JOIN passages p ON sp.id=p.patient'  # CROSS JOIN fixes the order: the planner scanned all passages for a one-patient scope
            params=[]
            if window['basis']!='all_observed':
                sql+=" JOIN records r ON r.id=json_extract(p.source,'$.record_id')"
                if window['basis']=='clinical':
                    if not (self.path/'clinical-events.json').exists():raise ValueError('Clinical-event bindings not built')
                    sql+=' JOIN clinical_events e ON e.record=r.id'
                    field='e.clinical_date';sql+=" WHERE e.status IN ('explicit_date','explicit_future_date')"
                else:
                    field="json_extract(r.payload,'$."+('event_date' if window['basis']=='source_event' else 'available_date')+"')"
                    sql+=' WHERE '+field+' IS NOT NULL'
                sql+=' AND '+field+'<=?';params.append(window['end'] or self.meta['snapshot']['as_of'])
                if window['start']:sql+=' AND '+field+'>=?';params.append(window['start'])
            self.db.execute(sql,params);self.db.commit()
            scope_join=' JOIN query_scope_passages qs ON qs.id=p.id'
        expanded=question.lower()
        for topic,aliases in ALIASES.items():
            if topic in expanded:expanded+=' '+' '.join(aliases)
        terms=[t for t in dict.fromkeys(re.findall(r'[a-z0-9]{2,}',expanded)) if self.term_frequency(t)<=COMMON_TERM_PASSAGES][:80]
        expression=' OR '.join('"'+x+'"' for x in terms)
        params=[expression];where=''
        if patient:where=' AND p.patient=?';params.append(patient)
        lexical=[r[0] for r in self.db.execute('SELECT p.id FROM search JOIN passages p ON p.id=search.rowid'+scope_join+' WHERE search MATCH ?'+where+' ORDER BY bm25(search),p.id LIMIT 80',params)] if expression else []
        query_windows=self.encoder.windows(question,size=160,overlap=24)
        q=self.encoder.encode([w['text'] for w in query_windows]).mean(axis=0)
        q=q/max(float(np.linalg.norm(q)),1e-12)
        eligible=None
        if scoped:
            eligible=np.array([r[0]-1 for r in self.db.execute('SELECT DISTINCT p.content FROM passages p'+scope_join)],dtype=int)
        elif patient:
            eligible=np.array([r[0]-1 for r in self.db.execute('SELECT DISTINCT content FROM passages WHERE patient=?',(patient,))],dtype=int)
        if self.dense_indices is not None:
            eligible=self.dense_indices if eligible is None else np.intersect1d(eligible,self.dense_indices)
        if eligible is not None:
            ds=self.vectors[eligible]@q; ordered=eligible[np.argsort(-ds,kind='stable')[:80]]
        else:
            ds=self.vectors@q; ordered=np.argsort(-ds,kind='stable')[:80]
        dense=[]
        for index in ordered:
            vals=[int(index)+1];cond=''
            if patient:cond=' AND patient=?';vals.append(patient)
            dense.extend(r[0] for r in self.db.execute('SELECT p.id FROM passages p'+scope_join+' WHERE content=?'+cond+' ORDER BY patient,p.id LIMIT 4',vals))
        fusion=Counter()
        for ranking in (lexical,dense):
            for rank,pid in enumerate(dict.fromkeys(ranking),1):fusion[pid]+=1/(60+rank)
        candidates=[]
        for pid,_ in fusion.most_common(24):
            content,source=self.db.execute('SELECT c.text,p.source FROM passages p JOIN contents c ON c.id=p.content WHERE p.id=?',(pid,)).fetchone()
            source=json.loads(source)
            candidates.append({'id':str(pid),'text':content,'source':source,'note_id':source['record_id'],
                               'start':source['start'],'protected':False,'rrf_score':fusion[pid]})
        return candidates,members,scope_join
    def term_frequency(self, term):
        """Passages containing term (fts5vocab, cached). A term in a large share of passages (a year, 'patient') adds nothing to
        ranking but makes the MATCH read millions of rows, so _candidates leaves it out."""
        cache=self.__dict__.setdefault('_term_df',{})
        if term not in cache:
            try:
                self.db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS temp.search_vocab USING fts5vocab(main,search,row)')
                row=self.db.execute('SELECT doc FROM temp.search_vocab WHERE term=?',(term,)).fetchone()
                cache[term]=row[0] if row else 0
            except sqlite3.Error:cache[term]=0  # no vocabulary (older index): keep the term
        return cache[term]
    def search(self, question, *, patient=None, limit=8,patient_ids=None,analysis_window=None,complete=True):
        from .clinical_events import validate_window
        window=validate_window(analysis_window,self.meta['snapshot']['as_of'])
        from .question_input import validate_question
        validate_question(question)
        if type(limit) is not int or not 1<=limit<=20:raise ValueError('Bounded result count required')
        if patient and not self.db.execute('SELECT 1 FROM patients WHERE id=?',(patient,)).fetchone():raise ValueError('Unknown patient')
        if patient_ids is not None and (not isinstance(patient_ids,list) or any(not isinstance(p,str) for p in patient_ids)):
            raise ValueError('Explicit cohort membership required')
        scoped=patient_ids is not None or window['basis']!='all_observed'
        membership=sorted(set(patient_ids)) if patient_ids is not None else None
        policy=self.policy(question)
        key=digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),self.meta['source_digest'],
                    self.encoder.identity,policy['reranker_identity'],policy['graph_identity'],policy['code'],
                    getattr(self,'annotation_identity',None),question,patient,limit,membership,window,bool(complete)])
        t=time.perf_counter();old=self.db.execute('SELECT payload FROM result_cache WHERE id=?',(key,)).fetchone()
        if old:return {**json.loads(old[0]),'cache_hit':True,'elapsed_seconds':time.perf_counter()-t,'new_paid_api_calls':0}
        candidates,members,scope_join=self._candidates(question,patient,membership,window,scoped)
        from . import protected_channels as PC
        channel=self._exception_channel(question,patient,scope_join,{c['id'] for c in candidates})
        candidates=candidates+channel
        graph_used=None
        if policy['graph_fallback'] or len(candidates)<limit:
            graph_used=self._graph_fallback(question,patient,membership,candidates)
            if graph_used: candidates=graph_used['candidates']
        PC.mark(question,candidates)
        reranker=self._reranker(policy['reranker'])
        ranked=reranker.rank(question,[{**c,'protected':False} for c in candidates],top_k=len(candidates)) if reranker and candidates else candidates
        def accept(row,seen,by_patient):
            s=row['source'];group=(s['patient_id'],s['record_id'],s['field'])
            if group in seen or (not patient and by_patient[s['patient_id']]>=2):return False
            seen.add(group);by_patient[s['patient_id']]+=1;return True
        selected=PC.select(ranked,limit,patient,accept)
        evidence_set=None
        if complete and patient:  # conclusion-guided: fetch the components of the conclusion the question asks for that the ranked set lacks
            from . import evidence_completeness as EC
            selected,evidence_set=EC.complete(lambda q:self.search(q,patient=patient,limit=12,analysis_window=analysis_window,complete=False)['evidence'],question,selected,limit)
        if self.annotation_db:
            by_patient={}
            for row in selected:
                s=row['source'];pid=s['patient_id']
                if pid not in by_patient:
                    by_patient[pid]=[json.loads(x[0]) for x in self.annotation_db.execute("SELECT payload FROM evidence WHERE kind='text' AND patient=?",(pid,))]
                labels=[]
                for a in by_patient[pid]:
                    origin=a['source']
                    if origin['record_id']!=s['record_id'] or origin['field']!=s['field']:continue
                    if not (s['start']<=origin['start'] and origin['end']<=s['end']):continue
                    start,end=origin['start']-s['start'],origin['end']-s['start']
                    if row['text'][start:end]!=origin['quote']:raise ValueError('Chart annotation source span changed')
                    labels.append({'target_quote':origin['quote'],'axes':a['axes'],'source':origin,
                                   'model_identity':a['model_identity'],'authority':a['authority'],
                                   'review_routing':a.get('review_routing')})
                row['chart_context_annotations']=labels
        result={'schema':VERSION,'synthetic':True,'question':question,'patient_id':patient,'evidence':selected,
                'candidate_passages':len(candidates),
                'method':'FTS5 BM25 + MiniLM cosine'+(' + concept graph' if graph_used else '')+' → reciprocal-rank fusion → '+
                         {'qwen':'Qwen3 reranker','bge':'BGE reranker'}.get(policy['reranker'] if reranker else '', 'no reranker')+
                         ' → protected exception/contradiction slots → source diversity',
                'evidence_set':evidence_set,
                'retrieval_policy':{'query_type':policy['type'],'reranker':policy['reranker'] if reranker else None,'basis':policy['basis'],
                                    'graph_fallback':bool(graph_used),'graph_nodes':(graph_used or {}).get('nodes'),
                                    'exception_channel_candidates':len(channel),
                                    'protected_selected':sum(1 for r in selected if r.get('protected')),
                                    'protected_reserve':PC.reserve(limit)},
                'snapshot':self.meta['snapshot'],'source_run':self.meta['source_run'],'cache_hit':False,
                'elapsed_seconds':time.perf_counter()-t,'new_paid_api_calls':0,'complete_evidence_guarantee':False,
                'query_windows':len(self.encoder.windows(question,size=160,overlap=24)),'query_embedding':'Normalized mean of complete token windows; no source truncation',
                'analysis_window':window,'cohort_patient_count':len(members) if scoped else None,
                'scope_applied_before_ranking':scoped,
                'scope':'Ranked passages for inspection, not a complete cohort or clinical answer. Scores are relevance, not correctness probabilities.'}
        with self.db:self.db.execute('INSERT OR REPLACE INTO result_cache VALUES (?,?)',(key,json.dumps(result,allow_nan=False)))
        return result
