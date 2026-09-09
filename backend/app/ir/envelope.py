"""What stage ② ENVELOPE produces: the rectangle you are allowed to build inside.

Separate from `models.py` because the frames differ, and conflating them is the
mistake this file exists to avoid. A `PlotSpec` is **road-relative** — `width_m` is
frontage, `depth_m` runs away from the road — while an `Envelope` is **compass-
aligned**, x east and y north, because setbacks attach to compass edges and Vastu
sectors are scored in the same frame. Converting between the two is `envelope.geometry`
and nowhere else.

Origin is the plot's south-west corner, so the plot spans (0, 0) to its extents and
the envelope is a rectangle strictly inside it.
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.ir.base import DerivedFieldsAreOutputOnly

from app.ir.enums import Facing

# The only edges a rectangle has. Ordered by bearing so iteration is deterministic.
CARDINALS: tuple[Facing, ...] = (Facing.NORTH, Facing.EAST, Facing.SOUTH, Facing.WEST)


class Envelope(DerivedFieldsAreOutputOnly):
    """The buildable rectangle, plus the caps on what may fill it.

    Three different limits, and they are not interchangeable. The rectangle bounds
    *where* you may build; `max_coverage` bounds the footprint as a fraction of the
    plot; `max_far` bounds total built area across all floors. A plan can sit inside
    the rectangle and still breach either cap.
    """

    model_config = ConfigDict(extra="forbid")

    x_min_m: float = Field(description="West edge of the buildable rect, metres east of the plot's SW corner.")
    y_min_m: float = Field(description="South edge, metres north of the plot's SW corner.")
    x_max_m: float = Field(description="East edge.")
    y_max_m: float = Field(description="North edge.")

    setbacks: dict[Facing, float] = Field(
        description="Applied margin per compass edge, metres. Every road edge carries "
        "the front setback — a corner plot has two."
    )
    plot_area_sq_m: float = Field(description="The whole site, for the ratio caps.", gt=0)
    max_coverage: float = Field(description="Footprint as a fraction of plot area.", gt=0, le=1)
    max_far: float = Field(description="Total built area across all floors, as a multiple of plot area.", gt=0)

    max_floors: int = Field(
        description="Storeys the abutting road permits, ground counted as 1. Capped by "
        "road width irrespective of the FAR earned — a narrow road restricts height "
        "even on a plot whose area would allow more built area.",
        ge=1,
        le=4,
    )
    road_width_m: float = Field(description="Abutting road width the caps were read from.", gt=0)
    road_edges: list[Facing] = Field(
        min_length=1,
        description="Which compass edges front a road, largest setback first. Required "
        "rather than defaulted: the setbacks above were computed from these, so an "
        "envelope that cannot say where the street is was never buildable in the first "
        "place. Stage \u2464 needs them to keep the car bay and the entrance reachable.",
    )

    ruleset: str = Field(description="Which rule revision produced this, as 'name@hash'.")
    authority: str = Field(description="Whose bye-laws, e.g. 'BBMP'.")

    @model_validator(mode="after")
    def _rect_is_real(self) -> Self:
        if self.x_max_m <= self.x_min_m or self.y_max_m <= self.y_min_m:
            raise ValueError(
                f"envelope has no area: x {self.x_min_m}-{self.x_max_m}, "
                f"y {self.y_min_m}-{self.y_max_m}"
            )
        if set(self.setbacks) != set(CARDINALS):
            raise ValueError(f"setbacks must cover all four cardinals, got {set(self.setbacks)}")
        return self

    @computed_field
    @property
    def east_west_m(self) -> float:
        return self.x_max_m - self.x_min_m

    @computed_field
    @property
    def north_south_m(self) -> float:
        return self.y_max_m - self.y_min_m

    @computed_field
    @property
    def area_sq_m(self) -> float:
        """Area of the buildable rectangle — an upper bound on the footprint, not the
        footprint itself, which `max_footprint_sq_m` may cut further."""
        return self.east_west_m * self.north_south_m

    @computed_field
    @property
    def max_footprint_sq_m(self) -> float:
        """Whichever binds first: the rectangle, or the coverage cap."""
        return min(self.area_sq_m, self.plot_area_sq_m * self.max_coverage)

    @computed_field
    @property
    def max_built_area_sq_m(self) -> float:
        """Total across all floors. This is the budget stage ③ spends on rooms."""
        return self.plot_area_sq_m * self.max_far

    @property
    def polygon(self) -> list[tuple[float, float]]:
        """Counter-clockwise, not closed — the IR-wide convention, so the solver's
        eventual input never has to be reshaped."""
        return [
            (self.x_min_m, self.y_min_m),
            (self.x_max_m, self.y_min_m),
            (self.x_max_m, self.y_max_m),
            (self.x_min_m, self.y_max_m),
        ]
