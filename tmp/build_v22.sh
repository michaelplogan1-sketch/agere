#!/bin/zsh
# Build Agere on warehouse release v2.2 (artifacts/agere-healthcare-v22) and run the verification chain.
#
#   tmp/build_v22.sh full            # all 10,539 patients, 3,387,699 records
#   tmp/build_v22.sh sample 0.35     # every deep-chart patient + 35% of each population segment (derived, pinned sample release)
#   V22_BUILD=... V22_PREV_RELEASE=... tmp/build_v22.sh release DIR   # smoke run on a small sample release
#
# Sequential, <=4 threads, resumable: completed stages (run.json present) are skipped on a rerun. A crashed reader run
# needs its partial annotations/ moved aside before rerunning. Nothing is activated; the activation command is printed.
# Measured on stratified samples (2026-10-13): ~22-25 KB on disk and ~7 KB peak RAM per received record in the ingest and
# retrieval stages (whole snapshot held in memory). Full build: ~75-80 GB on disk, ~24 GB peak RAM, ~2.5-3 h.
# Sample 0.35 (~2.3M records): ~50 GB, ~17 GB peak RAM, ~1.7-2 h.
set -e
setopt pipefail
ROOT=${AGERE_ROOT:-/Users/mikel/clinical-insights-local}
cd $ROOT
PY=$ROOT/.venv/bin/python
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 VECLIB_MAXIMUM_THREADS=4 NUMEXPR_MAX_THREADS=4 TOKENIZERS_PARALLELISM=false
RELEASES=/Users/mikel/synthetic-patient-ecosystem/releases
REL=$RELEASES/healthcare-warehouse-v2.2          # read only
PREV_REL=${V22_PREV_RELEASE:-$RELEASES/healthcare-warehouse-v2.0}   # read only
PREV=${V22_PREV_BUILD:-artifacts/agere-healthcare-v20}               # read only (model cache and program packages are copied, never written)
B=${V22_BUILD:-artifacts/agere-healthcare-v22}
MODE=${1:-}
case $MODE in
  full) NEED_GB=95 ;;
  sample) FRAC=${2:?fraction required, e.g. 0.35}; NEED_GB=65
          SRC=artifacts/healthcare-warehouse-v2.2-sample-$FRAC
          [[ -f $SRC/manifest.json ]] || $PY tools/sample_warehouse_release.py --release $REL --output $SRC --fraction $FRAC
          REL=$SRC ;;
  release) REL=${2:?release dir}; NEED_GB=5 ;;   # smoke runs on a sample release (tools/sample_warehouse_release.py)
  *) echo "usage: $0 full | sample FRACTION | release DIR"; exit 2 ;;
esac
FREE_GB=$(df -g $ROOT | awk 'NR==2 {print $4}')
if (( FREE_GB < NEED_GB )); then echo "only ${FREE_GB} GB free; this mode needs ~${NEED_GB} GB (build + temporary files)"; exit 3; fi
mkdir -p $B
LOG=$B/build-v22.log
step() { local name=$1; shift; echo "== $name start $(date '+%F %T')" | tee -a $LOG
         /usr/bin/time -l nice "$@" >> $LOG 2>&1
         echo "== $name done $(date '+%F %T') ($(du -sh $B | cut -f1))" | tee -a $LOG; }

# Build: ingest -> clinical -> retrieval/embeddings -> readers -> finish -> semantic + note facts + record store
step sources   $PY build_healthcare_warehouse_stack.py --release $REL --output $B --stage sources
step retrieval $PY build_healthcare_warehouse_stack.py --release $REL --output $B --stage retrieval --reuse-model-cache $PREV/stack
step readers   $PY build_healthcare_warehouse_stack.py --release $REL --output $B --stage readers
step finish    $PY build_healthcare_warehouse_stack.py --release $REL --output $B --stage finish
step semantic  $PY -c "import sys; from clinical_core.agere.clinical import semantic_layer; print(semantic_layer.ensure(sys.argv[1]))" $B/stack

# Verification chain (receipts that warehouse_activation.activate reads)
[[ -d $B/inventory ]] || step inventory $PY inventory_received_warehouse.py --release $REL --previous $PREV_REL --output $B/inventory --restatement --expansion
step general     $PY verify_healthcare_warehouse_stack.py --build $B
step native      $PY verify_native_requirements.py --build $B --out $B/native-requirements
step diagnostics $PY verify_received_diagnostics.py --build $B
mkdir -p $B/previous-packages/stack && cp -n $PREV/stack/program-documents.sqlite3 $B/previous-packages/stack/ || true
step extension   $PY verify_received_source_extension.py --build $B --previous $B/previous-packages --inventory $B/inventory
step audit       $PY tools/restatement_audit.py --build $B --previous $PREV_REL --expansion
echo "== chain complete $(date '+%F %T'); disk $(du -sh $B | cut -f1)" | tee -a $LOG
grep -E '"(identical_roster|prior_patients|new_patients|prior_patients_missing|prior_rows_missing|prior_rows_changed)"' $B/migration-audit.json | tee -a $LOG
echo "Not activated. To activate (service on 8885 stopped first):"
echo "  $PY activate_healthcare_warehouse.py --build $B --expansion --restatement"
