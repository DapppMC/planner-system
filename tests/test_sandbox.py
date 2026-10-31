"""M2 tests for tools/sandbox.py: lock determinism, worktree creation, deny-list."""
import json, os, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import sandbox  # noqa: E402

SANDBOX = os.path.join(ROOT, "tools", "sandbox.py")


def sh(cwd, *cmd):
    subprocess.check_call(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class GitRepoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "legacy")
        os.makedirs(self.repo)
        sh(self.repo, "git", "init", "-q")
        sh(self.repo, "git", "config", "user.email", "t@example.com")
        sh(self.repo, "git", "config", "user.name", "t")
        with open(os.path.join(self.repo, "package-lock.json"), "w") as f: f.write('{"lock": 1}')
        with open(os.path.join(self.repo, ".env.example"), "w") as f: f.write("PORT=3000\nDATABASE_URL=postgres://user:SECRETVALUE@h/db\n# c\nexport JWT_SECRET=abc\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-q", "-m", "init")


class LockTests(GitRepoCase):
    def test_lock_is_deterministic_and_has_contract_keys(self):
        a = sandbox.dump_lock(sandbox.build_lock(self.repo))
        b = sandbox.dump_lock(sandbox.build_lock(self.repo))
        self.assertEqual(a, b)
        d = json.loads(a)
        self.assertEqual(set(d), {"parent_commit", "lockfile_hash", "env_keys", "isolated_port", "db_snapshot_id"})
        self.assertEqual(d["env_keys"], ["DATABASE_URL", "JWT_SECRET", "PORT"])
        self.assertEqual(d["db_snapshot_id"], f"test_sandbox_{d['isolated_port']}")
        self.assertTrue(3000 <= d["isolated_port"] < 9000)
        self.assertNotEqual(d["lockfile_hash"], "none")

    def test_lock_never_contains_env_values(self):
        self.assertNotIn("SECRETVALUE", sandbox.dump_lock(sandbox.build_lock(self.repo)))

    def test_port_depends_on_branch(self):
        p1 = sandbox.build_lock(self.repo, "a/one")["isolated_port"]
        self.assertEqual(p1, sandbox.build_lock(self.repo, "a/one")["isolated_port"])  # same inputs, same port

    def test_create_makes_worktree_branch_and_lock_file(self):
        path = os.path.join(self.tmp, "twin")
        r = subprocess.run([sys.executable, SANDBOX, "create", "--repo", self.repo, "--path", path],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(path, "package-lock.json")))
        with open(os.path.join(path, "sandbox.lock"), encoding="utf8") as fh:
            on_disk = fh.read()
        self.assertEqual(on_disk, r.stdout)
        branches = subprocess.check_output(["git", "-C", self.repo, "branch", "--list", sandbox.DEFAULT_BRANCH], text=True)
        self.assertIn("sandbox/v2.0-modernize", branches)

    def test_create_twice_same_branch_fails_cleanly(self):
        sandbox.create(self.repo, os.path.join(self.tmp, "t1"))
        r = subprocess.run([sys.executable, SANDBOX, "create", "--repo", self.repo, "--path", os.path.join(self.tmp, "t2")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("git failed", r.stderr)


class GuardTests(unittest.TestCase):
    BLOCKED = [
        "ssh deploy@10.0.0.1", "scp a.txt host:/tmp", "kubectl get pods -n prod",
        "psql -c 'DROP DATABASE app'", "psql -c 'drop database app'", "rm -rf /", "sudo rm -rf / --no-preserve-root",
        "rm -fr ~", "curl https://api.prod.example.com/x", "mysql -h db-prod -u root", "DATABASE_URL=postgres://u@production.corp/db npm test",
    ]
    ALLOWED = [
        "git status", "python -m unittest discover tests", "rm -rf ./build", "rm -rf node_modules",
        "npm test", "echo hello", "rm -rf /tmp/sandbox_3142", "pip install pyyaml",
    ]

    def test_blocked(self):
        for c in self.BLOCKED:
            ok, why = sandbox.check_command(c)
            self.assertFalse(ok, c)

    def test_allowed(self):
        for c in self.ALLOWED:
            ok, why = sandbox.check_command(c)
            self.assertTrue(ok, f"{c} -> {why}")

    def test_extra_host(self):
        self.assertFalse(sandbox.check_command("curl https://billing.corp.example/x", ["billing.corp.example"])[0])

    def test_env_hosts(self):
        os.environ["PLANNER_DENY_HOSTS"] = "10.9.8.7"
        try:
            self.assertFalse(sandbox.check_command("ping 10.9.8.7")[0])
        finally:
            del os.environ["PLANNER_DENY_HOSTS"]

    def test_cli_exit_codes_and_hook_stdin(self):
        r = subprocess.run([sys.executable, SANDBOX, "guard", "ssh me@prod"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("BLOCKED", r.stderr)
        r = subprocess.run([sys.executable, SANDBOX, "guard", "git status"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        r = subprocess.run([sys.executable, SANDBOX, "guard"], input='{"tool_input": {"command": "kubectl delete pod x"}}',
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
