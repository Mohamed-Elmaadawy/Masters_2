"""Which (provider, model, OutputMode) combinations are known to work.

Deliberately free of `requests` (or any other adapter-only import): orchestrator/
config.py's resolve_run_config() calls supports_output_mode() to validate a run's
requested output mode BEFORE reading any API key or constructing any adapter (see
design/DESIGN_NOTES.md, "Run config, provider adapters, CLI HumanFns") -- that ordering
only holds if this module has no transitive dependency on the provider SDKs/HTTP layer.

OutputMode itself lives in design/schemas.py, not here -- StageConfig.output_mode is
typed with it directly (not persisted as a bare string), so this module imports the
same enum rather than defining a second one that could drift from it.

Default is DENY, not allow: an (provider, model, output_mode) combination not found in
the tables below is rejected, even if it might well work in practice. The tables record
only what a fetched, dated source actually said on 2026-08-09 -- see each table's own
citation. This is deliberately narrower than "true capability" and will reject some
models that really do support a mode; that's the intended, conservative failure mode
(reject, don't guess) rather than the reverse.
"""

from __future__ import annotations

from design.schemas import OutputMode

# ---------------------------------------------------------------------------------
# Gemini
#
# Source: https://ai.google.dev/gemini-api/docs/structured-output (fetched 2026-08-09).
# That page's code examples name "gemini-3.6-flash" and "gemini-3.1-pro-preview" as
# models used with generationConfig.response_mime_type / response_schema (field names
# themselves confirmed separately against
# https://ai.google.dev/api/generate-content, fetched 2026-08-09). The page states no
# exhaustive model list and notes general constraints ("not all JSON Schema features
# are supported", "very large or deeply nested schemas may be rejected") that apply on
# top of this allowlist, not instead of it.
#
# "gemini-2.0-flash" is used elsewhere in this repo (design/schemas.py, design/
# test_schemas.py) as an example model string, but that is NOT evidence it supports
# structured output -- deliberately left off this list rather than assumed onto it.
# ---------------------------------------------------------------------------------
_GEMINI_JSON_CAPABLE: frozenset[str] = frozenset({
    "gemini-3.6-flash",
    "gemini-3.1-pro-preview",
})

# ---------------------------------------------------------------------------------
# Groq
#
# Source: https://console.groq.com/docs/structured-outputs (fetched 2026-08-09).
# json_schema (strict, schema-guaranteed): "openai/gpt-oss-20b", "openai/gpt-oss-120b".
# json_schema (best-effort only, strict=False): "openai/gpt-oss-safeguard-20b" -- listed
# separately below since it is NOT strict-capable, only best-effort.
# json_object: the same page states plain JSON-object mode is "available on all models
# that support Structured Outputs", and a follow-up search of the same docs
# (console.groq.com/docs/structured-outputs, fetched 2026-08-09) returned: "for all
# other models, you can use JSON Object Mode ... though it may not match your schema."
# That is a genuine documented "broadly available" claim, not an assumption -- encoded
# below as allow-by-default for JSON_OBJECT specifically (and only for Groq), not as a
# general exception to this module's deny-by-default policy.
#
# Added 2026-08-09 (second correction, on review -- the first cut of this list was
# built entirely from the structured-outputs reference page above and missed these
# three; a review caught the omission and a follow-up fetch of
# https://console.groq.com/docs/changelog confirmed it): a changelog entry dated
# Jul 18, 2025 -- "Groq now supports structured outputs with JSON schema output for
# the following models: moonshotai/kimi-k2-instruct, meta-llama/llama-4-maverick-17b-
# 128e-instruct, meta-llama/llama-4-scout-17b-16e-instruct" -- predates the gpt-oss
# strict/best-effort distinction (added Aug 5, 2025, same changelog) by several weeks.
# It says responses are guaranteed to conform, but never mentions a `strict` field or
# constrained decoding for these three the way the later gpt-oss entry does -- so
# whether the `strict: true` REQUEST parameter itself is even accepted for them is
# unconfirmed. Classified best-effort-only here (strict=False) rather than assumed
# strict-capable: the risk of a request rejected for an unsupported field is worse
# than the risk of under-claiming a guarantee this project doesn't rely on either way
# (strict is a request-shape choice, not a validation this code performs itself).
# ---------------------------------------------------------------------------------
_GROQ_JSON_SCHEMA_STRICT: frozenset[str] = frozenset({
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
})
_GROQ_JSON_SCHEMA_BEST_EFFORT_ONLY: frozenset[str] = frozenset({
    "openai/gpt-oss-safeguard-20b",
    "moonshotai/kimi-k2-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "meta-llama/llama-4-scout-17b-16e-instruct",
})


def groq_json_schema_is_strict(model: str) -> bool:
    """True if `model` is documented to GUARANTEE schema compliance (strict: true) for
    OutputMode.JSON_SCHEMA; False if it's best-effort only. Only meaningful for a model
    that already passed supports_output_mode(..., OutputMode.JSON_SCHEMA)."""
    return model in _GROQ_JSON_SCHEMA_STRICT


def supports_output_mode(provider: str, model: str, output_mode: OutputMode) -> bool:
    """Pure function, no I/O: called by orchestrator/config.py's resolve_run_config()
    (primary check, before any API key is read) and defensively again by each provider
    adapter's complete() (secondary, in case something bypasses config resolution)."""
    if output_mode is OutputMode.TEXT:
        return True

    if provider == "gemini":
        return model in _GEMINI_JSON_CAPABLE

    if provider == "groq":
        if output_mode is OutputMode.JSON_OBJECT:
            return True  # documented broadly available, see module docstring above
        if output_mode is OutputMode.JSON_SCHEMA:
            return model in _GROQ_JSON_SCHEMA_STRICT or model in _GROQ_JSON_SCHEMA_BEST_EFFORT_ONLY

    return False
