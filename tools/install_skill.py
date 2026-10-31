#!/usr/bin/env python3
"""install_skill.py: one-command installer for the planner-3-engine skill. Standard library only (Python 3.8+).

    python tools/install_skill.py                 install into the Antigravity global skills folder
    python tools/install_skill.py --dry-run       show what would happen, write nothing
    python tools/install_skill.py --cli           use the Antigravity CLI folder instead
    python tools/install_skill.py --target DIR    install somewhere else (DIR/planner-3-engine)
    python tools/install_skill.py --no-dist       do not rebuild dist/planner-system-3.0.zip

Default targets (the user's home is %USERPROFILE% on Windows):
    Linux / macOS  ~/.gemini/config/skills/planner-3-engine
    Windows        %USERPROFILE%\\.gemini\\config\\skills\\planner-3-engine
    --cli          <home>/.gemini/antigravity-cli/skills/planner-3-engine
Steps: 1 sync skills/planner-3-engine from tools/, design/, gates/, catalog/ (single source of truth)
       2 copy it to the target (a folder that is not this skill is never overwritten)
       3 verify the installed copy (frontmatter, byte compile, smoke run)
       4 rebuild dist/planner-system-3.0.zip.  Exit 0 ok, 1 verification failed, 2 refused or bad usage.
"""
import argparse, filecmp, os, re, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = "planner-3-engine"
SKILL_SRC = os.path.join(ROOT, "skills", SKILL)
SCRIPTS = ["repomap.py", "cartograph.py", "sandbox.py", "odcat.py", "ticketc.py", "design_lint.py"]
REFERENCES = {  # skill path -> repo path
    "DESIGN_CONSTITUTION.md": "design/DESIGN_CONSTITUTION.md", "tokens.css": "design/tokens.css",
    "gate-2_cartography.md": "gates/gate-2_cartography.md", "gate-1_pinning.md": "gates/gate-1_pinning.md",
    "gate3_verification.md": "gates/gate3_verification.md",
}
NOT_INSTALLED = {"evals", "__pycache__"}  # test cases and caches stay in the repo


class Refused(Exception):
    pass


def detect_os(platform=None):
    p = platform or sys.platform
    return "windows" if p.startswith(("win", "cygwin", "msys")) else ("macos" if p == "darwin" else "linux")


def default_target(os_name, env=None, home=None, cli=False):
    env = os.environ if env is None else env
    if os_name == "windows":
        base = env.get("USERPROFILE") or home or os.path.expanduser("~")
        parts = [".gemini", "antigravity-cli", "skills"] if cli else [".gemini", "config", "skills"]
        return base.rstrip("\\/") + "\\" + "\\".join(parts)
    base = home or os.path.expanduser("~")
    parts = [".gemini", "antigravity-cli", "skills"] if cli else [".gemini", "config", "skills"]
    return os.path.join(base, *parts)


def skill_name_of(skill_dir):
    p = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf8", errors="ignore") as fh:
        m = re.match(r"---\s*\n(.*?)\n---", fh.read(), re.S)
    n = re.search(r"^name:\s*(\S+)\s*$", m.group(1), re.M) if m else None
    return n.group(1).strip("'\"") if n else None


def copy_tree(src, dst, skip=()):
    for r, ds, fs in os.walk(src):
        ds[:] = sorted(d for d in ds if d not in skip)
        rel = os.path.relpath(r, src)
        os.makedirs(os.path.join(dst, rel) if rel != "." else dst, exist_ok=True)
        for f in sorted(fs):
            if f.endswith(".pyc"):
                continue
            shutil.copyfile(os.path.join(r, f), os.path.join(dst, rel, f) if rel != "." else os.path.join(dst, f))


def sync_bundle(root=ROOT, dry=False):
    """Refresh skills/planner-3-engine/{scripts,references,catalog} from the repo's real files. Returns changed paths."""
    skill = os.path.join(root, "skills", SKILL)
    if not os.path.isfile(os.path.join(skill, "SKILL.md")):
        raise Refused(f"{skill}/SKILL.md not found")
    plan = []
    for f in SCRIPTS:
        plan.append((os.path.join(root, "tools", f), os.path.join(skill, "scripts", f)))
    for sub in ("gate3", "sgrecipes"):
        d = os.path.join(root, "tools", sub)
        for f in sorted(os.listdir(d)):
            if os.path.isfile(os.path.join(d, f)) and not f.endswith(".pyc"):
                plan.append((os.path.join(d, f), os.path.join(skill, "scripts", sub, f)))
    for dst, src in sorted(REFERENCES.items()):
        plan.append((os.path.join(root, src), os.path.join(skill, "references", dst)))
    cat = os.path.join(root, "catalog")
    for r, ds, fs in os.walk(cat):
        ds[:] = sorted(ds)
        for f in sorted(fs):
            s = os.path.join(r, f)
            plan.append((s, os.path.join(skill, "catalog", os.path.relpath(s, cat))))
    changed = []
    for s, d in plan:
        if not os.path.isfile(s):
            raise Refused(f"source missing: {s}")
        if not os.path.isfile(d) or not filecmp.cmp(s, d, shallow=False):
            changed.append(os.path.relpath(d, root).replace("\\", "/"))
            if not dry:
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copyfile(s, d)
    wanted = {os.path.normpath(d) for _, d in plan}
    for sub in ("scripts", "references", "catalog"):  # remove files that no longer exist in the repo
        base = os.path.join(skill, sub)
        for r, _, fs in os.walk(base):
            for f in fs:
                p = os.path.normpath(os.path.join(r, f))
                if p not in wanted and not f.endswith(".pyc") and "__pycache__" not in p:
                    changed.append("-" + os.path.relpath(p, root).replace("\\", "/"))
                    if not dry:
                        os.remove(p)
    return sorted(changed)


