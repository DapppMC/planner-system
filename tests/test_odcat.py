"""M1 tests for tools/odcat.py using the mock Open Design fixture."""
import io, json, os, shutil, sys, tempfile, unittest, contextlib, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import odcat  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "open-design")


class OdcatTests(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.out, True)

    def build(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return odcat.build(FIX, self.out)

    def test_build_skips_underscore_and_missing_design_md(self):
        self.assertEqual(self.build(), 7)
        self.assertFalse(os.path.exists(os.path.join(self.out, "cards", "_template.md")))
        self.assertFalse(os.path.exists(os.path.join(self.out, "cards", "no-design-md.md")))

    def test_facets_within_350_tokens(self):
        self.build()
        with open(os.path.join(self.out, "FACETS.md"), encoding="utf8") as fh:
            text = fh.read()
        self.assertLessEqual(odcat.est_tokens(text), 350)
        self.assertIn("Systems Total:** 7", text)

    def test_mode_detection_dark_and_light(self):
        self.build()
        with open(os.path.join(self.out, "catalog.jsonl"), encoding="utf8") as fh:
            recs = {json.loads(l)["id"]: json.loads(l) for l in fh}
        self.assertEqual(recs["linear-like"]["mode"], "dark")
        self.assertEqual(recs["paper-mono"]["mode"], "light")
        self.assertEqual(recs["data-grid"]["family"], "product-ui")

    def test_pure_black_is_dark(self):
        self.assertEqual(odcat.mode_of(odcat.lum("#000000")), "dark")
        self.assertEqual(odcat.mode_of(None), "unknown")

    def test_search_filters(self):
        self.build()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            res = odcat.search(self.out, fam="editorial", mode="light", limit=8)
        self.assertEqual({r["id"] for r in res}, {"paper-mono", "sunday-news"})
        with contextlib.redirect_stdout(io.StringIO()):
            res = odcat.search(self.out, query="neon")
        self.assertEqual([r["id"] for r in res], ["neon-pop"])

    def test_card_exists_and_small(self):
        self.build()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertTrue(odcat.card(self.out, "braun-warm"))
        self.assertLessEqual(odcat.est_tokens(buf.getvalue()), 250)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(odcat.card(self.out, "nope"))

    def test_search_without_build_fails_cleanly(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(odcat.search(self.out))

    def test_cli_help_exits_zero(self):
        for args in (["search", "--help"], ["build", "--help"], ["card", "--help"]):
            r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "odcat.py")] + args,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_cli_build_defaults_to_bundled_subset_and_rejects_bad_od(self):
        env = dict(os.environ); env.pop("OPEN_DESIGN_DIR", None)
        tool = os.path.join(ROOT, "tools", "odcat.py")
        r = subprocess.run([sys.executable, tool, "build", "--out", self.out], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Ingested 14", r.stdout)
        r = subprocess.run([sys.executable, tool, "build", "--od", self.out, "--out", self.out], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 2)


class ConstitutionTests(unittest.TestCase):
    def test_constitution_within_budget_and_layers(self):
        with open(os.path.join(ROOT, "design", "DESIGN_CONSTITUTION.md"), encoding="utf8") as fh:
            t = fh.read()
        self.assertLessEqual(odcat.est_tokens(t), 1500)
        for k in ("LAYER 0", "LAYER 1", "LAYER 2", "LAYER 3", "LAYER 4", "R_o = R_i + p"):
            self.assertIn(k, t)

    def test_tokens_css_has_archetypes_and_vars(self):
        with open(os.path.join(ROOT, "design", "tokens.css"), encoding="utf8") as fh:
            t = fh.read()
        for k in ("--bg-canvas", "--border-subtle", "industrial-warm", "editorial", "tabular-nums"):
            self.assertIn(k, t)


if __name__ == "__main__":
    unittest.main()
