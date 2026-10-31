"""M5 tests: verifier.py - the per-attempt verification pipeline."""
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import projectkit as pk  # noqa: E402
import verifier as v  # noqa: E402
import dag_planner as dp  # noqa: E402
from core import worktree as wt  # noqa: E402
from core.ui import cdp_linter  # noqa: E402

try:
    import pytest  # noqa: F401
    HAVE_PYTEST = True
except ImportError:  # pragma: no cover
    HAVE_PYTEST = False

needs_pytest = unittest.skipUnless(HAVE_PYTEST, "pytest is not installed")
UI_OK, UI_WHY = cdp_linter.available()


def ctx_for(demo, *, doc=None, scope=None, **opt):
    opts = v.Options(write_report=False, **opt)
    ctx = v.Ctx(doc or demo.doc, scope or demo.scope, demo.root, demo.workspace, opts)
    ctx.changes = wt.changes(ctx.baseline, demo.workspace)
    return ctx


def run(demo, **opt):
    return v.verify(demo.ticket, demo.scope, root=demo.root, workspace=demo.workspace, options=v.Options(write_report=False, **opt))


def by_name(rep):
    return {c.name: c for c in rep.checks}


class Decide(unittest.TestCase):
    def test_fail_dominates(self):
        cs = [v.Check("a", v.PASS), v.Check("b", v.FAIL), v.Check("c", v.INCONCLUSIVE)]
        self.assertEqual(v.decide(cs, "full"), v.V_FAIL)

    def test_inconclusive_is_never_pass(self):
        self.assertEqual(v.decide([v.Check("a", v.PASS), v.Check("b", v.INCONCLUSIVE)], "full"), v.V_INCONCLUSIVE)

    def test_na_allowed_except_for_the_oracle_checks(self):
        self.assertEqual(v.decide([v.Check("ui", v.NA), v.Check("sealed", v.PASS)], "full"), v.V_PASS)
        self.assertEqual(v.decide([v.Check("sealed", v.NA)], "full"), v.V_INCONCLUSIVE)
        self.assertEqual(v.decide([v.Check("scope", v.NA)], "full"), v.V_INCONCLUSIVE)

    def test_self_level_never_returns_a_closing_pass(self):
        self.assertEqual(v.decide([v.Check("scope", v.PASS)], "self"), v.V_SELF)
        self.assertNotEqual(v.EXIT[v.V_SELF], 1)

    def test_waived_counts_as_ok_but_only_listed_checks_are_waivable(self):
        self.assertEqual(v.decide([v.Check("mutation", v.WAIVED), v.Check("sealed", v.PASS)], "full"), v.V_PASS)
        for name in ("contract", "scope", "secrets", "ports", "weakening", "sealed"):
            self.assertNotIn(name, v.WAIVABLE)

    def test_vault_id_matches_flat_ticket_id_shape(self):
        self.assertEqual(v.vault_id("PLT-1", "worker_backend"), "PLT-1.AG5")


