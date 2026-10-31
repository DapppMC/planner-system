"""M2 tests for tools/repomap.py."""
import contextlib, io, os, shutil, subprocess, sys, tempfile, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import repomap  # noqa: E402


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf8") as fh:
        fh.write(text)


class RepomapTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        write(self.root, "core/parser.py", "class CertificateParser:\n    pass\n\ndef parse_certificate(x):\n    return CertificateParser()\n\ndef lonely_helper_function():\n    return 1\n")
        for i in range(4):
            write(self.root, f"app/use{i}.py", "from core.parser import parse_certificate, CertificateParser\n\ndef run%d():\n    return parse_certificate(CertificateParser())\n" % i)
        write(self.root, "web/api.ts", "export function handleSliceCert(req) { return 1 }\nexport const slicerConfiguration = {}\n")
        write(self.root, "web/main.ts", "import { handleSliceCert } from './api'\nhandleSliceCert(1)\n")
        write(self.root, "node_modules/junk/index.js", "function ignoredJunkFunction() {}\nignoredJunkFunction()\n")

    def test_most_referenced_file_ranks_first(self):
        text, used, n = repomap.build_repomap(self.root, 1500)
        self.assertTrue(text.splitlines()[0].startswith("core/parser.py"))
        self.assertIn("CertificateParser", text)
        self.assertEqual(n, 7)  # parser + 4 users + api + main; node_modules skipped

    def test_unreferenced_symbols_and_skip_dirs_excluded(self):
        text, _, _ = repomap.build_repomap(self.root, 1500)
        self.assertNotIn("lonely_helper_function", text)
        self.assertNotIn("ignoredJunkFunction", text)

    def test_budget_respected(self):
        for budget in (20, 60, 1500):
            text, used, _ = repomap.build_repomap(self.root, budget)
            self.assertLessEqual(used, budget)
            self.assertLessEqual(repomap.est(text), budget)

    def test_focus_personalization_keeps_focus_file_in(self):
        text, _, _ = repomap.build_repomap(self.root, 1500, focus=["web/api.ts"])
        self.assertIn("web/api.ts", text)

    def test_pagerank_is_distribution(self):
        nodes = ["a", "b", "c"]
        pr = repomap.pagerank({("a", "b"): 1.0, ("c", "b"): 2.0}, nodes, {})
        self.assertAlmostEqual(sum(pr.values()), 1.0, places=6)
        self.assertEqual(max(pr, key=pr.get), "b")

    def test_empty_repo_does_not_crash(self):
        empty = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, empty, True)
        text, used, n = repomap.build_repomap(empty, 1500)
        self.assertEqual((text, used, n), ("", 0, 0))

    def test_acceptance_cli_on_this_project_under_budget_and_3s(self):
        t0 = time.time()
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "repomap.py"), ROOT, "--budget", "1500"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(time.time() - t0, 3.0)
        self.assertLessEqual(repomap.est(r.stdout), 1500)
        self.assertIn("tokens (budget 1500)", r.stderr)

    def test_json_flag(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            repomap.main([self.root, "--json"])
        self.assertIn('"pagerank_score"', buf.getvalue())


if __name__ == "__main__":
    unittest.main()
