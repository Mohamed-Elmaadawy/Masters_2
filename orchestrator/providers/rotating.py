"""Key-rotating wrapper: same ProviderAdapter shape, backed by several keys for one
provider instead of one.

Motivation (2026-08-10): free-tier per-key/per-account rate limits stall multi-run
experiments well before any real cost is incurred (Gemini/Groq free tiers do not bill --
they hard-cut at 429). Holding several keys the user actually controls (separate
accounts) and rotating past an exhausted one is the fix; this file is deliberately just
that and nothing more. See design/DESIGN_NOTES.md, "Multi-key rotation for free-tier
rate limits" for what this does NOT do and why, before extending it.

Rotates ONLY on StageCallFailed (orchestrator/stage_fns.py) -- the same exception both
gemini.py and groq.py raise for 429/RESOURCE_EXHAUSTED/rate_limit_exceeded, and also for
plain transport failures (timeout, 5xx). That second case means a single transient
network blip also advances past a key; harmless, since the alternative is just retrying
the same key. StageCallFatal (bad credentials, capability mismatch, malformed request)
is deliberately NOT rotated past -- a fatal error is a property of the REQUEST, not the
key, so it is equally fatal on every other key too. Rotating on it would only burn
through the whole key list to reproduce the same error N times before finally raising
it, and would misreport "N keys exhausted" for what is actually a single config bug (see
StageCallFatal's own docstring on why it exists as a distinct case). StageCallPartial
(inference happened, tokens were spent, output was unusable) is not rotated past either,
for the same reason: it is not a key problem.

Ordering: starts at whichever key last succeeded (self._index), not always index 0 --
once a key is exhausted for the rest of its quota window, retrying it first on every
subsequent call would waste one request per call confirming what is already known.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from design.schemas import OutputMode
from orchestrator.providers.base import CompletionResult, ProviderAdapter
from orchestrator.stage_fns import StageCallFailed


class RotatingKeyAdapter:
    """Wraps N single-key ProviderAdapter instances (all the same provider/adapter
    class) behind the one ProviderAdapter.complete() shape, so nothing above this layer
    -- orchestrator/stages.py, orchestrator/cli.py's StageFns wiring, pipeline.py's
    retry loop -- needs to know rotation is happening at all."""

    def __init__(self, adapters: list[ProviderAdapter]):
        if not adapters:
            raise ValueError("RotatingKeyAdapter requires at least one adapter")
        self._adapters = list(adapters)
        self._index = 0

    @classmethod
    def from_env(
        cls, make_adapter: Callable[[str], ProviderAdapter], env_var: str,
    ) -> "RotatingKeyAdapter":
        """env_var holds a comma-separated key list (e.g. GEMINI_API_KEYS=key1,key2).
        Deliberately a new, separate env var name from GeminiAdapter.from_env's
        GEMINI_API_KEY/GroqAdapter.from_env's GROQ_API_KEY -- a single-key setup keeps
        working unchanged against the singular var; nothing here reinterprets it."""
        raw = os.environ.get(env_var)
        if not raw:
            raise RuntimeError(
                f"{env_var} is not set -- read from the environment only, as a "
                "comma-separated list of at least one key, e.g. 'key1,key2,key3'")
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        if not keys:
            raise RuntimeError(f"{env_var} is set but contains no non-empty keys")
        return cls([make_adapter(k) for k in keys])

    def complete(
        self, prompt: str, *, model: str, temperature: float, timeout_seconds: float,
        output_mode: OutputMode = OutputMode.TEXT,
        response_schema: Optional[dict] = None,
        schema_name: Optional[str] = None,
    ) -> CompletionResult:
        n = len(self._adapters)
        last_exc: Optional[StageCallFailed] = None
        for offset in range(n):
            idx = (self._index + offset) % n
            try:
                result = self._adapters[idx].complete(
                    prompt, model=model, temperature=temperature,
                    timeout_seconds=timeout_seconds, output_mode=output_mode,
                    response_schema=response_schema, schema_name=schema_name)
            except StageCallFailed as e:
                last_exc = e
                continue
            self._index = idx
            return result
        assert last_exc is not None  # n >= 1 (checked in __init__), so the loop ran >= once
        raise last_exc
