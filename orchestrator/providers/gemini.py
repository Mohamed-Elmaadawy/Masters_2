"""Gemini REST adapter.

Auth header verified 2026-08-09 against
https://ai.google.dev/gemini-api/docs/text-generation: the key goes in the
`x-goog-api-key` HTTP header, never a `?key=` query parameter -- verify this again
before relying on it if implementing much later, given how fast this API surface moves.

Structured-output field name corrected 2026-08-09 (second pass): an earlier version of
this file used `generationConfig.responseSchema` and explicitly claimed
`responseJsonSchema` "does not appear" anywhere -- wrong, caused by relying on several
WebFetch summaries of the (very long, apparently inconsistently-truncated) prose
reference pages, none of which surfaced the field. Corrected by checking the generated
SDK reference instead of prose docs: https://googleapis.github.io/js-genai/release_docs/
interfaces/types.GenerateContentConfig.html (the `@google/genai` SDK's own generated
API doc, a thin wrapper over the same REST fields) gives both fields' descriptions
directly and unambiguously:
  - `responseSchema`: "Represents a select subset of an OpenAPI 3.0 schema object...
    If `response_schema` doesn't process your schema correctly, try using
    `response_json_schema` instead."
  - `responseJsonSchema`: "An alternative to `response_schema` that accepts JSON
    Schema. If set, `response_schema` must be omitted, but `response_mime_type` is
    required. While the full JSON Schema may be sent, not all features are supported."
This adapter now sends `generationConfig.responseJsonSchema` (camelCase, matching
`responseMimeType`'s casing), not `responseSchema` -- `response_schema.model_json_schema()`
output from a Pydantic model routinely contains `$defs`/`$ref` for nested/recursive
models, which an OpenAPI-3.0 subset schema is not guaranteed to accept; `responseSchema`
is left unused deliberately, not merely unimplemented. `responseSchema`/`responseJsonSchema`
are mutually exclusive per the field descriptions above -- never both set at once.

Error-shape classification below is honestly uncertain: two fetched pages on the same
day disagreed on the error body's own shape --
https://ai.google.dev/gemini-api/docs/troubleshooting showed uppercase gRPC-style
`error.status` values (RESOURCE_EXHAUSTED, UNAVAILABLE) with 429/503 as concrete
examples, while https://ai.google.dev/gemini-api/docs/api-errors showed a different,
lowercase `error.code` taxonomy (authentication, permission_denied, rate_limit_exceeded)
and said only `code`+`message` are documented, no `status` field. Rather than trust
either page over the other, _classify_gemini_error checks BOTH shapes defensively and
falls back to a pure HTTP-status heuristic if neither is recognized -- re-verify against
a live account's actual error responses before depending on this in production.
"""

from __future__ import annotations

import os
from typing import Optional

import requests

from design.schemas import OutputMode
from orchestrator.providers.base import CompletionResult, require_response_schema
from orchestrator.providers.capabilities import supports_output_mode
from orchestrator.stage_fns import StageCallFailed, StageCallFatal, StageCallPartial

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Both taxonomies observed across the two disagreeing fetches (see module docstring).
_RETRYABLE_STATUS = {"RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE_EXCEEDED", "INTERNAL"}
_FATAL_STATUS = {"UNAUTHENTICATED", "PERMISSION_DENIED", "INVALID_ARGUMENT", "NOT_FOUND",
                 "FAILED_PRECONDITION"}
_RETRYABLE_CODE = {"rate_limit_exceeded", "quota_exceeded", "service_unavailable",
                   "deadline_exceeded", "api_error", "aborted", "unimplemented"}
_FATAL_CODE = {"invalid_request", "failed_precondition", "authentication",
              "permission_denied", "not_found", "model_not_found", "out_of_range",
              "parameter_unknown", "already_exists"}


