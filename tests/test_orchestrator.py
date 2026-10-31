"""Orchestrator tests: scripted fake agents drive the full AG1 -> AG6 -> AG5 -> verifier -> AG4 -> DONE pipeline."""
from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import projectkit as pk  # noqa: E402

import orchestrator as orch  # noqa: E402
import swarm as sw  # noqa: E402
from core import contract_ticket as ct  # noqa: E402
from core.verification import sealed_gate  # noqa: E402

AG4_GOOD = '''from src.bucket import bucket


def test_boundaries_hold():
    assert bucket(0) == "small"
    assert bucket(10) == "medium"
    assert bucket(100) == "large"
    assert bucket(99) == "medium"
    assert bucket(9) == "small"


def test_negative_is_rejected():
    import pytest
    with pytest.raises(ValueError, match="negative"):
        bucket(-1)
'''
AG4_FALSE_CLAIM = '''from src.bucket import bucket


def test_false_claim():
    assert bucket(5) == "large"
'''
AG4_TRUE_CLAIM = '''from src.bucket import bucket


def test_true_claim():
    assert bucket(100) == "large"
'''
VACUOUS = "def test_inv01_inv02_trivial():\n    assert True\n"
NO_INV = "from src.bucket import bucket\n\n\ndef test_something():\n    assert bucket(5) == 'small'\n"


def write(ws: Path, rel: str, text: str) -> None:
    p = Path(ws) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class Agents:
    """Scripted fleet: behaviour per worker, with a call log. Each behaviour is a list used one entry per call
    (the last entry repeats) of either a string (code or a mode) or a callable(task, ctx) -> output."""

    def __init__(self, ag6=None, ag5=None, ag4=None, ag8=None, verdict="REJECTED"):
        self.verdict = verdict
        self.arbiters = []   # who was asked to arbitrate
        self.tiers = []      # (worker, tier) of every task, in order
        self.models = []     # (worker, model string) of every task, in order
        self.script = {"AG6": ag6 or ["good"], "AG5": ag5 or [pk.GOOD], "AG4": ag4 or [AG4_GOOD], "AG8": ag8 or [pk.GOOD]}
        self.calls = []
        self.prompts = []

    def pick(self, worker):
        n = sum(1 for c in self.calls if c == worker)
        lst = self.script[worker]
        return lst[min(n, len(lst) - 1)]

    def __call__(self, task, env, ctx):
        w = task.worker
        self.tiers.append((w, task.tier))
        self.models.append((w, task.model))
        if "acting as arbiter" in task.prompt:
            self.arbiters.append(w)
            return f"VERDICT: {self.verdict}\nthe test {'contradicts' if self.verdict == 'REJECTED' else 'follows'} the contract"
        mode = self.pick(w)
        self.calls.append(w)
        self.prompts.append((w, task.prompt))
        ws = ctx.workspace
        if callable(mode):
            return mode(task, ctx)
        if w == "AG6":
            body = {"good": pk.SEALED_TESTS, "vacuous": VACUOUS, "noinv": NO_INV, "weak": pk.WEAK_SEALED}.get(mode, mode)
            write(ws, "tests/acceptance/AG5/test_sealed.py", body)
            return "REFLECTION: wrote the oracle"
        if w in ("AG5", "AG8"):
            write(ws, "src/bucket.py", mode)
            return "REFLECTION: implemented\n"
        if w == "AG4":
            write(ws, "tests/property/test_props.py", mode)
            return "REFLECTION: attacked"
        return ""


class OrchBase(unittest.TestCase):
    def setUp(self):
        self.demo = pk.Demo(sealed=None, register=False)
        self.demo.board.close()
        self.root = self.demo.root

    def tearDown(self):
        self.demo.close()

    def make(self, agents, **policy):
        pol = orch.Policy(claim_wait=2.0, mutation_max=40, mutation_budget=240, sealed_timeout=90, visible_timeout=90, **policy)
        return orch.Orchestrator(self.root, sw.CallableExecutor(agents), policy=pol)


