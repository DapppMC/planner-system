"""Tests for core/platform_compat.py (paths, overlap, process tree kill, env scrub, fs classification)."""
import os, sys, tempfile, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import platform_compat as pc  # noqa: E402


class NormalizePath(unittest.TestCase):
    def test_slashes_case_and_dots(self):
        self.assertEqual(pc.normalize_rel_path(".\\Src\\\\API//Routes.PY"), "src/api/routes.py")

    def test_unicode_nfc(self):
        nfd = "caf\u0065\u0301.txt"
        self.assertEqual(pc.normalize_rel_path(nfd), pc.normalize_rel_path("caf\u00e9.txt"))

    def test_trailing_dot_space_ignored_like_windows(self):
        self.assertEqual(pc.normalize_rel_path("src/a.py. "), "src/a.py")

    def test_rejections(self):
        bad = ["", "  ", "/etc/passwd", "C:\\x\\y", "..\\x", "a/../../b", "a/b:stream", "src/CON", "src/nul.txt",
               "src/com1.log", "\\\\server\\share\\x", "PROGRA~1/x", "a\x00b", "."]
        for p in bad:
            with self.assertRaises(pc.PathError, msg=repr(p)):
                pc.normalize_rel_path(p)

    def test_long_path_prefix_stripped(self):
        self.assertEqual(pc.normalize_rel_path("\\\\?\\src\\a.py"), "src/a.py")

    def test_non_string(self):
        with self.assertRaises(pc.PathError):
            pc.normalize_rel_path(None)  # type: ignore[arg-type]


class Overlap(unittest.TestCase):
    def ov(self, a, b):
        return pc.patterns_overlap(pc.normalize_rel_path(a), pc.normalize_rel_path(b))

    def test_equal_and_directory_cover(self):
        self.assertTrue(self.ov("src/api", "src/api/routes/x.py"))
        self.assertTrue(self.ov("src/api/routes/x.py", "src/api"))
        self.assertTrue(self.ov("src/a.py", "SRC/A.PY"))

    def test_siblings_do_not_overlap(self):
        self.assertFalse(self.ov("src/api", "src/apix"))
        self.assertFalse(self.ov("src/a.py", "src/b.py"))

    def test_glob_vs_literal(self):
        self.assertTrue(self.ov("packages/db/migrations/*.sql", "packages/db/migrations/001.sql"))
        self.assertFalse(self.ov("packages/db/migrations/*.sql", "packages/db/migrations/readme.md"))
        self.assertFalse(self.ov("packages/db/migrations/*.sql", "packages/api/x.sql"))

    def test_double_star(self):
        self.assertTrue(self.ov("src/**", "src/deep/er/x.py"))
        self.assertTrue(self.ov("**", "anything/at/all"))
        self.assertTrue(self.ov("src/**/x.py", "src/a/b/y.py"))  # conservative

    def test_two_globs_conservative(self):
        self.assertTrue(self.ov("src/*.py", "src/a*.py"))


class MatchesAny(unittest.TestCase):
    def test_scope_semantics(self):
        pats = ["packages/api/src/**", "apps/web/src/features/escrow/SettlementDrawer.tsx", "docs"]
        self.assertTrue(pc.matches_any("packages/api/src/domain/x.ts", pats))
        self.assertTrue(pc.matches_any("apps/web/src/features/escrow/settlementdrawer.tsx", pats))
        self.assertTrue(pc.matches_any("docs/readme.md", pats))
        self.assertFalse(pc.matches_any("apps/web/src/other.tsx", pats))
        self.assertFalse(pc.matches_any("../escape", pats))

    def test_star_does_not_cross_folders(self):
        self.assertTrue(pc.matches_any("tests/a.py", ["tests/*.py"]))
        self.assertFalse(pc.matches_any("tests/sub/a.py", ["tests/*.py"]))
        self.assertTrue(pc.matches_any("tests/sub/a.py", ["tests/**/*.py"]))


