"""
Regression tests for orchestrator/config.py. Run after any change there:

    python -m orchestrator.test_config

Plain script, no pytest, same convention as design/test_schemas.py.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from design.schemas import ALL_STAGES, RunMetadata
from orchestrator.config import (
    RateLimitConfig, ResolvedRunConfig, RunConfig, StageDefaults, StageOverride,
    load_run_config, read_resolved_run_config, resolve_run_config, retry_args,
    run_dir_for, throttle_from, to_run_metadata, write_run_config,
)

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


def accepts(label: str, fn) -> None:
    try:
        fn()
        ok(label)
    except ValidationError as e:
        ok(label, False)
        print(f"        unexpected: {e}")


def rejects(label: str, fn) -> None:
    try:
        fn()
        ok(label, False)
        print("        unexpectedly accepted")
    except (ValidationError, ValueError):
        ok(label)


def write_prompts(dir_path: Path, text_by_stage: dict = None) -> dict:
    """One prompt file per ALL_STAGES stage, relative filenames (as an operator would
    write in YAML). Returns the {stage: relative_path} dict for RunConfig.prompts."""
    text_by_stage = text_by_stage or {}
    prompts = {}
    for stage in ALL_STAGES:
        name = f"{stage}.txt"
        (dir_path / name).write_text(text_by_stage.get(stage, f"prompt text for {stage}"))
        prompts[stage] = name
    return prompts


def base_config_dict(dir_path: Path, **overrides) -> dict:
    d = dict(
        defaults=dict(provider="gemini", model="gemini-3.6-flash", prompt_version="v1"),
        rate_limits={"gemini/gemini-3.6-flash": {"requests_per_minute": 15, "tokens_per_minute": None}},
        prompts=write_prompts(dir_path),
    )
    d.update(overrides)
    return d


def test_extra_field_rejected() -> None:
    section("extra='forbid' rejects a typo'd key")
    with tempfile.TemporaryDirectory() as tmp:
        d = base_config_dict(Path(tmp))
        rejects("unknown top-level key", lambda: RunConfig.model_validate({**d, "bogus": 1}))
        rejects("unknown key inside defaults",
                lambda: StageDefaults.model_validate({**d["defaults"], "bogus": 1}))
        rejects("unknown key inside a stage override",
                lambda: StageOverride.model_validate({"bogus": 1}))
        rejects("unknown key inside a rate limit entry",
                lambda: RateLimitConfig.model_validate(
                    {"requests_per_minute": 1, "tokens_per_minute": None, "bogus": 1}))


def test_unknown_stage_key_rejected() -> None:
    section("unknown stage name in `stages` is rejected")
    with tempfile.TemporaryDirectory() as tmp:
        d = base_config_dict(Path(tmp), stages={"not_a_real_stage": {}})
        rejects("unknown stage override key", lambda: RunConfig.model_validate(d))


def test_prompts_coverage_required() -> None:
    section("prompts must cover exactly ALL_STAGES")
    with tempfile.TemporaryDirectory() as tmp:
        prompts = write_prompts(Path(tmp))
        del prompts[ALL_STAGES[0]]
        d = base_config_dict(Path(tmp), prompts=prompts)
        rejects("prompts missing a stage", lambda: RunConfig.model_validate(d))

        prompts2 = write_prompts(Path(tmp))
        prompts2["not_a_real_stage"] = "x.txt"
        d2 = base_config_dict(Path(tmp), prompts=prompts2)
        rejects("prompts with an unknown stage name", lambda: RunConfig.model_validate(d2))


def test_missing_prompt_file_rejected_at_resolve() -> None:
    section("a prompt path that doesn't exist on disk fails at resolve, not at load")
    with tempfile.TemporaryDirectory() as tmp:
        prompts = write_prompts(Path(tmp))
        prompts[ALL_STAGES[0]] = "does-not-exist.txt"
        d = base_config_dict(Path(tmp), prompts=prompts)
        config = RunConfig.model_validate(d)  # shape is fine, load succeeds
        ok("RunConfig itself loads (shape-only validation)", isinstance(config, RunConfig))
        rejects("resolve_run_config fails on the missing file",
                lambda: resolve_run_config(config, Path(tmp) / "config.yaml"))


def test_rate_limit_coverage_required() -> None:
    section("every distinct resolved provider/model must be covered by rate_limits")
    with tempfile.TemporaryDirectory() as tmp:
        d = base_config_dict(Path(tmp), rate_limits={})
        config = RunConfig.model_validate(d)
        rejects("missing rate_limits entry for the only model in use",
                lambda: resolve_run_config(config, Path(tmp) / "config.yaml"))

        d2 = base_config_dict(Path(tmp), rate_limits={"gemini/gemini-3.6-flash": {
            "requests_per_minute": None, "tokens_per_minute": None}})
        config2 = RunConfig.model_validate(d2)
        resolved = resolve_run_config(config2, Path(tmp) / "config.yaml")
        ok("explicit null is accepted as deliberately unthrottled",
           resolved.rate_limits["gemini/gemini-3.6-flash"].requests_per_minute is None)
        ok("throttle_from gives that model no min_interval entry",
           "gemini/gemini-3.6-flash" not in throttle_from(resolved).min_interval_seconds)
        ok("throttle_from gives that model no tokens_per_minute entry either",
           "gemini/gemini-3.6-flash" not in throttle_from(resolved).tokens_per_minute)


def test_rate_limit_value_must_be_positive_or_null() -> None:
    section("requests_per_minute/tokens_per_minute must each be > 0 or explicitly null")
    rejects("requests_per_minute zero is rejected",
            lambda: RateLimitConfig.model_validate(
                {"requests_per_minute": 0, "tokens_per_minute": None}))
    rejects("requests_per_minute negative is rejected",
            lambda: RateLimitConfig.model_validate(
                {"requests_per_minute": -5, "tokens_per_minute": None}))
    accepts("requests_per_minute null is accepted",
            lambda: RateLimitConfig.model_validate(
                {"requests_per_minute": None, "tokens_per_minute": None}))
    accepts("requests_per_minute a positive value is accepted",
            lambda: RateLimitConfig.model_validate(
                {"requests_per_minute": 15, "tokens_per_minute": None}))
    rejects("tokens_per_minute zero is rejected",
            lambda: RateLimitConfig.model_validate(
                {"requests_per_minute": None, "tokens_per_minute": 0}))
    rejects("tokens_per_minute negative is rejected",
            lambda: RateLimitConfig.model_validate(
                {"requests_per_minute": None, "tokens_per_minute": -12000}))
    accepts("tokens_per_minute null is accepted",
            lambda: RateLimitConfig.model_validate(
                {"requests_per_minute": None, "tokens_per_minute": None}))
    accepts("tokens_per_minute a positive value is accepted",
            lambda: RateLimitConfig.model_validate(
                {"requests_per_minute": None, "tokens_per_minute": 12000}))


def test_override_merge_picks_right_scalar_per_field() -> None:
    section("per-stage override merges field-by-field against defaults")
    with tempfile.TemporaryDirectory() as tmp:
        stage = ALL_STAGES[0]
        d = base_config_dict(
            Path(tmp),
            stages={stage: {"model": "gemini-3.1-pro-preview", "temperature": 0.2}},
            rate_limits={"gemini/gemini-3.6-flash": {"requests_per_minute": 15, "tokens_per_minute": None},
                        "gemini/gemini-3.1-pro-preview": {"requests_per_minute": 10, "tokens_per_minute": None}},
        )
        config = RunConfig.model_validate(d)
        resolved = resolve_run_config(config, Path(tmp) / "config.yaml")
        overridden = resolved.stages[stage]
        untouched = resolved.stages[ALL_STAGES[1]]
        ok("overridden field takes the override's value", overridden.model == "gemini-3.1-pro-preview")
        ok("overridden field's sibling override value also applies", overridden.temperature == 0.2)
        ok("provider falls back to defaults (not overridden)", overridden.provider == "gemini")
        ok("prompt_version falls back to defaults (not overridden)", overridden.prompt_version == "v1")
        ok("a stage with no override at all matches defaults exactly",
           untouched.model == "gemini-3.6-flash" and untouched.temperature == 1.0)


def test_hash_changes_with_file_content() -> None:
    section("prompt_hash is computed from the actual file content, not authored")
    with tempfile.TemporaryDirectory() as tmp:
        stage = ALL_STAGES[0]
        d = base_config_dict(Path(tmp))
        config = RunConfig.model_validate(d)
        r1 = resolve_run_config(config, Path(tmp) / "config.yaml")
        (Path(tmp) / d["prompts"][stage]).write_text("a completely different prompt")
        r2 = resolve_run_config(config, Path(tmp) / "config.yaml")
        ok("hash changes when the file content changes",
           r1.stages[stage].prompt_hash != r2.stages[stage].prompt_hash)
        ok("hashes for untouched stages stay the same",
           r1.stages[ALL_STAGES[1]].prompt_hash == r2.stages[ALL_STAGES[1]].prompt_hash)


def test_paths_resolve_relative_to_config_file_not_cwd() -> None:
    section("prompt/output paths resolve relative to the config file's own directory")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp).resolve()
        d = base_config_dict(tmp_path, output_dir="runs")
        config = RunConfig.model_validate(d)
        cwd = os.getcwd()
        other_dir = tempfile.mkdtemp()
        try:
            os.chdir(other_dir)
            resolved = resolve_run_config(config, tmp_path / "config.yaml")
        finally:
            os.chdir(cwd)
        ok("prompt_path is absolute and under the config file's directory, not cwd",
           resolved.stages[ALL_STAGES[0]].prompt_path.is_absolute()
           and str(tmp_path) in str(resolved.stages[ALL_STAGES[0]].prompt_path))
        ok("output_dir is absolute and under the config file's directory, not cwd",
           resolved.output_dir.is_absolute() and str(tmp_path) in str(resolved.output_dir))


def test_output_mode_capability_checked_before_any_env_var() -> None:
    section("output_mode/capability mismatch rejected before any API key is read")
    saved = {k: os.environ.pop(k, None) for k in ("GEMINI_API_KEY", "GROQ_API_KEY")}
    try:
        ok("neither env var is set for this test", "GEMINI_API_KEY" not in os.environ
           and "GROQ_API_KEY" not in os.environ)
        with tempfile.TemporaryDirectory() as tmp:
            d = base_config_dict(
                Path(tmp),
                defaults=dict(provider="gemini", model="gemini-2.0-flash", prompt_version="v1",
                             output_mode="json_schema"),
                rate_limits={"gemini/gemini-2.0-flash": {"requests_per_minute": 15, "tokens_per_minute": None}},
            )
            config = RunConfig.model_validate(d)
            rejects("unsupported output_mode fails at resolve, with no key ever read",
                    lambda: resolve_run_config(config, Path(tmp) / "config.yaml"))
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_to_run_metadata_round_trips() -> None:
    section("to_run_metadata produces a valid RunMetadata covering every stage")
    with tempfile.TemporaryDirectory() as tmp:
        d = base_config_dict(Path(tmp))
        config = RunConfig.model_validate(d)
        resolved = resolve_run_config(config, Path(tmp) / "config.yaml")
        meta = to_run_metadata(resolved, datetime.now(timezone.utc))
        ok("produces a RunMetadata instance", isinstance(meta, RunMetadata))
        ok("re-validates cleanly", RunMetadata.model_validate(meta.model_dump(mode="json")))
        ok("model string is provider/model",
           meta.stages[ALL_STAGES[0]].model == "gemini/gemini-3.6-flash")
        ok("throttle_from's keys match to_run_metadata's model strings",
           set(throttle_from(resolved).min_interval_seconds)
           <= {sc.model for sc in meta.stages.values()})


def test_retry_args_and_run_dir_for() -> None:
    section("retry_args and run_dir_for compute what they claim to")
    with tempfile.TemporaryDirectory() as tmp:
        d = base_config_dict(Path(tmp), retry={"max_attempts": 4, "initial_delay_seconds": 3.0,
                                                "multiplier": 2.0})
        config = RunConfig.model_validate(d)
        resolved = resolve_run_config(config, Path(tmp) / "config.yaml")
        max_attempts, backoff = retry_args(resolved)
        ok("max_attempts matches config", max_attempts == 4)
        ok("backoff(0) == initial_delay_seconds", backoff(0) == 3.0)
        ok("backoff(1) == initial * multiplier", backoff(1) == 6.0)
        ok("backoff(2) == initial * multiplier^2", backoff(2) == 12.0)
        ok("run_dir_for is output_dir / run_id",
           run_dir_for(resolved) == resolved.output_dir / resolved.run_id)


def test_write_and_read_round_trip() -> None:
    section("write_run_config -> read_resolved_run_config round-trips to an equal object")
    with tempfile.TemporaryDirectory() as tmp:
        d = base_config_dict(Path(tmp))
        config = RunConfig.model_validate(d)
        resolved = resolve_run_config(config, Path(tmp) / "config.yaml")
        run_dir = run_dir_for(resolved)
        write_run_config(run_dir, resolved)
        reread = read_resolved_run_config(run_dir)
        ok("round-trips to an equal ResolvedRunConfig", reread == resolved)


def test_load_run_config_from_yaml() -> None:
    section("load_run_config reads real YAML (not just a Python dict)")
    with tempfile.TemporaryDirectory() as tmp:
        import yaml
        d = base_config_dict(Path(tmp))
        yaml_path = Path(tmp) / "config.yaml"
        yaml_path.write_text(yaml.safe_dump(d))
        config = load_run_config(yaml_path)
        ok("loads into a RunConfig", isinstance(config, RunConfig))
        resolved = resolve_run_config(config, yaml_path)
        ok("the loaded config resolves cleanly", isinstance(resolved, ResolvedRunConfig))


def test_run_id_generated_when_absent() -> None:
    section("run_id is minted when the operator doesn't supply one")
    with tempfile.TemporaryDirectory() as tmp:
        d = base_config_dict(Path(tmp))
        config = RunConfig.model_validate(d)
        ok("run_id is None on the authored config", config.run_id is None)
        r1 = resolve_run_config(config, Path(tmp) / "config.yaml")
        r2 = resolve_run_config(config, Path(tmp) / "config.yaml")
        ok("a run_id is minted", bool(r1.run_id))
        ok("two independent resolutions get different minted ids",
           r1.run_id != r2.run_id)


def test_tampered_run_config_json_cannot_load() -> None:
    """ResolvedStageConfig/ResolvedRunConfig must be validated as strongly as the
    authored RunConfig -- read_resolved_run_config loads a persisted run_config.json
    straight through ResolvedRunConfig.model_validate_json, which never passes through
    any of RunConfig's own validators. A hand-edited (or corrupted) file must still be
    rejected on every axis RunConfig itself would have caught."""
    section("a tampered run_config.json cannot load")
    import json

    with tempfile.TemporaryDirectory() as tmp:
        d = base_config_dict(Path(tmp))
        config = RunConfig.model_validate(d)
        resolved = resolve_run_config(config, Path(tmp) / "config.yaml")
        run_dir = run_dir_for(resolved)
        write_run_config(run_dir, resolved)
        good_json = json.loads((run_dir / "run_config.json").read_text())

        def tampered(mutate) -> dict:
            copy = json.loads(json.dumps(good_json))  # deep copy
            mutate(copy)
            return copy

        def rejects_tampered(label: str, mutate) -> None:
            (run_dir / "run_config.json").write_text(json.dumps(tampered(mutate)))
            try:
                read_resolved_run_config(run_dir)
                ok(label, False)
                print("        unexpectedly loaded")
            except (ValidationError, ValueError):
                ok(label, True)
            finally:
                (run_dir / "run_config.json").write_text(json.dumps(good_json))  # restore

        ok("the untampered file loads cleanly (sanity check before tampering)",
           isinstance(read_resolved_run_config(run_dir), ResolvedRunConfig))

        rejects_tampered("negative max_revisions", lambda c: c.__setitem__("max_revisions", -1))
        rejects_tampered("max_revisions below the floor of 2", lambda c: c.__setitem__("max_revisions", 1))
        rejects_tampered("a stage entry deleted entirely",
                         lambda c: c["stages"].pop(ALL_STAGES[0]))
        rejects_tampered("an unknown stage name injected",
                         lambda c: c["stages"].__setitem__("not_a_real_stage", c["stages"][ALL_STAGES[0]]))
        rejects_tampered("temperature pushed out of bounds",
                         lambda c: c["stages"][ALL_STAGES[0]].__setitem__("temperature", 9.9))
        rejects_tampered("timeout_seconds pushed to zero",
                         lambda c: c["stages"][ALL_STAGES[0]].__setitem__("timeout_seconds", 0))
        rejects_tampered("a rate_limits entry deleted, leaving a model uncovered",
                         lambda c: c["rate_limits"].popitem())
        rejects_tampered("an unused rate_limits entry injected for a model nothing uses",
                         lambda c: c["rate_limits"].__setitem__(
                             "groq/totally-unused-model",
                             {"requests_per_minute": 5, "tokens_per_minute": None}))
        rejects_tampered("an extra unknown top-level field injected (extra='forbid')",
                         lambda c: c.__setitem__("bogus_field", 1))


def test_run_id_path_traversal_is_rejected() -> None:
    """Fix (2026-08-09, third review pass, reproduced before fixing): run_id becomes a
    directory name verbatim (run_dir_for: output_dir / run_id). A user-authored
    run_id="../escape" resolved to a directory OUTSIDE output_dir entirely. Rejected
    now at both RunConfig (authored) and ResolvedRunConfig (persisted) -- the second
    because read_resolved_run_config loads a file straight through ResolvedRunConfig,
    never through RunConfig's own validator."""
    section("run_id path traversal is rejected, not just avoided by luck")
    with tempfile.TemporaryDirectory() as tmp:
        for evil_run_id in ("../escape", "../../etc", "a/b", "a\\b", "..", "."):
            d = base_config_dict(Path(tmp), run_id=evil_run_id)
            rejects(f"RunConfig rejects run_id={evil_run_id!r}",
                    lambda d=d: RunConfig.model_validate(d))

        # Same check on ResolvedRunConfig directly -- this is what actually protects
        # read_resolved_run_config loading a hand-edited run_config.json, which never
        # passes through RunConfig at all.
        d = base_config_dict(Path(tmp))
        config = RunConfig.model_validate(d)
        resolved = resolve_run_config(config, Path(tmp) / "config.yaml")
        good = resolved.model_dump(mode="json")
        for evil_run_id in ("../escape", "a/b", ".."):
            tampered = {**good, "run_id": evil_run_id}
            rejects(f"ResolvedRunConfig rejects run_id={evil_run_id!r}",
                    lambda t=tampered: ResolvedRunConfig.model_validate(t))

    with tempfile.TemporaryDirectory() as tmp:
        # A safe-looking run_id must still be accepted -- the check must discriminate,
        # not just reject everything (CLAUDE.md: mutation-test new rules).
        d = base_config_dict(Path(tmp), run_id="run-001_ok.v2")
        accepts("a normal run_id is accepted", lambda: RunConfig.model_validate(d))


