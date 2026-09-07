"""The provider seam: routing, and the adapters' error mapping.

Adapters are tested with fake SDK clients rather than the real ones. The point of
these tests is that each vendor's failure vocabulary lands on the right *normalised*
error — because that mapping is the only thing standing between stage ①'s retry logic
and it silently working for one SDK only.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.ir.enums import Facing
from app.ir.models import BriefDraft, PlotSpec, ProgramHints
from app.llm.providers import (
    ProviderNotInstalled,
    ProviderOutputInvalid,
    ProviderRefused,
    ProviderUnavailable,
    StructuredCaller,
    UnknownProvider,
    build_provider,
    inferrable_providers,
    known_providers,
    resolve_provider_name,
)
from app.llm.providers.anthropic_api import AnthropicCaller
from app.llm.providers.claude_code import ClaudeCodeCaller
from app.llm.providers.openai_api import OpenAICaller


def draft() -> BriefDraft:
    return BriefDraft(
        plot=PlotSpec(width_m=9.144, depth_m=12.192, road_edges=[Facing.EAST]),
        program=ProgramHints(bedrooms=3),
    )


def call(caller: StructuredCaller) -> BaseModel:
    return caller.parse(
        model="m",
        system="s",
        messages=[{"role": "user", "content": "30x40"}],
        output_model=BriefDraft,
        max_tokens=1024,
    )


class TestRouting:
    @pytest.mark.parametrize(
        "model,provider",
        [
            ("claude-opus-5", "anthropic"),
            ("claude-sonnet-5", "anthropic"),
            ("anthropic.claude-opus-5", "anthropic"),  # Bedrock-style id
            ("CLAUDE-OPUS-5", "anthropic"),  # case-insensitive
            ("gpt-5.2", "openai"),
            ("gpt-4o-mini", "openai"),
            ("chatgpt-4o-latest", "openai"),
            ("o3-mini", "openai"),
            ("o4-mini", "openai"),
        ],
    )
    def test_inferred_from_model_id(self, model: str, provider: str):
        assert resolve_provider_name(model) == provider

    def test_explicit_override_wins(self):
        """For gateways that rename models, and fine-tuned checkpoints."""
        assert resolve_provider_name("claude-opus-5", "openai") == "openai"
        assert resolve_provider_name("ft:my-model:abc123", "openai") == "openai"

    def test_unknown_model_names_the_way_out(self):
        with pytest.raises(UnknownProvider, match="NAKSHA_INTENT_PROVIDER"):
            resolve_provider_name("llama-4-405b")

    def test_unknown_override_lists_valid_choices(self):
        with pytest.raises(UnknownProvider, match="anthropic"):
            resolve_provider_name("claude-opus-5", "gemini")

    def test_known_providers(self):
        assert known_providers() == ["anthropic", "claude_code", "openai"]

    def test_claude_code_is_opt_in_only(self):
        """`claude-opus-5` must mean the API, never the local CLI.

        Silently routing an API model id to a subscription-backed local install
        would change where the work runs, what it costs and what limits apply,
        without anyone asking for it.
        """
        assert resolve_provider_name("claude-opus-5") == "anthropic"
        assert "claude_code" not in inferrable_providers()
        assert resolve_provider_name("claude-opus-5", "claude_code") == "claude_code"

    @pytest.mark.parametrize(
        "model,key_var",
        [("claude-opus-5", "ANTHROPIC_API_KEY"), ("gpt-5.2", "OPENAI_API_KEY")],
    )
    def test_build_provider_returns_a_structured_caller(
        self, model: str, key_var: str, monkeypatch
    ):
        monkeypatch.setenv(key_var, "test-key-not-used-for-a-request")
        caller = build_provider(Settings(intent_model=model))
        assert isinstance(caller, StructuredCaller)

    def test_missing_openai_key_is_normalised_at_construction(self, monkeypatch):
        """The vendors disagree about *when* a missing key is an error.

        OpenAI raises at client construction; Anthropic gets as far as the request and
        raises a bare TypeError. Both must reach stage ① as ProviderUnavailable, or
        the fallback path only works for one of them.
        """
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_ADMIN_KEY", raising=False)
        with pytest.raises(ProviderUnavailable, match="OpenAIError"):
            build_provider(Settings(intent_model="gpt-5.2"))


class FakeSDK:
    """Minimal stand-in shaped like whichever SDK the adapter under test expects."""

    def __init__(self, result: Any, attr_path: tuple[str, str]) -> None:
        self.calls: list[dict[str, Any]] = []
        outer, inner = attr_path

        def method(**kwargs: Any) -> Any:
            self.calls.append(kwargs)
            if isinstance(result, Exception):
                raise result
            return result

        setattr(self, outer, type("Ns", (), {inner: staticmethod(method)})())


def anthropic_sdk(result: Any) -> Any:
    return FakeSDK(result, ("messages", "parse"))


def openai_sdk(result: Any) -> Any:
    return FakeSDK(result, ("responses", "parse"))


class AnthropicResponse:
    def __init__(self, parsed=None, stop_reason="end_turn", category=None):
        self.parsed_output = parsed
        self.stop_reason = stop_reason
        self.stop_details = type("D", (), {"category": category})() if category else None


class OpenAIResponse:
    def __init__(self, parsed=None, status="completed", reason=None, output=None):
        self.output_parsed = parsed
        self.status = status
        self.incomplete_details = type("D", (), {"reason": reason})() if reason else None
        self.output = output or []
        self.error = None


class TestAnthropicAdapter:
    def test_success_returns_the_instance(self):
        caller = AnthropicCaller(timeout_s=5, client=anthropic_sdk(AnthropicResponse(draft())))
        assert call(caller).program.bedrooms == 3

    def test_sends_the_schema_not_a_prose_instruction(self):
        sdk = anthropic_sdk(AnthropicResponse(draft()))
        call(AnthropicCaller(timeout_s=5, client=sdk))
        assert sdk.calls[0]["output_format"] is BriefDraft

    def test_refusal_is_checked_before_content(self):
        sdk = anthropic_sdk(AnthropicResponse(None, stop_reason="refusal", category="cyber"))
        with pytest.raises(ProviderRefused) as exc:
            call(AnthropicCaller(timeout_s=5, client=sdk))
        assert exc.value.category == "cyber"

    def test_truncation_is_retryable(self):
        sdk = anthropic_sdk(AnthropicResponse(draft(), stop_reason="max_tokens"))
        with pytest.raises(ProviderOutputInvalid, match="max_tokens"):
            call(AnthropicCaller(timeout_s=5, client=sdk))

    def test_empty_parse_is_retryable(self):
        with pytest.raises(ProviderOutputInvalid):
            call(AnthropicCaller(timeout_s=5, client=anthropic_sdk(AnthropicResponse(None))))

    def test_validation_error_carries_the_field_list(self):
        """Stage ① turns this into the correction prompt; losing it loses the retry."""
        try:
            PlotSpec(width_m=1200.0, depth_m=1200.0, road_edges=[Facing.EAST])
        except ValidationError as verr:
            sdk = anthropic_sdk(verr)
        with pytest.raises(ProviderOutputInvalid) as exc:
            call(AnthropicCaller(timeout_s=5, client=sdk))
        assert exc.value.validation_error is not None
        assert "width_m" in str(exc.value)

    def test_api_error_is_unavailable(self):
        import anthropic
        import httpx

        err = anthropic.APIConnectionError(
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        )
        with pytest.raises(ProviderUnavailable, match="APIConnectionError"):
            call(AnthropicCaller(timeout_s=5, client=anthropic_sdk(err)))

    def test_missing_credentials_typeerror_is_unavailable(self):
        """The regression that motivated normalising at all.

        With no key, token or profile the SDK raises a bare TypeError from
        `_validate_headers` at request time — not an APIError, and not at
        construction.
        """
        err = TypeError(
            "Could not resolve authentication method. Expected one of api_key, "
            "auth_token, or credentials to be set."
        )
        with pytest.raises(ProviderUnavailable, match="no usable credentials"):
            call(AnthropicCaller(timeout_s=5, client=anthropic_sdk(err)))

    def test_unrelated_typeerror_is_not_swallowed(self):
        """A genuine bug must stay a bug, not be relabelled as a provider outage."""
        with pytest.raises(TypeError, match="unsupported operand"):
            call(AnthropicCaller(timeout_s=5, client=anthropic_sdk(TypeError("unsupported operand"))))


class TestOpenAIAdapter:
    def test_success_returns_the_instance(self):
        caller = OpenAICaller(timeout_s=5, client=openai_sdk(OpenAIResponse(draft())))
        assert call(caller).program.bedrooms == 3

    def test_uses_the_responses_api_shape(self):
        """instructions/input/text_format/max_output_tokens — read off openai 3.1.0."""
        sdk = openai_sdk(OpenAIResponse(draft()))
        call(OpenAICaller(timeout_s=5, client=sdk))
        sent = sdk.calls[0]

        assert sent["text_format"] is BriefDraft
        assert sent["instructions"] == "s"
        assert sent["input"] == [{"role": "user", "content": "30x40"}]
        assert sent["max_output_tokens"] == 1024

    def test_incomplete_is_retryable(self):
        sdk = openai_sdk(OpenAIResponse(None, status="incomplete", reason="max_output_tokens"))
        with pytest.raises(ProviderOutputInvalid, match="max_output_tokens"):
            call(OpenAICaller(timeout_s=5, client=sdk))

    def test_content_filter_is_a_refusal_not_a_schema_problem(self):
        sdk = openai_sdk(OpenAIResponse(None, status="incomplete", reason="content_filter"))
        with pytest.raises(ProviderRefused) as exc:
            call(OpenAICaller(timeout_s=5, client=sdk))
        assert exc.value.category == "content_filter"

    def test_refusal_item_is_detected(self):
        """A refusal leaves output_parsed unset; without this it reads as bad schema."""
        block = type("B", (), {"type": "refusal", "refusal": "no"})()
        item = type("I", (), {"content": [block]})()
        sdk = openai_sdk(OpenAIResponse(None, output=[item]))
        with pytest.raises(ProviderRefused):
            call(OpenAICaller(timeout_s=5, client=sdk))

    def test_failed_status_is_unavailable(self):
        sdk = openai_sdk(OpenAIResponse(None, status="failed"))
        with pytest.raises(ProviderUnavailable, match="response failed"):
            call(OpenAICaller(timeout_s=5, client=sdk))

    def test_empty_parse_is_retryable(self):
        with pytest.raises(ProviderOutputInvalid):
            call(OpenAICaller(timeout_s=5, client=openai_sdk(OpenAIResponse(None))))

    def test_openai_error_is_unavailable(self):
        import openai

        err = openai.OpenAIError("no api_key")
        with pytest.raises(ProviderUnavailable, match="OpenAIError"):
            call(OpenAICaller(timeout_s=5, client=openai_sdk(err)))

    def test_unrelated_typeerror_is_not_swallowed(self):
        with pytest.raises(TypeError, match="unsupported operand"):
            call(OpenAICaller(timeout_s=5, client=openai_sdk(TypeError("unsupported operand"))))


class CCResult:
    """Shaped like claude_agent_sdk.ResultMessage. Matched by class name."""

    def __init__(
        self,
        structured_output=None,
        *,
        is_error=False,
        stop_reason=None,
        subtype="success",
        api_error_status=None,
    ):
        self.structured_output = structured_output
        self.is_error = is_error
        self.stop_reason = stop_reason
        self.subtype = subtype
        self.api_error_status = api_error_status


ResultMessage = CCResult  # the adapter matches on type(msg).__name__
CCResult.__name__ = "ResultMessage"


def claude_code_runner(*emitted: Any):
    """Stands in for claude_agent_sdk.query — an async generator function."""
    captured: dict[str, Any] = {}

    async def runner(*, prompt: Any, options: Any):
        captured["prompt"] = prompt
        captured["options"] = options
        for item in emitted:
            if isinstance(item, Exception):
                raise item
            yield item

    runner.captured = captured  # type: ignore[attr-defined]
    return runner


class TestClaudeCodeAdapter:
    def test_success_returns_the_instance(self):
        caller = ClaudeCodeCaller(
            timeout_s=5, runner=claude_code_runner(CCResult(draft().model_dump(mode="json")))
        )
        assert call(caller).program.bedrooms == 3

    def test_sends_a_json_schema_not_a_prose_instruction(self):
        """The discovery that makes this provider viable at all.

        `ClaudeAgentOptions.output_format` takes the Messages API's json_schema
        shape, so the schema is still enforced upstream — a bare `claude -p` wrapper
        would have had to prompt for JSON and parse prose, which CLAUDE.md forbids.
        """
        runner = claude_code_runner(CCResult(draft().model_dump(mode="json")))
        call(ClaudeCodeCaller(timeout_s=5, runner=runner))
        options = runner.captured["options"]

        assert options["output_format"]["type"] == "json_schema"
        assert options["output_format"]["schema"] == BriefDraft.model_json_schema()
        assert options["model"] == "m"
        assert options["system_prompt"] == "s"

    def test_runs_with_no_tools_and_room_to_answer(self):
        """No tools: a local Claude Code install otherwise brings file and shell
        access along for the ride.

        The turn budget is measured, not guessed — `max_turns=1` fails outright
        with "Reached maximum number of turns" and a real extraction settles at 2–3.
        """
        runner = claude_code_runner(CCResult(draft().model_dump(mode="json")))
        call(ClaudeCodeCaller(timeout_s=5, runner=runner))
        assert runner.captured["options"]["allowed_tools"] == []
        assert runner.captured["options"]["max_turns"] > 3

    def test_bare_exception_error_result_is_unavailable(self):
        """The SDK re-raises CLI error results as a plain `builtins.Exception`.

        No class to key on, so the mapping matches the message. Pinned here so a
        future SDK release that starts raising a real type shows up as a failure
        rather than as silently vaguer provenance.
        """
        runner = claude_code_runner(
            Exception("Claude Code returned an error result: Reached maximum number of turns (1)")
        )
        with pytest.raises(ProviderUnavailable, match="error result"):
            call(ClaudeCodeCaller(timeout_s=5, runner=runner))

    def test_retry_turns_are_labelled_when_flattened(self):
        """Claude Code takes one prompt string, so retries have to be flattened."""
        runner = claude_code_runner(CCResult(draft().model_dump(mode="json")))
        ClaudeCodeCaller(timeout_s=5, runner=runner).parse(
            model="m",
            system="s",
            messages=[
                {"role": "user", "content": "30x40"},
                {"role": "user", "content": "correction: width_m too large"},
            ],
            output_model=BriefDraft,
            max_tokens=1024,
        )
        prompt = runner.captured["prompt"]
        assert "30x40" in prompt and "correction" in prompt

    def test_error_result_is_unavailable(self):
        runner = claude_code_runner(CCResult(None, is_error=True, api_error_status=529))
        with pytest.raises(ProviderUnavailable, match="529"):
            call(ClaudeCodeCaller(timeout_s=5, runner=runner))

    def test_refusal_and_truncation_map_normally(self):
        with pytest.raises(ProviderRefused):
            call(ClaudeCodeCaller(timeout_s=5, runner=claude_code_runner(CCResult(None, stop_reason="refusal"))))
        with pytest.raises(ProviderOutputInvalid, match="truncated"):
            call(ClaudeCodeCaller(timeout_s=5, runner=claude_code_runner(CCResult(None, stop_reason="max_tokens"))))

    def test_missing_structured_output_is_retryable(self):
        with pytest.raises(ProviderOutputInvalid, match="no structured output"):
            call(ClaudeCodeCaller(timeout_s=5, runner=claude_code_runner(CCResult(None))))

    def test_stream_without_a_result_is_retryable(self):
        with pytest.raises(ProviderOutputInvalid, match="no result message"):
            call(ClaudeCodeCaller(timeout_s=5, runner=claude_code_runner()))

    def test_bad_payload_carries_the_field_list(self):
        bad = draft().model_dump(mode="json")
        bad["plot"]["width_m"] = 1200.0
        with pytest.raises(ProviderOutputInvalid) as exc:
            call(ClaudeCodeCaller(timeout_s=5, runner=claude_code_runner(CCResult(bad))))
        assert exc.value.validation_error is not None
        assert "width_m" in str(exc.value)

    def test_missing_cli_is_reported_as_not_installed(self):
        from claude_agent_sdk import CLINotFoundError

        runner = claude_code_runner(CLINotFoundError())
        with pytest.raises(ProviderNotInstalled, match="claude"):
            call(ClaudeCodeCaller(timeout_s=5, runner=runner))

    def test_refuses_to_run_inside_an_event_loop(self):
        """asyncio.run cannot nest. Failing loudly beats a deadlock in the API layer."""

        async def attempt():
            caller = ClaudeCodeCaller(
                timeout_s=5,
                runner=claude_code_runner(CCResult(draft().model_dump(mode="json"))),
            )
            with pytest.raises(ProviderUnavailable, match="event loop"):
                call(caller)

        asyncio.run(attempt())


class TestAdaptersAgree:
    """Both adapters must present the same vocabulary to stage ①.

    This is the whole justification for the seam: if the two disagree, the retry and
    fallback logic silently only works for one of them.
    """

    @pytest.mark.parametrize(
        "anthropic_result,openai_result,expected",
        [
            (AnthropicResponse(None), OpenAIResponse(None), ProviderOutputInvalid),
            (
                AnthropicResponse(None, stop_reason="refusal", category="c"),
                OpenAIResponse(None, status="incomplete", reason="content_filter"),
                ProviderRefused,
            ),
            (
                AnthropicResponse(draft(), stop_reason="max_tokens"),
                OpenAIResponse(None, status="incomplete", reason="max_output_tokens"),
                ProviderOutputInvalid,
            ),
        ],
        ids=["no-output", "refusal", "truncated"],
    )
    def test_same_situation_same_error(self, anthropic_result, openai_result, expected):
        with pytest.raises(expected):
            call(AnthropicCaller(timeout_s=5, client=anthropic_sdk(anthropic_result)))
        with pytest.raises(expected):
            call(OpenAICaller(timeout_s=5, client=openai_sdk(openai_result)))

    def test_both_return_the_same_instance_type_on_success(self):
        a = call(AnthropicCaller(timeout_s=5, client=anthropic_sdk(AnthropicResponse(draft()))))
        o = call(OpenAICaller(timeout_s=5, client=openai_sdk(OpenAIResponse(draft()))))
        assert type(a) is type(o) is BriefDraft

    @pytest.mark.parametrize("caller_cls", [AnthropicCaller, OpenAICaller])
    def test_both_satisfy_the_protocol(self, caller_cls):
        assert isinstance(caller_cls(timeout_s=5, client=object()), StructuredCaller)


class TestClaudeCodeResultErrors:
    """`is_error=True` with `subtype="success"` is the CLI's documented shape for a
    failed API call — `ResultMessage.api_error_status` carries 429/500/529 for exactly
    that case. So `ProviderUnavailable` is correct and schema retries are rightly
    skipped: no number of them fixes a rate limit. Only the wording was wrong."""

    def test_a_failed_api_call_is_unavailability_not_a_schema_problem(self):
        from app.llm.providers import ProviderUnavailable
        from app.llm.providers.claude_code import _as_provider_error

        normalised = _as_provider_error(Exception("Claude Code returned an error result: success"))
        assert isinstance(normalised, ProviderUnavailable)

    def test_the_contradiction_is_translated(self):
        """Verbatim, it reads "error result: success", which sends a reader looking
        for a bug in their own brief."""
        from app.llm.providers.claude_code import _explain_result_error

        explained = _explain_result_error("Claude Code returned an error result: success")
        assert "success" not in explained
        assert "rate limit" in explained

    def test_other_subtypes_are_left_alone(self):
        """`error_max_turns` already says what happened."""
        from app.llm.providers.claude_code import _explain_result_error

        original = "Claude Code returned an error result: error_max_turns"
        assert _explain_result_error(original) == original
