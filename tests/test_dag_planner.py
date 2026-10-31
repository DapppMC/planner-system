"""M4 tests: dag_planner - plan -> signed Contract-First tickets + DAG."""
import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import dag_planner as dp  # noqa: E402
from core import contract_ticket as ct, miniyaml  # noqa: E402
from core.coordination.blackboard import Board  # noqa: E402
import ticketc  # noqa: E402

PLAN = ROOT / "tests" / "fixtures" / "plans" / "escrow_plan.yaml"


def plan():
    return dp.load_plan(PLAN)


def item(tid, touches, deps=(), **kw):
    d = {"id": tid, "goal": f"goal of {tid}", "touches": list(touches)}
    if deps:
        d["depends_on"] = list(deps)
    d.update(kw)
    return d


INV = {"worker_backend": {"formal_invariants": {"engine": "fast-check", "properties": ["INV-01: p"]}}}


class Classify(unittest.TestCase):
    def test_roles(self):
        c = dp.classify_path
        self.assertEqual(c("packages/db/migrations/20261025_escrow.sql"), "worker_db")
        self.assertEqual(c("prisma/schema.prisma"), "worker_db")
        self.assertEqual(c("db/migrations/0001_init.py"), "worker_db")
        self.assertEqual(c("apps/web/src/features/escrow/SettlementDrawer.tsx"), "worker_ui")
        self.assertEqual(c("web/styles/app.css"), "worker_ui")
        self.assertEqual(c("web/src/components/button.ts"), "worker_ui")
        self.assertEqual(c("packages/api/src/domain/escrow/settle.pure.ts"), "worker_backend")
        self.assertEqual(c("src/server.py"), "worker_backend")
        self.assertEqual(c("packages/api/tests/property/a.spec.ts"), "worker_qa_guard")
        self.assertEqual(c("tests/test_x.py"), "worker_qa_guard")
        self.assertEqual(c("src/widget.test.tsx"), "worker_qa_guard")
        self.assertEqual(c(".github/workflows/ci.yml"), "worker_devops")
        self.assertEqual(c("Dockerfile"), "worker_devops")
        self.assertEqual(c("infra/main.tf"), "worker_devops")
        self.assertEqual(c("APPS\\Web\\src\\Page.TSX"), "worker_ui")
        self.assertEqual(c("src/security/sanitize.py"), "worker_security")
        self.assertEqual(c("packages/api/security/csrf.ts"), "worker_security")
        self.assertEqual(c("src/securityish/x.py"), "worker_backend", "only a whole `security` directory segment routes to AG8")
        self.assertEqual(c("tests/security/test_csrf.py"), "worker_qa_guard", "tests always belong to the QA guard")


