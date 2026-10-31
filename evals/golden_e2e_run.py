#!/usr/bin/env python3
"""golden_e2e_run.py - the Planner 4.0 golden end-to-end benchmark (8-worker fleet, scripted agents, no network).

It builds a throw-away project in a private temporary workspace and drives the REAL engine through seven steps:

  1 CONTRACT  AG1  dag_planner compiles a Contract-First plan into an HMAC-signed YAML ticket (two implementation scopes:
                   worker_backend/AG5 and worker_security/AG8), stubs and the SCIP symbol lock are materialised; a tampered
                   copy of the ticket must fail verification.
  2 SEAL      AG6  the Inquisitor writes the acceptance tests from the signed contract; the orchestrator seals them in the vault
                   with an HMAC over the manifest; the oracle must be RED on the stubs; a flipped vault byte, a stripped MAC
                   and a wrong key must all be refused (negative controls).
  3 IMPLEMENT AG5+AG8 implement in parallel in private workspaces with MASP port isolation; AG5's first attempt is deliberately
                   lazy, so the Reflexion loop must reject it with oracle-safe feedback and accept the second; the ports each
                   worker received are disjoint and inside its own ranges; the HMAC keys never reach a worker process.
  4 BOUNTY    AG4  the adversarial property tests are kept, the zero-sum utility is computed, and the same tests must FALSIFY
                   a deliberately broken implementation (they have teeth).
  5 MUTATION       the AST mutation gate reaches the 0.92 kill threshold on the merged tree; the trace-slice plugin turns a
                   synthetic failure into one valid JSON line of <= 400 tokens with secrets redacted.
  6 AUDIT     AG8  the deterministic security + concurrency audit finds nothing at medium or above in the delivered code, flags
                   a vulnerable negative control, and a threaded stress run proves the counter atomic.
  7 VERIFY         verifier.py runs its 12 ordered checks for every implementation scope and exits 0 with a PASS verdict.

Steps 2-5 are phases of ONE Orchestrator.run; each step is judged from that phase's recorded evidence.

Keys: fresh random HMAC keys are generated for this run only, set in this process's environment (PLANNER_LAWGIVER_KEY,
PLANNER_INQUISITOR_KEY), restored afterwards, and never printed or written to a file.

Honest limits: the agents are scripted functions, not Antigravity (the benchmark proves the engine's plumbing and gates, not any
model's quality); the workers are not sandboxed from each other beyond leases, scopes and ports; one ticket is a smoke benchmark,
not a performance measurement.

Usage:  python evals/golden_e2e_run.py [--keep] [--workdir DIR] [--json] [--only 1,2,...] [--quiet]
Exit:   0 every step passed, 1 a step failed, 2 usage error.
Standard library only (pytest is needed by the engine's own test runners, as in every other eval).
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Dict, List, Optional

ENGINE = Path(__file__).resolve().parent.parent
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

import dag_planner as dp  # noqa: E402
import orchestrator as orch  # noqa: E402
import swarm as sw  # noqa: E402
from core import contract_ticket as ct, fleet, symbol_check, worktree as wt  # noqa: E402
from core.verification import appsec_audit, sealed_gate, trace_slice_plugin as tsp  # noqa: E402

TICKET = "GLD-1-SAFE"
LAWGIVER_ENV = "PLANNER_LAWGIVER_KEY"
INQUISITOR_ENV = "PLANNER_INQUISITOR_KEY"
EXPECTED_CHECKS = ["contract", "scope", "secrets", "ports", "forbidden", "symbols", "invariants", "weakening", "visible", "sealed", "mutation", "ui"]

PLAN = """\
project: golden
epoch: 1
defaults:
  package: golden
  version: 1.0.0
items:
  - id: GLD-1-SAFE
    goal: Classify numbers into buckets, sanitise file names and count safely under threads
    touches:
      - src/bucket.py
      - src/security/guard.py
    symbols:
      - name: bucket
        file: src/bucket.py
        kind: function
        signature: |
          def bucket(n: int) -> str:
      - name: safe_name
        file: src/security/guard.py
        kind: function
        signature: |
          def safe_name(name: str) -> str:
      - name: Counter
        file: src/security/guard.py
        kind: class
        signature: |
          class Counter:
              def __init__(self) -> None: ...
              def incr(self, by: int = 1) -> int: ...
              @property
              def value(self) -> int: ...
    scopes:
      worker_backend:
        formal_invariants:
          engine: hypothesis
          properties:
            - "INV-01: bucket boundaries are 0, 10 and 100"
            - "INV-02: negative input raises ValueError"
      worker_security:
        formal_invariants:
          engine: hypothesis
          properties:
            - "INV-03: safe_name rejects separators, dot-dot, NUL, empty and non-string input"
            - "INV-04: safe_name returns an acceptable name unchanged"
            - "INV-05: Counter is atomic under concurrent increments"
      worker_qa_guard:
        allowed_paths: ["tests/property/**"]
