"""Agere streaming layer: incremental processing as each delivered file lands, and dependent replay of answers.

Two ideas, borrowed and adapted:
- High-frequency trading books: positions update tick by tick and only what a tick touches is recomputed. Here a tick is
  one delivery batch (one or more files); what it touches is a set of patients, streams and semantic tables.
- The Dataflow model (event time vs processing time, watermarks, late data, retractions): every record carries three
  clocks (event_date, recorded_date, available_date). A tick's records whose event time is behind the watermark are
  late; a record that replaces another (claim reversal, retro-termination, corrected lab) retracts an earlier fact, and
  every answer built on the retracted fact is marked stale and, when it was rendered by code, re-run.

Modules
    stage.py        incremental canonical staging: identity re-resolution with diff, adapters re-run only when their
                    landing inputs (or identity) changed, per-patient record diff, stack/warehouse updated in place.
    semantic.py     incremental semantic layer: touched patients rebuilt in a mini build with the same code and
                    spliced in (delete-and-reinsert per patient); as_of-dependent and global tables refreshed cheaply.
    ticks.py        the change log (<ws>/streaming/streaming.sqlite3): ticks, patients, streams, tables and flags (late
                    file, late record, claim reversal, retro-termination, corrected lab, restatement, identity merge).
    replay.py       answer dependency registry, stale marking, verified-cache invalidation, deterministic refresh and
                    the hooks the action backbone calls: affected_patients(tick) and stale_plans(tick).
    equivalence.py  table-by-table comparison of two builds (delta vs full rebuild must be identical).
    delivery_drop.py helpers to build a delivery drop folder (symlinks) up to a cutoff and to stream the rest in order.
    pipeline.py     Workspace.ingest_delta / Workspace.refresh implementations.

Switch: AGERE_STREAMING (default on) enables the new methods. The batch path (Workspace.run / canonical.stage /
semantic_layer.build) is unchanged; streaming state is bootstrapped from a batch-built workspace on first use.
"""
from __future__ import annotations

import os


def enabled():
    return os.environ.get('AGERE_STREAMING', 'on').strip().lower() not in ('off', '0', 'false', 'no')


class StreamingDisabled(RuntimeError):
    pass


def require():
    if not enabled():
        raise StreamingDisabled('AGERE_STREAMING is off')
