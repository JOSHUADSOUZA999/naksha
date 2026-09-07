from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings

GOLDEN_PATH = Path(__file__).parent / "golden" / "briefs.json"


def load_golden_cases() -> list[dict[str, Any]]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))["cases"]


@pytest.fixture(scope="session")
def golden_cases() -> list[dict[str, Any]]:
    return load_golden_cases()


@pytest.fixture(autouse=True)
def _settings_isolated_from_local_config(monkeypatch):
    """The suite must not depend on whatever is in the developer's `.env`.

    CLAUDE.md requires it to pass with no network and no key. It also has to pass with
    a *populated* `.env` — the README tells you to create one, and a single
    `NAKSHA_INTENT_PROVIDER` in it silently redirects every provider-inference test.
    Eight of them failed exactly that way.

    Two sources to neutralise, because they are independent: `os.environ`, which
    `load_dotenv` fills at import, and pydantic-settings' own `env_file` read, which
    bypasses `os.environ` entirely. Clearing one and not the other fixes nothing.
    """
    for key in [k for k in os.environ if k.startswith("NAKSHA_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
