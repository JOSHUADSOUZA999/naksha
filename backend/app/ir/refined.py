"""What stage ⑥ REFINE produces: walls with thickness, and the openings in them.

**Walls are geometry, not a rendering trick.** A wall in SVG is a stroke with a width;
in DXF it is a centreline on a `WALLS` layer, and a door is a block with a swing arc.
If the thickness lived only in the renderer there would be no way to export one, and
every consumer — SVG, the Konva editor, DXF later — would have to reinvent it and
disagree. So the centreline and the thickness are in the IR and the renderers are dumb.

This is where stage ⑤'s central simplification gets undone. `Layout` tiles its bounds
*exactly*, which is what makes the tiling gap-free by construction — and it means two
rooms share a line of zero width. A 200 mm wall is not a gap the solver failed to
close; it is a thing the solver was deliberately not asked to model. Stage ⑥ gives
that line a thickness and charges it to both rooms, half each.

Openings reference a wall by id and sit at an offset along it, rather than carrying
their own coordinates. A door is a hole *in a wall* — storing it as a free-floating
rectangle would let the two disagree, which is exactly the class of bug that
referential integrity in `ir/` exists to prevent.
"""

from __future__ import annotations

import math
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.ir.base import DerivedFieldsAreOutputOnly
from app.ir.enums import OpeningKind, WallKind

# Walls run on cardinal axes only — v1 is rectangular plots and a slicing tree, so a
# wall is horizontal or vertical and never anything else. Curved walls are out of
# scope by CLAUDE.md, and diagonal ones cannot arise from a rectangular tiling.
AXIS_TOLERANCE_M = 1e-6


class Wall(BaseModel):
    """One straight run of wall, as a centreline plus a thickness.

    The centreline sits on the line stage ⑤ tiled to, so the wall eats equally into
    the rooms on either side. That symmetry is the reason to store a centreline rather
    than a face: a face belongs to one room, and choosing which room loses the
    thickness would be an arbitrary decision made silently, room by room.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable handle; openings reference it.", min_length=1)
    x1_m: float
    y1_m: float
    x2_m: float
    y2_m: float
    thickness_m: float = Field(gt=0)
    kind: WallKind
    rooms: list[str] = Field(
        min_length=1,
        max_length=2,
        description="Room ids this wall bounds — two for an interior wall, one for an "
        "exterior. What makes a door placeable: an opening needs to know what it "
        "connects.",
    )

    @model_validator(mode="after")
    def _is_axis_aligned_and_real(self) -> Self:
        horizontal = abs(self.y2_m - self.y1_m) <= AXIS_TOLERANCE_M
        vertical = abs(self.x2_m - self.x1_m) <= AXIS_TOLERANCE_M
        if horizontal == vertical:
            # Both true is a zero-length wall; neither is a diagonal one. A rectangular
            # tiling can produce neither, so either means the extractor is wrong.
            raise ValueError(
                f"{self.id}: wall must be horizontal or vertical and have length, "
                f"got ({self.x1_m}, {self.y1_m})-({self.x2_m}, {self.y2_m})"
            )
        return self

    @computed_field
    @property
    def length_m(self) -> float:
        return math.hypot(self.x2_m - self.x1_m, self.y2_m - self.y1_m)

    @computed_field
    @property
    def is_vertical(self) -> bool:
        return abs(self.x2_m - self.x1_m) <= AXIS_TOLERANCE_M

    def point_at(self, offset_m: float) -> tuple[float, float]:
        """A point `offset_m` along the centreline from the (x1, y1) end."""
        span = self.length_m
        if span <= 0:                                   # unreachable; the validator refuses it
            return (self.x1_m, self.y1_m)
        fraction = offset_m / span
        return (
            self.x1_m + (self.x2_m - self.x1_m) * fraction,
            self.y1_m + (self.y2_m - self.y1_m) * fraction,
        )


class Opening(BaseModel):
    """A door or a window: a hole in one wall, positioned along it."""

    model_config = ConfigDict(extra="forbid")

    wall_id: str = Field(min_length=1)
    kind: OpeningKind
    offset_m: float = Field(
        ge=0, description="Distance from the wall's (x1, y1) end to the opening's centre."
    )
    width_m: float = Field(gt=0)
    connects: list[str] = Field(
        default_factory=list,
        max_length=2,
        description="Room ids on either side. One for an external door or a window; "
        "two for an internal door. Carried so circulation can be checked in ⑦ without "
        "re-deriving it from geometry.",
    )


class RefinedFloor(DerivedFieldsAreOutputOnly):
    """One storey, drawn: every wall and every opening in it."""

    model_config = ConfigDict(extra="forbid")

    floor: int = Field(default=1, ge=1, le=4)
    walls: list[Wall] = Field(min_length=1)
    openings: list[Opening] = Field(default_factory=list)

    @model_validator(mode="after")
    def _openings_sit_in_walls_that_exist(self) -> Self:
        """Referential integrity, per the `ir/` rule.

        An opening naming a missing wall, or hanging off the end of a real one, is the
        kind of defect that surfaces as a door drawn in mid-air — visible to a person
        looking at the drawing and to nothing else in the pipeline.
        """
        walls = {wall.id: wall for wall in self.walls}
        duplicates = {w.id for w in self.walls if sum(x.id == w.id for x in self.walls) > 1}
        if duplicates:
            raise ValueError(f"duplicate wall ids: {sorted(duplicates)}")

        for opening in self.openings:
            wall = walls.get(opening.wall_id)
            if wall is None:
                raise ValueError(f"opening references unknown wall {opening.wall_id!r}")
            half = opening.width_m / 2
            if opening.offset_m - half < -AXIS_TOLERANCE_M:
                raise ValueError(
                    f"opening in {wall.id} starts before the wall does "
                    f"({opening.offset_m:.3f} - {half:.3f} m)"
                )
            if opening.offset_m + half > wall.length_m + AXIS_TOLERANCE_M:
                raise ValueError(
                    f"opening in {wall.id} runs {opening.offset_m + half:.3f} m along a "
                    f"{wall.length_m:.3f} m wall"
                )
        return self

    def by_id(self, wall_id: str) -> Wall | None:
        for wall in self.walls:
            if wall.id == wall_id:
                return wall
        return None

    def openings_in(self, wall_id: str) -> list[Opening]:
        return [o for o in self.openings if o.wall_id == wall_id]
