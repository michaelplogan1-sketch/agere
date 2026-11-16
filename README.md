# Agere

Agere answers analytics questions about a healthcare population (readmissions, care gaps, cost per member, attributed
panels, value-based contracts) from the customer's own warehouse. Every question goes to the cheapest method that answers
it correctly, and a frontier model is only used when nothing cheaper can:

1. **Saved recipes** (`bottles.py`): a question answered and verified before runs again as SQL, with no model call.
2. **Built-in recipes** (`recipes.py`): reviewed SQL for common measures, with typed parameters.
3. **Analysis methods** (`methods.py`): code-only analysis engines for the questions SQL alone does not answer.
4. **Small models** (`models.py`, `near.py`): a typed-judgment classifier and a small model pick and fill a recipe.
5. **Frontier model** (`models.py`, `takeover.py`): writes new SQL, which is checked before it runs and saved once verified.

Each answer either states the definitions it used and the record behind it, or declines. A question with two plain readings ("how many patients were admitted": different patients, or admissions) is answered both ways (`readings.py`); when the answers differ the user is shown both and picks, instead of Agere guessing. Saved recipes keep a track record
and stop answering when the evidence turns against them (`upkeep.py`). All patient data here is synthetic.

## Where the product is

| Path | What it is |
|---|---|
| `clinical_core/agere/tiered/` | **The product core**: router, the method ladder, recipes, governed definitions, learning, upkeep, evaluation and the proof scorecard. Start with `router.py` and `recipes.py`. |
| `clinical_core/agere/hosted/` | The web server for customers: sign-in, tenancy, encrypted credentials, the question page. |
| `clinical_core/agere/service.py`, `clinical/conversation.py` | The local app shell, which sends each question to the core first. |
| `clinical_core/agere/clinical/` and the rest of `clinical_core/` | Analysis engines, the typed-judgment classifier transport and the model gateway that the core calls. Older code lives here too; the core still reaches most of it, so it stays until each dependency is cut. |
| `eval/tiered/` | Question sets with reference answers. `proof_v*.json` are frozen held-out sets, committed before their first run (proof_v1 aside: audited and run before its freeze commit) and never edited after. A set's items cited in product code have been fixed against, so that set no longer counts as held out. |
| `tests/core/` | Tests of the core. `tests/legacy/` tests the older modules the core still uses. |
| `archive/` | Earlier experiments, one-off scripts and reports, moved out of the way on 2026-11-16. See `archive/README.md`. |

Large data (warehouse, saved recipes, ledgers, local models) is in `artifacts/` and `models/`, outside git.

## First run

1. Python 3.12. Install the core, then the extras you need (`local-models` for the small-model rungs on Apple silicon,
   `analysis`, `reporting`, `connectors` for customer warehouses, `hosted` for the customer web server):

   ```bash
   python -m venv .venv && .venv/bin/pip install -e '.[local-models,analysis]'
   ```

2. Data is not in git. The synthetic semantic layer (about 8.8 GB) is read from `AGERE_TIERED_DB`, by default
   `artifacts/agere-full-stack-rerun-2026-10-30/focused/runtime/stack/semantic.sqlite3`; saved recipes from
   `artifacts/agere-tiered-core/bottles.sqlite3`. Without the layer the app cannot answer, and the tests that read it skip
   with that reason (every other test runs).
3. Provider keys are never in the repository. `clinical_core/agere/providers.py` reads them from `AGERE_KEYS_FILE`
   (default `~/Desktop/typesafe_key.txt`) and, optionally, `AGERE_ENV_FILE`. The paid Anthropic API key is kept only on the
   hosted server with `AGERE_CLAUDE_TRANSPORT=api`; everywhere else Claude is reached through the subscription transport.

Settings that change behaviour (all `AGERE_TIERED_*` unless noted): `DB`, `MAPPING` and `FOREIGN` (which warehouse and how it
is presented), `BOTTLES`, `LEDGER`, `CORRECTIONS` (where saved recipes, the answer ledger and wrong-answer reports live),
`GOVERNED`, `CATALOGUE`, `READINGS` (the definition checks, definitions and two-reading check: on by default, and only a
measurement turns them off), `LLM` and `SLM` (models), `AUTO_LEARN` (saving new recipes in the background), and
`AGERE_HOSTED`, `AGERE_CLAUDE_TRANSPORT`, `AGERE_TRUST_PROXY`, `AGERE_HOST` for the hosted server.

## Run it

```bash
python serve_current_warehouse.py --port 8908 --allow-live-stack \
  --config artifacts/agere-full-stack-rerun-2026-10-30/focused/evidence-work-network/active-config.json \
  --ledger artifacts/agere-full-stack-rerun-2026-10-30/integrated-network/provider-budget.sqlite3
```

The local app at http://127.0.0.1:8908. `serve_hosted.py` runs the customer web server (see `deploy/`).

## Test it

```bash
python run_tests.py
```

Runs the core's tests (`--legacy` adds the older modules' tests). No test can call the paid API.

## Measure it

```bash
python -m clinical_core.agere.tiered.evaluate chains
```

```bash
AGERE_PROOF_SET=eval/tiered/proof_v4.json python -m clinical_core.agere.tiered.proof run
```

`evaluate` scores the development sets (`chains`, `cohort`, `multi`, `followups`, and a question stage with its
rungs, for example `canonical bottles,recipes,system1,slm,near,llm`). `proof run` runs a frozen held-out set: +1 for a
right answer, 0 for declining, -10 for a wrong one, with a frontier model writing its own SQL scored beside it on the
same questions.

## Run it on another warehouse

A customer's warehouse is presented to the core through a mapping: one SQL view per semantic-layer table, over the
customer's own tables (`config/agere/mappings/` holds an example for the synthetic layout B). With
`AGERE_TIERED_DB=<warehouse> AGERE_TIERED_MAPPING=<mapping>` every recipe, saved recipe and governed definition runs
unchanged. `python -m clinical_core.agere.tiered.portability` builds layout B, has a frontier model draft a mapping,
audits a mapping against Agere's documented vocabulary, and runs one audit-driven revision. A drafted mapping needs a
person's review before use: the audit cannot see duplicated rows. `AGERE_TIERED_FOREIGN=on` instead shows a
warehouse's own tables to the frontier SQL writer, with no recipes.

## Run it on an EMR

Agere connects to an EMR's FHIR API (US Core) the way backend analytics apps do: SMART Backend Services authorization
(a JWT signed RS384 with Agere's private key, exchanged for a short-lived token), FHIR search for one patient's current
record, and a Bulk Data `$export` for the population. `fhir_client.sync` exports, downloads and imports into a semantic
layer (`fhir_import.py`), where every definition, check and recipe applies; `fhir_live.answer` answers a question about one
patient from their live record. `fhir_server.py` is a local stand-in EMR that answers the same requests from a synthetic
export (`fhir_synth.py`), so the whole connection is tested on this machine. A real EMR changes the connection settings:
its FHIR base URL, the client id it assigns and the private key whose public half it registers.

## How the build is run

`AGENTS.md` states the rules for anyone building here. `AGERE_BUILD_CONTROL.json` holds the current gate, checkpoint and
decision log, and `check_agere_build_control.py` checks that the build record is consistent.
