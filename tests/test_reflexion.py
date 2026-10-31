"""M4 tests: failure signatures and the reflect_check edit blocker."""
import os, re, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G3 = os.path.join(ROOT, "tools", "gate3")
sys.path.insert(0, G3)
import failure_signature as fs  # noqa: E402
import reflect_check as rc  # noqa: E402

GOOD = "Hypothesis: parser strips the leading zero because int() is applied before str padding"


class Signature(unittest.TestCase):
    def test_identical_errors_identical_signature(self):
        a = fs.compute_signature("tests/test_x.py::t1", "AssertionError", "parser.py:42")
        b = fs.compute_signature("  tests/test_x.py::t1 ", "AssertionError ", " parser.py:42")
        self.assertEqual(a, b)
        self.assertRegex(a, r"^[0-9a-f]{12}$")

    def test_different_errors_differ(self):
        base = fs.compute_signature("t", "E", "f:1")
        self.assertNotEqual(base, fs.compute_signature("t2", "E", "f:1"))
        self.assertNotEqual(base, fs.compute_signature("t", "E2", "f:1"))
        self.assertNotEqual(base, fs.compute_signature("t", "E", "f:2"))

    def test_cli(self):
        r = subprocess.run([sys.executable, os.path.join(G3, "failure_signature.py"), "t", "E"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), fs.compute_signature("t", "E"))
        r = subprocess.run([sys.executable, os.path.join(G3, "failure_signature.py")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)


class ReflectCheck(unittest.TestCase):
    SIG = "abc123def456"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.log = os.path.join(self.tmp, "reflection_log.md")

    def write(self, text):
        with open(self.log, "w", encoding="utf8") as fh:
            fh.write(text)

    def run_rc(self, *args):
        return subprocess.run([sys.executable, os.path.join(G3, "reflect_check.py"), *args, "--log", self.log],
                              capture_output=True, text=True)

    def test_blocks_when_log_missing(self):
        r = self.run_rc(self.SIG, "1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("missing", r.stderr)

    def test_blocks_when_no_entry_for_signature_or_attempt(self):
        self.write(f"## {self.SIG} / attempt 1\n{GOOD}\n")
        self.assertEqual(self.run_rc("ffffffffffff", "1").returncode, 2)
        self.assertEqual(self.run_rc(self.SIG, "2").returncode, 2)

    def test_blocks_when_hypothesis_missing(self):
        self.write(f"## {self.SIG} / attempt 1\nChange: edited parser\n")
        r = self.run_rc(self.SIG, "1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Hypothesis", r.stderr)

    def test_blocks_short_or_placeholder_hypothesis(self):
        for h in ("Hypothesis: bug", "Hypothesis: TBD", "Hypothesis:", "Hypothesis: unknown."):
            self.write(f"## {self.SIG} / attempt 1\n{h}\n")
            self.assertEqual(self.run_rc(self.SIG, "1").returncode, 2, h)

    def test_allows_valid_reflection(self):
        self.write(f"## {self.SIG} / attempt 1\n{GOOD}\n")
        self.assertEqual(self.run_rc(self.SIG, "1").returncode, 0)

    def test_hypothesis_of_a_later_entry_does_not_leak_backwards(self):
        self.write(f"## {self.SIG} / attempt 1\nChange: nothing yet\n\n## {self.SIG} / attempt 2\n{GOOD}\n")
        self.assertEqual(self.run_rc(self.SIG, "1").returncode, 2)
        self.assertEqual(self.run_rc(self.SIG, "2").returncode, 0)

    def test_repeating_the_same_hypothesis_is_blocked(self):
        self.write(f"## {self.SIG} / attempt 1\n{GOOD}\n\n## {self.SIG} / attempt 2\n{GOOD}\n")
        r = self.run_rc(self.SIG, "2")
        self.assertEqual(r.returncode, 2)
        self.assertIn("repeats", r.stderr)

    def test_three_failed_attempts_trigger_escalation_md(self):
        self.write("\n".join(f"## {self.SIG} / attempt {i}\nHypothesis: theory number {i} about the cause of failure\n" for i in (1, 2, 3)))
        self.assertEqual(self.run_rc(self.SIG, "3").returncode, 0)
        esc = os.path.join(self.tmp, "ESCALATION.md")
        self.assertFalse(os.path.exists(esc))
        r = self.run_rc(self.SIG, "4")
        self.assertEqual(r.returncode, 3)
        self.assertTrue(os.path.exists(esc))
        with open(esc, encoding="utf8") as fh:
            text = fh.read()
        self.assertIn(self.SIG, text)
        self.assertIn("theory number 2", text)
        self.assertIn("HALT", text)

    def test_missing_args_fail_closed(self):
        self.write(f"## {self.SIG} / attempt 1\n{GOOD}\n")
        self.assertEqual(self.run_rc().returncode, 1)

    def test_hook_mode_checks_last_entry(self):
        self.write(f"## {self.SIG} / attempt 1\n{GOOD}\n\n## {self.SIG} / attempt 2\nChange: guess\n")
        self.assertEqual(self.run_rc("--hook").returncode, 2)
        self.write(f"## {self.SIG} / attempt 1\n{GOOD}\n")
        self.assertEqual(self.run_rc("--hook").returncode, 0)
        self.write("no entries here\n")
        self.assertEqual(self.run_rc("--hook").returncode, 2)


if __name__ == "__main__":
    unittest.main()
