"""Tracing hook.

CLAUDE.md requires every LLM call to be traced. It should not require every developer
to hold Langfuse keys to run the test suite, so this is a real span when Langfuse is
configured and a no-op otherwise. The decision is made once, at import.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from typing import Any

_ENABLED = bool(os.environ.get("LANGFUSE_PUBLIC_KEY")) and bool(
    os.environ.get("LANGFUSE_SECRET_KEY")
)


@contextlib.contextmanager
def trace_span(name: str, **attributes: Any) -> Iterator[dict[str, Any]]:
    """Record one LLM call.

    Yields a mutable dict; write outputs into it and they land on the span. When
    tracing is off the dict is still yielded, so call sites need no branching.
    """
    payload: dict[str, Any] = {}
    if not _ENABLED:
        yield payload
        return

    try:  # pragma: no cover - exercised only with Langfuse installed and configured
        from langfuse import Langfuse
    except ImportError:
        yield payload
        return

    client = Langfuse()
    span = client.span(name=name, input=attributes)
    try:
        yield payload
    except Exception as exc:
        span.update(level="ERROR", status_message=str(exc))
        raise
    else:
        span.update(output=payload)
    finally:
        span.end()
        client.flush()


def tracing_enabled() -> bool:
    return _ENABLED
