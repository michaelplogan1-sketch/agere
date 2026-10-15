"""Promote a qualified synthetic warehouse while preserving the prior connection."""
import hashlib,json,os,tempfile
from pathlib import Path
from ..contracts import digest
from . import warehouse_retraction as _retraction
from .clinical.context_student import REQUEST_MAX_BYTES  # shared provider request bound


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
        if type(receipt.get(key)) is not int or not 0<receipt[key]<=REQUEST_MAX_BYTES:raise ValueError('Planner request exceeds qualified bound')
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


REBASELINE_BASIS='Rebaselined release (fresh load; prior-release diff recorded, not gated)'
DIFF_KEYS=('prior_release','new_release','kind','identical_roster','patients','prior_patients','new_patients','prior_patients_missing',
           'prior_received_rows','new_received_rows','prior_rows_missing','prior_rows_changed','changed_by_table',
           'prior_rows_reassigned','reassigned_by_table','retraction_rule','prior_rows_retracted','retracted_by_table','retracted_patients',
           'prior_rows_unexplained','unexplained_by_table','unexplained_by_reason','unexplained_examples',
           'deceased_patients_in_new_release','death_date_basis','rows_for_new_patients')


def validate_rebaseline(migration,connection,prior,client_checks):
    """--rebaseline: a fresh baseline load (like a new client's warehouse). The prior-release migration audit must exist
    and belong to this build, but its diff is informational and never gates; the cutoff still may not roll back.
    Client-data checks must have passed on this build's cutoff. Returns the informational diff."""
    snapshot=connection['snapshot'];dataset=snapshot['source_dataset']
    if (migration.get('new_received_rows')!=connection['records'] or migration.get('patient_id_digest')!=dataset['original_patient_id_digest'] or
        migration.get('manifest_source_sha256')!=dataset['source_sha256']):raise ValueError('Rebaseline needs the prior-release audit of this build')
    if not all(type(migration.get(k)) is int for k in ('prior_received_rows','prior_rows_missing','prior_rows_changed')) or not migration.get('prior_release'):
        raise ValueError('Rebaseline needs the prior-release migration audit (recorded as an informational diff)')
    if prior and prior['snapshot']['as_of']>snapshot['as_of']:raise ValueError('Activation would roll back the cutoff')
    if (not isinstance(client_checks,dict) or client_checks.get('synthetic') is not True or client_checks.get('as_of')!=snapshot['as_of']):
        raise ValueError('Rebaseline needs passing client-data checks for this build')
    return {**{k:migration[k] for k in DIFF_KEYS if k in migration},'gated':False}