class StaticChecks(unittest.TestCase):
    def setUp(self):
        self.d = pk.Demo()
        self.addCleanup(self.d.close)

    def test_contract_valid_and_cross_checked_with_the_board(self):
        c = v.check_contract(ctx_for(self.d))
        self.assertEqual(c.status, v.PASS, c.detail)

    def test_contract_edited_after_signing_fails(self):
        p = self.d.planner / "tickets" / f"{pk.TICKET}.yaml"
        p.write_text(p.read_text().replace("Classify numbers", "Classify EVERYTHING"), encoding="utf-8")
        doc = v.load_contract(self.d.root, pk.TICKET)
        c = v.check_contract(ctx_for(self.d, doc=doc))
        self.assertEqual(c.status, v.FAIL)

    def test_contract_not_registered_on_the_board_fails(self):
        d = pk.Demo(register=False)
        self.addCleanup(d.close)
        c = v.check_contract(ctx_for(d))
        self.assertEqual(c.status, v.FAIL)
        self.assertIn("not registered", " ".join(c.items))

    def test_contract_without_board_is_inconclusive_when_required(self):
        c = v.check_contract(ctx_for(self.d, board_path=str(self.d.root / "nope.db"), require_board=True))
        self.assertEqual(c.status, v.INCONCLUSIVE)

    def test_scope_ok(self):
        self.d.write("src/bucket.py", pk.GOOD)
        self.assertEqual(v.check_scope(ctx_for(self.d)).status, v.PASS)

    def test_scope_nothing_changed_is_inconclusive(self):
        self.assertEqual(v.check_scope(ctx_for(self.d)).status, v.INCONCLUSIVE)

    def test_scope_violations(self):
        self.d.write("src/bucket.py", pk.GOOD)
        self.d.write("src/other.py", "x = 1\n")
        self.d.write(".env", "A=1\n")
        self.d.write("tests/property/test_x.py", "def test_a():\n    assert True\n")
        c = v.check_scope(ctx_for(self.d))
        self.assertEqual(c.status, v.FAIL)
        text = "\n".join(c.items)
        self.assertIn("src/other.py", text)
        self.assertIn("another worker's scope", text)  # AG4's tests
        self.assertIn(".env: protected location", text)

    def test_scope_deleting_a_file_outside_the_scope_counts(self):
        (self.d.workspace / "contracts").exists()
        victim = next((self.d.workspace / "contracts").rglob("*.py"))
        victim.unlink()
        c = v.check_scope(ctx_for(self.d))
        self.assertEqual(c.status, v.FAIL)

    def test_secrets(self):
        self.d.write("src/bucket.py", pk.GOOD + "KEY = 'AKIAABCDEFGHIJKLMNOP'\n")
        self.assertEqual(v.check_secrets(ctx_for(self.d)).status, v.FAIL)
        self.d.write("src/bucket.py", pk.GOOD)
        self.d.write(".env", "TOKEN=abc\n")
        c = v.check_secrets(ctx_for(self.d))
        self.assertEqual(c.status, v.FAIL)
        self.assertIn(".env", " ".join(c.items))

    def test_secret_values_are_not_echoed(self):
        self.d.write("src/bucket.py", pk.GOOD + "KEY = 'AKIAABCDEFGHIJKLMNOP'\n")
        c = v.check_secrets(ctx_for(self.d))
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", json.dumps(c.to_dict()))

    def test_clean_change_has_no_secret_findings(self):
        self.d.write("src/bucket.py", pk.GOOD)
        self.assertEqual(v.check_secrets(ctx_for(self.d)).status, v.PASS)

    def test_ports_masp(self):
        self.d.write("src/bucket.py", pk.GOOD + "\nserver.listen(3000)\n")
        c = v.check_ports(ctx_for(self.d))
        self.assertEqual(c.status, v.FAIL)
        self.assertIn("AG1", " ".join(c.items))
        self.d.write("src/bucket.py", pk.GOOD + "\nserver.listen(3450)\n")
        self.assertEqual(v.check_ports(ctx_for(self.d)).status, v.PASS)

    def test_symbol_drift_is_detected(self):
        self.d.write("src/bucket.py", pk.GOOD.replace("def bucket(n: int) -> str:", "def bucket(n: int, strict: bool = False) -> str:"))
        c = v.check_symbols(ctx_for(self.d))
        self.assertEqual(c.status, v.FAIL)
        self.assertIn("drift", " ".join(c.items))

    def test_symbol_intact_with_a_body(self):
        self.d.write("src/bucket.py", pk.GOOD)
        self.assertEqual(v.check_symbols(ctx_for(self.d)).status, v.PASS)

    def test_symbol_drift_cannot_hide_behind_an_untouched_stub(self):
        # contracts/<ticket>/bucket.py still has the locked stub; the hinted file src/bucket.py is the canonical one
        self.d.write("src/bucket.py", pk.GOOD.replace("-> str", "-> int"))
        self.assertEqual(v.check_symbols(ctx_for(self.d)).status, v.FAIL)

    def test_invariants_need_tests_that_name_them(self):
        d = pk.Demo(sealed=pk.WEAK_SEALED.replace("inv01_inv02", "x"))
        self.addCleanup(d.close)
        c = v.check_invariants(ctx_for(d))
        self.assertEqual(c.status, v.FAIL)
        self.assertEqual(len(c.items), 2)
        self.assertEqual(v.check_invariants(ctx_for(self.d)).status, v.PASS)

    def test_invariants_without_any_tests_are_inconclusive(self):
        d = pk.Demo(sealed=None)
        self.addCleanup(d.close)
        self.assertEqual(v.check_invariants(ctx_for(d)).status, v.INCONCLUSIVE)

    def test_forbidden_calls_only_when_newly_introduced(self):
        doc = json.loads(json.dumps(self.d.doc))
        doc["worker_scopes"]["worker_backend"]["forbidden_calls"] = ["eval("]
        self.d.write("src/bucket.py", pk.GOOD + "\ndef risky(s):\n    return eval(s)\n")
        c = v.check_forbidden(ctx_for(self.d, doc=doc))
        self.assertEqual(c.status, v.FAIL)
        self.d.write("src/bucket.py", pk.GOOD + "\n# eval( is forbidden here\n")
        self.assertEqual(v.check_forbidden(ctx_for(self.d, doc=doc)).status, v.PASS)

    def test_forbidden_sql_types(self):
        doc = json.loads(json.dumps(self.d.doc))
        doc["worker_scopes"]["worker_backend"]["forbidden_ast"] = ["DOUBLE PRECISION", "FLOAT"]
        self.d.write("src/bucket.py", pk.GOOD)
        sql = self.d.write("src/schema.sql", "CREATE TABLE t (id INT, amount  double   precision);\n")
        del sql
        c = v.check_forbidden(ctx_for(self.d, doc=doc))
        self.assertEqual(c.status, v.FAIL)
        self.d.write("src/schema.sql", "-- FLOAT is banned\nCREATE TABLE t (id INT, floating_note TEXT, amount BIGINT);\n")
        self.assertEqual(v.check_forbidden(ctx_for(self.d, doc=doc)).status, v.PASS)

    def test_forbidden_na_when_nothing_declared(self):
        self.d.write("src/bucket.py", pk.GOOD)
        self.assertEqual(v.check_forbidden(ctx_for(self.d)).status, v.NA)

    def test_weakening_needs_a_baseline(self):
        c = v.check_weakening(ctx_for(self.d, baseline=str(self.d.root / "missing-baseline")))
        self.assertEqual(c.status, v.INCONCLUSIVE)

    def test_weakening_detects_a_deleted_or_loosened_test(self):
        base = self.d.planner / "baselines" / pk.TICKET / "tree"
        (base / "tests").mkdir(exist_ok=True)
        (base / "tests" / "test_old.py").write_text("def test_a():\n    assert 1 + 1 == 2\n    assert 2 * 2 == 4\n", encoding="utf-8")
        self.d.write("tests/test_old.py", "def test_a():\n    assert True\n")
        c = v.check_weakening(ctx_for(self.d))
        self.assertEqual(c.status, v.FAIL)
        (self.d.workspace / "tests" / "test_old.py").unlink()
        self.assertEqual(v.check_weakening(ctx_for(self.d)).status, v.FAIL)

    def test_weakening_ok_when_tests_only_grow(self):
        self.d.write("tests/test_new.py", "def test_a():\n    assert 1 + 1 == 2\n")
        self.assertEqual(v.check_weakening(ctx_for(self.d)).status, v.PASS)


