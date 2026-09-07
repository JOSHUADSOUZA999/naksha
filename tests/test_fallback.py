"""The deterministic parser — the guarantee that a dead API is not a dead product.

No network, no key, no mocks. If this file needs either, the fallback is not a
fallback.
"""

from __future__ import annotations

import pytest

from app.ir.enums import Facing, RoomKind, VastuStance
from app.llm import fallback


class TestDimensions:
    @pytest.mark.parametrize(
        "text,width,depth",
        [
            ("30x40 site", 9.144, 12.192),
            ("30 x 40", 9.144, 12.192),
            ("30by40 plot", 9.144, 12.192),
            ("30 by 40", 9.144, 12.192),
            ("30*40", 9.144, 12.192),
            ("site 30/40", 9.144, 12.192),
            ("40'x60'", 12.192, 18.288),
            ("40 x 60 ft", 12.192, 18.288),
            ("12m x 18m", 12.0, 18.0),
            ("12 mtr x 15 mtr", 12.0, 15.0),
        ],
    )
    def test_dimension_phrasings(self, text: str, width: float, depth: float):
        plot = fallback.parse(text).plot
        assert plot.width_m == pytest.approx(width, abs=1e-3)
        assert plot.depth_m == pytest.approx(depth, abs=1e-3)

    def test_first_number_is_frontage(self):
        """'30x40' is 30 ft of road frontage, 40 ft deep — not the other way round."""
        plot = fallback.parse("30x40 site").plot
        assert plot.width_m < plot.depth_m

    def test_frontage_reading_is_recorded_separately_from_the_unit(self):
        """Two different guesses. Swapping the pair rotates the plot, moves the front
        setback onto the long side and re-scores every Vastu sector, so it gets its
        own row rather than hiding inside the unit conversion."""
        rows = {a.field: a for a in fallback.parse("30x40 east facing").assumptions}
        assert "30 ft frontage" in rows["frontage"].value
        assert "40 ft deep" in rows["frontage"].value
        assert rows["plot size"].field != rows["frontage"].field

    @pytest.mark.parametrize(
        "text,area_sq_m",
        [
            ("1200 sqft plot", 111.484),
            ("1200 sq ft", 111.484),
            ("1200 square feet", 111.484),
            ("200 gaj", 167.225),
            ("4 cents", 161.874),
            ("150 sqm site", 150.0),
        ],
    )
    def test_area_only_derives_sides(self, text: str, area_sq_m: float):
        plot = fallback.parse(text).plot
        assert plot.area_sq_m == pytest.approx(area_sq_m, rel=1e-3)
        # 2:3 frontage-to-depth, the typical Indian residential proportion.
        assert plot.width_m / plot.depth_m == pytest.approx(2 / 3, rel=1e-6)

    def test_absurd_dimensions_are_discarded_not_clamped(self):
        """A reading that implies a 1.2 km plot is a misparse, not a big site.

        Silently clamping it to the bound would hand the solver a fabricated number
        with no trace; discarding it and saying so keeps the guess visible.
        """
        brief = fallback.parse("1200 x 1500 metres estate")
        assert brief.plot.width_m == pytest.approx(9.144, abs=1e-3)
        assert any("ignored" in a.value for a in brief.assumptions)

    def test_missing_size_falls_back_loudly(self):
        brief = fallback.parse("I want a 3 bedroom house")
        assert brief.plot.width_m == pytest.approx(9.144, abs=1e-3)
        assert any(
            a.field == "plot size" and "nothing in the text" in a.reason
            for a in brief.assumptions
        )


class TestFacing:
    @pytest.mark.parametrize(
        "text,facing",
        [
            ("east facing", Facing.EAST),
            ("facing east", Facing.EAST),
            ("north facing plot", Facing.NORTH),
            ("north east facing", Facing.NORTH_EAST),
            ("north-east facing", Facing.NORTH_EAST),
            ("south west facing site", Facing.SOUTH_WEST),
            ("west facing", Facing.WEST),
        ],
    )
    def test_facing_phrasings(self, text: str, facing: Facing):
        assert fallback.parse(f"30x40 {text}").plot.facing is facing

    def test_compound_beats_cardinal(self):
        """'north east' must not be read as 'north' by an earlier pattern."""
        assert fallback.parse("30x40 north east facing").plot.facing is Facing.NORTH_EAST

    def test_stated_facing_still_records_which_edge_it_means(self):
        """"East facing" is ambiguous in Indian usage — usually the road is on the
        east, but some people mean the main door. Those choose different frontages,
        which moves the setback and reshapes the envelope, so the reading is stated
        even though the compass point came straight from the text."""
        rows = {a.field: a for a in fallback.parse("30x40 east facing site").assumptions}
        assert "road" in rows["facing"].value and "east" in rows["facing"].value
        assert "door" in rows["facing"].reason
        assert rows["facing"].confidence == pytest.approx(0.85)

    def test_missing_facing_is_flagged(self):
        brief = fallback.parse("30x40 plot, 3bhk")
        assert brief.plot.facing is Facing.NORTH
        assert any(
            a.field == "facing" and "not stated" in a.reason for a in brief.assumptions
        )