class HappyPath(OrchBase):
    def test_full_pipeline_reaches_done(self):
        ag = Agents()
        with self.make(ag) as o:
            summary = o.run()
            self.assertTrue(summary.ok, summary.to_dict())
            oc = summary.outcomes[pk.TICKET]
            self.assertEqual(oc.state, "DONE")
            self.assertEqual(o.board.get_ticket(pk.TICKET)["state"], "DONE")
        self.assertEqual((self.root / "src" / "bucket.py").read_text(), pk.GOOD)
        self.assertTrue((self.root / "tests" / "property" / "test_props.py").is_file(), "AG4's tests are kept")
        self.assertIn("AG6", ag.calls)
        self.assertIn("AG5", ag.calls)
        self.assertIn("AG4", ag.calls)
        ok, problems, _ = sealed_gate.verify_vault(f"{pk.TICKET}.AG5", self.root / ".planner" / "sealed")
        self.assertTrue(ok, problems)
        self.assertTrue((self.root / ".planner" / "runs" / f"{pk.TICKET}.json").is_file())
        self.assertGreaterEqual(oc.ledger["kill_ratio"], 0.92)
        self.assertIsNotNone(oc.ledger["utility"])

    def test_sealed_tests_never_enter_the_implementer_workspace(self):
        ag = Agents()
        with self.make(ag) as o:
            o.run()
        ws = self.root / ".planner" / "work" / pk.TICKET / "AG5"
        self.assertFalse((ws / "tests" / "acceptance").exists())
        for who, prompt in ag.prompts:
            if who == "AG5":
                self.assertNotIn("pytest.raises", prompt)
                self.assertNotIn("test_inv01", prompt)

    def test_rerun_is_idempotent(self):
        with self.make(Agents()) as o:
            self.assertTrue(o.run().ok)
        ag2 = Agents()
        with self.make(ag2) as o:
            s = o.run()
        self.assertTrue(s.ok)
        self.assertEqual(ag2.calls, [], "nothing is re-executed for a DONE ticket")

    def test_board_events_are_posted(self):
        with self.make(Agents()) as o:
            o.run()
            kinds = {e["kind"] for e in o.board.events_for(pk.TICKET)}
        if True:
            self.assertTrue({"sealed", "dispatched", "ticket_done"} <= kinds, kinds)

    def test_agent_ports_are_inside_masp_ranges(self):
        seen = {}

        def impl(task, ctx):
            seen["port"] = int(ctx.ports.get("DEV_PORT", 0) or 0)
            write(ctx.workspace, "src/bucket.py", pk.GOOD)
            return "ok"

        with self.make(Agents(ag5=[impl])) as o:
            o.run()
        self.assertTrue(3400 <= seen["port"] <= 3499, seen)


class OracleQuality(OrchBase):
    def test_vacuous_oracle_is_rejected_and_ticket_escalates(self):
        with self.make(Agents(ag6=["vacuous"]), max_attempts=2) as o:
            s = o.run()
        oc = s.outcomes[pk.TICKET]
        self.assertEqual(oc.state, "ESCALATED")
        self.assertIn("VACUOUS", Path(oc.escalation).read_text())
        self.assertFalse((self.root / ".planner" / "sealed" / f"{pk.TICKET}.AG5").exists(), "a vacuous vault must not survive")

    def test_missing_invariant_reference_is_fed_back_to_ag6(self):
        ag = Agents(ag6=["noinv", "good"])
        with self.make(ag) as o:
            s = o.run()
        self.assertTrue(s.ok)
        self.assertEqual(ag.calls.count("AG6"), 2)
        second = [p for w, p in ag.prompts if w == "AG6"][1]
        self.assertIn("INV-01", second)

    def test_ag6_writing_outside_acceptance_dir_is_rejected(self):
        def bad(task, ctx):
            write(ctx.workspace, "src/bucket.py", pk.GOOD)
            write(ctx.workspace, "tests/acceptance/AG5/test_sealed.py", pk.SEALED_TESTS)
            return "done"

        with self.make(Agents(ag6=[bad]), max_attempts=2) as o:
            oc = o.run().outcomes[pk.TICKET]
        self.assertEqual(oc.state, "ESCALATED")
        self.assertIn("outside tests/acceptance", Path(oc.escalation).read_text())

    def test_human_supplied_sealed_tests_skip_ag6(self):
        src = self.root.parent / (self.root.name + "_human") / pk.TICKET / "AG5"
        src.mkdir(parents=True)
        (src / "test_sealed.py").write_text(pk.SEALED_TESTS, encoding="utf-8")
        ag = Agents()
        try:
            with self.make(ag, sealed_dir=str(src.parent.parent)) as o:
                s = o.run()
            self.assertTrue(s.ok, s.to_dict())
            self.assertNotIn("AG6", ag.calls)
        finally:
            shutil.rmtree(src.parent.parent, ignore_errors=True)

    def test_tampered_vault_is_never_silently_resealed(self):
        with self.make(Agents(ag5=["raise SystemExit"]), max_attempts=1) as o:
            o.run()
        vault = self.root / ".planner" / "sealed" / f"{pk.TICKET}.AG5" / "tests" / "test_sealed.py"
        self.assertTrue(vault.is_file())
        import os
        os.chmod(vault, 0o644)
        vault.write_text("def test_inv01_x():\n    assert True\n", encoding="utf-8")
        with self.make(Agents()) as o:
            oc = o.run().outcomes[pk.TICKET]
        self.assertEqual(oc.state, "ESCALATED")
        self.assertIn("tampered", Path(oc.escalation).read_text().lower())


