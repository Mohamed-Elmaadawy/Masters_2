"""
Regression tests for orchestrator/providers/. Run after any change there:

    python -m orchestrator.test_providers

Plain script, no pytest, same convention as design/test_schemas.py. Zero live network
calls -- a fake requests.Session stub captures outgoing requests and returns scripted
responses, so this never burns real API quota and never depends on network access.

Every assertion about request-body SHAPE here is checked against the official REST
field names as fetched 2026-08-09 (see gemini.py/groq.py module docstrings for exact
source URLs and quotes) -- these tests assert the documented shape independently, not
"whatever the implementation currently does": a prior version of this file asserted
snake_case Gemini fields and a flatter Groq json_schema shape, both wrong, and both
would have passed against a same-shaped-but-wrong implementation forever.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

import requests

from design.schemas import OutputMode
from orchestrator.providers.gemini import GeminiAdapter
from orchestrator.providers.groq import GroqAdapter
from orchestrator.stage_fns import StageCallFailed, StageCallFatal, StageCallPartial

PASSED = 0
FAILED: list[str] = []


def ok(label: str, condition: bool = True) -> None:
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILED.append(label)
        print(f"    FAIL  {label}")


def section(name: str) -> None:
    print(f"\n{name}")


class FakeResponse:
    def __init__(self, status_code: int, json_body: dict = None, text: str = ""):
        self.status_code = status_code
        self._json = json_body
        self.text = text if text else str(json_body)

    def json(self) -> dict:
        if self._json is None:
            raise ValueError("response body is not JSON")
        return self._json


class FakeSession:
    """Stands in for requests.Session -- .post() never touches the network. Each
    scripted response is popped in order; an Exception instance is raised instead of
    returned, mirroring test_harness.py's Scripted fixture."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.requests: list[dict] = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.requests.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


@contextmanager
def env_var(name: str, value):
    """Sets os.environ[name] = value for the duration of the block (or deletes it
    entirely if value is None), restoring whatever was there before on exit --
    from_env() reads the real environment directly, so this is the only way to drive
    its three paths (set, missing, empty-string) without a live GEMINI_API_KEY/
    GROQ_API_KEY actually being present in the test environment."""
    had_value = name in os.environ
    old_value = os.environ.get(name)
    try:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
        yield
    finally:
        if had_value:
            os.environ[name] = old_value
        else:
            os.environ.pop(name, None)


GEMINI_SUCCESS = FakeResponse(200, {
    "candidates": [{"content": {"parts": [{"text": "hello from gemini"}]}}],
    "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 4},
})
GROQ_SUCCESS = FakeResponse(200, {
    "choices": [{"message": {"content": "hello from groq"}}],
    "usage": {"prompt_tokens": 8, "completion_tokens": 3},
})


# ---------------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------------

def test_gemini_key_in_header_not_url() -> None:
    section("Gemini: key sent via header, never in URL")
    session = FakeSession([GEMINI_SUCCESS])
    adapter = GeminiAdapter(api_key="SECRET-KEY", session=session)
    adapter.complete("hi", model="gemini-3.6-flash", temperature=1.0, timeout_seconds=30)
    req = session.requests[0]
    ok("key is in the x-goog-api-key header", req["headers"]["x-goog-api-key"] == "SECRET-KEY")
    ok("key does not appear in the URL", "SECRET-KEY" not in req["url"])


def test_gemini_successful_extraction() -> None:
    section("Gemini: successful text/token extraction")
    session = FakeSession([GEMINI_SUCCESS])
    adapter = GeminiAdapter(api_key="k", session=session)
    result = adapter.complete("hi", model="gemini-3.6-flash", temperature=1.0, timeout_seconds=30)
    ok("text extracted", result.text == "hello from gemini")
    ok("prompt_tokens extracted", result.prompt_tokens == 12)
    ok("completion_tokens extracted", result.completion_tokens == 4)
    ok("output_mode recorded as requested (TEXT, the default)", result.output_mode is OutputMode.TEXT)


