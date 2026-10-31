"""M4 tests: miniyaml - strict YAML subset parser/emitter."""
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import miniyaml as y  # noqa: E402

SPEC_EXAMPLE = '''\
# Example: .planner/tickets/PLT-4092-ESCROW-SETTLEMENT.yaml
ticket_meta:
  id: "PLT-4092-ESCROW-SETTLEMENT"
  epoch: 14
  lawgiver_signature: "sha256:7f8a9b..."
  touches:
    - "packages/api/src/domain/escrow/settle.pure.ts"
    - "packages/db/migrations/20261025_escrow.sql"

shared_contracts:
  scip_symbols:
    - symbol: "scip-ts npm @planner/core 1.0.0 domain/escrow#SettlementCommand."
      signature: |
        export interface SettlementCommand {
          readonly idempotencyKey: string & { readonly __brand: 'UUIDv7' };
          readonly expectedVersion: number;
        }

worker_scopes:
  worker_db:
    worker_id: "AG2"
    allowed_paths: ["packages/db/migrations/*.sql"]   # flow list with a trailing comment
    forbidden_ast: ["FLOAT", "DOUBLE PRECISION"]
    formal_invariants:
      - "CHECK (balance_minor >= 0 AND version >= 1)"
  worker_backend:
    worker_id: "AG5"
    formal_invariants:
      engine: "fast-check"
      properties:
        - "INV-01: settlePure(state, cmd).status == 'COMMITTED' ==> state.balance - cmd.amount == result.newBalance"
  worker_ui:
    kinematic_spec:
      spring_damping_zeta: 0.82
      target_apca_lc: 75.0
      optical_trim: "@capsizecss/core"
  worker_qa_guard:
    forbidden_calls: ["jest.mock", "vi.mock", "sinon.stub"]
    mutation_gate:
      min_kill_score: 0.92
      target_diff_only: true
'''