class ReflexionLoop(OrchBase):
    def test_wrong_first_attempt_gets_oracle_safe_feedback_then_passes(self):
        ag = Agents(ag5=[pk.LAZY, pk.GOOD])
        with self.make(ag) as o:
            s = o.run()
        oc = s.outcomes[pk.TICKET]
        self.assertEqual(oc.state, "DONE", oc.reason)
        self.assertEqual(oc.rounds, 2)
        second = [p for w, p in ag.prompts if w == "AG5"][1]
        self.assertIn("PREVIOUS ATTEMPT FAILED", second)
        self.assertNotIn("pytest.raises", second, "the oracle's source must never leak")
        self.assertNotIn('match="negative"', second)

    def test_same_failure_twice_escalates_early(self):
        ag = Agents(ag5=[pk.LAZY])
        with self.make(ag, max_attempts=5) as o:
            oc = o.run().outcomes[pk.TICKET]
        self.assertEqual(oc.state, "ESCALATED")
        self.assertEqual(oc.rounds, 2)
        self.assertIn("not converging", oc.reason)
        text = Path(oc.escalation).read_text()
        self.assertIn("Escalation", text)
        self.assertEqual(ag.calls.count("AG5"), 2)

    def test_standby_gets_one_extra_attempt(self):
        ag = Agents(ag5=[pk.LAZY], ag8=[pk.GOOD])
        with self.make(ag, max_attempts=3, use_standby=True) as o:
            oc = o.run().outcomes[pk.TICKET]
        self.assertEqual(oc.state, "DONE", oc.reason)
        self.assertIn("AG8", ag.calls)
        self.assertEqual(ag.calls.count("AG5"), 2)

    def test_standby_is_used_once_only(self):
        ag = Agents(ag5=[pk.LAZY], ag8=[pk.LAZY])
        with self.make(ag, max_attempts=3, use_standby=True) as o:
            oc = o.run().outcomes[pk.TICKET]
        self.assertEqual(oc.state, "ESCALATED")
        self.assertEqual(ag.calls.count("AG8"), 1)

    def test_scope_violation_is_a_failure(self):
        def sneaky(task, ctx):
            write(ctx.workspace, "src/bucket.py", pk.GOOD)
            write(ctx.workspace, ".env", "SECRET=1\n")
            return "ok"

        ag = Agents(ag5=[sneaky, pk.GOOD])
        with self.make(ag) as o:
            oc = o.run().outcomes[pk.TICKET]
        self.assertEqual(oc.state, "DONE")
        self.assertEqual(oc.rounds, 2)
        self.assertFalse((self.root / ".env").exists())

    def test_crashing_agent_is_a_failed_attempt_not_a_crash(self):
        def boom(task, ctx):
            raise RuntimeError("model API down")

        ag = Agents(ag5=[boom, pk.GOOD])
        with self.make(ag) as o:
            oc = o.run().outcomes[pk.TICKET]
        self.assertEqual(oc.state, "DONE")

    def test_dry_run_executor_cannot_author_so_ticket_escalates(self):
        pol = orch.Policy(claim_wait=1.0, max_attempts=1)
        with orch.Orchestrator(self.root, sw.DryRunExecutor(), policy=pol) as o:
            oc = o.run().outcomes[pk.TICKET]
        self.assertEqual(oc.state, "ESCALATED")


