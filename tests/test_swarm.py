"""M5 tests: swarm.py - legacy-compatible dispatcher with leases, MASP ports and pluggable executors."""
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import swarm as sw  # noqa: E402
from core import fleet  # noqa: E402
from core.coordination.blackboard import Board  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.ws = self.dir / "ws"
        self.ws.mkdir()

    def task(self, worker="AG3", **kw):
        d = dict(worker=worker, prompt="do the thing", workspace=str(self.ws))
        d.update(kw)
        return sw.Task(**d)

    def swarm(self, executor=None, board=None, **kw):
        return sw.Swarm(executor, board=board, planner_dir=self.dir / ".planner", **kw)


class WorkerIds(unittest.TestCase):
    def test_accepted_forms(self):
        for raw, want in [(3, "AG3"), ("3", "AG3"), ("worker_3", "AG3"), ("Worker-3", "AG3"), ("AG3", "AG3"), ("ag7", "AG7"), (8, "AG8")]:
            self.assertEqual(sw.parse_worker_id(raw), want)

    def test_rejected_forms(self):
        for raw in (0, 9, "AG9", "AG", "x", "", True, None, "AG1; rm -rf /", 12):
            with self.assertRaises(sw.SwarmError, msg=repr(raw)):
                sw.parse_worker_id(raw)

    def test_legacy_key(self):
        self.assertEqual(sw.legacy_key("AG5"), "worker_5")


class TaskParsing(unittest.TestCase):
    def test_legacy_entry(self):
        t = sw.Task.from_legacy({"worker_id": 2, "prompt": "p", "workspace": "w", "model": "gemini-flash", "effort": "high", "timeout": 600})
        self.assertEqual((t.worker, t.model, t.effort, t.timeout), ("AG2", "gemini-flash", "high", 600.0))

    def test_errors(self):
        bad = [({"prompt": "p"}, "worker_id"), ({"worker_id": 1}, "prompt"), ({"worker_id": 1, "prompt": " "}, "prompt"),
               ({"worker_id": 1, "prompt": "p", "timeout": 0.1}, "timeout"), ({"worker_id": 1, "prompt": "p", "timeout": 99999}, "timeout"),
               ({"worker_id": 1, "prompt": "p", "model": "a b; rm"}, "model"), ({"worker_id": 1, "prompt": "p", "effort": "$(x)"}, "effort"),
               ({"worker_id": 1, "prompt": "p", "bogus": 1}, "unknown"), ({"worker_id": 1, "prompt": "p", "paths": "a"}, "paths"),
               ("nope", "object")]
        for d, word in bad:
            with self.assertRaises(sw.SwarmError, msg=str(d)) as cm:
                sw.Task.from_legacy(d)
            self.assertIn(word, str(cm.exception))

    def test_validate_rules(self):
        sw.Task("AG8", "p", ".").validate()                          # AG8 is an active worker: it may run on its own ports
        sw.Task("AG8", "p", ".", standby_for="AG2").validate()      # ...or as the escalation solver for another worker
        sw.Task("AG8", "p", ".", tier="premium").validate()
        with self.assertRaises(sw.SwarmError):
            sw.Task("AG8", "p", ".", tier="gemini-3").validate()    # a tier name, never a model name
        with self.assertRaises(sw.SwarmError):
            sw.Task("AG8", "p", ".", standby_for="AG6").validate()  # AG6 owns no write scope: nothing to take over
        with self.assertRaises(sw.SwarmError):
            sw.Task("AG2", "p", ".", standby_for="AG3").validate()
        with self.assertRaises(sw.SwarmError):
            sw.Task("AG8", "p", ".", standby_for="AG8").validate()
        with self.assertRaises(sw.SwarmError):
            sw.Task("AG2", "p", ".", paths=["a/**"]).validate()  # paths without ticket
        for bad in ("/etc/passwd", "../x", "C:/x", "a/../../b"):
            with self.assertRaises(ValueError, msg=bad):
                sw.Task("AG2", "p", ".", ticket="T-1", paths=[bad]).validate()