class Parsing(unittest.TestCase):
    def test_spec_example_parses_exactly(self):
        d = y.loads(SPEC_EXAMPLE)
        self.assertEqual(d["ticket_meta"]["epoch"], 14)
        self.assertEqual(d["ticket_meta"]["touches"][1], "packages/db/migrations/20261025_escrow.sql")
        sig = d["shared_contracts"]["scip_symbols"][0]["signature"]
        self.assertTrue(sig.startswith("export interface SettlementCommand {\n  readonly idempotencyKey"))
        self.assertTrue(sig.endswith("}\n"))
        ws = d["worker_scopes"]
        self.assertEqual(ws["worker_db"]["allowed_paths"], ["packages/db/migrations/*.sql"])
        self.assertEqual(ws["worker_backend"]["formal_invariants"]["engine"], "fast-check")
        self.assertIn("==>", ws["worker_backend"]["formal_invariants"]["properties"][0])
        self.assertEqual(ws["worker_ui"]["kinematic_spec"], {"spring_damping_zeta": 0.82, "target_apca_lc": 75.0, "optical_trim": "@capsizecss/core"})
        self.assertIs(ws["worker_qa_guard"]["mutation_gate"]["target_diff_only"], True)
        self.assertEqual(ws["worker_qa_guard"]["mutation_gate"]["min_kill_score"], 0.92)

    def test_scalars_and_typing(self):
        d = y.loads("a: 1\nb: -2\nc: 1.5\nd: true\ne: false\nf: null\ng: ~\nh:\ni: 007\nj: 1.0.0\nk: yes\nl: 1e3\nm: 'it''s'\nn: \"tab\\there\"\no: -\n")
        self.assertEqual(d, {"a": 1, "b": -2, "c": 1.5, "d": True, "e": False, "f": None, "g": None, "h": None, "i": "007",
                             "j": "1.0.0", "k": "yes", "l": "1e3", "m": "it's", "n": "tab\there", "o": "-"})

    def test_comments_do_not_eat_quoted_hashes_or_glued_hashes(self):
        d = y.loads('a: "x # y"   # real comment\nb: c#d\nc: \'p # q\'\nd: [1, "#", 2] # tail\n')
        self.assertEqual(d, {"a": "x # y", "b": "c#d", "c": "p # q", "d": [1, "#", 2]})

    def test_flow_collections(self):
        d = y.loads('a: []\nb: {}\nc: [1, [2, 3], {k: v, "q r": [x]}]\nd: {a: 1, b: [true, null]}\ne: [ "a, b", c ]\n')
        self.assertEqual(d, {"a": [], "b": {}, "c": [1, [2, 3], {"k": "v", "q r": ["x"]}], "d": {"a": 1, "b": [True, None]}, "e": ["a, b", "c"]})

    def test_list_forms(self):
        d = y.loads("items:\n- a\n- b\nnested:\n  - - 1\n    - 2\n  - - 3\nmaps:\n  - k: 1\n    j: 2\n  -\n    z: 9\n  - {x: 1}\nempty:\n  -\n")
        self.assertEqual(d["items"], ["a", "b"])
        self.assertEqual(d["nested"], [[1, 2], [3]])
        self.assertEqual(d["maps"], [{"k": 1, "j": 2}, {"z": 9}, {"x": 1}])
        self.assertEqual(d["empty"], [None])

    def test_block_scalars(self):
        t = "a: |\n  one\n   two\n\n  three\nb: |-\n  x\n  y\nc: >\n  folded\n  text\n\n  para\nd: |+\n  keep\n\ne: after\n"
        d = y.loads(t)
        self.assertEqual(d["a"], "one\n two\n\nthree\n")
        self.assertEqual(d["b"], "x\ny")
        self.assertEqual(d["c"], "folded text\n\npara\n")
        self.assertEqual(d["d"], "keep\n\n")
        self.assertEqual(d["e"], "after")

    def test_block_scalar_keeps_hash_lines_and_colons(self):
        d = y.loads("sig: |\n  # not a comment\n  key: value\n  - not a list\nnext: 1\n")
        self.assertEqual(d["sig"], "# not a comment\nkey: value\n- not a list\n")
        self.assertEqual(d["next"], 1)

    def test_document_markers_and_empty(self):
        self.assertEqual(y.loads("---\na: 1\n...\n"), {"a": 1})
        self.assertIsNone(y.loads(""))
        self.assertIsNone(y.loads("# only a comment\n\n"))
        self.assertEqual(y.loads("42"), 42)
        self.assertEqual(y.loads("- 1\n- 2"), [1, 2])
        self.assertEqual(y.loads("\ufeffa: 1"), {"a": 1})
        self.assertEqual(y.loads("a: 1\r\nb: 2\r\n"), {"a": 1, "b": 2})

    def test_quoted_keys_and_colon_values(self):
        d = y.loads('"a b": 1\n\'c: d\': 2\nurl: http://x.y/z?q=1\ntime: 12:30\n')
        self.assertEqual(d, {"a b": 1, "c: d": 2, "url": "http://x.y/z?q=1", "time": "12:30"})

    def test_errors_are_loud_and_have_line_numbers(self):
        bad = {
            "tabs": "a:\n\tb: 1\n",
            "dup": "a: 1\na: 2\n",
            "dup flow": "a: {x: 1, x: 2}\n",
            "anchor": "a: &x 1\nb: *x\n",
            "alias": "b: *x\n",
            "tag": "a: !!str 1\n",
            "merge": "<<: {a: 1}\n",
            "multi doc": "a: 1\n---\nb: 2\n",
            "bad indent": "a:\n    b: 1\n  c: 2\n",
            "unterminated": 'a: "oops\n',
            "unterminated single": "a: 'oops\n",
            "multiline flow": "a: [1,\n  2]\n",
            "after quote": 'a: "x" y\n',
            "bad escape": 'a: "\\q"\n',
            "directive": "%YAML 1.2\na: 1\n",
            "junk": "a: 1\n  - b\n",
            "missing colon": "a: 1\nbare\n",
            "after end": "a: 1\n...\nb: 2\n",
        }
        for name, text in bad.items():
            with self.assertRaises(y.YamlError, msg=name) as cm:
                y.loads(text)
            if name not in ("multiline flow",):
                self.assertTrue(str(cm.exception), name)
        with self.assertRaises(y.YamlError) as cm:
            y.loads("a: 1\nb: 2\nb: 3\n")
        self.assertEqual(cm.exception.line, 3)

    def test_limits(self):
        with self.assertRaises(y.YamlError):
            y.loads("a: " + "x" * (y.MAX_BYTES + 1))
        deep = "\n".join("  " * i + "k:" for i in range(y.MAX_DEPTH + 5)) + " 1\n"
        with self.assertRaises(y.YamlError):
            y.loads(deep)
        with self.assertRaises(y.YamlError):
            y.loads("a: " + "[" * 200 + "]" * 200)
        with self.assertRaises(TypeError):
            y.loads(b"a: 1")


