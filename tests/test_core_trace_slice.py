"""M2 tests: trace slice plugin (budget, validity, redaction, hostile objects, xdist)."""
import importlib.util, json, os, shutil, sys, tempfile, textwrap, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.verification import trace_slice_plugin as ts  # noqa: E402
from core.platform_compat import run_process, build_sandbox_env  # noqa: E402

HAVE_PYTEST = importlib.util.find_spec("pytest") is not None
HAVE_XDIST = importlib.util.find_spec("xdist") is not None


class PureHelpers(unittest.TestCase):
    def test_redact_known_secret_shapes(self):
        samples = ["AKIAABCDEFGHIJKLMNOP", "ghp_" + "a1B2c3D4" * 5, "sk-" + "Ab1" * 12, "xoxb-1234567890-abcdef",
                   "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abc123def", "-----BEGIN RSA PRIVATE KEY-----", "password=hunter2x",
                   "Bearer abcdefghijklmnopqrstuvwxyz0123456789"]
        for s in samples:
            self.assertIn("<redacted>", ts.redact(f"value {s} end"), s)

    def test_redact_keeps_git_shas_and_plain_text(self):
        sha = "a" * 12 + "0123456789abcdef0123456789ab"
        self.assertIn(sha, ts.redact(f"commit {sha}"))
        self.assertEqual(ts.redact("hello world, total=42"), "hello world, total=42")

    def test_fmt_value_never_calls_user_repr(self):
        class Evil:
            def __init__(self):
                self.name = "x"; self.password = "p4ss"; self.count = 3

            def __repr__(self):
                raise RuntimeError("boom")

            def __str__(self):
                raise RuntimeError("boom")

        out = ts.fmt_value(Evil())
        self.assertIn("Evil(", out)
        self.assertIn("name='x'", out)
        self.assertIn("password=<redacted>", out)
        self.assertNotIn("p4ss", out)

    def test_fmt_value_shapes(self):
        self.assertEqual(ts.fmt_value(5), "5")
        self.assertIn("list[10]", ts.fmt_value(list(range(10))))
        self.assertIn("dict[2]", ts.fmt_value({"a": 1, "b": [1, 2]}))
        self.assertTrue(len(ts.fmt_value("x" * 1000)) <= 40)
        self.assertIn("bytes[3]", ts.fmt_value(b"abc"))
        deep = [[[[[1]]]]]
        self.assertIn("…", ts.fmt_value(deep))

        class Arr:
            shape = (2, 3); dtype = "float64"
        self.assertEqual(ts.fmt_value(Arr()), "Arr shape=(2, 3) dtype=float64")

    def test_fit_budget_is_always_valid_json_and_within_budget(self):
        huge = {"sig": "abc", "test": "tests/test_x.py::TestA::test_" + "n" * 200, "phase": "call", "error": "AssertionError: " + "e" * 400,
                "frames": [{"file": "pkg/" + "d/" * 20 + "f.py", "line": 12, "func": "f", "src": "x = " + "y" * 300,
                            "locals": {f"v{i}": "val" * 30 for i in range(15)}} for _ in range(3)]}
        for tokens in (400, 200, 100, 60):
            line = ts.fit_budget(huge, tokens)
            rec = json.loads(line)
            self.assertLessEqual(ts.estimate_tokens(line), tokens, (tokens, len(line)))
            self.assertEqual(rec["sig"], "abc")

    def test_small_payload_untouched(self):
        p = {"sig": "s", "test": "t", "error": "E: x", "frames": [{"file": "a.py", "line": 1, "func": "f", "src": "x = 1", "locals": {"x": "1"}}]}
        self.assertNotIn("trunc", json.loads(ts.fit_budget(p, 400)))

    def test_estimate_tokens_penalises_non_ascii(self):
        self.assertGreater(ts.estimate_tokens("中" * 100), ts.estimate_tokens("a" * 100))

    def test_is_own_code(self):
        root = tempfile.mkdtemp()
        try:
            self.assertTrue(ts.is_own_code(os.path.join(root, "pkg", "a.py"), root))
            self.assertFalse(ts.is_own_code(os.path.join(root, ".venv", "lib", "site-packages", "x.py"), root))
            self.assertFalse(ts.is_own_code(root + "_other/a.py", root))
            self.assertFalse(ts.is_own_code("<frozen importlib>", root))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_read_and_render(self):
        d = Path(tempfile.mkdtemp())
        try:
            f = d / "trace_slice.jsonl"
            rec = lambda sig: {"sig": sig, "test": f"t::{sig}", "error": "E: x", "frames": [{"file": "a.py", "line": 3, "src": "assert a == b", "locals": {"a": "1", "b": "2"}}]}
            f.write_text(json.dumps(rec("s1")) + "\nnot json\n" + json.dumps(rec("s1")) + "\n")
            (d / "trace_slice.gw0.99.jsonl").write_text(json.dumps(rec("s2")) + "\n")
            got = ts.read_slices(str(f))
            self.assertEqual(sorted(r["sig"] for r in got), ["s1", "s2"])
            text = ts.render_for_prompt(got, 400)
            self.assertIn("a=1", text)
            self.assertLessEqual(ts.estimate_tokens(text), 400)
        finally:
            shutil.rmtree(d, ignore_errors=True)


