"""
Regression tests for evaluation/config_parity.py (Task 6 finding 4, 2026-08-17):
fair-configuration enforcement, checked before any adapter is constructed. Run after
any change:

    python -m evaluation.test_config_parity

Plain script, no pytest, no network -- reads orchestrator/runs_gemini.yaml (a file
already committed and covered by orchestrator/test_config.py) but never constructs a
real provider adapter or touches an API key.
"""

from __future__ import annotations

from pydantic import ValidationError

from design.schemas import OutputMode
from evaluation.config_parity import enforce_fair_config, frozen_arm_p_config
from evaluation.schemas import BaselineCallConfig

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


class SpyAdapterFactory:
    """Stands in for `GeminiAdapter.from_env`/`GroqAdapter.from_env`. Records whether
    it was ever invoked, and raises loudly if it is -- proves a rejection happened
    strictly before any provider/adapter access, with no real API key, no network,
    and no chance of the test passing "by accident" because the call happened to fail
    for an unrelated reason."""
    def __init__(self):
        self.called = False

    def __call__(self):
        self.called = True
        raise AssertionError(
            "adapter factory was called -- config validation did not reject before "
            "provider access")


section("frozen_arm_p_config -- loads the real values from orchestrator/runs_gemini.yaml")
frozen = frozen_arm_p_config()
ok("provider is gemini", frozen.provider == "gemini")
ok("model matches the shipped config", frozen.model == "gemini-3.6-flash")
ok("temperature matches the shipped config", frozen.temperature == 1.0)
ok("output_mode matches the shipped config", frozen.output_mode == OutputMode.TEXT)

section("enforce_fair_config -- an identical config passes (no exception)")
enforce_fair_config(frozen, frozen)
ok("identical config raises nothing", True)

section("enforce_fair_config -- provider mismatch is rejected")
mismatched_provider = BaselineCallConfig(
    provider="groq", model=frozen.model, temperature=frozen.temperature,
    output_mode=frozen.output_mode)
try:
    enforce_fair_config(mismatched_provider, frozen)
    ok("provider mismatch raises", False)
except ValueError as e:
    ok("provider mismatch raises", True)
    ok("error message names 'provider'", "provider" in str(e))

section("enforce_fair_config -- model mismatch is rejected")
mismatched_model = BaselineCallConfig(
    provider=frozen.provider, model="a-different-model", temperature=frozen.temperature,
    output_mode=frozen.output_mode)
try:
    enforce_fair_config(mismatched_model, frozen)
    ok("model mismatch raises", False)
except ValueError as e:
    ok("model mismatch raises", True)
    ok("error message names 'model'", "model" in str(e))

section("enforce_fair_config -- temperature mismatch is rejected")
mismatched_temp = BaselineCallConfig(
    provider=frozen.provider, model=frozen.model, temperature=0.2, output_mode=frozen.output_mode)
try:
    enforce_fair_config(mismatched_temp, frozen)
    ok("temperature mismatch raises", False)
except ValueError as e:
    ok("temperature mismatch raises", True)
    ok("error message names 'temperature'", "temperature" in str(e))

section("enforce_fair_config -- output_mode mismatch is rejected")
mismatched_mode = BaselineCallConfig(
    provider=frozen.provider, model=frozen.model, temperature=frozen.temperature,
    output_mode=OutputMode.JSON_OBJECT)
try:
    enforce_fair_config(mismatched_mode, frozen)
    ok("output_mode mismatch raises", False)
except ValueError as e:
    ok("output_mode mismatch raises", True)
    ok("error message names 'output_mode'", "output_mode" in str(e))

section("enforce_fair_config -- timeout_seconds is NOT compared (client-side transport "
       "setting, not part of the experiment)")
different_timeout = BaselineCallConfig(
    provider=frozen.provider, model=frozen.model, temperature=frozen.temperature,
    timeout_seconds=frozen.timeout_seconds + 100, output_mode=frozen.output_mode)
enforce_fair_config(different_timeout, frozen)
ok("a different timeout_seconds does not raise", True)

section("enforce_fair_config -- multiple mismatches are ALL named in one error, "
       "not just the first")
many_mismatches = BaselineCallConfig(
    provider="groq", model="wrong-model", temperature=0.0, output_mode=OutputMode.JSON_OBJECT)
try:
    enforce_fair_config(many_mismatches, frozen)
    ok("multi-mismatch config raises", False)
except ValueError as e:
    msg = str(e)
    ok("multi-mismatch config raises", True)
    ok("all four mismatched fields are named in the single error",
      all(term in msg for term in ("provider", "model", "temperature", "output_mode")))


# ---------------------------------------------------------------------------------
# Spy-adapter proof: rejection happens BEFORE any provider/adapter access.
# ---------------------------------------------------------------------------------

section("Spy adapter -- a fair-config mismatch is rejected before the adapter "
       "factory is ever called")
spy = SpyAdapterFactory()
try:
    candidate = BaselineCallConfig(
        provider=frozen.provider, model="wrong-model", temperature=frozen.temperature,
        output_mode=frozen.output_mode)
    enforce_fair_config(candidate, frozen)   # must raise before the next line
    spy()                                    # would only ever run if it didn't
    ok("unreachable line was not reached", False)
except ValueError:
    ok("config rejected before the adapter factory was ever called", spy.called is False)

section("Spy adapter -- an out-of-bounds temperature is rejected at CONSTRUCTION, "
       "before enforce_fair_config or any adapter access")
