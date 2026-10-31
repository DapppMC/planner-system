"""Shared fixture builder for the verifier / swarm / orchestrator tests: a tiny Python project governed by a signed
Contract-First ticket, with an AG6 sealed vault, a baseline snapshot and a worker workspace.

The domain is a three-bucket classifier because its boundaries (0, 10, 100) have no equivalent mutants, so a good
implementation really does reach the 0.92 kill score and a lazy one really does not.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dag_planner as dp  # noqa: E402
from core import contract_ticket as ct, symbol_check, worktree as wt  # noqa: E402
from core.coordination.blackboard import Board  # noqa: E402
from core.verification import sealed_gate  # noqa: E402

TICKET = "PLT-1-BUCKET"

PLAN = """\
project: demo
epoch: 1
defaults:
  package: demo
  version: 1.0.0
items:
  - id: PLT-1-BUCKET
    goal: Classify numbers into small, medium and large buckets
    touches:
      - src/bucket.py
    symbols:
      - name: bucket
        file: src/bucket.py
        kind: function
        signature: |
          def bucket(n: int) -> str:
    scopes:
      worker_backend:
        formal_invariants:
          engine: hypothesis
          properties:
            - "INV-01: bucket boundaries are 0, 10 and 100"
            - "INV-02: negative input raises ValueError"
      worker_qa_guard:
        allowed_paths: ["tests/property/**"]
"""

STUB = '''"""Bucket classifier."""


def bucket(n: int) -> str:
    raise NotImplementedError
'''

GOOD = '''"""Bucket classifier."""


def bucket(n: int) -> str:
    if n < 0:
        raise ValueError("negative")
    if n < 10:
        return "small"
    if n < 100:
        return "medium"
    return "large"
'''

# passes the weak tests but gets the medium boundary wrong at 100 and ignores negatives
LAZY = '''"""Bucket classifier."""


def bucket(n: int) -> str:
    if n < 10:
        return "small"
    if n < 100:
        return "medium"
    return "large"
'''

SEALED_TESTS = '''import pytest

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

# a weak oracle: only checks the happy path, so many mutants survive
WEAK_SEALED = '''from src.bucket import bucket


def test_inv01_inv02_smoke():
    assert bucket(5) == "small"
'''


class Demo:
    """root/            integration tree (stub committed), .planner/ with tickets, board, sealed vault, baseline
       root/.planner/work/PLT-1-BUCKET/AG5   the worker's workspace (a copy of the tree at lease time)"""

    def __init__(self, base: Optional[Path] = None, *, sealed: Optional[str] = SEALED_TESTS, register: bool = True, materialize: bool = True):
        self._tmp = None
        if base is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="demo_", ignore_cleanup_errors=True)
            base = Path(self._tmp.name)
        self.root = Path(base)
        self.planner = self.root / ".planner"
        self.ticket = TICKET
        self.scope = "worker_backend"
        (self.root / "src").mkdir(parents=True, exist_ok=True)
        (self.root / "src" / "bucket.py").write_text(STUB, encoding="utf-8")
        plan_file = self.root / "plan.yaml"
        plan_file.write_text(PLAN, encoding="utf-8")
        self.compiled = dp.compile_plan(dp.load_plan(plan_file))
        dp.write_plan(self.compiled, self.planner / "tickets", flat=True)
        self.doc = self.compiled.tickets[TICKET]
        self.board_path = self.planner / "board.db"
        self.board = Board(str(self.board_path))
        if register:
            ct.register_contract(self.board, self.doc)
        if materialize:
            symbol_check.materialize_stubs(self.doc, self.root)
        if sealed is not None:
            self.seal(sealed)
        wt.capture(self.root, self.planner / "baselines" / TICKET / "tree")
        self.workspace = self.planner / "work" / TICKET / "AG5"
        wt.capture(self.root, self.workspace)

    def seal(self, source: str, scope: str = "worker_backend") -> dict:
        d = self.planner / "_sealing_src"
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        (d / "test_sealed.py").write_text(source, encoding="utf-8")
        return sealed_gate.seal(f"{TICKET}.{ct.SCOPE_WORKER[scope]}", d, root=self.planner / "sealed")

    def write(self, rel: str, text: str) -> Path:
        p = self.workspace / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def close(self) -> None:
        try:
            self.board.close()
        except Exception:
            pass
        if self._tmp is not None:
            self._tmp.cleanup()

    def __enter__(self) -> "Demo":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