class Execution(Base):
    def test_dry_run_default_runs_nothing(self):
        (r,) = self.swarm().run([self.task()])
        self.assertEqual(r.status, sw.DRY)
        self.assertTrue(r.ok)
        self.assertIn("[dry-run]", r.output)
        self.assertTrue(3200 <= r.ports["DEV_PORT"] <= 3299 and 8200 <= r.ports["API_PORT"] <= 8299)

    def test_missing_workspace_is_rejected(self):
        (r,) = self.swarm().run([self.task(workspace=str(self.dir / "nope"))])
        self.assertEqual(r.status, sw.REJECTED)
        self.assertIn("workspace not found", r.stderr)

    def test_command_executor_environment_and_ports(self):
        os.environ["MY_SECRET_API_KEY"] = "hunter2hunter2"
        self.addCleanup(lambda: os.environ.pop("MY_SECRET_API_KEY", None))
        code = ("import os,json;print(json.dumps({k:os.environ.get(k) for k in "
                "['PLANNER_WORKER','PLANNER_TICKET','DEV_PORT','API_PORT','MASP_DEV_RANGE','MY_SECRET_API_KEY','PLANNER_SCOPE','PLANNER_MODEL_TIER']}))")
        ex = sw.CommandExecutor([sys.executable, "-c", code])
        (r,) = self.swarm(ex).run([self.task("AG5", ticket="T-9")])
        self.assertEqual(r.status, sw.OK, r.stderr)
        seen = json.loads(r.output)
        self.assertEqual(seen["PLANNER_WORKER"], "AG5")
        self.assertEqual(seen["PLANNER_SCOPE"], "worker_backend")
        self.assertEqual(seen["PLANNER_MODEL_TIER"], "standard")
        self.assertEqual(seen["MASP_DEV_RANGE"], "3400-3499")
        self.assertTrue(3400 <= int(seen["DEV_PORT"]) <= 3499)
        self.assertTrue(8400 <= int(seen["API_PORT"]) <= 8499)
        self.assertIsNone(seen["MY_SECRET_API_KEY"], "secrets must not leak into the agent environment")

    def test_pass_env_is_an_explicit_exception(self):
        os.environ["MY_SECRET_API_KEY"] = "hunter2hunter2"
        self.addCleanup(lambda: os.environ.pop("MY_SECRET_API_KEY", None))
        ex = sw.CommandExecutor([sys.executable, "-c", "import os;print(os.environ.get('MY_SECRET_API_KEY'))"], pass_env=["MY_SECRET_API_KEY"])
        (r,) = self.swarm(ex).run([self.task()])
        self.assertEqual(r.output.strip(), "hunter2hunter2")

    def test_prompt_goes_through_a_file_not_the_shell(self):
        prompt = "hello; rm -rf / $(whoami) `id` \"quoted\""
        code = "import sys;print(open(sys.argv[1], encoding='utf-8').read())"
        ex = sw.CommandExecutor([sys.executable, "-c", code, "{prompt_file}"])
        (r,) = self.swarm(ex).run([self.task(prompt=prompt)])
        self.assertEqual(r.output.strip(), prompt)

    def test_failed_exit_code_and_stderr(self):
        ex = sw.CommandExecutor([sys.executable, "-c", "import sys;sys.stderr.write('boom');sys.exit(3)"])
        (r,) = self.swarm(ex).run([self.task()])
        self.assertEqual((r.status, r.exit_code), (sw.FAILED, 3))
        self.assertIn("boom", r.stderr)

    def test_timeout_kills_the_process_tree(self):
        marker = self.dir / "grandchild.txt"
        grand = self.dir / "grand.py"
        grand.write_text(f"import time\ntime.sleep(6)\nopen({str(marker)!r}, 'w').write('alive')\n", encoding="utf-8")
        parent = self.dir / "parent.py"
        parent.write_text(f"import subprocess, sys, time\nsubprocess.Popen([sys.executable, {str(grand)!r}])\ntime.sleep(60)\n", encoding="utf-8")
        ex = sw.CommandExecutor([sys.executable, str(parent)])
        t0 = time.monotonic()
        (r,) = self.swarm(ex).run([self.task(timeout=2)])
        self.assertEqual(r.status, sw.TIMEOUT)
        self.assertLess(time.monotonic() - t0, 20)
        time.sleep(7)
        self.assertFalse(marker.exists(), "the grandchild survived the timeout")

    def test_missing_binary_is_an_error_not_a_crash(self):
        ex = sw.CommandExecutor(["definitely-not-a-real-binary-xyz"])
        (r,) = self.swarm(ex).run([self.task()])
        self.assertEqual(r.status, sw.ERROR)

    def test_known_placeholders_are_substituted_and_other_braces_are_left_alone(self):
        ex = sw.CommandExecutor([sys.executable, "-c", "d = {'a': 1}; print({worker}, {ticket})", "{workspace}", "{nope}"])
        argv = ex.argv(self.task("AG4", ticket="T-7"), sw.RunContext("task-1", self.dir, self.ws, {}), self.dir / "p.txt")
        self.assertEqual(argv[2], "d = {'a': 1}; print(AG4, T-7)")
        self.assertEqual(argv[3], str(self.ws))
        self.assertEqual(argv[4], "{nope}")

    def test_empty_template_is_refused(self):
        with self.assertRaises(sw.SwarmError):
            sw.CommandExecutor([])

    def test_callable_executor_exceptions_become_error_results(self):
        def boom(task, env, ctx):
            raise RuntimeError("agent crashed")
        (r,) = self.swarm(sw.CallableExecutor(boom)).run([self.task()])
        self.assertEqual(r.status, sw.ERROR)
        self.assertIn("agent crashed", r.stderr)

    def test_output_is_bounded(self):
        ex = sw.CallableExecutor(lambda t, e, c: "x" * (sw.MAX_OUTPUT * 3))
        (r,) = self.swarm(ex).run([self.task()])
        self.assertLess(len(r.output), sw.MAX_OUTPUT + 100)
        self.assertIn("omitted", r.output)

    def test_duplicate_task_ids(self):
        with self.assertRaises(sw.SwarmError):
            self.swarm().run([self.task(id="a"), self.task(id="a")])

    def test_standby_uses_the_replaced_workers_ports_and_marks_itself(self):
        seen = {}

        def fn(task, env, ctx):
            seen.update(env)
            return "ok"
        (r,) = self.swarm(sw.CallableExecutor(fn)).run([self.task("AG8", standby_for="AG2")])
        self.assertEqual(r.status, sw.OK)
        self.assertEqual(seen["PLANNER_WORKER"], "AG2")
        self.assertEqual(seen["PLANNER_STANDBY"], "AG8")
        self.assertTrue(3100 <= int(seen["DEV_PORT"]) <= 3199)

    def test_ag8_alone_uses_its_own_ports_and_the_given_tier(self):
        seen = {}

        def fn(task, env, ctx):
            seen.update(env)
            return "ok"
        (r,) = self.swarm(sw.CallableExecutor(fn)).run([self.task("AG8", tier="premium")])
        self.assertEqual(r.status, sw.OK)
        self.assertEqual(seen["PLANNER_WORKER"], "AG8")
        self.assertNotIn("PLANNER_STANDBY", seen)
        self.assertEqual(seen["PLANNER_MODEL_TIER"], "premium")
        self.assertEqual(seen["PLANNER_SCOPE"], "worker_security")
        self.assertEqual((seen["MASP_DEV_RANGE"], seen["MASP_API_RANGE"]), ("3700-3799", "8700-8799"))
        self.assertTrue(3700 <= int(seen["DEV_PORT"]) <= 3799 and 8700 <= int(seen["API_PORT"]) <= 8799)

    def test_ag8_alone_and_ag8_as_solver_do_not_share_ports_with_each_other(self):
        ports = {}

        def fn(task, env, ctx):
            ports[task.id] = int(env["DEV_PORT"])
            return "ok"
        res = self.swarm(sw.CallableExecutor(fn)).run([self.task("AG8", id="own", ticket="T-1"), self.task("AG2", id="db", ticket="T-1")])
        self.assertTrue(all(r.status == sw.OK for r in res))
        self.assertTrue(3700 <= ports["own"] <= 3799 and 3100 <= ports["db"] <= 3199)

    def test_legacy_spec_accepts_tier(self):
        t = sw.Task.from_legacy({"worker_id": 8, "prompt": "p", "tier": "premium"})
        self.assertEqual((t.worker, t.tier), ("AG8", "premium"))

    def test_one_worker_runs_one_task_at_a_time(self):
        spans, lock = [], threading.Lock()

        def fn(task, env, ctx):
            t0 = time.monotonic()
            time.sleep(0.3)
            with lock:
                spans.append((t0, time.monotonic()))
            return "ok"
        tasks = [self.task("AG3", id=f"t{i}", ticket=f"T-{i}") for i in range(3)]
        res = self.swarm(sw.CallableExecutor(fn), max_parallel=3).run(tasks)
        self.assertTrue(all(r.status == sw.OK for r in res))
        spans.sort()
        for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
            self.assertLessEqual(a1, b0 + 0.05)

    def test_different_workers_run_in_parallel_with_disjoint_ports(self):
        barrier = threading.Barrier(3, timeout=15)
        ports = []

        def fn(task, env, ctx):
            barrier.wait()
            ports.append(int(env["DEV_PORT"]))
            ports.append(int(env["API_PORT"]))
            return "ok"
        tasks = [self.task(w, id=w) for w in ("AG2", "AG3", "AG5")]
        res = self.swarm(sw.CallableExecutor(fn), max_parallel=3).run(tasks)
        self.assertTrue(all(r.status == sw.OK for r in res), [r.stderr for r in res])
        self.assertEqual(len(set(ports)), 6)

    def test_ports_are_deterministic_per_ticket_and_released(self):
        sz = self.swarm(sw.CallableExecutor(lambda t, e, c: "ok"))
        a = sz.run([self.task("AG3", ticket="T-1")])[0].ports
        b = sz.run([self.task("AG3", ticket="T-1")])[0].ports
        self.assertEqual(a, b)
        self.assertEqual(sz._ports_in_use["AG3"], set())


