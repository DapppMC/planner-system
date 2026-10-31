#!/usr/bin/env python3
"""evals/run.py: run the 10 golden tasks (5 greenfield + 5 brownfield) against the real tools.

Prints per-task PASS/FAIL/SKIP with duration and tool-output tokens (chars/4), the static context
cost of the always-loaded files, and `pass rate: P/10`. Exit 0 only when nothing FAILED.
SKIP (missing ast-grep binary) is reported, never counted as a pass.
"""
import contextlib, glob, hashlib, io, json, os, shutil, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path[:0] = [TOOLS, os.path.join(TOOLS, "gate3")]
import odcat, repomap, sandbox, ticketc, reflect_check, failure_signature, design_lint  # noqa: E402

est = lambda s: (len(s) + 3) // 4


class Skip(Exception):
    pass


def sh(cwd, *cmd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def mkdir_tmp(cleanup):
    d = tempfile.mkdtemp(); cleanup.append(d); return d


def w(root, rel, text):
    p = os.path.join(root, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf8", newline="\n") as fh:
        fh.write(text)
    return p


def init_repo(root):
    for c in (["git", "init", "-q"], ["git", "config", "user.email", "e@x.io"], ["git", "config", "user.name", "e"]):
        sh(root, *c)


def commit_all(root):
    sh(root, "git", "add", "-A"); sh(root, "git", "commit", "-q", "-m", "c")


TICKET = """id: T-9
goal: demo
frozen: {}
allowed_paths:
  - src/**
forbidden_paths:
  - tests/golden/**
protected_tests:
  - tests/golden/test_a.py
acceptance:
  - python -m unittest
stop_if:
  - golden test fails twice
"""


def gf_01(cl):
    d = mkdir_tmp(cl)
    ok_p = w(d, "ok.yml", TICKET)
    bad_p = w(d, "bad.yml", TICKET.replace("acceptance:\n  - python -m unittest\n", "acceptance: []\n"))
    ticketc.validate_ticket(ok_p, d)
    try:
        ticketc.validate_ticket(bad_p, d)
        return False, "ticket without acceptance was accepted", ""
    except ValueError as e:
        return "cceptance" in str(e), str(e)[:80], str(e)


def gf_02(cl):
    d = mkdir_tmp(cl); init_repo(d)
    w(d, "src/a.py", "1\n"); w(d, "README.md", "r\n"); commit_all(d)
    w(d, "src/a.py", "2\n")
    ticketc.verify_diff(["src/**"], ["tests/golden/**"], d)
    caught = 0
    w(d, "README.md", "leak\n")
    try: ticketc.verify_diff(["src/**"], [], d)
    except PermissionError: caught += 1
    sh(d, "git", "checkout", "README.md"); w(d, "x/evil.sh", "x\n")
    try: ticketc.verify_diff(["src/**"], [], d)
    except PermissionError: caught += 1
    return caught == 2, f"{caught}/2 leaks caught", ""


def gf_03(cl):
    d = mkdir_tmp(cl); log = os.path.join(d, "reflection_log.md")
    sig = failure_signature.compute_signature("t::x", "AssertionError", "m.py:3")
    code = lambda *a: reflect_check.main([*a, "--log", log])
    with contextlib.redirect_stderr(io.StringIO()):
        r0 = code(sig, "1")  # no log
        w(d, "reflection_log.md", f"## {sig} / attempt 1\nChange: guess\n")
        r1 = code(sig, "1")  # no hypothesis
        w(d, "reflection_log.md", "".join(f"## {sig} / attempt {i}\nHypothesis: theory {i}: off-by-one in slice end index\n\n" for i in (1, 2, 3)))
        r2 = code(sig, "3")
        r3 = code(sig, "4")
    esc = os.path.exists(os.path.join(d, "ESCALATION.md"))
    return (r0, r1, r2, r3, esc) == (2, 2, 0, 3, True), f"exit codes {(r0, r1, r2, r3)}, ESCALATION.md={esc}", ""


def gf_04(cl):
    out = mkdir_tmp(cl)
    fix = os.path.join(ROOT, "tests", "fixtures", "open-design")
    with contextlib.redirect_stdout(io.StringIO()):
        n = odcat.build(fix, out)
    with open(os.path.join(out, "FACETS.md"), encoding="utf8") as fh:
        facets = fh.read()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = odcat.search(out, fam="expressive", mode="dark")
    ok = n == 7 and est(facets) <= 350 and [r["id"] for r in res] == ["neon-pop"]
    return ok, f"{n} systems, FACETS {est(facets)} tokens", facets


def gf_05(cl):
    """Constitution + tokens + the anti-slop linter, proven by mutation: a known-good page must pass
    strictly, and each single mutation must be caught by exactly the rule that guards it."""
    with open(os.path.join(ROOT, "design", "DESIGN_CONSTITUTION.md"), encoding="utf8") as fh:
        c = fh.read()
    with open(os.path.join(ROOT, "design", "tokens.css"), encoding="utf8") as fh:
        css = fh.read()
    layers = all(f"LAYER {i}" in c for i in range(5))
    radius = "--radius-outer: calc(var(--radius-inner) + var(--pad-card))" in css
    arch = all(k in css for k in ("industrial-warm", "editorial", "--bg-canvas"))
    with open(os.path.join(ROOT, "tests", "fixtures", "design", "good.html"), encoding="utf8") as fh:
        good = fh.read()
    st = lambda rep, rid: next(r["status"] for r in rep["results"] if r["id"] == rid)
    base = design_lint.lint(good, strict=True)
    mutations = {  # name -> (edit, rule that must now fail)
        "indigo accent": (("--accent:#2f9e8f", "--accent:#6366f1"), "R01"),
        "purple tailwind": (("<header>", '<header class="bg-purple-600">'), "R01"),
        "broken concentric radius": (("--radius-outer:calc(var(--radius-inner) + var(--pad))", "--radius-outer:12px"), "R02"),
        "no tabular-nums": (("table{font-variant-numeric:tabular-nums}", "table{color:var(--fg)}"), "R03"),
        "transition all": ((".bar{transition:transform", ".bar{transition:all .2s,transform"), "R05"),
    }
    caught, detail = 0, []
    for name, ((a, b), rule) in mutations.items():
        if a not in good:
            detail.append(f"{name}: mutation anchor missing"); continue
        rep = design_lint.lint(good.replace(a, b), strict=True)
        if st(rep, rule) == "fail": caught += 1
        else: detail.append(f"{name}: {rule} not triggered")
    ok = est(c) <= 1500 and layers and radius and arch and base["ok"] and caught == len(mutations)
    return ok, f"constitution {est(c)} tokens; lint good=pass; mutations caught {caught}/{len(mutations)}", "; ".join(detail)


def bf_01(cl):
    d = mkdir_tmp(cl)
    w(d, "core/parser.py", "class CertificateParser:\n    pass\n\ndef parse_certificate(x):\n    return CertificateParser()\n")
    for i in range(3):
        w(d, f"app/u{i}.py", f"from core.parser import parse_certificate\n\ndef run{i}():\n    return parse_certificate(1)\n")
    w(d, "node_modules/x/index.js", "function junkFunctionName() {}\njunkFunctionName()\n")
    text, used, n = repomap.build_repomap(d, 300)
    ok = text.startswith("core/parser.py") and used <= 300 and "junk" not in text and n == 4
    return ok, f"{n} files, {used} tokens", text


def bf_02(cl):
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    from test_sgrecipes import find_astgrep  # reuse the verified binary finder
    b = find_astgrep()
    if not b:
        raise Skip("ast-grep not installed")
    r = subprocess.run([b, "scan", "-r", os.path.join(TOOLS, "sgrecipes", "find_api_routes.yml"),
                        os.path.join(ROOT, "tests", "fixtures", "sg", "routes.ts"), "--json=compact"], capture_output=True, text=True)
    lines = sorted(m["range"]["start"]["line"] + 1 for m in json.loads(r.stdout or "[]"))
    return lines == [4, 5, 6], f"route lines {lines}", r.stdout


def bf_03(cl):
    d = mkdir_tmp(cl); init_repo(d)
    w(d, "package-lock.json", "{}"); w(d, ".env.example", "PORT=1\nTOKEN=supersecretvalue\n"); commit_all(d)
    a, b = sandbox.dump_lock(sandbox.build_lock(d)), sandbox.dump_lock(sandbox.build_lock(d))
    keys = set(json.loads(a)) == {"parent_commit", "lockfile_hash", "env_keys", "isolated_port", "db_snapshot_id"}
    return a == b and keys and "supersecretvalue" not in a, "lock deterministic, keys only", a


def bf_04(cl):
    bad = ["ssh a@b", "kubectl get pods", "psql -c 'DROP DATABASE x'", "rm -rf /", "curl https://api.prod.example.com", "scp f h:/x"]
    good = ["git status", "python -m unittest", "rm -rf ./build", "npm test"]
    blocked = sum(not sandbox.check_command(c)[0] for c in bad)
    allowed = sum(sandbox.check_command(c)[0] for c in good)
    return (blocked, allowed) == (6, 4), f"blocked {blocked}/6, allowed {allowed}/4", ""


def bf_05(cl):
    d = mkdir_tmp(cl)
    p = w(d, "tests/golden/case1.json", '{"total": 10}')
    with open(p, "rb") as fh:
        h = hashlib.sha256(fh.read()).hexdigest()[:16]
    t = w(d, "t.yml", TICKET.replace("frozen: {}", f"frozen:\n  tests/golden/case1.json: {h}"))
    ticketc.validate_ticket(t, d)
    w(d, "tests/golden/case1.json", '{"total": 11}')
    try:
        ticketc.validate_ticket(t, d)
        return False, "tampered golden fixture accepted", ""
    except ValueError as e:
        return "changed" in str(e), "tampering detected", str(e)


CHECKS = {"gf-01-ticket-valid": gf_01, "gf-02-diff-guard": gf_02, "gf-03-reflexion-loop": gf_03,
          "gf-04-design-catalog": gf_04, "gf-05-design-constitution": gf_05, "bf-01-repomap-rank": bf_01,
          "bf-02-route-recipe": bf_02, "bf-03-sandbox-lock": bf_03, "bf-04-deny-list": bf_04, "bf-05-golden-pin": bf_05}


def context_cost():
    def tok(rel):
        with open(os.path.join(ROOT, rel), encoding="utf8") as fh:
            return est(fh.read())
    gates = {os.path.basename(p): tok(os.path.relpath(p, ROOT)) for p in glob.glob(os.path.join(ROOT, "gates", "*.md"))}
    return {"CLAUDE.md": tok("CLAUDE.md"), "DESIGN_CONSTITUTION.md": tok("design/DESIGN_CONSTITUTION.md"),
            "largest_gate": max(gates.values()), "all_gates": sum(gates.values())}


def main():
    specs = {}
    for p in sorted(glob.glob(os.path.join(ROOT, "evals", "tasks", "*.json"))):
        with open(p, encoding="utf8") as fh:
            s = json.load(fh)
        specs[s["id"]] = s
    if set(specs) != set(CHECKS):
        print(f"FAIL: task files and checks differ: {sorted(set(specs) ^ set(CHECKS))}", file=sys.stderr)
        return 1
    kinds = [s["kind"] for s in specs.values()]
    assert kinds.count("greenfield") == 5 and kinds.count("brownfield") == 5, "need 5 greenfield + 5 brownfield"
    passed = failed = skipped = 0
    tot_tokens = 0
    for tid in sorted(specs):
        cleanup, t0 = [], time.time()
        try:
            ok, detail, out = CHECKS[tid](cleanup)
            status = "PASS" if ok else "FAIL"
        except Skip as e:
            status, detail, out = "SKIP", str(e), ""
        except Exception as e:  # a crashing check is a failure, not a pass
            status, detail, out = "FAIL", f"{type(e).__name__}: {e}", ""
        finally:
            for d in cleanup:
                shutil.rmtree(d, ignore_errors=True)
        passed += status == "PASS"; failed += status == "FAIL"; skipped += status == "SKIP"
        tot_tokens += est(out)
        print(f"{status:4} {tid:28} {time.time()-t0:5.2f}s {est(out):5d} tok  {detail}")
    c = context_cost()
    print("\ncontext cost (tokens):", ", ".join(f"{k}={v}" for k, v in c.items()))
    print(f"tool output total: {tot_tokens} tokens")
    extra = f" ({skipped} skipped)" if skipped else ""
    print(f"pass rate: {passed}/{len(specs)}{extra}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
