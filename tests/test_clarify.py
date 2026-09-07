"""The clarification selector — which guesses earn a question, and which do not.

No network and no model: the selector takes assumptions and rules, both of which are
data, so every case here is exact rather than approximate.
"""

from __future__ import annotations

import pytest

from app.ir.models import Assumption
from app.llm import fallback, intent
from app.llm.intent import CLARIFY_RULES, cap_derived_confidence, select_questions
from app.rules import Ruleset, load_ruleset

RULES = load_ruleset(CLARIFY_RULES).data


def _a(field: str, confidence: float, value: str = "x") -> Assumption:
    return Assumption(field=field, value=value, reason="because", confidence=confidence)


def _asked(*assumptions: Assumption) -> list[str]:
    return [q.field for q in select_questions(list(assumptions))]


def _capped(*assumptions: Assumption) -> dict[str, float]:
    return {a.field: a.confidence for a in cap_derived_confidence(list(assumptions))}


class TestTheGate:
    def test_a_brief_that_states_everything_blocking_asks_nothing(self):
        """The bug this gate exists to fix.

        Dimensions, orientation, bedroom count and city — every blocking field. A
        bottom-N selector still asked two questions, because there is always a lowest
        row. There is not always a row worth asking about.
        """
        brief = fallback.parse("30x60 east facing 2bhk in Pune")
        assert select_questions(brief.assumptions) == []

    def test_each_tier_carries_its_own_bar(self):
        """0.6 is a question for a blocking field and a shrug for a consequential one.
        A single global threshold cannot express that."""
        assert _asked(_a("plot size", 0.6)) == ["plot size"]
        assert _asked(_a("facing", 0.6)) == []
        assert _asked(_a("facing", 0.4)) == ["facing"]

    def test_defaultable_is_never_asked_however_low_it_falls(self):
        """Parking and occupants are visible in the table and correctable on the
        gallery screen. Asking about them is how a clarifier turns into a form."""
        assert _asked(_a("parking", 0.01), _a("occupants", 0.01), _a("vastu", 0.01)) == []

    def test_tier_zero_is_not_a_value_anyone_could_answer(self):
        assert _asked(_a("parser", 0.0)) == []

    def test_unknown_fields_default_to_defaultable(self):
        """A model free to name its own fields will name one we have no rule for.
        Silence is the safe failure: we would not know what to ask about it."""
        assert _asked(_a("something_new", 0.01)) == []


class TestTierGatesBeforeConfidenceSorts:
    def test_a_blocking_coin_flip_outranks_a_lower_consequential_one(self):
        """The mechanism, not the outcome. Sorting on confidence alone puts facing
        first here; the tier is what says otherwise."""
        assert _asked(_a("facing", 0.1), _a("plot size", 0.65)) == ["plot size", "facing"]

    def test_within_a_tier_the_least_certain_comes_first(self):
        assert _asked(_a("bedrooms", 0.6), _a("plot size", 0.2)) == [
            "plot size",
            "bedrooms",
        ]

    def test_ties_break_on_name_not_dict_order(self):
        """A selector that reorders its own questions between identical runs cannot be
        tuned against a golden set."""
        first = _asked(_a("facing", 0.3), _a("floors", 0.3))
        second = _asked(_a("floors", 0.3), _a("facing", 0.3))
        assert first == second == ["facing", "floors"]


class TestBudgetIsACeiling:
    def test_never_more_than_the_cap(self):
        asked = _asked(
            _a("plot size", 0.1), _a("bedrooms", 0.1), _a("facing", 0.1), _a("floors", 0.1)
        )
        assert len(asked) == RULES["budget"]["ask"]

    def test_but_usually_far_fewer(self):
        assert len(_asked(_a("plot size", 0.2))) == 1


class TestCollapse:
    def test_one_answer_settles_everything_derived_from_it(self):
        """Asking about bedrooms and its three children spends the whole budget
        re-asking a single question."""
        assert _asked(
            _a("bedrooms", 0.4), _a("bathrooms", 0.4), _a("occupants", 0.4), _a("floors", 0.4)
        ) == ["bedrooms"]

    def test_plot_size_collapses_its_own_children(self):
        assert _asked(_a("plot size", 0.2), _a("frontage", 0.2), _a("parking", 0.2)) == [
            "plot size"
        ]

    def test_a_derived_value_stands_alone_when_its_source_was_read(self):
        """The collapse is only valid while the parent is a guess. With the plot size
        read from the text, the frontage reading is worth its own question."""
        assert _asked(_a("frontage", 0.3)) == ["frontage"]


