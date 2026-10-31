"""core/antigravity_bridge.py: profile mapping per OS, sanitized MASP environment, headless launches, swarm executor, CLI."""
from __future__ import annotations

import contextlib
import io
import json
import ntpath
import os
import posixpath
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import swarm as sw  # noqa: E402
from core import antigravity_bridge as br  # noqa: E402
from core import fleet  # noqa: E402

WIN_ENV = {"USERPROFILE": r"C:\Users\lenovo"}
LIN_ENV = {"HOME": "/home/dapp"}
DUMP = ("import os, json; keys = ['PLANNER_WORKER','PLANNER_TICKET','PLANNER_MODEL_TIER','PLANNER_SCOPE','PLANNER_STANDBY','DEV_PORT','API_PORT','PORT',"
        "'MASP_DEV_RANGE','MASP_API_RANGE','HOME','PLANNER_PROFILE_DIR','PLANNER_PROFILE_KIND','PLANNER_ACCOUNT','OPENAI_API_KEY','GITHUB_TOKEN','MY_AGENT_LOGIN'];"
        "print(json.dumps({k: os.environ.get(k) for k in keys}))")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.ws = Path(self.tmp.name) / "ws"
        self.ws.mkdir()

    def make_profiles(self, workers=range(2, 9)):
        for n in workers:
            (self.home / "AntigravityProfiles" / f"Account{n}").mkdir(parents=True, exist_ok=True)


class Detect(unittest.TestCase):
    def test_detect_os(self):
        for name, want in [("Windows", br.WINDOWS), ("windows", br.WINDOWS), ("CYGWIN_NT-10.0", br.WINDOWS), ("MSYS_NT", br.WINDOWS),
                           ("Linux", br.LINUX), ("", br.LINUX), ("Darwin", br.MACOS), ("FreeBSD", br.LINUX)]:
            self.assertEqual(br.detect_os(name), want, name)

    def test_detect_os_uses_platform_system_by_default(self):
        import platform
        self.assertEqual(br.detect_os(), br.detect_os(platform.system()))

    def test_wsl_detection(self):
        self.assertTrue(br.detect_wsl("Linux version 5.15.153.1-microsoft-standard-WSL2", {}))
        self.assertTrue(br.detect_wsl("whatever", {"WSL_DISTRO_NAME": "Ubuntu"}))
        self.assertFalse(br.detect_wsl("Linux version 6.8.0-generic (buildd@lcy02)", {}))