class Dumping(unittest.TestCase):
    def test_spec_example_roundtrips(self):
        d = y.loads(SPEC_EXAMPLE)
        text = y.dumps(d)
        self.assertEqual(y.loads(text), d)
        self.assertIn("signature: |\n", text)
        self.assertEqual(y.dumps(y.loads(text)), text, "dump is a fixed point")

    def test_strings_that_look_like_other_types_are_quoted(self):
        for s in ["true", "False", "null", "~", "yes", "No", "on", "off", "1", "-5", "1.5", "0.50", "", " lead", "trail ", "a: b", "a #b",
                  "- x", "? q", "[a]", "{a}", "#c", "&a", "*b", "!t", "|", ">", "'q'", '"q"', "%d", "@x", "`b`", "---", "...", "<<", "tab\there", "line\nbreak", "a:", "-"]:
            text = y.dumps({"k": s, "l": [s]})
            self.assertEqual(y.loads(text), {"k": s, "l": [s]}, repr(s))

    def test_plain_strings_stay_plain(self):
        t = y.dumps({"path": "packages/db/migrations/*.sql", "ver": "1.0.0", "sha": "sha256:abc", "u": "http://x/y", "name": "a-b c"})
        self.assertNotIn('"', t)

    def test_floats_stay_floats(self):
        for f in [0.0, -0.5, 75.0, 1e20, 1e-7, 123456789.123456789, 5e-324]:
            r = y.loads(y.dumps({"f": f}))["f"]
            self.assertIsInstance(r, float, f)
            self.assertEqual(r, f)
        with self.assertRaises(ValueError):
            y.dumps({"f": float("nan")})
        with self.assertRaises(ValueError):
            y.dumps([float("inf")])

    def test_bool_is_not_int(self):
        r = y.loads(y.dumps({"a": True, "b": 1, "c": False, "d": 0}))
        self.assertIs(r["a"], True)
        self.assertIs(r["c"], False)
        self.assertEqual(type(r["b"]), int)
        self.assertEqual(type(r["d"]), int)

    def test_empty_containers_and_nesting(self):
        data = {"a": [], "b": {}, "c": [[], {}, [[1, [2, []]]]], "d": {"e": {"f": []}}, "g": [{"h": [], "i": {}}], "j": None}
        self.assertEqual(y.loads(y.dumps(data)), data)
        for top in ([], {}, "x", 3, None, True, [1], [[1]], [{"a": 1}], "line1\nline2"):
            self.assertEqual(y.loads(y.dumps(top)), top, repr(top))

    def test_multiline_strings(self):
        for s in ["a\nb", "a\nb\n", "a\n\nb\n", "  indented\nx", "x\n  y\n", "tail space \nz", "\nleading", "x\n\n", "a\n \nb", "#h\nk: v\n- i\n", "uni\u2028code\nz", "cr\r\nlf"]:
            data = {"s": s, "l": [s, {"k": s}]}
            self.assertEqual(y.loads(y.dumps(data)), data, repr(s))

    def test_unicode_and_odd_keys(self):
        data = {"héllo": "wörld ✓", "日本": ["語"], "a b": 1, "c:d": 2, "": 3, "1": 4, "true": 5, "x\ny": 6, "[k]": 7, "k#": 8}
        self.assertEqual(y.loads(y.dumps(data)), data)

    def test_sort_keys_and_errors(self):
        self.assertEqual(list(y.loads(y.dumps({"b": 1, "a": {"d": 1, "c": 2}}, sort_keys=True)).keys()), ["a", "b"])
        self.assertTrue(y.dumps({"b": 1, "a": 2}).startswith("b: 1"))
        with self.assertRaises(TypeError):
            y.dumps({1: "x"})
        with self.assertRaises(TypeError):
            y.dumps({"a": object()})
        with self.assertRaises(TypeError):
            y.dumps({"a": {1, 2}})
        a = []
        a.append(a)
        with self.assertRaises(ValueError):
            y.dumps(a)

    def test_file_helpers_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.yaml"
            y.dump_file(p, {"a": [1, 2], "b": "x"})
            self.assertEqual(y.load_file(p), {"a": [1, 2], "b": "x"})


class PropertyBased(unittest.TestCase):
    ALPHABET = list("abcXYZ019 _-:#,.[]{}'\"\\/@*&!|>%`~?\t\n") + ["é", "日", "\u2028", "\x00", "\x7f", "yes", "null", "true", "- ", ": ", " #"]

    def rand_str(self, rnd):
        return "".join(rnd.choice(self.ALPHABET) for _ in range(rnd.randint(0, 12)))

    def rand_val(self, rnd, depth=0):
        k = rnd.random()
        if depth > 3 or k < 0.45:
            c = rnd.randint(0, 6)
            return [None, True, False, rnd.randint(-10**6, 10**6), rnd.uniform(-1e6, 1e6), self.rand_str(rnd), rnd.choice([0.0, 1.0, 1e21, 1e-9])][c]
        if k < 0.7:
            return [self.rand_val(rnd, depth + 1) for _ in range(rnd.randint(0, 4))]
        return {self.rand_str(rnd): self.rand_val(rnd, depth + 1) for _ in range(rnd.randint(0, 4))}

    def test_roundtrip_fuzz(self):
        rnd = random.Random(20261025)
        for i in range(1500):
            v = self.rand_val(rnd)
            text = y.dumps(v)
            try:
                back = y.loads(text)
            except y.YamlError as e:  # pragma: no cover
                self.fail(f"iteration {i}: {e}\n{text!r}\nvalue={v!r}")
            self.assertEqual(json.dumps(back, sort_keys=True), json.dumps(v, sort_keys=True), f"iteration {i}\n{text!r}")
            self.assertEqual(y.dumps(back), text, f"iteration {i}: dump is not a fixed point")

    def test_parser_never_raises_anything_but_yamlerror(self):
        rnd = random.Random(7)
        pieces = ["a", ": ", "- ", "  ", "\n", "[", "]", "{", "}", ",", "'", '"', "#", "|", ">", "&", "*", "!", "\t", "é", "0", "-"]
        for _ in range(3000):
            text = "".join(rnd.choice(pieces) for _ in range(rnd.randint(0, 30)))
            try:
                y.loads(text)
            except y.YamlError:
                pass
            except RecursionError:  # pragma: no cover
                self.fail(f"recursion on {text!r}")


if __name__ == "__main__":
    unittest.main()
