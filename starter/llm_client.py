"""LLM provider abstraction, DeepSeek client, and operational safeguards."""

from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from starter.config import Settings


class LLMResponseError(RuntimeError):
    """Raised when a model response cannot be used safely, with a stable public error class."""

    def __init__(self, message: str, *, error_code: str = "model_response_error"):
        super().__init__(message)
        self.error_code = error_code


class LLMProvider(ABC):
    """Abstract interface for pluggable LLM backends.

    Every provider must implement ``chat_json``.  Providers may optionally
    participate in the circuit breaker by returning ``True`` from
    ``supports_circuit_breaker``; the agent will then delegate transient-error
    tracking and cooldown gating to the provider.
    """

    @abstractmethod
    def chat_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """Send a conversation and return a parsed JSON object."""

    @property
    def supports_circuit_breaker(self) -> bool:
        """Whether the provider is compatible with ``ModelCircuitBreaker``."""
        return False


class DeepSeekClient(LLMProvider):
    def __init__(self, settings: Settings):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMResponseError(
                "The 'openai' package is not installed. Run pip install -r requirements.txt."
            ) from exc

        self._client = OpenAI(
            api_key=settings.api_key,
            base_url="https://api.deepseek.com",
            timeout=settings.timeout_seconds,
            max_retries=settings.max_retries,
        )
        self._model = settings.model

    @property
    def supports_circuit_breaker(self) -> bool:
        return True

    def chat_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or ""
        except Exception as exc:  # API library exposes provider-specific exception classes.
            raise LLMResponseError(
                f"DeepSeek request failed: {exc}", error_code=self._error_code(exc)
            ) from exc

        return self._parse_json(content)

    @staticmethod
    def _error_code(exc: Exception) -> str:
        name = type(exc).__name__
        if name == "APITimeoutError":
            return "timeout"
        if name == "APIConnectionError":
            return "connection"
        if name == "AuthenticationError":
            return "authentication"
        if name == "RateLimitError":
            return "rate_limit"
        if name == "APIStatusError":
            return "provider_status"
        return "model_request_error"

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        cleaned = content.strip()
        # 剥离 Markdown 代码块
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL)
        if fenced:
            cleaned = fenced.group(1).strip()
        # 修复中文引号
        fixed_quotes = (
            cleaned.replace("“", '"').replace("”", '"')
            .replace("‘", "'").replace("’", "'")
        )
        candidates = [fixed_quotes]
        # 提取第一个 { 到最后一个 } 的 JSON 子串（容忍前后有文字）
        start = fixed_quotes.find("{")
        end = fixed_quotes.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(fixed_quotes[start:end + 1])
        for cand in candidates:
            try:
                data = json.loads(cand)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue
        # 补齐未闭合的括号（模型输出截断时）
        for cand in candidates:
            for pad in range(1, 30):
                try:
                    data = json.loads(cand + "}" * pad)
                    if isinstance(data, dict):
                        return data
                except json.JSONDecodeError:
                    continue
        raise LLMResponseError("Model response was not valid JSON.", error_code="invalid_model_output")


@dataclass
class ModelObservability:
    """Process-local operational metrics that intentionally exclude prompt text."""

    calls: int = 0
    successes: int = 0
    failures: int = 0
    durations_ms: list[int] = field(default_factory=list)
    errors: dict[str, int] = field(default_factory=dict)

    def record_success(self, duration_ms: int) -> None:
        self.calls += 1
        self.successes += 1
        self.durations_ms.append(duration_ms)

    def record_failure(self, duration_ms: int, error_code: str) -> None:
        self.calls += 1
        self.failures += 1
        self.durations_ms.append(duration_ms)
        self.errors[error_code] = self.errors.get(error_code, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        values = sorted(self.durations_ms)
        p95_index = max(0, min(len(values) - 1, (95 * len(values) + 99) // 100 - 1)) if values else 0
        return {
            "calls": self.calls,
            "successes": self.successes,
            "failures": self.failures,
            "average_latency_ms": round(sum(values) / len(values), 1) if values else None,
            "p95_latency_ms": values[p95_index] if values else None,
            "errors": dict(self.errors),
        }


@dataclass
class ModelCircuitBreaker:
    """Bound provider retries across turns after consecutive transient failures."""

    cooldown_seconds: float
    consecutive_failures: int = 0
    open_until: float = 0.0

    TRANSIENT_ERRORS = {"timeout", "connection", "rate_limit", "provider_status", "model_request_error"}

    def is_open(self) -> bool:
        return time.monotonic() < self.open_until

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.open_until = 0.0

    def record_failure(self, error_code: str) -> bool:
        if error_code not in self.TRANSIENT_ERRORS:
            return False
        self.consecutive_failures += 1
        if self.consecutive_failures < 2:
            return False
        self.open_until = time.monotonic() + self.cooldown_seconds
        return True
