#!/usr/bin/env python3
"""build_dist.py: package planner-system-3.0 into dist/planner-system-3.0.zip (deterministic). Stdlib only."""
import argparse, os, sys, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "planner-system-3.0"
SKIP_DIRS = {"dist", "__pycache__", ".git", ".venv", "node_modules", ".planner", ".stryker-tmp", "mutants"}
SKIP_FILES = {"ESCALATION.md", "reflection_log.md", "sandbox.lock"}
REQUIRED = ["CLAUDE.md", "progress.md", "gates/gate0_discovery.md", "gates/gate4_readiness.md", "tools/repomap.py", "tools/sandbox.py",
            "tools/odcat.py", "tools/ticketc.py", "tools/gate3/reflect_check.py", "tools/gate3/failure_signature.py",
            "tools/sgrecipes/find_api_routes.yml", "design/DESIGN_CONSTITUTION.md", "design/tokens.css", "evals/run.py"]
REQUIRED_4 = ["orchestrator.py", "swarm.py", "verifier.py", "dag_planner.py", "core/fleet.py", "core/coordination/blackboard.py",
              "core/verification/sealed_gate.py", "core/verification/mutation_gate.py", "docs/ARCHITECTURE.md", "docs/ENGINE_AUDIT.md",
              "core/antigravity_bridge.py", "core/verification/appsec_audit.py", "evals/golden_e2e_run.py", "evals/run_evals.py",
              "skills/planner-4-engine/SKILL.md", "skills/planner-4-engine/bin/planner4", "skills/planner-4-engine/bin/planner4.bat"]
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def is_executable(rel):
    """.py scripts and the extension-less launchers in a bin/ folder get the exec bit (a zip loses it otherwise); .bat does not need it."""
    return rel.endswith(".py") or ("/bin/" in "/" + rel and "." not in os.path.basename(rel))


def collect(root=ROOT):
    out = []
    for r, ds, fs in os.walk(root):
        ds[:] = sorted(d for d in ds if d not in SKIP_DIRS)
        for f in sorted(fs):
            if f in SKIP_FILES or f.endswith((".pyc", ".zip", ".skill")):
                continue
            out.append(os.path.relpath(os.path.join(r, f), root).replace("\\", "/"))
    return sorted(out)


def build(root=ROOT, out=None, name=NAME):
    files = collect(root)
    missing = [r for r in REQUIRED + (REQUIRED_4 if name.endswith("4.0") else []) if r not in files]
    if missing:
        raise SystemExit(f"missing required files: {missing}")
    out = out or os.path.join(root, "dist", f"{name}.zip")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            zi = zipfile.ZipInfo(f"{name}/{rel}", FIXED_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = (0o755 if is_executable(rel) else 0o644) << 16
            with open(os.path.join(root, rel), "rb") as fh:
                z.writestr(zi, fh.read())
    return out, len(files)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the distribution zip")
    ap.add_argument("--out")
    ap.add_argument("--name", default=NAME, help="top-level folder inside the zip, e.g. planner-system-4.0")
    a = ap.parse_args(argv)
    path, n = build(ROOT, a.out, a.name)
    print(f"Wrote {path} ({n} files, {os.path.getsize(path)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