spy2 = SpyAdapterFactory()
try:
    bad = BaselineCallConfig(
        provider=frozen.provider, model=frozen.model, temperature=5.0,  # > 2.0, invalid
        output_mode=frozen.output_mode)
    enforce_fair_config(bad, frozen)
    spy2()
    ok("unreachable line was not reached", False)
except ValidationError:
    ok("out-of-bounds temperature rejected at construction, before any comparison", True)
    ok("adapter factory was never called", spy2.called is False)

section("Spy adapter -- a non-positive timeout is rejected at CONSTRUCTION")
try:
    BaselineCallConfig(
        provider=frozen.provider, model=frozen.model, temperature=frozen.temperature,
        timeout_seconds=0.0, output_mode=frozen.output_mode)
    ok("zero timeout_seconds rejected at construction", False)
except ValidationError:
    ok("zero timeout_seconds rejected at construction", True)

try:
    BaselineCallConfig(
        provider=frozen.provider, model=frozen.model, temperature=frozen.temperature,
        timeout_seconds=-5.0, output_mode=frozen.output_mode)
    ok("negative timeout_seconds rejected at construction", False)
except ValidationError:
    ok("negative timeout_seconds rejected at construction", True)

section("Spy adapter -- an unsupported provider is rejected at CONSTRUCTION "
       "(Literal type, not enforce_fair_config's job)")
try:
    BaselineCallConfig(
        provider="openai", model=frozen.model, temperature=frozen.temperature,
        output_mode=frozen.output_mode)
    ok("an unsupported provider literal is rejected at construction", False)
except ValidationError:
    ok("an unsupported provider literal is rejected at construction", True)


# ---------------------------------------------------------------------------------
# Finding 8 (2026-08-17, second fix): frozen_arm_p_config must verify EVERY resolved
# stage shares the (provider, model, temperature, output_mode) tuple before selecting
# a reference -- a per-stage override that only "resolves cleanly" is not the same
# as "uniform", and picking reference_stage's value while silently ignoring a
# divergent stage would make B1/B2 match one arbitrary stage, not "arm P".
# ---------------------------------------------------------------------------------

section("frozen_arm_p_config -- a per-stage temperature override is REJECTED, not "
       "silently ignored in favor of reference_stage's own value")
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

_REAL_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "orchestrator" / "example_prompts"

_STAGE_PROMPT_FILES = {
    "classifier": "classifier.txt", "quality_checker": "quality_checker.txt",
    "refiner_questioner": "refiner_questioner.txt", "refiner_rewriter": "refiner_rewriter.txt",
    "strategy_selector": "strategy_selector.txt", "test_generator": "test_generator.txt",
    "consistency_checker": "consistency_checker.txt", "dependency_mapper": "dependency_mapper.txt",
    "consistency_checker_refined": "consistency_checker_refined.txt",
    "dependency_mapper_refined": "dependency_mapper_refined.txt",
}


def _write_temp_run_config(tmp_dir: Path, stage_override: dict) -> Path:
    """Builds a real, resolvable RunConfig YAML -- same shape as
    orchestrator/runs_gemini.yaml -- but with ONE stage's temperature overridden, using
    absolute paths to the project's own real prompt files (so it resolves cleanly
    without needing to be co-located with orchestrator/'s own files)."""
    prompts_block = "\n".join(
        f"  {stage}: {(_REAL_PROMPTS_DIR / fname).as_posix()!r}"
        for stage, fname in _STAGE_PROMPT_FILES.items())
    stages_block = "\n".join(
        f"  {stage}: {{temperature: 0.2}}" if stage in stage_override else f"  {stage}: {{}}"
        for stage in _STAGE_PROMPT_FILES)
    text = f"""\
run_id: test-per-stage-override
output_dir: {(tmp_dir / "runs").as_posix()!r}
max_revisions: 3
retry:
  max_attempts: 3
  initial_delay_seconds: 2.0
  multiplier: 2.0
rate_limits:
  gemini/gemini-3.6-flash: {{requests_per_minute: 15, tokens_per_minute: null}}
defaults:
  provider: gemini
  model: gemini-3.6-flash
  prompt_version: v1
  temperature: 1.0
  timeout_seconds: 30
  output_mode: text
stages:
{stages_block}
prompts:
{prompts_block}
"""
    path = tmp_dir / "run_config.yaml"
    path.write_text(text)
    return path


with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    uniform_config_path = _write_temp_run_config(tmp_path, stage_override=set())
    ok("a genuinely uniform temp config resolves and returns a config (sanity check "
      "that the fixture itself is valid before testing the divergent case)",
      frozen_arm_p_config(uniform_config_path).temperature == 1.0)

    divergent_config_path = _write_temp_run_config(tmp_path, stage_override={"classifier"})
    try:
        frozen_arm_p_config(divergent_config_path)
        ok("a per-stage temperature override is rejected", False)
    except ValueError as e:
        ok("a per-stage temperature override is rejected", True)
        ok("the error names the diverging stage", "classifier" in str(e))
        ok("the error explains there is no single tuple to hold a baseline to",
          "single" in str(e).lower() or "one" in str(e).lower())


print(f"\n{PASSED}/{PASSED + len(FAILED)} checks passed")
if FAILED:
    print("FAILED:")
    for label in FAILED:
        print(f"  - {label}")

import sys
sys.exit(0 if not FAILED else 1)
