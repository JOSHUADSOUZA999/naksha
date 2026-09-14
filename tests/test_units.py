"""Unit conversion is where a silent bug does the most damage.

A metre/foot mixup does not crash — it produces a plan that is 3.3x too big and looks
entirely reasonable until someone tries to build it. Property tests, per CLAUDE.md,
because example-based tests miss exactly this class of drift.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.ir.units import (
    AreaUnit,
    LengthUnit,
    area_from_sq_m,
    area_to_sq_m,
    from_metres,
    parse_area_unit,
    parse_length_unit,
    to_metres,
)

finite_positive = st.floats(
    min_value=1e-3, max_value=1e6, allow_nan=False, allow_infinity=False
)


def test_known_conversions_are_exact():
    # The 1959 international agreement fixes these exactly; a "close enough" value
    # here would drift into every dimension chain downstream.
    assert to_metres(30, LengthUnit.FOOT) == pytest.approx(9.144, abs=1e-9)
    assert to_metres(40, LengthUnit.FOOT) == pytest.approx(12.192, abs=1e-9)
    assert to_metres(1, LengthUnit.YARD) == pytest.approx(0.9144, abs=1e-9)
    assert to_metres(15, LengthUnit.METRE) == 15.0


def test_known_area_conversions():
    assert area_to_sq_m(1200, AreaUnit.SQ_FOOT) == pytest.approx(111.484, abs=1e-3)
    assert area_to_sq_m(200, AreaUnit.SQ_YARD) == pytest.approx(167.225, abs=1e-3)
    assert area_to_sq_m(4, AreaUnit.CENT) == pytest.approx(161.874, abs=1e-3)


@given(value=finite_positive, unit=st.sampled_from(list(LengthUnit)))
def test_length_round_trip(value: float, unit: LengthUnit):
    assert from_metres(to_metres(value, unit), unit) == pytest.approx(value, rel=1e-12)


@given(value=finite_positive, unit=st.sampled_from(list(AreaUnit)))
def test_area_round_trip(value: float, unit: AreaUnit):
    assert area_from_sq_m(area_to_sq_m(value, unit), unit) == pytest.approx(
        value, rel=1e-12
    )


@given(value=finite_positive)
def test_length_conversion_is_monotonic_and_scale_free(value: float):
    """Doubling the input doubles the output — catches an accidental offset."""
    assert to_metres(2 * value, LengthUnit.FOOT) == pytest.approx(
        2 * to_metres(value, LengthUnit.FOOT), rel=1e-12
    )


@given(side=st.floats(min_value=1.0, max_value=500.0, allow_nan=False))
def test_area_and_length_units_agree(side: float):
    """A square measured in feet and in sq ft must land on the same square metres.

    This is the invariant that actually protects us: `units.py` has two independent
    conversion tables, and nothing else would notice if they disagreed.
    """
    via_length = to_metres(side, LengthUnit.FOOT) ** 2
    via_area = area_to_sq_m(side**2, AreaUnit.SQ_FOOT)
    assert via_length == pytest.approx(via_area, rel=1e-9)


@pytest.mark.parametrize(
    "token,expected",
    [
        ("ft", LengthUnit.FOOT),
        ("Feet", LengthUnit.FOOT),
        ("'", LengthUnit.FOOT),
        ("mtr", LengthUnit.METRE),
        ("METRES", LengthUnit.METRE),
        ("yd", LengthUnit.YARD),
    ],
)
def test_length_aliases(token: str, expected: LengthUnit):
    assert parse_length_unit(token) is expected


@pytest.mark.parametrize(
    "token,expected",
    [
        ("sqft", AreaUnit.SQ_FOOT),
        ("Sq Ft", AreaUnit.SQ_FOOT),
        ("square feet", AreaUnit.SQ_FOOT),
        ("gaj", AreaUnit.SQ_YARD),
        ("cents", AreaUnit.CENT),
    ],
)
def test_area_aliases(token: str, expected: AreaUnit):
    assert parse_area_unit(token) is expected


def test_gaj_is_area_not_length():
    """'gaj' is a real ambiguity: square yards as an area, yards as a side.

    Both tables claim the token, which is fine as long as callers check the area
    table first. This test exists so nobody "fixes" the duplication by deleting one.
    """
    assert parse_area_unit("gaj") is AreaUnit.SQ_YARD
    assert parse_length_unit("gaj") is LengthUnit.YARD


def test_unknown_unit_raises():
    with pytest.raises(ValueError, match="unknown length unit"):
        to_metres(1, "furlong")
    with pytest.raises(ValueError, match="unknown area unit"):
        area_to_sq_m(1, "acre")


def test_no_silent_rounding():
    """30 ft is 9.144 m, not 9.1 or 9.

    A "nicer" number here compounds through setbacks, wall centrelines and carpet
    area until the exported DXF is visibly wrong.
    """
    assert not math.isclose(to_metres(30, LengthUnit.FOOT), 9.1, abs_tol=1e-6)
    assert to_metres(30, LengthUnit.FOOT) == pytest.approx(9.144, abs=1e-12)


class TestFeetForPeople:
    """The IR stays metric; what a plot owner reads is in feet.

    A plan labelled 32.0 m² asks a Bengaluru owner to do arithmetic before they can
    picture the room. The figures here are the ones they would say aloud."""

    def test_the_numbers_people_actually_say(self):
        from app.ir.units import area_text, feet_and_inches, length_text, square_feet

        assert feet_and_inches(to_metres(40, LengthUnit.FOOT)) == "40'0\""
        assert feet_and_inches(3.76) == "12'4\""
        assert square_feet(area_to_sq_m(1200, AreaUnit.SQ_FOOT)) == "1,200 sq ft"
        assert area_text(32.0) == "344 sq ft (32.0 m²)"
        assert length_text(1.2) == "3'11\" (1.20 m)"

    def test_twelve_inches_carry_into_a_foot(self):
        """11.6 inches rounds to 12, which is 1'0" — never 0'12"."""
        from app.ir.units import feet_and_inches

        assert feet_and_inches(to_metres(11.6 / 12, LengthUnit.FOOT)) == "1'0\""

    @given(value=st.floats(min_value=0, max_value=1e4, allow_nan=False, allow_infinity=False))
    def test_feet_and_inches_is_within_half_an_inch(self, value: float):
        from app.ir.units import feet_and_inches

        feet, inches = feet_and_inches(value).rstrip('"').split("'")
        assert 0 <= int(inches) < 12
        total = int(feet) + int(inches) / 12
        assert abs(to_metres(total, LengthUnit.FOOT) - value) <= 0.0127 + 1e-9

    @given(value=st.floats(min_value=0, max_value=1e5, allow_nan=False, allow_infinity=False))
    def test_square_feet_is_the_same_conversion_the_parser_uses(self, value: float):
        """One constant for both directions, or the display drifts from what was read."""
        from app.ir.units import square_feet

        shown = float(square_feet(value).removesuffix(" sq ft").replace(",", ""))
        assert abs(shown - area_from_sq_m(value, AreaUnit.SQ_FOOT)) <= 0.5 + 1e-9
