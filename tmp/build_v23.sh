#!/bin/zsh
# Build Agere on warehouse release v2.3 (adapter/4, semantic-layer/15) and run the verification chain.
#
#   tmp/build_v23.sh                  # = sample 0.35 (default): every deep-chart patient + 35% of each population segment
#   tmp/build_v23.sh sample 0.35      # derived, pinned sample release under artifacts/ (outside PDFs of sampled patients copied)
#   tmp/build_v23.sh full             # all 9,855 patients, 3,555,650 records, 11,154 outside documents
#   V23_BUILD=... V23_PREV_RELEASE=... tmp/build_v23.sh release DIR   # smoke run on a small sample release
#
# Sequential, <=4 threads, resumable: completed stages (run.json present) are skipped on a rerun. A crashed reader run
# needs its partial annotations/ moved aside before rerunning. Nothing is activated; the activation command is printed.
# Estimates. Sample 0.35 = all 1,614 deep charts (1,766,711 records) + 35% of 8,241 population patients (~626K records)
# = ~2.39M records. v2.2 measured ~22-25 KB disk and ~7 KB peak RAM per received record (ingest/retrieval hold the snapshot);
# the v2.3 smoke (240 patients, 93,927 records, 2026-10-13) took 2.8 GB / ~7 min end to end, client-data tables ~2 s of a
# 36 s semantic build (note facts dominate), so v2.3 adds < 2% over v2.2 per record:
#   sample 0.35: ~55-60 GB on disk, ~17 GB peak RAM, ~2-2.5 h.   full (3,555,650 records): ~85-90 GB, ~25 GB, ~3-3.5 h.
set -e
setopt pipefail
ROOT=${AGERE_ROOT:-/Users/mikel/clinical-insights-local}
cd $ROOT
PY=${AGERE_PY:-$ROOT/.venv/bin/python}
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 VECLIB_MAXIMUM_THREADS=4 NUMEXPR_MAX_THREADS=4 TOKENIZERS_PARALLELISM=false
RELEASES=/Users/mikel/synthetic-patient-ecosystem/releases
REL=$RELEASES/healthcare-warehouse-v2.3          # read only; private/ is never read
PREV_REL=${V23_PREV_RELEASE:-$RELEASES/healthcare-warehouse-v2.0}   # read only
PREV=${V23_PREV_BUILD:-artifacts/agere-healthcare-v20}               # read only (model cache and program packages are copied, never written)
MODE=${1:-sample}
case $MODE in
  full) NEED_GB=100; B=${V23_BUILD:-artifacts/agere-healthcare-v23} ;;
  sample) FRAC=${2:-0.35}; NEED_GB=70; B=${V23_BUILD:-artifacts/agere-healthcare-v23-sample-$FRAC}
          SRC=artifacts/healthcare-warehouse-v2.3-sample-$FRAC
          [[ -f $SRC/manifest.json ]] || $PY tools/sample_warehouse_release.py --release $REL --output $SRC --fraction $FRAC
          REL=$SRC ;;
  release) REL=${2:?release dir}; NEED_GB=5; B=${V23_BUILD:?V23_BUILD required for release mode} ;;
  *) echo "usage: $0 [sample [FRACTION] | full | release DIR]"; exit 2 ;;
esac
FREE_GB=$(df -g $ROOT | awk 'NR==2 {print $4}')
if (( FREE_GB < NEED_GB )); then echo "only ${FREE_GB} GB free; this mode needs ~${NEED_GB} GB (build + temporary files)"; exit 3; fi
mkdir -p $B
LOG=$B/build-v23.log
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