"""

BUCKET_GOOD = '''"""Bucket classifier."""


def bucket(n: int) -> str:
    if n < 0:
        raise ValueError("negative")
    if n < 10:
        return "small"
    if n < 100:
        return "medium"
    return "large"
'''

BUCKET_STUB = '''"""Bucket classifier."""


def bucket(n: int) -> str:
    raise NotImplementedError
'''

GUARD_STUB = '''"""Name sanitiser and thread-safe counter."""


def safe_name(name: str) -> str:
    raise NotImplementedError


class Counter:
    """A counter whose increments are atomic."""

    def __init__(self) -> None:
        raise NotImplementedError

    def incr(self, by: int = 1) -> int:
        raise NotImplementedError

    @property
    def value(self) -> int:
        raise NotImplementedError
'''

# lazy: right on the happy path, forgets the negative check -> the sealed INV-02 test must reject it
BUCKET_LAZY = '''"""Bucket classifier."""


def bucket(n: int) -> str:
    if n < 10:
        return "small"
    if n < 100:
        return "medium"
    return "large"
'''

GUARD_GOOD = '''"""Name sanitiser and thread-safe counter."""
import threading


def safe_name(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise ValueError("invalid name")
    if "/" in name or "\\\\" in name or ".." in name or "\\x00" in name:
        raise ValueError("unsafe name")
    return name


class Counter:
    """A counter whose increments are atomic."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._n = 0

    def incr(self, by: int = 1) -> int:
        with self._lock:
            self._n += by
            return self._n

    @property
    def value(self) -> int:
        with self._lock:
            return self._n
'''

# the broken twin used to prove AG4's tests have teeth: no traversal check, and a racy increment
GUARD_BROKEN = '''"""Name sanitiser and thread-safe counter (BROKEN on purpose)."""
import time


def safe_name(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise ValueError("invalid name")
    return name


class Counter:
    def __init__(self) -> None:
        self._n = 0

    def incr(self, by: int = 1) -> int:
        cur = self._n
        time.sleep(0)
        self._n = cur + by
        return self._n

    @property
    def value(self) -> int:
        return self._n
'''

SEALED_BACKEND = '''import pytest

from src.bucket import bucket


def test_inv01_buckets_by_boundaries():
    assert bucket(0) == "small"
    assert bucket(9) == "small"
    assert bucket(10) == "medium"
    assert bucket(99) == "medium"
    assert bucket(100) == "large"
    assert bucket(10_000) == "large"


def test_inv02_negative_rejected():
    with pytest.raises(ValueError, match="negative"):
        bucket(-1)
'''

SEALED_SECURITY = '''import threading

import pytest

from src.security.guard import Counter, safe_name


@pytest.mark.parametrize("bad", ["a/b", "a\\\\b", "..", "a..b", "x\\x00y", "/etc/passwd", "..\\\\up"])
def test_inv03_unsafe_names_rejected(bad):
    with pytest.raises(ValueError, match="unsafe name"):
        safe_name(bad)


@pytest.mark.parametrize("bad", ["", None, 5, b"abc", ["a"]])
def test_inv03_invalid_input_rejected(bad):
    with pytest.raises(ValueError, match="invalid name"):
        safe_name(bad)


@pytest.mark.parametrize("good", ["report.txt", "a", "a.b", "name-with_chars 1", ".hidden"])
def test_inv04_good_names_unchanged(good):
    assert safe_name(good) == good


def test_inv05_counter_arithmetic():
    c = Counter()
    assert c.value == 0
    assert c.incr() == 1
    assert c.incr(5) == 6
    assert c.incr(-2) == 4
    assert c.value == 4


def test_inv05_counter_is_atomic_under_threads():
    c = Counter()
    threads = [threading.Thread(target=lambda: [c.incr() for _ in range(400)]) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert c.value == 3200
'''

AG4_PROPERTIES = '''import random
import threading

import pytest

from src.bucket import bucket
from src.security.guard import Counter, safe_name

ORDER = {"small": 0, "medium": 1, "large": 2}


def test_bucket_is_monotone_and_total():
    rng = random.Random(1234)
    last = 0
    for n in sorted(rng.randrange(0, 10_000) for _ in range(600)):
        cur = ORDER[bucket(n)]
        assert cur >= last
        last = cur


def test_bucket_rejects_every_negative():
    rng = random.Random(99)
    for _ in range(200):
        with pytest.raises(ValueError):
            bucket(-rng.randrange(1, 10_000))


def test_safe_name_never_lets_traversal_through():
    rng = random.Random(7)
    alphabet = "ab./\\\\\\x00 -_"
    for _ in range(3000):
        s = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 9)))
        try:
            out = safe_name(s)
        except ValueError:
            continue
        assert "/" not in out and "\\\\" not in out and ".." not in out and "\\x00" not in out and out


