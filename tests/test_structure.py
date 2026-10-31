"""M0 structure tests: CLAUDE.md size, gate files, progress.md sections."""
import os, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATES = ["gate-2_cartography.md", "gate-1_pinning.md", "gate0_discovery.md",
         "gate1_requirements.md", "gate2_architecture.md", "gate3_verification.md",
         "gate4_readiness.md"]

def lines(path):
    with open(path, encoding="utf8") as f:
        return f.read().splitlines()

class Structure(unittest.TestCase):
    def test_claude_md_at_most_80_lines(self):
        self.assertLessEqual(len(lines(os.path.join(ROOT, "CLAUDE.md"))), 80)

    def test_gates_exist_and_at_most_150_lines(self):
        for g in GATES:
            p = os.path.join(ROOT, "gates", g)
            self.assertTrue(os.path.exists(p), g)
            self.assertLessEqual(len(lines(p)), 150, g)
            self.assertTrue(lines(p)[0].startswith("# Gate"), g)

    def test_progress_has_resume_section(self):
        text = "\n".join(lines(os.path.join(ROOT, "progress.md")))
        self.assertIn("## 4. Next Actionable Steps (Resume Point for Next Model/Agent)", text)
        self.assertIn("Current Status:", text)

if __name__ == "__main__":
    unittest.main()


class FinishingPolish(unittest.TestCase):
    def test_skill_description_is_short_single_line(self):
        import re
        for rel in ("skills/planner-3-engine/SKILL.md",):
            with open(os.path.join(ROOT, rel), encoding="utf8") as f:
                m = re.search(r"^description:\s*(.+)$", f.read(), re.M)
            self.assertIsNotNone(m)
            self.assertLessEqual(len(m.group(1)), 198)
            self.assertNotIn(": ", m.group(1))

    def test_every_tool_help_exits_zero(self):
        import subprocess, sys
        tools = ["repomap.py", "cartograph.py", "sandbox.py", "odcat.py", "ticketc.py", "design_lint.py", "install_skill.py",
                 "build_dist.py", "gate3/failure_signature.py", "gate3/reflect_check.py"]
        for t in tools:
            for flag in ("--help", "-h"):
                r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", *t.split("/")), flag], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"{t} {flag}: {r.stderr[:100]}")