class Leases(Base):
    def setUp(self):
        super().setUp()
        self.board = Board(str(self.dir / "board.db"))
        self.addCleanup(self.board.close)

    def test_overlapping_paths_never_run_together(self):
        spans = []
        lock = threading.Lock()

        def fn(task, env, ctx):
            t0 = time.monotonic()
            time.sleep(0.4)
            with lock:
                spans.append((t0, time.monotonic()))
            return "ok"
        tasks = [self.task(w, id=f"a{i}", ticket=f"T-{i}", paths=["src/**"]) for i, w in enumerate(("AG5", "AG2", "AG7"))]
        res = self.swarm(sw.CallableExecutor(fn), self.board, max_parallel=3, claim_wait=20).run(tasks)
        self.assertTrue(all(r.status == sw.OK for r in res), [(r.status, r.stderr) for r in res])
        spans.sort()
        for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
            self.assertLessEqual(a1, b0 + 0.05, "two tasks with overlapping leases ran at the same time")

    def test_disjoint_paths_run_in_parallel(self):
        barrier = threading.Barrier(2, timeout=10)

        def fn(task, env, ctx):
            barrier.wait()
            return "ok"
        tasks = [self.task("AG5", id="a", ticket="T-1", paths=["src/a/**"]), self.task("AG2", id="b", ticket="T-2", paths=["db/**"])]
        res = self.swarm(sw.CallableExecutor(fn), self.board, max_parallel=2).run(tasks)
        self.assertTrue(all(r.status == sw.OK for r in res), [(r.status, r.stderr) for r in res])

    def test_conflict_with_a_live_lease_is_blocked(self):
        self.assertTrue(self.board.claim("AG2", "T-0", ["src/**"], ttl=60).ok)
        (r,) = self.swarm(sw.CallableExecutor(lambda t, e, c: "ok"), self.board, claim_wait=0.1).run([self.task("AG5", ticket="T-1", paths=["src/x.py"])])
        self.assertEqual(r.status, sw.BLOCKED)
        self.assertFalse(r.ok)

    def test_lease_is_released_and_fence_is_passed(self):
        seen = {}

        def fn(task, env, ctx):
            seen["fence"] = env.get("PLANNER_FENCE")
            seen["held"] = bool(self.board.leases("AG5"))
            return "ok"
        (r,) = self.swarm(sw.CallableExecutor(fn), self.board).run([self.task("AG5", ticket="T-1", paths=["src/**"])])
        self.assertEqual(r.status, sw.OK)
        self.assertTrue(seen["held"])
        self.assertGreater(int(seen["fence"]), 0)
        self.assertEqual(self.board.leases("AG5"), [])

    def test_lease_released_even_when_the_agent_crashes(self):
        def boom(task, env, ctx):
            raise RuntimeError("x")
        self.swarm(sw.CallableExecutor(boom), self.board).run([self.task("AG5", ticket="T-1", paths=["src/**"])])
        self.assertEqual(self.board.leases("AG5"), [])

    def test_lost_lease_invalidates_the_result(self):
        def fn(task, env, ctx):
            time.sleep(0.3)
            self.board.force_release("AG5", by="TEST", reason="steal")
            time.sleep(2.6)
            return "ok"
        (r,) = self.swarm(sw.CallableExecutor(fn), self.board).run([self.task("AG5", ticket="T-1", paths=["src/**"], lease_ttl=3)])
        self.assertEqual(r.status, sw.LEASE_LOST)

    def test_paths_without_a_board_are_noted_not_silently_leased(self):
        (r,) = self.swarm(sw.CallableExecutor(lambda t, e, c: "ok")).run([self.task("AG5", ticket="T-1", paths=["src/**"])])
        self.assertEqual(r.status, sw.OK)
        self.assertIn("not leased", r.note)


