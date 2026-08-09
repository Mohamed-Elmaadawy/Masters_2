"""Shared shape for provider adapters. No `requests` import here either -- only
gemini.py/groq.py need the actual HTTP layer; this module is just the Protocol/return
type/shared validation both share, importable on its own."""

from __future__ import annotations

import re
from typing import NamedTuple, Optional, Protocol

from design.schemas import OutputMode
from orchestrator.stage_fns import StageCallFatal

_NAME_SANITIZE = re.compile(r"[^A-Za-z0-9_-]+")


class CompletionResult(NamedTuple):
    """One provider call's raw text output -- not yet parsed into a stage's
    StageCallResult(raw: dict, ...); that parsing is stage-specific (JSON-mode
    prompting / extraction into each stage's own model_cls shape) and stays deferred to
    orchestrator/stages.py, still out of scope for this task. output_mode records what
    was actually sent to the provider (R3 Fix 1's "record the output mode"), scoped to
    this return value only -- NOT written into StageAttempt/DocumentStageAttempt, which
    would be a separate, larger, orchestrator-level persisted-log schema change nobody
    asked for here."""
    text: str
    prompt_tokens: int
    completion_tokens: int
    output_mode: OutputMode


class ProviderAdapter(Protocol):
    def complete(
        self, prompt: str, *, model: str, temperature: float, timeout_seconds: float,
        output_mode: OutputMode = OutputMode.TEXT,
        response_schema: Optional[dict] = None,
        schema_name: Optional[str] = None,
    ) -> CompletionResult:
        """Raises orchestrator.stage_fns.StageCallFailed for a retryable transport
        failure (timeout, connection error, rate limit, 5xx, or -- for Groq's
        best-effort JSON Schema mode specifically -- a generated-output/schema
        mismatch, which the provider's own docs recommend retrying rather than
        treating as fatal); orchestrator.stage_fns.StageCallFatal for a failure
        retrying cannot fix (bad credentials, a request the provider will never accept
        unchanged, an output mode the model/schema combination doesn't support, a
        missing response_schema when one was required); and
        orchestrator.stage_fns.StageCallPartial when inference happened and tokens were
        spent but no usable output could be extracted from an otherwise-200 response.
        Never raises anything else for a failed call -- every adapter-specific
        exception is translated into one of those three at the adapter boundary, so
        call_stage/call_document_stage only ever need to understand those three types.

        schema_name (2026-08-09): Groq's json_schema request format requires a stable
        `name` alongside the schema itself (see orchestrator/providers/groq.py). Optional
        here because Gemini's response_schema has no equivalent field and TEXT/
        JSON_OBJECT modes need no name at all; when JSON_SCHEMA is requested without an
        explicit schema_name, callers fall back to response_schema.get("title") and
        finally a fixed default -- see require_response_schema/default_schema_name
        below."""
        ...


def require_response_schema(output_mode: OutputMode, response_schema: Optional[dict]) -> None:
    """Both adapters call this before sending any HTTP request (fix for: an adapter
    silently sending JSON_SCHEMA mode with no schema, letting the provider itself
    reject it after a wasted round trip). A missing or invalid response_schema when
    JSON_SCHEMA is requested cannot be fixed by retrying with the same arguments, so
    this raises StageCallFatal, not StageCallFailed."""
    if output_mode is not OutputMode.JSON_SCHEMA:
        return
    if not isinstance(response_schema, dict) or not response_schema:
        raise StageCallFatal(
            f"output_mode={output_mode.value!r} requires a non-empty response_schema "
            f"dict, got {response_schema!r}")


def default_schema_name(response_schema: Optional[dict], schema_name: Optional[str]) -> str:
    """Resolves the stable name Groq's json_schema.name field requires (see
    orchestrator/providers/groq.py) -- explicit schema_name first, then
    response_schema['title'] if present, then a fixed fallback. Sanitized to the
    conservative [A-Za-z0-9_-] charset most schema-name validators accept, since a
    JSON Schema `title` is free text and not guaranteed to already be a valid
    identifier."""
    candidate = schema_name or (response_schema or {}).get("title") or "stage_output"
    sanitized = _NAME_SANITIZE.sub("_", str(candidate)).strip("_") or "stage_output"
    return sanitized[:64]
