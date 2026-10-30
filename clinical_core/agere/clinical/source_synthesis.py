"""Dispatch by explicit context contract; preserve category-complete replay."""
from pathlib import Path
from ...contracts import digest
from . import case_passes as legacy, record_context, record_synthesis, context_builder


_plan_retrievers = {}
def configure_plan_answers(retriever):
    """Use the active retriever to revalidate complete-plan views on every read."""
    _plan_retrievers[str(Path(retriever.path).resolve())] = retriever

def complete_plan_view(path,key,*,is_context=False):
    from . import plan_answer
    retriever = _plan_retrievers.get(str(Path(path).resolve()))
    if retriever is not None and plan_answer.exists(path,key,context=is_context):
        return plan_answer.inspect(retriever,key,context=is_context)
    return None

def code_identity(): return digest([legacy.code_identity(), record_synthesis.code_identity()])
def context(path, key):
    complete=complete_plan_view(path,key,is_context=True)
    if complete is not None:return complete
    return record_context.inspect(path,key) if record_context.exists(path,key) else context_builder.inspect(path,key)
def engine(path, key): return record_synthesis if record_context.exists(path,key) else legacy
def stages(path,key):
    from . import source_stage_synthesis as old, stable_reconciliation as old_reconciliation
    from . import availability_source_stage as current, availability_reconciliation as reconciliation
    # Resume previous paid work with its exact request bodies. Availability-aware
    # synthesis applies only to contexts without a historical stage or progress.
    *_,old_key=old.prepare(path,key)
    target=old.location(path,old_key)
    if target.exists() or target.with_name(old_key+'-progress.json').exists():
        return old,old_reconciliation
    try:engine(path,key).progress(path,key)
    except FileNotFoundError:pass
    except ValueError as exc:
        if 'No progress for the current context and implementation' not in str(exc):raise
    else:return old,old_reconciliation
    return current,reconciliation
def preview(path,key):
    stage,_=stages(path,key)
    return stage.preview(path,key)

def saved_source_stage(path,key):
    """Reopen completed source passes independently of later reconciliation.

    No inference or promotion occurs here. In particular, a failed or oversized
    reconciliation cannot hide paid source progress or become a reconciled answer.
    """
    row=context(path,key)
    from . import source_output_recovery, source_citation_recovery
    stage,_=stages(path,key)
    *_,answer_id=stage.prepare(path,key)
    recovered=source_output_recovery.saved_for_context(path,key) if stage.VERSION=='meaning-source-stage/1' else None
    if recovered is None and stage.VERSION=='meaning-source-stage/1':
        recovered=source_citation_recovery.saved_for_context(path,key)
    if recovered is None and not stage.exists(path,answer_id):
        return {'status':'source_passes_incomplete','context_id':key,'answer':None,'new_paid_api_calls':0}
    answer=recovered if recovered is not None else stage.inspect(path,answer_id)
    expected=[u['id'] for u in row['model_context']['evidence']]
    actual=[u['source_unit_id'] for p in answer.get('passes',[]) for u in p['result']['unit_reviews']]
    coverage=answer.get('coverage',{})
    if (answer.get('status')!='complete' or answer.get('context_id')!=key
            or answer.get('context_digest')!=digest(row) or answer.get('patient_id')!=row['patient_id']
            or answer.get('question')!=row['parameters']['question']
            or not expected or len(set(expected))!=len(expected) or sorted(actual)!=sorted(expected)
            or coverage.get('complete_declared_scope') is not True
            or any(coverage.get(k)!=len(expected) for k in ('received_units','submitted_units','model_accounted_units'))):
        raise ValueError('Saved source passes do not account for this exact context')
    return {'status':'completed_source_passes_only','context_id':key,'answer':answer,
        'unassessed_units':coverage.get('unassessed_units',[]),'new_paid_api_calls':0,
        'reconciliation_completed_by_reopen':False,'clinical_validated':False,
        'scope':'Reopens the immutable source-stage answer only. Later reconciliation failures and unresolved relationships remain unchanged.'}