@needs_pytest
class VisibleSealedMutation(unittest.TestCase):
    def setUp(self):
        self.d = pk.Demo()
        self.addCleanup(self.d.close)

    def test_visible_na_without_tests(self):
        self.d.write("src/bucket.py", pk.GOOD)
        self.assertEqual(v.check_visible(ctx_for(self.d)).status, v.NA)

    def test_visible_failure_carries_a_trace_slice_and_ids(self):
        self.d.write("src/bucket.py", pk.LAZY)
        self.d.write("tests/test_vis.py", "from src.bucket import bucket\n\ndef test_negative():\n    limit = 0\n    assert bucket(-1 + limit) == 'x'\n")
        c = v.check_visible(ctx_for(self.d))
        self.assertEqual(c.status, v.FAIL, c.detail)
        self.assertTrue(any("test_negative" in i for i in c.items))
        self.assertIn("trace_slice", c.data)

    def test_visible_pass(self):
        self.d.write("src/bucket.py", pk.GOOD)
        self.d.write("tests/test_vis.py", "from src.bucket import bucket\n\ndef test_small():\n    assert bucket(1) == 'small'\n")
        c = v.check_visible(ctx_for(self.d))
        self.assertEqual(c.status, v.PASS, c.detail)

    def test_sealed_pass_and_fail(self):
        self.d.write("src/bucket.py", pk.GOOD)
        self.assertEqual(v.check_sealed(ctx_for(self.d)).status, v.PASS)
        self.d.write("src/bucket.py", pk.LAZY)
        c = v.check_sealed(ctx_for(self.d))
        self.assertEqual(c.status, v.FAIL)

    def test_sealed_never_leaks_assertion_text(self):
        self.d.write("src/bucket.py", pk.LAZY)
        c = v.check_sealed(ctx_for(self.d))
        blob = json.dumps(c.to_dict())
        self.assertNotIn("assert bucket(", blob)
        self.assertNotIn('== "medium"', blob)

    def test_sealed_missing_vault_fails_closed(self):
        d = pk.Demo(sealed=None)
        self.addCleanup(d.close)
        d.write("src/bucket.py", pk.GOOD)
        c = v.check_sealed(ctx_for(d))
        self.assertEqual(c.status, v.FAIL)
        self.assertIn("NO_SEALED_TESTS", c.detail)

    def test_sealed_tampered_vault_fails(self):
        self.d.write("src/bucket.py", pk.GOOD)
        vt = self.d.planner / "sealed" / f"{pk.TICKET}.AG5" / "tests" / "test_sealed.py"
        os.chmod(vt, stat.S_IWRITE | stat.S_IREAD)
        vt.write_text(vt.read_text() + "\n# tamper\n", encoding="utf-8")
        c = v.check_sealed(ctx_for(self.d))
        self.assertEqual(c.status, v.FAIL)
        self.assertIn("VAULT_TAMPERED", c.detail)

    def test_mutation_passes_with_a_strong_oracle(self):
        self.d.write("src/bucket.py", pk.GOOD)
        c = v.check_mutation(ctx_for(self.d, mutation_max=40))
        self.assertEqual(c.status, v.PASS, c.detail)
        self.assertGreaterEqual(c.data["score"], 0.92)

    def test_mutation_fails_with_a_weak_oracle(self):
        d = pk.Demo(sealed=pk.WEAK_SEALED)
        self.addCleanup(d.close)
        d.write("src/bucket.py", pk.GOOD)
        c = v.check_mutation(ctx_for(d, mutation_max=40))
        self.assertEqual(c.status, v.FAIL, c.detail)
        self.assertTrue(any(i.startswith("survived") for i in c.items))

    def test_mutation_threshold_comes_from_the_qa_scope(self):
        self.assertAlmostEqual(ctx_for(self.d).qa_threshold(), 0.92)
        self.assertAlmostEqual(ctx_for(self.d, mutation_threshold=0.5).qa_threshold(), 0.5)

    def test_mutation_na_for_qa_scope_and_inconclusive_without_baseline(self):
        self.d.write("src/bucket.py", pk.GOOD)
        self.assertEqual(v.check_mutation(ctx_for(self.d, scope="worker_qa_guard")).status, v.NA)
        self.assertEqual(v.check_mutation(ctx_for(self.d, baseline=str(self.d.root / "nope"))).status, v.INCONCLUSIVE)

    def test_mutation_js_only_change_is_inconclusive(self):
        self.d.write("src/bucket.ts", "export function bucket(n: number) { return n < 10 ? 'small' : 'large'; }\n")
        c = v.check_mutation(ctx_for(self.d))
        self.assertEqual(c.status, v.INCONCLUSIVE)
        self.assertIn("STRYKER_NOT_INSTALLED", c.detail)


