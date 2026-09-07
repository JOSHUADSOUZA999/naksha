"""Provider registry.

The model id is the routing key. `claude-opus-5` goes to Anthropic and `gpt-5.2` to
OpenAI without anyone configuring anything, because in practice the one thing a user
always knows is which model they want. `NAKSHA_INTENT_PROVIDER` overrides the
inference for anything the prefix table cannot place — a fine-tuned checkpoint, a
gateway that renames models, a self-hosted OpenAI-compatible endpoint.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from app.llm.providers.base import (
    Message,
    ProviderError,
    ProviderNotInstalled,
    ProviderOutputInvalid,
    ProviderRefused,
    ProviderUnavailable,
    StructuredCaller,
)

if TYPE_CHECKING:
    from app.config import Settings

__all__ = [
    "Message",
    "ProviderError",
    "ProviderNotInstalled",
    "ProviderOutputInvalid",
    "ProviderRefused",
    "ProviderUnavailable",
    "StructuredCaller",
    "UnknownProvider",
    "build_provider",
    "inferrable_providers",
    "known_providers",
    "resolve_provider_name",
]


class UnknownProvider(ProviderError):
    """The model id matched no provider and none was configured."""


# Ordered longest-prefix-first so a more specific rule can shadow a general one.
_MODEL_PREFIXES: tuple[tuple[str, str], ...] = (
    ("anthropic.", "anthropic"),  # Bedrock-style ids
    ("claude-", "anthropic"),
    ("chatgpt-", "openai"),
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
)


def _build_anthropic(settings: Settings) -> StructuredCaller:
    from app.llm.providers.anthropic_api import AnthropicCaller

    return AnthropicCaller(timeout_s=settings.request_timeout_s)


def _build_openai(settings: Settings) -> StructuredCaller:
    from app.llm.providers.openai_api import OpenAICaller

    return OpenAICaller(timeout_s=settings.request_timeout_s)


def _build_claude_code(settings: Settings) -> StructuredCaller:
    from app.llm.providers.claude_code import ClaudeCodeCaller

    return ClaudeCodeCaller(timeout_s=settings.request_timeout_s)


# Imports are deferred into the factories so that having one SDK installed is enough.
_FACTORIES: dict[str, Callable[[Settings], StructuredCaller]] = {
    "anthropic": _build_anthropic,
    "claude_code": _build_claude_code,
    "openai": _build_openai,
}

# Never inferred from a model id — `claude-opus-5` means the API. Selecting it is
# always an explicit NAKSHA_INTENT_PROVIDER=claude_code, because it runs against a
# local install and a subscription rather than a key, and nobody should land on it
# by accident.
_OPT_IN_ONLY = frozenset({"claude_code"})


def known_providers() -> list[str]:
    return sorted(_FACTORIES)


def inferrable_providers() -> list[str]:
    """Providers a model id can route to on its own."""
    return sorted(set(_FACTORIES) - _OPT_IN_ONLY)


def resolve_provider_name(model: str, configured: str | None = None) -> str:
    """Pick a provider for `model`, honouring an explicit override first."""
    if configured:
        name = configured.strip().lower()
        if name not in _FACTORIES:
            raise UnknownProvider(
                f"NAKSHA_INTENT_PROVIDER={configured!r} is not one of "
                f"{known_providers()}"
            )
        return name

    lowered = model.strip().lower()
    for prefix, provider in _MODEL_PREFIXES:
        if lowered.startswith(prefix):
            return provider

    raise UnknownProvider(
        f"cannot tell which provider serves model {model!r}. Set "
        f"NAKSHA_INTENT_PROVIDER to one of {known_providers()} "
        f"(inferred automatically: {inferrable_providers()})."
    )


def build_provider(settings: Settings) -> StructuredCaller:
    name = resolve_provider_name(settings.intent_model, settings.intent_provider)
    return _FACTORIES[name](settings)
