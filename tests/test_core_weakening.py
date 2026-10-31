"""M1 tests: weakening detection (every known Goodhart bypass) for Python and JS/TS."""
import os, shutil, sys, tempfile, textwrap, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.verification import weakening as w  # noqa: E402

BASE = textwrap.dedent('''
    import math, pytest, unittest

    def test_total():
        r = compute(3)
        assert r == 9
        assert r > 0

    def test_float():
        assert compute(0.1) == pytest.approx(0.3, rel=1e-9)
        assert math.isclose(compute(1), 3.0, rel_tol=1e-9)

    @pytest.mark.parametrize("x", [1, 2, 3, 4])
    def test_param(x):
        assert compute(x) == x * 3

    class TestK(unittest.TestCase):
        def test_eq(self):
            self.assertEqual(compute(2), 6)
            self.assertAlmostEqual(compute(0.5), 1.5, places=7)
''')


def codes(after, before=BASE, rel="tests/test_x.py"):
    return sorted({v.code for v in w.compare_texts(before, after, rel) if v.severity == "violation"})


class PythonBypasses(unittest.TestCase):
    def test_identical_is_clean(self):
        self.assertEqual(codes(BASE), [])

    def test_adding_tests_and_asserts_is_fine(self):
        self.assertEqual(codes(BASE + "\ndef test_new():\n    assert 1 + 1 == 2\n    assert compute(1) == 3\n"), [])

    def test_deleted_assert(self):
        self.assertIn("ASSERT_DECREASED", codes(BASE.replace("    assert r > 0\n", "")))

    def test_assert_true(self):
        self.assertIn("ASSERT_DECREASED", codes(BASE.replace("assert r == 9", "assert True")))
        self.assertIn("VACUOUS_ADDED", codes(BASE.replace("assert r == 9", "assert True")))

    def test_or_true(self):
        self.assertIn("VACUOUS_ADDED", codes(BASE.replace("assert r == 9", "assert r == 9 or True")))

    def test_self_comparison(self):
        self.assertIn("VACUOUS_ADDED", codes(BASE.replace("assert r == 9", "assert r == r")))

    def test_operator_weakened(self):
        self.assertIn("STRENGTH_DECREASED", codes(BASE.replace("assert r == 9", "assert r >= 9")))
        self.assertIn("STRENGTH_DECREASED", codes(BASE.replace("assert r == 9", "assert r")))

    def test_conjunct_dropped(self):
        before = "def test_a():\n    assert x == 1 and y == 2\n"
        after = "def test_a():\n    assert x == 1\n"
        self.assertIn("STRENGTH_DECREASED", codes(after, before))

    def test_try_except_swallow(self):
        after = BASE.replace("    assert r == 9\n", "    try:\n        assert r == 9\n    except AssertionError:\n        pass\n")
        c = codes(after)
        self.assertIn("SWALLOWED_ASSERT_ADDED", c)
        self.assertIn("ASSERT_DECREASED", c)

    def test_reraising_handler_is_not_a_swallow(self):
        after = BASE.replace("    assert r == 9\n", "    try:\n        assert r == 9\n    except AssertionError:\n        raise\n")
        self.assertEqual(codes(after), [])

    def test_if_false_dead_code(self):
        after = BASE.replace("    assert r == 9\n", "    if False:\n        assert r == 9\n")
        self.assertIn("DEAD_ASSERT_ADDED", codes(after))

    def test_assert_after_return(self):
        after = BASE.replace("    assert r == 9\n    assert r > 0\n", "    return\n    assert r == 9\n    assert r > 0\n")
        self.assertIn("DEAD_ASSERT_ADDED", codes(after))

    def test_contextlib_suppress(self):
        after = BASE.replace("    assert r == 9\n", "    with contextlib.suppress(Exception):\n        assert r == 9\n")
        self.assertIn("SWALLOWED_ASSERT_ADDED", codes(after))

    def test_skip_variants(self):
        for deco in ("@pytest.mark.skip\n", "@pytest.mark.skipif(True, reason='x')\n", "@pytest.mark.xfail\n", "@unittest.skip('x')\n"):
            self.assertIn("SKIP_ADDED", codes(BASE.replace("def test_total", deco + "def test_total")), deco)
        self.assertIn("SKIP_ADDED", codes(BASE.replace("    r = compute(3)\n", "    pytest.skip('later')\n    r = compute(3)\n")))
        self.assertIn("SKIP_ADDED", codes(BASE.replace("    r = compute(3)\n", "    pytest.importorskip('nothing')\n    r = compute(3)\n")))
        self.assertIn("SKIP_ADDED", codes(BASE.replace("    r = compute(3)\n", "    raise unittest.SkipTest('x')\n    r = compute(3)\n")))

    def test_module_level_skip(self):
        self.assertIn("SKIP_ADDED", codes("pytestmark = pytest.mark.skip\n" + BASE))
        self.assertIn("SKIP_ADDED", codes("pytest.skip('all', allow_module_level=True)\n" + BASE))

    def test_class_level_skip(self):
        self.assertIn("SKIP_ADDED", codes(BASE.replace("class TestK", "@unittest.skip('x')\nclass TestK")))

    def test_tolerance_relaxed(self):
        self.assertIn("TOL_RELAXED", codes(BASE.replace("rel=1e-9", "rel=1e-2")))
        self.assertIn("TOL_RELAXED", codes(BASE.replace("rel_tol=1e-9", "rel_tol=0.5")))
        self.assertIn("TOL_RELAXED", codes(BASE.replace("places=7", "places=1")))
        self.assertIn("TOL_RELAXED", codes(BASE.replace("rel=1e-9", "rel=10**-1")))

    def test_tolerance_tightened_is_fine(self):
        self.assertEqual(codes(BASE.replace("rel=1e-9", "rel=1e-12")), [])

    def test_tolerance_becomes_variable(self):
        self.assertIn("DYNAMIC_TOLERANCE", codes(BASE.replace("rel=1e-9", "rel=TOL")))

    def test_exact_replaced_by_approx(self):
        self.assertIn("APPROX_ADDED", codes(BASE.replace("assert r == 9", "assert r == pytest.approx(9)")))

    def test_parametrize_pruned(self):
        self.assertIn("PARAM_SHRUNK", codes(BASE.replace("[1, 2, 3, 4]", "[1]")))
        self.assertIn("PARAM_SHRUNK", codes(BASE.replace("[1, 2, 3, 4]", "cases()")))

    def test_test_removed_or_renamed(self):
        self.assertIn("TEST_REMOVED", codes(BASE.replace("def test_float", "def check_float")))
        self.assertIn("TEST_REMOVED", codes(BASE.replace("def test_eq(self)", "def eq(self)")))

    def test_assert_moved_to_helper(self):
        after = BASE.replace("    assert r == 9\n    assert r > 0\n", "    check(r)\n") + "\ndef check(r):\n    assert r == 9\n    assert r > 0\n"
        self.assertIn("ASSERT_DECREASED", codes(after))

    def test_many_trivial_asserts_do_not_offset_a_deleted_strong_one(self):
        after = BASE.replace("    assert r == 9\n", "    assert True\n    assert True\n    assert 1\n    assert 'x'\n")
        self.assertIn("ASSERT_DECREASED", codes(after))

    def test_unittest_vacuous(self):
        self.assertIn("VACUOUS_ADDED", codes(BASE.replace("self.assertEqual(compute(2), 6)", "self.assertTrue(True)")))
        self.assertIn("VACUOUS_ADDED", codes(BASE.replace("self.assertEqual(compute(2), 6)", "self.assertEqual(compute(2), compute(2))")))

    def test_syntax_error_reported(self):
        self.assertIn("SYNTAX_ERROR", codes("def test_x(:\n"))

    def test_mock_inflation_is_warning_only(self):
        after = BASE.replace("    r = compute(3)\n", "    mock.patch('m.compute', return_value=9)\n    r = compute(3)\n")
        self.assertEqual(codes(after), [])
        self.assertEqual([v.code for v in w.compare_texts(BASE, after, "t.py") if v.severity == "warning"], ["MOCK_INFLATION"])


