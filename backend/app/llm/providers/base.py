"""The provider seam.

Stage ① needs one thing from a model: *give me an instance of this Pydantic class,
or tell me why you couldn't, in terms I already handle*. Everything provider-shaped —
SDK exception hierarchies, refusal encodings, truncation signals, the spelling of
"max tokens" — stops here.

The normalised errors are the load-bearing part. `intent.py` retries on
`ProviderOutputInvalid` and falls back on everything else; if a provider leaked its
own exception types through this boundary, that logic would silently only work for
whichever SDK it was written against.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError

Message = dict[str, str]


class ProviderError(Exception):
    """Base for every failure a provider is allowed to surface."""


class ProviderNotInstalled(ProviderError):
    """The SDK for this provider is not installed."""

    def __init__(self, provider: str, package: str) -> None:
        super().__init__(
            f"provider {provider!r} needs the {package!r} package: "
            f'pip install -e ".[{provider}]"'
        )


class ProviderUnavailable(ProviderError):
    """Auth, network, rate limit, 5xx — anything where retrying the schema is futile.

    Deliberately one class rather than a hierarchy. Stage ① takes the same action for
    all of them (fall back), and the specific cause is preserved in the message for
    provenance, where a human reads it.
    """


class ProviderRefused(ProviderError):
    """Safety classifiers declined the request."""

    def __init__(self, category: str | None = None) -> None:
        self.category = category or "unspecified"
        super().__init__(f"refused: {self.category}")


class ProviderOutputInvalid(ProviderError):
    """The model answered, but not with a usable instance. **The retryable one.**

    Covers three cases that look different per provider and identical to us: output
    that failed validation, output truncated before it was complete, and a response
    carrying no parsed object at all.
    """

    def __init__(
        self, detail: str, *, validation_error: ValidationError | None = None
    ) -> None:
        self.validation_error = validation_error
        super().__init__(detail)


@runtime_checkable
class StructuredCaller(Protocol):
    """What stage ① requires of a model provider.

    One method. Adding a provider means implementing it and registering a name — no
    changes to the stage itself.
    """

    name: str

    def parse(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        output_model: type[BaseModel],
        max_tokens: int,
    ) -> BaseModel:
        """Return a validated `output_model` instance.

        Raises `ProviderOutputInvalid` when a retry might help, and any other
        `ProviderError` when it will not.
        """
        ...