def _classify_gemini_error(status_code: int, body: Optional[dict]) -> type[Exception]:
    """Returns StageCallFailed (retryable) or StageCallFatal (retrying can't help),
    using the provider's own error detail when present, falling back to a pure
    status-code heuristic when the body doesn't parse as either observed shape."""
    error = (body or {}).get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        status = error.get("status")
        if status in _RETRYABLE_STATUS:
            return StageCallFailed
        if status in _FATAL_STATUS:
            return StageCallFatal
        code = error.get("code")
        if isinstance(code, str):
            if code in _RETRYABLE_CODE:
                return StageCallFailed
            if code in _FATAL_CODE:
                return StageCallFatal
    return StageCallFailed if (status_code == 429 or status_code >= 500) else StageCallFatal


class GeminiAdapter:
    def __init__(self, api_key: str, session: Optional[requests.Session] = None):
        self._api_key = api_key
        self._session = session or requests.Session()

    @classmethod
    def from_env(cls) -> "GeminiAdapter":
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set -- read from the environment only, never "
                "from a config file (see design/DESIGN_NOTES.md, 'never persist secrets')")
        return cls(api_key=key)

    def complete(
        self, prompt: str, *, model: str, temperature: float, timeout_seconds: float,
        output_mode: OutputMode = OutputMode.TEXT,
        response_schema: Optional[dict] = None,
        schema_name: Optional[str] = None,
    ) -> CompletionResult:
        require_response_schema(output_mode, response_schema)  # raises StageCallFatal; no request sent

        if not supports_output_mode("gemini", model, output_mode):
            raise StageCallFatal(
                f"gemini model {model!r} is not in the verified capability table for "
                f"{output_mode.value} -- see orchestrator/providers/capabilities.py")

        generation_config: dict = {"temperature": temperature}
        if output_mode is OutputMode.JSON_OBJECT:
            generation_config["responseMimeType"] = "application/json"
        elif output_mode is OutputMode.JSON_SCHEMA:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseJsonSchema"] = response_schema

        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }

        try:
            response = self._session.post(
                _ENDPOINT.format(model=model),
                headers={"x-goog-api-key": self._api_key, "Content-Type": "application/json"},
                json=body, timeout=timeout_seconds,
            )
        except requests.Timeout as e:
            raise StageCallFailed(f"gemini request timed out: {e}") from e
        except requests.ConnectionError as e:
            raise StageCallFailed(f"gemini connection error: {e}") from e

        if response.status_code != 200:
            try:
                error_body = response.json()
            except ValueError:
                error_body = None
            # Never include response.request.headers or response.url in the message --
            # the key lives in a header on this request, and must never round-trip into
            # a log line via either of those.
            exc_cls = _classify_gemini_error(response.status_code, error_body)
            raise exc_cls(f"gemini returned HTTP {response.status_code}: "
                          f"{str(error_body)[:500] if error_body else response.text[:500]}")

        try:
            payload = response.json()
        except ValueError as e:
            # No usage info is even parseable here -- genuinely no tokens to report.
            raise StageCallFailed(f"gemini returned a 200 with a non-JSON body: {e}") from e

        usage = payload.get("usageMetadata") if isinstance(payload, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        prompt_tokens = usage.get("promptTokenCount")
        completion_tokens = usage.get("candidatesTokenCount")
        tokens_known = prompt_tokens is not None and completion_tokens is not None

        try:
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            # 200 but no usable candidate -- most plausibly Gemini's safety filtering
            # removed every candidate, or the body was truncated after usage
            # accounting. Inference may well have happened (usageMetadata says so) even
            # though there's no text to show for it -- StageCallPartial preserves that
            # spend instead of silently discarding it; if usage ALSO didn't parse,
            # there is nothing to preserve and this is an ordinary retryable failure.
            if tokens_known:
                raise StageCallPartial(
                    f"gemini returned a 200 with no usable candidate content (possibly "
                    f"safety-filtered): {e}",
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens) from e
            raise StageCallFailed(
                f"gemini returned a 200 with no usable candidate content and no "
                f"usable usageMetadata: {e}") from e

        if not tokens_known:
            # Candidate text parsed but usage didn't -- retrying may get a response
            # with usage info this time; no tokens are known to report either way.
            raise StageCallFailed(
                "gemini returned candidate text but no usable usageMetadata "
                f"(promptTokenCount={prompt_tokens!r}, candidatesTokenCount={completion_tokens!r})")

        return CompletionResult(text=text, prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens, output_mode=output_mode)
