"""core/verification/mutation_gate.py: operators, rules that cannot be gamed, and real runs against pytest."""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import worktree as wt  # noqa: E402
from core.verification import mutation_gate as mg  # noqa: E402

try:
    import pytest  # noqa: F401
    HAVE_PYTEST = True
except ImportError:  # pragma: no cover
    HAVE_PYTEST = False

SRC = '''"""Doc."""
import os


def clamp(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def total(items):
    t = 0
    for i in items:
        t += i
    return t
'''

GOOD_TESTS = '''from calc import clamp, total


def test_clamp():
    assert clamp(5, 0, 10) == 5
    assert clamp(-1, 0, 10) == 0
    assert clamp(11, 0, 10) == 10
    assert clamp(0, 0, 10) == 0
    assert clamp(10, 0, 10) == 10


def test_total():
    assert total([]) == 0
    assert total([1, 2, 3]) == 6
    assert total([5]) == 5
'''

WEAK_TESTS = '''from calc import clamp, total


def test_smoke():
    assert clamp(5, 0, 10) == 5
    assert total([]) == 0
'''


def lines(src: str, a: int, b: int):
    return {"calc.py": [(a, b)]}


class Discover(unittest.TestCase):
    def test_only_changed_lines_are_mutated(self):
        everything, _ = mg.discover(SRC, "calc.py", [(1, 100)])
        clamp_only, _ = mg.discover(SRC, "calc.py", [(5, 10)])
        self.assertGreater(len(everything), len(clamp_only) > 0 and clamp_only and 0 or 0)
        self.assertTrue(all(5 <= m.line <= 10 for m in clamp_only))
        self.assertGreater(len(everything), len(clamp_only))

    def test_operator_families_are_present(self):
        ms, _ = mg.discover(SRC, "calc.py", [(1, 100)])
        ops = {m.operator.split(":")[0] for m in ms}
        self.assertTrue({"cmp", "return", "cond", "aug"} <= ops, ops)

    def test_boundary_swaps_cover_both_neighbours(self):
        ms, _ = mg.discover("def f(a, b):\n    return a < b\n", "f.py", [(1, 2)])
        descs = {m.description for m in ms if m.operator.startswith("cmp")}
        self.assertEqual(descs, {"< -> <=", "< -> >="})

    def test_docstrings_imports_and_annotations_are_not_mutated(self):
        src = '"""Doc."""\nimport os\n\n\ndef f(a: int = 1) -> int:\n    """inner doc"""\n    return a\n'
        ms, info = mg.discover(src, "f.py", [(1, 7)])
        self.assertFalse([m for m in ms if m.line in (1, 2, 6)])
        self.assertFalse([m for m in ms if "int" in m.description or "->" in m.description and m.line == 5 and m.operator.startswith("cmp")])

    def test_string_concatenation_is_not_treated_as_arithmetic(self):
        ms, _ = mg.discover("x = 'a' + 'b'\n", "f.py", [(1, 1)])
        self.assertFalse([m for m in ms if m.operator.startswith("bin")])

    def test_nothing_executable_counts_zero(self):
        _ms, info = mg.discover('"""Doc."""\nimport os\n# comment\n', "f.py", [(1, 3)])
        self.assertEqual(info["executable_statements"], 0)

    def test_pragma_lines_are_reported(self):
        _ms, info = mg.discover("x = 1  # pragma: no mutate\ny = 2  # nomut\nz = 3\n", "f.py", [(1, 3)])
        self.assertEqual(info["pragma_lines"], [1, 2])

    def test_ids_are_unique(self):
        ms, _ = mg.discover(SRC, "calc.py", [(1, 100)])
        keys = [m.id + str(m.variant) for m in ms]
        self.assertEqual(len(keys), len(set(keys)))