class MutationAndBounty(OrchBase):
    def test_weak_oracle_is_caught_by_the_mutation_gate_and_ag4_earns_the_bounty(self):
        ag = Agents(ag6=["weak"], ag4=[AG4_GOOD])
        with self.make(ag, require_red=False) as o:
            oc = o.run().outcomes[pk.TICKET]
        self.assertEqual(oc.state, "DONE", oc.reason)
        self.assertIn("AG4", ag.calls)
        self.assertGreaterEqual(oc.ledger["kill_ratio"], 0.92)

    def test_survivors_that_ag4_cannot_kill_block_the_ticket(self):
        useless = "def test_nothing_useful():\n    assert 1 + 1 == 2\n"
        ag = Agents(ag6=["weak"], ag4=[useless])
        with self.make(ag, require_red=False, bounty_rounds=1, max_attempts=1) as o:
            oc = o.run().outcomes[pk.TICKET]
        self.assertEqual(oc.state, "ESCALATED")
        self.assertIn("MUTATION", Path(oc.escalation).read_text())
        self.assertEqual((self.root / "src" / "bucket.py").read_text(), pk.STUB, "nothing is merged when the gate fails")

    def test_mutation_off_is_recorded_as_a_waiver(self):
        ag = Agents(ag6=["weak"])
        with self.make(ag, require_red=False, mutation="off", bounty="off") as o:
            oc = o.run().outcomes[pk.TICKET]
        self.assertEqual(oc.state, "DONE")
        self.assertEqual(oc.waivers, ["mutation"])

    def test_false_claim_is_rejected_by_the_arbiter_and_counts_against_ag4(self):
        def impl(task, ctx):
            write(ctx.workspace, "src/bucket.py", pk.GOOD)
            out = "REFLECTION: done"
            if "CLAIMS" in task.prompt:
                out += "\nDISPUTE: tests/property/test_props.py::test_false_claim - bucket(5) is small per INV-01"
            return out

        ag = Agents(ag5=[impl], ag4=[AG4_FALSE_CLAIM])
        with self.make(ag) as o:
            oc = o.run().outcomes[pk.TICKET]
        self.assertEqual(oc.state, "DONE", oc.reason)
        self.assertEqual(oc.rounds, 2)
        self.assertEqual(oc.ledger["false_positives"], 1)
        self.assertFalse((self.root / "tests" / "property" / "test_props.py").exists(), "a rejected AG4 test is not merged")
        self.assertEqual(ag.arbiters, ["AG8"], "AG8 (The Arbiter) arbitrates unless it took part in the ticket")

    def test_true_claim_goes_back_to_the_implementer(self):
        ag = Agents(ag5=[pk.GOOD.replace("n < 100", "n < 101"), pk.GOOD], ag4=[AG4_TRUE_CLAIM])
        with self.make(ag, max_attempts=3) as o:
            oc = o.run().outcomes[pk.TICKET]
        # the sealed oracle already catches bucket(100): the first round fails at the verifier, so AG4 sees the fixed code
        self.assertEqual(oc.state, "DONE", oc.reason)
        self.assertEqual(oc.rounds, 2)

    def test_ag4_cannot_touch_production_code(self):
        def evil(task, ctx):
            write(ctx.workspace, "src/bucket.py", "def bucket(n):\n    return 'small'\n")
            write(ctx.workspace, "tests/property/test_props.py", AG4_GOOD)
            return "ok"

        ag = Agents(ag4=[evil])
        with self.make(ag, max_attempts=1) as o:
            oc = o.run().outcomes[pk.TICKET]
        self.assertEqual(oc.state, "ESCALATED")
        self.assertIn("scope", Path(oc.escalation).read_text())
        self.assertEqual((self.root / "src" / "bucket.py").read_text(), pk.STUB)

    def test_utility_formula_uses_policy_weights(self):
        with self.make(Agents(), alpha=1.0, beta=10.0, gamma=0.0) as o:
            oc = o.run().outcomes[pk.TICKET]
        led = oc.ledger
        self.assertAlmostEqual(led["utility"], round(led["falsified"] + 10 * led["kill_ratio"], 3))


