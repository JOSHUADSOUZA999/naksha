"""What stage ⑤ LAYOUT produces: every room with a rectangle.

The first IR type that carries coordinates, and the boundary decision 1 draws. A
`Program` says *what rooms, how big, next to what*; a `Layout` says where. Keeping
them separate is what stops an LLM being asked for geometry it reasons badly about.

Same frame as `Envelope`: metres, x east, y north, origin at the plot's south-west
corner. A layout tiles its bounds **exactly** — gap-free and overlap-free — which is
the property a slicing tree gives by construction and which is validated here so a
solver that loses it fails loudly rather than producing a plan with a hole in it.
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.ir.base import DerivedFieldsAreOutputOnly

from app.ir.enums import Facing, Sector

# Millimetre. Slicing arithmetic is float division, so exact equality is the wrong
# test; a gap this size is not a gap, and one larger is a real defect.
TOLERANCE_M = 1e-3


class PlacedRoom(DerivedFieldsAreOutputOnly):
    """One room, and where it went."""

    model_config = ConfigDict(extra="forbid")

    room_id: str = Field(description="Matches a `RoomSpec.id` in the Program.", min_length=1)
    x_min_m: float
    y_min_m: float
    x_max_m: float
    y_max_m: float

    @model_validator(mode="after")
    def _has_area(self) -> Self:
        if self.x_max_m - self.x_min_m <= TOLERANCE_M:
            raise ValueError(f"{self.room_id}: zero width")
        if self.y_max_m - self.y_min_m <= TOLERANCE_M:
            raise ValueError(f"{self.room_id}: zero depth")
        return self

    @computed_field
    @property
    def width_m(self) -> float:
        return self.x_max_m - self.x_min_m

    @computed_field
    @property
    def depth_m(self) -> float:
        return self.y_max_m - self.y_min_m

    @computed_field
    @property
    def area_sq_m(self) -> float:
        return self.width_m * self.depth_m

    @property
    def centroid(self) -> tuple[float, float]:
        return ((self.x_min_m + self.x_max_m) / 2, (self.y_min_m + self.y_max_m) / 2)

    @property
    def shortest_side_m(self) -> float:
        return min(self.width_m, self.depth_m)

    @property
    def aspect(self) -> float:
        """Longest over shortest. 1.0 is square; large is a corridor."""
        return max(self.width_m, self.depth_m) / self.shortest_side_m

    @property
    def polygon(self) -> list[tuple[float, float]]:
        """Counter-clockwise, not closed — the IR-wide convention."""
        return [
            (self.x_min_m, self.y_min_m),
            (self.x_max_m, self.y_min_m),
            (self.x_max_m, self.y_max_m),
            (self.x_min_m, self.y_max_m),
        ]

    def touches(self, other: PlacedRoom) -> bool:
        """True when the two share a wall segment of positive length.

        Corner contact does not count: two rooms meeting at a point cannot have a door
        between them, and calling that adjacency would satisfy a constraint the plan
        does not actually meet.
        """
        x_overlap = min(self.x_max_m, other.x_max_m) - max(self.x_min_m, other.x_min_m)
        y_overlap = min(self.y_max_m, other.y_max_m) - max(self.y_min_m, other.y_min_m)
        vertical_wall = abs(x_overlap) <= TOLERANCE_M and y_overlap > TOLERANCE_M
        horizontal_wall = abs(y_overlap) <= TOLERANCE_M and x_overlap > TOLERANCE_M
        return vertical_wall or horizontal_wall

    def overlaps(self, other: PlacedRoom) -> bool:
        """Shared *interior*, which is a defect. Touching walls is not overlapping."""
        x_overlap = min(self.x_max_m, other.x_max_m) - max(self.x_min_m, other.x_min_m)
        y_overlap = min(self.y_max_m, other.y_max_m) - max(self.y_min_m, other.y_min_m)
        return x_overlap > TOLERANCE_M and y_overlap > TOLERANCE_M


class Layout(DerivedFieldsAreOutputOnly):
    """A complete tiling of the buildable rectangle, one floor.

    `violations` is the honest half. A slicing tree always produces a *valid* tiling —
    that is its whole appeal — but not necessarily a good house, and a layout that
    quietly reports only a score hides which constraints it gave up on.
    """

    model_config = ConfigDict(extra="forbid")

    rooms: list[PlacedRoom] = Field(min_length=1)
    x_min_m: float
    y_min_m: float
    x_max_m: float
    y_max_m: float
    floor: int = Field(default=1, ge=1, le=4)
    road_edges: list[Facing] = Field(
        default_factory=list,
        description="Which compass edges front a road, copied from the Envelope. The "
        "layout is the compass-aligned object, so this is where a scorer can ask "
        "whether the car bay is reachable. Empty means the street is unknown and the "
        "check is skipped — `solve` always populates it.",
    )
    score: float = Field(
        default=0.0,
        description="Total penalty. Lower is better; 0.0 means every constraint met.",
        ge=0.0,
    )
    unbuildable: int = Field(
        default=0,
        description="Rooms below a legal minimum. Ranked ahead of `score`, because no "
        "quantity of satisfied preferences makes an unbuildable room acceptable.",
        ge=0,
    )
    violations: list[str] = Field(
        default_factory=list,
        description="What this layout could not satisfy, in words. Empty is a plan "
        "that met every stated constraint.",
    )

    @model_validator(mode="after")
    def _tiles_its_bounds_exactly(self) -> Self:
        """Gap-free and overlap-free — what a slicing tree guarantees by construction.

        Checked anyway. This is the invariant every later stage assumes: ⑥ draws walls
        along shared edges, ⑦ measures circulation through them. A layout with a hole
        in it produces a plan that cannot be built, and nothing downstream would say so.
        """
        for i, room in enumerate(self.rooms):
            for other in self.rooms[i + 1 :]:
                if room.overlaps(other):
                    raise ValueError(f"{room.room_id} overlaps {other.room_id}")

        covered = sum(room.area_sq_m for room in self.rooms)
        bounds = (self.x_max_m - self.x_min_m) * (self.y_max_m - self.y_min_m)
        # Areas summing short means a gap; over means an overlap the pairwise check
        # missed. Relative, because a millimetre matters more on 20 m² than on 200.
        if abs(covered - bounds) > max(TOLERANCE_M, bounds * 1e-6):
            raise ValueError(
                f"rooms cover {covered:.4f} m² of a {bounds:.4f} m² envelope "
                f"— {'gap' if covered < bounds else 'overlap'} of "
                f"{abs(covered - bounds):.4f} m²"
            )
        ids = [room.room_id for room in self.rooms]
        if len(set(ids)) != len(ids):
            raise ValueError("a room was placed twice")
        return self

    @computed_field
    @property
    def area_sq_m(self) -> float:
        return (self.x_max_m - self.x_min_m) * (self.y_max_m - self.y_min_m)

    def sector_of(self, room: PlacedRoom) -> Sector:
        """Which Vastu zone a room sits in, by centroid on a 3x3 grid.

        The standard approximation: thirds each way, the middle cell being the
        brahmasthan. Advisory, like everything Vastu here — the ruleset version that
        produced a score is what a user argues with, not the score itself.
        """
        third_x = (self.x_max_m - self.x_min_m) / 3
        third_y = (self.y_max_m - self.y_min_m) / 3
        cx, cy = room.centroid
        col = min(2, int((cx - self.x_min_m) / third_x)) if third_x else 1
        row = min(2, int((cy - self.y_min_m) / third_y)) if third_y else 1
        return _SECTOR_GRID[row][col]

    def by_id(self, room_id: str) -> PlacedRoom | None:
        return next((r for r in self.rooms if r.room_id == room_id), None)


# Row 0 is south (low y), row 2 is north. Column 0 is west, column 2 is east.
_SECTOR_GRID: tuple[tuple[Sector, ...], ...] = (
    (Sector.SOUTH_WEST, Sector.SOUTH, Sector.SOUTH_EAST),
    (Sector.WEST, Sector.BRAHMASTHAN, Sector.EAST),
    (Sector.NORTH_WEST, Sector.NORTH, Sector.NORTH_EAST),
)


class PlanBundle(DerivedFieldsAreOutputOnly):
    """One plan, with everything a viewer needs to draw *and explain* it.

    The contract across the backend/frontend edge, and a model rather than a bare dict
    because that edge is exactly where CLAUDE.md's rule applies. A viewer needs three
    things a `Layout` alone does not carry: what the user asked for, what the rules
    allowed, and what each rectangle *is* — `Layout` knows room ids, not that `bed1`
    is a master bedroom that wanted the south-west.

    `seed` travels with it because a plan you cannot reproduce is one you cannot
    discuss. Two people looking at the same drawing need to be able to regenerate it.
    """

    model_config = ConfigDict(extra="forbid")

    brief_text: str = Field(description="The user's own words, for the title.")
    envelope: "Envelope"
    program: "Program"
    layouts: list[Layout] = Field(description="One per floor, ascending.")
    seed: int = Field(description="Regenerates these exact layouts.")
    candidates: int = Field(description="Topologies tried per floor.", gt=0)

    @computed_field
    @property
    def total_score(self) -> float:
        return sum(layout.score for layout in self.layouts)


from app.ir.envelope import Envelope  # noqa: E402  — circular at module scope
from app.ir.plan import Program  # noqa: E402

PlanBundle.model_rebuild()