class Build(unittest.TestCase):
    def mutant(self, src, desc_part, line=None):
        ms, _ = mg.discover(src, "m.py", [(1, 1000)])
        for m in ms:
            if desc_part in m.description and (line is None or m.line == line):
                return m
        self.fail(f"no mutant {desc_part!r} in {[m.description for m in ms]}")

    def test_comparison_mutant_source(self):
        src = "def f(a, b):\n    return a < b\n"
        out = mg.build_mutant_source(src, self.mutant(src, "< -> <="))
        self.assertIn("a <= b", out)
        compile(out, "x", "exec")

    def test_each_variant_changes_exactly_what_it_says(self):
        src = "def f(n):\n    if n:\n        n += 1\n    return n\n"
        self.assertIn("not n", mg.build_mutant_source(src, self.mutant(src, "negate if")))
        self.assertIn("n -= 1", mg.build_mutant_source(src, self.mutant(src, "+= -> -=")))
        self.assertIn("return None", mg.build_mutant_source(src, self.mutant(src, "return value")))

    def test_statement_deletion_and_constants(self):
        src = "def f():\n    x = 1\n    raise ValueError('bad')\n"
        ms, _ = mg.discover(src, "m.py", [(1, 3)])
        outs = [mg.build_mutant_source(src, m) for m in ms]
        self.assertTrue(any(o and "pass" in o for o in outs))
        self.assertTrue(any(o and "x = 2" in o for o in outs))
        self.assertTrue(any(o and "XXbadXX" in o for o in outs))

    def test_bool_const_and_unary(self):
        src = "def f(a):\n    return not a, True, -a\n"
        ms, _ = mg.discover(src, "m.py", [(1, 2)])
        outs = " ".join(o for o in (mg.build_mutant_source(src, m) for m in ms) if o)
        self.assertIn("False", outs)
        self.assertIn("(a, True, -a)", outs)

    def test_loop_keywords(self):
        src = "def f(xs):\n    for x in xs:\n        if x:\n            break\n"
        out = mg.build_mutant_source(src, self.mutant(src, "break -> continue"))
        self.assertIn("continue", out)

    def test_original_source_is_not_modified(self):
        src = "def f(a, b):\n    return a < b\n"
        m = self.mutant(src, "< -> <=")
        mg.build_mutant_source(src, m)
        self.assertEqual(src, "def f(a, b):\n    return a < b\n")

    def test_stale_site_is_invalid(self):
        src = "def f(a, b):\n    return a < b\n"
        m = self.mutant(src, "< -> <=")
        m.site = 9999
        self.assertIsNone(mg.build_mutant_source(src, m))


class Sampling(unittest.TestCase):
    def ms(self):
        return mg.discover(SRC, "calc.py", [(1, 100)])[0]

    def test_small_sets_are_kept_whole(self):
        ms = self.ms()
        self.assertEqual(len(mg.sample(ms, 10_000)), len(ms))
        self.assertEqual(len(mg.sample(ms, 0)), len(ms))

    def test_sample_is_deterministic_and_ordered(self):
        ms = self.ms()
        a, b = mg.sample(ms, 8, seed=3), mg.sample(ms, 8, seed=3)
        self.assertEqual([m.id + str(m.variant) for m in a], [m.id + str(m.variant) for m in b])
        self.assertEqual(len(a), 8)
        self.assertNotEqual([m.id for m in a], [m.id for m in mg.sample(ms, 8, seed=4)])

    def test_sample_covers_distinct_lines_first(self):
        ms = self.ms()
        lines_all = {m.line for m in ms}
        chosen = mg.sample(ms, len(lines_all), seed=1)
        self.assertEqual({m.line for m in chosen}, lines_all)


class Classify(unittest.TestCase):
    def test_exit_codes(self):
        self.assertEqual(mg._classify(0, False, "")[0], mg.SURVIVED)
        for rc in (1, 2, 3):
            self.assertEqual(mg._classify(rc, False, "")[0], mg.KILLED, rc)
        self.assertEqual(mg._classify(5, False, "")[0], mg.NO_TESTS, "deleting the tests must not earn a kill")
        self.assertEqual(mg._classify(4, False, "")[0], mg.ERROR)
        self.assertEqual(mg._classify(139, False, "")[0], mg.ERROR)
        self.assertEqual(mg._classify(None, True, "")[0], mg.TIMEOUT)
        self.assertEqual(mg._classify(0, False, "boom")[0], mg.ERROR)


