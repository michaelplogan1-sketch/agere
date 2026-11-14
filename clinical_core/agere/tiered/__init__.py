"""Tiered answering core: each question goes to the cheapest tier that answers it correctly.

Tier 0  bottles   previously solved question shapes; zero model calls
Tier 1  recipes   deterministic SQL over the semantic layer; every number is computed here
Tier 2  system1   System One (Jev / Perplexity): classify the question shape, with a confidence
Tier 3  slm       small model: fill a recipe's typed parameters from the question
Tier 4  llm       frontier model: write a parameterised recipe from schema and definitions, never rows

The router is code plus Tier 2. A question climbs one rung only when the current rung is not confident
or its output fails deterministic validation. Every question writes one ledger row.
"""
VERSION = 'tiered-core/1'
