"""Iteration 2: tools/install_skill.py (OS detection, safe install, verify, dist rebuild)."""
import contextlib, io, os, shutil, subprocess, sys, tempfile, unittest, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import install_skill as ins  # noqa: E402


def run(argv, **kw):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = ins.main(argv, **kw)
    return code, out.getvalue(), err.getvalue()


class Paths(unittest.TestCase):
    def test_detect_os(self):
        self.assertEqual(ins.detect_os("win32"), "windows")
        self.assertEqual(ins.detect_os("cygwin"), "windows")
        self.assertEqual(ins.detect_os("linux"), "linux")
        self.assertEqual(ins.detect_os("darwin"), "macos")

    def test_linux_default(self):
        self.assertEqual(ins.default_target("linux", home="/home/ann"), os.path.join("/home/ann", ".gemini", "config", "skills"))

    def test_windows_default_uses_userprofile(self):
        t = ins.default_target("windows", env={"USERPROFILE": "C:\\Users\\lenovo"})
        self.assertEqual(t, "C:\\Users\\lenovo\\.gemini\\config\\skills")

    def test_cli_variant(self):
        self.assertTrue(ins.default_target("linux", home="/h", cli=True).endswith(os.path.join(".gemini", "antigravity-cli", "skills")))
        self.assertTrue(ins.default_target("windows", env={"USERPROFILE": "C:\\U"}, cli=True).endswith("antigravity-cli\\skills"))


class Install(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = tempfile.mkdtemp()
        shutil.rmtree(cls.repo)
        shutil.copytree(ROOT, cls.repo, ignore=shutil.ignore_patterns("dist", "__pycache__", ".git", "*.pyc"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.repo, ignore_errors=True)

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        shutil.rmtree(os.path.join(self.repo, "dist"), ignore_errors=True)

    def go(self, *extra, **kw):
        return run(["--root", self.repo, *extra], os_name="linux", home=self.home, **kw)

    def dest(self):
        return os.path.join(self.home, ".gemini", "config", "skills", "planner-3-engine")

    def test_installs_to_default_home_location_and_verifies(self):
        code, out, err = self.go("--no-dist")
        self.assertEqual(code, 0, out + err)
        d = self.dest()
        for rel in ("SKILL.md", "scripts/cartograph.py", "scripts/design_lint.py", "scripts/gate3/reflect_check.py",
                    "scripts/sgrecipes/find_api_routes.yml", "references/DESIGN_CONSTITUTION.md", "catalog/FACETS.md",
                    "catalog/source/LICENSE"):
            self.assertTrue(os.path.isfile(os.path.join(d, *rel.split("/"))), rel)
        self.assertFalse(os.path.exists(os.path.join(d, "evals")), "evals must not be installed")
        self.assertIn("Verify: ok", out)

    def test_installed_copy_really_runs(self):
        self.go("--no-dist")
        r = subprocess.run([sys.executable, "-I", os.path.join(self.dest(), "scripts", "design_lint.py"),
                            os.path.join(ROOT, "tests", "fixtures", "design", "good.html"), "--strict"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        r = subprocess.run([sys.executable, "-I", os.path.join(self.dest(), "scripts", "cartograph.py"),
                            os.path.join(ROOT, "tests", "fixtures", "legacy-certs"), "--out", os.path.join(self.home, "c.json")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_reinstall_replaces_and_is_idempotent(self):
        self.go("--no-dist")
        stale = os.path.join(self.dest(), "scripts", "stale.py")
        with open(stale, "w") as fh:
            fh.write("x=1\n")
        code, out, _ = self.go("--no-dist")
        self.assertEqual(code, 0)
        self.assertIn("(replace)", out)
        self.assertFalse(os.path.exists(stale))
        self.assertEqual([n for n in os.listdir(os.path.dirname(self.dest())) if n.startswith(".planner-install")], [])

    def test_refuses_foreign_folder(self):
        os.makedirs(self.dest())
        with open(os.path.join(self.dest(), "SKILL.md"), "w") as fh:
            fh.write("---\nname: someone-elses-skill\ndescription: x\n---\n")
        code, _, err = self.go("--no-dist")
        self.assertEqual(code, 2)
        self.assertIn("REFUSED", err)
        with open(os.path.join(self.dest(), "SKILL.md")) as fh:
            self.assertIn("someone-elses-skill", fh.read())

    def test_refuses_folder_without_skill_md(self):
        os.makedirs(self.dest())
        code, _, err = self.go("--no-dist")
        self.assertEqual(code, 2)

    def test_dry_run_writes_nothing(self):
        code, out, _ = self.go("--dry-run")
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".gemini")))
        self.assertIn("Nothing written", out)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "dist")))

    def test_custom_target_and_cli_flag(self):
        t = os.path.join(self.home, "custom")
        self.assertEqual(self.go("--target", t, "--no-dist")[0], 0)
        self.assertTrue(os.path.isfile(os.path.join(t, "planner-3-engine", "SKILL.md")))
        self.assertEqual(self.go("--cli", "--no-dist")[0], 0)
        self.assertTrue(os.path.isfile(os.path.join(self.home, ".gemini", "antigravity-cli", "skills", "planner-3-engine", "SKILL.md")))

    def test_target_is_a_file_is_refused(self):
        f = os.path.join(self.home, "afile")
        open(f, "w").close()
        self.assertEqual(self.go("--target", f, "--no-dist")[0], 2)

    def test_dist_zip_rebuilt_with_synced_skill(self):
        code, out, err = self.go()
        self.assertEqual(code, 0, out + err)
        z = os.path.join(self.repo, "dist", "planner-system-3.0.zip")
        self.assertTrue(os.path.isfile(z))
        with zipfile.ZipFile(z) as zf:
            names = zf.namelist()
        for rel in ("tools/install_skill.py", "tools/cartograph.py", "tools/design_lint.py",
                    "skills/planner-3-engine/scripts/design_lint.py", "skills/planner-3-engine/catalog/source/LICENSE",
                    "catalog/source/curation.json"):
            self.assertIn("planner-system-3.0/" + rel, names)

    def test_sync_removes_stale_and_copies_source_of_truth(self):
        sk = os.path.join(self.repo, "skills", "planner-3-engine", "scripts")
        with open(os.path.join(sk, "old_tool.py"), "w") as fh:
            fh.write("x=1\n")
        ch = ins.sync_bundle(self.repo)
        self.assertIn("-skills/planner-3-engine/scripts/old_tool.py", ch)
        self.assertFalse(os.path.exists(os.path.join(sk, "old_tool.py")))
        self.assertEqual(ins.sync_bundle(self.repo), [], "second sync must be a no-op")

    def test_verify_catches_broken_install(self):
        self.go("--no-dist")
        with open(os.path.join(self.dest(), "scripts", "repomap.py"), "w") as fh:
            fh.write("def broken(:\n")
        self.assertTrue(any("does not compile" in p for p in ins.verify(self.dest())))


if __name__ == "__main__":
    unittest.main()
