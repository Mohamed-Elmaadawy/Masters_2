"""Groq REST adapter (OpenAI-compatible chat/completions endpoint).

Auth header verified 2026-08-09 against https://console.groq.com/docs/api-reference:
standard `Authorization: Bearer <key>` -- the stable, well-documented mechanism, high
confidence.

Error shape verified 2026-08-09 against https://console.groq.com/docs/errors:
`{"error": {"message": str, "type": str}}`. HTTP-status-to-category mapping from the
same page: 429 rate limiting (retryable), 401 authentication failure (fatal), 400/422
invalid request (fatal in general), 500/502/503 server errors (retryable).

Structured-output request shape verified 2026-08-09, directly, against
https://console.groq.com/docs/structured-outputs -- an earlier version of this file put
`strict` directly under `response_format`; the actual documented shape nests it (and a
required `name`) inside `response_format.json_schema`:

    {"response_format": {"type": "json_schema",
                         "json_schema": {"name": "...", "strict": true, "schema": {}}}}

Strict-vs-best-effort model support and the best-effort schema-mismatch retry guidance
are from the same fetch: strict (schema-guaranteed) is documented for
"openai/gpt-oss-20b"/"openai/gpt-oss-120b"; "openai/gpt-oss-safeguard-20b" is best-effort
only. In best-effort mode, a generated response that doesn't match the schema comes back
as HTTP 400 with error.type="invalid_request_error" and the message "Generated JSON does
not match the expected schema. Please adjust your prompt." -- the docs explicitly
recommend retrying this specific failure rather than treating it as a fatal malformed
request, since it's the model's non-deterministic output, not the request itself, that's
at fault (see _classify_groq_error).
"""

from __future__ import annotations

import os
from typing import Optional

import requests

from design.schemas import OutputMode
from orchestrator.providers.base import CompletionResult, default_schema_name, require_response_schema
from orchestrator.providers.capabilities import groq_json_schema_is_strict, supports_output_mode
from orchestrator.stage_fns import StageCallFailed, StageCallFatal, StageCallPartial

_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

_RETRYABLE_TYPE = {"rate_limit_error", "api_error", "server_error", "overloaded_error"}
_FATAL_TYPE = {"invalid_request_error", "authentication_error", "permission_error"}

# Documented verbatim (console.groq.com/docs/structured-outputs, fetched 2026-08-09) as
# the best-effort-mode message for a generated-output/schema mismatch. Substring match,
# lowercased, so minor wording drift (punctuation, trailing text) doesn't break it.
_SCHEMA_MISMATCH_MARKER = "does not match the expected schema"


def _classify_groq_error(
    status_code: int, body: Optional[dict], *, output_mode: OutputMode, strict: bool,
) -> type[Exception]:
    """Returns StageCallFailed (retryable) or StageCallFatal, preferring the provider's
    own error.type when present, folding in request context (output_mode, strict) to
    single out best-effort JSON Schema's documented-retryable mismatch case from an
    otherwise-fatal 400/invalid_request_error, and falling back to the documented
    status-code mapping when no error.type is present at all."""
    error = (body or {}).get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        error_type = error.get("type")
        message = str(error.get("message") or "").lower()
        if (status_code == 400 and error_type == "invalid_request_error"
                and output_mode is OutputMode.JSON_SCHEMA and not strict
                and _SCHEMA_MISMATCH_MARKER in message):
            return StageCallFailed
        if error_type in _RETRYABLE_TYPE:
            return StageCallFailed
        if error_type in _FATAL_TYPE:
            return StageCallFatal
    return StageCallFailed if (status_code == 429 or status_code >= 500) else StageCallFatal


class GroqAdapter:
    def __init__(self, api_key: str, session: Optional[requests.Session] = None):
        self._api_key = api_key
        self._session = session or requests.Session()

    @classmethod
    def from_env(cls) -> "GroqAdapter":
        key = os.environ.get("GROQ_API_KEY")
        if not key:
            raise RuntimeError(
                "GROQ_API_KEY is not set -- read from the environment only, never from "
                "a config file (see design/DESIGN_NOTES.md, 'never persist secrets')")
        return cls(api_key=key)

    def complete(
        self, prompt: str, *, model: str, temperature: float, timeout_seconds: float,
        output_mode: OutputMode = OutputMode.TEXT,
        response_schema: Optional[dict] = None,
        schema_name: Optional[str] = None,
    ) -> CompletionResult:
        require_response_schema(output_mode, response_schema)  # raises StageCallFatal; no request sent

        if not supports_output_mode("groq", model, output_mode):
            raise StageCallFatal(
                f"groq model {model!r} is not in the verified capability table for "
                f"{output_mode.value} -- see orchestrator/providers/capabilities.py")

        strict = groq_json_schema_is_strict(model) if output_mode is OutputMode.JSON_SCHEMA else False

        body: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if output_mode is OutputMode.JSON_OBJECT:
            body["response_format"] = {"type": "json_object"}
        elif output_mode is OutputMode.JSON_SCHEMA:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": default_schema_name(response_schema, schema_name),
                    "strict": strict,
                    "schema": response_schema,
                },
            }

        try:
            response = self._session.post(
                _ENDPOINT,
                headers={"Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json"},
                json=body, timeout=timeout_seconds,
            )
        except requests.Timeout as e:
            raise StageCallFailed(f"groq request timed out: {e}") from e
        except requests.ConnectionError as e:
            raise StageCallFailed(f"groq connection error: {e}") from e
        except requests.RequestException as e:
            # Catch-all for any other requests exception (SSLError,
            # ChunkedEncodingError, TooManyRedirects, ...) -- ProviderAdapter.complete's
            # own contract (orchestrator/providers/base.py) says it never raises
            # anything else for a failed call; Timeout/ConnectionError are the two
            # common, specifically-worth-naming cases, not the only ones requests can
            # raise before a response is ever received.
            raise StageCallFailed(f"groq request failed: {e}") from e

        if response.status_code != 200:
            try:
                error_body = response.json()
            except ValueError:
                error_body = None
            exc_cls = _classify_groq_error(response.status_code, error_body,
                                           output_mode=output_mode, strict=strict)
            raise exc_cls(f"groq returned HTTP {response.status_code}: "
                          f"{str(error_body)[:500] if error_body else response.text[:500]}")

        try:
            payload = response.json()
        except ValueError as e:
            raise StageCallFailed(f"groq returned a 200 with a non-JSON body: {e}") from e

        usage = payload.get("usage") if isinstance(payload, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        tokens_known = prompt_tokens is not None and completion_tokens is not None

        try:
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            if tokens_known:
                raise StageCallPartial(
                    f"groq returned a 200 with no usable choice content: {e}",
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens) from e
            raise StageCallFailed(
                f"groq returned a 200 with no usable choice content and no usable "
                f"usage: {e}") from e

        if not tokens_known:
            raise StageCallFailed(
                "groq returned choice content but no usable usage "
                f"(prompt_tokens={prompt_tokens!r}, completion_tokens={completion_tokens!r})")

        return CompletionResult(text=text, prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens, output_mode=output_mode)
