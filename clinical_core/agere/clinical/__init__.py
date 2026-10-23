"""Evidence-bound clinical facts, reconciliation and explicit workflow contracts."""

from .answer_budget import guard_sqlite as _guard_sqlite
_guard_sqlite()  # long statements stop at the running tier's deadline (answer_budget)
