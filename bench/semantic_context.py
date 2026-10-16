"""Documentation context for the frontier_semantic baseline.

The implementation is shared with the product's semantic-analyst rung and lives in
clinical_core/agere/clinical/semantic_docs.py (a text-only module: it parses the data model with `ast` and imports no
other product module). Re-exported here so the bench and the product send byte-identical documentation.
"""
from clinical_core.agere.clinical.semantic_docs import (  # noqa: F401 - re-exports
    ALLOWED_SPLITS, CLINICAL, CONFIG, EXAMPLES_PATH, HELDOUT_PREFIXES, REPO, _Literal, _compact, _concepts_section,
    _definitions_section, _examples_section, _measures_section, _metrics_section, _tables_section, build_context,
    concept_library, load_examples, schema_doc)
