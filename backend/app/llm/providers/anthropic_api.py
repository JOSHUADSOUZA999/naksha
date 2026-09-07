"""Anthropic provider — `client.messages.parse`, schema enforced server-side."""

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


class AnthropicCaller:
    name = "anthropic"

    def __init__(self, *, timeout_s: float, client: Any | None = None) -> None:
        if client is not None:
            self._client = client
            return
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - anthropic is a core dep
            raise ProviderNotInstalled("anthropic", "anthropic") from exc

        # No api_key argument on purpose: the SDK resolves ANTHROPIC_API_KEY, then
        # ANTHROPIC_AUTH_TOKEN, then an `ant auth login` profile. Passing an explicit
        # key would break the profile path for anyone who uses it.
        self._client = anthropic.Anthropic(timeout=timeout_s)

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
            response = self._client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                output_format=output_model,
            )
        except ValidationError as exc:
            # Numeric bounds are stripped from the schema sent to the API and checked
            # client-side, so these surface as a raise rather than a bad response.
            raise ProviderOutputInvalid(str(exc), validation_error=exc) from exc
        except Exception as exc:
            raise _as_provider_error(exc) from exc

        # Check stop_reason before touching content: on a refusal the content array is
        # empty and on max_tokens it is truncated, so reading it first turns either
        # into a confusing IndexError instead of an actionable reason.
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise ProviderRefused(getattr(details, "category", None))
        if stop_reason == "max_tokens":
            raise ProviderOutputInvalid(
                "response hit max_tokens before the brief was complete; raise "
                "NAKSHA_INTENT_MAX_TOKENS"
            )

        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            raise ProviderOutputInvalid("response contained no parsed output")
        return parsed


def _as_provider_error(exc: Exception) -> Exception:
    """Map an SDK exception onto the normalised set.

    The `TypeError` branch is not defensive padding: with no credentials at all the
    SDK raises a bare TypeError from `_validate_headers` at *request* time, not an
    APIError and not at construction. Without it, the single most common
    misconfiguration escapes as an unhandled crash.
    """
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return ProviderUnavailable(f"{type(exc).__name__}: {exc}")

    if isinstance(exc, anthropic.APIError):
        return ProviderUnavailable(f"{type(exc).__name__}: {exc}")
    if isinstance(exc, TypeError) and "authentication" in str(exc).lower():
        return ProviderUnavailable(f"no usable credentials: {exc}")
    return exc