def test_run_dir_for_defends_even_if_the_field_validator_is_bypassed() -> None:
    """run_dir_for's own belt-and-suspenders check (it resolves output_dir / run_id and
    confirms the result is actually a descendant), tested by deliberately bypassing
    ResolvedRunConfig's field validator via model_construct (pydantic's documented
    escape hatch that skips validation) -- proving the defense is real and not just a
    restatement of the field validator that happens to always agree with it."""
    section("run_dir_for defends the resolved path even if field validation is bypassed")
    with tempfile.TemporaryDirectory() as tmp:
        d = base_config_dict(Path(tmp))
        config = RunConfig.model_validate(d)
        resolved = resolve_run_config(config, Path(tmp) / "config.yaml")
        bypassed = resolved.model_copy(update={"run_id": "../escaped-via-bypass"})
        # model_copy (unlike model_validate) does not re-run validators -- this is the
        # deliberate bypass, standing in for a future code path that might construct a
        # ResolvedRunConfig without going through model_validate at all.
        rejects("run_dir_for still refuses to hand out an escaping path",
                lambda: run_dir_for(bypassed))


def test_no_field_looks_key_shaped() -> None:
    section("no config model has a field that could hold a secret")
    suspicious = {"key", "api_key", "secret", "token", "password", "credential"}
    # Narrow, explicit exemption -- NOT a loosened substring set, which would blunt
    # this check for a real future secret-shaped field (api_token, auth_token, ...).
    # RateLimitConfig.tokens_per_minute matches "token" as a substring but holds an
    # LLM token-count budget (orchestrator/pipeline.py's Throttle.tokens_per_minute),
    # never a credential -- confirmed by reading the field, not assumed here.
    known_false_positives = {("RateLimitConfig", "tokens_per_minute")}
    for model in (RunConfig, StageDefaults, StageOverride, RateLimitConfig,
                  ResolvedRunConfig):
        for name in model.model_fields:
            if (model.__name__, name) in known_false_positives:
                continue
            ok(f"{model.__name__}.{name} does not look key-shaped",
               not any(s in name.lower() for s in suspicious))


