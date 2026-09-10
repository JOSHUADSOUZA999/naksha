"""What stage ③ PROGRAM produces: rooms, sizes, and how they must sit together.

**No coordinates.** Decision 1 in CLAUDE.md: the LLM decides *what rooms, how big,
next to what, which sector*; a solver turns that into geometry. Nothing in this file
has an x or a y, and adding one would move the hardest part of the problem to the
component worst equipped for it.

A `Program` is therefore a graph with sizes attached — the input stage ⑤ tiles, not a
layout. It is also the first IR type with internal references (`AdjacencySpec` names
rooms by id), so referential integrity is validated on construction rather than
discovered inside the solver.
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.ir.base import DerivedFieldsAreOutputOnly

from app.ir.enums import Relation, Sector, SpaceKind


class RoomSpec(BaseModel):
    """One space, sized but unplaced.

    Two areas, deliberately. `min_area_sq_m` is the floor a bye-law or NBC minimum
    sets — below it the room is not legally a room. `target_area_sq_m` is what it
    should be if the budget allows. A solver given only one number either produces
    cramped plans or infeasible ones; the gap between them is its slack.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        description="Stable handle, unique within the Program. Adjacencies reference "
        "it, and two bedrooms need to be distinguishable.",
        min_length=1,
        max_length=40,
    )
    kind: SpaceKind
    min_area_sq_m: float = Field(description="Below this it is not legally a room.", gt=0)
    target_area_sq_m: float = Field(description="What it should be if the budget allows.", gt=0)
    max_target_sq_m: float | None = Field(
        default=None,
        gt=0,
        description="What this room grows to on a site that can afford it. Null means "
        "it does not grow — a bathroom and a corridor are the size they are, and a car "
        "bay is a statutory 18 m² on any plot. The ceiling is the load-bearing half: "
        "without one, surplus lands in whichever room the solver happens to pick.",
    )
    min_width_m: float = Field(
        description="Shortest usable dimension. Area alone permits a 1 m x 9 m bedroom.",
        gt=0,
    )
    max_aspect: float = Field(
        default=2.5,
        description="Longest side over shortest. The other half of stopping a room "
        "from solving into a corridor.",
        ge=1.0,
    )
    sector: Sector | None = Field(
        default=None,
        description="Preferred Vastu zone. Null means no preference — which is a "
        "different claim from `brahmasthan`, the centre.",
    )
    sector_is_hard: bool = Field(
        default=False,
        description="True only under `vastu=strict`, where the sector becomes a hard "
        "constraint in Stage B and may render the brief infeasible.",
    )
    needs_exterior_wall: bool = Field(
        default=False,
        description="True where NBC requires light and ventilation — habitable rooms "
        "and kitchens. A constraint on the tiling, not a preference.",
    )
    needs_door: bool = Field(
        default=True,
        description="True where a person reaches this space through the house. Stage "
        "⑥ guarantees a door to it and stage ⑦ reports it stranded without one — the "
        "same flag, so the two cannot disagree. False for a car porch, entered from "
        "the street.",
    )
    needs_road_access: bool = Field(
        default=False,
        description="True where the room must touch a road-facing boundary — the car "
        "bay and the entrance. Distinct from `needs_exterior_wall`: a window can face "
        "the neighbour, a driveway cannot.",
    )
    floor: int = Field(default=1, description="Ground counted as 1.", ge=1, le=4)

    @model_validator(mode="after")
    def _target_is_not_below_the_floor(self) -> Self:
        if self.target_area_sq_m < self.min_area_sq_m:
            raise ValueError(
                f"{self.id}: target {self.target_area_sq_m} m² is below the minimum "
                f"{self.min_area_sq_m} m²"
            )
        if self.max_target_sq_m is not None and self.max_target_sq_m < self.target_area_sq_m:
            raise ValueError(
                f"{self.id}: growth ceiling {self.max_target_sq_m} m² is below the "
                f"target {self.target_area_sq_m} m²"
            )
        return self


