# Run the synthetic contract demonstration

This is the November 10 code-only, same-machine engineering demo. It uses the existing approved Python environment, received warehouse, semantic materialization, models and mutable work/input/provider-accounting stores. It does not include a data backup or a standalone customer runtime. The [evaluation draft](WOLLSTONECRAFT_SYNTHETIC_CONTRACT_EVALUATION_DRAFT_2026-11-10.md) records the partial baseline results and qualification limits.

The exact archive is [code-only-v1.tar.gz](../../artifacts/agere-contract-release-2026-11-10/code-only-v1.tar.gz), SHA256 `ba8889dcc779902841e7041a756a1dcaf2d70cb1e314042843d2c455fcc32e04`. Its release ID is `dfa76415e97c7de0ba3e4265f3fb56af55b0b79ab1e1e1002ffab305d9b076f6`. The [packaging receipt](../../artifacts/agere-contract-release-2026-11-10/packaging-delivery-receipt.json) binds the tested code, bootstrap, configuration and external references.

The fresh code installation already exists. Check its code and declared dependencies without starting a provider:

```sh
/Users/mikel/clinical-insights-local/.venv/bin/python -I -B \
  /Users/mikel/clinical-insights-local/artifacts/agere-integration-recovery-20261104/workspace/artifacts/agere-contract-release-2026-11-10/code-only-rehearsal-v1/bootstrap.py \
  --storage-authorization /Users/mikel/clinical-insights-local/artifacts/agere-integration-recovery-20261104/workspace/artifacts/agere-contract-release-2026-11-10/code-only-storage-authorization.json \
  check
```

Serve saved analysis and internal work locally:

```sh
/Users/mikel/clinical-insights-local/.venv/bin/python -I -B \
  /Users/mikel/clinical-insights-local/artifacts/agere-integration-recovery-20261104/workspace/artifacts/agere-contract-release-2026-11-10/code-only-rehearsal-v1/bootstrap.py \
  --storage-authorization /Users/mikel/clinical-insights-local/artifacts/agere-integration-recovery-20261104/workspace/artifacts/agere-contract-release-2026-11-10/code-only-storage-authorization.json \
  serve --port 8906
```

Then open `http://127.0.0.1:8906/ask?context=ANS-72bdb20a4fbffdeeb17f13f2`. Both buyer views use one saved calculation. Expand monthly/practice/member sections or open the action workspace to see the single rehearsal review task. The UI's $0 new API usage belongs to the retained native replay, not the original four-attempt language-planning request; see the evaluation draft for all costs. No server is left running automatically after the rehearsal.

Explicitly adding `--allow-live-stack` to `serve` enables the existing approved subscription route for new questions. Its archived startup was separately checked through HTTP readiness with zero new provider attempts; no new question was submitted. That route requires the original approved credential environment and Claude subscription transport. No credential is bundled; never supply an external Anthropic API key. The new-question comparison was executed on the source checkout and is not repeated merely to start this archive.

The requested question is recorded verbatim in the evaluation protocol. Use explicit input `demo-vbc-2029-c3` for this demonstration; its terms and approximate quality definition are authored assumptions. A generic contract request pauses for input selection rather than choosing terms for the user. Broader healthcare questions and the permanent ten-area charter remain outside this one demo's acceptance claim.

The [storage authorization](../../artifacts/agere-contract-release-2026-11-10/code-only-storage-authorization.json) is limited to this exact archive/rehearsal location and code-only scope. It does not authorize copying a database, model, Python runtime, ledger or additional installation. Default package/large-run storage checks remain unchanged.

For recovery, keep this archive and its source/configuration identities intact. Reopening it uses the current shared mutable stores; it does not restore their historical state. Do not overwrite an existing installation or reset a ledger/work/input history. An isolated data restore and independent backup verification remain separate work under the standing preservation plan. A changed source/input/code binding must visibly invalidate dependent answers/work; do not restamp an old context to force it current.
