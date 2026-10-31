"""M3 tests: CDP kinematic/optical linter (Python bridge, design_lint --cdp, node unit tests, real Chromium)."""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.ui import cdp_linter as cl  # noqa: E402
from core.platform_compat import run_process  # noqa: E402

FX = ROOT / "tests" / "fixtures" / "ui"
NODE = cl.find_node()
_AVAIL = {}


def browser_available():
    if "v" not in _AVAIL:
        _AVAIL["v"] = cl.available() if NODE else (False, "node not found")
    return _AVAIL["v"]


class BridgeUnit(unittest.TestCase):
    def test_spec_to_options_maps_kinematic_spec(self):
        o = cl.spec_to_options({"spring_damping_zeta": 0.6, "target_apca_lc": 75, "optical_trim": "capsize"})
        self.assertEqual(o, {"target_lc": 75.0, "zeta": 0.6, "interrupt": True, "check_optical": True})
        self.assertFalse(cl.spec_to_options({"optical_trim": "off"})["check_optical"])
        self.assertEqual(cl.spec_to_options({}), {"check_optical": True})
        self.assertFalse(cl.spec_to_options({"spring_damping_zeta": 0.5}, interrupt=False)["interrupt"])

    def test_build_command(self):
        cmd = cl.build_command("node", "file:///a.html", "#x", target_lc=60, zeta=0.5, interrupt=True, check_optical=False, chromium="/c")
        self.assertEqual(cmd[:2], ["node", str(cl.LINTER)])
        for tok in ("--json", "--interrupt", "--no-optical", "--zeta", "0.5", "--target-lc", "60.0", "--chromium", "/c"):
            self.assertIn(tok, cmd)
        self.assertNotIn("--no-contrast", cmd)

    def test_last_json_ignores_noise(self):
        self.assertEqual(cl._last_json("warn\n{\"a\": 1}\ntrailing"), {"a": 1})
        self.assertIsNone(cl._last_json("no json here"))
        self.assertIsNone(cl._last_json("{broken}"))

    def test_normalize_url(self):
        self.assertEqual(cl.normalize_url("http://localhost:3000/x"), "http://localhost:3000/x")
        self.assertEqual(cl.normalize_url("file:///a/b.html"), "file:///a/b.html")
        self.assertTrue(cl.normalize_url("rel/page.html").startswith("file:///"))

    def test_missing_node_is_tooling_error_not_pass(self):
        old = os.environ.get("PLANNER_NODE")
        try:
            r = cl.run_cdp_linter("x.html", "#a", node="/definitely/not/node-binary-xyz")
        finally:
            if old is not None:
                os.environ["PLANNER_NODE"] = old
        self.assertEqual(r.verdict, "TOOLING_ERROR")
        self.assertEqual(r.exit_code, 2)
        self.assertFalse(r.passed)
        self.assertTrue(r.failures())

    def test_failures_render(self):
        r = cl.CdpResult("REJECTED_AI_SLOP", 1, report={"checks": {
            "contrast": {"status": "fail", "elements": [{"status": "fail", "path": "a", "lc": 40, "need": 75}]},
            "spring": {"status": "fail", "fittedZeta": 0.9, "targetZeta": 0.5, "reason": "x"},
            "interrupt": {"status": "inconclusive", "reason": "no mid-motion", "worstJump": None},
            "optical": {"status": "pass"}, "x": {"status": "skipped"}}})
        lines = r.failures()
        self.assertEqual(len(lines), 3)
        self.assertIn("lc=40", lines[0])
        self.assertIn("0.9", lines[1])
        self.assertFalse(r.passed)

    def test_inconclusive_is_never_passed(self):
        self.assertFalse(cl.CdpResult("INCONCLUSIVE", 3).passed)
        self.assertTrue(cl.CdpResult("HUMAN_CRAFT_VERIFIED", 0).passed)