@unittest.skipUnless(HAVE_PYTEST, "pytest not installed")
class PluginEndToEnd(unittest.TestCase):
    def setUp(self):
        self.proj = Path(tempfile.mkdtemp(prefix="ts_proj_"))
        (self.proj / "calc.py").write_text(textwrap.dedent('''
            def settle(balance, amount):
                remaining = balance - amount
                return remaining
        '''))
        self.out = self.proj / "out" / "slice.jsonl"

    def tearDown(self):
        shutil.rmtree(self.proj, ignore_errors=True)

    def run_pytest(self, *args, env=None):
        e = build_sandbox_env({"PYTHONPATH": os.pathsep.join([str(ROOT), str(self.proj)]), **(env or {})})
        return run_process([sys.executable, "-m", "pytest", "-p", "core.verification.trace_slice_plugin", "-p", "no:cacheprovider",
                            "--rootdir", str(self.proj), f"--trace-slice-out={self.out}", "-q", *args], cwd=str(self.proj), env=e, timeout=120)

    def records(self):
        return [json.loads(l) for l in self.out.read_text(encoding="utf-8").splitlines()] if self.out.exists() else []

    def test_failure_is_sliced_with_relevant_locals_only(self):
        (self.proj / "test_a.py").write_text(textwrap.dedent('''
            from calc import settle

            def test_settle():
                balance = 100
                amount = 30
                unrelated = [1] * 50
                result = settle(balance, amount)
                assert result == 80
        '''))
        r = self.run_pytest()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        recs = self.records()
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertIn("AssertionError", rec["error"])
        last = rec["frames"][-1]
        self.assertEqual(last["file"], "test_a.py")
        self.assertEqual(last["locals"].get("result"), "70")
        self.assertNotIn("unrelated", last["locals"])
        self.assertLessEqual(ts.estimate_tokens(json.dumps(rec)), 400)

    def test_crash_inside_project_code_adds_inner_frame(self):
        (self.proj / "calc.py").write_text("def settle(balance, amount):\n    ratio = balance / amount\n    return ratio\n")
        (self.proj / "test_b.py").write_text("from calc import settle\n\ndef test_zero():\n    settle(5, 0)\n")
        self.run_pytest()
        rec = self.records()[0]
        self.assertTrue(any(f["file"] == "calc.py" and f["locals"].get("amount") == "0" for f in rec["frames"]))
        self.assertIn("ZeroDivisionError", rec["error"])

    def test_passing_run_writes_nothing(self):
        (self.proj / "test_ok.py").write_text("def test_ok():\n    assert 1 == 1\n")
        r = self.run_pytest()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse(self.out.exists())

    def test_secret_names_and_values_are_redacted(self):
        (self.proj / "test_s.py").write_text(textwrap.dedent('''
            def test_secret():
                api_token = "ghp_" + "Zx9" * 12
                note = "key AKIAABCDEFGHIJKLMNOP leaked"
                assert api_token == "" or note == ""
        '''))
        self.run_pytest()
        raw = self.out.read_text(encoding="utf-8")
        self.assertNotIn("Zx9Zx9", raw)
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", raw)
        self.assertIn("<redacted>", raw)

    def test_hostile_repr_does_not_hang_or_break(self):
        (self.proj / "test_h.py").write_text(textwrap.dedent('''
            import time

            class Slow:
                def __repr__(self):
                    time.sleep(60)
                    return "slow"

            def test_hostile():
                thing = Slow()
                assert thing is None
        '''))
        r = self.run_pytest("--assert=plain")  # pytest's own assertion rewriting would call repr() itself
        self.assertFalse(r.timed_out)
        rec = self.records()[0]
        self.assertIn("Slow", rec["frames"][-1]["locals"]["thing"])

    def test_fixture_error_is_captured(self):
        (self.proj / "test_f.py").write_text(textwrap.dedent('''
            import pytest

            @pytest.fixture
            def db():
                connection = None
                return connection.cursor()

            def test_uses_db(db):
                assert db
        '''))
        self.run_pytest()
        recs = self.records()
        self.assertEqual(recs[0]["phase"], "setup")

    def test_budget_option(self):
        big = "\n".join(f"    v{i} = {'x' * 60!r}" for i in range(30))
        (self.proj / "test_big.py").write_text(f"def test_big():\n{big}\n    assert v1 == v2 == v3 == v4 == v5 == v6 == v7 and 1 == 2\n")
        self.run_pytest("--trace-slice-budget=100")
        line = self.out.read_text(encoding="utf-8").splitlines()[0]
        json.loads(line)
        self.assertLessEqual(ts.estimate_tokens(line), 100)

    def test_duplicate_failures_are_deduped(self):
        (self.proj / "test_d.py").write_text(textwrap.dedent('''
            import pytest

            @pytest.mark.parametrize("x", [1, 2, 3])
            def test_same(x):
                assert x == 99
        '''))
        self.run_pytest()
        # three different node ids => three different signatures; same node id re-run would dedupe
        self.assertEqual(len(self.records()), 3)

    @unittest.skipUnless(HAVE_XDIST, "pytest-xdist not installed")
    def test_xdist_writes_per_process_files_and_reader_merges(self):
        for i in range(4):
            (self.proj / f"test_x{i}.py").write_text(f"def test_x{i}():\n    value = {i}\n    assert value == 100\n")
        r = self.run_pytest("-n", "2")
        files = list((self.proj / "out").glob("*.jsonl"))
        self.assertGreaterEqual(len(files), 1, r.stdout + r.stderr)
        for f in files:
            for l in f.read_text().splitlines():
                json.loads(l)
        merged = ts.read_slices(str(self.out))
        self.assertEqual(len(merged), 4)


if __name__ == "__main__":
    unittest.main()