class Compile(unittest.TestCase):
    def setUp(self):
        self.cp = dp.compile_plan(plan())

    def test_every_ticket_is_valid_and_signed(self):
        self.assertEqual(list(self.cp.tickets), ["PLT-4090-ESCROW-TYPES", "PLT-4092-ESCROW-SETTLEMENT", "PLT-4093-ESCROW-NOTIFY", "PLT-4094-CI-PIPELINE"])
        for tid, doc in self.cp.tickets.items():
            rep = ct.validate(doc)
            self.assertEqual(rep.errors, [], tid)
            self.assertEqual(ct.verify_signature(doc), (True, ""))
            self.assertEqual(doc["ticket_meta"]["epoch"], 14)

    def test_roles_scopes_and_defaults(self):
        doc = self.cp.tickets["PLT-4092-ESCROW-SETTLEMENT"]
        ws = doc["worker_scopes"]
        self.assertEqual(set(ws), {"worker_db", "worker_backend", "worker_ui", "worker_qa_guard"})
        self.assertEqual(ws["worker_db"]["forbidden_ast"], ["FLOAT", "DOUBLE PRECISION"])
        self.assertEqual(ws["worker_db"]["allowed_paths"], ["packages/db/migrations/20261025_escrow.sql"])
        self.assertEqual(ws["worker_ui"]["selector"], "#settle-drawer")
        self.assertEqual(ws["worker_ui"]["kinematic_spec"], dp.DEFAULT_KINEMATIC)
        self.assertEqual(ws["worker_ui"]["mandatory_fsm"], "@zag-js/core")
        qa = ws["worker_qa_guard"]
        self.assertEqual(qa["role"], "ADVERSARIAL_BOUNTY")
        self.assertEqual(qa["mutation_gate"], {"min_kill_score": 0.92, "target_diff_only": True})
        self.assertIn("vi.mock", qa["forbidden_calls"])
        self.assertEqual({s["worker_id"] for s in ws.values()}, {"AG2", "AG5", "AG3", "AG4"})

    def test_qa_scope_defaults_only_for_production_tickets(self):
        types = self.cp.tickets["PLT-4090-ESCROW-TYPES"]["worker_scopes"]
        self.assertEqual(types["worker_qa_guard"]["allowed_paths"], ["tests/plt-4090-escrow-types/**"])
        ci = self.cp.tickets["PLT-4094-CI-PIPELINE"]["worker_scopes"]
        self.assertEqual(set(ci), {"worker_devops"})
        self.assertTrue(any("defaulting AG4" in w for w in self.cp.warnings))

    def test_symbols_are_built_locked_and_shared(self):
        t1 = self.cp.tickets["PLT-4090-ESCROW-TYPES"]["shared_contracts"]["scip_symbols"][0]
        t2 = self.cp.tickets["PLT-4092-ESCROW-SETTLEMENT"]["shared_contracts"]["scip_symbols"][0]
        self.assertEqual(t1["symbol"], "scip-typescript npm @planner/core 1.0.0 packages/api/src/domain/escrow/`types.ts`/SettlementCommand#")
        self.assertEqual(t1["symbol"], t2["symbol"])
        self.assertEqual(t1["lock"], t2["lock"])
        self.assertEqual(self.cp.symbols[t1["symbol"]]["owner"], "PLT-4090-ESCROW-TYPES")
        self.assertEqual(self.cp.symbols[t1["symbol"]]["lock"], t1["lock"])

    def test_dag_waves_edges_and_critical_path(self):
        dag = self.cp.dag
        self.assertEqual(dag["waves"], [["PLT-4090-ESCROW-TYPES", "PLT-4094-CI-PIPELINE"], ["PLT-4092-ESCROW-SETTLEMENT"], ["PLT-4093-ESCROW-NOTIFY"]])
        kinds = {(e["from"], e["to"]): e["kind"] for e in dag["edges"]}
        self.assertEqual(kinds[("PLT-4090-ESCROW-TYPES", "PLT-4092-ESCROW-SETTLEMENT")], "depends_on")
        self.assertEqual(kinds[("PLT-4092-ESCROW-SETTLEMENT", "PLT-4093-ESCROW-NOTIFY")], "touch-conflict")
        self.assertEqual(dag["critical_path"], ["PLT-4090-ESCROW-TYPES", "PLT-4092-ESCROW-SETTLEMENT", "PLT-4093-ESCROW-NOTIFY"])
        self.assertEqual(dag["predecessors"]["PLT-4093-ESCROW-NOTIFY"], ["PLT-4092-ESCROW-SETTLEMENT"])
        self.assertEqual(self.cp.tickets["PLT-4093-ESCROW-NOTIFY"]["ticket_meta"]["depends_on"], ["PLT-4092-ESCROW-SETTLEMENT"])

    def test_compile_is_deterministic(self):
        again = dp.compile_plan(plan())
        self.assertEqual(again.dag["ticket_sha"], self.cp.dag["ticket_sha"])
        self.assertEqual(miniyaml.dumps(again.tickets["PLT-4092-ESCROW-SETTLEMENT"]), miniyaml.dumps(self.cp.tickets["PLT-4092-ESCROW-SETTLEMENT"]))

    def test_hmac_signing(self):
        cp = dp.compile_plan(plan(), key=b"secret")
        for doc in cp.tickets.values():
            self.assertTrue(doc["ticket_meta"]["lawgiver_signature"].startswith("hmac-sha256:"))
            self.assertEqual(ct.verify_signature(doc, b"secret"), (True, ""))
        self.assertEqual(cp.dag["signature_kind"], "hmac-sha256")

    def test_epoch_override_and_per_item_epoch(self):
        p = plan()
        p["items"][0]["epoch"] = 3
        cp = dp.compile_plan(p, epoch=20)
        self.assertEqual(cp.tickets["PLT-4090-ESCROW-TYPES"]["ticket_meta"]["epoch"], 3)
        self.assertEqual(cp.tickets["PLT-4092-ESCROW-SETTLEMENT"]["ticket_meta"]["epoch"], 20)


