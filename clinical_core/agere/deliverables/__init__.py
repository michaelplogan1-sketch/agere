"""Agere deliverables: free-text requests composed into self-contained, interactive, verified artifacts.

- compose.py   compose(ws, request, audience) / revise(ws, id, instruction): deterministic intent -> tools -> frontier model design
               -> number verifier (one repair, then fail closed); reference examples render with no model
- tools.py     the stack as tools with receipts (catalog, cohort, ask, plan, work, simulate, what_if, attribution, contract,
               settlement, market, quality, barriers, outreach); cached per workspace
- receipts.py  Bundle: tool results keyed by receipt id (definition, SQL fingerprint, as_of, data fingerprint)
- runtime.py   the page runtime (data binding, formatting, receipts table, light/dark, print) and wrap()
- verify.py    every rendered number must resolve to a tool result; no network; bindings resolve
- finance.py   contract what-if math (Python reference + browser twin), Nash bargaining, exact Shapley attribution
- examples.py  reference deliverables (war room, pharmacist decision cards, self-running program) = few-shot examples
- intent.py    deterministic request parsing (program, audience, tools, example)
- writeback.py a person's decision from a page written back to the work store
"""
from .compose import compose, revise, load, listing
from .examples import EXAMPLES
from .writeback import record_decision

__all__ = ['compose', 'revise', 'load', 'listing', 'EXAMPLES', 'record_decision']
