"""Agent configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_DIR / ".env")


class ConfigurationError(RuntimeError):
    """Raised when a required local configuration value is missing."""


@dataclass(frozen=True)
class Settings:
    api_key: str
    model: str
    max_candidates: int
    timeout_seconds: float
    max_retries: int
    circuit_breaker_seconds: float
    thinking_enabled: bool = False

    @classmethod
    def from_environment(cls) -> "Settings":
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise ConfigurationError(
                "DEEPSEEK_API_KEY is missing. Copy .env.example to .env and set the key."
            )

        raw_limit = os.getenv("AGENT_MAX_CANDIDATES", "8")
        try:
            max_candidates = max(1, int(raw_limit))
        except ValueError as exc:
            raise ConfigurationError("AGENT_MAX_CANDIDATES must be an integer.") from exc

        try:
            timeout_seconds = max(1.0, float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "30")))
        except ValueError as exc:
            raise ConfigurationError("DEEPSEEK_TIMEOUT_SECONDS must be a number.") from exc
        try:
            max_retries = max(0, min(2, int(os.getenv("DEEPSEEK_MAX_RETRIES", "0"))))
        except ValueError as exc:
            raise ConfigurationError("DEEPSEEK_MAX_RETRIES must be an integer between 0 and 2.") from exc
        try:
            circuit_breaker_seconds = max(
                1.0, float(os.getenv("DEEPSEEK_CIRCUIT_BREAKER_SECONDS", "20"))
            )
        except ValueError as exc:
            raise ConfigurationError("DEEPSEEK_CIRCUIT_BREAKER_SECONDS must be a number.") from exc

        raw_thinking = os.getenv("DEEPSEEK_THINKING_ENABLED", "false").strip().casefold()
        if raw_thinking not in {"true", "false", "1", "0", "yes", "no"}:
            raise ConfigurationError("DEEPSEEK_THINKING_ENABLED must be true or false.")
        thinking_enabled = raw_thinking in {"true", "1", "yes"}

        return cls(
            api_key=api_key,
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro").strip(),
            max_candidates=max_candidates,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            circuit_breaker_seconds=circuit_breaker_seconds,
            thinking_enabled=thinking_enabled,
        )