class TestCeiling:
    def test_plot_size_is_a_root_and_its_doubt_reaches_its_children(self):
        """The second instance of the laundering bug. You cannot know which edge is
        the frontage, or whether a 2BHK fits on one level, without knowing the plot."""
        out = _capped(
            _a("plot size", 0.2), _a("frontage", 0.6), _a("floors", 0.75), _a("parking", 0.8)
        )
        assert out == {"plot size": 0.2, "frontage": 0.2, "floors": 0.2, "parking": 0.2}

    def test_bedrooms_is_the_other_root(self):
        out = _capped(_a("bedrooms", 0.4), _a("bathrooms", 0.8), _a("occupants", 0.7))
        assert out == {"bedrooms": 0.4, "bathrooms": 0.4, "occupants": 0.4}

    def test_two_parents_cap_by_the_weaker_of_them(self):
        """`floors` hangs off both the plot size and the bedroom count. The naive
        implementation takes the last parent it looked at."""
        assert _capped(_a("plot size", 0.6), _a("bedrooms", 0.3), _a("floors", 0.9))[
            "floors"
        ] == pytest.approx(0.3)
        assert _capped(_a("plot size", 0.3), _a("bedrooms", 0.6), _a("floors", 0.9))[
            "floors"
        ] == pytest.approx(0.3)

    def test_a_parent_that_was_read_constrains_nothing(self):
        """Absent from the assumption list means it came from the text. "3BHK" is not
        a guess, so the bathroom default it implies keeps its own confidence."""
        assert _capped(_a("bathrooms", 0.8)) == {"bathrooms": 0.8}

    def test_never_raises_a_confidence(self):
        assert _capped(_a("plot size", 0.9), _a("frontage", 0.3))["frontage"] == 0.3

    def test_doubt_reaches_through_a_chain(self, monkeypatch):
        """Transitive by construction. The shipped graph is one level deep today, so
        this pins the behaviour before a deeper edge is added rather than after."""
        chain = Ruleset(
            version="t", sha256="0" * 64, data={"depends_on": {"b": ["a"], "c": ["b"]}}
        )
        monkeypatch.setattr(intent, "load_ruleset", lambda _: chain)
        assert _capped(_a("a", 0.2), _a("b", 0.9), _a("c", 0.95)) == {
            "a": 0.2,
            "b": 0.2,
            "c": 0.2,
        }

    def test_a_malformed_cyclic_ruleset_does_not_hang(self, monkeypatch):
        """The shipped graph is acyclic and a test above enforces that. This is about
        the walk itself: a hand-edited rules file should not take the process down."""
        cycle = Ruleset(
            version="t", sha256="0" * 64, data={"depends_on": {"a": ["b"], "b": ["a"]}}
        )
        monkeypatch.setattr(intent, "load_ruleset", lambda _: cycle)
        assert _capped(_a("a", 0.5), _a("b", 0.3)) == {"a": 0.3, "b": 0.3}


class TestRulesetIsData:
    def test_every_askable_field_has_a_question(self):
        askable = {f for f, t in RULES["tiers"].items() if RULES["ask_below"][t] > 0}
        assert askable <= set(RULES["questions"])

    def test_every_tier_has_a_bar_and_a_rank(self):
        used = set(RULES["tiers"].values()) | {RULES["default_tier"]}
        assert used <= set(RULES["ask_below"])
        assert used <= set(RULES["rank"])

    def test_the_dag_only_references_fields_that_exist(self):
        known = set(RULES["tiers"])
        for field, parents in RULES["depends_on"].items():
            assert field in known, field
            assert set(parents) <= known, field

    def test_the_dag_is_acyclic(self):
        """A cycle would collapse both ends and ask about neither."""
        depends = RULES["depends_on"]

        def walk(node: str, seen: frozenset[str]) -> None:
            assert node not in seen, f"cycle through {node}"
            for parent in depends.get(node, []):
                walk(parent, seen | {node})

        for field in depends:
            walk(field, frozenset())

    def test_version_and_hash_are_both_recorded(self):
        stamp = load_ruleset(CLARIFY_RULES).stamp
        assert stamp.startswith(f"{CLARIFY_RULES}@")
        assert len(stamp.split("@")[1]) == 6

    def test_provenance_carries_the_ruleset(self):
        from app.cli import _offline_provenance

        assert _offline_provenance().ruleset_versions["clarify"].startswith("clarify_v1@")


class TestAgainstRealBriefs:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("30x60 east facing 2bhk in Pune", []),
            # Whitefield names Bengaluru at 0.95, which clears the blocking bar.
            ("30x40 east facing site in Whitefield, 3BHK with pooja room", []),
            # No city at all: it is the least certain blocking field, so it leads.
            ("30x60 east facing 2bhk", ["city"]),
            ("make me a house", ["city", "plot size", "bedrooms"]),
            ("3BHK, my mother lives with us", ["city", "plot size", "facing"]),
            ("40x60 for a joint family", ["city", "bedrooms", "facing"]),
        ],
    )
    def test_end_to_end(self, text: str, expected: list[str]):
        brief = fallback.parse(text)
        assert _asked(*cap_derived_confidence(brief.assumptions)) == expected

    def test_every_question_carries_the_value_it_would_replace(self):
        """A question the user can ignore has to say what happens if they do."""
        brief = fallback.parse("make me a house")
        for q in select_questions(brief.assumptions):
            assert q.assumed and q.ask and q.because
            assert q.confidence < 1.0


