"""skills/planner-4-engine: SKILL.md stays true to the fleet table, and the planner4 / planner4.bat entry points forward correctly."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import fleet  # noqa: E402

SKILL = ROOT / "skills" / "planner-4-engine"
LAUNCHER = SKILL / "bin" / "planner4"
BAT = SKILL / "bin" / "planner4.bat"


def load_launcher():
    loader = importlib.machinery.SourceFileLoader("planner4_launcher", str(LAUNCHER))
    spec = importlib.util.spec_from_loader("planner4_launcher", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def run(*args, env=None, launcher=LAUNCHER, cwd=None):
    e = dict(os.environ)
    e.pop("PLANNER4_HOME", None)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(launcher), *args], capture_output=True, text=True, env=e, cwd=cwd, timeout=120)


class SkillFile(unittest.TestCase):
    def setUp(self):
        self.text = (SKILL / "SKILL.md").read_text(encoding="utf-8")

    def test_frontmatter(self):
        m = re.match(r"---\n(.*?)\n---\n", self.text, re.S)
        self.assertIsNotNone(m)
        fm = dict(re.findall(r"^(\w+):\s*(.+)$", m.group(1), re.M))
        self.assertEqual(fm["name"], "planner-4-engine")
        self.assertEqual(fm["description"], "Contract-First, Bicameral 8-Worker Multi-Agent Planning Engine 4.0 with Sealed Oracles, AST Mutation Gates, APCA Kinematics, and SQLite WAL Blackboard")
        self.assertEqual(set(fm), {"name", "description"})

    def test_fleet_table_matches_the_code(self):
        rows = re.findall(r"^\|\s*(?:\*\*)?(AG\d)(?:\*\*)?[^|]*\|[^|]*\|[^|]*\|\s*(\d{4})-(\d{4})\s*\|\s*(\d{4})-(\d{4})\s*\|", self.text, re.M)
        self.assertEqual([r[0] for r in rows], [f"AG{i}" for i in range(1, 9)])
        for wid, d0, d1, a0, a1 in rows:
            w = fleet.FLEET[wid]
            self.assertEqual((w.dev, w.api), ((int(d0), int(d1)), (int(a0), int(a1))), wid)

    def test_scopes_in_the_table_match_the_code(self):
        for wid, w in fleet.FLEET.items():
            if w.scope:
                row = re.search(rf"^\|[^\n]*\b{wid}\b[^\n]*`{w.scope}`", self.text, re.M)
                self.assertIsNotNone(row, f"{wid} -> {w.scope}")

    def test_mentions_the_things_an_operator_needs(self):
        for needle in ("--lead 1|6|8", "--standby", "--standby-after", "Argon Titans", "AG1", "AG6", "AG8", "3000-3799", "8000-8799", "MASP",
                       "planner4 doctor", "PLANNER4_HOME", "PLANNER_LAWGIVER_KEY", "PLANNER_PROFILE_ROOT", "does not know Antigravity's command-line flags",
                       "AppData\\Roaming\\Antigravity", "AntigravityProfiles/Account<N>", "--model-premium", "INCONCLUSIVE", "0.92"):
            self.assertIn(needle, self.text, needle)

    def test_no_secrets_no_personal_data_no_model_names(self):
        for bad in ("@gmail", "sk-", "ghp_", "lenovo", "dapp", "gemini-", "Gemini 3", "Gemini 2"):
            self.assertNotIn(bad, self.text, bad)

    def test_every_documented_subcommand_exists(self):
        mod = load_launcher()
        known = set(mod.ROUTES) | {"test", "doctor", "where"}
        used = set(re.findall(r"`planner4 ([a-z]+)", self.text))
        self.assertTrue(used)
        self.assertTrue(used <= known, used - known)

    def test_documented_flags_exist_in_the_engine(self):
        out = subprocess.run([sys.executable, str(ROOT / "orchestrator.py"), "run", "--help"], capture_output=True, text=True, timeout=60).stdout
        for flag in ("--lead", "--standby", "--standby-after", "--model-premium", "--model-standard", "--pass-env", "--max-attempts", "--executor"):
            self.assertIn(flag, out, flag)
        self.assertIn("antigravity", out)


class Launcher(unittest.TestCase):
    def test_files_and_modes(self):
        self.assertTrue(LAUNCHER.is_file() and BAT.is_file())
        self.assertTrue(LAUNCHER.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3"))
        if os.name != "nt":
            self.assertTrue(LAUNCHER.stat().st_mode & stat.S_IXUSR, "bin/planner4 must be executable")

    def test_batch_file_is_windows_safe(self):
        raw = BAT.read_bytes()
        text = raw.decode("utf-8")
        self.assertNotIn(b"\x00", raw)
        self.assertIn("%*", text)
        self.assertIn('"%HERE%planner4"', text)
        self.assertIn("py -3", text)
        self.assertIn("exit /b %ERRORLEVEL%", text)
        self.assertTrue(all(ord(c) < 128 for c in text), "plain ASCII so cmd.exe code pages cannot mangle it")

    def test_engine_discovery(self):
        mod = load_launcher()
        self.assertTrue(mod.is_engine(ROOT))
        self.assertFalse(mod.is_engine(SKILL))
        self.assertFalse(mod.is_engine(ROOT / "nope"))
        self.assertEqual(mod.find_engine({}), ROOT)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(mod.candidates({"PLANNER4_HOME": d})[0], Path(d))
            self.assertEqual(mod.find_engine({"PLANNER4_HOME": d}), ROOT, "a bogus PLANNER4_HOME falls through to the repository")

    def test_build_command(self):
        mod = load_launcher()
        eng = Path("/e")
        cases = {("compile", "p.yaml"): ["dag_planner.py", "p.yaml"], ("view", "--root", "."): ["orchestrator.py", "plan", "--root", "."],
                 ("run", "-v"): ["orchestrator.py", "run", "-v"], ("status",): ["orchestrator.py", "status"],
                 ("orch", "plan"): ["orchestrator.py", "plan"], ("verify", "run"): ["verifier.py", "run"], ("swarm", "fleet"): ["swarm.py", "fleet"]}
        for argv, tail in cases.items():
            cmd = mod.build_command(list(argv), eng, "py")
            self.assertEqual(cmd[0], "py")
            self.assertEqual([Path(cmd[1]).name] + cmd[2:], tail)
        self.assertEqual(mod.build_command(["test", "-v"], eng, "py"), ["py", "-m", "unittest", "discover", "tests", "-v"])
        self.assertEqual(Path(mod.build_command(["fleet", "table"], eng, "py")[1]), eng / "core" / "fleet.py")
        self.assertEqual(Path(mod.build_command(["bridge", "check"], eng, "py")[1]), eng / "core" / "antigravity_bridge.py")
        self.assertEqual(Path(mod.build_command(["golden"], eng, "py")[1]), eng / "evals" / "golden_e2e_run.py")
        self.assertIsNone(mod.build_command(["nonsense"], eng, "py"))

    def test_where_and_help(self):
        r = run("where")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, str(ROOT)))
        r = run("help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("planner4 compile", r.stdout)
        self.assertIn("subcommands:", r.stdout)
        self.assertEqual(run().returncode, 0)

    def test_unknown_subcommand_is_exit_2(self):
        r = run("frobnicate")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown subcommand", r.stderr)

    def test_forwards_arguments_and_exit_codes(self):
        r = run("fleet", "table")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertEqual([w["id"] for w in data["fleet"]], ["AG1", "AG6", "AG2", "AG3", "AG4", "AG5", "AG7", "AG8"])
        self.assertEqual(data["table_problems"], [])
        self.assertEqual(run("fleet", "ports", "AG8", "T-1").returncode, 0)
        self.assertEqual(run("fleet", "ports", "AG9", "T-1").returncode, 2, "the engine's exit code is returned untouched")

    def test_runs_from_any_directory(self):
        with tempfile.TemporaryDirectory() as d:
            r = run("fleet", "ports", "AG3", "T-9", cwd=d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(3200 <= json.loads(r.stdout)["dev"] <= 3299)

    def test_engine_missing_is_exit_2_with_a_clear_message(self):
        with tempfile.TemporaryDirectory() as d:
            lone = Path(d) / "skill" / "bin"
            lone.mkdir(parents=True)
            shutil.copy(LAUNCHER, lone / "planner4")
            r = run("fleet", "table", launcher=lone / "planner4")
            self.assertEqual(r.returncode, 2)
            self.assertIn("PLANNER4_HOME", r.stderr)
            r = run("fleet", "table", launcher=lone / "planner4", env={"PLANNER4_HOME": str(ROOT)})
            self.assertEqual(r.returncode, 0, r.stderr)
            r = run("doctor", launcher=lone / "planner4")
            self.assertEqual(r.returncode, 1)
            self.assertIn("engine not found", r.stdout)

    def test_bundled_engine_folder_is_found(self):
        with tempfile.TemporaryDirectory() as d:
            skill = Path(d) / "skill"
            (skill / "bin").mkdir(parents=True)
            shutil.copy(LAUNCHER, skill / "bin" / "planner4")
            eng = skill / "engine"
            for rel in ("orchestrator.py", "swarm.py", "verifier.py", "dag_planner.py", "core/fleet.py"):
                (eng / rel).parent.mkdir(parents=True, exist_ok=True)
                (eng / rel).write_text("# stub\n", encoding="utf-8")
            r = run("where", launcher=skill / "bin" / "planner4")
            self.assertEqual((r.returncode, Path(r.stdout.strip())), (0, eng.resolve()))

    def test_doctor_reports_the_environment(self):
        r = run("doctor")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("fleet table: 8 workers", r.stdout)
        self.assertIn("account profiles on this machine", r.stdout)
        self.assertIn("PLANNER_LAWGIVER_KEY", r.stdout)
        r = run("doctor", env={"PLANNER_LAWGIVER_KEY": "x"})
        self.assertIn("PLANNER_LAWGIVER_KEY: set", r.stdout)
        self.assertNotIn("PLANNER_LAWGIVER_KEY: x", r.stdout, "the key itself is never printed")

    def test_view_without_tickets_is_a_clean_error(self):
        with tempfile.TemporaryDirectory() as d:
            r = run("view", "--root", d)
            self.assertEqual(r.returncode, 2)
            self.assertIn("no .planner/tickets", r.stderr)

    def test_in_process_main_help(self):
        mod = load_launcher()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(mod.main(["--help"]), 0)
        self.assertIn("planner4", out.getvalue())


if __name__ == "__main__":
    unittest.main()
