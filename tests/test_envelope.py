"""Stage ② ENVELOPE.

Deterministic end to end — no model, no network — so the invariants are exact and
hypothesis carries the numeric ones. The figures behind them are still unverified;
these tests pin the *arithmetic*, which is what a later correction to the tables must
not break.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.envelope import (
    CityUnknown,
    EnvelopeInfeasible,
    NoRulesetForCity,
    RulesetUnverified,
    build_envelope,
)
from app.envelope import geometry
from app.ir.enums import Facing
from app.ir.envelope import CARDINALS, Envelope
from app.ir.models import Brief, BriefDraft, Locale, PlotSpec, ProgramHints
from app.llm import fallback


def _brief(text: str) -> Brief:
    return fallback.parse(text)


def _envelope(text: str) -> Envelope:
    return build_envelope(_brief(text), allow_unverified=True)


class TestFrameConversion:
    """`PlotSpec` is road-relative, `Envelope` is compass-aligned. Confusing the two
    rotates every plan ninety degrees, silently."""

    def test_frontage_runs_along_the_road_not_across_it(self):
        north = geometry.plot_extent(_brief("30x40 north facing").plot)
        east = geometry.plot_extent(_brief("30x40 east facing").plot)
        assert north == pytest.approx((9.144, 12.192))
        assert east == pytest.approx((12.192, 9.144))

    @pytest.mark.parametrize(
        "diagonal,cardinal",
        [
            (Facing.NORTH_EAST, Facing.EAST),
            (Facing.SOUTH_EAST, Facing.SOUTH),
            (Facing.SOUTH_WEST, Facing.WEST),
            (Facing.NORTH_WEST, Facing.NORTH),
        ],
    )
    def test_diagonals_snap_to_an_edge_a_rectangle_actually_has(self, diagonal, cardinal):
        assert geometry.snap_to_cardinal(diagonal) is cardinal

    def test_cardinals_snap_to_themselves(self):
        assert all(geometry.snap_to_cardinal(c) is c for c in CARDINALS)


class TestSetbackAssignment:
    def test_every_road_edge_takes_the_front_margin(self):
        """The reason `road_edges` is a list. A corner plot under-set-back on its
        second road is the wrong envelope, not a cosmetic error."""
        applied = geometry.assign_setbacks(
            [Facing.NORTH, Facing.EAST], front=3.0, rear=1.5, sides=[1.0, 1.0]
        )
        assert applied[Facing.NORTH] == 3.0
        assert applied[Facing.EAST] == 3.0

    def test_the_edge_opposite_the_frontage_takes_the_rear_margin(self):
        applied = geometry.assign_setbacks(
            [Facing.NORTH], front=3.0, rear=1.5, sides=[1.0, 1.0]
        )
        assert applied[Facing.SOUTH] == 1.5
        assert applied[Facing.EAST] == applied[Facing.WEST] == 1.0

    def test_a_through_plot_has_two_fronts_and_no_rear(self):
        applied = geometry.assign_setbacks(
            [Facing.NORTH, Facing.SOUTH], front=3.0, rear=1.5, sides=[1.0, 2.0]
        )
        assert applied[Facing.NORTH] == applied[Facing.SOUTH] == 3.0
        assert 1.5 not in applied.values()

    def test_all_four_edges_always_get_a_margin(self):
        applied = geometry.assign_setbacks([Facing.NORTH], front=3, rear=1.5, sides=[1.0])
        assert set(applied) == set(CARDINALS)


class TestBands:
    def test_the_ceiling_is_inclusive(self):
        """A plot of exactly 90 m² takes the "up to 90" row. Off-by-one here moves a
        real site into the wrong margins."""
        from app.envelope import _band_for

        bands = [{"max_plot_sq_m": 90, "front": 1.5}, {"max_plot_sq_m": None, "front": 4.5}]
        assert _band_for(90.0, bands)["front"] == 1.5
        assert _band_for(90.01, bands)["front"] == 4.5

    def test_a_table_without_an_open_top_band_raises(self):
        """Falling off the end would apply the smallest margins to the largest plot."""
        from app.envelope import EnvelopeError, _band_for

        with pytest.raises(EnvelopeError, match="open top band"):
            _band_for(5000.0, [{"max_plot_sq_m": 90, "front": 1.5}])


class TestRefusals:
    """Four ways to decline, each carrying the numbers. "No envelope" is a finding
    stage ④ acts on, not a failure to paper over with a default."""

    def test_unverified_rules_are_refused_by_default(self):
        with pytest.raises(RulesetUnverified, match="unverified band"):
            build_envelope(_brief("30x40 east facing 3bhk in Bengaluru"))

    def test_no_city_means_no_bye_laws(self):
        """What the `blocking` tier on `city` exists to prevent."""
        with pytest.raises(CityUnknown):
            build_envelope(_brief("30x40 east facing 3bhk"), allow_unverified=True)

    def test_an_unmapped_city_is_not_given_a_neighbours_rules(self):
        with pytest.raises(NoRulesetForCity, match="Pune"):
            build_envelope(_brief("30x40 3bhk in Pune"), allow_unverified=True)

    def test_margins_that_consume_the_plot_say_so(self):
        """A small site under large margins genuinely has nowhere to build.

        Tested against the geometry rather than through the real ruleset, because
        RMP-2015's Table 8 cannot produce this: below 9 m it sets 1.0 m on at most
        two edges, and above 9 m it takes percentages that always leave ~76%. The
        guard is now unreachable for BBMP — and should stay, because the next
        authority's table will not be percentage-based.
        """
        from app.ir.layout import TOLERANCE_M

        plot = PlotSpec(width_m=4.0, depth_m=4.0, road_edges=[Facing.NORTH])
        greedy = geometry.assign_setbacks(
            plot.road_edges, front=2.5, rear=2.5, sides=[1.0, 1.0]
        )
        x0, y0, x1, y1 = geometry.buildable_rect(plot, greedy)
        assert y1 <= y0 + TOLERANCE_M, "5 m of margin on a 4 m depth must leave nothing"


class TestInvariants:
    PLOTS = st.builds(
        PlotSpec,
        width_m=st.floats(6.0, 60.0),
        depth_m=st.floats(6.0, 60.0),
        road_edges=st.lists(st.sampled_from(CARDINALS), min_size=1, max_size=2, unique=True),
    )

    @settings(max_examples=200, deadline=None)
    @given(plot=PLOTS)
    def test_the_buildable_rect_never_leaves_the_plot(self, plot: PlotSpec):
        """The invariant everything downstream assumes. A rect poking outside the site
        produces a plan that cannot be built and no error anyone would notice."""
        setbacks = geometry.assign_setbacks(
            plot.road_edges, front=3.0, rear=1.5, sides=[1.0, 1.0]
        )
        x0, y0, x1, y1 = geometry.buildable_rect(plot, setbacks)
        east_west, north_south = geometry.plot_extent(plot)
        assert x0 >= 0 and y0 >= 0
        assert x1 <= east_west + 1e-9 and y1 <= north_south + 1e-9

    @settings(max_examples=200, deadline=None)
    @given(plot=PLOTS, extra=st.floats(0.0, 5.0))
    def test_wider_margins_never_grow_the_rect(self, plot: PlotSpec, extra: float):
        base = geometry.assign_setbacks(plot.road_edges, front=1.0, rear=1.0, sides=[1.0, 1.0])
        wider = {edge: margin + extra for edge, margin in base.items()}
        bx0, by0, bx1, by1 = geometry.buildable_rect(plot, base)
        wx0, wy0, wx1, wy1 = geometry.buildable_rect(plot, wider)
        assert (wx1 - wx0) <= (bx1 - bx0) + 1e-9
        assert (wy1 - wy0) <= (by1 - by0) + 1e-9


class TestEnvelopeShape:
    def test_polygon_is_counter_clockwise_and_unclosed(self):
        """The IR-wide convention, so the solver's input never has to be reshaped."""
        polygon = _envelope("30x40 east facing 3bhk in Bengaluru").polygon
        assert len(polygon) == 4
        assert polygon[0] != polygon[-1]
        shoelace = sum(
            x0 * y1 - x1 * y0
            for (x0, y0), (x1, y1) in zip(polygon, polygon[1:] + polygon[:1])
        )
        assert shoelace > 0, "clockwise winding"

    def test_the_three_caps_are_not_interchangeable(self):
        """The rect bounds *where*; coverage bounds the footprint; FAR bounds the
        total across floors. A plan can sit inside the rect and breach either."""
        envelope = _envelope("30x40 east facing 3bhk in Bengaluru")
        assert envelope.max_footprint_sq_m <= envelope.plot_area_sq_m * envelope.max_coverage
        assert envelope.max_footprint_sq_m <= envelope.area_sq_m
        assert envelope.max_built_area_sq_m == pytest.approx(
            envelope.plot_area_sq_m * envelope.max_far
        )

    def test_provenance_names_the_rule_revision(self):
        """A plan is only reproducible if you know which revision produced it."""
        envelope = _envelope("30x40 east facing 3bhk in Bengaluru")
        assert envelope.authority == "BBMP"
        assert envelope.ruleset.startswith("setbacks_v1@")

    def test_a_corner_plot_loses_area_to_its_second_road(self):
        """The end-to-end consequence of the road_edges work: same site, one more
        road, materially smaller envelope."""
        ordinary = _envelope("30x40 north facing 3bhk in Bengaluru")
        corner = _envelope("30x40 north facing corner plot 3bhk in Bengaluru")
        assert corner.area_sq_m < ordinary.area_sq_m


