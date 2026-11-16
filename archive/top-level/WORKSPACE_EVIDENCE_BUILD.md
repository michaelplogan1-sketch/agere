# Agere workspace and connected-source investigation

Delivered October 10, 2026. All patients and records are synthetic.

The main `/stack` workbench is rebuilt around questions, population findings and source investigation. It now has a persistent workspace navigation, restrained ivory/sage/ink visual system, original animated evidence schematic, live warehouse metrics, a searchable analysis library, an evidence atlas and a connected-stack view. The screenshot is the working application, not a design mockup.

![Working desktop workspace](artifacts/agere-evidence-workspace-v1/workspace-desktop.jpg)

## Functional stack addition

`clinical/evidence_threads.py` adds a read-only received-source inventory, paginated records and bounded same-patient reference traversal. It reuses the existing typed source catalog, explicit clocks, original narrative mapping and source validation. Directed references remain references: they do not imply causation, clinical chronology, current treatment or completed care. Missing, unmapped, wrong-type and cross-patient references remain explicit issues. Full connected-component accounting distinguishes displayed nodes, omitted nodes and boundary links.

Both retrieved passages and population requirement evidence now open the same investigation drawer. Source fields and whole narrative paragraphs retain exact offsets. Selecting a unique phrase invokes the existing statement-reader controls; it does not introduce another reader or change a model's input contract. No interpretation is submitted automatically and no review is fabricated.

The drawer also prepares source-category context through the existing context builder, with selectable 8,000 / 16,000 / 32,000 / 60,000 UTF-8-byte budgets. The UI explicitly states that this context includes all received records from the displayed categories for that patient, not just the graph nodes. Incomplete packets are not represented as complete cases. Provider-native token checking remains separate; bytes are not tokens.

## Evidence

- Active warehouse unchanged: v1.23, **1,614 patients / 1,238,686 records / 158 source categories**. Sources: active configuration and `source-atlas.json`.
- **39 focused tests passed**, covering linked-source boundaries, wrong-patient/wrong-type references, cycles, omissions, exact narrative spans, mutation identities and existing context/clock handling. Log: `focused-tests.log`.
- One received root opened from every category: **158 categories, 806 displayed nodes, 748 exact reference links, 98 exact paragraphs and 76 reader-target handoffs** checked against received records. This is a structural integration audit, not clinical accuracy or complete semantic coverage. Source: `source-verification.json`.
- Live search returned **8** passages and opened a source network directly. Unknown categories and wrong-patient roots were rejected. Source: `http-verification.json`.
- P-1001 renewal example: **5** connected received records. At a 16,000-byte budget, **12/22** source units occupy **15,840 bytes**, with incomplete-case dispatch blocked. At 32,000 bytes, **22/22** occupy **26,913 bytes**, with the declared scope complete and native token checking still required. Source: `http-verification.json` and `renewal-thread.json`.
- Existing full-population renewal contract remains **702 supported / 44 contradicted / 868 unresolved**. The browser verified its population → requirement → source-network path, including a **13-record** supporting component. These are documentary results, not medication recommendations. Source: `browser-verification.json`, previous v1.23 qualification.
- Desktop **1440px** and mobile **390px** layouts verified without horizontal page overflow; mobile operation navigation, keyboard source selection, library filtering, exact-phrase controls and context-budget expansion exercised. Browser receipt and actual JPEGs retained.
- **0 paid model calls**, model training runs, human reviews, clinical actions or external messages in this milestone. Frozen models, protected cohorts and existing failed experiments remain unchanged.

The active v1.23 implementation files listed in its previous receipt still match their recorded hashes. No generator or warehouse migration occurred. A test-runner import typo was corrected before the HTTP audit; page zoom on the older 127.0.0.1 preview required using localhost for normal-zoom screenshot verification. These were engineering issues, not model failures.

## Design direction

Reviewed the official [Databricks](https://www.databricks.com/), [System](https://www.system.com/), [TextQL](https://www.textql.com/) and [TypeSafe](https://typesafe.ai/) sites for hierarchy, typography and technical product presentation. Agere's components and schematic are original; vendor assets and claims were not copied. The original design-system skill informed consistent components, loading/empty/error states, responsive navigation, focus indicators and reduced-motion behavior.

## Remaining work

This does not qualify broader clinical interpretation, create a new trained model, establish clinical superiority or finish the entire product. The atlas describes received sources; semantic mappings and source capture remain incomplete. Graphs are bounded and reference-based, not inferred whole-ontology graphs. The existing reader independently reconstructs its original single-field context; the graph is not silently supplied as new model context. Optional trained readers retain their prior failed/shadow qualification status. Current-source predictors remain unfitted where timing-safe development targets are single-class.

Continue broader source interpretation into reusable clinical/program state and evidence-supported review proposals, with exact source corrections and protected development boundaries. Extend the new visual system into program and measure workspaces as those capabilities develop. Broader payer/provider/contract applicability, portable customer integration, a current capability matrix and full acceptance remain unfinished.

Current owned server: **79935**, replacing 90968 / 13112. Start command: `.venv/bin/python serve_current_warehouse.py --allow-live-stack --port 8885`. App: [Agere workspace](http://localhost:8885/stack). No verification jobs remain running.