class TestProgram:
    @pytest.mark.parametrize(
        "text,bedrooms",
        [
            ("3BHK", 3), ("3 BHK", 3), ("3bhk", 3), ("2 b h k", 2),
            ("4 bedroom", 4), ("two bedroom", 2), ("three bed rooms", 3),
        ],
    )
    def test_bedroom_phrasings(self, text: str, bedrooms: int):
        assert fallback.parse(f"30x40 site {text}").program.bedrooms == bedrooms

    def test_bathrooms_default_to_the_conventional_count_and_say_so(self):
        """A null here just moves the same guess into a stage the user cannot see."""
        brief = fallback.parse("30x40 3bhk")
        assert brief.program.bathrooms == 2
        assert any(a.field == "bathrooms" for a in brief.assumptions)

    def test_stated_bathrooms_win_and_are_not_an_assumption(self):
        brief = fallback.parse("30x40 3bhk 2 bathrooms")
        assert brief.program.bathrooms == 2
        assert not any(a.field == "bathrooms" for a in brief.assumptions)

    @pytest.mark.parametrize(
        "text,kind",
        [
            ("pooja room", RoomKind.POOJA),
            ("puja room", RoomKind.POOJA),
            ("prayer room", RoomKind.POOJA),
            ("study", RoomKind.STUDY),
            ("car parking", RoomKind.CAR_PARKING),
            ("car porch", RoomKind.CAR_PARKING),
            ("garage", RoomKind.CAR_PARKING),
            ("store room", RoomKind.STORE),
            ("utility", RoomKind.UTILITY),
            ("guest room", RoomKind.GUEST_ROOM),
            ("servant room", RoomKind.SERVANT_ROOM),
            ("balcony", RoomKind.BALCONY),
            ("sit out", RoomKind.VERANDA),
            ("home office", RoomKind.OFFICE),
        ],
    )
    def test_extra_rooms(self, text: str, kind: RoomKind):
        assert kind in fallback.parse(f"30x40 3bhk with {text}").program.extra_rooms

    def test_car_porch_is_parking_only(self):
        """'car porch' is one room, not parking plus a veranda.

        Two patterns claiming the same phrase double-counts its area in stage ③.
        """
        rooms = fallback.parse("30x40 3bhk with car porch").program.extra_rooms
        assert rooms == [RoomKind.CAR_PARKING]

    def test_hall_and_kitchen_are_not_extra_rooms(self):
        """They are implied by BHK and added in stage ③. Listing them double-counts."""
        rooms = fallback.parse("30x40 3bhk hall kitchen").program.extra_rooms
        assert rooms == []

    @pytest.mark.parametrize(
        "text,floors", [("30x40 3bhk", 1), ("30x40 g+1", 2), ("30x40 two floors", 2)]
    )
    def test_floors(self, text: str, floors: int):
        assert fallback.parse(text).program.floors == floors