class DispatchLedger:
    """Numeric preflight adapter for immutable saved-answer engine contracts.

    Infinity exists only inside their comparisons. The actual ledger/API receipt
    uses JSON null and an explicit uncapped policy. Charges and retry protection
    still go through the real ledger. No source or answer identity changes.
    """
    def __init__(self,ledger):self.ledger=ledger
    def __getattr__(self,name):return getattr(self.ledger,name)
    def receipt(self):
        receipt=self.ledger.receipt()
        if receipt['remaining_reservation_usd'] is None:
            return {**receipt,'remaining_reservation_usd':float('inf')}
        return receipt

def synthesize(path,key,ledger,*,allow_live=False):
    # Existing paid answers retain their exact replay and contracts. New contexts
    # run source passes followed directly by stable-reference reconciliation.
    try:return engine(path,key).synthesize(path,key,DispatchLedger(ledger),allow_live=False)
    except ValueError as exc:
        if 'Live synthesis route disabled' not in str(exc):
            # A complete, original-parser-validated correction supersedes the
            # failed *source attempt* for downstream use, never its ledger status.
            # Incomplete recovery and historical engines keep their retry guard.
            if str(exc)!='Prior source pass pending/failed; no silent paid retry':raise
            candidate,_=stages(path,key)
            if candidate.VERSION!='meaning-source-stage/1' or saved_source_stage(path,key)['answer'] is None:raise
    source_stage_synthesis,stable_reconciliation=stages(path,key)
    completed=saved_source_stage(path,key) if source_stage_synthesis.VERSION=='meaning-source-stage/1' else None
    parent=({**completed['answer'],'new_paid_api_calls':0,'new_token_count_calls':0,'cache_hit':True}
            if completed and completed['answer'] is not None else
            source_stage_synthesis.synthesize(path,key,ledger,allow_live=allow_live))
    if len(parent['passes'])==1:return parent
    try:result=stable_reconciliation.synthesize(path,parent['id'],ledger,allow_live=allow_live)
    except ValueError as exc:
        if not allow_live and 'Live stable reconciliation disabled' in str(exc):
            raise ValueError('Live synthesis route disabled; source passes saved and stable reconciliation remains pending') from exc
        raise
    return {**result,'new_paid_api_calls':parent['new_paid_api_calls']+result['new_paid_api_calls'],
        'new_token_count_calls':parent['new_token_count_calls']+result['new_token_count_calls'],
        'cache_hit':parent['cache_hit'] and result['cache_hit']}
def progress(path,key):
    source_stage_synthesis,stable_reconciliation=stages(path,key)
    try:stage=source_stage_synthesis.progress(path,key)
    except FileNotFoundError:return engine(path,key).progress(path,key)
    if stage['status']=='complete' and len(stage['passes'])>1:
        try:planned=stable_reconciliation.preview(path,stage['id'])
        except ValueError as exc:return {'source_stage':stage,'status':'source_passes_complete_reconciliation_pending','reconciliation_error':str(exc)}
        return {'source_stage':stage,'stable_reconciliation':planned,
            'status':'complete' if planned['saved_answer_available'] else 'source_passes_complete_reconciliation_pending'}
    return stage
def inspect(path,key):
    from . import source_output_recovery, source_citation_recovery
    if source_output_recovery.exists(path,key):return source_output_recovery.inspect(path,key)
    if source_citation_recovery.exists(path,key):return source_citation_recovery.inspect(path,key)
    complete=complete_plan_view(path,key)
    if complete is not None:return complete
    from . import reconciliation
    if reconciliation.exists(path,key): return reconciliation.inspect(path,key)
    from . import stable_reconciliation,source_stage_synthesis,availability_source_stage,availability_reconciliation
    if availability_reconciliation.exists(path,key): return availability_reconciliation.inspect(path,key)
    if availability_source_stage.exists(path,key): return availability_source_stage.inspect(path,key)
    if stable_reconciliation.exists(path,key): return stable_reconciliation.inspect(path,key)
    if source_stage_synthesis.exists(path,key): return source_stage_synthesis.inspect(path,key)
    return record_synthesis.inspect(path,key) if record_synthesis.exists(path,key) else legacy.inspect(path,key)
