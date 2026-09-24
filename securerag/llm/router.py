"""LLM calls with cached clients, timeouts, retries and an ordered fallback chain.

``LLM_PROVIDER`` names the primary provider and ``LLM_FALLBACKS`` the ones to try next, e.g.
``LLM_PROVIDER=groq LLM_FALLBACKS=gemini,refusal``. Each provider gets ``LLM_MAX_RETRIES``
attempts with exponential backoff, but only for retryable failures (429, 5xx, timeouts and
connection errors). A provider without an API key, or failing with a non-retryable error (e.g.
400/401), is skipped right away. ``refusal`` always succeeds with a fixed "unavailable" message,
so a request never hangs on a dead provider. ``mock`` is the deterministic offline provider used
in tests and benchmarks.

``LLMResult`` records which provider actually answered and how many attempts it took; both go
into the graph state and the audit log.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from tenacity import RetryError, Retrying, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

REFUSAL_TEXT = ("The answer service is temporarily unavailable. Your request was received and audited; "
                "please try again shortly.")
_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


class ProviderUnavailable(Exception):
    """Provider cannot be used at all (e.g. no API key). Skip without retrying."""


@dataclass
class LLMResult:
    text: str
    provider: str
    attempts: int
    fallbacks: list[str] = field(default_factory=list)  # providers that failed before this one


def is_retryable(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status in _RETRYABLE_STATUS
    name = type(exc).__name__.lower()
    return isinstance(exc, (TimeoutError, ConnectionError)) or "timeout" in name or "connection" in name


# ------------------------------------------------------------------------------ providers

@lru_cache(maxsize=8)
def _client(provider: str, timeout: float) -> Any:
    """One cached SDK client per provider (connection pooling, no per-call construction)."""
    key_env = {"groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
               "gemini": "GEMINI_API_KEY"}[provider]
    api_key = os.getenv(key_env)
    if not api_key:
        raise ProviderUnavailable(f"{key_env} is not set")
    # SDK-level retries are disabled: the router owns retry policy.
    if provider == "groq":
        from groq import Groq

        return Groq(api_key=api_key, timeout=timeout, max_retries=0)
    if provider == "openai":
        from openai import OpenAI

        return OpenAI(api_key=api_key, timeout=timeout, max_retries=0)
    if provider == "anthropic":
        import anthropic

        return anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=0)
    from google import genai

    return genai.Client(api_key=api_key, http_options={"timeout": int(timeout * 1000)})


def _chat_completion(provider: str, model_env: str, default_model: str, timeout: float,
                     system: str, user: str) -> str:
    response = _client(provider, timeout).chat.completions.create(
        model=os.getenv(model_env, default_model),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return response.choices[0].message.content or ""


def _call_provider(provider: str, system: str, user: str, timeout: float) -> str:
    if provider == "mock":
        from securerag.llm.providers import _mock_grounded_response

        return _mock_grounded_response(system, user)
    if provider == "refusal":
        return REFUSAL_TEXT
    if provider == "groq":
        return _chat_completion("groq", "GROQ_MODEL", "llama-3.1-8b-instant", timeout, system, user)
    if provider == "openai":
        return _chat_completion("openai", "OPENAI_MODEL", "gpt-4o-mini", timeout, system, user)
    if provider == "anthropic":
        response = _client("anthropic", timeout).messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"), max_tokens=800, system=system,
            messages=[{"role": "user", "content": user}])
        return response.content[0].text
    if provider == "gemini":
        response = _client("gemini", timeout).models.generate_content(
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"), contents=f"{system}\n\n{user}")
        return response.text or ""
    raise ValueError(f"Unknown LLM provider: {provider}")


ProviderFn = Callable[[str, str, str, float], str]


# ------------------------------------------------------------------------------ router

class LLMRouter:
    def __init__(self, chain: list[str], *, timeout: float = 30.0, max_retries: int = 3,
                 backoff_base: float = 0.5, backoff_max: float = 8.0, call: ProviderFn = _call_provider):
        if not chain:
            raise ValueError("LLM provider chain is empty")
        self.chain = chain
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self._call = call

    @classmethod
    def from_env(cls, settings: Any = None) -> LLMRouter:
        primary = (os.getenv("LLM_PROVIDER") or getattr(settings, "llm_provider", "mock") or "mock").lower()
        fallbacks = os.getenv("LLM_FALLBACKS", getattr(settings, "llm_fallbacks", "refusal") or "")
        chain = [primary] + [p.strip().lower() for p in fallbacks.split(",") if p.strip()]
        chain = list(dict.fromkeys(chain))  # de-duplicate, keep order
        return cls(chain, timeout=float(getattr(settings, "llm_timeout_s", 30.0)),
                   max_retries=int(getattr(settings, "llm_max_retries", 3)))

    def generate(self, system: str, user: str) -> LLMResult:
        failed: list[str] = []
        for provider in self.chain:
            attempts = 0

            def attempt(p: str = provider) -> str:
                nonlocal attempts
                attempts += 1
                return self._call(p, system, user, self.timeout)

            retrying = Retrying(
                stop=stop_after_attempt(max(1, self.max_retries)),
                wait=wait_exponential(multiplier=self.backoff_base, max=self.backoff_max),
                retry=retry_if_exception(is_retryable),
                reraise=True,
            )
            try:
                text = retrying(attempt)
                if failed:
                    logger.warning("LLM answered by fallback provider %s after %s failed", provider, failed)
                return LLMResult(text=text, provider=provider, attempts=attempts, fallbacks=failed)
            except ProviderUnavailable as exc:
                logger.info("LLM provider %s unavailable: %s", provider, exc)
            except (RetryError, Exception) as exc:  # noqa: BLE001 - try the next provider
                logger.warning("LLM provider %s failed after %d attempt(s): %s", provider, attempts,
                               type(exc).__name__)
            failed.append(provider)
        # Nothing in the chain answered (no "refusal" configured): refuse rather than raise.
        return LLMResult(text=REFUSAL_TEXT, provider="refusal", attempts=0, fallbacks=failed)

    def stream(self, system: str, user: str) -> Iterator[str]:
        """Yield answer text in pieces. Uses ``generate`` (retries/fallbacks apply before the first
        byte) and splits the result, which keeps the retry and audit semantics identical to the
        non-streaming path. ``self.last`` holds the LLMResult afterwards."""
        result = self.generate(system, user)
        self.last = result
        words = result.text.split(" ")
        for i, word in enumerate(words):
            yield word if i == len(words) - 1 else word + " "