class LegacyExecutor(Base):
    def script(self, body):
        p = self.dir / "legacy_swarm.py"
        p.write_text(body, encoding="utf-8")
        return str(p)

    def test_delegates_and_maps_status(self):
        s = self.script("import json,sys\nspec=json.load(open(sys.argv[3]))\n"
                        "print('noise before')\nprint(json.dumps({'orchestrator_summary':'x','worker_results':{'worker_%d'%spec[0]['worker_id']:"
                        "{'status':'completed','duration_seconds':1,'output':'legacy says hi','stderr':''}}}))\n")
        (r,) = self.swarm(sw.LegacyExecutor(s)).run([self.task("AG4")])
        self.assertEqual(r.status, sw.OK, r.stderr)
        self.assertEqual(r.output, "legacy says hi")

    def test_failure_and_garbage(self):
        s = self.script("import json\nprint(json.dumps({'worker_results':{'worker_4':{'status':'failed','output':'','stderr':'bad'}}}))\n")
        (r,) = self.swarm(sw.LegacyExecutor(s)).run([self.task("AG4")])
        self.assertEqual(r.status, sw.FAILED)
        s2 = self.script("print('not json')\n")
        (r2,) = self.swarm(sw.LegacyExecutor(s2)).run([self.task("AG4")])
        self.assertEqual(r2.status, sw.ERROR)

    def test_missing_script(self):
        (r,) = self.swarm(sw.LegacyExecutor(str(self.dir / "nope.py"))).run([self.task("AG4")])
        self.assertEqual(r.status, sw.ERROR)
        with self.assertRaises(sw.SwarmError):
            os.environ.pop("PLANNER_LEGACY_SWARM", None)
            sw.LegacyExecutor()


