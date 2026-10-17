"""Native healthcare feed ingestion: vendor files -> landing tables with lineage -> canonical staging.

Parser contract (every format module):
    detect(path: Path) -> bool                       # does this file belong to this parser
    parse(path: Path, context: dict) -> Iterator[tuple[str, dict]]
        yields (landing_table, row). Table names are lowercase snake_case and stable across files of the same kind
        (e.g. x12_837_claim, x12_837_line, hl7_obx). Rows are flat dicts of scalars (str/int/float/None).
        context carries {'feed': top-level raw/ folder, 'instance': sending system/site/payer folder or None,
                         'drop': delivery period folder (e.g. 2029-06) or None, 'relpath': path relative to raw/}.
    The orchestrator adds lineage columns to every row: _feed, _instance, _drop, _file, _row (ordinal within file).
Parsers never read anything outside the file they are given, never write, and keep every source field (unknown
fields go into the row as-is or into a _raw/_extra JSON column) so nothing a customer sent is lost.
"""
