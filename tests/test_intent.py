"""Stage ① control flow, against a fake provider. No network in CI, ever.

The fake implements `StructuredCaller`, so these tests exercise the same seam every
real provider plugs into — and say nothing about any SDK. What matters here is not
that the happy path works; it's that every failure route lands on a usable Brief with
honest provenance.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.ir.enums import Facing, VastuStance
from app.ir.models import Assumption, BriefDraft, PlotSpec, ProgramHints
from app.llm import intent
from app.llm.client import load_prompt
from app.llm.providers import (
    ProviderOutputInvalid,
    ProviderRefused,
    ProviderUnavailable,
    StructuredCaller,
)

TEXT = "30x40 east facing site in Bengaluru, 3BHK with pooja room"


def settings(**overrides: Any) -> Settings:
    return Settings(**{"intent_model": "claude-opus-5", "max_schema_retries": 2, **overrides})


def good_draft() -> BriefDraft:
    return BriefDraft(
        plot=PlotSpec(width_m=9.144, depth_m=12.192, road_edges=[Facing.EAST]),
        program=ProgramHints(bedrooms=3),
        vastu=VastuStance.MODERATE,
        assumptions=[
            Assumption(
                field="plot size",
                value="9.14 x 12.19 m",
                reason="'30x40' is feet by Indian convention",
                confidence=0.9,
            )
        ],
    )


def a_validation_error() -> ValidationError:
    """A real ValidationError, from the mistake this stage actually makes."""
    try:
        PlotSpec(width_m=1200.0, depth_m=1200.0, road_edges=[Facing.EAST])
    except ValidationError as exc:
        return exc
    raise AssertionError("expected PlotSpec to reject a 1200 m side")


def invalid_output() -> ProviderOutputInvalid:
    exc = a_validation_error()
    return ProviderOutputInvalid(str(exc), validation_error=exc)


class FakeProvider:
    """Implements StructuredCaller. Each scripted step is returned or raised."""

    name = "fake"

    def __init__(self, *script: Any) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> BaseModel:
        self.calls.append(kwargs)
        if not self._script:
            raise AssertionError("stage made more model calls than the test scripted")
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def test_fake_satisfies_the_protocol():
    """If this fails, the tests below are exercising a shape nothing real implements."""
    assert isinstance(FakeProvider(), StructuredCaller)


class TestHappyPath:
    def test_returns_brief_and_provenance(self):
        provider = FakeProvider(good_draft())
        result = intent.extract_brief(TEXT, provider=provider, settings=settings())

        assert result.brief.program.bedrooms == 3
        assert result.provenance.fallback_used is False
        assert result.provenance.attempts == 1
        assert result.provenance.model == "claude-opus-5"
        assert result.provenance.provider == "fake"

    def test_provenance_pins_the_prompt_by_hash(self):
        """A prompt edited without a version bump must not be invisible."""
        result = intent.extract_brief(
            TEXT, provider=FakeProvider(good_draft()), settings=settings()
        )
        prompt = load_prompt(intent.PROMPT_VERSION)

        assert result.provenance.prompt_version == "intent_v1"
        assert result.provenance.prompt_sha256 == prompt.sha256
        assert len(result.provenance.prompt_sha256) == 64

    def test_raw_text_comes_from_us_not_the_model(self):
        raw = "  30x40 EAST facing!!  "
        result = intent.extract_brief(
            raw, provider=FakeProvider(good_draft()), settings=settings()
        )
        assert result.brief.raw_text == raw

    def test_request_is_schema_constrained_and_provider_neutral(self):
        provider = FakeProvider(good_draft())
        intent.extract_brief(TEXT, provider=provider, settings=settings())
        call = provider.calls[0]

        assert call["output_model"] is BriefDraft
        assert call["model"] == "claude-opus-5"
        assert call["system"] == load_prompt("intent_v1").text
        assert call["messages"] == [{"role": "user", "content": TEXT}]
        assert call["max_tokens"] == 4096


class TestSchemaRetries:
    def test_retries_then_succeeds(self):
        provider = FakeProvider(invalid_output(), good_draft())
        result = intent.extract_brief(TEXT, provider=provider, settings=settings())

        assert result.provenance.fallback_used is False
        assert result.provenance.attempts == 2

    def test_correction_names_the_failing_field(self):
        """"Try again" makes the model repeat itself; naming the field fixes it."""
        provider = FakeProvider(invalid_output(), good_draft())
        intent.extract_brief(TEXT, provider=provider, settings=settings())

        correction = provider.calls[1]["messages"][-1]["content"]
        assert "width_m" in correction
        assert "square feet" in correction
        assert provider.calls[1]["messages"][0]["content"] == TEXT

    def test_correction_without_a_validation_error_still_says_something(self):
        """Truncation and empty output carry no field list, only a message."""
        provider = FakeProvider(
            ProviderOutputInvalid("response hit max_tokens"), good_draft()
        )
        intent.extract_brief(TEXT, provider=provider, settings=settings())
        assert "max_tokens" in provider.calls[1]["messages"][-1]["content"]

    def test_retry_budget_is_respected(self):
        provider = FakeProvider(*[invalid_output()] * 3)
        result = intent.extract_brief(TEXT, provider=provider, settings=settings())

        # 1 initial call + 2 retries. A fourth would mean the budget leaked.
        assert len(provider.calls) == 3
        assert result.provenance.attempts == 3
        assert result.provenance.fallback_used is True
        assert result.provenance.fallback_reason.startswith("schema:")

    def test_retry_budget_is_configurable(self):
        provider = FakeProvider(*[invalid_output()] * 2)
        intent.extract_brief(
            TEXT, provider=provider, settings=settings(max_schema_retries=1)
        )
        assert len(provider.calls) == 2

    def test_fallback_after_exhaustion_still_reads_the_text(self):
        provider = FakeProvider(*[invalid_output()] * 3)
        result = intent.extract_brief(TEXT, provider=provider, settings=settings())

        assert result.brief.program.bedrooms == 3
        assert result.brief.plot.facing is Facing.EAST
        assert result.brief.locale.city == "Bengaluru"


class TestNonRetryableFailures:
    def test_unavailable_goes_straight_to_fallback(self):
        """No schema retries on a dead API — retrying the schema cannot help.

        The SDK has already spent its own 429/5xx budget by the time we see this.
        """
        provider = FakeProvider(ProviderUnavailable("AuthenticationError: bad key"))
        result = intent.extract_brief(TEXT, provider=provider, settings=settings())

        assert len(provider.calls) == 1
        assert result.provenance.fallback_used is True
        assert "AuthenticationError" in result.provenance.fallback_reason
        assert result.brief.program.bedrooms == 3

    def test_refusal_is_reported_with_its_category(self):
        provider = FakeProvider(ProviderRefused("cyber"))
        result = intent.extract_brief(TEXT, provider=provider, settings=settings())

        assert result.provenance.fallback_used is True
        assert "refusal" in result.provenance.fallback_reason
        assert "cyber" in result.provenance.fallback_reason

    @pytest.mark.parametrize(
        "exc",
        [TypeError("bad headers"), KeyError("nope"), RuntimeError("boom"), ValueError("x")],
        ids=["TypeError", "KeyError", "RuntimeError", "ValueError"],
    )
    def test_no_exception_type_escapes(self, exc):
        """Providers normalise what they can predict; this covers what they cannot.

        The motivating case: with no credentials at all the Anthropic SDK raises a
        bare TypeError at request time — not an APIError, not at construction.
        """
        provider = FakeProvider(exc)
        result = intent.extract_brief(TEXT, provider=provider, settings=settings())

        assert result.provenance.fallback_used is True
        assert type(exc).__name__ in result.provenance.fallback_reason

    def test_provider_construction_failure_never_escapes(self, monkeypatch):
        def boom(_settings):
            raise RuntimeError("no credentials")

        monkeypatch.setattr(intent, "build_provider", boom)
        result = intent.extract_brief(TEXT, settings=settings())

        assert result.provenance.fallback_used is True
        assert result.provenance.attempts == 0
        assert "provider" in result.provenance.fallback_reason

    def test_unknown_model_falls_back_before_any_request(self, monkeypatch):
        result = intent.extract_brief(
            TEXT, settings=settings(intent_model="llama-4-405b")
        )
        assert result.provenance.fallback_used is True
        assert "cannot tell which provider" in result.provenance.fallback_reason
        assert result.brief.program.bedrooms == 3


class TestProvenanceRecordsTheProvider:
    def test_fallback_still_names_the_intended_provider(self):
        """Which vendor was *going* to be called is the first debugging question."""
        provider = FakeProvider(ProviderUnavailable("down"))
        result = intent.extract_brief(
            TEXT, provider=provider, settings=settings(intent_model="gpt-5.2")
        )
        assert result.provenance.fallback_used is True
        assert result.provenance.provider == "openai"
        assert result.provenance.model is None

    @pytest.mark.parametrize(
        "model,expected", [("claude-opus-5", "anthropic"), ("gpt-5.2", "openai")]
    )
    def test_provider_inferred_from_model_id(self, model: str, expected: str):
        result = intent.extract_brief(
            TEXT, provider=FakeProvider(ProviderUnavailable("x")), settings=settings(intent_model=model)
        )
        assert result.provenance.provider == expected


class TestAlwaysReturns:
    @pytest.mark.parametrize(
        "failure",
        [ProviderUnavailable("down"), invalid_output(), ProviderRefused(), TypeError("x")],
        ids=["unavailable", "schema", "refusal", "unexpected"],
    )
    def test_a_brief_always_comes_back(self, failure):
        """The load-bearing guarantee: the LLM being down does not break generation."""
        provider = FakeProvider(failure, failure, failure)
        result = intent.extract_brief(TEXT, provider=provider, settings=settings())

        assert result.brief.raw_text == TEXT
        assert result.brief.plot.width_m > 0
        assert result.provenance.fallback_used is True
        assert result.provenance.fallback_reason


class TestNoFallback:
    """`allow_fallback=False` is for evaluating the model rather than serving a user.

    Serving a user, a degraded brief they can correct beats a stack trace — CLAUDE.md
    is explicit. Testing the model, a silent substitution is the worst outcome
    available: the output looks fine and answers a different question.
    """

    def test_a_dead_provider_raises_instead_of_degrading(self):
        from app.llm.intent import FallbackRefused

        with pytest.raises(FallbackRefused, match="no Brief after"):
            intent.extract_brief(
                TEXT,
                provider=FakeProvider(ProviderUnavailable("down")),
                settings=settings(),
                allow_fallback=False,
            )

    def test_the_reason_survives_for_the_hint(self):
        """The CLI turns it into "set ANTHROPIC_API_KEY", so it must not be lost."""
        from app.llm.intent import FallbackRefused

        try:
            intent.extract_brief(
                TEXT,
                provider=FakeProvider(ProviderUnavailable("down")),
                settings=settings(),
                allow_fallback=False,
            )
        except FallbackRefused as exc:
            assert exc.reason
            assert exc.attempts >= 1
        else:
            pytest.fail("expected FallbackRefused")

    def test_exhausted_schema_retries_also_raise(self):
        """Two bad answers then a refusal to substitute — otherwise a model that
        cannot satisfy its own schema looks like one that can."""
        from app.llm.intent import FallbackRefused

        # One initial call plus two retries, all invalid.
        provider = FakeProvider(invalid_output(), invalid_output(), invalid_output())
        with pytest.raises(FallbackRefused, match="schema"):
            intent.extract_brief(
                TEXT, provider=provider, settings=settings(), allow_fallback=False
            )

    def test_the_default_still_falls_back(self):
        """The guarantee CLAUDE.md makes is unchanged for everyone who does not opt out."""
        result = intent.extract_brief(
            TEXT, provider=FakeProvider(ProviderUnavailable("down")), settings=settings()
        )
        assert result.provenance.fallback_used
        assert result.brief.plot.width_m > 0

    def test_a_working_model_is_unaffected(self):
        result = intent.extract_brief(
            TEXT,
            provider=FakeProvider(good_draft()),
            settings=settings(),
            allow_fallback=False,
        )
        assert not result.provenance.fallback_used


class TestGapsBecomeQuestions:
    """The clarifier can only ask about what appears in `assumptions`.

    A blocking field left silently null therefore produces no question — even when the
    next stage hard-fails on it. "40x60 for a joint family" got no city, stage ②
    raised `CityUnknown`, and the one question that would have unblocked it was never
    asked. The offline parser always recorded this gap; the model does not, and prompt
    adherence varies run to run in a way a hard failure downstream cannot depend on.
    """

    def _draft_without_city(self):
        from app.ir.models import Locale

        return BriefDraft(
            plot=PlotSpec(width_m=12.192, depth_m=18.288, road_edges=[Facing.EAST]),
            program=ProgramHints(bedrooms=4),
            locale=Locale(),
        )

    def test_a_silently_null_city_becomes_an_assumption(self):
        result = intent.extract_brief(
            TEXT, provider=FakeProvider(self._draft_without_city()), settings=settings()
        )
        assert any(a.field == "city" for a in result.brief.assumptions)

    def test_and_therefore_becomes_a_question(self):
        """The whole point: `city` is `blocking`, so once recorded it is asked."""
        result = intent.extract_brief(
            TEXT, provider=FakeProvider(self._draft_without_city()), settings=settings()
        )
        assert "city" in [q.field for q in result.questions]

    def test_a_model_that_explains_itself_is_not_talked_over(self):
        """If the model already recorded a city assumption, its wording wins."""
        from app.ir.models import Assumption, Locale

        draft = self._draft_without_city().model_copy(
            update={
                "assumptions": [
                    Assumption(
                        field="city",
                        value="left blank",
                        reason="two localities of this name, in different states",
                        confidence=0.05,
                    )
                ]
            }
        )
        result = intent.extract_brief(
            TEXT, provider=FakeProvider(draft), settings=settings()
        )
        cities = [a for a in result.brief.assumptions if a.field == "city"]
        assert len(cities) == 1
        assert "different states" in cities[0].reason

    def test_a_stated_city_records_no_gap(self):
        from app.ir.models import Locale

        draft = self._draft_without_city().model_copy(
            update={"locale": Locale(city="Bengaluru")}
        )
        result = intent.extract_brief(
            TEXT, provider=FakeProvider(draft), settings=settings()
        )
        assert not any(a.field == "city" for a in result.brief.assumptions)