def test_active_yaml_configs_resolve_with_all_ten_stages() -> None:
    """S3 review finding 3: the three active, reusable YAML configs under
    orchestrator/ (not historical results under docs/superpowers/results/, which
    describe what actually ran and are never edited to match a later pipeline) must
    keep resolving now that ALL_STAGES has grown to ten -- resolve_run_config's
    per-stage loop and RunConfig's exact-coverage check would otherwise reject them
    for missing consistency_checker_refined/dependency_mapper_refined the moment
    anyone tried to actually use one. Guards against silently going stale again."""
    section("active YAML configs (example_run_config/runs_gemini/runs_groq) resolve cleanly")
    repo_root = Path(__file__).resolve().parent.parent
    for name in ("example_run_config.yaml", "runs_gemini.yaml", "runs_groq.yaml"):
        path = repo_root / "orchestrator" / name
        config = load_run_config(path)
        resolved = resolve_run_config(config, path)
        ok(f"{name} resolves", isinstance(resolved, ResolvedRunConfig))
        ok(f"{name} covers all ten current stages", set(resolved.stages) == set(ALL_STAGES))


def main() -> int:
    print("=" * 72)
    print("orchestrator/config.py regression")
    print("=" * 72)
    for fn in (
        test_extra_field_rejected, test_unknown_stage_key_rejected,
        test_prompts_coverage_required, test_missing_prompt_file_rejected_at_resolve,
        test_rate_limit_coverage_required, test_rate_limit_value_must_be_positive_or_null,
        test_override_merge_picks_right_scalar_per_field, test_hash_changes_with_file_content,
        test_paths_resolve_relative_to_config_file_not_cwd,
        test_output_mode_capability_checked_before_any_env_var,
        test_to_run_metadata_round_trips, test_retry_args_and_run_dir_for,
        test_write_and_read_round_trip, test_load_run_config_from_yaml,
        test_run_id_generated_when_absent, test_tampered_run_config_json_cannot_load,
        test_run_id_path_traversal_is_rejected,
        test_run_dir_for_defends_even_if_the_field_validator_is_bypassed,
        test_no_field_looks_key_shaped,
        test_active_yaml_configs_resolve_with_all_ten_stages,
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