class TestCornerPlots:
    """The second road has no other way of being asked about.

    "Corner plot" states that one exists without saying which side, and the two
    readings put the larger setback on opposite edges.
    """

    def test_a_stated_corner_still_asks_which_second_side(self):
        brief = fallback.parse("30x40 north facing corner plot 3bhk in Pune")
        assert _asked(*cap_derived_confidence(brief.assumptions)) == ["road edges"]

    def test_road_edges_does_not_collapse_into_facing(self):
        """Tempting edge, wrong edge. Which way the frontage points says nothing about
        which *other* side carries a road, so making it a child would retire a
        question that nothing else can ask."""
        assert "facing" not in RULES["depends_on"].get("road edges", [])
        assert _asked(_a("facing", 0.2), _a("road edges", 0.3)) == [
            "facing",
            "road edges",
        ]

    def test_an_ordinary_plot_says_nothing_about_road_edges(self):
        brief = fallback.parse("30x40 north facing 3bhk")
        assert not any(a.field == "road edges" for a in brief.assumptions)


class TestCityBecameBlocking:
    """Stage ② cannot pick a setback ruleset without a city, and a wrong ruleset is a
    wrong envelope rather than a wrong label. `city` was `defaultable` only while
    nothing consumed it."""

    def test_an_unplaceable_brief_asks_for_the_city_first(self):
        """Least certain of the blocking fields at 0.1, so it leads the list."""
        brief = fallback.parse("30x40 east facing 3bhk")
        assert _asked(*cap_derived_confidence(brief.assumptions))[0] == "city"

    def test_a_stated_city_is_not_a_question(self):
        brief = fallback.parse("30x40 east facing 3bhk in Pune")
        assert "city" not in _asked(*cap_derived_confidence(brief.assumptions))

    def test_a_locality_that_names_its_city_clears_the_bar(self):
        """Whitefield implies Bengaluru at 0.95, well above the 0.7 blocking bar, so
        the inference stands without interrupting anyone."""
        brief = fallback.parse("30x40 east facing 3bhk in Whitefield")
        assert brief.locale.city == "Bengaluru"
        assert "city" not in _asked(*cap_derived_confidence(brief.assumptions))


class TestUnverifiedRuleData:
    """Setback and FAR figures look equally authoritative whether right or invented,
    and the envelope they produce is something a person acts on."""

    def test_the_setback_tables_declare_themselves_unchecked(self):
        from app.rules import UnverifiedRuleset

        import warnings

        load_ruleset.cache_clear()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            rules = load_ruleset("setbacks_v1")
        assert rules.unverified, "provisional bands must not read as verified"
        assert any(w.category is UnverifiedRuleset for w in caught)

    def test_verified_rule_data_warns_about_nothing(self):
        assert load_ruleset(CLARIFY_RULES).unverified == []

    def test_every_band_cites_a_source(self):
        """An unverified figure without a citation cannot ever be checked."""
        bbmp = load_ruleset("setbacks_v1").data["authorities"]["BBMP"]
        for table in ("setback_bands", "coverage_far_bands"):
            for band in bbmp[table]:
                assert band["source"], f"{table}: uncited band {band}"

    def test_an_unmapped_city_has_no_ruleset_rather_than_a_borrowed_one(self):
        """A neighbouring city's bye-laws are not a conservative default. They are a
        different answer, and stage ② has to say so."""
        mapped = load_ruleset("setbacks_v1").data["city_to_authority"]
        assert "Pune" not in mapped
        assert mapped["Bengaluru"] == "BBMP"


class TestUnverifiedDetectionIsShapeAgnostic:
    """Each ruleset nests its blocks differently. A checker that knows one layout
    reports zero unverified entries for the others — which reads as "checked"."""

    def test_it_finds_blocks_nested_in_lists(self):
        found = load_ruleset("setbacks_v1").unverified
        assert any(p.startswith("authorities.BBMP.setback_bands[") for p in found)

    def test_it_finds_blocks_keyed_by_name(self):
        """`spaces.kitchen` was verified against the 2017 bye-laws, so the checker
        must no longer flag it — and must still flag the ones transcribed from
        practice, which is the harder half to get right."""
        rules = load_ruleset("spaces_v1")
        found = rules.unverified
        # Asserted as a property rather than by naming a room: entries get verified
        # one at a time, and a test that names today's unverified example breaks the
        # day someone checks it — which is the day you least want a red suite.
        assert found, "spaces_v1 has entries still transcribed from practice"
        assert all(u.startswith("spaces.") for u in found)
        verified = {n for n, r in rules.data["spaces"].items() if r.get("verified")}
        assert verified and not (verified & {u.split(".", 1)[1] for u in found})

    def test_prose_notes_are_not_rule_blocks(self):
        """Keys starting with `_` are commentary for the reader."""
        assert not any("._" in p for p in load_ruleset("setbacks_v1").unverified)

    def test_a_fully_checked_ruleset_reports_nothing(self):
        assert load_ruleset(CLARIFY_RULES).unverified == []