class EnvScrub(unittest.TestCase):
    def test_secrets_dropped_allowlist_kept(self):
        base = {"PATH": "/bin", "OPENAI_API_KEY": "sk-x", "GITHUB_TOKEN": "t", "AWS_SECRET_ACCESS_KEY": "s", "HOME": "/h",
                "RANDOM_VAR": "x", "MY_PASSWORD": "p", "SYSTEMROOT": "C:\\Windows"}
        env = pc.build_sandbox_env(base=base)
        self.assertEqual(env["PATH"], "/bin")
        self.assertEqual(env["HOME"], "/h")
        self.assertEqual(env["PYTHONUTF8"], "1")
        for k in ("OPENAI_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "RANDOM_VAR", "MY_PASSWORD"):
            self.assertNotIn(k, env)

    def test_keep_cannot_resurrect_secret_but_extra_can(self):
        env = pc.build_sandbox_env(base={"MY_TOKEN": "x", "FOO": "1"}, keep=["MY_TOKEN", "FOO"])
        self.assertNotIn("MY_TOKEN", env)
        self.assertEqual(env["FOO"], "1")
        env = pc.build_sandbox_env({"PORT": "3100"}, base={})
        self.assertEqual(env["PORT"], "3100")

    def test_is_secret_name(self):
        for n in ("API_KEY", "github_token", "DB_PASSWORD", "Authorization", "client-secret", "PAT"):
            self.assertTrue(pc.is_secret_name(n), n)
        for n in ("PATH", "PORT", "KEYBOARD", "TOKENIZERS_PARALLELISM_X"):
            self.assertFalse(pc.is_secret_name(n), n)


class RunProcess(unittest.TestCase):
    def py(self, code, **kw):
        return pc.run_process([sys.executable, "-c", code], **kw)

    def test_ok_and_output(self):
        r = self.py("print('hi'); import sys; print('err', file=sys.stderr)", timeout=30)
        self.assertTrue(r.ok)
        self.assertEqual(r.stdout.strip(), "hi")
        self.assertIn("err", r.stderr)

    def test_exit_code(self):
        r = self.py("import sys; sys.exit(3)", timeout=30)
        self.assertEqual(r.returncode, 3)
        self.assertFalse(r.ok)

    def test_timeout_kills_grandchildren(self):
        marker = Path(tempfile.mkdtemp()) / "alive.txt"
        code = (
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, '-c', \"import time,pathlib\\nfor i in range(200):\\n time.sleep(0.1)\\n pathlib.Path(r'{marker}').write_text(str(i))\"])\n"
            "time.sleep(60)\n"
        )
        t0 = time.monotonic()
        r = self.py(code, timeout=1.5, grace=0.5)
        self.assertTrue(r.timed_out)
        self.assertLess(time.monotonic() - t0, 15)
        time.sleep(0.6)
        size1 = marker.read_text() if marker.exists() else ""
        time.sleep(0.6)
        size2 = marker.read_text() if marker.exists() else ""
        self.assertEqual(size1, size2, "grandchild kept running after timeout")

    def test_output_is_bounded(self):
        r = self.py("print('x' * 5_000_000)", timeout=30, max_output=20000)
        self.assertTrue(r.truncated)
        self.assertLess(len(r.stdout), 40000)

    def test_missing_binary_returns_error_not_exception(self):
        r = pc.run_process(["definitely-not-a-real-binary-xyz"], timeout=5)
        self.assertTrue(r.error)
        self.assertFalse(r.ok)

    def test_stdin(self):
        r = self.py("import sys; print(sys.stdin.read().upper())", timeout=30, input_text="abc")
        self.assertEqual(r.stdout.strip(), "ABC")

    def test_invalid_utf8_is_replaced(self):
        r = self.py("import sys; sys.stdout.buffer.write(b'\\xff\\xfeok')", timeout=30)
        self.assertIn("ok", r.stdout)


MOUNTINFO_WSL = """\
22 1 8:16 / / rw,relatime - ext4 /dev/sdb rw,discard
60 22 0:50 / /mnt/c rw,noatime - 9p C:\\134 rw,dirsync,aname=drvfs;path=C:\\;uid=1000;gid=1000
61 22 0:51 / /home/u/with\\040space rw - ext4 /dev/sdb rw
62 22 0:52 / /mnt/share rw - cifs //srv/x rw
"""


class FilesystemKind(unittest.TestCase):
    def test_wsl_drvfs_is_network(self):
        info = pc.parse_mountinfo(MOUNTINFO_WSL, "/mnt/c/Users/me/board.db")
        self.assertEqual(info["kind"], "network")
        self.assertTrue(info["wsl_drvfs"])

    def test_ext4_is_local_and_longest_prefix_wins(self):
        self.assertEqual(pc.parse_mountinfo(MOUNTINFO_WSL, "/home/u/x/board.db")["kind"], "local")
        self.assertEqual(pc.parse_mountinfo(MOUNTINFO_WSL, "/home/u/with space/board.db")["mount"], "/home/u/with space")
        self.assertEqual(pc.parse_mountinfo(MOUNTINFO_WSL, "/mnt/share/a")["kind"], "network")

    def test_prefix_not_confused_by_sibling_names(self):
        self.assertEqual(pc.parse_mountinfo(MOUNTINFO_WSL, "/mnt/cx/file")["kind"], "local")

    def test_real_path_does_not_raise(self):
        info = pc.filesystem_kind(tempfile.gettempdir())
        self.assertIn(info["kind"], ("local", "network", "unknown"))
        info = pc.filesystem_kind(os.path.join(tempfile.gettempdir(), "does", "not", "exist.db"))
        self.assertIn("kind", info)


class FileHelpers(unittest.TestCase):
    def test_atomic_write_and_overwrite(self):
        d = Path(tempfile.mkdtemp())
        p = d / "sub" / "f.json"
        pc.atomic_write_text(p, "one")
        pc.atomic_write_text(p, "two")
        self.assertEqual(p.read_text(), "two")
        self.assertEqual([x.name for x in p.parent.iterdir()], ["f.json"])

    def test_safe_rmtree_removes_readonly(self):
        d = Path(tempfile.mkdtemp()) / "t"
        d.mkdir()
        f = d / "ro.txt"
        f.write_text("x")
        pc.make_tree_readonly(d)
        pc.safe_rmtree(d)
        self.assertFalse(d.exists())

    def test_is_relative_to(self):
        base = tempfile.mkdtemp()
        self.assertTrue(pc.is_relative_to(os.path.join(base, "a", "b"), base))
        self.assertFalse(pc.is_relative_to(base + "x", base))
        self.assertFalse(pc.is_relative_to(os.path.dirname(base), base))


if __name__ == "__main__":
    unittest.main()
