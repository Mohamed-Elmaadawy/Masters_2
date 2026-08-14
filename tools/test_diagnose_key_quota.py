"""Checks tools/diagnose_key_quota.py's verdict logic against a fake transport.

The point is that the diagnostic's conclusion is only worth acting on if the logic that
produces it has been shown to distinguish the cases -- including the three ways it is
supposed to refuse to answer. Running the real script costs quota; running this costs
nothing, so the logic is settled before any key is touched.

Run from the repo root:  python -m tools.test_diagnose_key_quota
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from tools.diagnose_key_quota import Probe, classify, read_keys, run

_passed = 0
_failed = 0


def check(label: str, got, want) -> None:
    global _passed, _failed
    if got == want:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL: {label}\n  got:  {got!r}\n  want: {want!r}")


class FakeGemini:
    """Models the thing under test: a per-PROJECT request budget that several keys may
    share. Exactly the situation the real script exists to detect."""

    def __init__(self, key_to_project: dict[str, str], rpm: int = 10,
                 dead: tuple[str, ...] = ()):
        self.key_to_project = key_to_project
        self.rpm = rpm
        self.dead = set(dead)
        self.counts: dict[str, int] = defaultdict(int)

    def __call__(self, key: str, model: str) -> Probe:
        if key in self.dead:
            return Probe(401, False, "API key not valid")
        project = self.key_to_project[key]
        self.counts[project] += 1
        if self.counts[project] > self.rpm:
            return Probe(429, True, "rate limited")
        return Probe(200, False, "ok")


class ScriptedSend:
    """Returns a fixed sequence of Probes, for cases a budget model can't express --
    notably a rate-limit window that resets partway through the probe."""

    def __init__(self, script: list[Probe]):
        self.script = list(script)
        self.i = 0

    def __call__(self, key: str, model: str) -> Probe:
        p = self.script[min(self.i, len(self.script) - 1)]
        self.i += 1
        return p


def collect(keys, fake, anchor: int = 0, burst: int = 40) -> tuple[int, str]:
    lines: list[str] = []
    code = run(keys, "fake-model", burst, anchor,
               send_fn=fake, sleep_fn=lambda _: None, log=lines.append)
    return code, "\n".join(lines)


def expect_systemexit(label: str, fn) -> None:
    global _passed, _failed
    try:
        fn()
    except SystemExit:
        _passed += 1
        return
    _failed += 1
    print(f"FAIL: {label}\n  expected SystemExit, none raised")


OK = Probe(200, False, "ok")
LIMITED = Probe(429, True, "rate limited")


# --- classify -----------------------------------------------------------------------
check("classify 200", classify(200, "{}").rate_limited, False)
check("classify 429", classify(429, "{}").rate_limited, True)
check("classify RESOURCE_EXHAUSTED body on a non-429 status",
      classify(500, '{"error":{"status":"RESOURCE_EXHAUSTED"}}').rate_limited, True)
check("classify 401 is not a rate limit", classify(401, "bad key").rate_limited, False)
check("classify 401 keeps the body as detail",
      classify(401, "API key not valid").detail, "API key not valid")

# --- read_keys ----------------------------------------------------------------------
import os  # noqa: E402  (imported here so the env manipulation reads next to its checks)

os.environ["_TEST_KEYS"] = ""
expect_systemexit("read_keys rejects an empty env var",
                  lambda: read_keys("_TEST_KEYS"))
os.environ["_TEST_KEYS"] = "solo"
expect_systemexit("read_keys rejects a single key (rotation is a no-op)",
                  lambda: read_keys("_TEST_KEYS"))
os.environ["_TEST_KEYS"] = "a,b,a"
expect_systemexit("read_keys rejects a byte-identical duplicate",
                  lambda: read_keys("_TEST_KEYS"))
os.environ["_TEST_KEYS"] = " a , b ,, c "
check("read_keys strips whitespace and drops empty entries",
      read_keys("_TEST_KEYS"), ["a", "b", "c"])

# --- all three keys in ONE project: rotation is a no-op ------------------------------
keys = ["k1", "k2", "k3"]
code, out = collect(keys, FakeGemini({k: "P" for k in keys}))
check("one project -> conclusive", code, 0)
check("one project -> names every non-anchor key as shared", out.count("SHARED with anchor"), 2)
check("one project -> says rotation buys nothing", "buys nothing" in out, True)
check("one project -> claims no quota multiple", "Effective quota" in out, False)

# --- three separate projects: rotation works -----------------------------------------
code, out = collect(keys, FakeGemini({"k1": "A", "k2": "B", "k3": "C"}))
check("three projects -> conclusive", code, 0)
check("three projects -> no key reported as shared", "SHARED with anchor" in out, False)
check("three projects -> reports the 3x multiple", "roughly 3x" in out, True)
check("three projects -> still hedges the claim", "evidence, not proof" in out, True)

# --- mixed: k2 shares the anchor's project, k3 does not -------------------------------
code, out = collect(keys, FakeGemini({"k1": "A", "k2": "A", "k3": "C"}))
check("mixed -> conclusive", code, 0)
check("mixed -> exactly one key shared", out.count("SHARED with anchor"), 1)
check("mixed -> reports both groups",
      ("buys nothing" in out and "separate projects" in out), True)
check("mixed -> does not claim a quota multiple", "Effective quota" in out, False)

# --- the anchor is already dead at baseline ------------------------------------------
code, out = collect(keys, FakeGemini({k: "P" for k in keys}, dead=("k1",)))
check("dead anchor -> inconclusive", code, 2)
check("dead anchor -> points at --rotate-anchor", "--rotate-anchor" in out, True)

# --- a non-anchor key is dead: it must be excluded, not counted as 'separate' ---------
code, out = collect(keys, FakeGemini({"k1": "A", "k2": "B", "k3": "C"}, dead=("k3",)))
check("dead non-anchor -> still conclusive on the rest", code, 0)
# The dead key must appear exactly once -- its phase 0 baseline line -- and nowhere in
# phase 2 or the verdict. Counting occurrences rather than testing `"key 3/3" not in out`
# keeps the check honest: the baseline line legitimately names it, so a plain
# absence test could only ever pass by accident.
check("dead non-anchor -> named at baseline and never again", out.count("key 3/3"), 1)
check("dead non-anchor -> quota multiple counts only healthy keys", "roughly 2x" in out, True)

# --- every non-anchor key is dead ----------------------------------------------------
code, out = collect(keys, FakeGemini({"k1": "A", "k2": "B", "k3": "C"}, dead=("k2", "k3")))
check("no comparison key -> inconclusive", code, 2)
check("no comparison key -> says so", "Nothing to compare against" in out, True)

# --- the burst never forces a 429 ----------------------------------------------------
code, out = collect(keys, FakeGemini({"k1": "A", "k2": "B", "k3": "C"}, rpm=10_000))
check("burst too small -> inconclusive", code, 2)
check("burst too small -> tells you to raise --burst", "larger --burst" in out, True)

# --- the anchor's window resets before phase 3 (the control catches it) ---------------
# 3 baseline OK, 1 burst LIMITED, 2 phase-2 OK, then the anchor is OK again -- which is
# what a shared bucket whose minute rolled over would look like. A SEPARATE verdict here
# would be unfalsifiable, so the run must refuse to give one.
scripted = ScriptedSend([OK, OK, OK, LIMITED, OK, OK, OK])
code, out = collect(keys, scripted)
check("anchor recovered mid-probe -> inconclusive", code, 2)
check("anchor recovered mid-probe -> explains why", "proves nothing" in out, True)
check("anchor recovered mid-probe -> emits no verdict", "VERDICT" in out, False)

# --- the control discriminates -------------------------------------------------------
# Same scenario as above, differing in one probe only: the control comes back limited
# instead of ok. If that single response flips the outcome from refusal to verdict, the
# phase-3 control is load-bearing rather than decorative. (Verified separately by
# deleting the control from the script and re-running: the recovered-window case above
# goes from exit 2 to exit 0 with a confident SEPARATE verdict, i.e. wrong.)
scripted_still_limited = ScriptedSend([OK, OK, OK, LIMITED, OK, OK, LIMITED])
code, out = collect(keys, scripted_still_limited)
check("control still limited -> conclusive", code, 0)
check("control still limited -> yields the separate-projects verdict",
      "separate projects" in out, True)
check("control still limited -> and no longer refuses", "proves nothing" in out, False)

# --- anchor selection ----------------------------------------------------------------
# --rotate-anchor exists to expose groupings invisible from key 1. k1 alone in A; k2/k3
# share B. Anchored on k1 that grouping cannot be seen; anchored on k2 it must be.
grouped = {"k1": "A", "k2": "B", "k3": "B"}
_, out_a = collect(keys, FakeGemini(grouped), anchor=0)
check("anchor=k1 cannot see the k2/k3 grouping", "SHARED with anchor" in out_a, False)
_, out_b = collect(keys, FakeGemini(grouped), anchor=1)
check("anchor=k2 exposes the k2/k3 grouping", out_b.count("SHARED with anchor"), 1)

print(f"\n{_passed} checks passed, {_failed} failed")
raise SystemExit(1 if _failed else 0)