def install(skill_dir, target_root, dry=False):
    """Copy skill_dir to target_root/planner-3-engine. Refuses to touch a folder that is not this skill."""
    dest = os.path.join(target_root, SKILL)
    if os.path.abspath(dest).startswith(os.path.abspath(skill_dir) + os.sep) or os.path.abspath(dest) == os.path.abspath(skill_dir):
        raise Refused("target is inside the source skill folder")
    if os.path.exists(target_root) and not os.path.isdir(target_root):
        raise Refused(f"{target_root} exists and is not a folder")
    action = "create"
    if os.path.lexists(dest):
        if os.path.islink(dest) or not os.path.isdir(dest) or skill_name_of(dest) != SKILL:
            raise Refused(f"{dest} exists and is not an installed {SKILL}; refusing to overwrite it")
        action = "replace"
    if dry:
        return dest, action
    os.makedirs(target_root, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".planner-install-", dir=target_root)
    try:
        staged = os.path.join(tmp, SKILL)
        copy_tree(skill_dir, staged, skip=NOT_INSTALLED)
        old = None
        if action == "replace":
            old = os.path.join(tmp, "old")
            os.rename(dest, old)
        try:
            os.rename(staged, dest)
        except OSError:
            if old:
                os.rename(old, dest)  # roll back
            raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return dest, action


def verify(dest):
    """Return a list of problems (empty = ok)."""
    problems = []
    if skill_name_of(dest) != SKILL:
        problems.append("SKILL.md frontmatter name is not " + SKILL)
    for r, _, fs in os.walk(os.path.join(dest, "scripts")):
        for f in fs:
            if f.endswith(".py"):
                path = os.path.join(r, f)
                try:  # compile in memory: writes no .pyc into the installed folder
                    with open(path, "rb") as fh:
                        compile(fh.read(), path, "exec")
                except (SyntaxError, ValueError) as e:
                    problems.append(f"does not compile: {f}: {str(e)[:80]}")
    for rel in ("references/DESIGN_CONSTITUTION.md", "references/tokens.css", "catalog/catalog.jsonl", "catalog/FACETS.md"):
        if not os.path.isfile(os.path.join(dest, *rel.split("/"))):
            problems.append("missing " + rel)
    if not problems:
        r = subprocess.run([sys.executable, "-I", os.path.join(dest, "scripts", "odcat.py"), "search", "--archetype", "editorial"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0 or "atelier-zero" not in r.stdout:
            problems.append("smoke run failed: odcat.py search --archetype editorial: " + (r.stderr or r.stdout)[:120])
    return problems


def main(argv=None, os_name=None, env=None, home=None):
    ap = argparse.ArgumentParser(description="Install the planner-3-engine skill into Antigravity's global skills folder")
    ap.add_argument("--target", help="skills folder to install into (the skill goes in DIR/planner-3-engine)")
    ap.add_argument("--cli", action="store_true", help="use the Antigravity CLI skills folder")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-dist", action="store_true", help="do not rebuild dist/planner-system-3.0.zip")
    ap.add_argument("--root", default=ROOT, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    osn = os_name or detect_os()
    target = a.target or default_target(osn, env, home, a.cli)
    root = os.path.abspath(a.root)
    skill = os.path.join(root, "skills", SKILL)
    print(f"OS: {osn}\nTarget: {os.path.join(target, SKILL)}")
    try:
        changed = sync_bundle(root, dry=a.dry_run)
        print(f"Sync: {len(changed)} file(s) {'would change' if a.dry_run else 'updated'} in skills/{SKILL}")
        for c in changed[:10]:
            print("  " + c)
        dest, action = install(skill, target, dry=a.dry_run)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 2
    if a.dry_run:
        print(f"Dry run: would {action} {dest}. Nothing written.")
        return 0
    print(f"Installed ({action}): {dest}")
    problems = verify(dest)
    if problems:
        for p in problems:
            print("VERIFY FAILED: " + p, file=sys.stderr)
        return 1
    print("Verify: ok (frontmatter, byte compile, smoke run)")
    if not a.no_dist:
        sys.path.insert(0, os.path.join(root, "tools"))
        import build_dist
        out, n = build_dist.build(root)
        print(f"Dist: {out} ({n} files)")
    print("Done. Restart Antigravity (or start a new session) so it re-reads the skills folder.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
