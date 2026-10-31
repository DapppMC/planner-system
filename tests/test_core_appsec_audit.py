"""core/verification/appsec_audit.py: every rule fires on a vulnerable sample and stays quiet on the hardened twin."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.verification import appsec_audit as aa  # noqa: E402


def rules(src: str) -> set:
    return {f.rule for f in aa.audit_source(src, "m.py")}


class Security(unittest.TestCase):
    def test_eval_and_exec(self):
        self.assertIn("SEC-EVAL", rules("def f(x):\n    return eval(x)\n"))
        self.assertIn("SEC-EVAL", rules("exec(open('a').read())\n"))
        self.assertNotIn("SEC-EVAL", rules("eval('1+1')\n"))

    def test_shell(self):
        self.assertIn("SEC-SHELL", rules("import os\ndef f(c):\n    os.system(c)\n"))
        self.assertIn("SEC-SHELL", rules("import subprocess\ndef f(c):\n    subprocess.run(c, shell=True)\n"))
        self.assertNotIn("SEC-SHELL", rules("import subprocess\nsubprocess.run(['ls', '-l'])\n"))
        self.assertNotIn("SEC-SHELL", rules("import os\nos.system('true')\n"))

    def test_sql(self):
        self.assertIn("SEC-SQL", rules("def f(c, n):\n    c.execute(f\"select * from t where n='{n}'\")\n"))
        self.assertIn("SEC-SQL", rules("def f(c, n):\n    c.execute('select ' + n)\n"))
        self.assertIn("SEC-SQL", rules("def f(c, n):\n    c.execute('select %s' % n)\n"))
        self.assertIn("SEC-SQL", rules("def f(c, n):\n    c.execute('select {}'.format(n))\n"))
        self.assertNotIn("SEC-SQL", rules("def f(c, n):\n    c.execute('select * from t where n=?', (n,))\n"))
        pragma = aa.audit_source("def f(c, n):\n    c.execute(f'PRAGMA busy_timeout={n}')\n")
        self.assertEqual([(x.rule, x.severity) for x in pragma], [("SEC-SQL", "low")], "PRAGMA cannot bind parameters: low, not high")
        self.assertNotIn("SEC-EVAL", rules("def f(s):\n    return compile(s, 'x', 'exec')\n"))

    def test_deserialize(self):
        self.assertIn("SEC-DESERIALIZE", rules("import pickle\ndef f(b):\n    return pickle.loads(b)\n"))
        self.assertIn("SEC-DESERIALIZE", rules("import yaml\ndef f(s):\n    return yaml.load(s)\n"))
        self.assertNotIn("SEC-DESERIALIZE", rules("import yaml\ndef f(s):\n    return yaml.load(s, Loader=yaml.SafeLoader)\n"))
        self.assertNotIn("SEC-DESERIALIZE", rules("import yaml\ndef f(s):\n    return yaml.safe_load(s)\n"))

    def test_path_taint(self):
        bad = "import os\ndef read(base, name):\n    p = os.path.join(base, name)\n    return open(p).read()\n"
        self.assertIn("SEC-PATH", rules(bad))
        good = ("import os\ndef read(name):\n    p = os.path.join('/srv', os.path.basename(name))\n    return open(p).read()\n")
        self.assertNotIn("SEC-PATH", rules(good))
        helper = "def read(name):\n    p = safe_join('/srv', name)\n    return open(p).read()\n"
        self.assertNotIn("SEC-PATH", rules(helper))
        self.assertNotIn("SEC-PATH", rules("def read():\n    return open('/etc/hostname').read()\n"))

    def test_taint_follows_assignments_and_loops(self):
        src = "def f(names):\n    for n in names:\n        p = '/srv/' + n\n        open(p)\n"
        self.assertIn("SEC-PATH", rules(src))
        src2 = "def f(name):\n    name = int(name)\n    open('/srv/%d' % name)\n"
        self.assertNotIn("SEC-PATH", rules(src2))

    def test_secrets(self):
        self.assertIn("SEC-HARDCODED-SECRET", rules("api_key = 'abcdefgh12345678'\n"))
        self.assertIn("SEC-HARDCODED-SECRET", rules("cfg = {}\ncfg['password'] = 'hunter2hunter2'\n"))
        self.assertIn("SEC-HARDCODED-SECRET", rules("x = 'AKIAABCDEFGHIJKLMNOP'\n"))
        self.assertNotIn("SEC-HARDCODED-SECRET", rules("api_key = os.environ['K']\n"))
        self.assertNotIn("SEC-HARDCODED-SECRET", rules("password = '<your password>'\n"))
        self.assertNotIn("SEC-HARDCODED-SECRET", rules("password = 'changeme'\n"))
        self.assertNotIn("SEC-HARDCODED-SECRET", rules("token_ttl = 'short'\n"))

    def test_tls_debug_chmod_hash_tmp(self):
        self.assertIn("SEC-TLS", rules("import requests\nrequests.get('u', verify=False)\n"))
        self.assertIn("SEC-DEBUG", rules("app.run(debug=True)\n"))
        self.assertIn("SEC-CHMOD", rules("import os\nos.chmod('f', 0o777)\n"))
        self.assertNotIn("SEC-CHMOD", rules("import os\nos.chmod('f', 0o600)\n"))
        got = {f.rule: f.severity for f in aa.audit_source("import hashlib, tempfile\nhashlib.md5(b'')\ntempfile.mktemp()\n")}
        self.assertEqual(got, {"SEC-HASH": "low", "SEC-TMP": "low"})

    def test_weak_random(self):
        self.assertIn("SEC-WEAK-RANDOM", rules("import random\ndef f():\n    token = ''.join(random.choice('ab') for _ in range(8))\n    return token\n"))
        self.assertIn("SEC-WEAK-RANDOM", rules("import random\ndef make_session_id():\n    return random.randint(0, 9)\n"))
        self.assertNotIn("SEC-WEAK-RANDOM", rules("import secrets\ndef make_token():\n    return secrets.token_hex(8)\n"))
        self.assertNotIn("SEC-WEAK-RANDOM", rules("import random\ndef shuffle_deck(d):\n    random.shuffle(d)\n"))


class Concurrency(unittest.TestCase):
    COUNTER = ("import threading\nclass C:\n    def __init__(self):\n        self._l = threading.Lock()\n        self.n = 0\n"
               "    def inc(self):\n        {body}\n")

    def test_unlocked_class_state(self):
        self.assertIn("CONC-UNLOCKED-STATE", rules(self.COUNTER.format(body="self.n += 1")))
        self.assertNotIn("CONC-UNLOCKED-STATE", rules(self.COUNTER.format(body="with self._l:\n            self.n += 1")))

    def test_private_helper_called_under_lock_is_covered(self):
        src = ("import threading\nclass C:\n    def __init__(self):\n        self._l = threading.Lock()\n        self.items = []\n"
               "    def add(self, x):\n        with self._l:\n            self._put(x)\n    def _put(self, x):\n        self.items.append(x)\n")
        self.assertNotIn("CONC-UNLOCKED-STATE", rules(src))
        src2 = src + "    def leak(self, x):\n        self._put(x)\n"
        self.assertIn("CONC-UNLOCKED-STATE", rules(src2), "one unlocked caller uncovers the helper")

    def test_class_without_a_lock_is_not_judged(self):
        self.assertNotIn("CONC-UNLOCKED-STATE", rules("class C:\n    def __init__(self):\n        self.n = 0\n    def inc(self):\n        self.n += 1\n"))

    def test_mutating_call_on_attribute(self):
        src = ("import threading\nclass C:\n    def __init__(self):\n        self.mu = threading.RLock()\n        self.q = []\n"
               "    def put(self, x):\n        self.q.append(x)\n")
        self.assertIn("CONC-UNLOCKED-STATE", rules(src))

    def test_global_mutation(self):
        bad = "import threading\nCACHE = {}\ndef put(k, v):\n    CACHE[k] = v\n"
        self.assertIn("CONC-GLOBAL-MUTATION", rules(bad))
        self.assertIn("CONC-GLOBAL-MUTATION", rules("import threading\nLOG = []\ndef w(x):\n    LOG.append(x)\n"))
        good = "import threading\nLOCK = threading.Lock()\nCACHE = {}\ndef put(k, v):\n    with LOCK:\n        CACHE[k] = v\n"
        self.assertNotIn("CONC-GLOBAL-MUTATION", rules(good))
        self.assertNotIn("CONC-GLOBAL-MUTATION", rules("CACHE = {}\ndef put(k, v):\n    CACHE[k] = v\n"), "single-threaded module is not judged")

    def test_toctou(self):
        self.assertIn("CONC-TOCTOU", rules("import os\ndef f(p):\n    if os.path.exists(p):\n        return open(p).read()\n"))
        self.assertNotIn("CONC-TOCTOU", rules("def f(p):\n    try:\n        return open(p).read()\n    except FileNotFoundError:\n        return ''\n"))

    def test_async_blocking(self):
        self.assertIn("CONC-ASYNC-BLOCKING", rules("import time\nasync def f():\n    time.sleep(1)\n"))
        self.assertNotIn("CONC-ASYNC-BLOCKING", rules("import time\ndef f():\n    time.sleep(1)\n"))


class Tree(unittest.TestCase):
    def make(self, files: dict) -> str:
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        for rel, text in files.items():
            p = Path(d) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        return d

    def test_tree_skips_tests_and_reports_other_languages(self):
        d = self.make({"src/a.py": "eval(input())\n", "tests/test_a.py": "eval(input())\n", "web/x.js": "eval(1)\n", "__pycache__/z.py": "eval(input())\n"})
        rep = aa.audit_tree(d)
        self.assertEqual(rep.audited, ["src/a.py"])
        self.assertEqual([f.file for f in rep.findings], ["src/a.py"])
        self.assertTrue(any("web/x.js" in s for s in rep.not_audited), "other languages are never reported as clean")
        self.assertEqual(len(aa.audit_tree(d, include_tests=True).audited), 2)

    def test_explicit_file_list_and_allow_list(self):
        d = self.make({"a.py": "def f(x):\n    eval(x)\n", "b.py": "def f(x):\n    eval(x)\n"})
        rep = aa.audit_tree(d, ["a.py"])
        self.assertEqual(rep.audited, ["a.py"])
        rep = aa.audit_tree(d, allow=["SEC-EVAL:a.py"])
        self.assertEqual([f.file for f in rep.findings], ["b.py"])
        self.assertEqual(len(rep.allowed), 1, "an allowed finding is echoed, never hidden")

    def test_syntax_error_is_a_finding_not_a_pass(self):
        rep = aa.audit_tree(self.make({"a.py": "def (:\n"}))
        self.assertEqual([f.rule for f in rep.findings], ["AUDIT-PARSE"])

    def test_no_inline_suppression(self):
        self.assertIn("SEC-EVAL", rules("def f(x):\n    eval(x)  # noqa\n"))
        self.assertIn("SEC-EVAL", rules("def f(x):\n    eval(x)  # nosec\n"))

    def test_oversized_and_symlink(self):
        d = self.make({"big.py": "x = 1\n" * 10})
        old = aa.MAX_BYTES
        aa.MAX_BYTES = 5
        try:
            rep = aa.audit_tree(d)
        finally:
            aa.MAX_BYTES = old
        self.assertEqual(rep.audited, [])
        self.assertTrue(rep.not_audited)

    def test_cli(self):
        d = self.make({"a.py": "def f(x):\n    eval(x)\n", "ok.py": "x = 1\n"})
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(aa.main([d]), 1)
        self.assertIn("SEC-EVAL", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(aa.main([d, "ok.py"]), 0)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(aa.main([d, "--json"]), 1)
        data = json.loads(out.getvalue())
        self.assertFalse(data["ok"])
        self.assertEqual(data["counts"]["high"], 1)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(aa.main([d + "/nope"]), 2)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(aa.main([d, "--fail-on", "high", "--allow", "SEC-EVAL:a.py"]), 0)

    def test_the_engine_audits_itself_clean_of_high_findings(self):
        """AG8's tool must not contain the vulnerabilities it hunts: no high finding in the engine's own core/."""
        rep = aa.audit_tree(ROOT / "core")
        self.assertGreater(len(rep.audited), 10)
        self.assertEqual([str(f) for f in rep.at_least("high")], [])


if __name__ == "__main__":
    unittest.main()