class Cli(Base):
    def call(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sw.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def spec(self, tasks):
        p = self.dir / "tasks.json"
        p.write_text(json.dumps(tasks), encoding="utf-8")
        return str(p)

    def test_legacy_contract_output_shape(self):
        spec = self.spec([{"worker_id": 3, "prompt": "p", "workspace": str(self.ws), "model": "m", "effort": "high", "timeout": 60},
                          {"worker_id": 5, "prompt": "q", "workspace": str(self.ws)}])
        code, out, _ = self.call("parallel", "--spec", spec, "--planner-dir", str(self.dir / ".planner"))
        self.assertEqual(code, 0)
        rep = json.loads(out)
        self.assertEqual(set(rep), {"orchestrator_summary", "worker_results", "schema", "executor", "tasks"})
        self.assertEqual(set(rep["worker_results"]), {"worker_3", "worker_5"})
        for wr in rep["worker_results"].values():
            self.assertEqual(set(wr), {"status", "duration_seconds", "output", "stderr"})
        self.assertIn("2 dry_run", rep["orchestrator_summary"])

    def test_duplicate_worker_keys_do_not_overwrite(self):
        spec = self.spec([{"worker_id": 3, "prompt": "p", "workspace": str(self.ws)}, {"worker_id": 3, "prompt": "q", "workspace": str(self.ws)}])
        rep = json.loads(self.call("parallel", "--spec", spec)[1])
        self.assertEqual(set(rep["worker_results"]), {"worker_3", "worker_3.2"})

    def test_command_executor_via_cli_and_exit_code(self):
        spec = self.spec([{"worker_id": 2, "prompt": "p", "workspace": str(self.ws)}])
        code, out, _ = self.call("parallel", "--spec", spec, "--executor", "command", "--cmd", f"{sys.executable} -c \"import sys; sys.exit(4)\"")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["worker_results"]["worker_2"]["status"], "failed")

    def test_usage_errors_exit_2(self):
        self.assertEqual(self.call("parallel", "--spec", str(self.dir / "missing.json"))[0], 2)
        self.assertEqual(self.call("parallel", "--spec", self.spec([{"worker_id": 9, "prompt": "p"}]))[0], 2)
        self.assertEqual(self.call("parallel", "--spec", self.spec([]))[0], 2)
        self.assertEqual(self.call("parallel", "--spec", self.spec([{"worker_id": 1, "prompt": "p"}]), "--executor", "command")[0], 2)

    def test_fleet_and_ports_subcommands(self):
        code, out, _ = self.call("fleet")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual([w["id"] for w in data["fleet"]], ["AG1", "AG6", "AG2", "AG3", "AG4", "AG5", "AG7", "AG8"])
        self.assertEqual(data["table_problems"], [])
        code, out, _ = self.call("ports", "worker_7", "T-1")
        self.assertTrue(3600 <= json.loads(out)["dev"] <= 3699)
        code, out, _ = self.call("ports", "AG8", "T-1")
        self.assertEqual(code, 0)
        p8 = json.loads(out)
        self.assertTrue(3700 <= p8["dev"] <= 3799 and 8700 <= p8["api"] <= 8799)
        self.assertEqual(self.call("ports", "AG9", "T-1")[0], 2)

    def test_help_exits_zero(self):
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
            sw.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(self.call()[0], 0)

    def test_out_file(self):
        spec = self.spec([{"worker_id": 1, "prompt": "p", "workspace": str(self.ws)}])
        out_file = self.dir / "report.json"
        self.call("parallel", "--spec", spec, "--out", str(out_file))
        self.assertIn("worker_1", json.loads(out_file.read_text())["worker_results"])


if __name__ == "__main__":
    unittest.main()
