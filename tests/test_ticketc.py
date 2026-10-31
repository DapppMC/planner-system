"""M5 tests for tools/ticketc.py."""
import hashlib, os, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import ticketc  # noqa: E402

TOOL = os.path.join(ROOT, "tools", "ticketc.py")


def sh(cwd, *cmd):
    subprocess.check_call(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ticket(frozen="{}", **over):
    f = dict(id="T-001", goal="Add slicer", allowed=["src/**", "tests/test_slicer.py"], forbidden=["tests/golden/**"],
             protected=["tests/golden/test_case1.py"], acceptance=["python -m unittest tests/test_slicer.py"], stop=["a golden test fails twice"])
    f.update(over)
    lst = lambda xs: "\n".join(f"  - {x}" for x in xs) if xs else " []"
    def block(name, xs):
        return f"{name}:\n" + "\n".join(f"  - {x}" for x in xs) if xs else f"{name}: []"
    fr = "frozen: {}" if frozen == "{}" else "frozen:\n" + "\n".join(f"  {k}: {v}" for k, v in frozen.items())
    return "\n".join([f"id: {f['id']}", f"goal: {f['goal']}  # comment", fr, block("allowed_paths", f["allowed"]),
                      block("forbidden_paths", f["forbidden"]), block("protected_tests", f["protected"]),
                      block("acceptance", f["acceptance"]), block("stop_if", f["stop"]), ""])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def w(self, rel, text):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf8", newline="\n") as fh:
            fh.write(text)
        return p

    def errs(self, text):
        return ticketc.validate_data(ticketc.parse_yaml(text), self.tmp)


class Parser(Base):
    def test_parses_scalars_lists_maps_comments(self):
        d = ticketc.parse_yaml(ticket(frozen={"a/b.txt": "abcdef12"}))
        self.assertEqual(d["id"], "T-001")
        self.assertEqual(d["goal"], "Add slicer")
        self.assertEqual(d["frozen"], {"a/b.txt": "abcdef12"})
        self.assertEqual(d["allowed_paths"], ["src/**", "tests/test_slicer.py"])

    def test_empty_inline_collections_and_quotes(self):
        d = ticketc.parse_yaml('a: []\nb: {}\nc: "x # y"\nd:\n  - "q: r"\n')
        self.assertEqual((d["a"], d["b"], d["c"], d["d"]), ([], {}, "x # y", ["q: r"]))

    def test_rejects_duplicate_keys_and_bad_lines(self):
        for bad in ("a: 1\na: 2\n", "  indented: 1\n", "just words\n"):
            with self.assertRaises(ticketc.TicketError):
                ticketc.parse_yaml(bad)


class Validation(Base):
    def test_conforming_ticket_passes_and_cli_exits_0(self):
        p = self.w("T-001.yml", ticket())
        r = subprocess.run([sys.executable, TOOL, "validate", p, "--root", self.tmp], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_missing_each_required_field_is_rejected(self):
        for f in ticketc.REQUIRED_FIELDS:
            d = ticketc.parse_yaml(ticket()); d.pop(f)
            self.assertIn(f"Missing required field: {f}", ticketc.validate_data(d, self.tmp))

    def test_missing_or_empty_acceptance_rejected(self):
        self.assertTrue(any("Acceptance" in e or "acceptance" in e for e in self.errs(ticket(acceptance=[]))))

    def test_empty_stop_if_and_allowed_rejected(self):
        self.assertTrue(any("stop_if" in e for e in self.errs(ticket(stop=[]))))
        self.assertTrue(any("allowed_paths" in e for e in self.errs(ticket(allowed=[]))))

    def test_too_broad_and_escaping_paths_rejected(self):
        self.assertTrue(any("too broad" in e for e in self.errs(ticket(allowed=["**"]))))
        self.assertTrue(any("relative" in e for e in self.errs(ticket(allowed=["../x/**"]))))
        self.assertTrue(any("relative" in e for e in self.errs(ticket(forbidden=["/etc/**"]))))

    def test_protected_test_inside_allowed_rejected(self):
        self.assertTrue(any("protected test" in e for e in self.errs(ticket(protected=["src/test_a.py"]))))

    def test_frozen_hash_ok_then_broken(self):
        p = self.w("tests/golden/case1.json", '{"x": 1}')
        with open(p, "rb") as fh:
            good = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(self.errs(ticket(frozen={"tests/golden/case1.json": good[:16]})), [])
        self.assertTrue(any("hash broken" in e for e in self.errs(ticket(frozen={"tests/golden/case1.json": "deadbeef00"}))))
        self.assertTrue(any("not hex" in e for e in self.errs(ticket(frozen={"tests/golden/case1.json": "zzz"}))))
        self.assertTrue(any("missing" in e for e in self.errs(ticket(frozen={"nope.txt": "deadbeef00"}))))
        with open(p, "w") as fh:
            fh.write("tampered")
        self.assertTrue(any("changed" in e for e in self.errs(ticket(frozen={"tests/golden/case1.json": good[:16]}))))

    def test_cli_rejects_broken_ticket(self):
        p = self.w("bad.yml", ticket(acceptance=[]))
        r = subprocess.run([sys.executable, TOOL, "validate", p, "--root", self.tmp], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL", r.stderr)


class Globbing(unittest.TestCase):
    def test_semantics(self):
        m = ticketc.matches
        self.assertTrue(m("src/a/b.ts", ["src/**"]))
        self.assertTrue(m("src/b.ts", ["src/*"]))
        self.assertFalse(m("src/a/b.ts", ["src/*"]))
        self.assertTrue(m("a/b/test_x.py", ["**/test_*.py"]))
        self.assertTrue(m("test_x.py", ["**/test_*.py"]))
        self.assertFalse(m("srcx/a.ts", ["src/**"]))
        self.assertTrue(m("src\\a.ts", ["src/**"]))


class DiffGuard(Base):
    def setUp(self):
        super().setUp()
        sh(self.tmp, "git", "init", "-q")
        sh(self.tmp, "git", "config", "user.email", "t@example.com")
        sh(self.tmp, "git", "config", "user.name", "t")
        self.w("src/app.py", "x = 1\n")
        self.w("tests/golden/case1.json", "{}")
        self.w("README.md", "hi\n")
        sh(self.tmp, "git", "add", "-A")
        sh(self.tmp, "git", "commit", "-q", "-m", "init")
        self.allowed, self.forbidden = ["src/**"], ["tests/golden/**"]

    def test_clean_tree_and_in_scope_edit_pass(self):
        self.assertTrue(ticketc.verify_diff(self.allowed, self.forbidden, self.tmp))
        self.w("src/app.py", "x = 2\n")
        self.w("src/new_module.py", "y = 1\n")  # untracked but in scope
        self.assertTrue(ticketc.verify_diff(self.allowed, self.forbidden, self.tmp))

    def test_leak_outside_allowed_paths_caught(self):
        self.w("README.md", "changed\n")
        with self.assertRaises(PermissionError) as c:
            ticketc.verify_diff(self.allowed, self.forbidden, self.tmp)
        self.assertIn("outside allowed_paths: README.md", str(c.exception))

    def test_untracked_leak_caught(self):
        self.w("scripts/evil.sh", "echo\n")
        with self.assertRaises(PermissionError):
            ticketc.verify_diff(self.allowed, self.forbidden, self.tmp)

    def test_forbidden_beats_allowed(self):
        self.w("tests/golden/case1.json", '{"a": 1}')
        with self.assertRaises(PermissionError) as c:
            ticketc.verify_diff(["**/*.json", "tests/**"], self.forbidden, self.tmp)
        self.assertIn("forbidden", str(c.exception))

    def test_staged_changes_also_caught(self):
        self.w("README.md", "staged\n")
        sh(self.tmp, "git", "add", "README.md")
        with self.assertRaises(PermissionError):
            ticketc.verify_diff(self.allowed, self.forbidden, self.tmp)

    def test_cli_diff_exit_codes(self):
        p = self.w("../ticket.yml", ticket(allowed=["src/**"]))
        p = os.path.abspath(p)
        r = subprocess.run([sys.executable, TOOL, "diff", p, "--repo", self.tmp], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.w("README.md", "leak\n")
        r = subprocess.run([sys.executable, TOOL, "diff", p, "--repo", self.tmp], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("README.md", r.stderr)

    def test_cli_hash(self):
        r = subprocess.run([sys.executable, TOOL, "hash", os.path.join(self.tmp, "README.md")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn(hashlib.sha256(b"hi\n").hexdigest(), r.stdout)


if __name__ == "__main__":
    unittest.main()
