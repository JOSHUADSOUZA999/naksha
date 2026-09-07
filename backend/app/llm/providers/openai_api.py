"""OpenAI provider — `client.responses.parse`, schema enforced server-side.

Signatures here were read off the installed SDK (`openai` 3.1.0) rather than
recalled: `responses.parse(model=, instructions=, input=, text_format=,
max_output_tokens=)` returning a `ParsedResponse` whose `.output_parsed` holds the
instance.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from app.llm.providers.base import (
    Message,
    ProviderNotInstalled,
    ProviderOutputInvalid,
    ProviderRefused,
    ProviderUnavailable,
)


class OpenAICaller:
    name = "openai"

    def __init__(self, *, timeout_s: float, client: Any | None = None) -> None:
        if client is not None:
            self._client = client
            return
        try:
            import openai
        except ImportError as exc:
            raise ProviderNotInstalled("openai", "openai") from exc

        # As with Anthropic, no explicit api_key: the SDK reads OPENAI_API_KEY (and
        # OPENAI_BASE_URL), which is also what lets this point at any OpenAI-compatible
        # gateway without a code change.
        #
        # Unlike Anthropic, OpenAI raises for a missing key *here*, at construction,
        # rather than at request time. Normalising it means stage ① sees the same
        # ProviderUnavailable either way instead of one vendor's exception class.
        try:
            self._client = openai.OpenAI(timeout=timeout_s)
        except openai.OpenAIError as exc:
            raise ProviderUnavailable(f"{type(exc).__name__}: {exc}") from exc

    def parse(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        output_model: type[BaseModel],
        max_tokens: int,
    ) -> BaseModel:
        try:
            response = self._client.responses.parse(
                model=model,
                instructions=system,  # the Responses API's system-prompt slot
                input=messages,
                text_format=output_model,
                max_output_tokens=max_tokens,
            )
        except ValidationError as exc:
            raise ProviderOutputInvalid(str(exc), validation_error=exc) from exc
        except Exception as exc:
            raise _as_provider_error(exc) from exc

        status = getattr(response, "status", None)
        if status == "incomplete":
            details = getattr(response, "incomplete_details", None)
            reason = getattr(details, "reason", None) or "unknown"
            if reason == "content_filter":
                raise ProviderRefused("content_filter")
            raise ProviderOutputInvalid(
                f"response incomplete ({reason}); if this is max_output_tokens, "
                "raise NAKSHA_INTENT_MAX_TOKENS"
            )
        if status == "failed":
            error = getattr(response, "error", None)
            raise ProviderUnavailable(f"response failed: {getattr(error, 'message', error)}")

        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            # A refusal lands here too — the SDK leaves output_parsed unset and puts a
            # refusal item in `output`, so look before calling it a schema problem.
            if _has_refusal(response):
                raise ProviderRefused("refusal")
            raise ProviderOutputInvalid("response contained no parsed output")
        return parsed


def _has_refusal(response: Any) -> bool:
    for item in getattr(response, "output", None) or []:
        for block in getattr(item, "content", None) or []:
            if getattr(block, "type", None) == "refusal" or getattr(block, "refusal", None):
                return True
    return False


def _as_provider_error(exc: Exception) -> Exception:
    try:
        import openai
    except ImportError:  # pragma: no cover
        return ProviderUnavailable(f"{type(exc).__name__}: {exc}")

    # Raised when generation stops at the token cap mid-object — retryable, unlike
    # the transport failures below.
    length_error = getattr(openai, "LengthFinishReasonError", None)
    if length_error is not None and isinstance(exc, length_error):
        return ProviderOutputInvalid(f"output truncated at the token cap: {exc}")

    filter_error = getattr(openai, "ContentFilterFinishReasonError", None)
    if filter_error is not None and isinstance(exc, filter_error):
        return ProviderRefused("content_filter")

    if isinstance(exc, openai.OpenAIError):
        # OpenAIError covers both API errors and the "no api_key" construction error,
        # which is the OpenAI equivalent of Anthropic's bare TypeError.
        return ProviderUnavailable(f"{type(exc).__name__}: {exc}")
    return exc
