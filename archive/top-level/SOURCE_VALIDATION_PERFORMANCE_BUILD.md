# Source validation performance — October 10, 2026

Agere now reuses unchanged native-source snapshots in its actual question, graph and source-inspection routes. The legacy evaluator and every saved evidence identity remain unchanged. A private read-only SQLite watcher checks commit version, file replacement/stat identity, exact small metadata and terminology contents, and evaluator identities before and after computation or reuse. Changed inputs recompute; repeated concurrent change fails closed. Returned objects are isolated and the cache has eight entries, with no TTL-based validity.

The implementation uses SQLite's documented same-connection `PRAGMA data_version` semantics: [official SQLite documentation](https://www.sqlite.org/pragma.html#pragma_data_version). The watcher never writes. Tests cover WAL commits with an unchanged main database file, update/insert/delete, rollback, replacement, restored metadata timestamps, terminology/code changes, changes during computation/hits and concurrent requests. Deep raw-source verification remains active. This is an explicit service-scoped decorator around the unchanged evaluator, not a new evidence algorithm.

The Agere-owned derived event artifact also has a covering index on `events(stream)`. The external warehouse and generator were untouched. All 1,273,307 event rows retain the same ordered content digest: `821895e37d978ae243d1e9e7a1cf74d88971339fa554ad31f68c7f956e393fcb`.

## Measured result

| Local route | Before | After |
|---|---:|---:|
| Saved P-1001 portfolio inspection | 6.388 s | 0.132–0.141 s warm |
| Same saved P-1001 question | 7.286 s | 0.330 s warm |
| Fresh P-1001 graph, warm source cache | prior milestone cold graph 60.216 s | 1.163 s |
| Fresh P-7193 graph, warm source cache | prior milestone cold graph 57.659 s | 1.188 s |

The cross-milestone cold/warm graph timings are different cache conditions, not an isolated controlled speedup claim. The first inspection after server restart was still 5.651 s. P-7193's first question on this run took 1.595 s. Source mutations or a server restart require scanning again; remaining semantic/longitudinal dependencies are not cached by this extension. These are local synthetic fixtures, not a production throughput benchmark.

## Verification and preservation

91 unique focused tests passed, including 17 new cache tests. Actual HTTP question/graph/source-inspection routes and the browser question-to-source path passed. The initial HTTP harness incorrectly compared per-node cache telemetry; it was corrected to exclude only `cache_hit` and `selection_cache_hit`, while comparing every evidence field. Both attempts are retained. Identical graph replays retain result IDs; fresh graph executions retain the exact substantive results and saved portfolio IDs.

76 protected implementation files, eight paid answers, six internal buyer tasks and 19 external research documents verify unchanged. Functional work retains its 256 items and 257 events with identical digests. The shared provider ledger is unchanged; this milestone used zero provider or token-count calls and no external actions. Spending remains uncapped.

Next: exact pass-qualified references in saved review questions, routed to bounded follow-up evidence while preserving the original question and saved answers. Broader clinical semantics, qualified prediction, benefit applicability, reviewed clinical knowledge, latent-state/causal/shared-value research and portable full-stack acceptance remain incomplete.
