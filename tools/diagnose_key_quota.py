"""Does GEMINI_API_KEYS actually buy you N times the quota, or one project's worth?

WHY THIS EXISTS (2026-08-11)
----------------------------
orchestrator/providers/rotating.py rotates past a key that returned 429, on the stated
premise of "free-tier per-key/per-account rate limits". But
https://ai.google.dev/gemini-api/docs/rate-limits (fetched 2026-08-11) says, verbatim:

    "Rate limits are applied per project, not per API key."

Those two statements are only compatible if every key in GEMINI_API_KEYS belongs to a
DIFFERENT Google Cloud project. Several keys minted inside one AI Studio project share a
single quota bucket, and rotating between them buys nothing at all -- each rotation just
re-confirms the same exhausted bucket, one wasted request at a time. That premise has
never been checked against this account's actual keys. This script checks it.

CHECK THE FREE WAY FIRST
------------------------
https://aistudio.google.com/apikey lists every key with the project it belongs to. If
they visibly sit in different projects, you already have your answer and should not run
this script -- it costs quota and this does not.

Run this only when the console answer is unclear (e.g. keys from separate Google
accounts you cannot see side by side).

WHAT IT COSTS
-------------
It deliberately exhausts the *per-minute* (RPM) limit on the first key, because RPM and
RPD are governed at the same scope -- the sentence above says "rate limits", not "the
daily one". If RPM turns out to be shared, RPD is shared. RPM refills after ~60s, RPD
would cost a day, so RPM is the cheap proxy.

Budget: --burst requests on key 1 (default 40), plus one per key, all with
maxOutputTokens=1. Tens of requests against your RPD, not hundreds. Still not free.

WHAT IT CANNOT TELL YOU
-----------------------
1. It partitions keys RELATIVE TO KEY 1 only. It answers "which keys share key 1's
   bucket?", not the full partition. If keys 2 and 3 share a project with each other but
   not with key 1, both report SEPARATE and that grouping stays invisible. Re-run with
   --rotate-anchor to make a different key the anchor if you need the full picture.
2. Rate limits are per (project, model) -- the same docs page: "Limits vary depending on
   the specific model being used." A verdict obtained with one model does not transfer to
   another. Pass --model with the model your run config actually uses.
3. A SEPARATE verdict is evidence of separate buckets, not proof: it is still consistent
   with a shared bucket whose window happened to reset mid-probe. Phase 3 exists to catch
   exactly that and will mark the run INCONCLUSIVE when it does.

Prints only an index and a short digest of each key, never the key itself, so the output
is safe to paste into a thesis appendix or a chat log.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from typing import Callable, NamedTuple, Optional

import requests

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Smallest request that still counts as one request against RPM. The response is
# discarded -- only the HTTP status matters here.
_BODY = {
    "contents": [{"parts": [{"text": "hi"}]}],
    "generationConfig": {"maxOutputTokens": 1},
}


class Probe(NamedTuple):
    """One request's outcome, reduced to the only three distinctions that matter.

    `status` is the HTTP status (0 if the request never completed). `rate_limited` is the
    single derived fact the verdict logic reads; keeping it separate from `status` means
    the classification rule lives in exactly one place (`classify`) instead of being
    re-derived at each call site."""
    status: int
    rate_limited: bool
    detail: str


def classify(status: int, body: str) -> Probe:
    """429 is the documented rate-limit status for the Gemini API, and RESOURCE_EXHAUSTED
    its gRPC-style counterpart (orchestrator/providers/gemini.py's `_RETRYABLE_STATUS`
    treats both as the same case, for the same reason). Both are checked here because
    that adapter's own module docstring records two Google doc pages disagreeing about
    which shape the error body uses."""
    if status == 429 or "RESOURCE_EXHAUSTED" in body:
        return Probe(status, True, "rate limited")
    if status == 200:
        return Probe(status, False, "ok")
    return Probe(status, False, body[:180].replace("\n", " "))


def send(key: str, model: str, timeout: float = 30.0) -> Probe:
    """Real transport. Isolated behind this one function so test_diagnose_key_quota.py can
    substitute a fake and exercise the verdict logic without spending any quota."""
    try:
        r = requests.post(
            _ENDPOINT.format(model=model),
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json=_BODY, timeout=timeout)
    except requests.RequestException as e:
        # Never let a transport failure masquerade as a clean 200; a SEPARATE verdict
        # built on a request that never arrived would be worse than no verdict.
        return Probe(0, False, f"transport error: {type(e).__name__}: {e}")
    return classify(r.status_code, r.text)


def digest(key: str) -> str:
    """Stable short identifier for a key that is not the key. Lets you match a verdict
    back to an entry in GEMINI_API_KEYS without the key material appearing in output."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def read_keys(env_var: str = "GEMINI_API_KEYS") -> list[str]:
    """Same env var and same comma-separated format as
    RotatingKeyAdapter.from_env, so this script sees exactly the key list a real run
    would rotate through -- not a separately-maintained copy that could drift from it."""
    raw = os.environ.get(env_var, "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise SystemExit(
            f"{env_var} is empty or unset. Expected a comma-separated key list, the same "
            f"format orchestrator/providers/rotating.py's from_env() reads.")
    if len(keys) < 2:
        raise SystemExit(
            f"{env_var} holds only one key. There is nothing to compare -- rotation is "
            f"a no-op with one key regardless of which project it belongs to.")
    seen: dict[str, int] = {}
    for i, k in enumerate(keys):
        if k in seen:
            raise SystemExit(
                f"{env_var} entry {i + 1} is byte-identical to entry {seen[k] + 1}. That is "
                f"not a project question -- it is a duplicated key, and rotation across it "
                f"is provably useless. Fix the env var before probing.")
        seen[k] = i
    return keys