class DagLogic(unittest.TestCase):
    def test_independent_items_share_a_wave(self):
        p = {"items": [item("T-AAA", ["src/a.py"], scopes=INV), item("T-BBB", ["src/b.py"], scopes=INV), item("T-CCC", ["src/c.py"], scopes=INV)]}
        dag = dp.compile_plan(p).dag
        self.assertEqual(dag["waves"], [["T-AAA", "T-BBB", "T-CCC"]])
        self.assertEqual(dag["edges"], [])

    def test_overlapping_touches_serialise_in_plan_order(self):
        p = {"items": [item("T-AAA", ["src/shared/*.py"], scopes=INV), item("T-BBB", ["src/shared/util.py"], scopes=INV), item("T-CCC", ["src/other.py"], scopes=INV)]}
        dag = dp.compile_plan(p).dag
        self.assertEqual(dag["waves"], [["T-AAA", "T-CCC"], ["T-BBB"]])
        self.assertEqual([e["kind"] for e in dag["edges"]], ["touch-conflict"])

    def test_case_and_separator_variants_still_conflict(self):
        p = {"items": [item("T-AAA", ["Src/Util.py"], scopes=INV), item("T-BBB", ["src\\util.py"], scopes=INV)]}
        self.assertEqual(len(dp.compile_plan(p).dag["waves"]), 2)

    def test_conflict_between_already_ordered_items_adds_no_extra_edge(self):
        p = {"items": [item("T-AAA", ["src/a.py"], scopes=INV), item("T-BBB", ["src/a.py"], deps=["T-AAA"], scopes=INV)]}
        self.assertEqual([e["kind"] for e in dp.compile_plan(p).dag["edges"]], ["depends_on"])

    def test_transitive_ordering_suppresses_conflict_edge(self):
        p = {"items": [item("T-AAA", ["src/a.py"], scopes=INV), item("T-BBB", ["src/b.py"], deps=["T-AAA"], scopes=INV), item("T-CCC", ["src/a.py"], deps=["T-BBB"], scopes=INV)]}
        kinds = [e["kind"] for e in dp.compile_plan(p).dag["edges"]]
        self.assertEqual(kinds, ["depends_on", "depends_on"])

    def test_cycles_are_reported_with_the_cycle(self):
        p = {"items": [item("T-AAA", ["src/a.py"], deps=["T-CCC"], scopes=INV), item("T-BBB", ["src/b.py"], deps=["T-AAA"], scopes=INV), item("T-CCC", ["src/c.py"], deps=["T-BBB"], scopes=INV)]}
        with self.assertRaises(dp.PlanError) as cm:
            dp.compile_plan(p)
        msg = str(cm.exception)
        self.assertIn("dependency cycle", msg)
        for t in ("T-AAA", "T-BBB", "T-CCC"):
            self.assertIn(t, msg)

    def test_long_chain_and_cycle_do_not_hit_the_recursion_limit(self):
        n = 3000
        order = [f"T-{i:05d}" for i in range(n)]
        preds = {order[i]: {order[i - 1]} for i in range(1, n)}
        self.assertIsNone(dp.find_cycle(order, preds))
        preds[order[0]] = {order[-1]}
        cyc = dp.find_cycle(order, preds)
        self.assertEqual(cyc[0], cyc[-1])
        self.assertEqual(len(cyc), n + 1)

    def test_large_plan_compiles(self):
        items = [item(f"T-{i:04d}", [f"src/m{i}/a.py"], deps=[f"T-{i - 1:04d}"] if i % 5 else (), scopes=INV) for i in range(120)]
        cp = dp.compile_plan({"items": items})
        self.assertEqual(len(cp.tickets), 120)
        self.assertEqual(len(cp.dag["waves"]), 5)
        self.assertEqual(len(cp.dag["critical_path"]), 5)

    def test_symbol_owner_edge(self):
        sig = "export interface A { x: string }"
        sym = {"name": "A", "file": "src/a.ts", "signature": sig}
        p = {"defaults": {"package": "p", "version": "1"},
             "items": [item("T-AAA", ["src/a.ts"], symbols=[sym], scopes=INV), item("T-BBB", ["src/b.ts"], symbols=[dict(sym, owner="T-AAA")], scopes=INV)]}
        dag = dp.compile_plan(p).dag
        self.assertEqual(dag["waves"], [["T-AAA"], ["T-BBB"]])
        self.assertEqual(dag["edges"][0]["kind"], "symbol-owner")

    def test_ready_tickets(self):
        dag = dp.compile_plan(plan()).dag
        self.assertEqual(dp.ready_tickets(dag, set()), ["PLT-4090-ESCROW-TYPES", "PLT-4094-CI-PIPELINE"])
        self.assertEqual(dp.ready_tickets(dag, {"PLT-4090-ESCROW-TYPES"}), ["PLT-4094-CI-PIPELINE", "PLT-4092-ESCROW-SETTLEMENT"])
        self.assertEqual(dp.ready_tickets(dag, {t for w in dag["waves"] for t in w}), [])