class AdjacencySpec(BaseModel):
    """A required relationship between two spaces."""

    model_config = ConfigDict(extra="forbid")

    a: str = Field(description="RoomSpec id.", min_length=1)
    b: str = Field(description="RoomSpec id.", min_length=1)
    relation: Relation
    hard: bool = Field(
        default=True,
        description="Hard constraints bind in Stage B and can make a brief "
        "infeasible; soft ones are objective terms. A kitchen reachable from the hall "
        "is hard; a study away from the road is soft.",
    )

    @model_validator(mode="after")
    def _two_distinct_rooms(self) -> Self:
        if self.a == self.b:
            raise ValueError(f"{self.a!r} cannot be adjacent to itself")
        return self


class Program(DerivedFieldsAreOutputOnly):
    """The full room list and its constraint graph — stage ⑤'s input."""

    model_config = ConfigDict(extra="forbid")

    rooms: list[RoomSpec] = Field(min_length=1)
    adjacencies: list[AdjacencySpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _references_resolve(self) -> Self:
        """Referential integrity, per the `ir/` rule.

        An adjacency naming a room that does not exist is the kind of defect that
        surfaces as an unsatisfiable constraint deep inside the solver, where nothing
        can say which line of the program caused it.
        """
        ids = [room.id for room in self.rooms]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"duplicate room ids: {sorted(duplicates)}")

        known = set(ids)
        for edge in self.adjacencies:
            missing = {edge.a, edge.b} - known
            if missing:
                raise ValueError(f"adjacency references unknown room(s): {sorted(missing)}")

        # (a, b) and (b, a) are the same edge. Two of them is a contradiction waiting
        # to happen — one hard "connected", one soft "separated" — so reject the shape
        # rather than resolve it arbitrarily.
        seen: set[frozenset[str]] = set()
        for edge in self.adjacencies:
            pair = frozenset({edge.a, edge.b})
            if pair in seen:
                raise ValueError(f"duplicate adjacency between {sorted(pair)}")
            seen.add(pair)
        return self

    @computed_field
    @property
    def min_area_sq_m(self) -> float:
        """Floor area if every room is at its legal minimum. What feasibility tests."""
        return sum(room.min_area_sq_m for room in self.rooms)

    @computed_field
    @property
    def target_area_sq_m(self) -> float:
        return sum(room.target_area_sq_m for room in self.rooms)

    def on_floor(self, floor: int) -> list[RoomSpec]:
        """Stage ⑤ tiles one floor at a time; each is its own rectangle."""
        return [room for room in self.rooms if room.floor == floor]


class RoomRequest(BaseModel):
    """One space the model asks for. **Not** a `RoomSpec` — deliberately smaller.

    The model decides *which rooms, on which floor, in which sector, and why*. It is
    never asked for `min_area_sq_m` or `min_width_m`: those are NBC figures, and a
    model inventing one would produce exactly the kind of authoritative-looking wrong
    number the whole `verified: false` discipline exists to prevent. Sizes come from
    `rules/spaces_v1.json` when this is merged into a `Program`.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        description="Short, stable, unique: 'hall', 'bed1', 'bath2'.",
        min_length=1,
        max_length=40,
    )
    kind: SpaceKind
    floor: int = Field(default=1, description="Ground counted as 1.", ge=1, le=4)
    sector: Sector | None = Field(
        default=None,
        description="Preferred Vastu zone, or null for no preference. Null and "
        "`brahmasthan` are different claims.",
    )
    why: str = Field(
        default="",
        description="Why this room, on this floor, in this sector — one clause. Say "
        "it when the brief drove the choice: 'ground floor, near the entrance, "
        "because a parent is elderly'. Leave empty when it is simply conventional.",
        max_length=160,
    )


class ProgramDraft(BaseModel):
    """Exactly what the model returns for stage ③.

    Split from `Program` the same way `BriefDraft` is split from `Brief`: what the
    model may decide is separated from what the rules supply, so the schema it sees
    has no field for a number it should not be inventing.
    """

    model_config = ConfigDict(extra="forbid")

    rooms: list[RoomRequest] = Field(min_length=1)
    adjacencies: list[AdjacencySpec] = Field(default_factory=list)
