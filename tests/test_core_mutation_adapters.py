"""core/verification/mutation_adapters.py: Stryker JSON, mutmut output, and a real mutmut run where available."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.platform_compat import IS_WINDOWS  # noqa: E402
from core.verification import mutation_adapters as ma  # noqa: E402
from core.verification import mutation_gate as mg  # noqa: E402

try:
    import mutmut  # noqa: F401
    HAVE_MUTMUT = True
except Exception:  # pragma: no cover
    HAVE_MUTMUT = False


def mutant(line, status, name="ArithmeticOperator", repl="a - b"):
    return {"id": f"m{line}{status}", "mutatorName": name, "replacement": repl, "status": status,
            "location": {"start": {"line": line, "column": 1}, "end": {"line": line, "column": 9}}}


def report(files):
    return {"schemaVersion": "1", "thresholds": {"high": 80, "low": 60}, "files": {k: {"language": "typescript", "source": "", "mutants": v} for k, v in files.items()}}


class Score(unittest.TestCase):
    def test_pass_and_fail(self):
        self.assertEqual(ma.score_counts(23, 2, 0, 0, 0.92).status, mg.PASS)
        r = ma.score_counts(8, 1, 1, 0, 0.92)
        self.assertEqual(r.status, mg.FAIL)
        self.assertEqual(r.score, 0.8)

    def test_no_result_is_inconclusive_never_pass(self):
        self.assertEqual(ma.score_counts(0, 0, 0, 5, 0.92).status, mg.INCONCLUSIVE)

    def test_too_many_errors_make_a_pass_inconclusive(self):
        self.assertEqual(ma.score_counts(10, 0, 0, 6, 0.92).status, mg.INCONCLUSIVE)
        self.assertEqual(ma.score_counts(10, 0, 0, 2, 0.92).status, mg.PASS)

    def test_bad_threshold(self):
        self.assertEqual(ma.score_counts(1, 0, 0, 0, 0).status, mg.FAIL)


class Stryker(unittest.TestCase):
    def test_command_uses_native_line_ranges(self):
        cmd = ma.stryker_command({"src/a.ts": [(3, 9), (20, 22)], "src/b.ts": [(1, 4)]}, concurrency=2)
        self.assertEqual(cmd[:4], ["npx", "--no-install", "stryker", "run"])
        self.assertIn("src/a.ts:3-9", cmd)
        self.assertIn("src/a.ts:20-22", cmd)
        self.assertIn("src/b.ts:1-4", cmd)
        self.assertEqual(cmd[cmd.index("--concurrency") + 1], "2")
        self.assertEqual(sum(1 for c in cmd if c == "--mutate"), 3)

    def test_statuses_are_scored_the_stryker_way(self):
        rep = report({"src/a.ts": [mutant(1, "Killed"), mutant(2, "Timeout"), mutant(3, "Survived"), mutant(4, "NoCoverage"),
                                   mutant(5, "CompileError"), mutant(6, "Ignored")]})
        r = ma.parse_stryker(rep, threshold=0.5)
        self.assertEqual(r.counts[mg.KILLED], 2)
        self.assertEqual(r.counts[mg.SURVIVED], 1)
        self.assertEqual(r.counts[mg.NO_TESTS], 1)
        self.assertEqual(r.counts[mg.ERROR], 2)
        self.assertEqual(r.score, 0.5)
        self.assertEqual(r.status, mg.PASS)
        self.assertEqual({s["line"] for s in r.survivors}, {3, 4})

    def test_only_changed_lines_count(self):
        rep = report({"src/a.ts": [mutant(1, "Survived"), mutant(2, "Survived"), mutant(10, "Killed"), mutant(11, "Killed")]})
        whole = ma.parse_stryker(rep, threshold=0.9)
        scoped = ma.parse_stryker(rep, {"src/a.ts": [(10, 11)]}, threshold=0.9)
        self.assertEqual(whole.status, mg.FAIL)
        self.assertEqual(scoped.status, mg.PASS)
        scoped_bad = ma.parse_stryker(rep, {"src/a.ts": [(1, 2)]}, threshold=0.9)
        self.assertEqual(scoped_bad.status, mg.FAIL)

    def test_changed_file_without_mutants_fails_closed(self):
        r = ma.parse_stryker(report({"src/a.ts": [mutant(1, "Killed")]}), {"src/a.ts": [(50, 60)]})
        self.assertEqual(r.status, mg.FAIL)
        self.assertIn("NO_MUTANTS", r.reason)
        r2 = ma.parse_stryker(report({"src/a.ts": [mutant(1, "Killed")]}), {"src/other.ts": [(1, 5)]})
        self.assertEqual(r2.status, mg.FAIL)

    def test_path_forms_are_matched(self):
        rep = report({"packages/web/src/a.ts": [mutant(2, "Killed")]})
        self.assertEqual(ma.parse_stryker(rep, {"src/a.ts": [(1, 3)]}, threshold=0.9).status, mg.PASS)
        rep = report({"src\\a.ts": [mutant(2, "Killed")]})
        self.assertEqual(ma.parse_stryker(rep, {"src/a.ts": [(1, 3)]}, threshold=0.9).status, mg.PASS)

    def test_malformed_and_unreadable_reports_are_inconclusive(self):
        self.assertEqual(ma.parse_stryker({"nope": 1}).status, mg.INCONCLUSIVE)
        self.assertEqual(ma.parse_stryker("/does/not/exist.json").status, mg.INCONCLUSIVE)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.json"
            p.write_text("{not json", encoding="utf-8")
            self.assertEqual(ma.parse_stryker(p).status, mg.INCONCLUSIVE)
            p.write_text(json.dumps(report({"a.ts": [mutant(1, "Killed")]})), encoding="utf-8")
            self.assertEqual(ma.parse_stryker(p, threshold=0.9).status, mg.PASS)

    def test_unknown_status_is_excluded_and_noted(self):
        r = ma.parse_stryker(report({"a.ts": [mutant(1, "Killed"), mutant(2, "Weird")]}), threshold=0.9)
        self.assertIn("Weird", " ".join(r.notes))
        self.assertEqual(r.counts[mg.ERROR], 1)

    def test_mutants_without_location_are_skipped(self):
        bad = {"id": "x", "status": "Survived"}
        r = ma.parse_stryker(report({"a.ts": [bad, mutant(1, "Killed")]}), threshold=0.9)
        self.assertEqual(r.status, mg.PASS)


class MutmutParsing(unittest.TestCase):
    RESULTS = """\
    mylib.calc.x_clamp__mutmut_1: survived
    mylib.calc.x_clamp__mutmut_2: survived
    mylib.calc.x_clamp__mutmut_3: killed
    mylib.calc.x_total__mutmut_1: killed
    mylib.calc.x_total__mutmut_2: no tests
    mylib.other.x_f__mutmut_1: timeout
    mylib.other.x_f__mutmut_2: suspicious
