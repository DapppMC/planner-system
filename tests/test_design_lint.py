import json, os, subprocess, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import design_lint as dl
FX = os.path.join(ROOT, "tests", "fixtures", "design")


def rd(n):
    with open(os.path.join(FX, n), encoding="utf8") as f:
        return f.read()


def status(rep, rid):
    return next(r["status"] for r in rep["results"] if r["id"] == rid)


def wrap(css, body="<p>x</p>"):
    return "<html><head><style>%s</style></head><body>%s</body></html>" % (css, body)


class Fixtures(unittest.TestCase):
    def test_good_passes_strict(self):
        rep = dl.lint(rd("good.html"), strict=True)
        self.assertTrue(rep["ok"], rep["failed"])
        self.assertEqual(status(rep, "R02"), "pass")
        self.assertEqual(status(rep, "R03"), "pass")

    def test_bad_fails_every_antislop_rule(self):
        rep = dl.lint(rd("bad.html"))
        for rid in ("R01", "R03", "R04", "R05", "R06", "R07", "R08", "R02"):
            self.assertEqual(status(rep, rid), "fail", rid)

    def test_cli_exit_codes(self):
        run = lambda *a: subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "design_lint.py"), *a],
                                        capture_output=True, text=True)
        self.assertEqual(run(os.path.join(FX, "good.html"), "--strict").returncode, 0)
        r = run(os.path.join(FX, "bad.html"), "--json")
        self.assertEqual(r.returncode, 1)
        self.assertFalse(json.loads(r.stdout)["ok"])
        self.assertEqual(run(os.path.join(FX, "missing.html")).returncode, 2)


class Colors(unittest.TestCase):
    def test_banned_hex_and_hue(self):
        self.assertEqual(status(dl.lint(wrap("a{color:#6366f1}")), "R01"), "fail")
        self.assertEqual(status(dl.lint(wrap("a{color:hsl(270,70%,50%)}")), "R01"), "fail")
        self.assertEqual(status(dl.lint(wrap("a{color:#2f9e8f}")), "R01"), "pass")

    def test_tailwind_class(self):
        self.assertEqual(status(dl.lint(wrap("", '<b class="from-purple-600">x</b>')), "R01"), "fail")

    def test_allow_hue(self):
        self.assertEqual(status(dl.lint(wrap("a{color:#6366f1}"), allow_hue=True), "R01"), "pass")


class Radius(unittest.TestCase):
    css = ".o{padding:12px;border-radius:%s}.o .i{border-radius:%s}"
    body = '<div class="o"><div class="i">x</div></div>'

    def test_concentric_ok(self):
        self.assertEqual(status(dl.lint(wrap(self.css % ("20px", "8px"), self.body)), "R02"), "pass")

    def test_non_concentric_fails(self):
        rep = dl.lint(wrap(self.css % ("12px", "12px"), self.body))
        self.assertEqual(status(rep, "R02"), "fail")

    def test_var_calc_resolution(self):
        css = ":root{--p:12px;--ri:8px;--ro:calc(var(--ri) + var(--p))}.o{padding:var(--p);border-radius:var(--ro)}.i{border-radius:var(--ri)}"
        self.assertEqual(status(dl.lint(wrap(css, self.body)), "R02"), "pass")

    def test_na_vs_strict(self):
        self.assertEqual(status(dl.lint(wrap("p{margin:0}")), "R02"), "na")
        self.assertEqual(status(dl.lint(wrap("p{margin:0}"), strict=True), "R02"), "fail")


class Numbers(unittest.TestCase):
    body = "<table><tr><td>1204</td><td>38</td><td>9.5</td></tr></table>"

    def test_missing_tabular_nums(self):
        self.assertEqual(status(dl.lint(wrap("td{color:#111}", self.body)), "R03"), "fail")

    def test_inherited_tabular_nums(self):
        self.assertEqual(status(dl.lint(wrap("table{font-variant-numeric:tabular-nums}", self.body)), "R03"), "pass")

    def test_mono_font_accepted(self):
        self.assertEqual(status(dl.lint(wrap("td{font-family:ui-monospace,monospace}", self.body)), "R03"), "pass")

    def test_js_rendered_table_needs_rule(self):
        js = "<table id=t></table><script>t.innerHTML='<td class=\"num\">'+n+'</td>'</script>"
        self.assertEqual(status(dl.lint(wrap("td{color:#111}", js)), "R03"), "fail")
        self.assertEqual(status(dl.lint(wrap(".num{font-variant-numeric:tabular-nums}", js)), "R03"), "pass")


    def test_js_rendered_cells_with_mono_class_accepted(self):
        js = "<table id=t></table><script>t.innerHTML='<td class=\\\"mono\\\">'+n+'</td>'</script>"
        self.assertEqual(status(dl.lint(wrap(":root{--m:ui-monospace,monospace}.mono{font-family:var(--m)}", js)), "R03"), "pass")


class Motion(unittest.TestCase):
    def test_transition_all_fails(self):
        self.assertEqual(status(dl.lint(wrap("a{transition:all .2s}")), "R05"), "fail")

    def test_transform_opacity_ok(self):
        self.assertEqual(status(dl.lint(wrap("a{transition:transform .2s,opacity .2s}")), "R05"), "pass")

    def test_keyframes_width_fails(self):
        self.assertEqual(status(dl.lint(wrap("@keyframes g{from{width:0}to{width:9px}}")), "R05"), "fail")


class Misc(unittest.TestCase):
    def test_backdrop_on_card_fails_on_header_ok(self):
        self.assertEqual(status(dl.lint(wrap(".card{backdrop-filter:blur(4px)}")), "R04"), "fail")
        self.assertEqual(status(dl.lint(wrap("header{backdrop-filter:blur(4px)}")), "R04"), "pass")

    def test_contrast(self):
        self.assertEqual(status(dl.lint(wrap("p{color:#bbb;background:#fff}")), "R08"), "fail")
        self.assertEqual(status(dl.lint(wrap("p{color:#222;background:#fff}")), "R08"), "pass")

    def test_deterministic(self):
        a = json.dumps(dl.lint(rd("bad.html")), sort_keys=True)
        self.assertEqual(a, json.dumps(dl.lint(rd("bad.html")), sort_keys=True))


if __name__ == "__main__":
    unittest.main()
