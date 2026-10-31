"""Iteration 2 tests for tools/cartograph.py (Gate -2 in one command)."""
import json, os, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import cartograph  # noqa: E402
from test_sgrecipes import find_astgrep  # noqa: E402

TOOL = os.path.join(ROOT, "tools", "cartograph.py")
FIX = os.path.join(ROOT, "tests", "fixtures", "legacy-certs")
REQUIRED_KEYS = {"repo_root", "entrypoints", "top_dependencies", "orphan_files", "db_schema", "env_contract"}


def w(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf8", newline="\n") as fh:
        fh.write(text)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True)


class LegacyFixture(Base):
    @classmethod
    def setUpClass(cls):
        cls.res, _ = cartograph.cartograph(FIX)

    def test_gate_minus2_schema_keys(self):
        self.assertTrue(REQUIRED_KEYS <= set(self.res))
        self.assertEqual(self.res["db_schema"].keys(), {"tables"})

    def test_entrypoints_are_the_three_routes_with_handlers_and_lines(self):
        got = [(e["type"], e["method"], e["route"], e["handler"], e["path"], e["line"]) for e in self.res["entrypoints"]]
        self.assertEqual(got, [
            ("http_route", "GET", "/certs", "listCerts", "src/server.ts", 8),
            ("http_route", "POST", "/certs/slice", "handleSliceCert", "src/server.ts", 9),
            ("http_route", "DELETE", "/certs/:id", "removeCert", "src/server.ts", 10),
        ])

    def test_orphan_tables_env_deprecated(self):
        self.assertEqual(self.res["orphan_files"], ["src/legacy/old_ocr.ts"])
        self.assertEqual(self.res["db_schema"]["tables"], ["certificates", "employees", "logs"])
        self.assertEqual(self.res["env_contract"], ["DATABASE_URL", "JWT_SECRET", "PORT"])
        self.assertEqual([(d["path"], d["pattern"]) for d in self.res["deprecated_calls"]], [("src/legacy/old_ocr.ts", "require('request')")])

    def test_top_dependencies_ranked_with_scores(self):
        syms = {d["symbol"] for d in self.res["top_dependencies"]}
        self.assertTrue({"parseCertificate", "normalizeEmployeeId", "query", "requireAuth"} <= syms)
        self.assertNotIn("runOcr", syms)
        scores = [d["pagerank_score"] for d in self.res["top_dependencies"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(0 < s <= 1 for s in scores))

    def test_env_values_never_leak(self):
        text = json.dumps(self.res)
        for secret in ("change-me", "user:pass", "localhost/certs"):
            self.assertNotIn(secret, text)

    def test_no_behaviour_claims_only_structure(self):
        self.assertTrue(any("Structure only" in x for x in self.res["limits"]))


class Determinism(Base):
    def test_two_runs_are_byte_identical_and_have_no_timestamp(self):
        a, b = os.path.join(self.tmp, "a.json"), os.path.join(self.tmp, "b.json")
        self.assertEqual(self.run_cli(FIX, "--out", a).returncode, 0)
        self.assertEqual(self.run_cli(FIX, "--out", b).returncode, 0)
        with open(a, "rb") as fa, open(b, "rb") as fb:
            da, db = fa.read(), fb.read()
        self.assertEqual(da, db)
        self.assertNotRegex(da.decode(), r"20\d\d-\d\d-\d\d")

    def test_cli_exit_codes_and_repomap_out(self):
        out, rm = os.path.join(self.tmp, "c.json"), os.path.join(self.tmp, "map.txt")
        r = self.run_cli(FIX, "--out", out, "--repomap-out", rm)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("3 entrypoints", r.stdout)
        with open(rm, encoding="utf8") as fh:
            self.assertIn("parseCertificate", fh.read())
        r = self.run_cli(os.path.join(self.tmp, "nope"), "--out", out)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a directory", r.stderr)


class Scanners(Base):
    def test_python_repo_flask_fastapi_main_worker_event_env_sql(self):
        w(self.tmp, "app/web.py", 'import os\nfrom flask import Flask\napp = Flask(__name__)\nDB = os.environ["DB_URL"]\nKEY = os.environ.get("API_KEY")\n\n'
                                  '@app.route("/health", methods=["GET", "POST"])\ndef health():\n    return "ok"\n\n'
                                  'if __name__ == "__main__":\n    app.run()\n')
        w(self.tmp, "app/api.py", 'from fastapi import APIRouter\nrouter = APIRouter()\n\n@router.post("/items")\nasync def create_item():\n    return 1\n')
        w(self.tmp, "app/jobs.py", 'from celery import shared_task\n\n@shared_task\ndef nightly_sync():\n    q = """\n    SELECT id\n    FROM invoices i\n    JOIN customers c ON c.id = i.customer_id\n    """\n    return q\n')
        w(self.tmp, "app/signals.py", 'from django.dispatch import receiver\n\n@receiver(post_save)\ndef on_saved(sender, **kw):\n    pass\n')
        w(self.tmp, "db/schema.sql", "CREATE TABLE IF NOT EXISTS audit_log (id int);\n")
        res, _ = cartograph.cartograph(self.tmp)
        got = {(e["type"], e.get("route") or e.get("event") or e["handler"], e["handler"]) for e in res["entrypoints"]}
        self.assertIn(("http_route", "/health", "health"), got)
        self.assertIn(("http_route", "/items", "create_item"), got)
        self.assertIn(("cli", "__main__", "__main__"), got)
        self.assertIn(("worker", "nightly_sync", "nightly_sync"), got)
        self.assertIn(("event", "post_save", "on_saved"), got)
        methods = {e["route"]: e["method"] for e in res["entrypoints"] if e["type"] == "http_route"}
        self.assertEqual(methods["/health"], "GET|POST")
        self.assertEqual(methods["/items"], "POST")
        self.assertEqual(res["env_contract"], ["API_KEY", "DB_URL"])
        self.assertEqual(res["db_schema"]["tables"], ["audit_log", "customers", "invoices"])

    def test_prose_is_not_sql_and_comments_are_ignored(self):
        w(self.tmp, "src/a.ts", '// app.get("/commented", h)\nconst a = "Update your profile settings now";\nconst b = "Select a plan from the list below";\n')
        res, _ = cartograph.cartograph(self.tmp)
        self.assertEqual(res["entrypoints"], [])
        self.assertEqual(res["db_schema"]["tables"], [])
        self.assertEqual(res["sql_calls"], [])

    def test_skip_dirs_are_ignored(self):
        w(self.tmp, "node_modules/x/index.js", 'app.get("/ghost", ghost)\nprocess.env.GHOST_KEY\n')
        w(self.tmp, "src/ok.js", "process.env.REAL_KEY\n")
        res, _ = cartograph.cartograph(self.tmp)
        self.assertEqual(res["env_contract"], ["REAL_KEY"])
        self.assertEqual(res["entrypoints"], [])

    def test_dynamic_loading_downgrades_orphan_to_unknown(self):
        w(self.tmp, "src/plugin.js", "function loadedByName() { return 1 }\n")
        w(self.tmp, "src/loader.js", "const m = require(path.join(dir, name));\nexports.load = () => m;\n")
        res, _ = cartograph.cartograph(self.tmp)
        self.assertIn("src/plugin.js", res["orphan_files"])
        self.assertEqual(res["dynamic_dispatch_files"], ["src/loader.js"])
        self.assertIn("unknown", res["orphan_notes"]["src/plugin.js"])

    def test_tests_and_entry_names_are_never_orphans(self):
        w(self.tmp, "src/main.py", "def start():\n    pass\n")
        w(self.tmp, "tests/test_x.py", "def test_thing():\n    pass\n")
        w(self.tmp, "src/util.py", "def lonely_unused_helper():\n    pass\n")
        res, _ = cartograph.cartograph(self.tmp)
        self.assertEqual(res["orphan_files"], ["src/util.py"])


class Noise(Base):
    """Iteration 2 polish: findings from running the tool on its own repository."""

    def test_entrypoints_in_nonprod_folders_are_not_counted(self):
        w(self.tmp, "src/app.py", "from flask import Flask\napp = Flask(__name__)\n@app.route('/real')\ndef real():\n    pass\n")
        for d in ("tests", "fixtures", "docs", "samples", "src/__tests__"):
            w(self.tmp, d + "/demo.py", "@app.route('/fake')\ndef fake():\n    pass\n\nif __name__ == '__main__':\n    fake()\n")
        res, _ = cartograph.cartograph(self.tmp)
        self.assertEqual([(e["type"], e.get("route"), e["path"]) for e in res["entrypoints"]], [("http_route", "/real", "src/app.py")])
        self.assertEqual(res["stats"]["entrypoint_scan_skipped_files"], 5)
        self.assertTrue(any("ignore these folders" in x for x in res["limits"]))

    def test_include_nonprod_flag_restores_them(self):
        w(self.tmp, "tests/demo.py", "@app.route('/fake')\ndef fake():\n    pass\n")
        res, _ = cartograph.cartograph(self.tmp, include_nonprod=True)
        self.assertEqual([e["path"] for e in res["entrypoints"]], ["tests/demo.py"])

    def test_nonprod_only_matches_folder_names_not_file_names(self):
        w(self.tmp, "src/tests_helper.py", "@app.route('/ok')\ndef ok():\n    pass\n")
        w(self.tmp, "src/docsy.py", "@app.route('/ok2')\ndef ok2():\n    pass\n")
        res, _ = cartograph.cartograph(self.tmp)
        self.assertEqual(len(res["entrypoints"]), 2)

    def test_generic_symbols_do_not_dominate_top_dependencies(self):
        w(self.tmp, "svc/util.py", "class Thing:\n    def __init__(self):\n        pass\n\ndef get(x):\n    return x\n\ndef add(a):\n    return a\n\ndef save(a):\n    return a\n\ndef fmt(a):\n    return a\n")
        w(self.tmp, "svc/parser.py", "def normalize_employee_id(raw):\n    return raw\n")
        for i in range(4):
            w(self.tmp, f"app/u{i}.py", "from svc.util import get, add, save, fmt, Thing\nfrom svc.parser import normalize_employee_id\n"
              f"def run{i}():\n    get(1); add(1); save(1); fmt(1); Thing()\n    return normalize_employee_id(1)\n")
        res, _ = cartograph.cartograph(self.tmp)
        names = [d["symbol"] for d in res["top_dependencies"]]
        self.assertIn("normalize_employee_id", names)
        self.assertNotIn("__init__", names)
        for generic in ("get", "add", "save", "fmt"):
            self.assertNotIn(generic, names)

    def test_deprecated_pattern_inside_strings_and_comments_is_ignored(self):
        w(self.tmp, "scanner.py",
          'PATTERNS = [(r"\\bfs\\.exists\\s*\\(", "fs.exists()"), (r"datetime.utcnow(", "datetime.utcnow()")]\n'
          '"""docstring mentions new Buffer(10) and url.parse(x)"""\n'
          "# datetime.utcnow() in a comment\n"
          "import datetime\n"
          "now = datetime.utcnow()\n")
        w(self.tmp, "legacy.js",
          "const msg = 'do not call fs.exists(p)';\n"
          "/* url.parse(old) */\n"
          "const b = new Buffer(8);\n"
          "const r = require('request');\n"
          "if (fs.exists(p)) { run(); } // fs.exists( again in a comment\n")
        res, _ = cartograph.cartograph(self.tmp)
        got = sorted((d["path"], d["line"], d["pattern"]) for d in res["deprecated_calls"])
        self.assertEqual(got, [("legacy.js", 3, "new Buffer()"), ("legacy.js", 4, "require('request')"),
                               ("legacy.js", 5, "fs.exists()"), ("scanner.py", 5, "datetime.utcnow()")])

    def test_scanning_the_scanner_finds_no_false_deprecated(self):
        res, _ = cartograph.cartograph(os.path.join(ROOT, "tools"))
        self.assertEqual([d for d in res["deprecated_calls"] if d["path"] == "cartograph.py"], [])

    def test_code_mask_handles_multiline_strings_and_escapes(self):
        t = 'a = "x\\"y"  # c\nb = """l1\nl2"""\nc = 1\n'
        m = cartograph.code_mask(t, ".py")
        self.assertTrue(m[t.index("a =")])
        self.assertFalse(m[t.index("y")])
        self.assertFalse(m[t.index("c\n")])
        self.assertFalse(m[t.index("l2")])
        self.assertTrue(m[t.index("c = 1")])

    def test_builtin_method_names_are_generic(self):
        self.assertTrue({"append", "join", "split", "strip"} <= cartograph.GENERIC_SYMBOLS)

    def test_ignore_option_adds_folders(self):
        w(self.tmp, "scripts/tool.py", "if __name__ == '__main__':\n    pass\n")
        w(self.tmp, "src/pkg/web.py", "@app.get('/x')\ndef x():\n    pass\n")
        res, _ = cartograph.cartograph(self.tmp)
        self.assertEqual(len(res["entrypoints"]), 2)
        res, _ = cartograph.cartograph(self.tmp, ignore=["scripts"])
        self.assertEqual([e["path"] for e in res["entrypoints"]], ["src/pkg/web.py"])
        self.assertTrue(any("scripts" in x for x in res["limits"]))
        out = os.path.join(self.tmp, "o.json")
        self.assertEqual(cartograph.main([self.tmp, "--out", out, "--ignore", "scripts"]), 0)
        with open(out, encoding="utf8") as fh:
            self.assertEqual(len(json.load(fh)["entrypoints"]), 1)

    def test_dunder_main_module_is_a_cli_entrypoint(self):
        w(self.tmp, "src/pkg/__main__.py", "from .cli import main\nraise SystemExit(main())\n")
        res, _ = cartograph.cartograph(self.tmp)
        self.assertEqual([(e["type"], e["path"], e["line"]) for e in res["entrypoints"]], [("cli", "src/pkg/__main__.py", 1)])

    def test_top_dependencies_ignore_docs_copies_and_test_referrers(self):
        w(self.tmp, "src/core/matcher.py", "def match_employee_name(a):\n    return a\n")
        w(self.tmp, "src/app/use.py", "from core.matcher import match_employee_name\n\ndef go():\n    return match_employee_name(1)\n")
        for i in range(6):
            w(self.tmp, f"tests/test_{i}.py", "from core.matcher import match_employee_name\n\ndef test_a():\n    match_employee_name(1)\n")
        w(self.tmp, "docs/spec/scripts/make_fixtures.py", "def build_fixture_bundle():\n    pass\n")
        for i in range(8):
            w(self.tmp, f"docs/spec/use{i}.py", "from make_fixtures import build_fixture_bundle\n\ndef r():\n    build_fixture_bundle()\n")
        res, _ = cartograph.cartograph(self.tmp)
        got = [(d["symbol"], d["file"], d["referenced_by"]) for d in res["top_dependencies"]]
        self.assertEqual(got, [("match_employee_name", "src/core/matcher.py", 1)])


class Engines(Base):
    def test_ast_grep_missing_falls_back_with_warning(self):
        orig = cartograph.find_astgrep
        cartograph.find_astgrep = lambda: None
        try:
            res, _ = cartograph.cartograph(FIX, engine="ast-grep")
        finally:
            cartograph.find_astgrep = orig
        self.assertEqual(res["engine"], "regex")
        self.assertIn("fell back", res["warnings"][0])

    def test_ast_grep_engine_adds_multiline_routes_and_otherwise_matches_regex(self):
        if not find_astgrep():
            self.skipTest("ast-grep binary not installed")
        regex, _ = cartograph.cartograph(FIX)
        sg, _ = cartograph.cartograph(FIX, engine="ast-grep")
        self.assertEqual(sg["engine"], "ast-grep")
        for res in (regex, sg):
            res.pop("engine"); res.pop("ast_grep", None)
        self.assertEqual(regex, sg)
        w(self.tmp, "src/server.ts", 'import express from "express";\nconst app = express();\napp.get(\n  "/multi",\n  auth,\n  listMulti\n);\n')
        r_regex, _ = cartograph.cartograph(self.tmp)
        r_sg, _ = cartograph.cartograph(self.tmp, engine="ast-grep")
        self.assertEqual(r_regex["entrypoints"], [])
        self.assertEqual([(e["route"], e["handler"], e["line"]) for e in r_sg["entrypoints"]], [("/multi", "listMulti", 3)])


if __name__ == "__main__":
    unittest.main()