class IntegrationSafety(OrchBase):
    def test_merge_conflict_with_a_moved_integration_tree_is_not_overwritten(self):
        def impl(task, ctx):
            write(ctx.workspace, "src/bucket.py", pk.GOOD)
            # someone else changes the integration tree while this worker is busy
            (self.root / "src" / "bucket.py").write_text(pk.STUB + "\n# hot fix\n", encoding="utf-8")
            return "ok"

        with self.make(Agents(ag5=[impl]), max_attempts=2) as o:
            oc = o.run().outcomes[pk.TICKET]
        self.assertEqual(oc.state, "ESCALATED")
        self.assertIn("MERGE_CONFLICT", Path(oc.escalation).read_text())
        self.assertIn("hot fix", (self.root / "src" / "bucket.py").read_text())

    def test_edited_ticket_is_quarantined(self):
        p = self.root / ".planner" / "tickets" / f"{pk.TICKET}.yaml"
        with self.make(Agents()) as o:
            o.register_all(o.load_tickets())
        text = p.read_text(encoding="utf-8").replace("Classify numbers", "Classify EVERYTHING")
        p.write_text(text, encoding="utf-8")
        with self.make(Agents()) as o:
            oc = o.run().outcomes.get(pk.TICKET)
            blocked = o.run().blocked
        state = oc.state if oc else "BLOCKED"
        self.assertIn(state, ("QUARANTINED", "BLOCKED"), (oc, blocked))

    def test_unsigned_ticket_is_refused_unless_allowed(self):
        p = self.root / ".planner" / "tickets" / f"{pk.TICKET}.yaml"
        doc = ct.load_ticket(p)
        doc["ticket_meta"].pop("lawgiver_signature", None)
        ct.save_ticket(p, doc)
        with self.make(Agents()) as o:
            s = o.run()
        self.assertFalse(s.ok)
        self.assertIn(pk.TICKET, s.blocked)

    def test_dependency_not_done_blocks_the_ticket(self):
        p = self.root / ".planner" / "dag.json"
        dag = json.loads(p.read_text())
        dag["predecessors"][pk.TICKET] = ["PLT-0-NOPE"]
        p.write_text(json.dumps(dag))
        with self.make(Agents()) as o:
            s = o.run()
        self.assertIn(pk.TICKET, s.blocked)
        self.assertIn("PLT-0-NOPE", s.blocked[pk.TICKET])


def _sneaky(task, ctx):
    """A different kind of failure than the sealed oracle: a forbidden file outside the write scope."""
    write(ctx.workspace, "src/bucket.py", pk.GOOD)
    write(ctx.workspace, ".env", "SECRET=1\n")
    return "REFLECTION: done"


OFF_BY_ONE = pk.GOOD.replace("n < 100", "n < 101")