def run(
    keys: list[str], model: str, burst: int, anchor: int = 0,
    send_fn: Callable[[str, str], Probe] = lambda k, m: send(k, m),
    sleep_fn: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = lambda s: print(s, flush=True),
) -> int:
    """Returns a process exit code: 0 = a usable verdict was reached, 2 = inconclusive.

    Deliberately does NOT return the verdict as data -- nothing else in this repo consumes
    it, and a one-off diagnostic that grows a return type nobody reads is exactly the
    premature abstraction CLAUDE.md warns about. It prints, and it exits."""
    n = len(keys)
    label = [f"key {i + 1}/{n} ({digest(k)})" for i, k in enumerate(keys)]

    # ---- Phase 0: baseline -------------------------------------------------------
    # Every key must be demonstrably healthy BEFORE the burst. Without this, a key that
    # was already daily-exhausted this morning would 429 in phase 2 and be misread as
    # sharing the anchor's bucket -- the single most likely way to get a confident wrong
    # answer out of this script.
    log(f"Phase 0: baseline, one request per key, model={model!r}")
    healthy: list[int] = []
    for i, k in enumerate(keys):
        p = send_fn(k, model)
        log(f"  {label[i]}: {p.status} {p.detail}")
        if p.status == 200:
            healthy.append(i)
        sleep_fn(1.0)

    if anchor not in healthy:
        log(f"\nINCONCLUSIVE: {label[anchor]} is not usable at baseline, so it cannot "
            f"serve as the anchor. Re-run with --rotate-anchor pointing at a healthy key.")
        return 2
    others = [i for i in healthy if i != anchor]
    if not others:
        log("\nINCONCLUSIVE: no key other than the anchor was healthy at baseline. "
            "Nothing to compare against -- wait for the daily window to reset and re-run.")
        return 2

    # ---- Phase 1: exhaust the anchor's per-minute bucket --------------------------
    log(f"\nPhase 1: bursting {label[anchor]} up to {burst} requests to force a 429")
    hit = 0
    for attempt in range(1, burst + 1):
        p = send_fn(keys[anchor], model)
        if p.rate_limited:
            hit = attempt
            log(f"  rate limited after {attempt} request(s)")
            break
    if not hit:
        log(f"\nINCONCLUSIVE: {burst} requests did not trigger a 429. This key's RPM for "
            f"{model!r} is higher than the burst size. Re-run with a larger --burst.")
        return 2

    # ---- Phase 2: probe the others while the anchor is still limited --------------
    # No sleep here on purpose: the whole test rests on these landing inside the anchor's
    # still-open rate-limit window.
    log("\nPhase 2: probing every other key while the anchor is still limited")
    shared, separate = [], []
    for i in others:
        p = send_fn(keys[i], model)
        (shared if p.rate_limited else separate).append(i)
        log(f"  {label[i]}: {p.status} {p.detail}"
            f"  -> {'SHARED with anchor' if p.rate_limited else 'separate'}")

    # ---- Phase 3: control ---------------------------------------------------------
    # If the anchor recovered while phase 2 was running, its window closed mid-test and
    # every 'separate' reading above is unfalsifiable. Checking is one request.
    log("\nPhase 3: control -- confirm the anchor is still rate limited")
    ctrl = send_fn(keys[anchor], model)
    log(f"  {label[anchor]}: {ctrl.status} {ctrl.detail}")
    if not ctrl.rate_limited:
        log("\nINCONCLUSIVE: the anchor stopped being rate limited before phase 2 "
            "finished, so a non-429 from another key proves nothing. Re-run; if it keeps "
            "happening, raise --burst so the anchor is further past its limit.")
        return 2

    # ---- Verdict ------------------------------------------------------------------
    log("\n" + "=" * 72)
    if shared:
        log(f"VERDICT: {len(shared)} key(s) share {label[anchor]}'s rate-limit bucket:")
        for i in shared:
            log(f"  - {label[i]}")
        log("\nRotating between these buys nothing. orchestrator/providers/rotating.py "
            "will spend one wasted request per key per call re-confirming the same "
            "exhausted bucket, then raise the same 429 it started with.")
    if separate:
        log(f"\n{len(separate)} key(s) served a request while {label[anchor]} was "
            f"limited, so they appear to be separate projects:")
        for i in separate:
            log(f"  - {label[i]}")
        log("\nRotation is doing real work for these. Note this is evidence, not proof "
            "(see the module docstring, 'WHAT IT CANNOT TELL YOU' item 3).")
    if not shared:
        log(f"\nEffective quota is roughly {len(healthy)}x a single project's, for "
            f"model {model!r} only.")
    log("=" * 72)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Check whether GEMINI_API_KEYS holds keys from separate Gemini "
                    "projects (rotation works) or one project (rotation is a no-op).")
    ap.add_argument(
        "--model", default="gemini-3.6-flash",
        help="Model to probe with. Rate limits are per (project, model), so pass the "
             "model your run config actually uses -- a verdict from one model does not "
             "transfer to another. Default: %(default)s")
    ap.add_argument(
        "--burst", type=int, default=40,
        help="Max requests to fire at the anchor key before giving up on forcing a 429. "
             "Default: %(default)s")
    ap.add_argument(
        "--rotate-anchor", type=int, default=1, metavar="N",
        help="1-based index of the key to use as the anchor. Change it to partition the "
             "remaining keys against a different one. Default: %(default)s")
    ap.add_argument(
        "--env-var", default="GEMINI_API_KEYS",
        help="Env var holding the comma-separated key list. Default: %(default)s")
    args = ap.parse_args(argv)

    keys = read_keys(args.env_var)
    anchor = args.rotate_anchor - 1
    if not 0 <= anchor < len(keys):
        raise SystemExit(
            f"--rotate-anchor must be between 1 and {len(keys)}, got {args.rotate_anchor}")
    print(f"Probing {len(keys)} keys from {args.env_var}. This spends quota "
          f"(up to {args.burst + len(keys) + 1} requests). Ctrl-C now to abort.\n")
    return run(keys, args.model, args.burst, anchor)


if __name__ == "__main__":
    sys.exit(main())
