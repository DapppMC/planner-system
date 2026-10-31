"""core/fleet.py: the 8-worker table, MASP port isolation, scans and separation of powers."""
from __future__ import annotations

import contextlib
import io
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import fleet  # noqa: E402

EXPECTED = {
    "AG1": ((3000, 3099), (8000, 8099)), "AG2": ((3100, 3199), (8100, 8199)), "AG3": ((3200, 3299), (8200, 8299)),
    "AG4": ((3300, 3399), (8300, 8399)), "AG5": ((3400, 3499), (8400, 8499)), "AG6": ((3500, 3599), (8500, 8599)),
    "AG7": ((3600, 3699), (8600, 8699)), "AG8": ((3700, 3799), (8700, 8799)),
}


def legacy_standby() -> "fleet.Worker":
    """The 4.0 pre-release AG8 (cold standby, no ports). No worker has this kind any more; the code paths stay tested."""
    return fleet.Worker("AG9", "Cold Standby", "Reserve", fleet.STANDBY, None, None, None, fleet.STANDARD, False)


class Table(unittest.TestCase):
    def test_ranges_match_the_specification(self):
        for wid, (dev, api) in EXPECTED.items():
            self.assertEqual(fleet.FLEET[wid].dev, dev, wid)
            self.assertEqual(fleet.FLEET[wid].api, api, wid)

    def test_table_is_self_consistent(self):
        self.assertEqual(fleet.validate_table(), [])

    def test_eight_active_workers(self):
        self.assertEqual(list(fleet.FLEET), ["AG1", "AG6", "AG2", "AG3", "AG4", "AG5", "AG7", "AG8"])
        self.assertTrue(all(w.has_ports for w in fleet.FLEET.values()))
        self.assertEqual(len(fleet.FLEET), 8)

    def test_ag8_is_the_arbiter_with_its_own_scope_and_ports(self):
        w = fleet.FLEET["AG8"]
        self.assertEqual((w.title, w.kind, w.scope), ("The Arbiter", fleet.ARBITER, "worker_security"))
        self.assertEqual((w.dev, w.api), ((3700, 3799), (8700, 8799)))
        self.assertTrue(w.writes_code)
        self.assertTrue(w.chameleon)
        self.assertEqual(fleet.SCOPE_TO_WORKER["worker_security"], "AG8")
        self.assertIn("AG8", fleet.WRITE_WORKERS)
        self.assertTrue(any("arbitrate QA bounty disputes" in d for d in w.duties))
        self.assertTrue(any("escalation solver" in d for d in w.duties))

    def test_architects_and_leads(self):
        self.assertEqual(fleet.ARCHITECTS, ("AG1", "AG6", "AG8"))
        self.assertEqual(fleet.LEADS, ("AG1", "AG6", "AG8"))

    def test_table_numbering_follows_the_masp_plan(self):
        for i in range(1, 9):
            w = fleet.FLEET[f"AG{i}"]
            self.assertEqual(w.dev, (3000 + 100 * (i - 1), 3099 + 100 * (i - 1)))
            self.assertEqual(w.api, (8000 + 100 * (i - 1), 8099 + 100 * (i - 1)))

    def test_validate_table_rejects_a_wrong_plan_or_a_portless_active_worker(self):
        bad = dict(fleet.FLEET)
        ag8 = fleet.FLEET["AG8"]
        bad["AG8"] = fleet.Worker(ag8.id, ag8.title, ag8.role, ag8.kind, ag8.scope, (3600, 3699), (8700, 8799))
        self.assertTrue(any("overlaps" in p for p in fleet.validate_table(bad)))
        bad["AG8"] = fleet.Worker(ag8.id, ag8.title, ag8.role, ag8.kind, ag8.scope, None, None)
        self.assertTrue(any("must own a dev and an api range" in p for p in fleet.validate_table(bad)))
        bad["AG8"] = fleet.Worker(ag8.id, ag8.title, ag8.role, ag8.kind, ag8.scope, (3800, 3899), (8800, 8899))
        self.assertTrue(any("do not follow the MASP plan" in p for p in fleet.validate_table(bad)))

    def test_legacy_standby_kind_must_not_own_ports(self):
        w = legacy_standby()
        bad = dict(fleet.FLEET)
        bad["AG9"] = fleet.Worker(w.id, w.title, w.role, w.kind, None, (3800, 3899), None)
        self.assertTrue(any("cold standby" in p for p in fleet.validate_table(bad)))

    def test_validate_table_detects_overlap_and_width(self):
        bad = dict(fleet.FLEET)
        ag2 = fleet.FLEET["AG2"]
        bad["AG2"] = fleet.Worker(ag2.id, ag2.title, ag2.role, ag2.kind, ag2.scope, (3050, 3149), ag2.api)
        problems = fleet.validate_table(bad)
        self.assertTrue(any("overlaps" in p for p in problems), problems)
        bad["AG2"] = fleet.Worker(ag2.id, ag2.title, ag2.role, ag2.kind, ag2.scope, (3100, 3150), ag2.api)
        self.assertTrue(any("100 ports" in p for p in fleet.validate_table(bad)))

    def test_default_tiers_only_the_lead_is_premium(self):
        for w in fleet.FLEET.values():
            self.assertEqual(w.model_tier == fleet.PREMIUM, w.id == "AG1", w.id)
            self.assertEqual(fleet.effective_tier(w.id) == fleet.PREMIUM, w.id == "AG1", w.id)
        self.assertEqual({w.id for w in fleet.FLEET.values() if w.chameleon}, {"AG6", "AG8"})

    def test_port_owner(self):
        self.assertEqual(fleet.port_owner(3450), "AG5")
        self.assertEqual(fleet.port_owner(8399), "AG4")
        self.assertIsNone(fleet.port_owner(9000))
        self.assertEqual(fleet.port_owner(3700), "AG8")
        self.assertEqual(fleet.port_owner(8799), "AG8")
        self.assertIsNone(fleet.port_owner(3800))

    def test_scope_mapping(self):
        self.assertEqual(fleet.SCOPE_TO_WORKER["worker_ui"], "AG3")
        self.assertEqual(fleet.SCOPE_TO_WORKER["worker_qa_guard"], "AG4")

    def test_unknown_worker(self):
        with self.assertRaises(fleet.FleetError):
            fleet.get("AG99")
        self.assertEqual(fleet.get("ag5").id, "AG5")


