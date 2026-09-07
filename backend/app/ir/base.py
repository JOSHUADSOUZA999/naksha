"""Shared model behaviour for the IR.

One rule lives here, and it exists because `computed_field` and `extra="forbid"`
contradict each other. Computed fields are *serialised* — that is the point of them,
so a consumer reading JSON sees `area_sq_m` without recomputing it — but they are not
*inputs*, so a strict model rejects its own output. A type that cannot read what it
just wrote is write-only: no round-trip, no test fixture built from a saved payload,
no API that echoes a stored plan back.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, model_validator


class DerivedFieldsAreOutputOnly(BaseModel):
    """Accept a model's own serialised form back, ignoring what it recomputes.

    Dropping rather than checking, deliberately. `PlotSpec` does the stricter thing —
    a `facing` disagreeing with `road_edges[0]` raises rather than riding along stale
    — because there the derived value names a *choice* someone might have edited by
    hand. These are arithmetic: `area_sq_m` is width times depth and cannot
    meaningfully disagree, so a mismatch means a stale file, not a decision, and
    recomputing is the right answer rather than an error.
    """

    @model_validator(mode="before")
    @classmethod
    def _drop_derived(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        derived = set(cls.model_computed_fields)
        if not derived.intersection(data):
            return data
        return {key: value for key, value in data.items() if key not in derived}
