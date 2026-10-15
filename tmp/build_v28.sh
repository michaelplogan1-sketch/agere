#!/bin/zsh
# Build Agere on warehouse release v2.8 (adapter/4, semantic-layer/15) and run the verification chain for a REBASELINE
# activation: v2.8 regenerates the population (record_ids recycled, 335 v2.3 patients absent), so it is a fresh baseline
# load, not a continuation of v2.3. The v2.3 comparison (inventory --rebaseline, migration audit --expansion --retraction
# with the post-death rule of clinical_core/agere/warehouse_retraction.py) is still produced and recorded in the
# activation receipt as an informational diff; it does not gate.
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
# Decisions (2026-10-14) for the earlier blockers: supersession hides the parent and keeps every child current (no
# multi-child quarantine; the finish stage reconciles current + superseded + not-visible + quarantined = ingested); the
# planner verifiers accept 1 (direct, <= 128 KB) or 2 planner calls; migration = --rebaseline (informational diff).
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
  sample) FRAC=${2:-0.30}; NEED_GB=80; B=${V28_BUILD:-artifacts/agere-healthcare-v28-sample-$FRAC}
          SRC=artifacts/healthcare-warehouse-v2.8-sample-$FRAC          # all deep charts + FRAC of each population segment
          [[ -f $SRC/manifest.json ]] || $PY tools/sample_warehouse_release.py --release $REL --output $SRC --fraction $FRAC
          REL=$SRC ;;
  release) REL=${2:?release dir}; NEED_GB=10; B=${V28_BUILD:?V28_BUILD required for release mode} ;;
  *) echo "usage: $0 [full | sample [FRACTION] | release DIR]"; exit 2 ;;
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
# Release-diff guard vs the prior build (concept scope, evidence sources, code families, field bindings, lab name variants,
# governed metric value sets): writes $B/release-diff.json + .md. Informational; V28_RELEASE_DIFF_GATE=1 makes unacknowledged
# flags stop the chain (acknowledge reviewed ones in V28_RELEASE_DIFF_ACCEPT, JSON {"accepted": [ids or kinds]}). v2.8
# regenerates the population, so whole rosters are compared (--segments roster).
step release-diff $PY tools/release_diff_guard.py --candidate $B --prior $PREV --segments roster \
     ${V28_RELEASE_DIFF_GATE:+--gate} ${V28_RELEASE_DIFF_ACCEPT:+--accept $V28_RELEASE_DIFF_ACCEPT}
# Source coverage: every received stream mapped, read by an engine, or declared in config/agere/unmapped_streams.json.
# Informational while that file has enforce=false (V28_SOURCE_COVERAGE_GATE=1 forces it); writes $B/source-coverage.json.
step source-coverage $PY tools/source_coverage_gate.py --build $B ${V28_SOURCE_COVERAGE_GATE:+--gate}

# Verification chain (receipts that warehouse_activation.activate reads)
[[ -d $B/inventory ]] || step inventory $PY inventory_received_warehouse.py --release $REL --previous $PREV_REL --output $B/inventory --rebaseline
step general     $PY verify_healthcare_warehouse_stack.py --build $B
step native      $PY verify_native_requirements.py --build $B --out $B/native-requirements
step diagnostics $PY verify_received_diagnostics.py --build $B
mkdir -p $B/previous-packages/stack && cp -n $PREV/stack/program-documents.sqlite3 $B/previous-packages/stack/ || true
step extension   $PY verify_received_source_extension.py --build $B --previous $B/previous-packages --inventory $B/inventory
step audit       $PY tools/restatement_audit.py --build $B --previous $PREV_REL --expansion --retraction   # informational diff for --rebaseline
echo "== chain complete $(date '+%F %T'); disk $(du -sh $B | cut -f1)" | tee -a $LOG
grep -E '"(identical_roster|prior_patients|new_patients|prior_patients_missing|prior_rows_missing|prior_rows_changed|prior_rows_retracted|retracted_patients|prior_rows_unexplained|prior_rows_reassigned)"' $B/migration-audit.json | tee -a $LOG
echo "Not activated. To activate (service on 8885 stopped first):"
echo "  $PY activate_healthcare_warehouse.py --build $B --rebaseline --carry-forward-verified [--require-release-diff --accept-release-diff FILE] [--require-source-coverage]"
echo "  (--carry-forward-verified replays the active stack's verified pairs on $B; the v1.23 store can be replayed too:"
echo "   $PY tools/carry_forward_verified.py --from artifacts/agere-healthcare-v123 --to $B)"