"""

    def test_results_parse(self):
        s = ma.parse_mutmut_results(self.RESULTS + "noise line\nDone.\n")
        self.assertEqual(len(s), 7)
        self.assertEqual(s["mylib.calc.x_total__mutmut_2"], "no tests")
        self.assertEqual(s["mylib.other.x_f__mutmut_1"], "timeout")

    def test_diff_gives_the_original_line(self):
        diff = "--- mylib/calc.py\n+++ mylib/calc.py\n@@ -1,6 +1,6 @@\n def clamp(x, lo, hi):\n-    if x < lo:\n+    if x <= lo:\n         return lo\n"
        self.assertEqual(ma.parse_mutmut_diff(diff), 2)
        self.assertIsNone(ma.parse_mutmut_diff("nothing"))
        self.assertEqual(ma.parse_mutmut_diff("@@ -10,2 +10,3 @@\n a\n+b\n c\n"), 11)

    def test_stats(self):
        st = ma.parse_mutmut_stats({"killed": 4, "survived": 2, "total": 6, "no_tests": 0, "skipped": 0, "suspicious": 0, "timeout": 0, "segfault": 0,
                                    "check_was_interrupted_by_user": False})
        self.assertEqual((st["killed"], st["survived"], st["total"]), (4, 2, 6))

    def test_verdict_is_diff_scoped(self):
        s = ma.parse_mutmut_results(self.RESULTS)
        lines = {n: i + 1 for i, n in enumerate(s)}
        # changed lines 1-3 hold clamp mutants 1..3: survived, survived, killed
        r = ma.mutmut_verdict(s, lines, {"mylib/calc.py": [(1, 3)]}, 0.9)
        self.assertEqual(r.status, mg.FAIL)
        self.assertEqual(r.counts[mg.SURVIVED], 2)
        self.assertEqual(r.counts[mg.KILLED], 1)
        r2 = ma.mutmut_verdict(s, lines, {"mylib/calc.py": [(3, 4)]}, 0.9)
        self.assertEqual(r2.counts[mg.KILLED], 2)
        self.assertEqual(r2.status, mg.PASS)

    def test_no_mutants_on_changed_lines_fails_closed(self):
        s = ma.parse_mutmut_results(self.RESULTS)
        r = ma.mutmut_verdict(s, {n: 1 for n in s}, {"mylib/never.py": [(1, 5)]}, 0.9)
        self.assertEqual(r.status, mg.FAIL)
        self.assertIn("NO_MUTANTS", r.reason)

    def test_no_tests_counts_against_the_score(self):
        r = ma.mutmut_verdict({"m.x_f__mutmut_1": "no tests", "m.x_f__mutmut_2": "killed"}, {"m.x_f__mutmut_1": 1, "m.x_f__mutmut_2": 1}, {"m.py": [(1, 1)]}, 0.9)
        self.assertEqual(r.status, mg.FAIL)
        self.assertEqual(r.score, 0.5)


class RunMutmut(unittest.TestCase):
    def test_windows_is_reported_honestly(self):
        with mock.patch.object(ma, "IS_WINDOWS", True):
            r = ma.run_mutmut(".", {"a.py": [(1, 2)]})
        self.assertEqual(r.status, mg.INCONCLUSIVE)
        self.assertIn("WINDOWS", r.reason)

    def test_missing_mutmut_is_inconclusive(self):
        class P:
            returncode = 1
        with mock.patch.object(ma, "IS_WINDOWS", False), mock.patch.object(ma, "run_process", return_value=P()):
            r = ma.run_mutmut(".", {"a.py": [(1, 2)]})
        self.assertEqual(r.status, mg.INCONCLUSIVE)
        self.assertIn("NOT_INSTALLED", r.reason)

    @unittest.skipUnless(HAVE_MUTMUT and not IS_WINDOWS, "mutmut 3.x needs Linux/macOS/WSL2")
    def test_real_mutmut_run_finds_the_weak_spots(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "mylib").mkdir()
            (root / "tests").mkdir()
            (root / "mylib" / "__init__.py").write_text("", encoding="utf-8")
            (root / "mylib" / "calc.py").write_text("def total(items):\n    t = 0\n    for i in items:\n        t += i\n    return t\n", encoding="utf-8")
            (root / "tests" / "test_calc.py").write_text("from mylib.calc import total\n\n\ndef test_total_smoke():\n    assert total([]) == 0\n", encoding="utf-8")
            before = (root / "mylib" / "calc.py").read_text()
            weak = ma.run_mutmut(root, {"mylib/calc.py": [(1, 5)]}, threshold=0.9, timeout=300)
            self.assertIn(weak.status, (mg.FAIL, mg.INCONCLUSIVE), weak.to_dict())
            if weak.status == mg.FAIL:
                self.assertTrue(weak.survivors)
            (root / "tests" / "test_calc.py").write_text(
                "from mylib.calc import total\n\n\ndef test_total():\n    assert total([]) == 0\n    assert total([1, 2, 3]) == 6\n    assert total([5]) == 5\n", encoding="utf-8")
            strong = ma.run_mutmut(root, {"mylib/calc.py": [(1, 5)]}, threshold=0.5, timeout=300)
            self.assertEqual(strong.status, mg.PASS, strong.to_dict())
            self.assertEqual((root / "mylib" / "calc.py").read_text(), before, "the worktree must not be modified")
            self.assertFalse((root / "mutants").exists())


if __name__ == "__main__":
    unittest.main()


import shutil as _shutil  # noqa: E402
import subprocess as _subprocess  # noqa: E402


def _stryker_project(root: Path) -> bool:
    """Install StrykerJS into `root` (needs node, npm and the registry); False when that is not possible here."""
    if not (_shutil.which("node") and _shutil.which("npm")):
        return False
    (root / "src").mkdir()
    (root / "test").mkdir()
    (root / "package.json").write_text('{"name":"st","version":"1.0.0","type":"module"}', encoding="utf-8")
    (root / "src" / "calc.js").write_text(
        "export function clamp(x, lo, hi) {\n  if (x < lo) return lo;\n  if (x > hi) return hi;\n  return x;\n}\n", encoding="utf-8")
    (root / "test" / "calc.test.js").write_text(
        "import test from 'node:test';\nimport assert from 'node:assert';\nimport { clamp } from '../src/calc.js';\n"
        "test('clamp', () => { assert.equal(clamp(5,0,10),5); assert.equal(clamp(-1,0,10),0); assert.equal(clamp(11,0,10),10); });\n", encoding="utf-8")
    try:
        r = _subprocess.run(["npm", "install", "--no-audit", "--no-fund", "--silent", "@stryker-mutator/core"], cwd=root, capture_output=True, timeout=240)
    except (OSError, _subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and (root / "node_modules" / ".bin").is_dir()


class RunStryker(unittest.TestCase):
    def test_not_installed_and_no_js_are_reported(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ma.run_stryker(d, {"a.py": [(1, 2)]}).status, mg.NOT_APPLICABLE)
            r = ma.run_stryker(d, {"src/a.js": [(1, 2)]})
            self.assertEqual(r.status, mg.INCONCLUSIVE)
            self.assertIn("STRYKER_NOT_INSTALLED", r.reason)

    def test_real_stryker_run(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            if not _stryker_project(root):
                self.skipTest("node/npm/registry not available")
            r = ma.run_stryker(root, {"src/calc.js": [(1, 5)]}, threshold=0.7, test_command="node --test")
            self.assertEqual(r.status, mg.PASS, r.to_dict())
            self.assertTrue(any(s["line"] in (2, 3) for s in r.survivors), "the equivalent boundary mutants survive")
            self.assertFalse((root / "reports").exists(), "the worktree must stay clean")
            self.assertFalse((root / ".stryker-tmp").exists())
            hard = ma.run_stryker(root, {"src/calc.js": [(1, 5)]}, threshold=0.95, test_command="node --test")
            self.assertEqual(hard.status, mg.FAIL)
            (root / "test" / "calc.test.js").write_text("import test from 'node:test';\nimport assert from 'node:assert';\ntest('bad', () => assert.equal(1, 2));\n", encoding="utf-8")
            red = ma.run_stryker(root, {"src/calc.js": [(1, 5)]}, test_command="node --test")
            self.assertEqual(red.status, mg.FAIL)
            self.assertIn("BASELINE_NOT_GREEN", red.reason)

    def test_command_builder_has_no_unsupported_flags(self):
        self.assertNotIn("--jsonReporter.fileName", ma.stryker_command({"a.js": [(1, 2)]}))