class EightWorkerFleet(OrchBase):
    """AG8 as an active worker: tier rotation (--lead), escalation solver promotion, arbiter independence."""

    def test_default_lead_is_ag1_everyone_else_runs_standard(self):
        ag = Agents()
        with self.make(ag, models={"premium": "big-model", "standard": "small-model"}) as o:
            self.assertTrue(o.run().ok)
        tiers = dict(ag.tiers)
        self.assertEqual({tiers["AG6"], tiers["AG5"], tiers["AG4"]}, {"standard"})
        self.assertEqual({m for _w, m in ag.models}, {"small-model"})

    def test_lead_rotation_makes_ag6_the_premium_worker(self):
        ag = Agents()
        with self.make(ag, lead="6", models={"premium": "big-model", "standard": "small-model"}) as o:
            self.assertTrue(o.run().ok)
        self.assertIn(("AG6", "premium"), ag.tiers)
        self.assertNotIn(("AG6", "standard"), ag.tiers)
        self.assertEqual({m for w, m in ag.models if w == "AG6"}, {"big-model"})
        self.assertEqual({m for w, m in ag.models if w == "AG5"}, {"small-model"})

    def test_plan_view_shows_the_effective_tier(self):
        with self.make(Agents(), lead="8") as o:
            view = o.plan_view()
        tiers = {sc["worker"]: sc["tier"] for wave in view["waves"] for e in wave for sc in e["scopes"]}
        self.assertEqual(tiers["AG5"], "standard")
        with self.make(Agents()) as o:
            self.assertEqual(o._tier("AG1"), "premium")
        with self.make(Agents(), lead="AG8") as o:
            self.assertEqual((o._tier("AG1"), o._tier("AG8"), o._tier("AG6")), ("standard", "premium", "standard"))

    def test_escalation_solver_runs_premium_and_stands_in_for_the_failed_worker(self):
        ag = Agents(ag5=[_sneaky, OFF_BY_ONE], ag8=[pk.GOOD])
        with self.make(ag, max_attempts=5, use_standby=True, standby_after=2) as o:
            oc = o.run().outcomes[pk.TICKET]
        self.assertEqual(oc.state, "DONE", oc.reason)
        self.assertEqual(ag.calls.count("AG5"), 2, "two Reflexion retries, then the solver is summoned")
        self.assertEqual(ag.calls.count("AG8"), 1)
        self.assertIn(("AG8", "premium"), ag.tiers, "the chameleon is promoted for the escalation")
        self.assertEqual([t for w, t in ag.tiers if w == "AG5"], ["standard", "standard"])

    def test_standby_after_three_lets_the_original_worker_try_again_first(self):
        ag = Agents(ag5=[_sneaky, OFF_BY_ONE, pk.GOOD], ag8=[pk.GOOD])
        with self.make(ag, max_attempts=5, use_standby=True, standby_after=3) as o:
            oc = o.run().outcomes[pk.TICKET]
        self.assertEqual(oc.state, "DONE", oc.reason)
        self.assertEqual(ag.calls.count("AG5"), 3)
        self.assertEqual(ag.calls.count("AG8"), 0)

    def test_no_standby_flag_means_no_ag8_even_after_repeated_failures(self):
        ag = Agents(ag5=[_sneaky, OFF_BY_ONE, pk.GOOD], ag8=[pk.GOOD])
        with self.make(ag, max_attempts=5, use_standby=False, standby_after=2) as o:
            oc = o.run().outcomes[pk.TICKET]
        self.assertEqual(ag.calls.count("AG8"), 0)
        self.assertEqual(oc.state, "DONE", oc.reason)

    def test_arbiter_choice_stays_independent(self):
        with self.make(Agents()) as o:
            run = orch.RunRecord(o.planner / "runs" / "x.json")
            plain = {"worker_scopes": {"worker_backend": {}}}
            sec = {"worker_scopes": {"worker_backend": {}, "worker_security": {}}}
            self.assertEqual(o._arbiter_for(plain, run), "AG8")
            self.assertEqual(o._arbiter_for(sec, run), "AG6", "AG8 implements worker_security, so it cannot judge the same ticket")
            run.data["standby_used"] = True
            self.assertEqual(o._arbiter_for(plain, run), "AG6", "AG8 solved part of the ticket as escalation solver")
        pol = dict(arbiter="AG6")
        with self.make(Agents(), **pol) as o:
            run = orch.RunRecord(o.planner / "runs" / "y.json")
            self.assertEqual(o._arbiter_for({"worker_scopes": {}}, run), "AG6")
        with self.make(Agents(), arbiter="AG8") as o:
            run = orch.RunRecord(o.planner / "runs" / "z.json")
            self.assertEqual(o._arbiter_for({"worker_scopes": {"worker_security": {}}}, run), "AG6")
            self.assertEqual(o._arbiter_for({"worker_scopes": {}}, run), "AG8")

    def test_upheld_verdict_keeps_the_claim_and_the_arbiter_is_logged(self):
        def impl(task, ctx):
            write(ctx.workspace, "src/bucket.py", pk.GOOD)
            out = "REFLECTION: done"
            if "CLAIMS" in task.prompt:
                out += "\nDISPUTE: tests/property/test_props.py::test_true_claim - I disagree"
            return out
        ag = Agents(ag5=[pk.GOOD.replace("n < 100", "n < 101"), impl, impl], ag4=[AG4_TRUE_CLAIM], verdict="UPHELD")
        with self.make(ag, max_attempts=4) as o:
            o.run()
        self.assertTrue(all(w == "AG8" for w in ag.arbiters))
        for line in (self.root / ".planner" / "runs" / f"{pk.TICKET}.json").read_text().splitlines():
            self.assertNotIn("claim_rejected", line)

    def test_policy_validation(self):
        with self.assertRaises(Exception):
            orch.Policy(lead="2")
        with self.assertRaises(ValueError):
            orch.Policy(arbiter="AG5")
        with self.assertRaises(ValueError):
            orch.Policy(standby_after=0)
        self.assertEqual(orch.Policy(lead=8).lead, "AG8")

    def test_security_scope_is_an_implementing_scope(self):
        self.assertIn("worker_security", orch.IMPL_SCOPES)
        self.assertIn("worker_security", orch.SCOPE_ORDER)
        self.assertEqual(ct.SCOPE_WORKER["worker_security"], "AG8")

    def test_cli_accepts_lead_and_standby_after(self):
        import contextlib
        import io
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = orch.main(["plan", "--root", str(self.root)])
        self.assertEqual(rc, 0, err.getvalue())
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            orch.main(["run", "--root", str(self.root), "--lead", "1", "--standby-after", "x"])