class Allocation(unittest.TestCase):
    def test_deterministic_and_inside_range(self):
        a = fleet.allocate_ports("AG5", "T-1")
        b = fleet.allocate_ports("AG5", "T-1")
        self.assertEqual((a.dev, a.api), (b.dev, b.api))
        self.assertTrue(3400 <= a.dev <= 3499 and 8400 <= a.api <= 8499)

    def test_different_tickets_spread(self):
        ports = {fleet.allocate_ports("AG5", f"T-{i}").dev for i in range(30)}
        self.assertGreater(len(ports), 10)

    def test_taken_ports_are_skipped_within_the_same_range(self):
        first = fleet.allocate_ports("AG3", "X")
        second = fleet.allocate_ports("AG3", "X", taken=[first.dev, first.api])
        self.assertNotEqual(second.dev, first.dev)
        self.assertTrue(3200 <= second.dev <= 3299 and 8200 <= second.api <= 8299)

    def test_exhausted_range_raises(self):
        with self.assertRaises(fleet.FleetError):
            fleet.allocate_ports("AG1", "X", taken=range(3000, 3100))

    def test_wraps_around_the_range_end(self):
        taken = list(range(3000, 3099))  # only 3099 left
        self.assertEqual(fleet.allocate_ports("AG1", "X", taken=taken).dev, 3099)

    def test_ag8_gets_ports_inside_its_own_range(self):
        plan = fleet.allocate_ports("AG8", "T")
        self.assertTrue(3700 <= plan.dev <= 3799 and 8700 <= plan.api <= 8799)

    def test_portless_worker_cannot_get_ports(self):
        with mock.patch.dict(fleet.FLEET, {"AG9": legacy_standby()}):
            with self.assertRaises(fleet.FleetError):
                fleet.allocate_ports("AG9", "T")

    def test_probe_skips_a_busy_port(self):
        plan = fleet.allocate_ports("AG7", "PROBE-1")
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", plan.dev))
            s.listen(1)
        except OSError:
            s.close()
            self.skipTest("preferred port busy on this machine")
        try:
            moved = fleet.allocate_ports("AG7", "PROBE-1", probe=True)
            self.assertNotEqual(moved.dev, plan.dev)
            self.assertTrue(any("busy" in w for w in moved.warnings))
            self.assertTrue(3600 <= moved.dev <= 3699)
            self.assertFalse(fleet.port_is_free(plan.dev))
        finally:
            s.close()

    def test_worker_env(self):
        env = fleet.worker_env("AG5", "T-9", extra={"X": "1"})
        self.assertEqual(env["PLANNER_WORKER"], "AG5")
        self.assertEqual(env["PLANNER_SCOPE"], "worker_backend")
        self.assertEqual(env["PLANNER_MODEL_TIER"], "standard")
        self.assertEqual(env["MASP_DEV_RANGE"], "3400-3499")
        self.assertEqual(env["PORT"], env["DEV_PORT"])
        self.assertEqual(env["X"], "1")
        self.assertEqual(fleet.worker_env("AG6", "T")["PLANNER_MODEL_TIER"], "standard")
        self.assertEqual(fleet.worker_env("AG6", "T", tier="premium")["PLANNER_MODEL_TIER"], "premium")
        env8 = fleet.worker_env("AG8", "T")
        self.assertEqual((env8["MASP_DEV_RANGE"], env8["MASP_API_RANGE"], env8["PLANNER_SCOPE"]), ("3700-3799", "8700-8799", "worker_security"))
        with mock.patch.dict(fleet.FLEET, {"AG9": legacy_standby()}):
            self.assertNotIn("DEV_PORT", fleet.worker_env("AG9", "T"))


