"""Stage ④ FEASIBILITY — explain, and offer options that were actually measured.

"Infeasible" is useless to a plot owner. The tests here are mostly about the
*distinctions* the stage draws, because each one is a different thing to tell a user:
fits, fits-but-only-if-lucky, and no arrangement exists.
"""

from __future__ import annotations

import pytest

from app.envelope import build_envelope
from app.feasibility import assess
from app.ir.enums import SpaceKind
from app.llm import fallback
from app.program import expand


def _case(text: str):
    brief = fallback.parse(text)
    envelope = build_envelope(brief, allow_unverified=True)
    return expand(brief, envelope), envelope


class TestVerdicts:
    def test_a_roomy_plot_simply_fits(self):
        program, envelope = _case("50x80 4bhk in Bengaluru with study and store room")
        assert assess(program, envelope, floor=1).feasible

    def test_a_floor_with_no_legal_arrangement_says_so(self):
        """A 30x40 3BHK is 98% packed once minimums are measured inside the walls, and
        none of the arrangements stage ⑤ tries survives. Reporting "fits" would be
        false, and reporting a percentage would dress a zero up as a chance."""
        program, envelope = _case("30x40 east facing 3bhk in Whitefield with pooja room")
        verdict = assess(program, envelope, floor=1)
        assert not verdict.feasible
        assert "arrangements" in verdict.reason

    def test_an_empty_floor_is_trivially_fine(self):
        program, envelope = _case("30x40 3bhk in Bengaluru")
        assert assess(program, envelope, floor=4).feasible

    def test_the_reason_carries_the_numbers(self):
        """Stage ④'s job is explaining, and "infeasible" explains nothing."""
        program, envelope = _case("30x40 east facing 3bhk in Whitefield with pooja room")
        reason = assess(program, envelope, floor=1).reason
        assert "%" in reason or "m²" in reason


class TestOptionsAreMeasuredNotGuessed:
    def test_options_appear_only_when_something_is_wrong(self):
        """Including when the floor is solvable but thin: offering a plot owner rooms
        to drop while their house fits is noise, so the margin goes in the reason."""
        program, envelope = _case("50x80 4bhk in Bengaluru with study and store room")
        verdict = assess(program, envelope, floor=1)
        assert verdict.feasible
        assert verdict.options == []

    def test_the_verdict_counts_the_arrangements_stage_five_actually_tries(self):
        """It used to compound a hit rate — "5 in 60, and ⑤ tries 24, so 88%" — and
        that needs the 24 to be independent draws. They are not: the shortlist is
        *ranked*, and this probe runs the same ranked shortlist, so one legal
        arrangement among them means ⑤ finds it rather than probably finds it.

        Measured over six seeds a brief, the count separated the four reference briefs
        where the compounded rate did not: it called a 50x80 undependable at 71% while
        that plot laid out cleanly every single time.
        """
        program, envelope = _case("50x80 4bhk in Bengaluru with study and store room")
        verdict = assess(program, envelope, floor=1)
        assert verdict.feasible
        assert "arrangements stage" in verdict.reason
        assert "% to solve" not in verdict.reason

    def test_each_option_reports_what_it_actually_buys(self):
        """The whole point: the stage applies the change and runs the solver, rather
        than asserting that something ought to help."""
        program, envelope = _case("30x40 east facing 3bhk in Whitefield with pooja room")
        options = assess(program, envelope, floor=1).options
        assert options
        for option in options:
            assert option.probes > 0
            assert 0 <= option.feasible_after <= option.probes
            # An option that buys nothing says so in words. "0/60 become legal" is
            # technically the count and reads as though something happened.
            rendered = str(option)
            if option.helps:
                assert f"{option.feasible_after}/{option.probes}" in rendered
            else:
                assert "still no legal layout" in rendered

    def test_moving_the_car_porch_out_is_offered_and_helps(self):
        """The change that turns a 90%-packed floor into a solvable one — and the
        rule behind it is still unverified, which the rationale says."""
        program, envelope = _case("30x40 east facing 3bhk in Whitefield with pooja room")
        porch = next(
            o for o in assess(program, envelope, floor=1).options if "porch" in o.change
        )
        assert porch.helps
        assert "VERIFY" in porch.because

    def test_an_option_that_does_not_apply_is_not_offered(self):
        """No dining room on a 2BHK, so folding one in is not a suggestion."""
        program, envelope = _case("20x30 2bhk in Bengaluru")
        assert not any(r.kind is SpaceKind.DINING for r in program.on_floor(1))
        assert not any("dining" in o.change for o in assess(program, envelope, floor=1).options)


class TestNothingIsChangedBehindTheUsersBack:
    def test_assessing_leaves_the_programme_alone(self):
        """A plot owner who asked for a separate dining should be told what dropping
        it costs, not quietly deprived of it."""
        program, envelope = _case("30x40 east facing 3bhk in Whitefield with pooja room")
        before = [r.id for r in program.rooms]
        assess(program, envelope, floor=1)
        assert [r.id for r in program.rooms] == before
