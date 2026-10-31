"""Iteration 2: the curated Open Design subset ships inside catalog/ (no external clone needed)."""
import contextlib, io, json, os, re, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import odcat, design_lint  # noqa: E402

SRC = os.path.join(ROOT, "catalog", "source")
CAT = os.path.join(ROOT, "catalog")


def rb(p):
    with open(p, "rb") as fh:
        return fh.read()


def load(p):
    with open(p, encoding="utf8") as fh:
        return json.load(fh)


class Bundled(unittest.TestCase):
    def setUp(self):
        self.cur = load(os.path.join(SRC, "curation.json"))

    def test_between_10_and_15_systems(self):
        n = len(self.cur["systems"])
        self.assertTrue(10 <= n <= 15, n)

    def test_every_system_has_the_three_files(self):
        for sid in self.cur["systems"]:
            for fn in self.cur["source"]["files_copied"]:
                self.assertTrue(os.path.isfile(os.path.join(SRC, "design-systems", sid, fn)), f"{sid}/{fn}")

    def test_no_unlisted_systems_shipped(self):
        on_disk = set(os.listdir(os.path.join(SRC, "design-systems")))
        self.assertEqual(on_disk, set(self.cur["systems"]))

    def test_license_and_notice_present(self):
        with open(os.path.join(SRC, "LICENSE"), encoding="utf8") as fh:
            self.assertIn("Apache License", fh.read())
        with open(os.path.join(SRC, "NOTICE"), encoding="utf8") as fh:
            n = fh.read()
        self.assertIn("e03d5c23", n)
        self.assertIn("Apache", n)

    def test_archetypes_cover_the_brief(self):
        used = {v["archetype"] for v in self.cur["systems"].values()}
        self.assertTrue({"editorial", "minimal", "workspace-light", "precision-dark", "industrial-warm"} <= used)
        self.assertTrue(used <= set(self.cur["archetypes"]))

    def test_shipped_catalog_matches_bundled_source(self):
        with open(os.path.join(CAT, "catalog.jsonl"), encoding="utf8") as fh:
            recs = [json.loads(l) for l in fh]
        self.assertEqual({r["id"] for r in recs}, set(self.cur["systems"]))
        for r in recs:
            self.assertTrue(os.path.isfile(os.path.join(CAT, "cards", r["id"] + ".md")))

    def test_card_files_match_records_exactly(self):
        self.assertEqual({f[:-3] for f in os.listdir(os.path.join(CAT, "cards"))}, set(self.cur["systems"]))

    def test_rebuild_removes_stale_cards(self):
        out = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, out, True)
        os.makedirs(os.path.join(out, "cards"))
        with open(os.path.join(out, "cards", "ghost.md"), "w") as fh:
            fh.write("old")
        with contextlib.redirect_stdout(io.StringIO()):
            odcat.build(SRC, out)
        self.assertFalse(os.path.exists(os.path.join(out, "cards", "ghost.md")))

    def test_rebuild_is_deterministic_and_default_needs_no_argument(self):
        out1, out2 = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out1, True); self.addCleanup(shutil.rmtree, out2, True)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(odcat.main(["build", "--out", out1]), 0)
            odcat.main(["build", "--out", out2])
        for fn in ("catalog.jsonl", "FACETS.md"):
            self.assertEqual(rb(os.path.join(out1, fn)), rb(os.path.join(out2, fn)))
        self.assertEqual(rb(os.path.join(out1, "catalog.jsonl")), rb(os.path.join(CAT, "catalog.jsonl")),
                         "shipped catalog is stale: run odcat.py build")

    def test_facets_within_budget(self):
        with open(os.path.join(CAT, "FACETS.md"), encoding="utf8") as fh:
            self.assertLessEqual(odcat.est_tokens(fh.read()), 350)

    def test_cards_small(self):
        for fn in os.listdir(os.path.join(CAT, "cards")):
            with open(os.path.join(CAT, "cards", fn), encoding="utf8") as fh:
                self.assertLessEqual(odcat.est_tokens(fh.read()), 250, fn)

    def test_search_by_archetype(self):
        with contextlib.redirect_stdout(io.StringIO()):
            res = odcat.search(CAT, archetype="precision-dark")
        self.assertEqual({r["id"] for r in res}, {"mission-control", "trading-terminal", "hud"})
        self.assertTrue(all(r["mode"] == "dark" for r in res))
        with contextlib.redirect_stdout(io.StringIO()):
            res = odcat.search(CAT, archetype="editorial", mode="light")
        self.assertIn("atelier-zero", {r["id"] for r in res})

    def test_search_matches_use_when(self):
        with contextlib.redirect_stdout(io.StringIO()):
            res = odcat.search(CAT, query="order books")
        self.assertEqual([r["id"] for r in res], ["trading-terminal"])

    def test_bundled_tokens_follow_our_own_antislop_color_rule(self):
        for sid in self.cur["systems"]:
            with open(os.path.join(SRC, "design-systems", sid, "tokens.css"), encoding="utf8") as fh:
                rep = design_lint.lint("<html><body></body></html>", fh.read())
            r01 = next(r for r in rep["results"] if r["id"] == "R01")
            self.assertEqual(r01["status"], "pass", f"{sid}: {r01['evidence']}")

    def test_bundled_files_have_no_urls_or_injection_phrases(self):
        bad = re.compile(r"ignore (all |previous|prior)|system prompt|<script|curl |wget ", re.I)
        for root, _, files in os.walk(os.path.join(SRC, "design-systems")):
            for fn in files:
                with open(os.path.join(root, fn), encoding="utf8", errors="ignore") as fh:
                    self.assertIsNone(bad.search(fh.read()), os.path.join(root, fn))

    def test_curate_copies_only_listed_files(self):
        fake = tempfile.mkdtemp(); into = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, fake, True); self.addCleanup(shutil.rmtree, into, True)
        for sid in self.cur["systems"]:
            d = os.path.join(fake, "design-systems", sid); os.makedirs(d)
            for fn in ("manifest.json", "DESIGN.md", "tokens.css", "extra.html"):
                with open(os.path.join(d, fn), "w") as fh:
                    fh.write(fn)
        shutil.copyfile(os.path.join(SRC, "curation.json"), os.path.join(into, "curation.json"))
        with contextlib.redirect_stdout(io.StringIO()):
            got = odcat.curate(fake, into)
        self.assertEqual(len(got), len(self.cur["systems"]))
        self.assertFalse(os.path.exists(os.path.join(into, "design-systems", got[0], "extra.html")))


if __name__ == "__main__":
    unittest.main()