class Scan(unittest.TestCase):
    def codes(self, text, worker="AG5"):
        return [(f.code, f.port) for f in fleet.scan_text(text, worker)]

    def test_binding_inside_own_range_is_fine(self):
        self.assertEqual(self.codes("app.listen(3450)"), [])
        self.assertEqual(self.codes("uvicorn app:app --port 8450"), [])
        self.assertEqual(self.codes('server = {"port": 3400}'), [])

    def test_bind_outside_own_range_is_a_violation(self):
        self.assertIn(("BIND_OUTSIDE_RANGE", 3000), self.codes("app.listen(3000)"))
        self.assertIn(("BIND_OUTSIDE_RANGE", 3100), self.codes("vite --port 3100"))
        self.assertIn(("BIND_OUTSIDE_RANGE", 8000), self.codes("PORT=8000 node server.js"))
        self.assertIn(("BIND_OUTSIDE_RANGE", 5050), self.codes('s.bind(("0.0.0.0", 5050))'))

    def test_compose_and_dockerfile(self):
        self.assertIn(("BIND_OUTSIDE_RANGE", 3000), self.codes('    - "3000:3000"', "AG7"))
        self.assertIn(("BIND_OUTSIDE_RANGE", 8080), self.codes("EXPOSE 8080", "AG7"))
        self.assertEqual(self.codes('    - "3650:3650"', "AG7"), [])

    def test_connect_to_a_foreign_range_is_a_violation(self):
        self.assertIn(("CONNECT_FOREIGN_RANGE", 8200), self.codes('fetch("http://localhost:8200/api")'))
        self.assertIn(("CONNECT_FOREIGN_RANGE", 3300), self.codes("curl 127.0.0.1:3300"))

    def test_connecting_to_own_or_infra_ports_is_fine(self):
        self.assertEqual(self.codes('fetch("http://localhost:8400/x")'), [])
        self.assertEqual(self.codes('DB = "postgres://u@localhost:5432/db"'), [])

    def test_well_known_bind_is_only_a_warning(self):
        f = fleet.scan_text("port: 5432", "AG5")
        self.assertEqual([x.severity for x in f], ["warning"])
        self.assertEqual(fleet.violations(f), [])

    def test_comments_are_ignored(self):
        self.assertEqual(self.codes("# app.listen(3000)\n// vite --port 3100"), [])

    def test_ag8_may_bind_its_own_range_only(self):
        self.assertEqual(self.codes("app.listen(3710)", "AG8"), [])
        self.assertEqual([c for c, _ in self.codes("app.listen(3400)", "AG8")], ["BIND_OUTSIDE_RANGE"])
        self.assertEqual([c for c, _ in self.codes("fetch('http://localhost:3710/x')", "AG5")], ["CONNECT_FOREIGN_RANGE"])

    def test_legacy_standby_kind_may_not_touch_ports(self):
        with mock.patch.dict(fleet.FLEET, {"AG9": legacy_standby()}):
            self.assertEqual([c for c, _ in self.codes("app.listen(3400)", "AG9")], ["PORT_FORBIDDEN_FOR_STANDBY"])

    def test_line_numbers(self):
        f = fleet.scan_text("x = 1\n\napp.listen(3000)\n", "AG5", file="a.js")
        self.assertEqual((f[0].file, f[0].line), ("a.js", 3))
        self.assertIn("a.js:3", str(f[0]))

    def test_scan_files_skips_binary_and_symlinks_and_bad_paths(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.js").write_text("app.listen(3000)\n", encoding="utf-8")
            (root / "b.bin").write_bytes(b"\x00app.listen(3000)")
            (root / "c.md").write_text("`vite --port 3000`", encoding="utf-8")
            found = fleet.scan_files(root, ["a.js", "b.bin", "../escape.js", "missing.js", "c.md"], "AG5")
            self.assertEqual(sorted({f.file for f in found}), ["a.js", "c.md"])

    def test_every_worker_may_use_every_port_of_its_own_ranges(self):
        for wid, (dev, api) in EXPECTED.items():
            for p in (dev[0], dev[1], api[0], api[1]):
                self.assertEqual(self.codes(f"app.listen({p})", wid), [], (wid, p))

    def test_no_worker_may_bind_into_any_other_workers_ranges(self):
        for wid in EXPECTED:
            for other, (dev, api) in EXPECTED.items():
                if other == wid:
                    continue
                for p in (dev[0], api[1]):
                    self.assertTrue(fleet.violations(fleet.scan_text(f"app.listen({p})", wid)), (wid, other, p))


class Independence(unittest.TestCase):
    def test_clean_assignment(self):
        self.assertEqual(fleet.check_independence({"contract": "AG1", "seal": "AG6", "implement": ["AG2", "AG5"], "bounty": "AG4"}), [])

    def test_architects_cannot_implement(self):
        self.assertTrue(fleet.check_independence({"implement": ["AG1"]}))
        self.assertTrue(fleet.check_independence({"implement": "AG6"}))

    def test_bounty_hunter_cannot_implement_what_it_attacks(self):
        p = fleet.check_independence({"implement": ["AG4", "AG5"], "bounty": "AG4"})
        self.assertTrue(any("adversarial" in x or "bounty" in x for x in p), p)

    def test_wrong_authors(self):
        self.assertTrue(fleet.check_independence({"contract": "AG5"}))
        self.assertTrue(fleet.check_independence({"seal": "AG1"}))

    def test_unknown_and_duplicate_implementers(self):
        self.assertTrue(fleet.check_independence({"implement": ["AG42"]}))
        self.assertTrue(fleet.check_independence({"implement": ["AG5", "AG5"]}))

    def test_ag8_may_implement_its_own_scope_but_then_does_not_arbitrate(self):
        self.assertEqual(fleet.check_independence({"implement": ["AG8", "AG5"], "bounty": "AG4"}), [])  # arbiter falls back to AG6
        p = fleet.check_independence({"implement": ["AG8"], "arbiter": "AG8"})
        self.assertTrue(any("arbitrates" in x for x in p), p)

    def test_ag8_cannot_implement_what_it_audits_or_attacks(self):
        self.assertTrue(any("audits" in x for x in fleet.check_independence({"implement": ["AG8"], "audit": "AG8"})))
        self.assertTrue(any("audits" in x for x in fleet.check_independence({"implement": ["AG5", "AG8"], "audit": ["AG8"]})))
        self.assertTrue(any("attacks" in x or "bounty" in x for x in fleet.check_independence({"implement": ["AG8"], "bounty": "AG8"})))

    def test_ag8_default_arbiter_and_arbiter_choices(self):
        self.assertEqual(fleet.check_independence({"implement": ["AG5"], "bounty": "AG4", "arbiter": "AG8"}), [])
        self.assertEqual(fleet.check_independence({"implement": ["AG5"], "arbiter": "AG6"}), [])
        self.assertTrue(any("arbitrated by AG8" in x for x in fleet.check_independence({"implement": ["AG5"], "arbiter": "AG2"})))
        self.assertTrue(any("may not implement" in x for x in fleet.check_independence({"implement": ["AG6"]})))

    def test_declared_escalation_solver(self):
        ok = {"implement": ["AG8"], "escalation": {"AG8": "AG2"}, "bounty": "AG4"}
        self.assertEqual(fleet.check_independence(ok), [])
        self.assertTrue(fleet.check_independence({"implement": ["AG5"], "escalation": {"AG8": "AG2"}}))           # AG8 not implementing
        self.assertTrue(fleet.check_independence({"implement": ["AG8"], "escalation": {"AG8": "AG8"}}))          # cannot replace itself
        self.assertTrue(fleet.check_independence({"implement": ["AG8"], "escalation": {"AG8": "AG6"}}))          # AG6 owns no write scope
        self.assertTrue(fleet.check_independence({"implement": ["AG8"], "escalation": {"AG5": "AG2"}}))          # only AG8 solves

    def test_write_paths(self):
        self.assertIn("tests/acceptance/**", fleet.write_paths_for("AG6"))
        self.assertIn("contracts/**", fleet.write_paths_for("AG1"))
        self.assertEqual(fleet.write_paths_for("AG5"), ())
        self.assertEqual(fleet.write_paths_for("AG8"), ())


class Tiers(unittest.TestCase):
    def test_parse_lead_accepts_numbers_and_ids(self):
        for raw, want in [(1, "AG1"), ("6", "AG6"), ("AG8", "AG8"), ("ag1", "AG1"), (" 8 ", "AG8")]:
            self.assertEqual(fleet.parse_lead(raw), want)

    def test_parse_lead_rejects_non_architects(self):
        for bad in (2, "AG5", "9", "", "x", None):
            with self.assertRaises(fleet.FleetError, msg=repr(bad)):
                fleet.parse_lead(bad)

    def test_exactly_one_architect_is_premium_for_every_lead(self):
        for lead in ("AG1", "AG6", "AG8"):
            premium = [w for w in fleet.FLEET if fleet.effective_tier(w, lead=lead) == fleet.PREMIUM]
            self.assertEqual(premium, [lead])

    def test_chameleon_promotion_on_escalation(self):
        self.assertEqual(fleet.effective_tier("AG8", escalated=True), fleet.PREMIUM)
        self.assertEqual(fleet.effective_tier("AG6", escalated=True), fleet.PREMIUM)
        self.assertEqual(fleet.effective_tier("AG8"), fleet.STANDARD)

    def test_non_chameleons_are_never_promoted(self):
        for w in ("AG1", "AG2", "AG3", "AG4", "AG5", "AG7"):
            if w == "AG1":
                self.assertEqual(fleet.effective_tier(w, lead="AG6", escalated=True), fleet.STANDARD)
            else:
                self.assertEqual(fleet.effective_tier(w, escalated=True), fleet.STANDARD, w)

    def test_lead_rotation_demotes_ag1(self):
        self.assertEqual(fleet.effective_tier("AG1", lead="AG6"), fleet.STANDARD)
        self.assertEqual(fleet.effective_tier("AG6", lead="AG6"), fleet.PREMIUM)

    def test_tiers_are_names_not_models(self):
        self.assertEqual({fleet.effective_tier(w, lead=l) for w in fleet.FLEET for l in fleet.LEADS}, {"premium", "standard"})


class Cli(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = fleet.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_table(self):
        rc, out, _ = self.run_cli("table")
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(len(data["fleet"]), 8)
        self.assertEqual(data["table_problems"], [])

    def test_ports(self):
        rc, out, _ = self.run_cli("ports", "AG3", "T-1")
        self.assertEqual(rc, 0)
        self.assertTrue(3200 <= json.loads(out)["dev"] <= 3299)

    def test_ports_for_ag8(self):
        rc, out, _ = self.run_cli("ports", "AG8", "T-1")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(3700 <= data["dev"] <= 3799 and 8700 <= data["api"] <= 8799)

    def test_ports_for_an_unknown_worker_is_an_error(self):
        rc, _, err = self.run_cli("ports", "AG9", "T-1")
        self.assertEqual(rc, 2)
        self.assertIn("unknown worker", err)

    def test_scan_exit_code(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "s.js").write_text("app.listen(3000)\n", encoding="utf-8")
            rc, out, _ = self.run_cli("scan", "AG5", d, "s.js")
            self.assertEqual(rc, 1)
            self.assertEqual(json.loads(out)[0]["code"], "BIND_OUTSIDE_RANGE")
            (Path(d) / "s.js").write_text("app.listen(3400)\n", encoding="utf-8")
            self.assertEqual(self.run_cli("scan", "AG5", d, "s.js")[0], 0)

    def test_no_command_prints_help(self):
        self.assertEqual(self.run_cli()[0], 0)


if __name__ == "__main__":
    unittest.main()