class TestVastuAndLocale:
    @pytest.mark.parametrize(
        "text,stance",
        [
            ("30x40 3bhk", VastuStance.MODERATE),
            ("30x40 3bhk as per vastu", VastuStance.MODERATE),
            ("30x40 3bhk strict vastu", VastuStance.STRICT),
            ("30x40 3bhk vastu compliant mandatory", VastuStance.STRICT),
            ("30x40 3bhk no vastu", VastuStance.IGNORE),
            ("30x40 3bhk ignore vaastu", VastuStance.IGNORE),
            # How people actually insist, without ever typing "strict". Reading these
            # as a soft preference is the system overriding the person.
            ("30x40 3bhk, Vastu is very important", VastuStance.STRICT),
            ("30x40 3bhk, vastu is important", VastuStance.STRICT),
            ("30x40 3bhk, vaasthu is critical", VastuStance.STRICT),
            ("30x40 3bhk, vastu non-negotiable", VastuStance.STRICT),
            # And the negations, which contain the same words.
            ("30x40 3bhk, vastu is not important", VastuStance.IGNORE),
            ("30x40 3bhk, vastu isn't very important", VastuStance.IGNORE),
            ("30x40 3bhk, don't care about vastu", VastuStance.IGNORE),
        ],
    )
    def test_vastu_stance(self, text: str, stance: VastuStance):
        assert fallback.parse(text).vastu is stance

    @pytest.mark.parametrize("spelling", ["vastu", "vaastu", "vasthu", "vaasthu"])
    def test_all_common_spellings(self, spelling: str):
        """All four transliterations appear in real listings.

        Missing one silently downgrades a strict-vastu brief to moderate, which is
        the system overriding the person.
        """
        assert fallback.parse(f"30x40 3bhk strict {spelling}").vastu is VastuStance.STRICT
        assert fallback.parse(f"30x40 3bhk no {spelling}").vastu is VastuStance.IGNORE

    @pytest.mark.parametrize(
        "text,city",
        [
            ("30x40 in Bengaluru", "Bengaluru"),
            ("30x40 in bangalore", "Bangalore"),
            ("plot in New Delhi", "New Delhi"),
            ("plot in Navi Mumbai", "Navi Mumbai"),
        ],
    )
    def test_city(self, text: str, city: str):
        assert fallback.parse(text).locale.city == city

    def test_longer_city_name_wins(self):
        """'New Delhi' must not be swallowed by the 'Delhi' pattern."""
        assert fallback.parse("site in New Delhi").locale.city == "New Delhi"

    def test_locality_names_its_own_city(self):
        """Stage \u2461 cannot pick a setback ruleset without a city, and "Whitefield"
        supplies one as surely as the word "Bengaluru" does."""
        brief = fallback.parse("30x40 east facing site in Whitefield, 3BHK")
        assert brief.locale.city == "Bengaluru"
        assert brief.locale.locality == "Whitefield"
        assert any(a.field == "city" and a.confidence >= 0.9 for a in brief.assumptions)

    def test_a_stated_city_beats_the_locality_table(self):
        """No assumption either — a city that was read is not a city that was guessed."""
        brief = fallback.parse("30x40 site in Whitefield, Bengaluru")
        assert (brief.locale.city, brief.locale.locality) == ("Bengaluru", "Whitefield")
        assert not any(a.field == "city" for a in brief.assumptions)

    def test_unrecognised_locality_leaves_the_city_null(self):
        """A wrong city selects the wrong bye-laws, which is worse than no ruleset."""
        assert fallback.parse("30x40 site in Kuppam Extension").locale.city is None

    def test_unknown_city_is_null_and_flagged(self):
        brief = fallback.parse("30x40 site in Kuppam")
        assert brief.locale.city is None
        assert any(a.field == "city" for a in brief.assumptions)


class TestParking:
    def test_a_bay_is_assumed_when_nobody_mentions_parking(self):
        brief = fallback.parse("30x40 3bhk in Pune")
        assert (brief.program.parking_bays, brief.program.covered_parking) == (1, True)
        assert any(a.field == "parking" for a in brief.assumptions)

    def test_declining_parking_is_not_a_request_for_parking(self):
        """"no parking" matches the car-parking room pattern on the word it declines."""
        brief = fallback.parse("30x40 3bhk in Pune, no parking")
        assert brief.program.parking_bays == 0
        assert RoomKind.CAR_PARKING not in brief.program.extra_rooms
        assert not any(a.field == "parking" for a in brief.assumptions)

    def test_a_named_car_porch_still_lists_the_room(self):
        brief = fallback.parse("30x40 3bhk in Pune with car porch")
        assert brief.program.parking_bays == 1
        assert RoomKind.CAR_PARKING in brief.program.extra_rooms