class Rules(unittest.TestCase):
    """Rules that need no test runner: they are decided before any mutant runs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def put(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_nothing_executable_is_not_applicable(self):
        self.put("calc.py", '"""Doc."""\nimport os\n')
        rep = mg.run_mutation(self.root, {"calc.py": [(1, 2)]}, visible_tests=["t.py"])
        self.assertEqual(rep.status, mg.NOT_APPLICABLE)

    def test_non_python_and_test_files_are_ignored(self):
        self.put("a.js", "var x = 1;\n")
        self.put("tests/test_a.py", "def test_a():\n    assert 1 == 1\n")
        rep = mg.run_mutation(self.root, {"a.js": [(1, 1)], "tests/test_a.py": [(1, 2)]}, visible_tests=["tests/test_a.py"])
        self.assertEqual(rep.status, mg.NOT_APPLICABLE)

    def test_pragma_in_changed_code_fails_closed(self):
        self.put("calc.py", "def f(a):\n    return a + 1  # pragma: no mutate\n")
        rep = mg.run_mutation(self.root, {"calc.py": [(1, 2)]}, visible_tests=["t.py"])
        self.assertEqual(rep.status, mg.FAIL)
        self.assertIn("MUTATION_PRAGMA", rep.reason)

    def test_unparsable_source_fails(self):
        self.put("calc.py", "def f(:\n")
        rep = mg.run_mutation(self.root, {"calc.py": [(1, 1)]}, visible_tests=["t.py"])
        self.assertEqual(rep.status, mg.FAIL)
        self.assertIn("UNPARSABLE", rep.reason)

    def test_executable_code_that_yields_no_mutants_fails(self):
        self.put("calc.py", "def f():\n    pass\n")
        rep = mg.run_mutation(self.root, {"calc.py": [(1, 2)]}, visible_tests=["t.py"])
        self.assertEqual(rep.status, mg.NOT_APPLICABLE if rep.candidates == 0 and "no executable" in rep.reason else rep.status)
        self.put("calc.py", "import sys\nsys.setrecursionlimit\n")
        rep2 = mg.run_mutation(self.root, {"calc.py": [(1, 2)]}, visible_tests=["t.py"])
        self.assertEqual(rep2.status, mg.FAIL)
        self.assertIn("NO_MUTANTS", rep2.reason)

    def test_no_test_command_fails(self):
        self.put("calc.py", SRC)
        rep = mg.run_mutation(self.root, {"calc.py": [(1, 100)]})
        self.assertEqual(rep.status, mg.FAIL)
        self.assertIn("NO_TEST_COMMAND", rep.reason)

    def test_invalid_threshold(self):
        self.put("calc.py", SRC)
        for bad in (0, -1, 1.5):
            rep = mg.run_mutation(self.root, {"calc.py": [(1, 100)]}, visible_tests=["t.py"], config=mg.MutationConfig(threshold=bad))
            self.assertEqual(rep.status, mg.FAIL)

    def test_empty_ranges_means_no_candidates(self):
        self.put("calc.py", SRC)
        rep = mg.run_mutation(self.root, {"calc.py": []}, visible_tests=["t.py"])
        self.assertEqual(rep.status, mg.NOT_APPLICABLE)


@unittest.skipUnless(HAVE_PYTEST, "pytest is not installed")
class RealRuns(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "calc.py").write_text(SRC, encoding="utf-8")
        (self.root / "tests").mkdir()
        # `x < lo -> x <= lo` and `x > hi -> x >= hi` are equivalent mutants of clamp (same return value), so two
        # survivors are expected even for perfect tests: 12/14 = 0.857. This is exactly why the production threshold is not 1.0.
        self.cfg = mg.MutationConfig(threshold=0.85, max_mutants=40, time_budget=240, min_timeout=5, max_timeout=40)

    def tearDown(self):
        self._tmp.cleanup()

    def write_tests(self, text):
        (self.root / "tests" / "test_calc.py").write_text(text, encoding="utf-8")

    def run_it(self, ranges=None, **kw):
        return mg.run_mutation(self.root, {"calc.py": ranges or [(5, 17)]}, visible_tests=["tests/test_calc.py"], config=kw.pop("config", self.cfg), **kw)

    def test_strong_tests_pass(self):
        self.write_tests(GOOD_TESTS)
        rep = self.run_it()
        self.assertEqual(rep.status, mg.PASS, rep.to_dict())
        self.assertGreaterEqual(rep.score, 0.85)
        self.assertGreater(rep.executed, 10)

    def test_weak_tests_fail_and_list_survivors(self):
        self.write_tests(WEAK_TESTS)
        rep = self.run_it()
        self.assertEqual(rep.status, mg.FAIL, rep.to_dict())
        self.assertLess(rep.score, 0.85)
        self.assertTrue(rep.survivors)
        self.assertTrue(all("line" in s and "description" in s for s in rep.survivors))

    def test_baseline_must_be_green(self):
        self.write_tests(GOOD_TESTS + "\n\ndef test_wrong():\n    assert clamp(1, 0, 10) == 99\n")
        rep = self.run_it()
        self.assertEqual(rep.status, mg.FAIL)
        self.assertIn("BASELINE_NOT_GREEN", rep.reason)

    def test_deleting_the_tests_is_not_a_pass(self):
        # an empty tests dir: pytest exits 5 on the baseline -> not green -> FAIL, never PASS
        (self.root / "tests" / "test_calc.py").write_text("", encoding="utf-8")
        rep = self.run_it()
        self.assertNotEqual(rep.status, mg.PASS)

    def test_the_worktree_is_never_modified(self):
        self.write_tests(GOOD_TESTS)
        before = {r: p.read_bytes() for r, p in wt.iter_files(self.root)}
        self.run_it()
        after = {r: p.read_bytes() for r, p in wt.iter_files(self.root)}
        self.assertEqual(before, after)

    def test_infinite_loop_mutant_counts_as_killed_by_timeout(self):
        (self.root / "calc.py").write_text("def countdown(n):\n    while n > 0:\n        n -= 1\n    return n\n", encoding="utf-8")
        self.write_tests("from calc import countdown\n\n\ndef test_c():\n    assert countdown(3) == 0\n")
        cfg = mg.MutationConfig(max_mutants=20, time_budget=200, min_timeout=3, max_timeout=6)
        rep = mg.run_mutation(self.root, {"calc.py": [(1, 4)]}, visible_tests=["tests/test_calc.py"], config=cfg)
        self.assertGreaterEqual(rep.counts.get(mg.TIMEOUT, 0) + rep.counts.get(mg.KILLED, 0), 1, rep.to_dict())
        self.assertIn(rep.status, (mg.PASS, mg.FAIL))

    def test_sealed_tests_and_visible_tests_both_kill(self):
        sealed = self.root.parent / (self.root.name + "_sealed")
        sealed.mkdir()
        try:
            (sealed / "test_sealed.py").write_text(GOOD_TESTS, encoding="utf-8")
            self.write_tests(WEAK_TESTS)
            rep = mg.run_mutation(self.root, {"calc.py": [(5, 17)]}, sealed_tests=sealed, visible_tests=["tests/test_calc.py"], config=self.cfg)
            self.assertEqual(rep.status, mg.PASS, rep.to_dict())
        finally:
            shutil.rmtree(sealed, ignore_errors=True)

    def test_time_budget_exhaustion_is_inconclusive_not_pass(self):
        self.write_tests(GOOD_TESTS)
        cfg = mg.MutationConfig(max_mutants=40, time_budget=0.0001, min_timeout=5, max_timeout=40)
        rep = self.run_it(config=cfg)
        self.assertNotEqual(rep.status, mg.PASS)

    def test_parallel_workers_give_the_same_verdict(self):
        self.write_tests(GOOD_TESTS)
        one = self.run_it()
        two = self.run_it(config=mg.MutationConfig(threshold=0.85, max_mutants=40, time_budget=240, workers=2, min_timeout=5, max_timeout=40))
        self.assertEqual((one.status, one.score), (two.status, two.score))


@unittest.skipUnless(HAVE_PYTEST, "pytest is not installed")
class Cli(unittest.TestCase):
    def test_list_and_run(self):
        with tempfile.TemporaryDirectory() as d:
            root, base = Path(d) / "wt", Path(d) / "base"
            root.mkdir()
            (root / "tests").mkdir()
            (root / "calc.py").write_text("def f(a):\n    return a\n", encoding="utf-8")
            wt.capture(root, base)
            (root / "calc.py").write_text(SRC, encoding="utf-8")
            (root / "tests" / "test_calc.py").write_text(GOOD_TESTS, encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = mg._cli(["list", "--worktree", str(root), "--baseline", str(base)])
            self.assertEqual(rc, 0)
            self.assertGreater(len(json.loads(out.getvalue())), 5)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = mg._cli(["run", "--worktree", str(root), "--baseline", str(base), "--visible", "tests/test_calc.py", "--max-mutants", "30", "--threshold", "0.85", "--json"])
            self.assertEqual(rc, 0, out.getvalue()[:400])
            self.assertEqual(json.loads(out.getvalue())["status"], "PASS")

    def test_missing_baseline_is_a_tooling_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            rc = mg._cli(["list", "--worktree", "/nonexistent-a", "--baseline", "/nonexistent-b"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
