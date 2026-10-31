"""M6 tests: distribution packaging and the eval runner contract."""
import json, os, subprocess, sys, tempfile, unittest, zipfile, hashlib, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_dist  # noqa: E402


class Dist(unittest.TestCase):
    def test_zip_is_complete_clean_and_deterministic(self):
        with tempfile.TemporaryDirectory() as t:
            a, n = build_dist.build(ROOT, os.path.join(t, "a.zip"))
            b, _ = build_dist.build(ROOT, os.path.join(t, "b.zip"))
            def sha(p):
                with open(p, "rb") as fh:
                    return hashlib.sha256(fh.read()).hexdigest()
            self.assertEqual(sha(a), sha(b))
            with zipfile.ZipFile(a) as z:
                names = z.namelist()
                self.assertIsNone(z.testzip())
            self.assertTrue(all(x.startswith("planner-system-3.0/") for x in names))
            self.assertIn("planner-system-3.0/CLAUDE.md", names)
            self.assertFalse([x for x in names if "/dist/" in x or "__pycache__" in x or x.endswith(".pyc")])
            self.assertEqual(len(names), n)


class Dist4(unittest.TestCase):
    def test_4_0_zip_contains_the_engines_and_no_runtime_state(self):
        with tempfile.TemporaryDirectory() as t:
            out, n = build_dist.build(ROOT, os.path.join(t, "p4.zip"), "planner-system-4.0")
            with zipfile.ZipFile(out) as z:
                names = z.namelist()
            self.assertTrue(all(x.startswith("planner-system-4.0/") for x in names))
            for rel in ("orchestrator.py", "swarm.py", "verifier.py", "core/fleet.py", "core/ui/cdp-kinematic-optical-linter.mjs", "docs/ENGINE_AUDIT.md"):
                self.assertIn("planner-system-4.0/" + rel, names)
            self.assertFalse([x for x in names if "/.planner/" in x or "/mutants/" in x or "node_modules" in x])
            for rel in ("core/antigravity_bridge.py", "core/verification/appsec_audit.py", "evals/golden_e2e_run.py", "evals/run_evals.py",
                        "skills/planner-4-engine/SKILL.md", "skills/planner-4-engine/bin/planner4", "skills/planner-4-engine/bin/planner4.bat"):
                self.assertIn("planner-system-4.0/" + rel, names)
            self.assertFalse([x for x in names if "__pycache__" in x or x.endswith(".pyc")])
            with zipfile.ZipFile(out) as z:
                mode = {i.filename: (i.external_attr >> 16) & 0o777 for i in z.infolist()}
            self.assertEqual(mode["planner-system-4.0/skills/planner-4-engine/bin/planner4"], 0o755, "the launcher keeps its exec bit in the zip")
            self.assertEqual(mode["planner-system-4.0/skills/planner-4-engine/bin/planner4.bat"], 0o644)
            self.assertEqual(mode["planner-system-4.0/orchestrator.py"], 0o755)
            self.assertEqual(mode["planner-system-4.0/CLAUDE.md"], 0o644)


class EvalTasks(unittest.TestCase):
    def test_ten_tasks_five_each(self):
        specs = []
        for p in glob.glob(os.path.join(ROOT, "evals", "tasks", "*.json")):
            with open(p, encoding="utf8") as fh:
                specs.append(json.load(fh))
        self.assertEqual(len(specs), 10)
        self.assertEqual(sorted(s["kind"] for s in specs).count("greenfield"), 5)

    def test_run_py_passes(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "evals", "run.py")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("FAIL", r.stdout)
        self.assertRegex(r.stdout, r"pass rate: (10/10|9/10 \(1 skipped\))")


if __name__ == "__main__":
    unittest.main()
