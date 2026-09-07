"""Runtime settings. Everything tunable lives here, nothing is hardcoded downstream."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> naksha/
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = PROJECT_ROOT / ".env"

# Load .env into os.environ, not just into Settings below.
#
# pydantic-settings' `env_file` only feeds this model — it never touches
# os.environ, which is where the Anthropic SDK looks for ANTHROPIC_API_KEY. Without
# this line a correctly-filled .env produces no error and no key: every request
# fails auth and silently lands on the offline parser. `override=False` so a real
# environment variable still wins over the file, which is what CI and prod expect.
load_dotenv(_ENV_FILE, override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NAKSHA_", env_file=_ENV_FILE, extra="ignore"
    )

    intent_model: str = "claude-opus-5"
    intent_max_tokens: int = 4096

    # Usually unset: the provider is inferred from the model id, since that is the
    # thing a user always knows. Set it for ids the prefix table cannot place — a
    # fine-tuned checkpoint, a gateway that renames models, an OpenAI-compatible
    # endpoint serving something else entirely.
    intent_provider: str | None = None

    # Schema-failure retries only. Transport retries (429/5xx) are the SDK's own
    # budget — layering ours on top would turn one bad minute into nine requests.
    max_schema_retries: int = 2

    # Generous: a cold structured-outputs schema compiles on first use, and the
    # fallback is what protects us from a hang, not a tight timeout.
    request_timeout_s: float = 90.0

    # True is the product default and CLAUDE.md's guarantee: a dead API must not be a
    # dead product. Set NAKSHA_ALLOW_FALLBACK=false while *evaluating the model*,
    # where a silent substitution is the worst outcome available — the output looks
    # fine and answers a different question. `--no-fallback` sets it per run.
    allow_fallback: bool = True


def get_settings() -> Settings:
    return Settings()
