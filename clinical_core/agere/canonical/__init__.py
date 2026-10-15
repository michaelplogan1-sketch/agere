"""Agere canonical analytic model (canonical/1).

The canonical tables themselves are produced by the Tuva Project (dbt) from an input layer. This package holds the parts
Agere owns around it: the raw-stream coverage gate (`coverage`), global current-version resolution that feeds the Tuva
input layer (`versions`), LLM-facing documentation generated from the built database (`docs_gen`), and the code-family
config under config/agere/canonical/. See docs/agere/CANONICAL_MODEL.md.
"""
from pathlib import Path

VERSION = 'canonical/1'
REPO = Path(__file__).resolve().parents[3]
CONFIG = REPO / 'config/agere/canonical'