class JsHeuristics(unittest.TestCase):
    JS = textwrap.dedent('''
        import fc from 'fast-check';
        describe('settle', () => {
          it('is idempotent', () => {
            fc.assert(fc.property(fc.integer(), (n) => { expect(settle(n)).toEqual(settle(settle(n))); }), { numRuns: 500 });
          });
          it('rounds', () => { expect(round(1.005)).toBeCloseTo(1.01, 6); expect(a).toBe(1); });
        });
    ''')

    def c(self, after):
        return sorted({v.code for v in w.compare_texts(self.JS, after, "src/a.test.ts") if v.severity == "violation"})

    def test_clean(self):
        self.assertEqual(self.c(self.JS), [])

    def test_skip_and_only(self):
        self.assertIn("SKIP_ADDED", self.c(self.JS.replace("it('rounds'", "it.skip('rounds'")))
        self.assertIn("SKIP_ADDED", self.c(self.JS.replace("it('rounds'", "xit('rounds'")))
        self.assertIn("FOCUS_ONLY", self.c(self.JS.replace("it('rounds'", "it.only('rounds'")))

    def test_numruns_and_digits(self):
        self.assertIn("TOL_RELAXED", self.c(self.JS.replace("numRuns: 500", "numRuns: 1")))
        self.assertIn("TOL_RELAXED", self.c(self.JS.replace("1.01, 6", "1.01, 1")))
        self.assertIn("TOL_RELAXED", self.c(self.JS.replace(", { numRuns: 500 }", "")))

    def test_expectation_and_case_removed(self):
        self.assertIn("ASSERT_DECREASED", self.c(self.JS.replace(" expect(a).toBe(1);", "")))
        self.assertIn("TEST_REMOVED", self.c(self.JS.replace("it('rounds', () => { expect(round(1.005)).toBeCloseTo(1.01, 6); expect(a).toBe(1); });", "")))

    def test_vacuous(self):
        self.assertIn("VACUOUS_ADDED", self.c(self.JS + "\nit('x', () => { expect(true).toBe(true); });"))

    def test_comments_are_ignored(self):
        self.assertEqual(self.c(self.JS + "\n// it.skip('x'); xit('y')\n/* it.only('z') */"), [])


