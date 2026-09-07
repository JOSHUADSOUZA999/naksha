"""Stage-level failures.

Transport and vendor failures are normalised in `providers/base.py` — these are the
ones that belong to the stage rather than to any provider. Neither should escape
`extract_brief` in normal operation; the deterministic fallback catches them. They
exist so the reason a fallback fired is a typed value in provenance rather than a
stringly-typed guess.
"""

from __future__ import annotations


class IntentError(Exception):
    """Base class for stage ① failures."""


class SchemaRetriesExhausted(IntentError):
    """The model returned output that never validated against `BriefDraft`.

    Carries the per-attempt errors so provenance can record which constraint kept
    failing — usually the plot-side sanity bounds, which means the model was reading
    square feet as a side length.
    """

    def __init__(self, attempts: int, errors: list[str]) -> None:
        self.attempts = attempts
        self.errors = errors
        super().__init__(
            f"model did not produce a valid Brief in {attempts} attempt(s): "
            f"{errors[-1] if errors else 'no detail'}"
        )
