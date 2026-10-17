"""Hybrid answer core (plan step 5, docs/agere/HANDOFF_2026-10-17.md).

A cheap model writes an analysis contract and SQL (or names a governed metric); everything after that is deterministic:
validate the contract (contract.py), compile a governed metric only when it matches the contract, else run the model's
bounded SQL (execute.py), check the executed result against the contract, then vote among the runs that passed
(vote.py). The answer is always the executed result, never a number the model typed. Nothing is promoted into a library
or training set automatically.
"""