class Mapping(Base):
    def test_windows_layout(self):
        t = br.profile_table(os_name=br.WINDOWS, env=WIN_ENV)
        self.assertEqual(t["AG1"].path, ntpath.join(r"C:\Users\lenovo", "AppData", "Roaming", "Antigravity"))
        for n in range(2, 9):
            self.assertEqual(t[f"AG{n}"].path, ntpath.join(r"C:\Users\lenovo", "AppData", "Roaming", f"Antigravity{n}"))
        self.assertTrue(t["AG1"].path.endswith(r"AppData\Roaming\Antigravity"))
        self.assertTrue(t["AG8"].path.endswith(r"AppData\Roaming\Antigravity8"))
        self.assertTrue(all(not p.home_override for p in t.values()), "on Windows the profile is not a home directory")
        self.assertEqual((t["AG1"].kind, t["AG5"].kind), (br.DEFAULT_KIND, br.ACCOUNT_KIND))

    def test_linux_layout(self):
        t = br.profile_table(os_name=br.LINUX, env=LIN_ENV)
        self.assertEqual(t["AG1"].path, "/home/dapp")
        self.assertFalse(t["AG1"].home_override)
        for n in range(2, 9):
            self.assertEqual(t[f"AG{n}"].path, f"/home/dapp/AntigravityProfiles/Account{n}")
            self.assertTrue(t[f"AG{n}"].home_override)
        self.assertEqual(br.default_config_dir(t["AG1"]), "/home/dapp/.gemini/antigravity")
        self.assertIsNone(br.default_config_dir(t["AG2"]))
        self.assertIsNone(br.default_config_dir(br.profile_for("AG1", os_name=br.WINDOWS, env=WIN_ENV)))

    def test_macos_follows_the_linux_layout(self):
        self.assertEqual(br.profile_for("AG3", os_name=br.MACOS, env={"HOME": "/Users/x"}).path, "/Users/x/AntigravityProfiles/Account3")

    def test_every_profile_is_distinct(self):
        for os_name, env in ((br.WINDOWS, WIN_ENV), (br.LINUX, LIN_ENV)):
            paths = [p.path for p in br.profile_table(os_name=os_name, env=env).values()]
            self.assertEqual(len(paths), 8)
            self.assertEqual(len(set(paths)), 8)

    def test_worker_forms_and_errors(self):
        for raw in (3, "3", "AG3", "ag3"):
            self.assertEqual(br.profile_for(raw, os_name=br.LINUX, env=LIN_ENV).worker, "AG3")
        for bad in (0, 9, "AG9", "x", "", "AG", None, "AG1; rm"):
            with self.assertRaises(br.BridgeError, msg=repr(bad)):
                br.profile_for(bad, os_name=br.LINUX, env=LIN_ENV)

    def test_home_resolution_order(self):
        env = {"HOME": "/home/a", br.ENV_HOME: "/srv/b"}
        self.assertEqual(br.profile_for("AG2", os_name=br.LINUX, env=env).path, "/srv/b/AntigravityProfiles/Account2")
        self.assertEqual(br.profile_for("AG2", os_name=br.LINUX, env=env, home="/x").path, "/x/AntigravityProfiles/Account2")
        self.assertEqual(br.profile_for("AG1", os_name=br.WINDOWS, env={"HOMEDRIVE": "D:", "HOMEPATH": "\\Users\\q"}).path,
                         ntpath.join("D:\\Users\\q", "AppData", "Roaming", "Antigravity"))
        self.assertEqual(br.profile_for("AG1", os_name=br.LINUX, env={}).path, str(Path.home()))

    def test_profile_root_override(self):
        env = dict(LIN_ENV, **{br.ENV_ROOT: "/mnt/data/profiles"})
        self.assertEqual(br.profile_for("AG4", os_name=br.LINUX, env=env).path, "/mnt/data/profiles/Account4")
        wenv = dict(WIN_ENV, **{br.ENV_ROOT: r"D:\agents"})
        self.assertEqual(br.profile_for("AG4", os_name=br.WINDOWS, env=wenv).path, ntpath.join(r"D:\agents", "Antigravity4"))

    def test_no_user_name_is_hard_coded(self):
        text = (ROOT / "core" / "antigravity_bridge.py").read_text(encoding="utf-8")
        for needle in ("dapp", "lenovo", "@gmail"):
            self.assertNotIn(needle, text)

    def test_probe_and_missing_use_the_real_disk(self):
        t = br.profile_table(os_name=br.LINUX, env={"HOME": str(self.home)})
        self.assertEqual(list(t), [f"AG{n}" for n in range(1, 9)])
        self.assertEqual(br.missing_profiles(table=t), ["AG2", "AG3", "AG4", "AG5", "AG6", "AG7", "AG8"])
        self.make_profiles([2, 3])
        self.assertEqual(br.missing_profiles(table=t), ["AG4", "AG5", "AG6", "AG7", "AG8"])
        self.assertEqual(br.missing_profiles(["AG2", "AG3"], table=t), [])
        probe = br.probe_profiles(t)
        self.assertTrue(probe["AG1"] and probe["AG2"] and not probe["AG8"])

    def test_profile_env(self):
        acc = br.profile_for("AG6", os_name=br.LINUX, env=LIN_ENV)
        e = br.profile_env(acc)
        self.assertEqual((e["HOME"], e["PLANNER_PROFILE_DIR"], e["PLANNER_ACCOUNT"], e["PLANNER_PROFILE_KIND"]),
                         ("/home/dapp/AntigravityProfiles/Account6", "/home/dapp/AntigravityProfiles/Account6", "AG6", "account"))
        lead = br.profile_env(br.profile_for("AG1", os_name=br.LINUX, env=LIN_ENV))
        self.assertNotIn("HOME", lead, "AG1 keeps the real home")
        win = br.profile_env(br.profile_for("AG2", os_name=br.WINDOWS, env=WIN_ENV))
        self.assertNotIn("HOME", win)
        self.assertTrue(win["PLANNER_PROFILE_DIR"].endswith("Antigravity2"))