class PlanErrors(unittest.TestCase):
    def bad(self, mutate, needle):
        p = plan()
        mutate(p)
        with self.assertRaises(dp.PlanError) as cm:
            dp.compile_plan(p)
        self.assertIn(needle, str(cm.exception))

    def test_structure(self):
        self.bad(lambda p: p.update(items=[]), "non-empty")
        self.bad(lambda p: p.update(extra=1), "unknown plan key")
        self.bad(lambda p: p["items"][0].update(extra=1), "unknown key")
        self.bad(lambda p: p["items"][0].update(id="x"), "must match")
        self.bad(lambda p: p["items"][1].update(id=p["items"][0]["id"]), "duplicate item id")
        self.bad(lambda p: p["items"][0].pop("goal"), "goal is required")
        self.bad(lambda p: p["items"][0].update(touches=[]), "touches")
        self.bad(lambda p: p["items"][1].update(depends_on=["T-NOPE"]), "unknown ticket")
        self.bad(lambda p: p["items"][1].update(depends_on=[p["items"][1]["id"]]), "depends on itself")
        self.bad(lambda p: p["items"][0].update(touches=["../x.py"]), "touches")
        self.bad(lambda p: p["items"][0].update(touches=[".env"]), "protected")

    def test_not_a_mapping(self):
        with self.assertRaises(dp.PlanError):
            dp.compile_plan([])
        with self.assertRaises(dp.PlanError):
            dp.compile_plan({"items": [5]})

    def test_symbol_problems(self):
        def conflict(p):
            p["items"][2]["symbols"] = [{"name": "SettlementCommand", "file": "packages/api/src/domain/escrow/types.ts", "kind": "type", "signature": "export interface SettlementCommand { other: 1 }"}]
        self.bad(conflict, "different signature")
        self.bad(lambda p: p["items"][0]["symbols"][0].pop("signature"), "signature is required")
        self.bad(lambda p: p["items"][0]["symbols"][0].pop("file"), "needs either")
        self.bad(lambda p: p["defaults"].pop("package"), "defaults.package")
        def ghost(p):
            p["items"][0]["symbols"][0]["owner"] = "T-GHOST"
            p["items"][1].pop("symbols")
        self.bad(ghost, "not a ticket in this plan")
        self.bad(lambda p: p["items"][1]["symbols"][0].update(owner="PLT-4093-ESCROW-NOTIFY"), "disagrees")

    def test_scope_problems(self):
        self.bad(lambda p: p["items"][0]["scopes"].update(worker_ghost={}), "unknown scope")
        self.bad(lambda p: p["items"][0].update(roles={"packages/api/src/domain/escrow/types.ts": "worker_ghost"}), "unknown scope")

    def test_invalid_contract_from_bad_overrides_is_reported(self):
        p = plan()
        p["items"][1]["scopes"]["worker_backend"]["formal_invariants"]["properties"] = ["not an invariant"]
        with self.assertRaises(dp.PlanError) as cm:
            dp.compile_plan(p)
        self.assertIn("invalid contract", str(cm.exception))

    def test_roles_override(self):
        p = {"items": [item("T-AAA", ["lib/odd.thing"], roles={"lib/odd.thing": "worker_db"}, skip_scopes=["worker_qa_guard"])]}
        doc = dp.compile_plan(p).tickets["T-AAA"]
        self.assertEqual(set(doc["worker_scopes"]), {"worker_db"})
        p["items"][0].pop("skip_scopes")
        self.assertEqual(set(dp.compile_plan(p).tickets["T-AAA"]["worker_scopes"]), {"worker_db", "worker_qa_guard"})

    def test_missing_invariants_warn(self):
        cp = dp.compile_plan({"items": [item("T-AAA", ["src/a.py"])]})
        self.assertTrue(any("no formal_invariants" in w for w in cp.warnings))

    def test_load_plan_json_and_errors(self):
        with tempfile.TemporaryDirectory() as t:
            j = Path(t) / "p.json"
            j.write_text(json.dumps({"items": [item("T-AAA", ["src/a.py"], scopes=INV)]}))
            self.assertEqual(list(dp.compile_plan(dp.load_plan(j)).tickets), ["T-AAA"])
            (Path(t) / "bad.json").write_text("{nope")
            with self.assertRaises(dp.PlanError):
                dp.load_plan(Path(t) / "bad.json")
            (Path(t) / "bad.yaml").write_text("a: 1\na: 2\n")
            with self.assertRaises(dp.PlanError):
                dp.load_plan(Path(t) / "bad.yaml")


class WriteAndCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dag_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = dp.main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def test_write_plan_files_are_reloadable(self):
        cp = dp.compile_plan(plan())
        files = dp.write_plan(cp, os.path.join(self.tmp, "tickets"), flat=True)
        self.assertTrue(all(os.path.exists(f) for f in files))
        for tid, doc in cp.tickets.items():
            back = ct.load_ticket(os.path.join(self.tmp, "tickets", f"{tid}.yaml"))
            self.assertEqual(back, doc)
            self.assertEqual(ct.validate(back).errors, [])
        with open(os.path.join(self.tmp, "dag.json")) as fh:
            dag = json.load(fh)
        self.assertEqual(dag["waves"][0], ["PLT-4090-ESCROW-TYPES", "PLT-4094-CI-PIPELINE"])
        with open(os.path.join(self.tmp, "symbols.lock.json")) as fh:
            locks = json.load(fh)
        self.assertEqual(len(locks), 1)
        for f in Path(self.tmp, "tickets", "flat").glob("*.yaml"):
            self.assertEqual(ticketc.validate_data(ticketc.load(str(f)), str(ROOT)), [], f.name)

    def test_cli_check_writes_nothing(self):
        rc, out, _ = self.run_cli(str(PLAN), "--out", os.path.join(self.tmp, "t"), "--check")
        self.assertEqual(rc, 0)
        self.assertIn("4 tickets in 3 waves", out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "t")))

    def test_cli_writes_and_registers_on_the_board(self):
        db = os.path.join(self.tmp, "board.db")
        rc, out, err = self.run_cli(str(PLAN), "--out", os.path.join(self.tmp, "tickets"), "--board", db, "--json")
        self.assertEqual(rc, 0, err)
        summary = json.loads(out)
        self.assertEqual(len(summary["tickets"]), 4)
        with Board(db) as b:
            self.assertEqual({t["state"] for t in b.tickets()}, {"CONTRACTED"})
            for tid in summary["tickets"]:
                doc = ct.load_ticket(os.path.join(self.tmp, "tickets", f"{tid}.yaml"))
                self.assertEqual(ct.verify_against_board(b, doc), (True, ""))

    def test_cli_failures_exit_nonzero(self):
        bad = os.path.join(self.tmp, "bad.yaml")
        Path(bad).write_text("items:\n  - id: T-AAA\n    goal: x\n    touches: [src/a.py]\n    depends_on: [T-ZZZ]\n")
        rc, _, err = self.run_cli(bad, "--check")
        self.assertEqual(rc, 1)
        self.assertIn("unknown ticket", err)
        rc, _, err = self.run_cli(os.path.join(self.tmp, "missing.yaml"), "--check")
        self.assertEqual(rc, 1)
        rc, _, err = self.run_cli(str(PLAN), "--check", "--hmac-env", "PLANNER_DEFINITELY_UNSET_KEY")
        self.assertEqual(rc, 2)

    def test_cli_hmac_key_from_environment(self):
        os.environ["PLANNER_TEST_HMAC"] = "k"
        try:
            rc, out, err = self.run_cli(str(PLAN), "--out", os.path.join(self.tmp, "t"), "--hmac-env", "PLANNER_TEST_HMAC")
        finally:
            del os.environ["PLANNER_TEST_HMAC"]
        self.assertEqual(rc, 0, err)
        doc = ct.load_ticket(os.path.join(self.tmp, "t", "PLT-4090-ESCROW-TYPES.yaml"))
        self.assertTrue(doc["ticket_meta"]["lawgiver_signature"].startswith("hmac-sha256:"))

    def test_script_runs_standalone(self):
        r = subprocess.run([sys.executable, "-I", str(ROOT / "dag_planner.py"), str(PLAN), "--check"], capture_output=True, text=True, cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([sys.executable, "-I", str(ROOT / "dag_planner.py"), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()


class SecurityScope(unittest.TestCase):
    def test_security_touch_compiles_to_an_ag8_scope(self):
        cp = dp.compile_plan({"project": "p", "epoch": 1, "items": [
            item("PLT-9-SEC", ["src/api/handler.py", "src/security/sanitize.py"], scopes={
                "worker_backend": INV["worker_backend"],
                "worker_security": {"forbidden_calls": ["eval", "os.system"], "formal_invariants": {"engine": "hypothesis", "properties": ["INV-07: no user input reaches a shell"]}}})]})
        doc = cp.tickets["PLT-9-SEC"]
        ws = doc["worker_scopes"]
        self.assertEqual(set(ws), {"worker_backend", "worker_security", "worker_qa_guard"})
        self.assertEqual(ws["worker_security"]["worker_id"], "AG8")
        self.assertEqual(ws["worker_security"]["allowed_paths"], ["src/security/sanitize.py"])
        self.assertEqual(ws["worker_security"]["forbidden_calls"], ["eval", "os.system"])
        self.assertEqual(ct.validate(doc).errors, [])

    def test_security_only_ticket_still_gets_a_qa_scope(self):
        cp = dp.compile_plan({"project": "p", "epoch": 1, "items": [item("PLT-9-SEC", ["src/security/csrf.py"])]})
        self.assertEqual(set(cp.tickets["PLT-9-SEC"]["worker_scopes"]), {"worker_security", "worker_qa_guard"})

    def test_role_override_can_force_security(self):
        cp = dp.compile_plan({"project": "p", "epoch": 1, "items": [item("PLT-9-SEC", ["src/auth/login.py"], roles={"src/auth/login.py": "worker_security"})]})
        self.assertEqual(cp.tickets["PLT-9-SEC"]["worker_scopes"]["worker_security"]["allowed_paths"], ["src/auth/login.py"])
