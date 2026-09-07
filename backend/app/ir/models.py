"""The IR types stage ① produces and stage ② consumes.

Every model forbids unknown keys. A key we did not plan for is a bug — either the
model invented a field or an upstream contract changed — and failing loudly here is
much cheaper than discovering it inside the solver.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.ir.enums import Facing, RoomKind, VastuStance

# A plot side outside this range is almost always a unit-conversion mistake rather
# than a real site — 1200 read as metres instead of square feet, most often. We would
# rather fail the parse and retry with feedback than hand the solver a 1.2 km plot.
_MIN_SIDE_M = 2.0
_MAX_SIDE_M = 200.0


class PlotSpec(BaseModel):
    """The site as the user described it, normalised to metres."""

    model_config = ConfigDict(extra="forbid")

    width_m: float = Field(
        description="Frontage along the primary road, in metres.",
        gt=_MIN_SIDE_M,
        lt=_MAX_SIDE_M,
    )
    depth_m: float = Field(
        description="Depth away from the primary road, in metres.",
        gt=_MIN_SIDE_M,
        lt=_MAX_SIDE_M,
    )
    road_edges: list[Facing] = Field(
        description="Which sides of the plot abut a road, primary frontage first. "
        "One for an ordinary plot; two for a corner or through plot. A list rather "
        "than a single direction because *every* road edge takes the larger road-side "
        "setback — collapsing a corner plot to one direction produces a wrong "
        "envelope, not a cosmetic error.",
        min_length=1,
        max_length=2,
    )
    road_width_m: float | None = Field(
        default=None,
        description="Width of the primary abutting road in metres, if stated. Some "
        "city bye-laws scale the front setback with it.",
        gt=0,
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_derived_fields(cls, data: object) -> object:
        """Tolerate `facing`/`corner_plot` on input, since we serialise them.

        They are computed, so a Brief that round-trips through JSON carries them back
        in and `extra="forbid"` would reject its own output. Dropping them silently
        would let a stale `facing` ride alongside a corrected `road_edges`, so a value
        that disagrees is an error rather than a shrug.
        """
        if not isinstance(data, dict) or "road_edges" not in data:
            return data
        data = dict(data)
        data.pop("corner_plot", None)
        stated = data.pop("facing", None)
        edges = data["road_edges"] or []
        if stated is None or not edges:
            return data
        primary, given = (getattr(x, "value", x) for x in (edges[0], stated))
        if primary != given:
            raise ValueError(
                f"facing {given!r} disagrees with road_edges[0] {primary!r} — "
                "road_edges is the stored field"
            )
        return data

    @model_validator(mode="after")
    def _road_edges_are_distinct(self) -> Self:
        """One side, one road. A repeated edge is a parse error upstream, and it would
        quietly make `corner_plot` true for an ordinary plot."""
        if len(set(self.road_edges)) != len(self.road_edges):
            raise ValueError(f"road_edges names a side twice: {self.road_edges}")
        return self

    @computed_field  # serialised for consumers and `| jq`, never an input
    @property
    def facing(self) -> Facing:
        """The primary frontage — the direction "east facing site" names.

        Derived rather than stored so it cannot disagree with `road_edges`. Vastu
        scoring and the front-setback lookup both read this.
        """
        return self.road_edges[0]

    @computed_field
    @property
    def corner_plot(self) -> bool:
        """Two roads on *adjacent* sides.

        False for a through plot — roads on opposite sides — which is a different
        thing: it takes the larger setback on both ends rather than opening a second
        entrance onto a corner.
        """
        if len(self.road_edges) < 2:
            return False
        return abs(self.road_edges[0].degrees - self.road_edges[1].degrees) % 180 != 0

    @property
    def area_sq_m(self) -> float:
        return self.width_m * self.depth_m


class ProgramHints(BaseModel):
    """What the user asked to be in the house.

    Deliberately not the full room list — stage ③ PROGRAM expands this into every
    room including circulation. This captures only what a person states out loud.
    """

    model_config = ConfigDict(extra="forbid")

    bedrooms: int = Field(
        description="Bedroom count. The B in 'BHK'.", ge=1, le=8
    )
    bathrooms: int | None = Field(
        default=None,
        description="Bathroom count. Fill it with the conventional count for this "
        "bedroom count when the user does not say, and record an `Assumption`. Null "
        "only when even a guess would be unfounded.",
        ge=1,
        le=8,
    )
    extra_rooms: list[RoomKind] = Field(
        default_factory=list,
        description="Named rooms beyond the bedroom/hall/kitchen core.",
    )
    floors: int = Field(
        default=1,
        description="Floors requested, ground counted as 1.",
        ge=1,
        le=4,
    )
    occupants: int | None = Field(
        default=None,
        description="How many people will live here. Stage ③ sizes bedrooms and "
        "sanitary provision from it; assume the median household for the bedroom "
        "count when unstated and record an `Assumption`.",
        ge=1,
        le=20,
    )
    parking_bays: int | None = Field(
        default=None,
        description="Car bays to provide. 0 is a real answer — it means the user "
        "declined parking, which is different from null (nobody has decided yet).",
        ge=0,
        le=4,
    )
    covered_parking: bool | None = Field(
        default=None,
        description="True for a stilt or porch bay, False for open standing. Not "
        "cosmetic: a covered bay is built-up area and counts against FAR, so stage "
        "② has to know before it sizes the envelope.",
    )

    @model_validator(mode="after")
    def _dedupe_extra_rooms(self) -> Self:
        """Order-preserving dedupe.

        Models restating "pooja room" twice from one sentence is common and harmless;
        letting the duplicate through would double-count area in stage ③.
        """
        seen: dict[RoomKind, None] = {}
        for room in self.extra_rooms:
            seen[room] = None
        object.__setattr__(self, "extra_rooms", list(seen))
        return self


class Locale(BaseModel):
    """Where the site is. Selects the setback ruleset in stage ②."""

    model_config = ConfigDict(extra="forbid")

    city: str | None = Field(
        default=None,
        description="City or municipal area, e.g. 'Bengaluru'. Null if not stated.",
    )
    locality: str | None = Field(
        default=None,
        description="Neighbourhood or layout name, e.g. 'Whitefield'. Null if not stated.",
    )
    state: str | None = Field(
        default=None, description="Indian state or union territory. Null if not stated."
    )


class Assumption(BaseModel):
    """One value stage ① filled in rather than read.

    Structured rather than a sentence because each consumer wants a different slice.
    The CLI renders a table; stage ④ decides which guesses are worth interrupting the
    user over; a threshold on `confidence` is the only thing separating "Whitefield is
    in Bengaluru" from "a household of four". None of that is recoverable from prose.
    """

    model_config = ConfigDict(extra="forbid")

    field: str = Field(
        description="What was assumed, in the vocabulary a person would use — "
        "'bathrooms', 'city', 'parking'. Short label, not a dotted schema path.",
        min_length=1,
        max_length=32,
    )
    value: str = Field(
        description="What it was set to, written to be read: '2, one en-suite', "
        "'1 covered bay', 'ground only'.",
        min_length=1,
        max_length=80,
    )
    reason: str = Field(
        description="Why, in one short clause — 'standard for 3BHK'. No full stop.",
        min_length=1,
        max_length=120,
    )
    confidence: float = Field(
        description="How likely this survives contact with the user. Above 0.9 is a "
        "near-certainty like a locality naming its own city; around 0.7 is a "
        "defensible default they may well change. Never 1.0 — a value you are certain "
        "of was read, not assumed.",
        ge=0.0,
        lt=1.0,
    )


class Question(BaseModel):
    """One thing worth interrupting the user for.

    Not part of `BriefDraft` — the model does not write these. They are selected from
    the assumptions afterwards by rules the user can tune, because "which guess is
    worth a question" is a product decision that changes without the model changing.

    A question never blocks: the Brief is complete and usable before it is asked, and
    `assumed` is what stands if it goes unanswered.
    """

    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="The `Assumption.field` this would settle.")
    ask: str = Field(description="The question, in the user's vocabulary.")
    because: str = Field(
        description="Why this one and not another — the reasoning the selector used, "
        "so a user who disagrees can argue with it."
    )
    assumed: str = Field(description="What stands if they do not answer.")
    confidence: float = Field(
        description="Confidence of the assumption this replaces.", ge=0.0, lt=1.0
    )


class BriefDraft(BaseModel):
    """Exactly what the model is asked to produce.

    Split from `Brief` so `raw_text` cannot be paraphrased: the model never sees a
    field for it, and we attach the user's real words ourselves afterwards.
    """

    model_config = ConfigDict(extra="forbid")

    plot: PlotSpec
    program: ProgramHints
    locale: Locale = Field(default_factory=Locale)
    vastu: VastuStance = Field(
        default=VastuStance.MODERATE,
        description="How hard Vastu should bind. 'strict' only when the user says so "
        "explicitly; 'ignore' only when they decline it explicitly.",
    )
    constraints: list[str] = Field(
        default_factory=list,
        description="Requirements that do not fit the fields above, kept verbatim in "
        "the user's own words so nothing is lost before a later stage can use them.",
    )
    assumptions: list[Assumption] = Field(
        default_factory=list,
        description="Every value inferred rather than read, one record each. A value "
        "taken straight from the text does not belong here.",
    )


class Brief(BriefDraft):
    """A `BriefDraft` plus the untouched input that produced it."""

    raw_text: str = Field(description="The user's original words, never normalised.")

    @classmethod
    def from_draft(cls, draft: BriefDraft, raw_text: str) -> Brief:
        return cls(**draft.model_dump(), raw_text=raw_text)


class Provenance(BaseModel):
    """How this Brief came to exist.

    You cannot debug a non-deterministic pipeline without it. `prompt_sha256` is the
    load-bearing field: a prompt file edited without a version bump is otherwise an
    invisible change that silently invalidates every stored result.
    """

    model_config = ConfigDict(extra="forbid")

    stage: str = "intent"
    provider: str | None = Field(
        default=None,
        description="Which vendor served this — 'anthropic', 'openai', .... Recorded "
        "separately from the model id because two providers can serve similarly-named "
        "models, and a regression is often provider-shaped rather than model-shaped.",
    )
    model: str | None = Field(
        default=None, description="Model id, or null when the fallback produced this."
    )
    prompt_version: str | None = None
    prompt_sha256: str | None = None
    ruleset_versions: dict[str, str] = Field(
        default_factory=dict,
        description="Rule files that shaped this result, as name -> 'version@hash'. "
        "Rules are versioned data (decision 4), so a plan is only reproducible if you "
        "know which revision of them produced it.",
    )
    attempts: int = Field(
        default=0, description="Model calls made, including the ones that failed schema validation.", ge=0
    )
    fallback_used: bool = Field(
        default=False,
        description="True when the deterministic parser produced this Brief because "
        "the model was unavailable or never returned a valid one.",
    )
    fallback_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class IntentResult(BaseModel):
    """What stage ① returns: the Brief, the record of how it was obtained, and the
    handful of guesses worth asking about before anyone builds on them."""

    model_config = ConfigDict(extra="forbid")

    brief: Brief
    provenance: Provenance
    questions: list[Question] = Field(
        default_factory=list,
        description="Selected from `brief.assumptions`, highest cost-of-being-wrong "
        "first. Empty is the normal case for a well-specified brief.",
    )