class Environment(Base):
    SECRETS = {"PATH": os.environ.get("PATH", ""), "HOME": "/real/home", "OPENAI_API_KEY": "sk-secret", "GITHUB_TOKEN": "ghp_secret",
               "MY_AGENT_LOGIN": "login-value", "RANDOM_VAR": "x"}

    def env(self, worker="AG3", **kw):
        kw.setdefault("os_name", br.LINUX)
        kw.setdefault("base_env", self.SECRETS)
        return br.sanitized_env(worker, "T-1", **kw)

    def test_required_variables_for_every_worker(self):
        for w in fleet.FLEET:
            e = self.env(w)
            for k in br.REQUIRED_ENV:
                self.assertIn(k, e, (w, k))
            self.assertEqual(e["PLANNER_WORKER"], w)
            dev, api = fleet.FLEET[w].dev, fleet.FLEET[w].api
            self.assertTrue(dev[0] <= int(e["DEV_PORT"]) <= dev[1], w)
            self.assertTrue(api[0] <= int(e["API_PORT"]) <= api[1], w)
            self.assertEqual(e["MASP_DEV_RANGE"], f"{dev[0]}-{dev[1]}")
            self.assertEqual(e["MASP_API_RANGE"], f"{api[0]}-{api[1]}")

    def test_secrets_and_unknown_variables_are_dropped(self):
        e = self.env()
        for k in ("OPENAI_API_KEY", "GITHUB_TOKEN", "MY_AGENT_LOGIN", "RANDOM_VAR"):
            self.assertNotIn(k, e)
        self.assertIn("PATH", e)

    def test_pass_env_is_an_explicit_decision(self):
        e = self.env(pass_env=["MY_AGENT_LOGIN", "NOT_SET_ANYWHERE"])
        self.assertEqual(e["MY_AGENT_LOGIN"], "login-value")
        self.assertNotIn("NOT_SET_ANYWHERE", e)
        self.assertNotIn("OPENAI_API_KEY", e)

    def test_account_profile_sets_home_and_lead_keeps_it(self):
        self.assertEqual(self.env("AG3", home="/h")["HOME"], "/h/AntigravityProfiles/Account3")
        self.assertEqual(self.env("AG1", home="/h")["HOME"], "/real/home")

    def test_windows_does_not_touch_home(self):
        e = self.env("AG3", os_name=br.WINDOWS, base_env={"USERPROFILE": r"C:\Users\u", "HOME": "keep"})
        self.assertEqual(e["HOME"], "keep")
        self.assertTrue(e["PLANNER_PROFILE_DIR"].endswith("Antigravity3"))

    def test_escalation_solver_runs_in_ag8s_profile_with_the_replaced_workers_scope_and_ports(self):
        e = self.env("AG8", standby_for="AG2", home="/h", tier="premium")
        self.assertEqual(e["PLANNER_WORKER"], "AG2")
        self.assertEqual(e["PLANNER_STANDBY"], "AG8")
        self.assertEqual(e["PLANNER_ACCOUNT"], "AG8")
        self.assertEqual(e["HOME"], "/h/AntigravityProfiles/Account8")
        self.assertTrue(3100 <= int(e["DEV_PORT"]) <= 3199)
        self.assertEqual(e["PLANNER_MODEL_TIER"], "premium")
        self.assertEqual(e["PLANNER_SCOPE"], "worker_db")

    def test_extra_wins_last(self):
        self.assertEqual(self.env(extra={"PLANNER_TICKET": "T-override", "X": 1})["X"], "1")

    def test_same_ticket_never_shares_a_port_across_workers(self):
        ports = []
        for w in fleet.FLEET:
            e = self.env(w)
            ports += [int(e["DEV_PORT"]), int(e["API_PORT"])]
        self.assertEqual(len(set(ports)), 16)

    def test_portless_worker_is_refused(self):
        legacy = fleet.Worker("AG9", "Reserve", "r", fleet.STANDBY, None, None, None)
        from unittest import mock
        with mock.patch.dict(fleet.FLEET, {"AG9": legacy}):
            with self.assertRaises(br.BridgeError):
                br.sanitized_env("AG9", "T", profile=br.profile_for("AG2", os_name=br.LINUX, env=LIN_ENV), base_env=self.SECRETS)


