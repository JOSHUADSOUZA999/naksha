"""Prompt loading.

Client construction lives in `providers/` — this module stayed provider-neutral when
the seam went in, because a versioned, hashed prompt is not a vendor concept.
"""

from __future__ import annotations

import functools
import hashlib
from dataclasses import dataclass
from pathlib import Path

_PROMPT_DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True, slots=True)
class Prompt:
    """A versioned prompt file plus the hash of its contents.

    The hash is what makes the version honest. A prompt edited without renaming the
    file would otherwise silently invalidate every stored Brief with no way to tell.
    """

    version: str
    text: str
    sha256: str


@functools.lru_cache(maxsize=8)
def load_prompt(version: str) -> Prompt:
    path = _PROMPT_DIR / f"{version}.md"
    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return Prompt(version=version, text=text, sha256=digest)
