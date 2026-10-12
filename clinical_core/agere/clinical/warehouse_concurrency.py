"""Permit source readers and short cache commits on the derived local index."""
import sqlite3
from pathlib import Path


def configure(path):
    index=Path(path)/'stack.sqlite3'
    if not index.is_file():raise ValueError('Existing derived stack index required')
    db=sqlite3.connect(index,timeout=30)
    try:
        current=db.execute('PRAGMA journal_mode').fetchone()[0]
        # Persistent WAL avoids making source scans block cache commits. The
        # received input warehouse is separate and is never opened here.
        mode=current if current=='wal' else db.execute('PRAGMA journal_mode=WAL').fetchone()[0]
        if mode!='wal':raise ValueError('Concurrent local index mode unavailable')
        # Source-scoped questions must not rescan the entire received index.
        # The older (patient,stream) index serves chart reads; this ordering
        # serves cross-population stream filters and counts without models.
        fields={r[1] for r in db.execute('PRAGMA table_info(records)')}
        source_index={'stream','patient'}<=fields
        if source_index:
            db.execute('CREATE INDEX IF NOT EXISTS received_stream_patients ON records(stream,patient)')
            db.commit()
        return {'derived_index':str(index.resolve()),'journal_mode':mode,'source_warehouse_modified':False,
                'population_source_index':source_index}
    finally:db.close()