def validate_migration(migration,connection,prior,*,restatement=False,expansion=False,retraction=False):
    """Migration gate. Returns 'identical' | 'restated' | 'expanded' | 'expanded_restated' or raises.

    identical: same roster, no prior row changed or missing (default).
    restated (--restatement): same roster, nothing dropped, every changed prior row counted per table.
    expanded (--expansion): every prior patient present (prior roster digest = the active roster), prior rows not
    dropped, new patients counted; prior rows may change only with --restatement too, counted per table.
    retracted (--retraction, combinable with either): prior rows may be missing only when the audit explains every one
    by the declared retraction rule (warehouse_retraction.RULE, post-death); retracted rows are counted per table and
    the kind gains a '_retracted' suffix when any were retracted. Prior patients still may not disappear.
    Results keyed to the old source digest (learned answers, plan caches) stop matching automatically."""
    retracted=_retraction.validate(migration) if retraction else 0
    if not retraction and migration.get('retraction') and migration.get('prior_rows_missing'):
        raise ValueError('Release retracts prior rows; activation needs --retraction')
    suffix='_retracted' if retracted else ''
    snapshot=connection['snapshot'];dataset=snapshot['source_dataset']
    if (migration.get('new_received_rows')!=connection['records'] or migration.get('patient_id_digest')!=dataset['original_patient_id_digest'] or
        migration.get('manifest_source_sha256')!=dataset['source_sha256']):raise ValueError('Unverified original-cohort migration')
    if prior and prior['snapshot']['as_of']>snapshot['as_of']:raise ValueError('Activation would change the roster or roll back the cutoff')
    if expansion:
        if migration.get('kind')!='expansion':raise ValueError('Expansion activation needs an expansion migration audit')
        counts=[migration.get(k) for k in ('prior_patients','new_patients','patients','prior_patients_missing','prior_rows_missing','prior_rows_changed')]
        if not all(type(v) is int and v>=0 for v in counts):raise ValueError('Expansion audit must count prior, new and missing patients and rows')
        prior_n,new_n,total,missing_patients,missing_rows,changed=counts
        if missing_patients!=0:raise ValueError('Expansion would drop prior patients')
        if missing_rows!=retracted:raise ValueError('Expansion would drop prior rows')
        if total!=prior_n+new_n or total!=connection['patients']:raise ValueError('Expansion patient counts do not reconcile with the build')
        by_table=migration.get('changed_by_table')
        if not isinstance(by_table,dict) or sum(by_table.values())!=changed:raise ValueError('Changed prior rows must be counted per table')
        if changed and not restatement:raise ValueError('Expansion changes prior rows; activation needs --restatement as well')
        if prior and prior['snapshot']['source_dataset']['original_patient_id_digest']!=migration.get('prior_patient_id_digest'):
            raise ValueError('Expansion audit was not taken against the active roster')
        return ('expanded_restated' if changed else 'expanded')+suffix
    # A restated release (the source re-delivers history with corrections) is allowed only when asked for explicitly:
    # same roster, nothing dropped, every changed row counted per table in the audit.
    restated=(restatement and migration.get('kind')=='restatement' and migration.get('prior_rows_missing')==retracted
              and isinstance(migration.get('prior_rows_changed'),int) and isinstance(migration.get('changed_by_table'),dict))
    if restatement and not restated:raise ValueError('Restatement activation needs a restatement migration audit (no missing rows, changes counted per table)')
    if migration.get('identical_roster') is not True or (not restated and migration.get('prior_rows_missing_or_changed')!=retracted):
        raise ValueError('Unverified original-cohort migration')
    if prior and prior['snapshot']['source_dataset']['original_patient_id_digest']!=dataset['original_patient_id_digest']:
        raise ValueError('Activation would change the roster or roll back the cutoff')
    return ('restated' if restated else 'identical')+suffix


BASIS={'identical':'','restated':'Restated release: ','expanded':'Expanded release: ','expanded_restated':'Expanded and restated release: '}
BASIS.update({k+'_retracted':(v[:-2] if v else 'Release')+' with post-death retractions: ' for k,v in BASIS.items()})


def release_diff_gate(build,accepted=()):
    """--require-release-diff: the candidate's release-diff report (tools/release_diff_guard.py) must exist for this build's
    source digest and carry no unacknowledged flag. Returns the gate result."""
    from . import release_diff
    build=Path(build);report_path=build/'release-diff.json'
    if not report_path.exists():raise ValueError('Release-diff report missing: run tools/release_diff_guard.py --candidate BUILD first')
    report=json.loads(report_path.read_text());run=json.loads((build/'stack'/'run.json').read_text())
    if report.get('candidate',{}).get('digest')!=run.get('source_digest'):raise ValueError('Release-diff report belongs to another source digest')
    result=release_diff.gate(report,accepted)
    if not result['passed']:raise ValueError(f"Release-diff gate: {len(result['open_flags'])} unacknowledged flags (see release-diff.md)")
    return result


def carry_forward_verified(prior_build,build,out=None):
    """Activation hook: replay the prior stack's verified question->SQL pairs on the new build; passing pairs are carried
    into the new stack's learning store with provenance, the rest quarantined with reasons (verified_carry_forward.py).
    Returns the summary without per-pair outcomes (those go to <stack or out>/carry-forward.json)."""
    from .verified_carry_forward import carry_forward,_stack
    summary=carry_forward(prior_build,build,out)
    target=Path(out) if out else _stack(build)
    (target/'carry-forward.json').write_text(json.dumps(summary,indent=1,default=str)+'\n')
    return {k:v for k,v in summary.items() if k!='outcomes'}


