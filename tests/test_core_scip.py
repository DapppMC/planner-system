"""M4 tests: SCIP symbol parsing, building and locking."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import scip_symbols as sc  # noqa: E402


class Parse(unittest.TestCase):
    def test_typescript_style_symbol(self):
        s = sc.parse_symbol("scip-typescript npm @planner/core 1.0.0 domain/escrow/`settle.pure.ts`/SettlementCommand#")
        self.assertEqual((s.scheme, s.manager, s.package, s.version), ("scip-typescript", "npm", "@planner/core", "1.0.0"))
        self.assertEqual([(d.name, d.kind) for d in s.descriptors],
                         [("domain", "namespace"), ("escrow", "namespace"), ("settle.pure.ts", "namespace"), ("SettlementCommand", "type")])
        self.assertEqual(s.last_kind, "type")

    def test_all_descriptor_kinds(self):
        s = sc.parse_symbol("scip-python pypi pkg 0.1 mod/Cls#method().field.meta:macro![T](arg)")
        self.assertEqual([d.kind for d in s.descriptors], ["namespace", "type", "method", "term", "meta", "macro", "type-parameter", "parameter"])
        m = sc.parse_symbol("x y z 1 A#m(2).")
        self.assertEqual(m.descriptors[1].disambiguator, "2")

    def test_prototype_example_is_grammatical_but_a_term(self):
        s = sc.parse_symbol("scip-ts npm @planner/core 1.0.0 domain/escrow#SettlementCommand.")
        self.assertEqual(s.last_kind, "term")
        warn = sc.check_kind(s, "export interface SettlementCommand { a: string }")
        self.assertIn("types end in '#'", warn)

    def test_escaped_spaces_and_backticks(self):
        s = sc.parse_symbol("my  scheme npm pkg  name 1.0 `we``ird name`#")
        self.assertEqual(s.scheme, "my scheme")
        self.assertEqual(s.package, "pkg name")
        self.assertEqual(s.descriptors[0].name, "we`ird name")

    def test_local_symbols(self):
        s = sc.parse_symbol("local 42")
        self.assertTrue(s.is_local)
        with self.assertRaises(sc.SymbolError):
            sc.parse_symbol("local ")

    def test_rejects_garbage(self):
        for bad in ["", "   ", "only-one-part", "a b c d", "a b c d e", "a b c 1 name", "a b c 1 name(", "a b c 1 name(x)", "a b c 1 `unterminated#",
                    "a b c 1 ``#", "a b c 1 []#", "a b c 1 a#\nb#", "a b c 1 a b#", " a b c 1 a#", "a b c 1 a#\t", "a b c 1 é#", 5, None]:
            with self.assertRaises(sc.SymbolError, msg=repr(bad)):
                sc.parse_symbol(bad)


class Build(unittest.TestCase):
    def test_build_roundtrip(self):
        text = sc.build_symbol("scip-typescript", "npm", "@planner/core", "1.0.0", [("domain", "namespace"), ("Cmd", "type"), ("run", "method"), ("x", "term")])
        s = sc.parse_symbol(text)
        self.assertEqual([d.kind for d in s.descriptors], ["namespace", "type", "method", "term"])

    def test_build_escapes_odd_names(self):
        text = sc.build_symbol("s", "m", "p k", "1", [("a b", "type")])
        self.assertEqual(sc.parse_symbol(text).descriptors[0].name, "a b")
        self.assertEqual(sc.parse_symbol(text).package, "p k")

    def test_empty_tokens_become_dot(self):
        self.assertTrue(sc.build_symbol("s", "", "", "", [("a", "type")]).startswith("s . . . "))

    def test_typescript_symbol_helper(self):
        t = sc.typescript_symbol("@planner/core", "1.0.0", "packages/api/src/domain/escrow/types.ts", "SettlementCommand")
        self.assertEqual(t, "scip-typescript npm @planner/core 1.0.0 packages/api/src/domain/escrow/`types.ts`/SettlementCommand#")
        self.assertEqual(sc.parse_symbol(t).descriptors[-1].kind, "type")
        with self.assertRaises(sc.SymbolError):
            sc.typescript_symbol("p", "1", "", "X")
        with self.assertRaises(sc.SymbolError):
            sc.build_symbol("s", "m", "p", "1", [("a", "nonsense")])


class Locks(unittest.TestCase):
    def test_lock_ignores_whitespace_noise_but_not_content(self):
        a = "export interface A {\n  readonly x: string;\n}\n"
        b = "export interface A {  \r\n  readonly x: string;\r\n}\r\n\r\n"
        c = "export interface A {\n  readonly x: number;\n}\n"
        self.assertEqual(sc.signature_lock(a), sc.signature_lock(b))
        self.assertNotEqual(sc.signature_lock(a), sc.signature_lock(c))
        self.assertTrue(sc.signature_lock(a).startswith("sha256:"))
        self.assertNotEqual(sc.signature_lock("a  b"), sc.signature_lock("a b"), "inner whitespace is significant")

    def test_check_kind(self):
        t = sc.parse_symbol("s m p 1 A#")
        self.assertIsNone(sc.check_kind(t, "export interface A {}"))
        self.assertIsNone(sc.check_kind(t, "no declaration here"))
        self.assertIn("function", sc.check_kind(t, "export function f(): void"))
        self.assertIn("value", sc.check_kind(t, "export const x = 1"))
        self.assertIsNone(sc.check_kind(sc.parse_symbol("s m p 1 f()."), "export function f(): void"))
        self.assertIsNone(sc.check_kind(sc.parse_symbol("local 1"), "export interface A {}"))


if __name__ == "__main__":
    unittest.main()