class TestContract:
    def test_raw_text_is_untouched(self):
        raw = "  30X40 East Facing!!  3BHK  "
        assert fallback.parse(raw).raw_text == raw

    def test_always_marks_itself_as_the_offline_parser(self):
        assert any(
            a.field == "parser" and a.value == "offline"
            for a in fallback.parse("30x40").assumptions
        )

    def test_corner_plot(self):
        assert fallback.parse("30x40 corner plot").plot.corner_plot is True
        assert fallback.parse("30x40 plot").plot.corner_plot is False

    def test_a_corner_names_its_second_road_and_admits_it_guessed(self):
        """"Corner plot" says a second road exists without saying which side, and the
        two readings put the extra setback on opposite edges. Dropping the fact would
        silently produce an ordinary-plot envelope, so it is named at low confidence
        and the clarifier asks."""
        brief = fallback.parse("30x40 north facing corner plot")
        assert [e.value for e in brief.plot.road_edges] == ["north", "east"]
        row = next(a for a in brief.assumptions if a.field == "road edges")
        assert row.confidence <= 0.3

    def test_road_width(self):
        assert fallback.parse("30x40 on 30 feet road").plot.road_width_m == pytest.approx(
            9.144, abs=1e-3
        )

    @pytest.mark.parametrize(
        "text",
        ["", "   ", "hello", "??!!", "0x0", "999999999 sqft", "a" * 5000, "🏠 30x40 east"],
    )
    def test_never_raises_on_junk(self, text: str):
        """A degraded brief beats a stack trace — that is the whole point."""
        brief = fallback.parse(text)
        assert brief.plot.width_m > 0
        assert brief.program.bedrooms >= 1


class TestGolden:
    def test_golden_cases(self, golden_cases):
        for case in golden_cases:
            brief = fallback.parse(case["text"])
            want = case["expected"]
            ctx = case["id"]

            plot = want["plot"]
            assert brief.plot.width_m == pytest.approx(plot["width_m"], abs=1e-2), ctx
            assert brief.plot.depth_m == pytest.approx(plot["depth_m"], abs=1e-2), ctx
            assert brief.plot.facing.value == plot["facing"], ctx
            assert brief.plot.corner_plot == plot["corner_plot"], ctx
            if "road_width_m" in plot:
                assert brief.plot.road_width_m == pytest.approx(
                    plot["road_width_m"], abs=1e-2
                ), ctx

            program = want["program"]
            assert brief.program.bedrooms == program["bedrooms"], ctx
            if "bathrooms" in program:
                assert brief.program.bathrooms == program["bathrooms"], ctx
            if "floors" in program:
                assert brief.program.floors == program["floors"], ctx
            assert sorted(r.value for r in brief.program.extra_rooms) == sorted(
                program["extra_rooms"]
            ), ctx

            assert brief.locale.city == want["locale"]["city"], ctx
            if "locality" in want["locale"]:
                assert brief.locale.locality == want["locale"]["locality"], ctx
            assert brief.vastu.value == want["vastu"], ctx

    def test_must_assume_cases_record_their_guesses(self, golden_cases):
        for case in golden_cases:
            for topic in case.get("must_assume", []):
                brief = fallback.parse(case["text"])
                blob = " ".join(
                    f"{a.field} {a.value} {a.reason}" for a in brief.assumptions
                ).lower()
                assert topic.split()[0] in blob, f"{case['id']}: no assumption for {topic}"


class TestCompoundCorners:
    """"North-east corner plot" names two roads, not a diagonal frontage.

    Reading it as a NE-pointing frontage and stepping 90° round produced road edges
    of north_east + south_east — a side the user never mentioned, and the wrong two
    edges to widen the setback on.
    """

    @pytest.mark.parametrize(
        "text,edges",
        [
            ("30x40 north east corner site", ["north", "east"]),
            ("30x40 north-west corner plot", ["north", "west"]),
            ("30x40 south east corner", ["south", "east"]),
            ("30x40 SW corner plot", ["south", "west"]),
        ],
    )
    def test_a_compound_corner_names_both_cardinals(self, text, edges):
        assert [e.value for e in fallback.parse(text).plot.road_edges] == edges

    def test_it_is_stated_not_guessed(self):
        """Both edges came from the text, so this is a reading rather than a guess and
        does not get the low-confidence `road edges` row a bare "corner" earns."""
        brief = fallback.parse("30x40 north east corner site")
        assert not any(a.field == "road edges" for a in brief.assumptions)
        assert next(a for a in brief.assumptions if a.field == "facing").confidence == 0.8

    def test_a_compound_facing_without_a_corner_stays_diagonal(self):
        """Only "corner" turns north-east into two sides. On an ordinary plot it is
        one edge pointing north-east, which is what Vastu scoring expects."""
        plot = fallback.parse("30x40 north east facing 3bhk").plot
        assert [e.value for e in plot.road_edges] == ["north_east"]
        assert plot.corner_plot is False
