"""Fit per-entry vCache thresholds from the labelled semantic-cache cases and write vcache_thresholds.json."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from clinical_core.agere.clinical import vcache
from clinical_core.agere.clinical.calibrated_thresholds import get


def main():
    rows, ident = vcache.cases_from_file()
    g = get('semantic_cache.similarity', 0.9, bind=ident)
    doc = vcache.fit(rows, encoder_identity=ident, global_threshold=g['threshold'])
    vcache.write(doc)
    print({'observations': doc['observations'], 'entries': len(doc['entries']), 'global': doc['global']})


if __name__ == '__main__':
    main()
