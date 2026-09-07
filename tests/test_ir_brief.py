"""The IR contract itself. If these loosen, everything downstream inherits it."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ir.enums import Facing, RoomKind, VastuStance
from app.ir.models import (
    Assumption,
    Brief,
    BriefDraft,
    Locale,
    PlotSpec,
    ProgramHints,
    Provenance,
)


def _plot(**overrides) -> PlotSpec:
    return PlotSpec(
        **{"width_m": 9.144, "depth_m": 12.192, "road_edges": [Facing.EAST], **overrides}
    )


def _draft(**overrides) -> BriefDraft:
    base = {"plot": _plot(), "program": ProgramHints(bedrooms=3)}
    return BriefDraft(**{**base, **overrides})


class TestExtraForbid:
    """Unknown keys are bugs — either the model invented a field or a contract moved."""

    @pytest.mark.parametrize(
        "model,payload",
        [
            (PlotSpec, {"width_m": 9.0, "depth_m": 12.0, "facing": "east", "area": 108}),
            (ProgramHints, {"bedrooms": 3, "halls": 1}),
            (Locale, {"city": "Pune", "pincode": "411001"}),
            (Provenance, {"temperature": 0.7}),
        ],
    )
    def test_unknown_key_rejected(self, model, payload):
        with pytest.raises(ValidationError, match="[Ee]xtra"):
            model(**payload)

    def test_brief_rejects_unknown_key(self):
        with pytest.raises(ValidationError, match="[Ee]xtra"):
            Brief(
                plot=_plot(),
                program=ProgramHints(bedrooms=3),
                raw_text="x",
                budget_lakhs=40,
            )


class TestPlotSpec:
    def test_area_is_derived_not_stored(self):
        assert _plot().area_sq_m == pytest.approx(111.48, abs=0.01)

    @pytest.mark.parametrize("side", [0, -5, 1.5, 200.0, 1200.0])
    def test_implausible_sides_rejected(self, side: float):
        """1200 is the case that matters: square feet misread as a side length.

        Catching it here is what turns a silently absurd plan into a retry with
        specific feedback.
        """
        with pytest.raises(ValidationError):
            _plot(width_m=side)

    def test_road_width_must_be_positive(self):
        with pytest.raises(ValidationError):
            _plot(road_width_m=0)

    def test_road_width_optional(self):
        assert _plot().road_width_m is None

    def test_facing_accepts_the_enum_string(self):
        assert _plot(road_edges=["north_east"]).facing is Facing.NORTH_EAST

    def test_facing_rejects_freeform(self):
        with pytest.raises(ValidationError):
            _plot(road_edges=["northeast-ish"])

    def test_facing_degrees_are_clockwise_from_north(self):
        assert Facing.NORTH.degrees == 0.0
        assert Facing.EAST.degrees == 90.0
        assert Facing.SOUTH.degrees == 180.0
        assert Facing.WEST.degrees == 270.0


class TestProgramHints:
    def test_extra_rooms_deduped_preserving_order(self):
        program = ProgramHints(
            bedrooms=3,
            extra_rooms=[RoomKind.POOJA, RoomKind.STUDY, RoomKind.POOJA],
        )
        assert program.extra_rooms == [RoomKind.POOJA, RoomKind.STUDY]

    def test_defaults(self):
        program = ProgramHints(bedrooms=2)
        assert program.bathrooms is None
        assert program.extra_rooms == []
        assert program.floors == 1

    @pytest.mark.parametrize("bedrooms", [0, -1, 9])
    def test_bedroom_bounds(self, bedrooms: int):
        with pytest.raises(ValidationError):
            ProgramHints(bedrooms=bedrooms)

    def test_multi_floor_is_captured_not_flattened(self):
        """v1 only builds ground floor, but the brief records what was asked.

        Feasibility rejects it downstream with a reason. Silently rewriting floors to
        1 here would be the system overriding the person.
        """
        assert ProgramHints(bedrooms=4, floors=2).floors == 2


class TestBrief:
    def test_defaults(self):
        draft = _draft()
        assert draft.vastu is VastuStance.MODERATE
        assert draft.constraints == []
        assert draft.assumptions == []
        assert draft.locale.city is None

    def test_from_draft_preserves_raw_text_verbatim(self):
        raw = "  30x40 EAST facing!!  extra   spaces  "
        brief = Brief.from_draft(_draft(), raw_text=raw)
        assert brief.raw_text == raw

    def test_draft_has_no_raw_text_field(self):
        """The model must not be able to paraphrase the user's own words.

        `raw_text` is attached by us after parsing, which is only safe as long as the
        schema handed to the model genuinely lacks the field.
        """
        assert "raw_text" not in BriefDraft.model_fields
        assert "raw_text" not in BriefDraft.model_json_schema()["properties"]

    def test_round_trips_through_json(self):
        brief = Brief.from_draft(
            _draft(
                constraints=["budget 40 lakhs"],
                assumptions=[
                    Assumption(
                        field="plot size",
                        value="9.14 x 12.19 m",
                        reason="'30x40' is feet by Indian convention",
                        confidence=0.9,
                    )
                ],
            ),
            raw_text="30x40 east facing",
        )
        assert Brief.model_validate_json(brief.model_dump_json()) == brief


class TestProvenance:
    def test_records_what_debugging_needs(self):
        prov = Provenance(
            model="claude-opus-5",
            prompt_version="intent_v1",
            prompt_sha256="a" * 64,
            attempts=2,
        )
        assert prov.stage == "intent"
        assert prov.fallback_used is False
        assert prov.created_at.tzinfo is not None

    def test_fallback_has_no_model(self):
        prov = Provenance(fallback_used=True, fallback_reason="api: APIConnectionError")
        assert prov.model is None
        assert prov.attempts == 0


class TestSchemaForStructuredOutputs:
    """Guards on the JSON schema actually sent to the API.

    Structured outputs require `additionalProperties: false` on every object and do
    not support recursive schemas. Breaking either is a 400 at request time rather
    than an import-time error, so it is worth asserting here.
    """

    def test_every_object_forbids_additional_properties(self):
        schema = BriefDraft.model_json_schema()
        objects = [schema, *schema.get("$defs", {}).values()]
        for obj in objects:
            if obj.get("type") == "object":
                assert obj.get("additionalProperties") is False, obj.get("title")

    def test_schema_is_not_recursive(self):
        schema = BriefDraft.model_json_schema()
        assert "BriefDraft" not in set(schema.get("$defs", {}))


class TestRoadEdges:
    """A corner plot has two roads, and both take the larger road-side setback.

    Collapsing them to one direction produced a wrong envelope rather than a wrong
    label, which is why `road_edges` is the stored field and `facing` is derived.
    """

    def test_facing_is_the_primary_frontage(self):
        assert _plot(road_edges=["north", "east"]).facing is Facing.NORTH

    def test_two_adjacent_roads_are_a_corner(self):
        assert _plot(road_edges=["north", "east"]).corner_plot is True

    def test_two_opposite_roads_are_a_through_plot_not_a_corner(self):
        """Different thing: the larger setback lands on both ends rather than
        opening a second entrance onto a corner."""
        assert _plot(road_edges=["north", "south"]).corner_plot is False
        assert _plot(road_edges=["east", "west"]).corner_plot is False

    def test_one_road_is_never_a_corner(self):
        assert _plot(road_edges=["east"]).corner_plot is False

    def test_the_same_side_cannot_carry_two_roads(self):
        with pytest.raises(ValidationError):
            _plot(road_edges=["east", "east"])

    def test_at_least_one_road_is_required(self):
        with pytest.raises(ValidationError):
            _plot(road_edges=[])

    def test_three_roads_are_out_of_scope_for_v1(self):
        with pytest.raises(ValidationError):
            _plot(road_edges=["north", "east", "south"])


class TestDerivedFieldsSurviveJson:
    """`facing` and `corner_plot` are serialised so consumers and `jq` still see them,
    but they are computed — they cannot drift from `road_edges`."""

    def test_they_are_in_the_json(self):
        dumped = _plot(road_edges=["north", "east"]).model_dump(mode="json")
        assert dumped["facing"] == "north"
        assert dumped["corner_plot"] is True

    def test_the_model_is_never_asked_to_produce_them(self):
        """They must not appear in the schema handed to the LLM, or it will try to
        fill them and they will disagree with `road_edges`."""
        properties = BriefDraft.model_json_schema()["$defs"]["PlotSpec"]["properties"]
        assert "facing" not in properties
        assert "corner_plot" not in properties
        assert "road_edges" in properties

    def test_a_dumped_plot_reloads(self):
        plot = _plot(road_edges=["north", "east"])
        assert PlotSpec.model_validate(plot.model_dump(mode="json")) == plot

    def test_a_stale_facing_is_rejected_rather_than_ignored(self):
        """Silently dropping it would let a corrected `road_edges` travel with an
        uncorrected `facing`, which is the drift the split was meant to prevent."""
        dumped = _plot(road_edges=["north", "east"]).model_dump(mode="json")
        with pytest.raises(ValidationError, match="disagrees with road_edges"):
            PlotSpec.model_validate({**dumped, "facing": "south"})