def test_gemini_malformed_with_usage_is_partial() -> None:
    section("Gemini: a 200 with no usable candidate but real usage preserves tokens")
    for label, bad in [
        ("empty candidates list", FakeResponse(200, {
            "candidates": [], "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 0}})),
        ("missing content.parts (safety-filtered shape)", FakeResponse(200, {
            "candidates": [{"content": {}}],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 0}})),
    ]:
        session = FakeSession([bad])
        adapter = GeminiAdapter(api_key="k", session=session)
        try:
            adapter.complete("hi", model="gemini-3.6-flash", temperature=1.0, timeout_seconds=30)
            ok(f"{label} raises StageCallPartial", False)
        except StageCallPartial as e:
            ok(f"{label} raises StageCallPartial", True)
            ok(f"{label}: prompt_tokens preserved", e.prompt_tokens is not None)
            ok(f"{label}: completion_tokens preserved", e.completion_tokens is not None)
        except Exception as e:
            ok(f"{label} raises StageCallPartial (got {type(e).__name__})", False)


def test_gemini_malformed_without_usage_is_failed() -> None:
    section("Gemini: a 200 with no usable candidate AND no usage has nothing to preserve")
    for label, bad in [
        ("missing usageMetadata entirely", FakeResponse(200, {"candidates": []})),
        ("non-JSON 200 body", FakeResponse(200, None, text="not json")),
        ("candidate text present but usageMetadata missing", FakeResponse(200, {
            "candidates": [{"content": {"parts": [{"text": "x"}]}}]})),
    ]:
        session = FakeSession([bad])
        adapter = GeminiAdapter(api_key="k", session=session)
        try:
            adapter.complete("hi", model="gemini-3.6-flash", temperature=1.0, timeout_seconds=30)
            ok(f"{label} raises StageCallFailed", False)
        except StageCallFailed:
            ok(f"{label} raises StageCallFailed", True)
        except Exception as e:
            ok(f"{label} raises StageCallFailed (got {type(e).__name__})", False)


def test_gemini_error_classification_status_shape() -> None:
    section("Gemini: error classification, uppercase status shape")
    cases = [
        (429, {"error": {"status": "RESOURCE_EXHAUSTED"}}, StageCallFailed),
        (503, {"error": {"status": "UNAVAILABLE"}}, StageCallFailed),
        (401, {"error": {"status": "UNAUTHENTICATED"}}, StageCallFatal),
        (403, {"error": {"status": "PERMISSION_DENIED"}}, StageCallFatal),
        (400, {"error": {"status": "INVALID_ARGUMENT"}}, StageCallFatal),
    ]
    for status_code, body, expected in cases:
        session = FakeSession([FakeResponse(status_code, body)])
        adapter = GeminiAdapter(api_key="k", session=session)
        try:
            adapter.complete("hi", model="gemini-3.6-flash", temperature=1.0, timeout_seconds=30)
            ok(f"HTTP {status_code} {body['error']['status']} -> {expected.__name__}", False)
        except expected:
            ok(f"HTTP {status_code} {body['error']['status']} -> {expected.__name__}", True)
        except Exception as e:
            ok(f"HTTP {status_code} {body['error']['status']} -> {expected.__name__} "
               f"(got {type(e).__name__})", False)


def test_gemini_error_classification_code_shape() -> None:
    section("Gemini: error classification, lowercase code shape (second observed doc shape)")
    cases = [
        (429, {"error": {"code": "rate_limit_exceeded"}}, StageCallFailed),
        (401, {"error": {"code": "authentication"}}, StageCallFatal),
        (404, {"error": {"code": "model_not_found"}}, StageCallFatal),
    ]
    for status_code, body, expected in cases:
        session = FakeSession([FakeResponse(status_code, body)])
        adapter = GeminiAdapter(api_key="k", session=session)
        try:
            adapter.complete("hi", model="gemini-3.6-flash", temperature=1.0, timeout_seconds=30)
            ok(f"HTTP {status_code} code={body['error']['code']} -> {expected.__name__}", False)
        except expected:
            ok(f"HTTP {status_code} code={body['error']['code']} -> {expected.__name__}", True)


def test_gemini_error_classification_fallback() -> None:
    section("Gemini: unparseable body falls back to status-code heuristic")
    session = FakeSession([FakeResponse(500, None, text="upstream proxy error")])
    adapter = GeminiAdapter(api_key="k", session=session)
    try:
        adapter.complete("hi", model="gemini-3.6-flash", temperature=1.0, timeout_seconds=30)
        ok("unparseable 500 body -> StageCallFailed", False)
    except StageCallFailed:
        ok("unparseable 500 body -> StageCallFailed", True)

    session = FakeSession([FakeResponse(400, None, text="malformed request")])
    adapter = GeminiAdapter(api_key="k", session=session)
    try:
        adapter.complete("hi", model="gemini-3.6-flash", temperature=1.0, timeout_seconds=30)
        ok("unparseable 400 body -> StageCallFatal", False)
    except StageCallFatal:
        ok("unparseable 400 body -> StageCallFatal", True)


def test_gemini_transport_exceptions() -> None:
    section("Gemini: requests.Timeout/ConnectionError map to StageCallFailed")
    for exc in (requests.Timeout("timed out"), requests.ConnectionError("refused")):
        session = FakeSession([exc])
        adapter = GeminiAdapter(api_key="k", session=session)
        try:
            adapter.complete("hi", model="gemini-3.6-flash", temperature=1.0, timeout_seconds=30)
            ok(f"{type(exc).__name__} -> StageCallFailed", False)
        except StageCallFailed:
            ok(f"{type(exc).__name__} -> StageCallFailed", True)


def test_gemini_generic_request_exception() -> None:
    """Any other requests exception (not just Timeout/ConnectionError) must still map
    to StageCallFailed -- base.py's ProviderAdapter.complete contract says "never
    raises anything else for a failed call". Uses requests.exceptions.SSLError, a real
    RequestException subclass distinct from Timeout/ConnectionError, so this cannot
    pass by accident via either of those two excepts."""
    section("Gemini: a generic requests.RequestException also maps to StageCallFailed")
    session = FakeSession([requests.exceptions.SSLError("cert verify failed")])
    adapter = GeminiAdapter(api_key="k", session=session)
    try:
        adapter.complete("hi", model="gemini-3.6-flash", temperature=1.0, timeout_seconds=30)
        ok("SSLError -> StageCallFailed", False)
    except StageCallFailed:
        ok("SSLError -> StageCallFailed", True)
    except Exception as e:
        ok(f"SSLError -> StageCallFailed (got {type(e).__name__})", False)


def test_gemini_capability_check_before_any_request() -> None:
    section("Gemini: unsupported output_mode is rejected before any request is sent")
    session = FakeSession([])  # would raise IndexError if .post() were ever called
    adapter = GeminiAdapter(api_key="k", session=session)
    try:
        adapter.complete("hi", model="gemini-2.0-flash", temperature=1.0, timeout_seconds=30,
                         output_mode=OutputMode.JSON_SCHEMA, response_schema={"type": "object"})
        ok("unsupported combo raises StageCallFatal", False)
    except StageCallFatal:
        ok("unsupported combo raises StageCallFatal", True)
    ok("no HTTP request was ever made", session.requests == [])


def test_gemini_missing_schema_rejected_before_any_request() -> None:
    section("Gemini: JSON_SCHEMA with no response_schema is rejected before any request")
    for bad_schema in (None, {}, "not-a-dict"):
        session = FakeSession([])
        adapter = GeminiAdapter(api_key="k", session=session)
        try:
            adapter.complete("hi", model="gemini-3.6-flash", temperature=1.0, timeout_seconds=30,
                             output_mode=OutputMode.JSON_SCHEMA, response_schema=bad_schema)
            ok(f"response_schema={bad_schema!r} raises StageCallFatal", False)
        except StageCallFatal:
            ok(f"response_schema={bad_schema!r} raises StageCallFatal", True)
        ok(f"response_schema={bad_schema!r}: no HTTP request was made", session.requests == [])


def test_gemini_json_schema_request_body_uses_camel_case() -> None:
    section("Gemini: JSON_SCHEMA request body uses the documented camelCase REST fields")
    session = FakeSession([GEMINI_SUCCESS])
    adapter = GeminiAdapter(api_key="k", session=session)
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    result = adapter.complete("hi", model="gemini-3.6-flash", temperature=0.5, timeout_seconds=30,
                              output_mode=OutputMode.JSON_SCHEMA, response_schema=schema)
    gen_config = session.requests[0]["json"]["generationConfig"]
    ok("responseMimeType (camelCase) is set", gen_config.get("responseMimeType") == "application/json")
    ok("responseJsonSchema (camelCase) forwarded unchanged", gen_config.get("responseJsonSchema") == schema)
    ok("no snake_case response_mime_type leaked in", "response_mime_type" not in gen_config)
    ok("no snake_case response_json_schema leaked in", "response_json_schema" not in gen_config)
    ok("responseSchema is NOT set -- mutually exclusive with responseJsonSchema per the "
       "official field descriptions", "responseSchema" not in gen_config)
    ok("output_mode on the result matches what was requested", result.output_mode is OutputMode.JSON_SCHEMA)


def test_gemini_from_env() -> None:
    section("Gemini: from_env() reads GEMINI_API_KEY -- set, missing, empty")
    with env_var("GEMINI_API_KEY", "env-secret-key"):
        adapter = GeminiAdapter.from_env()
        ok("a set env var produces an adapter with that key", adapter._api_key == "env-secret-key")

    with env_var("GEMINI_API_KEY", None):
        try:
            GeminiAdapter.from_env()
            ok("a missing GEMINI_API_KEY raises RuntimeError", False)
        except RuntimeError:
            ok("a missing GEMINI_API_KEY raises RuntimeError", True)

    with env_var("GEMINI_API_KEY", ""):
        try:
            GeminiAdapter.from_env()
            ok("an empty-string GEMINI_API_KEY raises RuntimeError (falsy but set)", False)
        except RuntimeError:
            ok("an empty-string GEMINI_API_KEY raises RuntimeError (falsy but set)", True)


def test_gemini_json_object_request_body() -> None:
    section("Gemini: JSON_OBJECT sets responseMimeType only, no schema field")
    session = FakeSession([GEMINI_SUCCESS])
    adapter = GeminiAdapter(api_key="k", session=session)
    adapter.complete("hi", model="gemini-3.6-flash", temperature=1.0, timeout_seconds=30,
                     output_mode=OutputMode.JSON_OBJECT)
    gen_config = session.requests[0]["json"]["generationConfig"]
    ok("responseMimeType is set", gen_config.get("responseMimeType") == "application/json")
    ok("no responseSchema for JSON_OBJECT mode", "responseSchema" not in gen_config)


# ---------------------------------------------------------------------------------
# Groq
# ---------------------------------------------------------------------------------

def test_groq_from_env() -> None:
    section("Groq: from_env() reads GROQ_API_KEY -- set, missing, empty")
    with env_var("GROQ_API_KEY", "env-secret-key"):
        adapter = GroqAdapter.from_env()
        ok("a set env var produces an adapter with that key", adapter._api_key == "env-secret-key")

    with env_var("GROQ_API_KEY", None):
        try:
            GroqAdapter.from_env()
            ok("a missing GROQ_API_KEY raises RuntimeError", False)
        except RuntimeError:
            ok("a missing GROQ_API_KEY raises RuntimeError", True)

    with env_var("GROQ_API_KEY", ""):
        try:
            GroqAdapter.from_env()
            ok("an empty-string GROQ_API_KEY raises RuntimeError (falsy but set)", False)
        except RuntimeError:
            ok("an empty-string GROQ_API_KEY raises RuntimeError (falsy but set)", True)


def test_groq_key_in_header() -> None:
    section("Groq: key sent via Authorization header")
    session = FakeSession([GROQ_SUCCESS])
    adapter = GroqAdapter(api_key="SECRET-KEY", session=session)
    adapter.complete("hi", model="llama-3.3-70b-versatile", temperature=1.0, timeout_seconds=30)
    req = session.requests[0]
    ok("Authorization header is a Bearer token with the key",
       req["headers"]["Authorization"] == "Bearer SECRET-KEY")
    ok("key does not appear in the URL", "SECRET-KEY" not in req["url"])


def test_groq_successful_extraction() -> None:
    section("Groq: successful text/token extraction")
    session = FakeSession([GROQ_SUCCESS])
    adapter = GroqAdapter(api_key="k", session=session)
    result = adapter.complete("hi", model="llama-3.3-70b-versatile", temperature=1.0, timeout_seconds=30)
    ok("text extracted", result.text == "hello from groq")
    ok("prompt_tokens extracted", result.prompt_tokens == 8)
    ok("completion_tokens extracted", result.completion_tokens == 3)


def test_groq_malformed_with_usage_is_partial() -> None:
    section("Groq: a 200 with no usable choice but real usage preserves tokens")
    bad = FakeResponse(200, {"choices": [], "usage": {"prompt_tokens": 6, "completion_tokens": 0}})
    session = FakeSession([bad])
    adapter = GroqAdapter(api_key="k", session=session)
    try:
        adapter.complete("hi", model="llama-3.3-70b-versatile", temperature=1.0, timeout_seconds=30)
        ok("empty choices with usage raises StageCallPartial", False)
    except StageCallPartial as e:
        ok("empty choices with usage raises StageCallPartial", True)
        ok("prompt_tokens preserved", e.prompt_tokens == 6)
        ok("completion_tokens preserved", e.completion_tokens == 0)


def test_groq_malformed_without_usage_is_failed() -> None:
    section("Groq: malformed response with no usage has nothing to preserve")
    for label, bad in [
        ("missing usage entirely", FakeResponse(200, {"choices": [{"message": {"content": "x"}}]})),
        ("non-JSON 200 body", FakeResponse(200, None, text="not json")),
        ("empty choices, no usage", FakeResponse(200, {"choices": [], "usage": {}})),
    ]:
        session = FakeSession([bad])
        adapter = GroqAdapter(api_key="k", session=session)
        try:
            adapter.complete("hi", model="llama-3.3-70b-versatile", temperature=1.0, timeout_seconds=30)
            ok(f"{label} raises StageCallFailed", False)
        except StageCallFailed:
            ok(f"{label} raises StageCallFailed", True)


def test_groq_transport_exceptions() -> None:
    section("Groq: requests.Timeout/ConnectionError map to StageCallFailed")
    for exc in (requests.Timeout("timed out"), requests.ConnectionError("refused")):
        session = FakeSession([exc])
        adapter = GroqAdapter(api_key="k", session=session)
        try:
            adapter.complete("hi", model="llama-3.3-70b-versatile", temperature=1.0, timeout_seconds=30)
            ok(f"{type(exc).__name__} -> StageCallFailed", False)
        except StageCallFailed:
            ok(f"{type(exc).__name__} -> StageCallFailed", True)


def test_groq_generic_request_exception() -> None:
    """Groq's twin of test_gemini_generic_request_exception -- same source-level fix
    (a bare requests.RequestException catch-all after the specific Timeout/
    ConnectionError excepts), same reasoning, checked independently per CLAUDE.md's
    "a fix at one level needs checking at the other" rule."""
    section("Groq: a generic requests.RequestException also maps to StageCallFailed")
    session = FakeSession([requests.exceptions.SSLError("cert verify failed")])
    adapter = GroqAdapter(api_key="k", session=session)
    try:
        adapter.complete("hi", model="llama-3.3-70b-versatile", temperature=1.0, timeout_seconds=30)
        ok("SSLError -> StageCallFailed", False)
    except StageCallFailed:
        ok("SSLError -> StageCallFailed", True)
    except Exception as e:
        ok(f"SSLError -> StageCallFailed (got {type(e).__name__})", False)


def test_groq_error_classification() -> None:
    section("Groq: error classification by error.type, with status fallback")
    cases = [
        (429, {"error": {"type": "rate_limit_error", "message": "x"}}, StageCallFailed),
        (500, {"error": {"type": "api_error", "message": "x"}}, StageCallFailed),
        (401, {"error": {"type": "authentication_error", "message": "x"}}, StageCallFatal),
        (400, {"error": {"type": "invalid_request_error", "message": "malformed JSON body"}}, StageCallFatal),
    ]
    for status_code, body, expected in cases:
        session = FakeSession([FakeResponse(status_code, body)])
        adapter = GroqAdapter(api_key="k", session=session)
        try:
            adapter.complete("hi", model="llama-3.3-70b-versatile", temperature=1.0, timeout_seconds=30)
            ok(f"HTTP {status_code} type={body['error']['type']} -> {expected.__name__}", False)
        except expected:
            ok(f"HTTP {status_code} type={body['error']['type']} -> {expected.__name__}", True)

    session = FakeSession([FakeResponse(502, None, text="bad gateway")])
    adapter = GroqAdapter(api_key="k", session=session)
    try:
        adapter.complete("hi", model="llama-3.3-70b-versatile", temperature=1.0, timeout_seconds=30)
        ok("unparseable 502 body falls back to StageCallFailed", False)
    except StageCallFailed:
        ok("unparseable 502 body falls back to StageCallFailed", True)


# test_groq_best_effort_schema_mismatch_is_retryable removed (2026-08-09): it drove
# GroqAdapter.complete() with output_mode=JSON_SCHEMA on previously-allowlisted models,
# expecting the request to actually be attempted so the best-effort-mismatch error-
# classification logic below it could be exercised. supports_output_mode() now returns
# False for provider=groq + JSON_SCHEMA unconditionally (orchestrator/providers/
# capabilities.py, v1 scope decision -- see design/DESIGN_NOTES.md, "Real stage
# functions -- prompt provenance"), so complete() raises StageCallFatal before ever
# reaching that code -- the scenario this test drove can no longer occur through the
# public API. The classification code itself is untouched, not deleted; it is simply
# unreachable for v1, same as the request-body-shape code test_groq_json_schema_
# request_body_shape used to cover (also removed for the identical reason).


def test_groq_missing_schema_rejected_before_any_request() -> None:
    section("Groq: JSON_SCHEMA with no response_schema is rejected before any request")
    for bad_schema in (None, {}, "not-a-dict"):
        session = FakeSession([])
        adapter = GroqAdapter(api_key="k", session=session)
        try:
            adapter.complete("hi", model="openai/gpt-oss-20b", temperature=1.0, timeout_seconds=30,
                             output_mode=OutputMode.JSON_SCHEMA, response_schema=bad_schema)
            ok(f"response_schema={bad_schema!r} raises StageCallFatal", False)
        except StageCallFatal:
            ok(f"response_schema={bad_schema!r} raises StageCallFatal", True)
        ok(f"response_schema={bad_schema!r}: no HTTP request was made", session.requests == [])


def test_groq_json_object_allowed_broadly() -> None:
    section("Groq: JSON_OBJECT is allowed on an arbitrary model (documented broadly available)")
    session = FakeSession([GROQ_SUCCESS])
    adapter = GroqAdapter(api_key="k", session=session)
    adapter.complete("hi", model="llama-3.3-70b-versatile", temperature=1.0, timeout_seconds=30,
                     output_mode=OutputMode.JSON_OBJECT)
    ok("response_format json_object is in the request body",
       session.requests[0]["json"]["response_format"] == {"type": "json_object"})


def test_groq_json_schema_rejected_for_every_model() -> None:
    """Was 'allowlist-only, unlike JSON_OBJECT' -- rewritten (2026-08-09) now that
    supports_output_mode() rejects provider=groq + JSON_SCHEMA unconditionally for v1
    (orchestrator/providers/capabilities.py), not just for models outside an allowlist.
    Covers a previously-strict-capable model, a previously-best-effort-capable model,
    and an arbitrary one -- all three now rejected identically, before any request."""
    section("Groq: JSON_SCHEMA is rejected for every model, not just unsupported ones (v1)")
    for model in ("openai/gpt-oss-20b", "openai/gpt-oss-safeguard-20b", "llama-3.3-70b-versatile"):
        session = FakeSession([])
        adapter = GroqAdapter(api_key="k", session=session)
        try:
            adapter.complete("hi", model=model, temperature=1.0, timeout_seconds=30,
                             output_mode=OutputMode.JSON_SCHEMA, response_schema={"type": "object"})
            ok(f"{model}: rejected before any request", False)
        except StageCallFatal:
            ok(f"{model}: rejected before any request", True)
        ok(f"{model}: no HTTP request was made", session.requests == [])


def main() -> int:
    print("=" * 72)
    print("provider adapter regression (no live network)")
    print("=" * 72)
    for fn in (
        test_gemini_key_in_header_not_url, test_gemini_successful_extraction,
        test_gemini_malformed_with_usage_is_partial, test_gemini_malformed_without_usage_is_failed,
        test_gemini_error_classification_status_shape,
        test_gemini_error_classification_code_shape, test_gemini_error_classification_fallback,
        test_gemini_transport_exceptions, test_gemini_generic_request_exception,
        test_gemini_capability_check_before_any_request,
        test_gemini_missing_schema_rejected_before_any_request,
        test_gemini_json_schema_request_body_uses_camel_case, test_gemini_json_object_request_body,
        test_gemini_from_env,
        test_groq_from_env, test_groq_key_in_header, test_groq_successful_extraction,
        test_groq_malformed_with_usage_is_partial, test_groq_malformed_without_usage_is_failed,
        test_groq_transport_exceptions, test_groq_generic_request_exception,
        test_groq_error_classification,
        test_groq_missing_schema_rejected_before_any_request,
        test_groq_json_object_allowed_broadly, test_groq_json_schema_rejected_for_every_model,
    ):
        fn()
    print("\n" + "=" * 72)
    if FAILED:
        print(f"{PASSED} passed, {len(FAILED)} FAILED:")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print(f"{PASSED} checks passed, 0 failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
