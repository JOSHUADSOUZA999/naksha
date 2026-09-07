"""Versioned rule data.

CLAUDE.md decision 4: *rules are versioned data, not code.* Vastu schools disagree,
setbacks differ per city, and NBC gets amended — so the things that vary live as JSON
with a version stamped into every result's provenance, not as constants in a module.

The eventual home is Postgres (`store/`, unbuilt). Until then these are files on disk,
loaded exactly like a prompt: by name, with a sha256 of the contents, so a ruleset
edited without a version bump is detectable rather than silent.
"""

from __future__ import annotations

import functools
import hashlib
import json
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_RULES_DIR = Path(__file__).parent


class UnverifiedRuleset(UserWarning):
    """Raised as a warning when a loaded ruleset still contains unchecked figures.

    A warning rather than an error so the machinery can be built and tested against
    provisional data. Stage ② is the layer that must refuse to emit an envelope from
    one — by then the number reaches a person.
    """


@dataclass(frozen=True, slots=True)
class Ruleset:
    """A versioned rule file plus the hash of its contents."""

    version: str
    data: dict[str, Any]
    sha256: str

    @property
    def stamp(self) -> str:
        """What goes into `Provenance.ruleset_versions` — version and short hash.

        The hash is the load-bearing half. Two plans produced under "clarify_v1" are
        only comparable if the file behind that name never moved underneath them.
        """
        return f"{self.version}@{self.sha256[:6]}"


    @property
    def unverified(self) -> list[str]:
        """Every block still carrying `verified: false`, as a dotted path.

        Walks the whole document rather than one known shape. `setbacks_v1` nests its
        bands in lists under `authorities`; `spaces_v1` keys them by room name under
        `spaces`; the next ruleset will do something else again. A checker that knows
        only one layout reports zero unverified entries for the others — which reads
        as "checked" and is the exact failure this property exists to prevent.

        Keys starting with `_` are prose notes for the reader, never rule blocks.
        """
        found: list[str] = []

        def walk(node: Any, path: str) -> None:
            if isinstance(node, dict):
                if node.get("verified") is False:
                    found.append(path or "<root>")
                    return  # the block is the unit; do not descend into its fields
                for key, value in node.items():
                    if not key.startswith("_"):
                        walk(value, f"{path}.{key}" if path else key)
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, f"{path}[{index}]")

        walk(self.data, "")
        return found


@functools.lru_cache(maxsize=8)
def load_ruleset(version: str) -> Ruleset:
    path = _RULES_DIR / f"{version}.json"
    raw = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    ruleset = Ruleset(version=version, data=json.loads(raw), sha256=digest)
    if ruleset.unverified:
        warnings.warn(
            f"{version}: {len(ruleset.unverified)} block(s) not checked against the "
            f"source document (e.g. {ruleset.unverified[0]})",
            UnverifiedRuleset,
            stacklevel=2,
        )
    return ruleset
