"""Promote a qualified synthetic warehouse while preserving the prior connection."""
import hashlib,json,os,tempfile
from pathlib import Path
from ..contracts import digest


def code_identity():
    from .clinical import analysis_graph,clinical_inputs,planner_catalog,graph_planner,planner_scope
    return digest([analysis_graph.code_identity(),clinical_inputs.identity(),
        *[hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in (planner_catalog,graph_planner,planner_scope)]])


def validate_receipts(connection,general,native,diagnostic,*,current_code):
    if connection.get('status')!='connected' or connection.get('synthetic') is not True:raise ValueError('Completed synthetic connection required')
    for receipt in (general,native,diagnostic):
        if receipt.get('synthetic') is not True or receipt.get('qualification_code_identity')!=current_code:raise ValueError('Missing or stale verification code identity')
        if receipt.get('source_snapshot')!=connection['snapshot']:raise ValueError('Verification belongs to another source snapshot')
        if receipt.get('patients')!=connection['patients'] or receipt.get('new_paid_api_calls')!=0:raise ValueError('Verification population or local-call contract mismatch')
    if (general.get('original_patient_ids_match') is not True or general.get('received_records')!=connection['records'] or
        general.get('source_categories')!=connection['source_categories'] or general.get('searchable_records')!=connection['records']):
        raise ValueError('Incomplete received-source coverage')
    for receipt,key in ((general,'frontier_request_bytes_1500_char_question'),(native,'frontier_request_bytes_1500_ascii_question')):
        if type(receipt.get(key)) is not int or not 0<receipt[key]<=20000:raise ValueError('Planner request exceeds qualified bound')
    contracts={r['contract'] for r in native.get('results',[]) if r.get('public_source_patient_sets_match') is True}
    required={'native_affordability_report','native_medication_nonuse','native_outside_service','native_advance_document_review'}
    if not required<=contracts:raise ValueError('Missing full-population source-account qualification')
    if diagnostic.get('protocol_positive_measurement_population_matches_public_fields') is not True:raise ValueError('Diagnostic-to-protocol qualification missing')
    return True


def validate_extension(connection,receipt,*,current_code,catalog_hash,categories):
    if (receipt.get('passed') is not True or receipt.get('synthetic') is not True or
        receipt.get('source_snapshot')!=connection['snapshot'] or receipt.get('patients')!=connection['patients'] or
        receipt.get('qualification_code_identity')!=current_code or receipt.get('source_catalog_sha256')!=catalog_hash or
        set(receipt.get('new_categories',[]))!=set(categories) or receipt.get('new_paid_api_calls')!=0):
        raise ValueError('Missing or stale received-source extension qualification')
    expected=sum(connection['source_coverage'].get(k,0) for k in categories)
    if receipt.get('checks',{}).get('source_accounts')!=expected:
        raise ValueError('Received-source account coverage is incomplete')
    required={'prescriber_reassessment_request','renewal_clarification_deferral','transport_support_report','community_service_delivery_report'}
    if not required<={r['contract'] for r in receipt.get('contracts',[]) if r.get('public_source_sets_match') is True and r.get('patients')==connection['patients']}:
        raise ValueError('New-source question contracts have not been qualified')
    if receipt.get('prior_reviews_copied')!=0 or receipt.get('prior_patient_receipts_copied')!=0:
        raise ValueError('Prior source-specific authority cannot transfer with package definitions')
    return True


def activate(build,config,restatement=False):
    from .healthcare_warehouse import checksum
    from .clinical import clinical_state,longitudinal
    build=Path(build).resolve();config=Path(config).resolve()
    def read(path):return json.loads(Path(path).read_text())
    connection=read(build/'connection.json');general=read(build/'verification.json')
    native=read(build/'native-requirements/verification.json');diagnostic=read(build/'diagnostic-verification.json')
    validate_receipts(connection,general,native,diagnostic,current_code=code_identity())
    from .received_source_catalog import DEFINITIONS,identity as catalog_identity
    if set(DEFINITIONS)&set(connection.get('source_coverage',{})):
        validate_extension(connection,read(build/'source-extension-verification.json'),current_code=code_identity(),
                           catalog_hash=catalog_identity(),categories=DEFINITIONS)
    migration=read(build/'migration-audit.json');snapshot=connection['snapshot'];dataset=snapshot['source_dataset']
    # A restated release (the source re-delivers history with corrections) is allowed only when asked for explicitly:
    # same roster, nothing dropped, every changed row counted per table in the audit. Results keyed to the old
    # source digest (learned answers, plan caches) stop matching automatically.
    restated=(restatement and migration.get('kind')=='restatement' and migration.get('prior_rows_missing')==0
              and isinstance(migration.get('prior_rows_changed'),int) and isinstance(migration.get('changed_by_table'),dict))
    if restatement and not restated:raise ValueError('Restatement activation needs a restatement migration audit (no missing rows, changes counted per table)')
    if (migration.get('identical_roster') is not True or (not restated and migration.get('prior_rows_missing_or_changed')!=0) or
        migration.get('new_received_rows')!=connection['records'] or migration.get('patient_id_digest')!=dataset['original_patient_id_digest'] or
        migration.get('manifest_source_sha256')!=dataset['source_sha256']):raise ValueError('Unverified original-cohort migration')
    if checksum(dataset['source'])!=dataset['source_sha256']:raise ValueError('Pinned source content changed')
    clinical_state.attached(build/'stack');db,_=longitudinal.connect(build/'stack');db.close()
    prior=read(config) if config.exists() else None
    if prior and (prior['snapshot']['source_dataset']['original_patient_id_digest']!=dataset['original_patient_id_digest'] or
                  prior['snapshot']['as_of']>snapshot['as_of']):raise ValueError('Activation would change the roster or roll back the cutoff')
    value={'schema':'active-healthcare-warehouse/1','build':str(build),'source_release':connection['release'],
           'snapshot':snapshot,'synthetic':True,'population_policy':'Original healthcare-warehouse roster; no expanded benchmark variants',
           'activation_basis':('Restated release: ' if restated else '')+'Qualified received-source migration, full-population contracts and diagnostic review; prior connection retained'}
    config.parent.mkdir(parents=True,exist_ok=True)
    if prior:
        history=config.parent/'activation-history';history.mkdir(exist_ok=True)
        saved=history/(digest(prior)+'.json')
        if saved.exists():
            if read(saved)!=prior:raise ValueError('Prior connection history collision')
        else:saved.write_text(json.dumps(prior,indent=2)+'\n')
    with tempfile.NamedTemporaryFile('w',dir=config.parent,prefix='.activate-',delete=False) as f:
        json.dump(value,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno());temporary=f.name
    os.replace(temporary,config)
    receipt={'synthetic':True,'active_build':str(build),'source_snapshot':snapshot,'prior_build':prior['build'] if prior else None,
             'qualification_code_identity':code_identity(),'configuration':str(config),'clinical_validation_claimed':False,
             'restatement':{k:migration[k] for k in ('prior_rows_changed','changed_by_table','prior_release') if k in migration} if restated else None}
    (build/'activation.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt
