"""Validated YAML run configuration: RunConfig (what an operator authors) and
ResolvedRunConfig (what actually ran, fully concrete, persisted for reproducibility).

See design/DESIGN_NOTES.md, "Run config, provider adapters, CLI HumanFns" for the full
reasoning; this docstring only orients, it doesn't restate.

Deliberately independent of `requests`: this module (and orchestrator/providers/
capabilities.py, which it uses for the output-mode capability check) never imports
anything that touches the network, so a config can be fully validated -- including the
per-stage capability check -- before any API key is read or any adapter constructed.
`orchestrator.pipeline` is imported for `Throttle` and `atomic_write_text`, neither of
which has a `requests` dependency.

Two models, two purposes, never confused:
  RunConfig          -- authored (YAML). Optional per-stage overrides, prompt FILE
                        PATHS (never a hand-typed hash), required rate-limit coverage.
  ResolvedRunConfig   -- fully concrete. Every stage's provider/model/temperature/
                        output_mode/timeout resolved, every prompt hashed from its
                        actual file content, every path absolute and normalized. This,
                        not RunConfig, is what write_run_config persists.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from design.schemas import ALL_STAGES, NonEmptyStr, OutputMode, RunMetadata, StageConfig, prompt_fingerprint
from orchestrator.pipeline import Throttle, atomic_write_text
from orchestrator.providers.capabilities import supports_output_mode

_SCALAR_FIELDS = ("provider", "model", "prompt_version", "temperature", "timeout_seconds", "output_mode")

_SAFE_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_run_id(v: Optional[str]) -> Optional[str]:
    """run_id becomes a directory name verbatim (run_dir_for: output_dir / run_id) --
    must be exactly one safe path component, never a path itself. Rejects '..' as a
    substring (not just as the whole value) so 'a/../../etc' style values are caught by
    the same check that catches a bare '..', and rejects '/'/'\\\\' outright rather than
    relying on the charset alone to imply it (an explicit check reads correctly even if
    the regex is ever loosened later)."""
    if v is None:
        return v
    if v in (".", "..") or "/" in v or "\\" in v or ".." in v or not _SAFE_PATH_COMPONENT.match(v):
        # '.' isn't a traversal escape the way '..' is, but output_dir / "." resolves
        # to output_dir itself, not a subdirectory of it -- run_dir_for's own
        # descendant check would (correctly) accept that as "not outside output_dir",
        # since it IS output_dir, which is exactly the wrong result: every run must get
        # its own directory, and '.' would silently alias every such run onto
        # output_dir directly. Rejected here, by name, rather than relying on the
        # descendant check to catch it by accident.
        raise ValueError(
            f"run_id {v!r} is not a safe single path component -- only "
            "[A-Za-z0-9._-] allowed, no '/', '\\', '..', or exactly '.'")
    return v


class RetryConfig(BaseModel):
    """Global -- not per-stage. call_stage/call_document_stage (orchestrator/
    pipeline.py) take max_attempts/backoff_seconds as single values threaded through
    one run_document() call; there is no per-stage retry mechanism in the orchestrator,
    and changing that is out of scope here. A per-stage override would be accepted by a
    schema and silently ignored by the orchestrator -- exactly the kind of gap this
    whole design is trying to close, not add."""
    model_config = ConfigDict(extra="forbid")
    max_attempts: int = Field(3, ge=1)
    initial_delay_seconds: float = Field(2.0, ge=0)
    multiplier: float = Field(2.0, ge=1)  # ge=1: <1 would shrink delays over time


class RateLimitConfig(BaseModel):
    """requests_per_minute is a required key with an optional value: the key must be
    present (see RunConfig.rate_limits's coverage check below) so a model can never be
    silently unthrottled by omission; the value itself can be null, meaning the
    operator deliberately chose not to throttle it."""
    model_config = ConfigDict(extra="forbid")
    requests_per_minute: Optional[float] = Field(...)

    @field_validator("requests_per_minute")
    @classmethod
    def _positive_or_none(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError(
                "requests_per_minute must be > 0, or null for deliberately unthrottled")
        return v


class StageDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["gemini", "groq"]
    model: NonEmptyStr
    prompt_version: NonEmptyStr
    temperature: float = Field(1.0, ge=0.0, le=2.0)
    timeout_seconds: float = Field(30.0, gt=0)
    output_mode: OutputMode = OutputMode.TEXT


class StageOverride(BaseModel):
    """Every field optional and scalar -- falls back to StageDefaults's matching field
    when omitted. No nested (multi-field) object here: if one is ever added, it must
    merge field-by-field against StageDefaults's corresponding object, never replace it
    wholesale (a partial nested override silently resetting the rest of that object's
    fields to StageDefaults's own inner defaults would be the bug to avoid)."""
    model_config = ConfigDict(extra="forbid")
    provider: Optional[Literal["gemini", "groq"]] = None
    model: Optional[NonEmptyStr] = None
    prompt_version: Optional[NonEmptyStr] = None
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    timeout_seconds: Optional[float] = Field(None, gt=0)
    output_mode: Optional[OutputMode] = None


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: Optional[NonEmptyStr] = None
    output_dir: NonEmptyStr = "runs"        # resolved relative to the config file's own dir
    max_revisions: int = Field(3, ge=2)     # pipeline.run_requirement already requires >=2
    retry: RetryConfig = Field(default_factory=RetryConfig)
    rate_limits: dict[str, RateLimitConfig]                # keyed "provider/model", coverage checked at resolve time
    defaults: StageDefaults
    stages: dict[str, StageOverride] = Field(default_factory=dict)
    prompts: dict[str, NonEmptyStr]         # REQUIRED, exactly ALL_STAGES, file paths -- never a hash

    @field_validator("run_id")
    @classmethod
    def _run_id_is_safe(cls, v: Optional[str]) -> Optional[str]:
        return _validate_run_id(v)

    @model_validator(mode="after")
    def _stage_keys_are_known(self) -> "RunConfig":
        if unknown := sorted(set(self.stages) - set(ALL_STAGES)):
            raise ValueError(f"stages contains unknown stage name(s): {unknown}")
        return self

    @model_validator(mode="after")
    def _prompts_cover_every_stage(self) -> "RunConfig":
        given, expected = set(self.prompts), set(ALL_STAGES)
        if missing := sorted(expected - given):
            raise ValueError(f"prompts is missing an entry for: {missing}")
        if unknown := sorted(given - expected):
            raise ValueError(f"prompts contains unknown stage name(s): {unknown}")
        return self


def _resolved_scalars(config: RunConfig, stage: str) -> dict:
    """Merges StageDefaults + stages.get(stage) for one stage, field by field. Direct
    attribute access, not model_dump(): a dump-and-reread round trip risks losing the
    OutputMode enum's identity (`is` comparisons in capabilities.py would then compare
    an enum member against a plain string that happens to be `==`-equal but not the
    same object) -- reading attributes directly off the Pydantic models avoids that
    ambiguity entirely."""
    override = config.stages.get(stage)
    resolved = {}
    for field_name in _SCALAR_FIELDS:
        value = getattr(override, field_name, None) if override is not None else None
        resolved[field_name] = value if value is not None else getattr(config.defaults, field_name)
    return resolved


class ResolvedStageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["gemini", "groq"]
    model: NonEmptyStr
    prompt_version: NonEmptyStr
    prompt_hash: NonEmptyStr          # computed from prompt_path's actual content, never authored
    prompt_path: Path                 # absolute, normalized -- which file produced prompt_hash
    # Bounds restored (2026-08-09): a first version of this model left temperature/
    # timeout_seconds unbounded, on the theory that RunConfig/StageDefaults/StageOverride
    # had already checked them. That reasoning doesn't survive read_resolved_run_config:
    # a hand-tampered run_config.json on disk never passes through RunConfig's
    # validators at all, only ResolvedRunConfig's (via model_validate_json) -- so the
    # resolved model must enforce its own bounds independently, the same way the
    # authored one does, or a corrupted persisted file loads as if it were fine.
    temperature: float = Field(..., ge=0.0, le=2.0)
    timeout_seconds: float = Field(..., gt=0)
    output_mode: OutputMode


class ResolvedRunConfig(BaseModel):
    """Fully concrete -- this, not RunConfig, is what write_run_config persists.
    Every path absolute and normalized, every prompt hashed, every stage's scalars
    merged, every distinct (provider, model) covered by rate_limits -- and, on load,
    validated as strongly as RunConfig itself (see ResolvedStageConfig's docstring for
    why: read_resolved_run_config never passes a loaded file through RunConfig's own
    validators)."""
    model_config = ConfigDict(extra="forbid")
    run_id: NonEmptyStr
    output_dir: Path                  # absolute, normalized
    max_revisions: int = Field(..., ge=2)   # pipeline.run_requirement's own floor
    retry: RetryConfig
    rate_limits: dict[str, RateLimitConfig]
    stages: dict[str, ResolvedStageConfig]   # exactly ALL_STAGES

    @field_validator("run_id")
    @classmethod
    def _run_id_is_safe(cls, v: str) -> str:
        # Same check as RunConfig's -- required here too, not just there, because
        # read_resolved_run_config loads a persisted run_config.json straight through
        # this model and never through RunConfig's own validator (see
        # ResolvedStageConfig's docstring for the general version of this reasoning).
        return _validate_run_id(v)

    @model_validator(mode="after")
    def _stages_cover_exactly_all_stages(self) -> "ResolvedRunConfig":
        given, expected = set(self.stages), set(ALL_STAGES)
        if missing := sorted(expected - given):
            raise ValueError(f"stages is missing an entry for: {missing}")
        if unknown := sorted(given - expected):
            raise ValueError(f"stages contains unknown stage name(s): {unknown}")
        return self

    @model_validator(mode="after")
    def _rate_limits_match_resolved_models_exactly(self) -> "ResolvedRunConfig":
        # Exact match, not just coverage: an entry for a model nothing actually uses is
        # either a typo (meant for a model that IS in use, silently not applying) or a
        # stale leftover from a since-changed config -- both are worth rejecting rather
        # than carrying forward quietly. This validator is what makes the "no unused
        # rate_limits keys" decision (resolve_run_config asked for one) real: it applies
        # equally whether the ResolvedRunConfig came from a fresh resolve_run_config()
        # call or from read_resolved_run_config() loading a file off disk.
        distinct_models = {f"{sc.provider}/{sc.model}" for sc in self.stages.values()}
        given = set(self.rate_limits)
        if missing := sorted(distinct_models - given):
            raise ValueError(
                f"rate_limits is missing an entry for: {missing} -- add "
                "{requests_per_minute: <n>} or {requests_per_minute: null} (deliberately "
                "unthrottled) for each")
        if unused := sorted(given - distinct_models):
            raise ValueError(
                f"rate_limits contains entries for model(s) not in use by any stage: "
                f"{unused} -- remove them (a typo of an in-use model silently doesn't "
                "apply; a stale entry from a since-changed config is misleading either way)")
        return self


def load_run_config(path: Path) -> RunConfig:
    """Shape-only: yaml.safe_load + RunConfig.model_validate. No filesystem reads
    beyond the YAML file itself, no capability check -- that's resolve_run_config's
    job, and keeping this function to shape validation only means a syntactically
    invalid or wrongly-shaped YAML file fails immediately, before this function even
    tries to interpret paths relative to anything."""
    data = yaml.safe_load(Path(path).read_text())
    return RunConfig.model_validate(data)


def resolve_run_config(config: RunConfig, config_path: Path) -> ResolvedRunConfig:
    """config_path is the YAML file's own path, not a directory -- resolving relative
    to it (config_path.resolve().parent), not the current working directory, means the
    same YAML file resolves identically regardless of where the command is run from.

    Runs entirely before any API key is read or any adapter constructed: the
    output-mode capability check (via orchestrator.providers.capabilities, which has no
    `requests` import) happens here, per stage, before this function even reads a
    prompt file for that stage. 'Validate the complete configuration before making any
    API calls' is true of this function specifically, not just of the pipeline as a
    whole.
    """
    base_dir = Path(config_path).resolve().parent
    run_id = config.run_id or uuid.uuid4().hex
    output_dir = (base_dir / config.output_dir).resolve()

    resolved_stages: dict[str, ResolvedStageConfig] = {}
    for stage in ALL_STAGES:
        scalars = _resolved_scalars(config, stage)
        provider, model, output_mode = scalars["provider"], scalars["model"], scalars["output_mode"]

        if not supports_output_mode(provider, model, output_mode):
            raise ValueError(
                f"stage {stage!r}: {provider}/{model} is not in the verified capability "
                f"table for output_mode={output_mode.value!r} -- see "
                "orchestrator/providers/capabilities.py")

        prompt_path = (base_dir / config.prompts[stage]).resolve()
        if not prompt_path.is_file():
            raise ValueError(f"stage {stage!r}: prompt file not found: {prompt_path}")
        prompt_hash = prompt_fingerprint(prompt_path.read_text())

        resolved_stages[stage] = ResolvedStageConfig(
            provider=provider, model=model, prompt_version=scalars["prompt_version"],
            prompt_hash=prompt_hash, prompt_path=prompt_path,
            temperature=scalars["temperature"], timeout_seconds=scalars["timeout_seconds"],
            output_mode=output_mode,
        )

    # No manual rate_limits coverage check here: ResolvedRunConfig's own
    # _rate_limits_match_resolved_models_exactly validator (a "check that can't NOT
    # fire" the moment the constructor below runs) covers both missing AND unused
    # entries -- duplicating a subset of it here would be exactly the kind of check
    # that can never independently fire that CLAUDE.md warns against.
    return ResolvedRunConfig(
        run_id=run_id, output_dir=output_dir, max_revisions=config.max_revisions,
        retry=config.retry, rate_limits=dict(config.rate_limits), stages=resolved_stages,
    )


def to_run_metadata(resolved: ResolvedRunConfig, started_at: datetime) -> RunMetadata:
    """Builds design/schemas.py's own RunMetadata -- the persisted-with-the-record
    provenance schema, unchanged by this task except for StageConfig gaining
    prompt_version/temperature/output_mode (see design/DESIGN_NOTES.md). No
    RunMetadata-level temperature/prompt_version args: those fields were removed from
    RunMetadata for exactly this reason -- they now live only on each StageConfig."""
    stages = {
        name: StageConfig(
            model=f"{sc.provider}/{sc.model}", prompt_hash=sc.prompt_hash,
            prompt_version=sc.prompt_version, temperature=sc.temperature,
            output_mode=sc.output_mode,
        )
        for name, sc in resolved.stages.items()
    }
    return RunMetadata(run_id=resolved.run_id, started_at=started_at, stages=stages)


def throttle_from(resolved: ResolvedRunConfig) -> Throttle:
    """Throttle is keyed by model string (orchestrator/pipeline.py) -- the same
    "provider/model" strings to_run_metadata uses, so Throttle.wait_for_slot(model_name)
    in pipeline.py actually finds these entries. A model with an explicit `null` limit
    gets no key here, which is exactly Throttle's own "absent key = unthrottled"
    behavior -- reached deliberately (resolve_run_config required the key to exist with
    an explicit value, null or numeric), not by omission."""
    min_interval = {
        key: 60.0 / rl.requests_per_minute
        for key, rl in resolved.rate_limits.items()
        if rl.requests_per_minute is not None
    }
    return Throttle(min_interval_seconds=min_interval)


def retry_args(resolved: ResolvedRunConfig) -> tuple[int, Callable[[int], float]]:
    """Feeds directly into run_document(..., max_attempts=..., backoff_seconds=...)."""
    initial = resolved.retry.initial_delay_seconds
    multiplier = resolved.retry.multiplier
    return resolved.retry.max_attempts, (lambda attempt: initial * (multiplier ** attempt))


def run_dir_for(resolved: ResolvedRunConfig) -> Path:
    """The one place a run's directory is computed -- every caller uses this instead of
    recomputing output_dir / run_id slightly differently.

    Defense in depth: run_id already passed ResolvedRunConfig/RunConfig's own
    _run_id_is_safe field validator (single safe path component, no '/', '\\', or
    '..'), so this should already be a genuine child of output_dir. Checked again here,
    directly on the computed result, rather than trusting the field validator alone --
    this is the one function whose entire job is producing a path something else will
    read/write, so it confirms what it's about to hand out rather than assuming an
    earlier check elsewhere was airtight."""
    run_dir = (resolved.output_dir / resolved.run_id).resolve()
    output_dir = resolved.output_dir.resolve()
    if run_dir != output_dir and output_dir not in run_dir.parents:
        raise ValueError(
            f"run_dir {run_dir} would not be inside output_dir {output_dir} -- "
            f"run_id {resolved.run_id!r} is not a safe path component")
    return run_dir


def write_run_config(run_dir: Path, resolved: ResolvedRunConfig) -> None:
    """Mirrors orchestrator/pipeline.py's write_document_run: run_dir / "run_config.json"
    is a sibling of document.json. Safe to dump whole -- no field on ResolvedRunConfig
    (or RunConfig) ever holds an API key; keys are read from the environment only, by
    each provider adapter's from_env(), and never touch a Pydantic model anywhere in
    this design. Written atomically (see atomic_write_text) -- an interruption mid-write
    must not leave a run_config.json a later read_resolved_run_config can't parse."""
    run_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(run_dir / "run_config.json", resolved.model_dump_json(indent=2))


def read_resolved_run_config(run_dir: Path) -> ResolvedRunConfig:
    """Inverse of write_run_config -- mirrors pipeline.read_document_run's pattern.
    Lets a resumed run reload the EXACT resolved config a run started with, instead of
    re-resolving from YAML + prompt files that may have since changed."""
    return ResolvedRunConfig.model_validate_json((run_dir / "run_config.json").read_text())
