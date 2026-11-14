"""Tiered answering core: each question goes to the cheapest tier that answers it correctly.

Tier 0  bottles   previously solved question shapes; zero model calls
Tier 1  recipes   deterministic SQL over the semantic layer; every number is computed here
Tier 2  system1   System One (Jev / Perplexity): classify the question shape, with a confidence
Tier 3  slm       small model: fill a recipe's typed parameters from the question
Tier 4  llm       frontier model: write a parameterised recipe from schema and definitions, never rows

Around the router, in the app (app.py):
  methods   before the tiers: the wider application's analysis methods (forecasts, simulations, program effect,
            monitoring, contract and payer measures, ...) when its own compiler names one from the wording; after the
            cheap tiers, the same methods through a small-model restatement that code and System One check (methods.py)
  takeover  after the tiers: the frontier model answers with read-only queries, bounded by turns and seconds (takeover.py)

The router is code plus Tier 2. A question climbs one rung only when the current rung is not confident
or its output fails deterministic validation. Every question writes one ledger row.
"""
VERSION = 'tiered-core/1'
