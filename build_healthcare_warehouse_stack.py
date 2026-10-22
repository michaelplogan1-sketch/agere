"""Build Agere against a pinned, original-cohort healthcare warehouse release."""
import argparse
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path
from clinical_core.agere.warehouse import Warehouse
from clinical_core.agere import healthcare_warehouse, healthcare_warehouse_v2, healthcare_warehouse_v3, healthcare_warehouse_v4


def ingest(warehouse, release):
    """v1 manifests keep adapter/1 (reproducible release identity); standards-coded v2 manifests use adapter/2;
    v2 manifests with broader-population feeds (v2.2) use adapter/3; client-realism releases (v2.3+, outside PDFs) use adapter/4."""
    manifest=json.loads((Path(release)/'manifest.json').read_text())
    module={healthcare_warehouse.VERSION:healthcare_warehouse,healthcare_warehouse_v2.VERSION:healthcare_warehouse_v2,
            healthcare_warehouse_v3.VERSION:healthcare_warehouse_v3,
            healthcare_warehouse_v4.VERSION:healthcare_warehouse_v4}[healthcare_warehouse_v4.adapter_for(manifest)]
    return module.ingest(warehouse,release)


def write(path,value): path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def main():
    p=argparse.ArgumentParser();p.add_argument('--release',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--stage',choices=['sources','retrieval','readers','finish','all'],default='all')
    p.add_argument('--reuse-model-cache',type=Path)
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    if a.reuse_model_cache:
        if a.reuse_model_cache.is_dir():a.reuse_model_cache=a.reuse_model_cache/'model-cache.sqlite3'
        if not a.reuse_model_cache.is_file():p.error('Model cache must be an existing SQLite file or stack directory containing model-cache.sqlite3')
    root=Path(__file__).resolve().parent;started=time.perf_counter()
    def progress(event): print(json.dumps(event),flush=True)
    if a.stage in ('sources','all'):
        w=Warehouse(out/'warehouse.sqlite3')
        try:release,receipt=ingest(w,a.release)
        finally:w.close()
        if not receipt['cache_hit']:write(out/'ingestion.json',{'release':release,**receipt})
        from clinical_core.agere.clinical.run import run
        if not (out/'clinical'/'run.json').exists():
            progress({'stage':'clinical_source_interpretation'})
            r=run(warehouse_path=out/'warehouse.sqlite3',release=release,
                  as_of=json.loads((a.release/'manifest.json').read_text())['as_of'],output=out/'clinical')
            progress({'stage':'clinical_completed','patients':r['patients'],'snapshot':r['snapshot']})
    if a.stage in ('retrieval','all'):
        from clinical_core.agere.clinical.warehouse_retrieval import build
        if not (out/'stack'/'run.json').exists():
            progress({'stage':'full_source_retrieval'})
            r=build(out/'warehouse.sqlite3',out/'clinical',out/'stack',reuse_model_cache=a.reuse_model_cache)
            progress({'stage':'retrieval_completed','patients':r['patients'],'records':r['visible_records'],
                      'passages':r['passages'],'dense_texts':r['dense_passage_texts']})
        from clinical_core.agere.clinical import med_graph_rag
        progress({'stage':'concept_graph'});g=med_graph_rag.build(out/'stack')  # fallback retriever the measured reranker/graph policy reads
        progress({'stage':'concept_graph_completed','nodes':g['nodes'],'edges':g['edges'],'links':g['links']})
    if a.stage in ('readers','all'):
        from clinical_core.agere.clinical.reader_warehouse import annotate
        from clinical_core.agere.clinical.evidence_queries import build_index
        if not (out/'annotations'/'run.json').exists():
            progress({'stage':'source_bound_narrative_reader'})
            r=annotate(root,root/'artifacts/agere-evidence-reader-v4',out/'clinical',out/'annotations',
                       warehouse_path=out/'warehouse.sqlite3',progress=progress)
            progress({'stage':'reader_completed','counts':r['counts']})
        if not (out/'evidence'/'run.json').exists():build_index(out/'clinical',out/'annotations',out/'evidence')
        e=json.loads((out/'evidence'/'run.json').read_text())
        write(out/'stack'/'annotation-link.json',{'path':str(out/'evidence'),'model_identity':e['model_identity'],
            'source_run':e['source_run'],'authority':'Freshly executed unvalidated machine annotations on this warehouse'})
    if a.stage in ('finish','all'):
        from clinical_core.agere.clinical.warehouse_concurrency import configure
        configure(out/'stack')
        from clinical_core.agere.clinical import analytic_registry, clinical_events, clinical_state, longitudinal, program_registry
        from build_cohort_stack import values
        from clinical_core.agere.clinical import derived_value_index
        stack=out/'stack'
        for name,fn in [('events',lambda:clinical_events.build(stack)),
                        ('operators',lambda:analytic_registry.compute_all(stack)),
                        ('reviews',lambda:analytic_registry.attach_clinical_reviews(stack,out/'clinical')),
                        ('values',lambda:values(stack)),('programs',lambda:program_registry.install_defaults(stack)),
                        ('derived_values',lambda:derived_value_index.build(stack))]:
            progress({'stage':name});fn()
        if not (out/'longitudinal'/'run.json').exists():longitudinal.build(stack,out/'longitudinal')
        state=out/'clinical-state'
        ident=clinical_state.identity(stack)
        if (state/'run.json').exists() and json.loads((state/'run.json').read_text())['identity']!=ident:
            from clinical_core.contracts import digest
            state=out/('clinical-state-'+digest(ident)[:12])
        if not (state/'run.json').exists():clinical_state.build(stack,state)
        write(stack/'clinical-state-link.json',{'path':str(state)})
        clinical_state.attached(stack)
        # Portable terminology can be reused; patient results/forecasts cannot.
        old=root/'artifacts/agere-population-stack-v3'
        link=json.loads((old/'medication-terminology-link.json').read_text())
        write(stack/'medication-terminology-link.json',link)
        from clinical_core.agere.clinical.medication_identity import attached
        attached(stack)
        # Frozen shadow students include the old narrative-source contract in
        # their identity. Requalify them separately; never transplant a link
        # or rewrite a frozen manifest to bypass that check.
        source=json.loads((out/'ingestion.json').read_text());meta=json.loads((stack/'run.json').read_text())
        db=sqlite3.connect(stack/'stack.sqlite3')
        try:
            roster=[r[0] for r in db.execute('SELECT id FROM patients ORDER BY id')]
            coverage={t:n for t,n in db.execute('SELECT stream,count(*) FROM records GROUP BY stream')}
            searchable=db.execute("SELECT count(DISTINCT json_extract(source,'$.record_id')) FROM passages").fetchone()[0]
        finally:db.close()
        assert len(roster)==source['counts']['patients']
        # Every ingested row is accounted for: current (in the stack) + superseded (history, hidden from the current
        # view) + not visible at the cutoff + quarantined (ambiguous correction links), per stream.
        snap=meta['snapshot'];accounted=Counter(coverage);not_current={}
        for key in ('superseded_by_stream','not_visible_by_stream','correction_conflict_by_stream'):
            accounted.update(snap.get(key,{}))
            if snap.get(key):not_current[key[:-len('_by_stream')]]=sum(snap[key].values())
        assert {k:v for k,v in accounted.items() if v}=={k:v for k,v in source['counts'].items() if k!='patients'},'ingested rows not reconciled'
        assert searchable==sum(coverage.values())
        result={'synthetic':True,'release':str(a.release.resolve()),'patients':len(roster),
                'records':sum(coverage.values()),'source_categories':len(coverage),'searchable_records':searchable,
                'snapshot':meta['snapshot'],'new_paid_api_calls':0,'new_model_weights':False,
                'old_patient_results_reused':False,'old_forecasts_attached':False,
                'semantic_students':{'attached':False,'reason':'Narrative source contract changed; separate requalification required.'},
                'source_coverage':coverage,**({'received_rows_not_current':not_current} if not_current else {}),
                'elapsed_seconds_this_invocation':time.perf_counter()-started,
                'status':'connected','limits':['Full transport and lexical coverage is not complete clinical interpretation.',
                    'Existing bounded reader vocabulary and clinical mappings need evaluation on this new source release.',
                    'Old source-specific forecasts and learning queues are not valid on the new population.']}
        write(out/'connection.json',result);progress(result)


if __name__=='__main__':main()