@unittest.skipUnless(NODE, "node not installed")
class NodeUnitTests(unittest.TestCase):
    def test_node_pure_tests_pass(self):
        r = run_process([NODE, "--test", str(ROOT / "core" / "ui" / "test" / "pure.test.mjs")], cwd=str(ROOT), timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
        self.assertTrue("# fail 0" in r.stdout or "ℹ fail 0" in r.stdout or "fail 0" in r.stdout, r.stdout)

    def test_cli_help_and_argument_errors(self):
        r = run_process([NODE, str(cl.LINTER), "--help"], timeout=30)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--zeta", r.stdout)
        r = run_process([NODE, str(cl.LINTER), "--bogus"], timeout=30)
        self.assertEqual(r.returncode, 2)
        r = run_process([NODE, str(cl.LINTER), "--url", "x.html"], timeout=30)
        self.assertEqual(r.returncode, 2)


class RealBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ok, why = browser_available()
        if not ok:
            raise unittest.SkipTest(f"playwright/chromium unavailable: {why}")

    def test_good_page_is_verified(self):
        r = cl.run_cdp_linter(str(FX / "good.html"), "#cta", zeta=0.5, interrupt=True, timeout=180)
        self.assertEqual(r.verdict, "HUMAN_CRAFT_VERIFIED", r.failures())
        self.assertEqual({k: v["status"] for k, v in r.checks.items()},
                         {"contrast": "pass", "optical": "pass", "spring": "pass", "interrupt": "pass"})
        self.assertAlmostEqual(r.checks["spring"]["fittedZeta"], 0.5, delta=0.08)

    def test_bad_page_is_rejected_with_actionable_reasons(self):
        r = cl.run_cdp_linter(str(FX / "bad.html"), "#cta", zeta=0.5, interrupt=True, timeout=180)
        self.assertEqual(r.verdict, "REJECTED_AI_SLOP")
        self.assertEqual(r.exit_code, 1)
        st = {k: v["status"] for k, v in r.checks.items()}
        self.assertEqual(st["contrast"], "fail")
        self.assertEqual(st["optical"], "fail")
        self.assertEqual(st["interrupt"], "fail")
        text = "\n".join(r.failures())
        self.assertIn("lc=", text)
        self.assertIn("asymmetryPx", text)

    def test_gradient_text_is_inconclusive_not_green(self):
        r = cl.run_cdp_linter(str(FX / "gradient.html"), "#hero", timeout=180)
        self.assertEqual(r.verdict, "INCONCLUSIVE", r.failures())
        self.assertEqual(r.exit_code, 3)
        self.assertEqual(r.checks["contrast"]["status"], "inconclusive")

    def test_wrong_damping_target_fails(self):
        r = cl.run_cdp_linter(str(FX / "good.html"), "#cta", zeta=1.0, check_contrast=False, check_optical=False, timeout=180)
        self.assertEqual(r.verdict, "REJECTED_AI_SLOP")
        self.assertEqual(r.checks["spring"]["status"], "fail")

    def test_missing_selector_is_tooling_error(self):
        r = cl.run_cdp_linter(str(FX / "good.html"), "#nope", timeout=180)
        self.assertEqual(r.verdict, "TOOLING_ERROR")
        self.assertIn("matches nothing", r.error + r.report.get("error", ""))

    def test_missing_page_is_tooling_error(self):
        r = cl.run_cdp_linter(str(FX / "does-not-exist.html"), "#cta", timeout=180)
        self.assertEqual(r.verdict, "TOOLING_ERROR")

    def test_nothing_requested_is_inconclusive(self):
        r = cl.run_cdp_linter(str(FX / "good.html"), "#cta", check_contrast=False, check_optical=False, timeout=180)
        self.assertEqual(r.verdict, "INCONCLUSIVE")

    def test_target_lc_override(self):
        r = cl.run_cdp_linter(str(FX / "bad.html"), "#cta", target_lc=30, check_optical=False, timeout=180)
        self.assertEqual(r.checks["contrast"]["status"], "pass")

    def test_design_lint_cli_integration(self):
        run = lambda *a: subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "design_lint.py"), *a], capture_output=True, text=True, timeout=240)
        ok = run("--cdp", str(FX / "good.html"), "--selector", "#cta", "--zeta", "0.5", "--interrupt")
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertIn("HUMAN_CRAFT_VERIFIED", ok.stdout)
        bad = run("--cdp", str(FX / "bad.html"), "--selector", "#cta", "--json")
        self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
        self.assertEqual(json.loads(bad.stdout)["cdp"]["verdict"], "REJECTED_AI_SLOP")
        self.assertFalse(json.loads(bad.stdout)["ok"])
        usage = run("--cdp", str(FX / "good.html"))
        self.assertEqual(usage.returncode, 2)


class DesignLintStaticUnchanged(unittest.TestCase):
    def test_static_mode_still_works_and_needs_input(self):
        run = lambda *a: subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "design_lint.py"), *a], capture_output=True, text=True)
        self.assertEqual(run(str(ROOT / "tests" / "fixtures" / "design" / "good.html"), "--strict").returncode, 0)
        self.assertEqual(run().returncode, 2)


if __name__ == "__main__":
    unittest.main()
