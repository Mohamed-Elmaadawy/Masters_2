"""
Regression tests for orchestrator/providers/rotating.py. Run after any change there:

    python -m orchestrator.test_rotating

Plain script, no pytest, same convention as orchestrator/test_providers.py. FakeAdapter
below never touches the network -- it's a scripted stand-in for a ProviderAdapter, same
pattern as orchestrator/test_stages.py's provider stand-in.
"""

from __future__ import annotations

import io
import os
from contextlib import contextmanager, redirect_stderr

from design.schemas import OutputMode
from orchestrator.providers.base import CompletionResult
from orchestrator.providers.rotating import RotatingKeyAdapter
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


class FakeAdapter:
    """One key's worth of scripted behavior: either a canned CompletionResult or an
    Exception instance to raise, in order, one per .complete() call. Records how many
    times it was actually called, so tests can assert an adapter was skipped entirely."""

    def __init__(self, name: str, script: list):
        self.name = name
        self._script = list(script)
        self.calls = 0

    def complete(self, prompt, *, model, temperature, timeout_seconds,
                 output_mode=OutputMode.TEXT, response_schema=None, schema_name=None):
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _success(text: str) -> CompletionResult:
    return CompletionResult(text=text, prompt_tokens=1, completion_tokens=1,
                             output_mode=OutputMode.TEXT)


@contextmanager
def env_var(name: str, value):
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


def _call(adapter: RotatingKeyAdapter):
    return adapter.complete("hi", model="m", temperature=1.0, timeout_seconds=30)


def test_rotates_past_stage_call_failed() -> None:
    section("rotates to the next key on StageCallFailed (e.g. 429/quota exhausted)")
    a = FakeAdapter("a", [StageCallFailed("quota exhausted")])
    b = FakeAdapter("b", [_success("from b")])
    adapter = RotatingKeyAdapter([a, b])
    result = _call(adapter)
    ok("result came from the second key", result.text == "from b")
    ok("first key was tried once", a.calls == 1)
    ok("second key was tried once", b.calls == 1)


def test_does_not_rotate_past_fatal() -> None:
    section("does NOT rotate past StageCallFatal -- raises immediately")
    a = FakeAdapter("a", [StageCallFatal("bad credentials")])
    b = FakeAdapter("b", [_success("from b")])
    adapter = RotatingKeyAdapter([a, b])
    try:
        _call(adapter)
        ok("StageCallFatal propagated", False)
    except StageCallFatal:
        ok("StageCallFatal propagated", True)
    ok("second key was never tried", b.calls == 0)


def test_does_not_rotate_past_partial() -> None:
    section("does NOT rotate past StageCallPartial -- raises immediately")
    a = FakeAdapter("a", [StageCallPartial("safety-filtered", prompt_tokens=5, completion_tokens=0)])
    b = FakeAdapter("b", [_success("from b")])
    adapter = RotatingKeyAdapter([a, b])
    try:
        _call(adapter)
        ok("StageCallPartial propagated", False)
    except StageCallPartial:
        ok("StageCallPartial propagated", True)
    ok("second key was never tried", b.calls == 0)


def test_raises_last_failure_when_all_keys_exhausted() -> None:
    section("all keys StageCallFailed -> raises the LAST one, every key tried once")
    a = FakeAdapter("a", [StageCallFailed("a exhausted")])
    b = FakeAdapter("b", [StageCallFailed("b exhausted")])
    adapter = RotatingKeyAdapter([a, b])
    try:
        _call(adapter)
        ok("StageCallFailed propagated", False)
    except StageCallFailed as e:
        ok("raised the last key's failure", "b exhausted" in str(e))
    ok("first key was tried once", a.calls == 1)
    ok("second key was tried once", b.calls == 1)


def test_sticky_index_skips_known_exhausted_key() -> None:
    section("next call starts from the last-successful key, not always index 0")
    a = FakeAdapter("a", [StageCallFailed("a exhausted"), _success("a recovered (should not be reached)")])
    b = FakeAdapter("b", [_success("from b"), _success("from b again")])
    adapter = RotatingKeyAdapter([a, b])

    first = _call(adapter)
    ok("first call: rotated a -> b", first.text == "from b")

    second = _call(adapter)
    ok("second call started at b directly", second.text == "from b again")
    ok("a was not retried on the second call", a.calls == 1)


def test_empty_adapter_list_rejected() -> None:
    section("constructing with zero adapters is a config error, not a runtime surprise")
    try:
        RotatingKeyAdapter([])
        ok("ValueError raised", False)
    except ValueError:
        ok("ValueError raised", True)


def test_from_env_parses_comma_separated_keys() -> None:
    section("from_env: comma-separated list, whitespace-trimmed, trailing comma ignored")
    with env_var("TEST_MULTI_KEYS", " key1 , key2,key3, "):
        seen: list[str] = []
        adapter = RotatingKeyAdapter.from_env(lambda k: seen.append(k) or FakeAdapter(k, []),
                                              "TEST_MULTI_KEYS")
        ok("three keys parsed in order", seen == ["key1", "key2", "key3"])
        ok("adapter holds three entries", len(adapter._adapters) == 3)


def test_from_env_missing_or_empty_raises() -> None:
    section("from_env: unset or empty env var raises RuntimeError, not a silent empty list")
    with env_var("TEST_MULTI_KEYS", None):
        try:
            RotatingKeyAdapter.from_env(lambda k: FakeAdapter(k, []), "TEST_MULTI_KEYS")
            ok("unset -> RuntimeError", False)
        except RuntimeError:
            ok("unset -> RuntimeError", True)
    with env_var("TEST_MULTI_KEYS", "   ,  ,"):
        try:
            RotatingKeyAdapter.from_env(lambda k: FakeAdapter(k, []), "TEST_MULTI_KEYS")
            ok("all-blank -> RuntimeError", False)
        except RuntimeError:
            ok("all-blank -> RuntimeError", True)


def main() -> int:
    print("=" * 72)
    print("RotatingKeyAdapter regression (no live network)")
    print("=" * 72)
    for fn in (
        test_rotates_past_stage_call_failed,
        test_does_not_rotate_past_fatal,
        test_does_not_rotate_past_partial,
        test_raises_last_failure_when_all_keys_exhausted,
        test_sticky_index_skips_known_exhausted_key,
        test_empty_adapter_list_rejected,
        test_from_env_parses_comma_separated_keys,
        test_from_env_missing_or_empty_raises,
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
