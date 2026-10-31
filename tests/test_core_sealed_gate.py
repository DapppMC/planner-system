"""M1 tests: sealed vault + isolated verification runner (needs pytest in the target interpreter)."""
import importlib.util, os, shutil, stat, sys, tempfile, textwrap, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.verification import sealed_gate as sg  # noqa: E402

HAVE_PYTEST = importlib.util.find_spec("pytest") is not None


@unittest.skipUnless(HAVE_PYTEST, "pytest not installed")
class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sg_"))
        self.root = self.tmp / "vault"
        self.wt = self.tmp / "wt"
        self.wt.mkdir()
        (self.wt / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        self.tests = self.tmp / "tests_src"
        self.tests.mkdir()
        (self.tests / "test_calc.py").write_text(textwrap.dedent('''
            import os
            from calc import add

            def test_add():
                assert add(2, 3) == 5

            def test_add_negative():
                assert add(-1, 1) == 0

            def test_no_secrets_in_env():
                assert "PLANNER_TEST_SECRET_TOKEN" not in os.environ
        '''))
        os.environ["PLANNER_TEST_SECRET_TOKEN"] = "hunter2"

    def tearDown(self):
        os.environ.pop("PLANNER_TEST_SECRET_TOKEN", None)
        for p in (self.root,):
            from core.platform_compat import safe_rmtree
            safe_rmtree(p)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def seal(self, **kw):
        return sg.seal("T-1", self.tests, root=self.root, **kw)

    def run_gate(self, **kw):
        kw.setdefault("timeout", 90)
        return sg.run_isolated_verification(self.wt, "T-1", root=self.root, **kw)

    def test_seal_writes_manifest_and_counts_tests(self):
        m = self.seal()
        self.assertEqual(m["expected_tests"], 3)
        self.assertIn("test_calc.py", m["files"])
        ok, problems, _ = sg.verify_vault("T-1", self.root)
        self.assertTrue(ok, problems)

    def test_pass(self):
        self.seal()
        r = self.run_gate()
        self.assertTrue(r.passed, r.to_dict())
        self.assertEqual(r.counts["tests"], 3)
        self.assertEqual(r.reason, "ALL_SEALED_TESTS_PASSED")

    def test_fail_reveals_ids_but_not_expected_values(self):
        self.seal()
        (self.wt / "calc.py").write_text("def add(a, b):\n    return a - b\n")
        r = self.run_gate()
        self.assertFalse(r.passed)
        self.assertTrue(any("test_add" in t for t in r.failed_tests))
        self.assertNotIn("== 5", r.output)
        self.assertNotIn("assert", r.output.lower())
        r2 = self.run_gate(reveal="summary")
        self.assertFalse(r2.passed)

    def test_worker_conftest_and_ini_cannot_influence_the_run(self):
        self.seal()
        (self.wt / "calc.py").write_text("def add(a, b):\n    return 0\n")
        (self.wt / "conftest.py").write_text("def pytest_collection_modifyitems(items):\n    items.clear()\n")
        (self.wt / "pytest.ini").write_text("[pytest]\naddopts = --deselect sealed\n")
        (self.wt / "sitecustomize.py").write_text("import sys\n")
        r = self.run_gate()
        self.assertFalse(r.passed, "worker config must not be able to empty or deselect the oracle")
        self.assertGreater(r.counts["failures"], 0)

    def test_skipped_oracle_is_not_a_pass(self):
        (self.tests / "test_calc.py").write_text("import pytest\ndef test_a():\n    pytest.skip('nope')\n")
        self.seal()
        r = self.run_gate()
        self.assertFalse(r.passed)
        self.assertIn("SKIPPED", r.reason)

    def test_missing_vault_fails_closed_unless_allowed(self):
        r = self.run_gate()
        self.assertFalse(r.passed)
        self.assertIn("NO_SEALED_TESTS", r.reason)
        self.assertTrue(self.run_gate(allow_missing=True).passed)

    def test_tampered_vault_detected(self):
        self.seal()
        f = self.root / "T-1" / "tests" / "test_calc.py"
        os.chmod(f, stat.S_IWRITE | stat.S_IREAD)
        f.write_text("def test_x():\n    assert True\n")
        r = self.run_gate()
        self.assertFalse(r.passed)
        self.assertIn("VAULT_TAMPERED", r.reason)
        extra = self.root / "T-1" / "tests" / "test_extra.py"
        f.write_text((self.tests / "test_calc.py").read_text())
        extra.write_text("def test_y():\n    assert True\n")
        ok, problems, _ = sg.verify_vault("T-1", self.root)
        self.assertFalse(ok)
        self.assertTrue(any("ADDED" in p for p in problems))

    def test_timeout_kills_and_reports(self):
        (self.tests / "test_calc.py").write_text("import time\ndef test_slow():\n    time.sleep(120)\n")
        self.seal()
        r = self.run_gate(timeout=3)
        self.assertFalse(r.passed)
        self.assertTrue(r.timed_out)
        self.assertLess(r.duration, 60)

    def test_zero_tests_refused_at_seal_time(self):
        (self.tests / "test_calc.py").write_text("x = 1\n")
        with self.assertRaises(sg.GateError):
            self.seal()
        self.assertFalse((self.root / "T-1").exists())

    def test_pytest_exit_5_is_failure_when_tests_vanish(self):
        self.seal()
        # a worker that makes collection fail / empty must not pass
        (self.wt / "calc.py").write_text("raise SystemExit(0)\n")
        r = self.run_gate()
        self.assertFalse(r.passed)

    def test_ticket_id_validation(self):
        for bad in ("../x", "a/b", "", ".", "..", "a b", "x" * 200):
            with self.assertRaises(sg.GateError, msg=bad):
                sg.check_ticket_id(bad)
        self.assertFalse(sg.run_isolated_verification(self.wt, "T-1", root=self.root).passed)

    @unittest.skipIf(os.name == "nt", "symlink creation needs privileges on Windows")
    def test_symlinks_are_not_copied_into_sandbox(self):
        secret = self.tmp / "secret.txt"
        secret.write_text("TOP SECRET")
        os.symlink(secret, self.wt / "leak.txt")
        os.symlink(self.tmp, self.wt / "leakdir")
        stats = sg.copy_tree_safe(self.wt, self.tmp / "copy")
        self.assertIn("leak.txt", stats.skipped_symlinks)
        self.assertIn("leakdir", stats.skipped_symlinks)
        self.assertFalse((self.tmp / "copy" / "leak.txt").exists())

    def test_size_budget(self):
        (self.wt / "big.bin").write_bytes(b"0" * 300_000)
        with self.assertRaises(sg.GateError):
            sg.copy_tree_safe(self.wt, self.tmp / "c2", max_total_bytes=100_000)
        st = sg.copy_tree_safe(self.wt, self.tmp / "c3", max_file_bytes=100_000)
        self.assertIn("big.bin", st.skipped_large)

    def test_custom_command_runner(self):
        self.seal()
        cmd = [sys.executable, "-c", "import sys; sys.exit(0)"]
        r = self.run_gate(command=cmd)
        self.assertTrue(r.passed)
        r = self.run_gate(command=[sys.executable, "-c", "import sys; sys.exit(7)"])
        self.assertFalse(r.passed)

    def test_network_isolation_yes_fails_loudly_when_unavailable(self):
        self.seal()
        r = self.run_gate(isolate_network="yes")
        if sg._unshare_prefix():
            self.assertTrue(r.passed, r.to_dict())
            self.assertTrue(r.network_isolated)
        else:
            self.assertFalse(r.passed)
            self.assertIn("NETWORK_ISOLATION_UNAVAILABLE", r.reason)


KEY = "k" * 32


@unittest.skipUnless(HAVE_PYTEST, "pytest not installed")
class VaultHmac(unittest.TestCase):
    """The vault manifest authenticated with HMAC-SHA256 under the Inquisitor's own key (PLANNER_INQUISITOR_KEY)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sgh_"))
        self.root = self.tmp / "vault"
        self.tests = self.tmp / "tests_src"
        self.tests.mkdir()
        (self.tests / "test_a.py").write_text("def test_a():\n    assert 1 + 1 == 2\n")
        self._saved = os.environ.pop(sg.ENV_SEAL_KEY, None)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        os.environ.pop(sg.ENV_SEAL_KEY, None)
        if self._saved is not None:
            os.environ[sg.ENV_SEAL_KEY] = self._saved
        from core.platform_compat import safe_rmtree
        safe_rmtree(self.root)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def forge(self, key):
        """Attacker with write access to the vault: replace the test and recompute the sha256 manifest (and optionally a MAC)."""
        import json
        from core.platform_compat import safe_rmtree
        vault = self.root / "T-1"
        mf = vault / "manifest.json"
        m = json.loads(mf.read_text())
        t = vault / "tests" / "test_a.py"
        t.chmod(0o644)
        t.write_text("def test_a():\n    assert True\n")
        m["files"] = sg._hash_tree(vault / "tests")
        if key:
            m["hmac"] = sg.manifest_mac(m, key)
        mf.chmod(0o644)
        mf.write_text(json.dumps(m))

    def test_no_key_means_sha256_only_and_no_mac_field(self):
        m = sg.seal("T-1", self.tests, root=self.root)
        self.assertNotIn("hmac", m)
        self.assertTrue(sg.verify_vault("T-1", self.root)[0])

    def test_key_from_the_environment_signs_and_verifies(self):
        os.environ[sg.ENV_SEAL_KEY] = KEY
        m = sg.seal("T-1", self.tests, root=self.root)
        self.assertEqual(m["auth"], "hmac-sha256")
        self.assertRegex(m["hmac"], r"^[0-9a-f]{64}$")
        ok, problems, _ = sg.verify_vault("T-1", self.root)
        self.assertTrue(ok, problems)

    def test_explicit_key_argument(self):
        sg.seal("T-1", self.tests, root=self.root, hmac_key=KEY)
        self.assertTrue(sg.verify_vault("T-1", self.root, hmac_key=KEY)[0])
        ok, problems, _ = sg.verify_vault("T-1", self.root, hmac_key="z" * 32)
        self.assertFalse(ok)
        self.assertTrue(any("VAULT_HMAC_INVALID" in p for p in problems), problems)

    def test_forged_sha256_manifest_is_caught_only_with_the_mac(self):
        sg.seal("T-1", self.tests, root=self.root, hmac_key=KEY)
        self.forge(None)
        ok, problems, _ = sg.verify_vault("T-1", self.root)   # no key: only sha256 is checked, and the attacker fixed that
        self.assertTrue(ok, "this is exactly the gap the MAC closes")
        ok, problems, _ = sg.verify_vault("T-1", self.root, hmac_key=KEY)
        self.assertFalse(ok)
        self.assertTrue(any("VAULT_HMAC_INVALID" in p for p in problems), problems)

    def test_attacker_without_the_key_cannot_forge_a_mac(self):
        sg.seal("T-1", self.tests, root=self.root, hmac_key=KEY)
        self.forge(b"attacker-guess-0123456789abcdef")
        ok, problems, _ = sg.verify_vault("T-1", self.root, hmac_key=KEY)
        self.assertFalse(ok)
        self.assertTrue(any("VAULT_HMAC_INVALID" in p for p in problems))

    def test_stripping_the_mac_is_a_downgrade_that_fails_closed(self):
        import json
        sg.seal("T-1", self.tests, root=self.root, hmac_key=KEY)
        mf = self.root / "T-1" / "manifest.json"
        m = json.loads(mf.read_text())
        m.pop("hmac"), m.pop("auth")
        mf.chmod(0o644)
        mf.write_text(json.dumps(m))
        ok, problems, _ = sg.verify_vault("T-1", self.root, hmac_key=KEY)
        self.assertFalse(ok)
        self.assertTrue(any("VAULT_UNAUTHENTICATED" in p for p in problems), problems)

    def test_editing_any_manifest_field_breaks_the_mac(self):
        import json
        sg.seal("T-1", self.tests, root=self.root, hmac_key=KEY)
        mf = self.root / "T-1" / "manifest.json"
        m = json.loads(mf.read_text())
        m["expected_tests"] = 0   # lowering the count the gate insists on
        mf.chmod(0o644)
        mf.write_text(json.dumps(m))
        self.assertFalse(sg.verify_vault("T-1", self.root, hmac_key=KEY)[0])

    def test_short_keys_are_refused(self):
        with self.assertRaises(sg.GateError):
            sg.seal("T-1", self.tests, root=self.root, hmac_key="short")
        os.environ[sg.ENV_SEAL_KEY] = "short"
        sg.seal("T-2", self.tests, root=self.root, hmac_key=KEY)
        ok, problems, _ = sg.verify_vault("T-2", self.root)
        self.assertFalse(ok)
        self.assertTrue(any("VAULT_KEY_INVALID" in p for p in problems), problems)

    def test_gate_run_enforces_the_mac(self):
        os.environ[sg.ENV_SEAL_KEY] = KEY
        sg.seal("T-1", self.tests, root=self.root)
        wt = self.tmp / "wt"
        wt.mkdir()
        r = sg.run_isolated_verification(wt, "T-1", root=self.root, timeout=60)
        self.assertTrue(r.passed, r.reason)
        self.forge(None)
        r = sg.run_isolated_verification(wt, "T-1", root=self.root, timeout=60)
        self.assertFalse(r.passed)
        self.assertIn("VAULT_TAMPERED", r.reason)

    def test_the_key_never_appears_in_the_manifest(self):
        sg.seal("T-1", self.tests, root=self.root, hmac_key=KEY)
        self.assertNotIn(KEY, (self.root / "T-1" / "manifest.json").read_text())


class Guard(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "wt" / "src").mkdir(parents=True)
        self.sealed = self.tmp / "sealed"
        self.sealed.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_relative_resolves_against_worktree_not_cwd(self):
        p = sg.path_guard("src/a.py", self.tmp / "wt", sealed=self.sealed)
        self.assertEqual(p, (self.tmp / "wt" / "src" / "a.py").resolve())

    def test_denials(self):
        for bad in ("../outside.txt", str(self.sealed / "T" / "t.py"), str(self.tmp / "other")):
            with self.assertRaises(PermissionError, msg=bad):
                sg.path_guard(bad, self.tmp / "wt", sealed=self.sealed)

    def test_sibling_prefix_is_not_inside(self):
        (self.tmp / "wt2").mkdir()
        with self.assertRaises(PermissionError):
            sg.path_guard(str(self.tmp / "wt2" / "a.py"), self.tmp / "wt", sealed=self.sealed)

    @unittest.skipIf(os.name == "nt", "symlink creation needs privileges on Windows")
    def test_symlink_escape(self):
        os.symlink(self.tmp, self.tmp / "wt" / "src" / "out")
        with self.assertRaises(PermissionError):
            sg.path_guard("src/out/secret", self.tmp / "wt", sealed=self.sealed)


class Baseline(unittest.TestCase):
    def test_capture_and_wrapper(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            wt = tmp / "wt"
            (wt / "tests").mkdir(parents=True)
            (wt / "tests" / "test_a.py").write_text("def test_a():\n    assert f() == 1\n")
            (wt / "app.py").write_text("x=1\n")
            (wt / "pytest.ini").write_text("[pytest]\n")
            n = sg.capture_baseline(wt, tmp / "base")
            self.assertEqual(n, 2)
            self.assertFalse((tmp / "base" / "app.py").exists())
            (wt / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
            msgs = sg.check_weakening(tmp / "base", wt)
            self.assertTrue(any("ASSERT_DECREASED" in m for m in msgs))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