def source_coverage_check(build,enforce=None):
    """Every received stream mapped, read by an engine or declared unused (source_coverage.py). Raises when enforced
    (config/agere/unmapped_streams.json enforce, or enforce=True) and a stream is in none; informational otherwise."""
    from . import source_coverage
    build=Path(build)
    if not (build/'stack'/'source-profile.json').exists():
        if enforce:raise ValueError('Source coverage gate needs the build source profile')
        return {'status':'unavailable','reason':'no stack/source-profile.json'}
    report=source_coverage.gate(build,enforce=enforce)
    return {k:report[k] for k in ('schema','enforce','passed','counts','unread','unread_records','invalid_declarations')}


def activate(build,config,restatement=False,expansion=False,retraction=False,rebaseline=False,carry_forward=False,
             require_release_diff=False,release_diff_accepted=(),source_coverage_enforce=None):
    if rebaseline and (restatement or expansion or retraction):
        raise ValueError('--rebaseline is a fresh load; it cannot be combined with --restatement, --expansion or --retraction')
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
    prior=read(config) if config.exists() else None
    if rebaseline:
        diff=validate_rebaseline(migration,connection,prior,read(build/'client-checks.json') if (build/'client-checks.json').exists() else None)
        kind='rebaselined'
    else:
        kind=validate_migration(migration,connection,prior,restatement=restatement,expansion=expansion,retraction=retraction)
    restated=kind.startswith('restated')
    diff_gate=release_diff_gate(build,release_diff_accepted) if require_release_diff else None
    coverage=source_coverage_check(build,source_coverage_enforce)
    if checksum(dataset['source'])!=dataset['source_sha256']:raise ValueError('Pinned source content changed')
    clinical_state.attached(build/'stack');db,_=longitudinal.connect(build/'stack');db.close()
    policy=(f"Rebaselined healthcare-warehouse roster: {connection['patients']} received patients loaded fresh; prior roster not carried forward" if rebaseline else
            'Original healthcare-warehouse roster; no expanded benchmark variants' if not kind.startswith('expanded') else
            f"Expanded healthcare-warehouse roster: all {migration['prior_patients']} prior patients retained, {migration['new_patients']} received patients added")
    value={'schema':'active-healthcare-warehouse/1','build':str(build),'source_release':connection['release'],
           'snapshot':snapshot,'synthetic':True,'population_policy':policy,
           'activation_basis':(REBASELINE_BASIS+'; full-population contracts, diagnostic review and client-data checks; prior connection retained' if rebaseline else
                               BASIS[kind]+'Qualified received-source migration, full-population contracts and diagnostic review; prior connection retained')}
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
             'restatement':{k:migration[k] for k in ('prior_rows_changed','changed_by_table','prior_release') if k in migration} if restated else None,
             'expansion':{k:migration[k] for k in ('prior_patients','new_patients','prior_patient_id_digest','prior_rows_changed','changed_by_table',
                                                   'prior_release') if k in migration} if kind.startswith('expanded') else None,
             'retraction':{k:migration[k] for k in ('retraction_rule','prior_rows_retracted','retracted_by_table','retracted_patients',
                                                    'deceased_patients_in_new_release','prior_release') if k in migration} if kind.endswith('_retracted') else None,
             'rebaseline':{'basis':REBASELINE_BASIS,'prior_release_diff':diff} if rebaseline else None}
    if diff_gate is not None:receipt['release_diff_gate']=diff_gate
    receipt['source_coverage']=coverage
    if carry_forward and prior:
        try:receipt['verified_carry_forward']=carry_forward_verified(prior['build'],build)
        except Exception as exc:  # noqa: BLE001 - the activation stands; the store starts empty and the receipt says why
            receipt['verified_carry_forward']={'error':f'{type(exc).__name__}: {exc}'[:300]}
    (build/'activation.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt
