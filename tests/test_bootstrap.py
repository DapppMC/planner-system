"""Tests for Planner AI 3.0 project bootstrapper."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from tools.bootstrap_project import bootstrap_project, detect_project_mode

ROOT = Path(__file__).resolve().parent.parent

class TestBootstrap(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_bootstrap_"))

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_detect_greenfield(self):
        mode = detect_project_mode(self.test_dir)
        self.assertEqual(mode, "GREENFIELD")

    def test_detect_brownfield(self):
        src_file = self.test_dir / "app.py"
        src_file.write_text("print('hello')", encoding="utf-8")
        mode = detect_project_mode(self.test_dir)
        self.assertEqual(mode, "BROWNFIELD")

    def test_bootstrap_greenfield(self):
        bootstrap_project(self.test_dir, ROOT)
        
        # Verify directories exist
        self.assertTrue((self.test_dir / "docs" / "spec" / "tickets").exists())
        self.assertTrue((self.test_dir / "scripts" / "cartograph.py").exists())
        self.assertTrue((self.test_dir / "scripts" / "design_lint.py").exists())
        self.assertTrue((self.test_dir / "scripts" / "ticketc.py").exists())
        self.assertTrue((self.test_dir / "scripts" / "gate3" / "reflect_check.py").exists())
        self.assertTrue((self.test_dir / "docs" / "spec" / "DESIGN_CONSTITUTION.md").exists())
        self.assertTrue((self.test_dir / "docs" / "spec" / "gates" / "gate-2_cartography.md").exists())
        self.assertTrue((self.test_dir / "swarm.config.json").exists())
        self.assertTrue((self.test_dir / "progress.md").exists())
        self.assertTrue((self.test_dir / ".planner_manifest.json").exists())
        
        manifest = json.loads((self.test_dir / ".planner_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "3.0.0")
        self.assertEqual(manifest["project_mode"], "GREENFIELD")
        self.assertEqual(manifest["status"], "READY_FOR_DISCOVERY")

    def test_bootstrap_brownfield(self):
        (self.test_dir / "legacy.py").write_text("def run(): pass", encoding="utf-8")
        bootstrap_project(self.test_dir, ROOT)
        
        manifest = json.loads((self.test_dir / ".planner_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "3.0.0")
        self.assertEqual(manifest["project_mode"], "BROWNFIELD")
        self.assertEqual(manifest["status"], "READY_FOR_CARTOGRAPHY")
        
        prog = (self.test_dir / "progress.md").read_text(encoding="utf-8")
        self.assertIn("BROWNFIELD", prog)
        self.assertIn("Gate -2 Cartography", prog)

class TestBootstrapPlanner4(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_bootstrap4_"))

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_planner4_engines_are_injected_additively(self):
        bootstrap_project(self.test_dir, ROOT)
        p4 = self.test_dir / "scripts" / "planner4"
        for rel in ("orchestrator.py", "swarm.py", "verifier.py", "dag_planner.py", "core/fleet.py", "core/coordination/blackboard.py",
                    "core/verification/sealed_gate.py", "core/ui/cdp_linter.py"):
            self.assertTrue((p4 / rel).exists(), rel)
        self.assertFalse(list(p4.rglob("__pycache__")))
        for d in ("tickets", "sealed", "work", "runs", "escalations"):
            self.assertTrue((self.test_dir / ".planner" / d).is_dir(), d)
        manifest = json.loads((self.test_dir / ".planner_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "3.0.0")
        self.assertEqual(manifest["planner4"]["scripts"], "scripts/planner4")
        self.assertIn(".planner/board.db*", (self.test_dir / ".gitignore").read_text(encoding="utf-8"))

    def test_the_injected_orchestrator_runs_from_the_workspace(self):
        import subprocess
        import sys
        bootstrap_project(self.test_dir, ROOT)
        r = subprocess.run([sys.executable, str(self.test_dir / "scripts" / "planner4" / "orchestrator.py"), "--help"], capture_output=True, text=True, cwd=self.test_dir)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.assertIn("plan", r.stdout)

    def test_no_planner4_when_the_root_has_none(self):
        fake = Path(tempfile.mkdtemp(prefix="fake_root_"))
        try:
            for d in ("tools", "gates", "design", "catalog"):
                shutil.copytree(ROOT / d, fake / d, ignore=shutil.ignore_patterns("__pycache__"))
            bootstrap_project(self.test_dir, fake)
            self.assertFalse((self.test_dir / "scripts" / "planner4").exists())
            manifest = json.loads((self.test_dir / ".planner_manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn("planner4", manifest)
        finally:
            shutil.rmtree(fake, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
