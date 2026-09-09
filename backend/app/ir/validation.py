"""What stage ⑦ VALIDATE produces: findings about a finished plan.

Separate from `score`, and the split is the point. `score` ranks *candidates* during
the search — it must be cheap, it runs tens of thousands of times, and everything it
knows about is a room rectangle. This runs once, on the drawing, after ⑥ has put walls
and doors in; it can ask questions that only make sense of a finished plan, and the
first of those is whether you can walk through the house.

A `Finding` names a defect, not a score. Stage ④ explains what to change before a plan
exists; this explains what is wrong with the one that does.
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.ir.base import DerivedFieldsAreOutputOnly
from app.ir.enums import Severity


class Finding(BaseModel):
    """One defect, in words a person can act on.

    `rooms` carries the ids rather than the message alone so a viewer can highlight
    them. A finding a user cannot locate on the drawing is a finding they cannot fix.
    """

    model_config = ConfigDict(extra="forbid")

    check: str = Field(
        description="Which check produced this — 'circulation', 'light', 'legality'. "
        "Stable, so a UI can group and a regression can be traced to its rule.",
        min_length=1,
    )
    severity: Severity
    message: str = Field(min_length=1)
    rooms: list[str] = Field(default_factory=list)


class Report(DerivedFieldsAreOutputOnly):
    """Every finding for one storey."""

    model_config = ConfigDict(extra="forbid")

    floor: int = Field(default=1, ge=1, le=4)
    findings: list[Finding] = Field(default_factory=list)
    checks_run: list[str] = Field(
        min_length=1,
        description="Which checks actually ran. A report with no findings means "
        "nothing was wrong *and* something was looked at — without this the two are "
        "indistinguishable, and a check that silently stopped running reads as a pass.",
    )

    @model_validator(mode="after")
    def _findings_come_from_checks_that_ran(self) -> Self:
        unknown = {f.check for f in self.findings} - set(self.checks_run)
        if unknown:
            raise ValueError(f"findings from checks not listed as run: {sorted(unknown)}")
        return self

    @computed_field
    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.severity is Severity.ERROR)

    @computed_field
    @property
    def ok(self) -> bool:
        """No errors. Warnings are things to look at, not things that are wrong."""
        return self.errors == 0

    def by_check(self, check: str) -> list[Finding]:
        return [f for f in self.findings if f.check == check]