class Expand(unittest.TestCase):
    def test_known_placeholders_only(self):
        out = br.expand(["agent", "--dir", "{profile_dir}", "--json", '{"a": {"b": 1}}', "{unknown}", "{worker}"], {"profile_dir": "/p", "worker": "AG3"})
        self.assertEqual(out, ["agent", "--dir", "/p", "--json", '{"a": {"b": 1}}', "{unknown}", "AG3"])

    def test_split_template(self):
        self.assertEqual(br.split_template(["a", "b c"]), ["a", "b c"])
        self.assertEqual(br.split_template("agent --x 'a b'"), ["agent", "--x", "a b"])


class Launch(Base):
    def launch(self, worker="AG3", cmd=None, **kw):
        cmd = cmd or [sys.executable, "-I", "-c", DUMP]
        kw.setdefault("home", str(self.home))
        kw.setdefault("os_name", br.LINUX)
        kw.setdefault("base_env", dict(os.environ, OPENAI_API_KEY="sk-secret", GITHUB_TOKEN="ghp_secret", MY_AGENT_LOGIN="login-value"))
        kw.setdefault("timeout", 60)
        return br.launch_headless(worker, cmd, prompt="do the thing", workspace=self.ws, ticket="T-77", **kw)

    def test_runs_inside_the_account_profile_with_a_clean_env(self):
        self.make_profiles()
        r = self.launch("AG3")
        self.assertTrue(r.ok, r.to_dict())
        got = json.loads(r.stdout)
        self.assertEqual(got["PLANNER_WORKER"], "AG3")
        self.assertEqual(got["HOME"], str(self.home / "AntigravityProfiles" / "Account3").replace("\\", "/"))
        self.assertTrue(3200 <= int(got["DEV_PORT"]) <= 3299 and 8200 <= int(got["API_PORT"]) <= 8299)
        self.assertEqual(got["MASP_DEV_RANGE"], "3200-3299")
        self.assertIsNone(got["OPENAI_API_KEY"])
        self.assertIsNone(got["GITHUB_TOKEN"])
        self.assertIsNone(got["MY_AGENT_LOGIN"])

    def test_pass_env_reaches_the_process(self):
        self.make_profiles()
        got = json.loads(self.launch("AG4", pass_env=["MY_AGENT_LOGIN"]).stdout)
        self.assertEqual(got["MY_AGENT_LOGIN"], "login-value")
        self.assertIsNone(got["OPENAI_API_KEY"])

    def test_placeholders_and_prompt_file(self):
        self.make_profiles()
        code = "import sys; print(open(sys.argv[1], encoding='utf-8').read()); print(sys.argv[2]); print(sys.argv[3])"
        r = self.launch("AG5", [sys.executable, "-I", "-c", code, "{prompt_file}", "{profile_dir}", "{account}"])
        self.assertTrue(r.ok, r.to_dict())
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "do the thing")
        self.assertTrue(lines[1].replace("\\", "/").endswith("AntigravityProfiles/Account5"))
        self.assertEqual(lines[2], "AG5")

    def test_missing_profile_stops_before_anything_runs(self):
        r = self.launch("AG6")
        self.assertFalse(r.ok)
        self.assertEqual(r.status, "no_profile")
        self.assertIn("does not exist", r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertFalse((self.ws / ".planner_prompts").exists())

    def test_missing_profile_can_be_waived_explicitly(self):
        self.assertTrue(self.launch("AG6", require_profile=False).ok)

    def test_failure_and_timeout_statuses(self):
        self.make_profiles()
        r = self.launch("AG2", [sys.executable, "-I", "-c", "import sys; sys.exit(3)"])
        self.assertEqual((r.status, r.returncode, r.ok), ("failed", 3, False))
        r = self.launch("AG2", [sys.executable, "-I", "-c", "import time; time.sleep(30)"], timeout=1)
        self.assertEqual(r.status, "timeout")
        self.assertIn("process tree killed", r.stderr)

    def test_unlaunchable_command_is_an_error_not_a_crash(self):
        self.make_profiles()
        r = self.launch("AG2", ["definitely-not-an-installed-binary-xyz"])
        self.assertEqual(r.status, "error")

    def test_empty_command_raises(self):
        with self.assertRaises(br.BridgeError):
            br.launch_headless("AG2", [], prompt="p", workspace=self.ws, home=str(self.home), os_name=br.LINUX)

    def test_stdin_prompt(self):
        self.make_profiles()
        r = self.launch("AG2", [sys.executable, "-I", "-c", "import sys; print(sys.stdin.read().upper())"], stdin_prompt=True)
        self.assertIn("DO THE THING", r.stdout)

    def test_ag8_solver_launch(self):
        self.make_profiles()
        got = json.loads(self.launch("AG8", standby_for="AG2", tier="premium").stdout)
        self.assertEqual((got["PLANNER_WORKER"], got["PLANNER_STANDBY"], got["PLANNER_ACCOUNT"]), ("AG2", "AG8", "AG8"))
        self.assertTrue(got["HOME"].replace("\\", "/").endswith("Account8"))
        self.assertTrue(3100 <= int(got["DEV_PORT"]) <= 3199)

    def test_ag1_runs_in_the_real_home(self):
        got = json.loads(self.launch("AG1", base_env=dict(os.environ, HOME=str(self.home))).stdout)
        self.assertEqual(got["HOME"], str(self.home))
        self.assertEqual(got["PLANNER_PROFILE_KIND"], "default")


class SwarmExecutor(Base):
    def swarm(self, executor):
        return sw.Swarm(executor, planner_dir=Path(self.tmp.name) / ".planner")

    def executor(self, template=None, **kw):
        cls = br.BridgeExecutor
        return cls(template or [sys.executable, "-I", "-c", DUMP + "; print('PROFILE=' + __import__('sys').argv[1])", "{profile_dir}"], os_name=br.LINUX,
                   home=str(self.home), base_env=dict(os.environ, MY_AGENT_LOGIN="login-value"), **kw)

    def task(self, worker, **kw):
        return sw.Task(worker=worker, prompt="p", workspace=str(self.ws), timeout=60, **kw)

    def test_each_task_runs_under_its_own_workers_profile(self):
        self.make_profiles()
        res = self.swarm(self.executor()).run([self.task("AG2", id="a"), self.task("AG5", id="b"), self.task("AG8", id="c")])
        self.assertEqual([r.status for r in res], [sw.OK] * 3)
        for r, n in zip(res, (2, 5, 8)):
            got = json.loads(r.output.splitlines()[0])
            self.assertTrue(got["HOME"].replace("\\", "/").endswith(f"Account{n}"), got)
            self.assertTrue(r.output.splitlines()[1].replace("\\", "/").endswith(f"Account{n}"))
            self.assertEqual(got["PLANNER_WORKER"], f"AG{n}")

    def test_solver_uses_the_replaced_workers_ports_and_its_own_account(self):
        self.make_profiles()
        (r,) = self.swarm(self.executor()).run([self.task("AG8", standby_for="AG2", tier="premium")])
        got = json.loads(r.output.splitlines()[0])
        self.assertEqual((got["PLANNER_WORKER"], got["PLANNER_STANDBY"], got["PLANNER_ACCOUNT"], got["PLANNER_MODEL_TIER"]), ("AG2", "AG8", "AG8", "premium"))
        self.assertTrue(3100 <= int(got["DEV_PORT"]) <= 3199)

    def test_missing_profile_is_a_swarm_error_result(self):
        (r,) = self.swarm(self.executor()).run([self.task("AG4")])
        self.assertEqual(r.status, sw.ERROR)
        self.assertIn("does not exist", r.stderr)

    def test_secrets_stay_out_unless_passed(self):
        self.make_profiles()
        (r,) = self.swarm(self.executor()).run([self.task("AG3")])
        self.assertIsNone(json.loads(r.output.splitlines()[0])["MY_AGENT_LOGIN"])
        (r,) = self.swarm(self.executor(pass_env=["MY_AGENT_LOGIN"])).run([self.task("AG3", id="again")])
        self.assertEqual(json.loads(r.output.splitlines()[0])["MY_AGENT_LOGIN"], "login-value")

    def test_build_executor_antigravity(self):
        with self.assertRaises(sw.SwarmError):
            sw.build_executor("antigravity")
        ex = sw.build_executor("antigravity", "agent --dir {profile_dir}")
        self.assertEqual(ex.name, "antigravity")
        self.assertIsInstance(ex, sw.CommandExecutor)

    def test_command_executor_still_leaves_unknown_braces_alone(self):
        ex = sw.CommandExecutor(["x", '{"k": {"n": 1}}', "{worker}", "{nothing}"])
        ctx = sw.RunContext("t", Path(self.tmp.name), self.ws, {})
        argv = ex.argv(sw.Task("AG2", "p", str(self.ws), ticket="T"), ctx, Path("pf.txt"))
        self.assertEqual(argv, ["x", '{"k": {"n": 1}}', "AG2", "{nothing}"])

    def test_lazy_attribute_is_cached_and_unknown_names_raise(self):
        self.assertIs(br.BridgeExecutor, br.BridgeExecutor)
        with self.assertRaises(AttributeError):
            br.NotAThing


class Cli(Base):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = br.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_profiles_for_windows_from_linux(self):
        rc, out, _ = self.run_cli("--os", "windows", "--home", r"C:\Users\someone", "profiles")
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["os"], "windows")
        self.assertTrue(data["profiles"]["AG1"]["path"].endswith(r"AppData\Roaming\Antigravity"))
        self.assertTrue(data["profiles"]["AG7"]["path"].endswith("Antigravity7"))

    def test_check_exit_codes(self):
        rc, out, _ = self.run_cli("--os", "linux", "--home", str(self.home), "check")
        self.assertEqual(rc, 1)
        self.assertIn("AG2", json.loads(out)["missing"])
        self.make_profiles()
        rc, out, _ = self.run_cli("--os", "linux", "--home", str(self.home), "check")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["missing"], [])

    def test_env_prints_only_planner_variables(self):
        rc, out, _ = self.run_cli("--os", "linux", "--home", "/h", "env", "--worker", "AG8", "--standby-for", "AG5", "--ticket", "T-5")
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual((data["PLANNER_WORKER"], data["PLANNER_STANDBY"]), ("AG5", "AG8"))
        self.assertTrue(3400 <= int(data["DEV_PORT"]) <= 3499)
        self.assertTrue(all(k.startswith(("PLANNER_", "MASP_")) or k in ("DEV_PORT", "API_PORT", "PORT", "HOME") for k in data))

    def test_bad_worker_is_exit_2(self):
        rc, _, err = self.run_cli("env", "--worker", "AG12")
        self.assertEqual(rc, 2)
        self.assertIn("unknown worker", err)

    def test_launch(self):
        self.make_profiles()
        rc, out, _ = self.run_cli("--os", "linux", "--home", str(self.home), "launch", "--worker", "AG2", "--cmd",
                                  f'"{sys.executable}" -I -c "print(42)"', "--prompt", "hi", "--workspace", str(self.ws), "--timeout", "60")
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(out)["stdout"].strip(), "42")
        rc, _, err = self.run_cli("launch", "--worker", "AG2", "--cmd", "x", "--prompt", " ")
        self.assertEqual(rc, 2)
        rc, out, _ = self.run_cli("--os", "linux", "--home", str(self.home / "none"), "launch", "--worker", "AG2", "--cmd", "x", "--prompt", "hi",
                                  "--workspace", str(self.ws))
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out)["status"], "no_profile")

    def test_no_command_prints_help(self):
        self.assertEqual(self.run_cli()[0], 0)


if __name__ == "__main__":
    unittest.main()