@needs_pytest
class FullPipeline(unittest.TestCase):
    def setUp(self):
        self.d = pk.Demo()
        self.addCleanup(self.d.close)

    def test_good_implementation_passes_everything(self):
        self.d.write("src/bucket.py", pk.GOOD)
        rep = run(self.d, level="full", mutation_max=40)
        self.assertEqual(rep.verdict, v.V_PASS, v.render_feedback(rep))
        self.assertEqual(rep.exit_code, 0)
        names = [c.name for c in rep.checks]
        self.assertEqual(names, list(v.CHECK_ORDER))

    def test_lazy_implementation_fails_and_feedback_is_oracle_safe(self):
        self.d.write("src/bucket.py", pk.LAZY)
        rep = run(self.d, level="full", mutation_max=20)
        self.assertEqual(rep.verdict, v.V_FAIL)
        fb = v.render_feedback(rep)
        self.assertIn("sealed", fb)
        self.assertNotIn("assert bucket", fb)
        self.assertLess(len(fb), 2000)

    def test_unchanged_stub_is_inconclusive_not_pass(self):
        rep = run(self.d, level="full", mutation_max=10)
        self.assertNotEqual(rep.verdict, v.V_PASS)

    def test_self_level_skips_the_oracle(self):
        self.d.write("src/bucket.py", pk.GOOD)
        rep = run(self.d, level="self")
        self.assertEqual(rep.verdict, v.V_SELF)
        self.assertNotIn("sealed", [c.name for c in rep.checks])
        self.assertNotIn("mutation", [c.name for c in rep.checks])

    def test_auto_level_is_full_when_the_oracle_is_reachable_and_self_otherwise(self):
        self.d.write("src/bucket.py", pk.GOOD)
        ctx = ctx_for(self.d)
        self.assertEqual(v.resolve_level(v.Options(), ctx), "full")
        ctx2 = ctx_for(self.d, sealed_root=str(self.d.root / "elsewhere"))
        self.assertEqual(v.resolve_level(v.Options(sealed_root=str(self.d.root / "elsewhere")), ctx2), "self")

    def test_partial_runs_can_never_close_a_ticket(self):
        self.d.write("src/bucket.py", pk.GOOD)
        rep = run(self.d, level="full", only=["scope", "sealed"])
        self.assertEqual(rep.verdict, v.V_PARTIAL)
        self.assertTrue(any("partial run" in w for w in rep.warnings))

    def test_waiver_is_recorded_and_unwaivable_checks_are_refused(self):
        self.d.write("src/bucket.py", pk.GOOD)
        rep = run(self.d, level="full", waive=["mutation", "sealed"])
        self.assertEqual(rep.waivers, ["mutation"])
        self.assertTrue(any("sealed" in w for w in rep.warnings))
        self.assertEqual(by_name(rep)["sealed"].status, v.PASS)

    def test_a_crashing_check_is_inconclusive(self):
        saved = v.CHECKS["ports"]
        v.CHECKS["ports"] = lambda ctx: 1 / 0
        self.addCleanup(lambda: v.CHECKS.__setitem__("ports", saved))
        self.d.write("src/bucket.py", pk.GOOD)
        rep = run(self.d, level="self")
        self.assertEqual(by_name(rep)["ports"].status, v.INCONCLUSIVE)
        self.assertNotEqual(rep.verdict, v.V_SELF)

    def test_report_file_is_written(self):
        self.d.write("src/bucket.py", pk.GOOD)
        rep = v.verify(self.d.ticket, self.d.scope, root=self.d.root, workspace=self.d.workspace, options=v.Options(level="self"))
        f = self.d.planner / "reports" / f"{pk.TICKET}.AG5.verify.json"
        self.assertTrue(f.is_file())
        self.assertEqual(json.loads(f.read_text())["verdict"], rep.verdict)

    def test_unknown_scope_raises(self):
        with self.assertRaises(ValueError):
            v.verify(self.d.ticket, "worker_ui", root=self.d.root, workspace=self.d.workspace, options=v.Options(write_report=False))


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = pk.Demo()
        self.addCleanup(self.d.close)

    def call(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = v.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_checks_lists_every_check(self):
        code, out, _ = self.call("checks")
        self.assertEqual(code, 0)
        self.assertEqual(out.split(), list(v.CHECK_ORDER))

    def test_help_exits_zero(self):
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
            v.main(["--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_unknown_ticket_is_exit_2(self):
        code, _, err = self.call("run", "--ticket", "NOPE-1", "--scope", "worker_backend", "--root", str(self.d.root))
        self.assertEqual(code, 2)
        self.assertIn("contract not found", err)

    def test_self_check_json(self):
        self.d.write("src/bucket.py", pk.GOOD)
        code, out, _ = self.call("run", "--ticket", pk.TICKET, "--scope", "worker_backend", "--root", str(self.d.root), "--workspace", str(self.d.workspace),
                                 "--level", "self", "--json")
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)["verdict"], "SELF_PASS")

    def test_failure_exit_code_is_1(self):
        self.d.write("src/bucket.py", pk.GOOD)
        self.d.write("src/rogue.py", "x = 1\n")
        code, out, _ = self.call("run", "--ticket", pk.TICKET, "--scope", "worker_backend", "--root", str(self.d.root), "--workspace", str(self.d.workspace),
                                 "--level", "self")
        self.assertEqual(code, 1)
        self.assertIn("rogue.py", out)

    def test_script_runs_as_a_program(self):
        import subprocess
        r = subprocess.run([sys.executable, str(ROOT / "verifier.py"), "--help"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0)


@unittest.skipUnless(UI_OK, f"CDP linter unavailable: {UI_WHY}")
class UiCheck(unittest.TestCase):
    """worker_ui is judged by the CDP linter, served by a static server on AG3's MASP port."""

    PLAN = """\
project: ui-demo
epoch: 1
items:
  - id: PLT-2-CTA
    goal: Build a call to action button
    touches: [web/index.html, web/spring.js]
    roles:
      web/spring.js: worker_ui
    scopes:
      worker_ui:
        selector: "#cta"
        url: "web/index.html"
        kinematic_spec: {spring_damping_zeta: 0.5, target_apca_lc: 60, optical_trim: "@capsizecss/core"}
"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "plan.yaml").write_text(self.PLAN, encoding="utf-8")
        cp = dp.compile_plan(dp.load_plan(self.root / "plan.yaml"))
        dp.write_plan(cp, self.root / ".planner" / "tickets")
        self.doc = cp.tickets["PLT-2-CTA"]
        self.ws = self.root / "ws"
        (self.ws / "web").mkdir(parents=True)

    def ctx(self):
        return v.Ctx(self.doc, "worker_ui", self.root, self.ws, v.Options(write_report=False, ui_timeout=120))

    def test_good_page_is_verified_and_bad_page_is_rejected(self):
        fx = ROOT / "tests" / "fixtures" / "ui"
        shutil.copy(fx / "spring.js", self.ws / "web" / "spring.js")
        shutil.copy(fx / "good.html", self.ws / "web" / "index.html")
        good = v.check_ui(self.ctx())
        self.assertEqual(good.status, v.PASS, good.detail + str(good.items))
        shutil.copy(fx / "bad.html", self.ws / "web" / "index.html")
        bad = v.check_ui(self.ctx())
        self.assertEqual(bad.status, v.FAIL, bad.detail + str(bad.items))

    def test_unreachable_dev_server_is_inconclusive_and_foreign_port_is_a_failure(self):
        self.doc["worker_scopes"]["worker_ui"]["url"] = "http://localhost:3237/"
        self.assertEqual(v.check_ui(self.ctx()).status, v.INCONCLUSIVE)
        self.doc["worker_scopes"]["worker_ui"]["url"] = "http://localhost:3400/"
        c = v.check_ui(self.ctx())
        self.assertEqual(c.status, v.FAIL)
        self.assertIn("AG5", c.detail)

    def test_missing_selector_or_url_is_inconclusive(self):
        del self.doc["worker_scopes"]["worker_ui"]["selector"]
        self.assertEqual(v.check_ui(self.ctx()).status, v.INCONCLUSIVE)


class WorktreeHelpers(unittest.TestCase):
    def test_changes_and_line_ranges(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            base, cur = Path(t, "b"), Path(t, "c")
            (cur / "src").mkdir(parents=True)
            (cur / "src" / "a.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
            (cur / "gone.txt").write_text("x", encoding="utf-8")
            wt.capture(cur, base)
            (cur / "src" / "a.py").write_text("a = 1\nb = 20\nc = 3\nd = 4\n", encoding="utf-8")
            (cur / "gone.txt").unlink()
            (cur / "new.py").write_text("x\n", encoding="utf-8")
            ch = wt.changes(base, cur)
            self.assertEqual((ch.added, ch.modified, ch.deleted), (["new.py"], ["src/a.py"], ["gone.txt"]))
            self.assertEqual(wt.diff_lines(base, cur, ["src/a.py", "new.py"]), {"src/a.py": [(2, 2), (4, 4)], "new.py": [(1, 1)]})

    def test_ignored_dirs_and_symlinks_are_not_snapshotted(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            root = Path(t, "r")
            (root / "node_modules").mkdir(parents=True)
            (root / "node_modules" / "x.js").write_text("1", encoding="utf-8")
            (root / ".planner").mkdir()
            (root / ".planner" / "board.db").write_text("1", encoding="utf-8")
            (root / "ok.py").write_text("1", encoding="utf-8")
            try:
                os.symlink(str(root / "ok.py"), str(root / "link.py"))
            except (OSError, NotImplementedError):
                pass
            self.assertEqual(sorted(wt.hash_tree(root)), ["ok.py"])

    def test_size_cap_and_big_diff_fallback(self):
        self.assertEqual(wt.changed_line_ranges(None, "a\nb\n"), [(1, 2)])
        self.assertEqual(wt.changed_line_ranges("a\n", ""), [])
        big = "\n".join(str(i) for i in range(wt.MAX_DIFF_LINES + 5))
        self.assertEqual(wt.changed_line_ranges(big, big + "\nx"), [(1, wt.MAX_DIFF_LINES + 6)])

    def test_capture_budget(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            root = Path(t, "r")
            root.mkdir()
            (root / "a.txt").write_text("x" * 100, encoding="utf-8")
            with self.assertRaises(wt.WorktreeError):
                wt.capture(root, Path(t, "d"), max_total_bytes=10)


if __name__ == "__main__":
    unittest.main()