def test_counter_never_loses_an_increment():
    c = Counter()
    threads = [threading.Thread(target=lambda: [c.incr() for _ in range(2000)]) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert c.value == 8000
'''

VULNERABLE_SAMPLE = '''import os
import sqlite3
import threading

API_KEY = "s3cr3t-value-0123456789"
CACHE = {}


class Racy:
    def __init__(self):
        self._lock = threading.Lock()
        self.n = 0

    def bump(self):
        self.n += 1


def load(base, name, conn):
    conn.execute(f"select * from files where name = '{name}'")
    return open(os.path.join(base, name)).read()


def run(cmd):
    os.system(cmd)
    return eval(cmd)


def remember(k, v):
    CACHE[k] = v
'''

SYNTHETIC_FAILURE = '''def test_synthetic_failure():
    api_token = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    order_total = 41
    assert order_total == 42 and api_token == ""
'''


def _write(base: Path, rel: str, text: str) -> None:
    p = Path(base) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class StepFailed(AssertionError):
    pass


def need(cond: bool, message: str) -> None:
    if not cond:
        raise StepFailed(message)


class Golden:
    def __init__(self, workdir: Path, quiet: bool = False):
        self.work = Path(workdir)
        self.root = self.work / "project"
        self.planner = self.root / ".planner"
        self.quiet = quiet
        self.keys = {LAWGIVER_ENV: secrets.token_hex(32), INQUISITOR_ENV: secrets.token_hex(32)}
        self._saved: Dict[str, Optional[str]] = {}
        self.doc: dict = {}
        self.compiled = None
        self.outcome = None
        self.calls: List[tuple] = []
        self.envs: Dict[str, dict] = {}
        self.leaks: List[str] = []
        self.lock = threading.Lock()
        self.results: List[dict] = []
        self.facts: Dict[str, object] = {}

    # -- environment ---------------------------------------------------------------------------------
    def __enter__(self) -> "Golden":
        for k, v in self.keys.items():
            self._saved[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, *exc) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def child_env(self) -> dict:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ENGINE) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    # -- the scripted fleet ----------------------------------------------------------------------------
    def agent(self, task, env, ctx):
        w = task.worker
        with self.lock:
            n = sum(1 for c in self.calls if c[0] == w and not c[2])
            self.calls.append((w, task.tier, "acting as arbiter" in task.prompt))
            if w in ("AG5", "AG8"):
                self.envs.setdefault(w, dict(env))
            for k, v in env.items():
                if v and (v in self.keys.values() or k in self.keys):
                    self.leaks.append(f"{w}:{k}")
        ws = ctx.workspace
        if "acting as arbiter" in task.prompt:
            return "VERDICT: REJECTED\nthe claim contradicts the signed contract"
        if w == "AG6":
            _write(ws, "tests/acceptance/AG5/test_sealed.py", SEALED_BACKEND)
            _write(ws, "tests/acceptance/AG8/test_sealed.py", SEALED_SECURITY)
            return "REFLECTION: wrote one test per INV-NN from the signed contract, boundaries first"
        if w == "AG5":
            _write(ws, "src/bucket.py", BUCKET_LAZY if n == 0 else BUCKET_GOOD)
            return "REFLECTION: " + ("implemented the three buckets" if n == 0 else "the failing id was INV-02, so negative input must raise ValueError")
        if w == "AG8":
            _write(ws, "src/security/guard.py", GUARD_GOOD)
            return "REFLECTION: validate before use; lock every mutation of shared state"
        if w == "AG4":
            _write(ws, "tests/property/test_props.py", AG4_PROPERTIES)
            return "REFLECTION: attacked boundaries, traversal alphabet and increment atomicity"
        return ""

    # -- steps -----------------------------------------------------------------------------------------
    def step1_contract(self) -> str:
        _write(self.root, "src/bucket.py", BUCKET_STUB)
        _write(self.root, "src/security/guard.py", GUARD_STUB)
        _write(self.root, "plan.yaml", PLAN)
        key = ct.key_from_env(LAWGIVER_ENV)
        need(key is not None and len(key) >= 32, "the Lawgiver key was not provisioned")
        self.compiled = dp.compile_plan(dp.load_plan(self.root / "plan.yaml"), key=key)
        dp.write_plan(self.compiled, self.planner / "tickets", flat=True)
        self.doc = self.compiled.tickets[TICKET]
        sig = self.doc["ticket_meta"]["lawgiver_signature"]
        need(sig.startswith("hmac-sha256:"), f"the contract is not HMAC-signed ({sig[:14]})")
        need(self.compiled.dag.get("signature_kind") == "hmac-sha256", "dag.json does not record the signature kind")
        rep = ct.validate(self.doc, check_signature=True, key=key)
        need(rep.ok, f"the signed contract does not validate: {rep.errors}")
        scopes = sorted(self.doc["worker_scopes"])
        need({"worker_backend", "worker_security", "worker_qa_guard"} <= set(scopes), f"scopes {scopes}")
        # negative controls: a tampered ticket and a wrong key must fail
        forged = json.loads(json.dumps(self.doc))
        forged["ticket_meta"]["goal"] = forged["ticket_meta"].get("goal", "") + " (edited by an attacker)"
        ok, _why = ct.verify_signature(forged, key)
        need(not ok, "a tampered ticket still verified")
        ok, _why = ct.verify_signature(self.doc, secrets.token_hex(32).encode())
        need(not ok, "a ticket verified under the wrong key")
        stubs = symbol_check.materialize_stubs(self.doc, self.root)
        need(len(stubs) == 3 and all(x.startswith("contracts/") for x in stubs), f"signature stubs not written: {stubs}")
        need(bool(self.compiled.symbols) and len(self.compiled.symbols) == 3 and all(sym.get("lock") for sym in self.compiled.symbols.values()), f"symbol lock is incomplete: {list(self.compiled.symbols)}")
        wt.capture(self.root, self.planner / "baselines" / TICKET / "tree")
        return f"HMAC-signed {TICKET}: {len(scopes)} scopes, {len(self.compiled.symbols)} locked symbols, tamper + wrong key refused"

    def run_pipeline(self) -> None:
        pol = orch.Policy(claim_wait=5.0, mutation_max=120, mutation_budget=420, sealed_timeout=120, visible_timeout=120, max_attempts=4)
        t0 = time.monotonic()
        with orch.Orchestrator(self.root, sw.CallableExecutor(self.agent), policy=pol) as o:
            summary = o.run()
            self.outcome = summary.outcomes.get(TICKET)
            self.facts["state"] = o.board.get_ticket(TICKET)["state"] if self.outcome else "?"
            self.facts["events"] = sorted({e["kind"] for e in o.board.events_for(TICKET)})
        self.facts["pipeline_seconds"] = round(time.monotonic() - t0, 1)

    def step2_seal(self) -> str:
        need(self.outcome is not None, "the orchestrator produced no outcome")
        sroot = self.planner / "sealed"
        for wid in ("AG5", "AG8"):
            vid = f"{TICKET}.{wid}"
            ok, problems, manifest = sealed_gate.verify_vault(vid, sroot)
            need(ok, f"{vid}: {problems}")
            need(bool(manifest.get("hmac")) or bool(manifest.get("mac")) or "hmac" in json.dumps(manifest).lower(), f"{vid}: the manifest carries no MAC")
        need("sealed" in self.facts["events"], f"no 'sealed' event on the board: {self.facts['events']}")
        # negative controls on a COPY of the vault: a wrong key, a stripped MAC and a flipped byte must all be refused
        copy = self.work / "vault_copy"
        shutil.copytree(sroot, copy)
        vid = f"{TICKET}.AG8"
        ok, problems, _m = sealed_gate.verify_vault(vid, copy, hmac_key=secrets.token_hex(32))
        need(not ok, "the vault verified under the wrong HMAC key")
        manifest_path = next(p for p in (copy / vid).rglob("*") if p.is_file() and p.name.startswith("manifest"))
        manifest_path.chmod(0o600)
        original = manifest_path.read_text(encoding="utf-8")
        data = json.loads(original)
        stripped = {k: v for k, v in data.items() if "mac" not in k.lower()}
        manifest_path.write_text(json.dumps(stripped, indent=2, sort_keys=True), encoding="utf-8")
        ok, problems, _m = sealed_gate.verify_vault(vid, copy)
        need(not ok, "a vault with its MAC stripped verified")
        manifest_path.write_text(original, encoding="utf-8")
        victim = next(p for p in (copy / vid).rglob("test_sealed.py"))
        victim.chmod(0o600)
        victim.write_text(victim.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
        ok, problems, _m = sealed_gate.verify_vault(vid, copy)
        need(not ok, "a vault with a modified test verified")
        # the oracle was RED on the stubs (otherwise the orchestrator refuses it as vacuous); prove it again on a fresh stub tree
        stub_tree = self.work / "stub_tree"
        wt.capture(self.planner / "baselines" / TICKET / "tree", stub_tree)
        res = sealed_gate.run_isolated_verification(stub_tree, f"{TICKET}.AG5", root=sroot, timeout=120, python=sys.executable)
        need(not res.passed, "the sealed backend oracle passes against the unimplemented stub (vacuous)")
        return "2 vaults HMAC-authenticated; wrong key, stripped MAC and a tampered test refused; oracle red on the stubs"

    def step3_implement(self) -> str:
        oc = self.outcome
        need(oc.state == "DONE", f"ticket state {oc.state}: {oc.reason}")
        a5 = sum(1 for c in self.calls if c[0] == "AG5" and not c[2])
        need(a5 >= 2, f"AG5 ran {a5} time(s); the lazy first attempt should have been rejected by the oracle and retried")
        need((self.root / "src" / "bucket.py").read_text(encoding="utf-8") == BUCKET_GOOD, "the delivered bucket.py is not the corrected one")
        need((self.root / "src" / "security" / "guard.py").read_text(encoding="utf-8") == GUARD_GOOD, "the delivered guard.py differs from AG8's work")
        e5, e8 = self.envs.get("AG5"), self.envs.get("AG8")
        need(bool(e5) and bool(e8), "the workers received no task environment")
        r5, r8 = fleet.get("AG5"), fleet.get("AG8")
        for w, e in ((r5, e5), (r8, e8)):
            dev, api = int(e["DEV_PORT"]), int(e["API_PORT"])
            need(w.dev[0] <= dev <= w.dev[1], f"{w.id} DEV_PORT {dev} is outside {w.dev}")
            need(w.api[0] <= api <= w.api[1], f"{w.id} API_PORT {api} is outside {w.api}")
            need(e.get("MASP_DEV_RANGE") == f"{w.dev[0]}-{w.dev[1]}" and e.get("MASP_API_RANGE") == f"{w.api[0]}-{w.api[1]}", f"{w.id} MASP ranges {e.get('MASP_DEV_RANGE')}/{e.get('MASP_API_RANGE')}")
        need({int(e5["DEV_PORT"]), int(e5["API_PORT"])}.isdisjoint({int(e8["DEV_PORT"]), int(e8["API_PORT"])}), "AG5 and AG8 were given overlapping ports")
        need(not self.leaks, f"an HMAC key reached a worker environment: {self.leaks}")
        # the static port scan flags a bind into another worker's range and accepts the worker's own
        bad = fleet.violations(fleet.scan_text("app.listen(3000)\n", "AG5", file="x.js"))
        good = fleet.violations(fleet.scan_text("app.listen(3450)\n", "AG5", file="x.js"))
        need(bool(bad) and not good, "the static port scan did not separate a foreign port from an own port")
        problems = fleet.check_independence({"authored_by": "AG1", "sealed_by": "AG6", "implemented_by": ["AG5", "AG8"], "attacked_by": "AG4", "arbiter": "AG6"})
        need(problems == [], f"separation of powers: {problems}")
        tiers = {w: t for w, t, arb in self.calls if not arb}
        need(tiers.get("AG6") == "standard" and tiers.get("AG5") == "standard", f"tiers {tiers}: only the lead may be premium")
        return (f"DONE; AG5 retried after an oracle rejection ({a5} attempts), AG8 parallel; ports AG5 {e5['DEV_PORT']}/{e5['API_PORT']}, "
                f"AG8 {e8['DEV_PORT']}/{e8['API_PORT']} disjoint and in range; keys never reached a worker")

    def step4_bounty(self) -> str:
        led = self.outcome.ledger
        need(led.get("utility") is not None, f"no zero-sum utility in the ledger: {led}")
        props = self.root / "tests" / "property" / "test_props.py"
        need(props.is_file(), "AG4's property tests were not kept")
        need(any(c[0] == "AG4" for c in self.calls), "AG4 was never called")
        # teeth: the same tests must falsify a broken implementation
        broken = self.work / "broken_tree"
        wt.capture(self.root, broken)
        _write(broken, "src/security/guard.py", GUARD_BROKEN)
        _write(broken, "src/bucket.py", BUCKET_LAZY)
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "tests/property"], cwd=broken, env=self.child_env(),
                           capture_output=True, text=True, timeout=180)
        need(r.returncode != 0, "AG4's property tests passed against a broken implementation (no teeth)")
        r_all = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/property"], cwd=broken, env=self.child_env(),
                               capture_output=True, text=True, timeout=180)
        failed = r_all.stdout.count(" FAILED") + r_all.stdout.count("\nFAILED")
        failed = max(failed, len([ln for ln in r_all.stdout.splitlines() if ln.startswith("FAILED")]))
        need(failed >= 2, f"only {failed} property test(s) falsified the broken tree:\n{r_all.stdout[-600:]}")
        r_good = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/property"], cwd=self.root, env=self.child_env(),
                                capture_output=True, text=True, timeout=180)
        need(r_good.returncode == 0, f"AG4's tests fail on the delivered code:\n{r_good.stdout[-600:]}")
        return f"utility {led['utility']:.2f}; the property tests falsify {failed} of 4 on a broken tree and pass on the delivered code"

    def step5_mutation(self) -> str:
        led = self.outcome.ledger
        ratio = led.get("kill_ratio")
        need(ratio is not None and ratio >= 0.92, f"mutation kill ratio {ratio} is below 0.92")
        # trace-slice on a synthetic failure
        d = self.work / "trace_case"
        d.mkdir()
        _write(d, "test_synth.py", SYNTHETIC_FAILURE)
        out = d / "slices.jsonl"
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "core.verification.trace_slice_plugin", f"--trace-slice-out={out}",
                            "test_synth.py"], cwd=d, env=self.child_env(), capture_output=True, text=True, timeout=120)
        need(r.returncode == 1, f"the synthetic test should fail (exit {r.returncode}): {r.stdout[-300:]}")
        slices = tsp.read_slices(str(out))
        need(len(slices) == 1, f"expected exactly one trace slice, got {len(slices)}")
        raw = out.read_text(encoding="utf-8")
        need("ghp_" not in raw and "A1b2C3d4" not in raw, "a secret survived in the trace slice")
        for line in raw.splitlines():
            json.loads(line)
        tokens = tsp.estimate_tokens(tsp.fit_budget(slices[0], 400))
        need(tokens <= 400, f"the slice costs {tokens} tokens")
        frames = slices[0]["frames"]
        local = {k: v for fr in frames for k, v in fr["locals"].items()}
        need("order_total" in local and "41" in str(local["order_total"]), f"the failing value is missing from the slice: {local}")
        need("api_token" not in local or "ghp_" not in str(local["api_token"]), "the token local was not redacted")
        return f"mutation kill ratio {ratio:.3f} >= 0.92; trace slice {tokens} tokens, valid JSON, token redacted"

    def step6_audit(self) -> str:
        rep = appsec_audit.audit_tree(self.root, ["src/bucket.py", "src/security/guard.py"])
        bad = rep.at_least("medium")
        need(not bad, "the delivered code has findings: " + "; ".join(str(f) for f in bad))
        need(sorted(rep.audited) == ["src/bucket.py", "src/security/guard.py"], f"audited {rep.audited}")
        # negative control: the auditor must flag the vulnerable sample on every axis it claims to cover
        found = {f.rule for f in appsec_audit.audit_source(VULNERABLE_SAMPLE, "vuln.py")}
        expect = {"SEC-HARDCODED-SECRET", "SEC-SQL", "SEC-PATH", "SEC-SHELL", "SEC-EVAL", "CONC-UNLOCKED-STATE", "CONC-GLOBAL-MUTATION"}
        need(expect <= found, f"the audit missed {sorted(expect - found)} on the vulnerable control")
        broken = {f.rule for f in appsec_audit.audit_source(GUARD_BROKEN, "broken.py")}
        # the racy twin has no lock at all, so it is not statically judged; the stress run below is what separates them
        # concurrency stress on the delivered counter, in this process
        mod = _load_module(self.root / "src" / "security" / "guard.py", "golden_guard")
        c = mod.Counter()
        start = threading.Barrier(8)

        def work() -> None:
            start.wait()
            for _ in range(2000):
                c.incr()

        ts = [threading.Thread(target=work) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        need(c.value == 16000, f"lost updates: {c.value} != 16000")
        # fuzz the sanitiser with a seeded generator
        import random
        rng = random.Random(20261025)
        alphabet = "ab./\\\x00 -_~%"
        accepted = 0
        for _ in range(5000):
            s = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 10)))
            try:
                out = mod.safe_name(s)
            except ValueError:
                continue
            accepted += 1
            need(out == s and "/" not in out and "\\" not in out and ".." not in out and "\x00" not in out and out != "", f"unsafe name accepted: {out!r}")
        need(accepted > 100, "the fuzz run accepted almost nothing; the generator is useless")
        leaked = []
        for dirpath, _dirs, files in os.walk(self.work):
            for f in files:
                fp = Path(dirpath) / f
                try:
                    if fp.stat().st_size > 2_000_000 or fp.is_symlink():
                        continue
                    blob = fp.read_bytes()
                except OSError:
                    continue
                if any(v.encode() in blob for v in self.keys.values()):
                    leaked.append(str(fp.relative_to(self.work)))
        need(not leaked, f"HMAC key material was written to disk: {leaked[:5]}")
        arbiter = fleet.get("AG8").kind
        need(arbiter == fleet.ARBITER, "AG8 is not the arbiter in the fleet table")
        return (f"{len(rep.audited)} files clean at medium+; vulnerable control flagged ({len(found)} rules); 16000/16000 increments under 8 threads; "
                f"{accepted} fuzz names accepted, none unsafe; no key on disk; broken twin rules {sorted(broken) or 'none (stress run covers it)'}")

    def step7_verify(self) -> str:
        details = []
        # each implementer is verified in ITS OWN private workspace: only that scope's changes are in the diff, and only that scope's
        # sealed vault judges it (the cross-scope union is the orchestrator's integration gate, covered by step 5)
        for scope, wid in (("worker_backend", "AG5"), ("worker_security", "AG8")):
            workspace = self.planner / "work" / TICKET / wid
            need(workspace.is_dir(), f"{wid}'s workspace is gone: {workspace}")
            r = subprocess.run([sys.executable, str(ENGINE / "verifier.py"), "run", "--ticket", TICKET, "--scope", scope, "--root", str(self.root),
                                "--workspace", str(workspace), "--level", "full", "--max-mutants", "120", "--mutation-budget", "420", "--json"],
                               cwd=self.root, env=self.child_env(), capture_output=True, text=True, timeout=900)
            try:
                data = json.loads(r.stdout)
            except ValueError:
                data = {}
            bad = [f"{c['name']}={c['status']} ({c.get('detail', '')[:160]} {c.get('items', [])[:2]})" for c in data.get("checks", []) if c.get("status") not in ("pass", "na", "skip")]
            need(r.returncode == 0, f"verifier exit {r.returncode} for {scope}, verdict {data.get('verdict')}: {'; '.join(bad) or r.stdout[-600:] + r.stderr[-300:]}")
            verdict = data.get("verdict")
            need(verdict == "PASS", f"{scope}: verdict {verdict}")
            names = [c["name"] for c in data.get("checks", [])]
            need(names == EXPECTED_CHECKS, f"{scope}: checks {names}")
            statuses = {c["name"]: c.get("status") for c in data["checks"]}
            need(all(s in ("pass", "na") for s in statuses.values()), f"{scope}: {statuses}")
            need(statuses["sealed"] == "pass" and statuses["mutation"] == "pass" and statuses["contract"] == "pass", f"{scope}: {statuses}")
            details.append(f"{scope}={verdict}")
        return "12 ordered checks, exit 0: " + ", ".join(details)


def _load_module(path: Path, name: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


STEPS = [
    (1, "CONTRACT  (AG1 Lawgiver)", "step1_contract"),
    (2, "SEAL      (AG6 Inquisitor)", "step2_seal"),
    (3, "IMPLEMENT (AG5 + AG8, MASP)", "step3_implement"),
    (4, "BOUNTY    (AG4 zero-sum)", "step4_bounty"),
    (5, "MUTATION  (gate + trace slice)", "step5_mutation"),
    (6, "AUDIT     (AG8 Arbiter)", "step6_audit"),
    (7, "VERIFY    (12 ordered checks)", "step7_verify"),
]


def run(workdir: Path, only: Optional[List[int]] = None, quiet: bool = False) -> dict:
    t0 = time.monotonic()
    with Golden(workdir, quiet=quiet) as g:
        results: List[dict] = []
        pipeline_error = ""
        failed_earlier = False

        def record(n: int, name: str, fn: Callable[[], str]) -> None:
            nonlocal failed_earlier
            t = time.monotonic()
            try:
                detail = fn()
                status = "PASS"
            except StepFailed as exc:
                detail, status = str(exc), "FAIL"
            except Exception as exc:  # an unexpected crash is a failure with a traceback, never a pass
                detail, status = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}", "FAIL"
            if status == "FAIL":
                failed_earlier = True
            results.append({"step": n, "name": name.strip(), "status": status, "seconds": round(time.monotonic() - t, 1), "detail": detail})
            if not quiet:
                print(f"[{status}] step {n} {name}: {detail.splitlines()[0] if detail else ''}", flush=True)

        wanted = set(only or [n for n, _, _ in STEPS])
        for n, name, meth in STEPS:
            if n not in wanted:
                continue
            if n == 2 and not g.outcome:
                try:
                    g.run_pipeline()
                except Exception as exc:
                    pipeline_error = f"{type(exc).__name__}: {exc}"
            if failed_earlier and n > 1:
                results.append({"step": n, "name": name.strip(), "status": "SKIPPED", "seconds": 0.0, "detail": "an earlier step failed"})
                if not quiet:
                    print(f"[SKIP] step {n} {name}", flush=True)
                continue
            if n >= 2 and pipeline_error:
                record(n, name, lambda: (_ for _ in ()).throw(StepFailed(f"the orchestrated run crashed: {pipeline_error}")))
                continue
            record(n, name, getattr(g, meth))
        ok = bool(results) and all(r["status"] == "PASS" for r in results)
        return {"ok": ok, "seconds": round(time.monotonic() - t0, 1), "steps": results,
                "facts": {k: v for k, v in g.facts.items()}, "engine": "planner-4.0", "fleet": [w.id for w in fleet.FLEET.values()]}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="golden_e2e_run", description="Planner 4.0 golden end-to-end benchmark")
    ap.add_argument("--workdir", help="use this (empty or new) directory instead of a temporary one")
    ap.add_argument("--keep", action="store_true", help="keep the working directory and print its path")
    ap.add_argument("--json", action="store_true", help="print only the JSON report")
    ap.add_argument("--only", help="comma separated step numbers (steps 2-6 still run the orchestrated pipeline once)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    only = None
    if a.only:
        try:
            only = sorted({int(x) for x in a.only.split(",") if x.strip()})
        except ValueError:
            print("golden: --only wants numbers like 1,2,3", file=sys.stderr)
            return 2
        if not only or any(n < 1 or n > 7 for n in only):
            print("golden: step numbers are 1..7", file=sys.stderr)
            return 2
    if a.workdir:
        wd = Path(a.workdir)
        if wd.exists() and any(wd.iterdir()):
            print(f"golden: {wd} is not empty", file=sys.stderr)
            return 2
        wd.mkdir(parents=True, exist_ok=True)
        tmp = None
    else:
        tmp = tempfile.mkdtemp(prefix="planner4_golden_")
        wd = Path(tmp)
    try:
        report = run(wd, only, quiet=a.quiet or a.json)
    finally:
        if not a.keep and tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    if a.keep or a.workdir:
        report["workdir"] = str(wd)
    if a.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"\ngolden run: {'PASS' if report['ok'] else 'FAIL'} in {report['seconds']}s "
              f"({sum(1 for s in report['steps'] if s['status'] == 'PASS')}/{len(report['steps'])} steps)")
        for s in report["steps"]:
            if s["status"] == "FAIL":
                print(f"\n--- step {s['step']} {s['name']} ---\n{s['detail']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