class TreeComparison(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.before, self.after = self.tmp / "b", self.tmp / "a"
        for d in (self.before, self.after):
            (d / "tests").mkdir(parents=True)
            (d / "tests" / "test_a.py").write_text("def test_a():\n    assert f() == 1\n")
            (d / "pytest.ini").write_text("[pytest]\n")
            (d / "src.py").write_text("x = 1\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def vcodes(self, **kw):
        return sorted(v.code for v in w.check_weakening(self.before, self.after, **kw))

    def test_clean(self):
        self.assertEqual(self.vcodes(), [])

    def test_deleted_test_file(self):
        (self.after / "tests" / "test_a.py").unlink()
        self.assertEqual(self.vcodes(), ["FILE_REMOVED"])

    def test_config_changes(self):
        (self.after / "pytest.ini").write_text("[pytest]\naddopts = --deselect tests/test_a.py\n")
        self.assertEqual(self.vcodes(), ["CONFIG_CHANGED"])
        self.assertEqual(self.vcodes(allow_config=["pytest.ini"]), [])

    def test_new_conftest(self):
        (self.after / "conftest.py").write_text("def pytest_collection_modifyitems(items):\n    items.clear()\n")
        self.assertEqual(self.vcodes(), ["CONFIG_CHANGED"])

    def test_forbidden_calls_in_new_tests(self):
        (self.after / "tests" / "x.test.ts").write_text("jest.mock('./db');\n")
        self.assertEqual(self.vcodes(forbidden_calls=["jest.mock"]), ["FORBIDDEN_CALL"])

    def test_non_test_source_changes_ignored(self):
        (self.after / "src.py").write_text("x = 2\n")
        self.assertEqual(self.vcodes(), [])

    def test_is_test_file(self):
        for p in ("tests/test_a.py", "pkg/a_test.py", "src/a.test.ts", "src/b.spec.tsx", "web/__tests__/c.js", "tests/helper.py"):
            self.assertTrue(w.is_test_file(p), p)
        for p in ("src/app.py", "tests/conftest.py", "tests/__init__.py", "README.md"):
            self.assertFalse(w.is_test_file(p), p)


if __name__ == "__main__":
    unittest.main()
