"""M3 tests: recipe YAML structure (always) and matches against fixtures (needs the ast-grep binary)."""
import glob, json, os, re, shutil, subprocess, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECIPES = os.path.join(ROOT, "tools", "sgrecipes")
FIX = os.path.join(ROOT, "tests", "fixtures", "sg")

EXPECTED = {  # recipe -> (fixture, expected 1-based match lines)
    "find_api_routes": ("routes.ts", [4, 5, 6]),
    "extract_sql_queries": ("sql.ts", [1, 2, 3]),
    "find_deprecated_calls": ("deprecated.ts", [1, 2, 3, 5]),
}


def find_astgrep():
    """Return a path to a real ast-grep binary, or None. On Linux `sg` can be a different tool, so verify."""
    cands = [shutil.which("ast-grep"), shutil.which("sg"),
             os.path.expanduser("~/.local/bin/ast-grep"), os.path.expanduser("~/.local/bin/sg")]
    for c in cands:
        if not c or not os.path.exists(c):
            continue
        try:
            out = subprocess.run([c, "--version"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if "ast-grep" in out.lower() or re.match(r"^(ast-grep|sg)\s+\d", out.lower()):
            return c
    return None


def top_keys(text):
    return {m.group(1) for m in re.finditer(r"^([A-Za-z_][\w-]*):", text, re.M)}


class RecipeStructure(unittest.TestCase):
    def test_three_recipes_exist(self):
        names = {os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(RECIPES, "*.yml"))}
        self.assertEqual(names, set(EXPECTED))

    def test_yaml_has_required_keys_and_no_tabs(self):
        for p in glob.glob(os.path.join(RECIPES, "*.yml")):
            with open(p, encoding="utf8") as fh:
                text = fh.read()
            self.assertNotIn("\t", text, p)
            keys = top_keys(text)
            self.assertTrue({"id", "language", "rule"} <= keys, f"{p}: {keys}")
            rid = re.search(r"^id:\s*(\S+)", text, re.M).group(1)
            self.assertEqual(rid, os.path.basename(p)[:-4].replace("_", "-"))
            self.assertRegex(text, r"(?m)^\s+(any|pattern|all):")

    def test_fixtures_exist(self):
        for _, (fx, _) in EXPECTED.items():
            self.assertTrue(os.path.exists(os.path.join(FIX, fx)), fx)


class RecipeMatching(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bin = find_astgrep()

    def lines(self, recipe, fixture):
        r = subprocess.run([self.bin, "scan", "-r", os.path.join(RECIPES, recipe + ".yml"),
                            os.path.join(FIX, fixture), "--json=compact"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return sorted(m["range"]["start"]["line"] + 1 for m in json.loads(r.stdout or "[]"))

    def test_each_recipe_matches_exactly_the_expected_lines(self):
        if not self.bin:
            self.skipTest("ast-grep binary not installed (pip install ast-grep-cli / npm i -g @ast-grep/cli)")
        for recipe, (fx, want) in EXPECTED.items():
            with self.subTest(recipe=recipe):
                self.assertEqual(self.lines(recipe, fx), want)

    def test_recipes_do_not_match_unrelated_fixture(self):
        if not self.bin:
            self.skipTest("ast-grep binary not installed")
        self.assertEqual(self.lines("find_api_routes", "sql.ts"), [])
        self.assertEqual(self.lines("extract_sql_queries", "routes.ts"), [])


if __name__ == "__main__":
    unittest.main()
