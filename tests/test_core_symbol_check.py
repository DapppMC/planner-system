"""core/symbol_check.py and the Python SCIP symbol builder: locked signatures, drift, stubs."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import scip_symbols as scip  # noqa: E402
from core import symbol_check as sc  # noqa: E402


def ts_sym(file, name, kind="type"):
    return scip.typescript_symbol("demo", "1.0.0", file, name, kind)


def py_sym(file, name, kind="function"):
    return scip.python_symbol("demo", "1.0.0", file, name, kind)


class PythonSymbolBuilder(unittest.TestCase):
    def test_function_and_class(self):
        f = py_sym("src/bucket.py", "bucket", "function")
        c = py_sym("src/models.py", "User", "class")
        self.assertTrue(f.startswith("scip-python python demo 1.0.0 "))
        self.assertTrue(f.endswith("bucket()."), f)
        self.assertTrue(c.endswith("User#"), c)

    def test_round_trip_through_the_parser(self):
        s = scip.parse_symbol(py_sym("pkg/sub/mod.py", "thing", "function"))
        self.assertEqual(sc.symbol_name(s), "thing")
        self.assertIn("pkg/sub/mod.py", sc.path_hints(s) + ["pkg/sub/mod.py"])

    def test_init_module_and_stub_suffix(self):
        s = py_sym("pkg/__init__.py", "f")
        self.assertIn("pkg/f().", s)
        self.assertIn("`pkg.mod`", py_sym("pkg/mod.pyi", "f"))

    def test_bare_init_has_no_module(self):
        with self.assertRaises(scip.SymbolError):
            scip.python_symbol("demo", "1", "__init__.py", "f")

    def test_empty_path(self):
        with self.assertRaises(scip.SymbolError):
            scip.python_symbol("demo", "1", "", "f")

    def test_windows_separators_are_normalised(self):
        self.assertEqual(py_sym("src\\bucket.py", "bucket"), py_sym("src/bucket.py", "bucket"))


class TypeScriptScanner(unittest.TestCase):
    def test_tokens_ignore_comments_and_whitespace(self):
        a = sc.ts_tokens("export interface A {\n  id: string; // the id\n  /* x */ n: number,\n}")
        b = sc.ts_tokens("export interface A { id: string  n: number }")
        self.assertEqual(a, b)

    def test_strings_are_atomic(self):
        self.assertIn("notacomment", "".join(sc.ts_tokens("const x = 'a // not a comment';")).replace(" ", ""))
        self.assertEqual(len(sc.ts_tokens("x = 'a;b'")), len(sc.ts_tokens("x = 'c;d'")))

    def test_declaration_extraction(self):
        text = "import x from 'y';\n\nexport interface User {\n  id: string;\n  tags: { a: number }[];\n}\n\nexport type Id = string;\n"
        decl, kind, line = sc.ts_declaration(text, "User")
        self.assertEqual((kind, line), ("interface", 3))
        self.assertTrue(decl.rstrip().endswith("}"))
        self.assertNotIn("Id", decl)
        decl2, kind2, _ = sc.ts_declaration(text, "Id")
        self.assertEqual(kind2, "type")
        self.assertIn("string", decl2)
        self.assertIsNone(sc.ts_declaration(text, "Nope"))

    def test_name_prefix_is_not_a_match(self):
        self.assertIsNone(sc.ts_declaration("export interface UserX { a: 1 }", "User"))

    def test_multiline_type_alias(self):
        text = "export type Shape =\n  | { kind: 'a' }\n  | { kind: 'b' };\n\nexport const k = 1;\n"
        decl, _, _ = sc.ts_declaration(text, "Shape")
        self.assertIn("'b'", decl)
        self.assertNotIn("const k", decl)

    def test_function_lock_is_a_signature_prefix(self):
        locked = "export function add(a: number, b: number): number"
        self.assertTrue(sc.ts_matches(locked, "export function add(a: number, b: number): number {\n  return a + b;\n}", "function"))
        self.assertFalse(sc.ts_matches(locked, "export function add(a: number, b: string): number { return 0 }", "function"))
        self.assertFalse(sc.ts_matches(locked, "export function add(a: number): number { return 0 }", "function"))

    def test_interface_must_match_exactly(self):
        locked = "export interface A { id: string }"
        self.assertTrue(sc.ts_matches(locked, "export interface A {\n  id: string;\n}", "interface"))
        self.assertFalse(sc.ts_matches(locked, "export interface A { id: string; extra?: number }", "interface"))
        self.assertFalse(sc.ts_matches(locked, "export interface A { id: number }", "interface"))


class PythonScanner(unittest.TestCase):
    def test_interface_ignores_body_and_docstring(self):
        found = sc.py_declaration('def f(a: int, b: str = "x") -> bool:\n    """doc"""\n    return True\n', "f")
        self.assertIsNotNone(found)
        node, line = found
        self.assertEqual(line, 1)
        self.assertTrue(sc.py_matches('def f(a: int, b: str = "x") -> bool:', node, "f"))

    def test_signature_changes_are_drift(self):
        node, _ = sc.py_declaration("def f(a: int) -> bool:\n    return True\n", "f")
        self.assertFalse(sc.py_matches("def f(a: str) -> bool:", node, "f"))
        self.assertFalse(sc.py_matches("def f(a: int, b: int) -> bool:", node, "f"))
        self.assertFalse(sc.py_matches("def f(a: int) -> int:", node, "f"))

    def test_class_interface(self):
        text = "class A:\n    x: int\n\n    def m(self, y: int) -> int:\n        return y\n"
        node, _ = sc.py_declaration(text, "A")
        self.assertTrue(sc.py_matches("class A:\n    x: int\n\n    def m(self, y: int) -> int: ...", node, "A"))
        self.assertFalse(sc.py_matches("class A:\n    x: str\n\n    def m(self, y: int) -> int: ...", node, "A"))

    def test_unparseable_lock_is_none(self):
        node, _ = sc.py_declaration("def f(): pass\n", "f")
        self.assertIsNone(sc.py_matches("def f(:::", node, "f"))

    def test_missing_name(self):
        self.assertIsNone(sc.py_declaration("def g(): pass\n", "f"))


class Locate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def put(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    SIG = "def bucket(n: int) -> str:"

    def test_found_python(self):
        self.put("src/bucket.py", "def bucket(n: int) -> str:\n    return 'x'\n")
        st = sc.locate(self.root, py_sym("src/bucket.py", "bucket"), self.SIG, ["src/bucket.py"])
        self.assertEqual(st.status, sc.FOUND, st)
        self.assertEqual(st.path, "src/bucket.py")

    def test_drift_python(self):
        self.put("src/bucket.py", "def bucket(n: int, extra: int = 0) -> str:\n    return 'x'\n")
        st = sc.locate(self.root, py_sym("src/bucket.py", "bucket"), self.SIG, ["src/bucket.py"])
        self.assertEqual(st.status, sc.DRIFT)

    def test_missing_python(self):
        self.put("src/bucket.py", "x = 1\n")
        st = sc.locate(self.root, py_sym("src/bucket.py", "bucket"), self.SIG, ["src/bucket.py"])
        self.assertEqual(st.status, sc.MISSING)

    def test_drift_cannot_hide_behind_an_untouched_stub(self):
        self.put("contracts/T/bucket.py", self.SIG + "\n")  # the stub still matches
        self.put("src/bucket.py", "def bucket(n: str) -> str:\n    return n\n")  # the real file drifted
        st = sc.locate(self.root, py_sym("src/bucket.py", "bucket"), self.SIG, ["contracts/T/bucket.py"])
        self.assertEqual(st.status, sc.DRIFT, st)

    def test_hinted_file_is_canonical_when_candidates_disagree(self):
        self.put("src/bucket.py", "def bucket(n: int) -> str:\n    return 'x'\n")
        self.put("other/bucket.py", "def bucket(n: bytes) -> str:\n    return 'x'\n")
        st = sc.locate(self.root, py_sym("src/bucket.py", "bucket"), self.SIG, ["other/bucket.py"])
        self.assertEqual(st.status, sc.FOUND, st)

    def test_typescript_found_and_drift(self):
        sym = ts_sym("src/types.ts", "User", "interface")
        sig = "export interface User { id: string }"
        self.put("src/types.ts", "export interface User {\n  id: string;\n}\n")
        self.assertEqual(sc.locate(self.root, sym, sig, ["src/types.ts"]).status, sc.FOUND)
        self.put("src/types.ts", "export interface User {\n  id: number;\n}\n")
        self.assertEqual(sc.locate(self.root, sym, sig, ["src/types.ts"]).status, sc.DRIFT)

    def test_language_mismatch_is_not_searched(self):
        self.put("src/bucket.ts", "export function bucket(n: number): string { return '' }\n")
        st = sc.locate(self.root, py_sym("src/bucket.py", "bucket"), self.SIG, ["src/bucket.ts"])
        self.assertEqual(st.status, sc.MISSING)

    def test_unparsable_symbol_and_unknown_language(self):
        self.assertEqual(sc.locate(self.root, "garbage", self.SIG, []).status, sc.UNSUPPORTED)
        st = sc.locate(self.root, ts_sym("a.ts", "X"), "message X { int32 a = 1; }", [])
        self.assertEqual(st.status, sc.UNSUPPORTED)

    def test_oversized_and_unreadable_candidates_are_skipped(self):
        st = sc.locate(self.root, py_sym("src/bucket.py", "bucket"), self.SIG, ["nope.py", "../outside.py"])
        self.assertEqual(st.status, sc.MISSING)

    def test_windows_style_candidate_paths(self):
        self.put("src/bucket.py", "def bucket(n: int) -> str:\n    return 'x'\n")
        st = sc.locate(self.root, py_sym("src/bucket.py", "bucket"), self.SIG, ["src\\bucket.py"])
        self.assertEqual(st.status, sc.FOUND)


class Stubs(unittest.TestCase):
    def doc(self):
        return {"ticket_meta": {"id": "T-1"},
                "shared_contracts": {"scip_symbols": [
                    {"symbol": py_sym("src/bucket.py", "bucket"), "signature": "def bucket(n: int) -> str:"},
                    {"symbol": ts_sym("src/types.ts", "User", "interface"), "signature": "export interface User { id: string }"}]}}

    def test_materialize_writes_each_signature_once(self):
        with tempfile.TemporaryDirectory() as d:
            made = sc.materialize_stubs(self.doc(), d)
            self.assertEqual(sorted(made), ["contracts/T-1/User.ts", "contracts/T-1/bucket.py"])
            py = (Path(d) / "contracts/T-1/bucket.py").read_text()
            self.assertIn("def bucket(n: int) -> str:", py)
            self.assertTrue(py.startswith("# Generated by AG1"))
            self.assertTrue((Path(d) / "contracts/T-1/User.ts").read_text().startswith("// Generated by AG1"))
            (Path(d) / "contracts/T-1/bucket.py").write_text("edited")
            self.assertEqual(sc.materialize_stubs(self.doc(), d), [], "existing stubs are never overwritten")
            self.assertEqual((Path(d) / "contracts/T-1/bucket.py").read_text(), "edited")

    def test_stub_path_sanitises_names(self):
        p = sc.stub_path("../evil id", py_sym("src/b.py", "b"), "def b(): ...")
        self.assertFalse(p.startswith("/"))
        self.assertTrue(p.startswith("contracts/"))
        self.assertNotIn(" ", p)

    def test_check_contract_symbols(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "src").mkdir()
            (root / "src/bucket.py").write_text("def bucket(n: int) -> str:\n    return 'x'\n")
            (root / "src/types.ts").write_text("export interface User { id: number }\n")
            res = sc.check_contract_symbols(self.doc(), root, ["src/bucket.py", "src/types.ts"])
            self.assertEqual([r.status for r in res], [sc.FOUND, sc.DRIFT])


if __name__ == "__main__":
    unittest.main()
