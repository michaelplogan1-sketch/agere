#!/bin/zsh
# Build Agere on warehouse release v2.8 (adapter/4, semantic-layer/15) and run the verification chain with
# --restatement --expansion --retraction (v2.6+ drops deep-chart records dated after a death; see
# clinical_core/agere/warehouse_retraction.py for the declared post-death rule).
#
#   tmp/build_v28.sh                  # = full (default): all 9,948 patients, 3,807,947 records
#   tmp/build_v28.sh full
#   V28_BUILD=... V28_PREV_RELEASE=... tmp/build_v28.sh release DIR   # smoke run on a small sample release
#
# Previous release for the migration diffs: the v2.3 full release (the active build is v2.3 sample 0.35). The v2.3
# sample-0.35 build is read only (model cache and program packages are copied, never written).
# Sequential, <=4 threads, resumable: completed stages (run.json present) are skipped on a rerun. A crashed reader run
# needs its partial annotations/ moved aside before rerunning. Nothing is activated; the activation command is printed.
#
# Estimates: SCALED, NOT MEASURED ON A FULL v2.8 BUILD. v2.3 estimated ~22-25 KB disk and ~7 KB peak RAM per received
# record. The v2.8 smoke (2026-10-14; 175 patients, 158,239 records; supersedes links cleared, see below) used 4.4 GB
# (~28 KB/record) and ~5 min to the end of the semantic stage; peak RSS 2.3 GB. v2.8 full has 3,807,947 records (+7% over
# v2.3 full's 3,555,650). Scaled: ~95-110 GB on disk, ~27 GB peak RAM, ~3.5-4 h.
#
# KNOWN BLOCKERS (measured 2026-10-14, releases read only; details in the commit message):
#   1. Finish stage: the adapter now carries source_records.supersedes_record_id (73,354 links in v2.8). Warehouse.snapshot
#      hides superseded rows and quarantines parents with >1 child (void + replacement of one claim: 7,744 parents), and
#      2 death_records rows have event_date after as_of ('future'), so stack coverage != ingested counts and
#      build_healthcare_warehouse_stack.py --stage finish asserts. Needs a decision on supersedes semantics first.
#   2. general/native verifiers: verify_scoped_planner.exercise asserts exactly 2 planner calls; since the 128 KB request
#      bound (b03a2d5) a catalog whose direct request fits makes 1 call (smoke: 100,882 bytes) and the assert fails.
#   3. Migration vs the v2.3 full release: population record_ids are regenerated per release (~1.7M v2.3 population
#      record_ids now name another patient/stream), 335 v2.3 patients are absent, 84,381 population rows of living
#      patients are missing, and 176 deep-chart rows of 10 decedents are missing although dated on/before the death:
#      inventory/audit with --retraction report 90,869 unexplained rows and expansion rejects the missing patients.
set -e
setopt pipefail
ROOT=${AGERE_ROOT:-/Users/mikel/clinical-insights-local}
cd $ROOT
PY=${AGERE_PY:-$ROOT/.venv/bin/python}
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 VECLIB_MAXIMUM_THREADS=4 NUMEXPR_MAX_THREADS=4 TOKENIZERS_PARALLELISM=false
RELEASES=/Users/mikel/synthetic-patient-ecosystem/releases
REL=$RELEASES/healthcare-warehouse-v2.8          # read only; private/ and raw/ are never read
PREV_REL=${V28_PREV_RELEASE:-$RELEASES/healthcare-warehouse-v2.3}       # read only
PREV=${V28_PREV_BUILD:-artifacts/agere-healthcare-v23-sample-0.35}      # read only (model cache and program packages are copied, never written)
MODE=${1:-full}
case $MODE in
  full) NEED_GB=110; B=${V28_BUILD:-artifacts/agere-healthcare-v28} ;;
  release) REL=${2:?release dir}; NEED_GB=10; B=${V28_BUILD:?V28_BUILD required for release mode} ;;
  *) echo "usage: $0 [full | release DIR]"; exit 2 ;;
esac
FREE_GB=$(df -g $ROOT | awk 'NR==2 {print $4}')
if (( FREE_GB < NEED_GB )); then echo "only ${FREE_GB} GB free; this mode needs ~${NEED_GB} GB (build + temporary files)"; exit 3; fi
mkdir -p $B
LOG=$B/build-v28.log
step() { local name=$1; shift; echo "== $name start $(date '+%F %T')" | tee -a $LOG
         /usr/bin/time -l nice "$@" >> $LOG 2>&1
         echo "== $name done $(date '+%F %T') ($(du -sh $B | cut -f1))" | tee -a $LOG; }

# Build: ingest (adapter/4 + outside-PDF text layer) -> clinical -> retrieval/embeddings -> readers -> finish
#        -> semantic layer 15 (+ client-data tables, note facts, record store)
step sources   $PY build_healthcare_warehouse_stack.py --release $REL --output $B --stage sources
step retrieval $PY build_healthcare_warehouse_stack.py --release $REL --output $B --stage retrieval --reuse-model-cache $PREV/stack
step readers   $PY build_healthcare_warehouse_stack.py --release $REL --output $B --stage readers
step finish    $PY build_healthcare_warehouse_stack.py --release $REL --output $B --stage finish
step semantic  $PY -c "import sys; from clinical_core.agere.clinical import semantic_layer; print(semantic_layer.ensure(sys.argv[1]))" $B/stack
step client-checks $PY tools/v23_client_checks.py --build $B --out $B/client-checks.json

# Verification chain (receipts that warehouse_activation.activate reads)
[[ -d $B/inventory ]] || step inventory $PY inventory_received_warehouse.py --release $REL --previous $PREV_REL --output $B/inventory --restatement --expansion --retraction
step general     $PY verify_healthcare_warehouse_stack.py --build $B
step native      $PY verify_native_requirements.py --build $B --out $B/native-requirements
step diagnostics $PY verify_received_diagnostics.py --build $B
mkdir -p $B/previous-packages/stack && cp -n $PREV/stack/program-documents.sqlite3 $B/previous-packages/stack/ || true
step extension   $PY verify_received_source_extension.py --build $B --previous $B/previous-packages --inventory $B/inventory
step audit       $PY tools/restatement_audit.py --build $B --previous $PREV_REL --expansion --retraction
echo "== chain complete $(date '+%F %T'); disk $(du -sh $B | cut -f1)" | tee -a $LOG
grep -E '"(identical_roster|prior_patients|new_patients|prior_patients_missing|prior_rows_missing|prior_rows_changed|prior_rows_retracted|retracted_patients|prior_rows_unexplained|prior_rows_reassigned)"' $B/migration-audit.json | tee -a $LOG
echo "Not activated. To activate (service on 8885 stopped first):"
echo "  $PY activate_healthcare_warehouse.py --build $B --expansion --restatement --retraction"
