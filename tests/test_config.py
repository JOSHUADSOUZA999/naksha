"""Settings, and the .env bridge.

The bridge test is here because its failure mode is invisible: a correctly-filled
.env that never reaches the SDK produces no error, just a permanent silent fallback.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

from app.config import PROJECT_ROOT, Settings, get_settings


def test_project_root_resolves_to_the_repo():
    assert (PROJECT_ROOT / "pyproject.toml").is_file()
    assert (PROJECT_ROOT / ".env.example").is_file()
    assert (PROJECT_ROOT / "backend" / "app").is_dir()


def test_defaults():
    settings = get_settings()
    assert settings.intent_model == "claude-opus-5"
    assert settings.max_schema_retries == 2


def test_naksha_vars_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("NAKSHA_INTENT_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("NAKSHA_MAX_SCHEMA_RETRIES", "5")
    settings = Settings()
    assert settings.intent_model == "claude-sonnet-5"
    assert settings.max_schema_retries == 5


def test_dotenv_reaches_os_environ(tmp_path):
    """A .env key must land in os.environ, where the Anthropic SDK reads it.

    pydantic-settings' `env_file` only populates the Settings model. Importing
    app.config has to do the os.environ half as well, or ANTHROPIC_API_KEY in .env
    is read by nobody. Run in a subprocess so we get a clean import and a clean
    environment rather than whatever this process already has.
    """
    script = textwrap.dedent(
        """
        import os, sys
        sys.path.insert(0, os.environ["BACKEND"])
        import app.config  # noqa: F401  — the import is the thing under test
        print(os.environ.get("ANTHROPIC_API_KEY", ""))
        print(os.environ.get("NAKSHA_INTENT_MODEL", ""))
        """
    )
    env = {
        **os.environ,
        "BACKEND": str(PROJECT_ROOT / "backend"),
        "HOME": str(tmp_path),
    }
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("NAKSHA_INTENT_MODEL", None)

    # The loader is anchored to the project root, so the fixture has to live there.
    # A developer's real .env is moved aside rather than skipped around: reading
    # *their* file and asserting against it is how this test started passing
    # vacuously and then failing for the wrong reason.
    env_file = PROJECT_ROOT / ".env"
    stashed = env_file.read_text(encoding="utf-8") if env_file.exists() else None
    env_file.write_text(
        "ANTHROPIC_API_KEY=sk-ant-test-not-a-real-key\n"
        "NAKSHA_INTENT_MODEL=claude-haiku-4-5\n",
        encoding="utf-8",
    )
    try:
        out = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,  # cwd is deliberately elsewhere: the .env lookup must be
            check=True,    # anchored to the project root, not to where you ran from
        ).stdout.split("\n")
    finally:
        if stashed is None:
            env_file.unlink()
        else:
            env_file.write_text(stashed, encoding="utf-8")

    # Unconditional now. The old version weakened to "something loaded" whenever a
    # developer had their own .env — which is the case that most needed the check.
    api_key, model = out[0], out[1]
    assert api_key == "sk-ant-test-not-a-real-key"
    assert model == "claude-haiku-4-5"