class TestRoadWidth:
    """Road width is two constraints at once, and neither is cosmetic: it demotes the
    FAR band and it caps the storeys independently of the FAR earned."""

    def test_an_under_width_road_demotes_the_far(self):
        """RMP 2031 §5.2(iii). A 500 m² plot on a narrow road earns the FAR of a much
        smaller one — the single reason this field had to exist before ③."""
        wide = _envelope("60x90 site on a 60 feet road in Bengaluru 4bhk")
        narrow = _envelope("60x90 site on a 20 feet road in Bengaluru 4bhk")
        assert wide.plot_area_sq_m == pytest.approx(narrow.plot_area_sq_m)
        assert narrow.max_far < wide.max_far

    def test_demotion_leaves_coverage_alone(self):
        """Only the FAR moves; ground coverage stays with the plot-size row."""
        wide = _envelope("60x90 site on a 60 feet road in Bengaluru 4bhk")
        narrow = _envelope("60x90 site on a 20 feet road in Bengaluru 4bhk")
        assert narrow.max_coverage == wide.max_coverage

    def test_a_wider_road_than_the_band_needs_changes_nothing(self):
        """§5.2(iii)'s second half: over-width earns the band's own FAR, not more."""
        at_band = _envelope("30x40 site on a 30 feet road in Bengaluru 3bhk")
        over = _envelope("30x40 site on a 60 feet road in Bengaluru 3bhk")
        assert over.max_far == at_band.max_far

    def test_a_narrow_road_caps_the_storeys(self):
        """RMP 2031 §5.2(iv): below 9.5 m it is GF+1 whatever the FAR allows."""
        # RMP-2015 Table 10 note (b): under 9 m of road is Stilt+GF+2, i.e. 3.
        assert _envelope("30x40 3bhk on a 20 feet road in Bengaluru").max_floors == 3
        assert _envelope("60x90 4bhk on a 60 feet road in Bengaluru").max_floors > 2

    def test_the_default_is_recorded_not_silent(self):
        """9 m sits just under the 9.5 m threshold, so defaulting it costs a storey.
        That has to be visible."""
        brief = fallback.parse("30x40 east facing 3bhk in Whitefield")
        assert brief.plot.road_width_m == 9.0
        assert any(a.field == "road width" for a in brief.assumptions)

    def test_a_stated_road_width_is_not_an_assumption(self):
        brief = fallback.parse("30x40 3bhk on a 40 feet road in Bengaluru")
        assert brief.plot.road_width_m == pytest.approx(12.192, abs=1e-3)
        assert not any(a.field == "road width" for a in brief.assumptions)

    def test_far_budgets_against_base_not_total(self):
        """`total_far` needs TDR bought. Budgeting against it would size a programme
        that cannot legally be built."""
        from app.rules import load_ruleset

        bands = load_ruleset("setbacks_v1").data["authorities"]["BBMP"]["coverage_far_bands"]
        # RMP-2015 Table 10 states one FAR per band with no TDR component, so base
        # and total coincide. The distinction still has to hold: naksha budgets
        # against `base_far`, and a future table that separates them must not
        # silently start spending the TDR half.
        envelope = _envelope("60x90 site on a 60 feet road in Bengaluru 4bhk")
        assert envelope.max_far in {b["base_far"] for b in bands}
        assert all(b["base_far"] <= b["total_far"] for b in bands)
