"""evals/golden_e2e_run.py: the 7-step golden benchmark passes end to end and leaves no trace of its keys."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))
import golden_e2e_run as golden  # noqa: E402


class GoldenRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = {k: os.environ.get(k) for k in (golden.LAWGIVER_ENV, golden.INQUISITOR_ENV)}
        cls.tmp = tempfile.mkdtemp(prefix="golden_test_")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            cls.code = golden.main(["--json", "--workdir", str(Path(cls.tmp) / "w")])
        cls.stdout = out.getvalue()
        cls.report = json.loads(cls.stdout)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_exit_zero_and_every_step_passes(self):
        self.assertEqual(self.code, 0, json.dumps(self.report["steps"], indent=1))
        self.assertTrue(self.report["ok"])
        self.assertEqual([s["step"] for s in self.report["steps"]], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual({s["status"] for s in self.report["steps"]}, {"PASS"})

    def test_report_names_the_eight_worker_fleet(self):
        self.assertEqual(self.report["fleet"], ["AG1", "AG6", "AG2", "AG3", "AG4", "AG5", "AG7", "AG8"])

    def test_keys_are_restored_and_never_printed(self):
        for k, v in self.before.items():
            self.assertEqual(os.environ.get(k), v, k)
        # the report must not contain anything that looks like a 64-hex run key
        import re
        self.assertIsNone(re.search(r"\b[0-9a-f]{64}\b", self.stdout))

    def test_the_workdir_is_kept_on_request_and_holds_no_key(self):
        wd = Path(self.tmp) / "w"
        self.assertTrue((wd / "project" / ".planner" / "tickets").is_dir())
        self.assertIn("workdir", self.report)
        self.assertTrue((wd / "project" / ".planner" / "sealed").is_dir())


class Cli(unittest.TestCase):
    def call(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = golden.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_bad_only_is_a_usage_error(self):
        self.assertEqual(self.call("--only", "x")[0], 2)
        self.assertEqual(self.call("--only", "9")[0], 2)

    def test_non_empty_workdir_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "keep.txt").write_text("x", encoding="utf-8")
            code, _o, err = self.call("--workdir", d)
        self.assertEqual(code, 2)
        self.assertIn("not empty", err)

    def test_step_one_alone_runs_and_restores_the_environment(self):
        saved = os.environ.get(golden.LAWGIVER_ENV)
        code, out, _e = self.call("--only", "1", "--json")
        self.assertEqual(code, 0, out)
        self.assertEqual([s["step"] for s in json.loads(out)["steps"]], [1])
        self.assertEqual(os.environ.get(golden.LAWGIVER_ENV), saved)

    def test_a_broken_step_makes_the_run_fail_and_skips_the_rest(self):
        class Broken(golden.Golden):
            def step1_contract(self):
                raise golden.StepFailed("simulated")
        original = golden.Golden
        golden.Golden = Broken
        try:
            with tempfile.TemporaryDirectory() as d:
                rep = golden.run(Path(d), quiet=True)
        finally:
            golden.Golden = original
        self.assertFalse(rep["ok"])
        self.assertEqual([s["status"] for s in rep["steps"]], ["FAIL"] + ["SKIPPED"] * 6)


class Fixtures(unittest.TestCase):
    def test_the_fixture_implementations_parse_and_the_lazy_one_differs(self):
        import ast
        for src in (golden.BUCKET_GOOD, golden.BUCKET_LAZY, golden.GUARD_GOOD, golden.GUARD_BROKEN, golden.AG4_PROPERTIES, golden.SEALED_SECURITY,
                    golden.SEALED_BACKEND, golden.VULNERABLE_SAMPLE, golden.SYNTHETIC_FAILURE, golden.BUCKET_STUB, golden.GUARD_STUB):
            ast.parse(src)
        self.assertNotEqual(golden.BUCKET_GOOD, golden.BUCKET_LAZY)
        self.assertNotIn("negative", golden.BUCKET_LAZY)

    def test_every_invariant_in_the_plan_is_named_by_a_sealed_test(self):
        import re
        ids = re.findall(r"INV-\d\d", golden.PLAN)
        text = golden.SEALED_BACKEND + golden.SEALED_SECURITY
        for i in ids:
            self.assertIn(i.lower().replace("-", ""), text.lower().replace("-", "").replace("_", ""), i)


if __name__ == "__main__":
    unittest.main()
