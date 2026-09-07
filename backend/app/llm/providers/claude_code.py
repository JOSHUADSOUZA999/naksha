"""Claude Code provider — runs against a Claude subscription, no API key.

Uses the Claude Agent SDK (`claude-agent-sdk`), which drives a local Claude Code
install. That makes it the one provider whose credentials are a *login* rather than
a key, which is the entire reason it exists: it unblocks running stage ① on a
machine with no API access.

Signatures were read off the installed package (`claude-agent-sdk` 0.2.139), not
recalled:

- `query(prompt=..., options=ClaudeAgentOptions(...))` is an **async generator**
- `ClaudeAgentOptions.output_format` takes `{"type": "json_schema", "schema": {...}}`,
  the same shape as the Messages API
- the terminal `ResultMessage` carries `structured_output`, `is_error`, `subtype`,
  `stop_reason` and `api_error_status`

That `output_format` field is the important discovery. A naive `claude -p` wrapper
would have to prompt for JSON and parse prose, which `CLAUDE.md` forbids — here the
schema is still enforced upstream and we still receive a parsed object.

**Development provider.** It needs a Claude Code install on the box, spawns a
process per call, and draws on subscription limits rather than API rate limits.
Production paths are `anthropic_api` and `openai_api`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ValidationError

from app.llm.providers.base import (
    Message,
    ProviderNotInstalled,
    ProviderOutputInvalid,
    ProviderRefused,
    ProviderUnavailable,
)


class ClaudeCodeCaller:
    name = "claude_code"

    def __init__(self, *, timeout_s: float, runner: Any | None = None) -> None:
        self._timeout_s = timeout_s
        if runner is not None:
            # Injected for tests: an async generator function with query()'s shape.
            self._query = runner
            self._options_cls: Any = dict
            return
        try:
            from claude_agent_sdk import ClaudeAgentOptions, query
        except ImportError as exc:
            raise ProviderNotInstalled("claude_code", "claude-agent-sdk") from exc

        self._query = query
        self._options_cls = ClaudeAgentOptions

    def parse(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        output_model: type[BaseModel],
        max_tokens: int,
    ) -> BaseModel:
        # The SDK is async-only. Stage ① is synchronous, so bridge here rather than
        # colouring the whole pipeline async for one provider. asyncio.run refuses to
        # nest, which is the right failure: a caller already inside a loop needs to
        # await this properly, not have a second loop started underneath it.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise ProviderUnavailable(
                "claude_code cannot be called from inside a running event loop; "
                "use the anthropic or openai provider on async paths"
            )

        try:
            payload = asyncio.run(
                self._collect(
                    model=model,
                    system=system,
                    messages=messages,
                    output_model=output_model,
                    max_tokens=max_tokens,
                )
            )
        except (ProviderOutputInvalid, ProviderRefused, ProviderUnavailable):
            raise
        except Exception as exc:
            raise _as_provider_error(exc) from exc

        if payload is None:
            raise ProviderOutputInvalid("no structured output in the result message")

        try:
            return output_model.model_validate(payload)
        except ValidationError as exc:
            raise ProviderOutputInvalid(str(exc), validation_error=exc) from exc

    async def _collect(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        output_model: type[BaseModel],
        max_tokens: int,
    ) -> Any:
        options = self._build_options(model=model, system=system, output_model=output_model)

        # Claude Code takes a single prompt string, not a message array. Our retry
        # turns are all `user`, so flattening loses nothing — but label them so a
        # correction reads as a follow-up rather than as one run-on request.
        prompt = _flatten(messages)

        result: Any = None
        async for message in self._query(prompt=prompt, options=options):
            if type(message).__name__ == "ResultMessage":
                result = message

        if result is None:
            raise ProviderOutputInvalid("stream ended with no result message")

        if getattr(result, "is_error", False):
            status = getattr(result, "api_error_status", None)
            subtype = getattr(result, "subtype", None)
            raise ProviderUnavailable(f"claude code error (subtype={subtype}, http={status})")

        stop_reason = getattr(result, "stop_reason", None)
        if stop_reason == "refusal":
            raise ProviderRefused("refusal")
        if stop_reason == "max_tokens":
            raise ProviderOutputInvalid(
                "output truncated at the token cap; raise NAKSHA_INTENT_MAX_TOKENS"
            )

        return getattr(result, "structured_output", None)

    def _build_options(
        self, *, model: str, system: str, output_model: type[BaseModel]
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": model,
            "system_prompt": system,
            # Same shape as the Messages API. This is what keeps the schema
            # guarantee instead of degrading to prose parsing.
            "output_format": {
                "type": "json_schema",
                "schema": output_model.model_json_schema(),
            },
            # Stage ① needs no tools. Denying them closes off the file and shell
            # access a local Claude Code install would otherwise bring along.
            "allowed_tools": [],
            # Measured, not guessed: `max_turns=1` fails outright with
            # "Reached maximum number of turns", and a real extraction settles at
            # 2–3. Bounded well above that so a hard brief has room, since with no
            # tools there is no loop for it to run away in.
            "max_turns": 8,
        }
        if self._options_cls is dict:
            return kwargs
        return self._options_cls(**kwargs)


def _flatten(messages: list[Message]) -> str:
    if len(messages) == 1:
        return messages[0].get("content", "")
    return "\n\n".join(
        f"[{m.get('role', 'user')}]\n{m.get('content', '')}" for m in messages
    )


def _as_provider_error(exc: Exception) -> Exception:
    try:
        from claude_agent_sdk import (
            CLIConnectionError,
            CLIJSONDecodeError,
            CLINotFoundError,
            ProcessError,
        )
    except ImportError:  # pragma: no cover
        return ProviderUnavailable(f"{type(exc).__name__}: {exc}")

    if isinstance(exc, CLINotFoundError):
        return ProviderNotInstalled("claude_code", "the `claude` CLI (see claude.com/code)")
    if isinstance(exc, (CLIConnectionError, ProcessError)):
        return ProviderUnavailable(f"{type(exc).__name__}: {exc}")
    if isinstance(exc, CLIJSONDecodeError):
        # Garbled output is worth one more attempt, unlike a dead process.
        return ProviderOutputInvalid(f"could not decode CLI output: {exc}")

    # Matched on the message, deliberately. When the CLI reports an error result the
    # SDK re-raises it without a class we can key on. Getting this wrong is not
    # dangerous — the stage's catch-all still falls back — but it keeps "the provider
    # failed" distinct from "we have a bug" in provenance, which is what you check first.
    if "returned an error result" in str(exc):
        return ProviderUnavailable(_explain_result_error(str(exc)))
    return exc


def _explain_result_error(message: str) -> str:
    """Make the CLI's error text mean something to a reader.

    `ResultMessage` documents `api_error_status` as carrying the HTTP status "when
    `is_error` is True and `subtype` is 'success'" — so a result error whose subtype
    is *success* is the CLI's way of reporting a failed API call, typically 429, 500
    or 529. The subtype travels in the exception text; the status does not, because
    the SDK raises before we ever see the `ResultMessage` that holds it.

    Passing that through verbatim gives the user "returned an error result: success",
    which reads as a contradiction and sends them looking for a bug in the brief. The
    classification is right either way — a rate limit is unavailability, and no
    number of schema retries fixes one — but the sentence should say so.
    """
    if message.rstrip().endswith("success"):
        return (
            "the Claude Code CLI's API call failed (rate limit or server error — the "
            "HTTP status is not reported through this path). Subscription limits are "
            "the usual cause; an API key provider does not share them."
        )
    return message