class PlanStatusCli(OrchBase):
    def test_plan_view_lists_waves_ports_and_tiers(self):
        with self.make(Agents()) as o:
            v = o.plan_view()
        entry = v["waves"][0][0]
        self.assertEqual(entry["ticket"], pk.TICKET)
        s = {x["scope"]: x for x in entry["scopes"]}
        self.assertEqual(s["worker_backend"]["worker"], "AG5")
        self.assertTrue(3400 <= s["worker_backend"]["dev_port"] <= 3499)
        self.assertTrue(8400 <= s["worker_backend"]["api_port"] <= 8499)
        self.assertEqual(s["worker_qa_guard"]["tier"], "standard")
        self.assertIn("AG6 sealed tests", entry["needs"])

    def test_status_counts(self):
        with self.make(Agents()) as o:
            o.run()
            st = o.status()
        self.assertEqual(st["counts"].get("DONE"), 1)

    def test_cli_plan_and_status(self):
        import contextlib
        import io
        for cmd in ("plan", "status"):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = orch.main([cmd, "--root", str(self.root)])
            self.assertEqual(rc, 0)
            json.loads(buf.getvalue())

    def test_cli_without_tickets_is_a_usage_error(self):
        import contextlib
        import io
        import tempfile
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(orch.main(["plan", "--root", d]), 2)

    def test_cli_run_with_command_executor(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = orch.main(["run", "--root", str(self.root), "--executor", "dry-run", "--max-attempts", "1", "--json"])
        self.assertEqual(rc, 1)
        self.assertFalse(json.loads(buf.getvalue())["ok"])


class Helpers(unittest.TestCase):
    def test_disputes_and_reflection_parsing(self):
        out = "stuff\nREFLECTION: did X\nDISPUTE: tests/a.py::t - because Y\n"
        self.assertEqual(orch.reflection_of(out), "did X")
        self.assertEqual(orch.disputes_of(out), {"tests/a.py::t": "because Y"})
        self.assertEqual(orch.disputes_of(""), {})

    def test_failure_signature_ignores_numbers(self):
        from verifier import Check, Report, FAIL
        r1, r2 = Report("T", "worker_backend", "AG5"), Report("T", "worker_backend", "AG5")
        r1.checks.append(Check("sealed", FAIL, "3 failed in 1.2s"))
        r2.checks.append(Check("sealed", FAIL, "5 failed in 9.9s"))
        self.assertEqual(orch.failure_signature(r1), orch.failure_signature(r2))

    def test_prompts_are_english_and_name_the_scope(self):
        d = pk.Demo(sealed=None, register=False)
        try:
            p = orch.implementer_prompt(d.doc, "worker_backend")
            self.assertIn("src/bucket.py", p)
            self.assertIn("NEVER TOUCH", p)
            self.assertIn("INV-01", p)
            q = orch.inquisitor_prompt(d.doc, ["worker_backend"])
            self.assertIn("MUST FAIL against the current stubs", q)
        finally:
            d.close()



PLAN2 = """\
project: demo2
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
  - id: PLT-2-LABEL
    goal: Label a number using the bucket classifier
    depends_on: [PLT-1-BUCKET]
    touches:
      - src/label.py
    symbols:
      - name: label
        file: src/label.py
        kind: function
        signature: |
          def label(n: int) -> str:
    scopes:
      worker_backend:
        formal_invariants:
          engine: hypothesis
          properties:
            - "INV-01: label wraps the bucket name in angle brackets"
      worker_qa_guard:
        allowed_paths: ["tests/property/**"]
"""

LABEL_IMPL = '''from src.bucket import bucket


def label(n: int) -> str:
    return "<" + bucket(n) + ">"
'''

LABEL_SEALED = '''from src.label import label


def test_inv01_label_wraps_the_bucket():
    assert label(5) == "<small>"
    assert label(50) == "<medium>"
    assert label(500) == "<large>"
'''


class TwoTickets(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory(prefix="two_", ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "bucket.py").write_text(pk.STUB, encoding="utf-8")
        (self.root / "src" / "label.py").write_text("def label(n):\n    raise NotImplementedError\n", encoding="utf-8")
        import dag_planner as dp
        (self.root / "plan.yaml").write_text(PLAN2, encoding="utf-8")
        cp = dp.compile_plan(dp.load_plan(self.root / "plan.yaml"))
        dp.write_plan(cp, self.root / ".planner" / "tickets")

    def tearDown(self):
        self._tmp.cleanup()

    def test_dependency_order_and_both_merge_into_the_tree(self):
        order = []

        def ag6(task, ctx):
            if "VERDICT" in task.prompt:
                return "VERDICT: REJECTED"
            if "PLT-1-BUCKET" in task.prompt:
                write(ctx.workspace, "tests/acceptance/AG5/test_sealed.py", pk.SEALED_TESTS)
            else:
                write(ctx.workspace, "tests/acceptance/AG5/test_sealed.py", LABEL_SEALED)
            return "REFLECTION: sealed"

        def ag5(task, ctx):
            order.append(task.ticket)
            if task.ticket == "PLT-1-BUCKET":
                write(ctx.workspace, "src/bucket.py", pk.GOOD)
            else:
                write(ctx.workspace, "src/label.py", LABEL_IMPL)
            return "REFLECTION: done"

        def ag4(task, ctx):
            # a second ticket's AG4 must add its own file: rewriting ticket 1's tests would be flagged as weakening
            name = "test_props.py" if "PLT-1-BUCKET" in task.prompt else "test_props_label.py"
            write(ctx.workspace, "tests/property/" + name, AG4_GOOD if "PLT-1-BUCKET" in task.prompt else
                  "from src.label import label\n\n\ndef test_label_small():\n    assert label(0) == '<small>'\n    assert label(100) == '<large>'\n")
            return "ok"

        class Fleet:
            def __call__(self, task, env, ctx):
                return {"AG6": ag6, "AG8": ag6, "AG5": ag5, "AG4": ag4}[task.worker](task, ctx)

        pol = orch.Policy(claim_wait=2.0, mutation_max=30, mutation_budget=200, sealed_timeout=90, visible_timeout=90, mutation_threshold=0.85)
        with orch.Orchestrator(self.root, sw.CallableExecutor(Fleet()), policy=pol) as o:
            s = o.run()
        self.assertTrue(s.ok, s.to_dict())
        self.assertEqual(order, ["PLT-1-BUCKET", "PLT-2-LABEL"])
        self.assertEqual((self.root / "src" / "bucket.py").read_text(), pk.GOOD)
        self.assertEqual((self.root / "src" / "label.py").read_text(), LABEL_IMPL)

    def test_a_failed_first_ticket_blocks_its_dependant(self):
        def fleet(task, env, ctx):
            if task.worker == "AG6":
                write(ctx.workspace, "tests/acceptance/AG5/test_sealed.py", VACUOUS)
            return "x"

        pol = orch.Policy(claim_wait=1.0, max_attempts=1)
        with orch.Orchestrator(self.root, sw.CallableExecutor(fleet), policy=pol) as o:
            s = o.run()
        self.assertEqual(s.outcomes["PLT-1-BUCKET"].state, "ESCALATED")
        self.assertIn("PLT-2-LABEL", s.blocked)
        self.assertIn("PLT-1-BUCKET", s.blocked["PLT-2-LABEL"])
        self.assertFalse(s.ok)

    def test_only_selects_tickets(self):
        pol = orch.Policy(claim_wait=1.0, max_attempts=1)
        with orch.Orchestrator(self.root, sw.DryRunExecutor(), policy=pol) as o:
            s = o.run(only=["PLT-2-LABEL"])
        self.assertIn("PLT-2-LABEL", s.blocked)
        self.assertNotIn("PLT-1-BUCKET", s.outcomes)
        with orch.Orchestrator(self.root, sw.DryRunExecutor(), policy=pol) as o:
            s = o.run(only=["NOPE"])
        self.assertEqual(s.blocked["NOPE"], "no such ticket")



class Resume(OrchBase):
    def test_escalated_ticket_resumes_with_a_fresh_budget_and_keeps_its_oracle(self):
        with self.make(Agents(ag5=[pk.LAZY]), max_attempts=1) as o:
            self.assertEqual(o.run().outcomes[pk.TICKET].state, "ESCALATED")
        vault_manifest = (self.root / ".planner" / "sealed" / f"{pk.TICKET}.AG5" / "manifest.json").read_text()
        ag = Agents(ag5=[pk.GOOD])
        with self.make(ag, max_attempts=1) as o:
            oc = o.run().outcomes[pk.TICKET]
        self.assertEqual(oc.state, "DONE", oc.reason)
        self.assertNotIn("AG6", ag.calls, "the sealed oracle is kept, not rewritten")
        self.assertEqual((self.root / ".planner" / "sealed" / f"{pk.TICKET}.AG5" / "manifest.json").read_text(), vault_manifest)

    def test_ticket_interrupted_mid_verification_resumes(self):
        with self.make(Agents(ag5=[pk.LAZY]), max_attempts=1) as o:
            o.run()
            o.board.set_ticket(pk.TICKET, "CONTRACTED", owner="test", note="reset")
            o.board.set_ticket(pk.TICKET, "SEALED", owner="test")
            o.board.set_ticket(pk.TICKET, "LEASED", owner="test")
            o.board.set_ticket(pk.TICKET, "IN_PROGRESS", owner="test")
            o.board.set_ticket(pk.TICKET, "VERIFYING", owner="test")
        with self.make(Agents(ag5=[pk.GOOD]), max_attempts=2) as o:
            oc = o.run().outcomes[pk.TICKET]
        self.assertEqual(oc.state, "DONE", oc.reason)


if __name__ == "__main__":
    unittest.main()
