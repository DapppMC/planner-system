#!/usr/bin/env python3
"""
Transactional & Idempotent Workspace Bootstrapper for AI Planner System 3.0.
Bootstraps target workspaces (greenfield or brownfield) with Planner 3.0 tools,
spec templates, design constitution, bundled catalogs, and swarm configuration.
"""

import argparse
import datetime
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

REQUIRED_DIRS = [
    "docs/spec",
    "docs/spec/tickets",
    "docs/spec/decisions",
    "docs/spec/swarm",
    "docs/spec/gates",
    "scripts",
    "scripts/gate3",
    "catalog",
    "tests/golden",
    ".quarantine",
]

GITIGNORE_CONTENT = """# AI Planner System 3.0 Ignore Rules
.planner_staging_*/
.planner_interview_state.json
.quarantine/
.agent-run/
.swarm/
.check-logs/
__pycache__/
*.pyc
.env
.env.*
node_modules/
dist/
coverage/
logs/ag*_port_*.log
# Planner 4.0 runtime state (the sealed vault and tickets are kept)
.planner/board.db*
.planner/work/
.planner/baselines/
.planner/reports/
.planner/prompts/
"""

INITIAL_PROGRESS_MD_TEMPLATE = """# PROJECT CONTINUITY & STATE INVARIANT LOG (`progress.md`)
> **PROTOCOL FOR AI AGENTS:** Read Section 1 and Section 2 FIRST before reading any source file.
> Update Section 1 BEFORE and AFTER every atomic action. Never violate Locked Decisions in Section 3.

## 1. LIVE EXECUTION CURSOR (MUTABLE SNAPSHOT)
```yaml
last_updated_utc: "{timestamp}"
system_version: "3.0.0"
project_mode: "{project_mode}" # GREENFIELD | BROWNFIELD
current_phase: "{initial_phase}" # CARTOGRAPHY | DISCOVERY | SPEC_GEN | WAVE_EXECUTION | QA_RECOVERY | COMPLETED
active_wave: "W0"
active_ticket: "T-000"
active_instance: "AG1"
working_tree_status: "CLEAN"
next_atomic_action: >
  {next_action}
blocker_or_warning: "None"
```

## 2. WAVE & TICKET MATRIX (SINGLE SOURCE OF TRUTH)
| Ticket | Wave | Owner | Write Scope (Strict Glob) | Port Allocation | Status | Git Commit SHA |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `T-000` | W0 | AG1 | `docs/spec/**` | `3000 / 8000` | `IN_PROGRESS` | `-` |

## 3. IMMUTABLE ARCHITECTURAL INVARIANTS (DO NOT RE-LITIGATE)
1. **Design Constitution Enforcement:** Banned default AI colors (indigo/purple/hue 245-290). Optical concentric radius $R_o = R_i + p$. Tabular numbers for all metrics (`font-variant-numeric: tabular-nums`).
2. **Reflexion Verification Rule:** Retrying failing tests requires a 12-char failure signature and causal hypothesis in `reflection_log.md` (no blind edits).
3. **Ticket Diff Guard:** All file modifications must stay strictly within declared `allowed_paths` of active ticket.
4. **Hermetic Sandboxing:** No destructive shell commands (rm -rf, drop table, external ssh/scp blocked).

## 4. APPEND-ONLY HANDOFF LEDGER (CHRONOLOGICAL WAL)
- `[{timestamp}] [bootstrap_project] [AG1]` Workspace initialized with Planner System 3.0 ({project_mode} mode).
"""

PLANNER4_DIRS = ["tickets", "sealed", "baselines", "work", "reports", "runs", "escalations", "prompts"]
PLANNER4_SCRIPTS = ["orchestrator.py", "swarm.py", "verifier.py", "dag_planner.py"]


def inject_planner4(staging_dir: Path, planner_root: Path) -> bool:
    """Additive: when the planner root ships the 4.0 engines (core/ + orchestrator.py ...), copy them to scripts/planner4/
    and create the .planner/ runtime folders. Returns True when something was injected."""
    if not (planner_root / "orchestrator.py").exists() or not (planner_root / "core").is_dir():
        return False
    dest = staging_dir / "scripts" / "planner4"
    dest.mkdir(parents=True, exist_ok=True)
    for name in PLANNER4_SCRIPTS:
        if (planner_root / name).exists():
            shutil.copy2(planner_root / name, dest / name)
    shutil.copytree(planner_root / "core", dest / "core", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules"))
    for d in PLANNER4_DIRS:
        keep = staging_dir / ".planner" / d
        keep.mkdir(parents=True, exist_ok=True)
        (keep / ".gitkeep").write_text("", encoding="utf-8")
    return True


def detect_project_mode(target_root: Path) -> str:
    """Detect if target directory has existing source code (brownfield) or is empty/new (greenfield)."""
    code_extensions = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".c", ".cpp", ".php", ".rb"}
    ignore_dirs = {".git", ".quarantine", ".planner_staging_", "docs", "tests", "scripts", "node_modules", "dist", "build"}
    
    for item in target_root.glob("**/*"):
        if any(part in ignore_dirs or part.startswith(".") for part in item.parts):
            continue
        if item.is_file() and item.suffix in code_extensions:
            return "BROWNFIELD"
    return "GREENFIELD"

def find_planner_root() -> Path:
    """Locate the root of planner-system-3.0."""
    curr = Path(__file__).resolve()
    for parent in [curr] + list(curr.parents):
        if (parent / "tools" / "cartograph.py").exists() and (parent / "design" / "DESIGN_CONSTITUTION.md").exists():
            return parent
            
    candidates = [
        Path("/home/dapp/Projects/Planner_AI/planner-system-3.0"),
        Path(r"C:\Users\lenovo\Downloads\Planner AI\planner-system-3.0"),
        Path.home() / "Projects" / "Planner_AI" / "planner-system-3.0",
        Path.home() / ".gemini" / "config" / "skills" / "planner-3-engine"
    ]
    for c in candidates:
        if c.exists() and (c / "tools" / "cartograph.py").exists():
            return c
        if c.exists() and (c / "scripts" / "cartograph.py").exists():
            return c
    return curr.parent.parent

def bootstrap_project(target_root: Path, planner_root: Path = None) -> None:
    target_root = target_root.resolve()
    manifest_path = target_root / ".planner_manifest.json"
    
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if data.get("version", "").startswith("3."):
                print(f"[OK] Workspace already initialized with Planner System {data.get('version')} at {target_root}")
                return
        except Exception:
            pass

    if planner_root is None:
        planner_root = find_planner_root()

    print(f"[BOOTSTRAP 3.0] Initializing AI Planner System 3.0 at: {target_root}")
    target_root.mkdir(parents=True, exist_ok=True)
    
    mode = detect_project_mode(target_root)
    print(f"[DETECTION] Project mode identified as: {mode}")

    staging_dir = Path(tempfile.mkdtemp(prefix=".planner_staging_", dir=target_root))

    def cleanup(signum=None, frame=None):
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        if signum:
            print("\n[ABORTED] Workspace initialization cancelled. Cleaned up staging.", file=sys.stderr)
            sys.exit(130)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        # 1. Create required directories
        for rel_dir in REQUIRED_DIRS:
            (staging_dir / rel_dir).mkdir(parents=True, exist_ok=True)

        # 2. Write staging .gitignore if not present
        (staging_dir / ".gitignore").write_text(GITIGNORE_CONTENT, encoding="utf-8")

        # 3. Write initial progress.md
        now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if mode == "BROWNFIELD":
            initial_phase = "CARTOGRAPHY"
            next_action = "Execute Gate -2 Cartography via `python3 scripts/cartograph.py . --out docs/spec/CARTOGRAPHY.json`."
        else:
            initial_phase = "DISCOVERY"
            next_action = "Conduct Gate 0 Socratic Discovery & Winning Demo extraction via Gemini Flash/Pro."

        prog_content = INITIAL_PROGRESS_MD_TEMPLATE.format(
            timestamp=now_utc,
            project_mode=mode,
            initial_phase=initial_phase,
            next_action=next_action
        )
        (staging_dir / "progress.md").write_text(prog_content, encoding="utf-8")

        # 4. Copy 3.0 tools into scripts/
        tools_src = planner_root / "tools"
        if not tools_src.exists():
            tools_src = planner_root / "scripts"

        if tools_src.exists():
            for script_name in ["cartograph.py", "repomap.py", "design_lint.py", "ticketc.py", "sandbox.py", "odcat.py"]:
                src_file = tools_src / script_name
                if src_file.exists():
                    shutil.copy2(src_file, staging_dir / "scripts" / script_name)
            
            gate3_src = tools_src / "gate3"
            if gate3_src.exists():
                for g3_file in gate3_src.glob("*.py"):
                    shutil.copy2(g3_file, staging_dir / "scripts" / "gate3" / g3_file.name)
            
            sg_src = tools_src / "sgrecipes"
            if sg_src.exists():
                dest_sg = staging_dir / "scripts" / "sgrecipes"
                dest_sg.mkdir(parents=True, exist_ok=True)
                for sg_file in sg_src.glob("*.yml"):
                    shutil.copy2(sg_file, dest_sg / sg_file.name)
            print("[SCRIPTS] Injected Planner 3.0 tools into scripts/")

        # 4b. Planner 4.0 engines (optional, additive)
        planner4 = inject_planner4(staging_dir, planner_root)
        if planner4:
            print("[PLANNER 4.0] Injected core/ engines, orchestrator, swarm, verifier into scripts/planner4/ and created .planner/")

        # 5. Copy Design Constitution & tokens
        design_src = planner_root / "design"
        if not design_src.exists():
            design_src = planner_root / "references"

        if design_src.exists():
            for fname in ["DESIGN_CONSTITUTION.md", "tokens.css"]:
                fpath = design_src / fname
                if fpath.exists():
                    shutil.copy2(fpath, staging_dir / "docs" / "spec" / fname)
            print("[DESIGN] Injected Design Constitution & tokens into docs/spec/")

        # 6. Copy Gates
        gates_src = planner_root / "gates"
        if not gates_src.exists():
            gates_src = planner_root / "references"

        if gates_src.exists():
            for gpath in gates_src.glob("*.md"):
                shutil.copy2(gpath, staging_dir / "docs" / "spec" / "gates" / gpath.name)
            print("[GATES] Injected 7 Gate specifications into docs/spec/gates/")

        # 7. Copy Catalog (FACETS.md & bundled open design systems if available)
        cat_src = planner_root / "catalog"
        if cat_src.exists():
            if (cat_src / "FACETS.md").exists():
                shutil.copy2(cat_src / "FACETS.md", staging_dir / "catalog" / "FACETS.md")
            if (cat_src / "curation.json").exists():
                shutil.copy2(cat_src / "curation.json", staging_dir / "catalog" / "curation.json")
            if (cat_src / "source").exists():
                dest_src = staging_dir / "catalog" / "source"
                dest_src.mkdir(parents=True, exist_ok=True)
                for item in (cat_src / "source").iterdir():
                    if item.is_dir():
                        shutil.copytree(item, dest_src / item.name, dirs_exist_ok=True)
            print("[CATALOG] Injected bundled Open Design systems into catalog/")

        # 8. Inject Swarm Configuration
        swarm_cfg = {
            "swarm_py": "/home/dapp/AntigravityProfiles/orchestrator/swarm.py",
            "swarm_py_windows": r"C:\Users\lenovo\AntigravityProfiles\orchestrator\swarm.py",
            "model_pattern": "gemini-3.8-flash-{effort}",
            "default_lead": 1,
            "argon_capable_leads": [1, 6, 8],
            "workers": {
                "1": {"role": "Lead Architect & System Integrator", "ports": "3000-3099 / 8000-8099", "tier": "argon_capable"},
                "2": {"role": "Backend 3NF & ACID Database", "ports": "3100-3199 / 8100-8199", "tier": "flash_worker"},
                "3": {"role": "Frontend Open Design & Kinetic UI", "ports": "3200-3299 / 8200-8299", "tier": "flash_worker"},
                "4": {"role": "QA Test Harness & Adversarial Fuzzing", "ports": "3300-3399 / 8300-8399", "tier": "flash_worker"},
                "5": {"role": "API Endpoints, Middleware & Services", "ports": "3400-3499 / 8400-8499", "tier": "flash_worker"},
                "6": {"role": "Data Pipeline, Caching & Performance", "ports": "3500-3599 / 8500-8599", "tier": "argon_capable"},
                "7": {"role": "DevOps, Launcher & Seed Data Factory", "ports": "3600-3699 / 8600-8699", "tier": "gemini_plus"},
                "8": {"role": "Arbiter, AppSec & Escalation Solver (Planner 4.0)", "ports": "3700-3799 / 8700-8799", "tier": "argon_capable"}
            },
            "presets": {
                "eco": "100% Gemini 3.8 Flash across all 7 workers (limitless quota)",
                "pro-solo": "1 Argon Thinker Lead (W1 or W6) + 5 Flash Workers + 1 DevOps Seeder",
                "pro-allout": "Dual-Argon Council (W1 + W6) + 4 Flash Workers + 1 DevOps Seeder"
            },
            "default_timeout": 900,
            "retries": 1,
            "quota_wait_minutes": 60,
            "max_quota_waits": 4,
            "stop_on_gate_failure": True
        }
        (staging_dir / "swarm.config.json").write_text(json.dumps(swarm_cfg, indent=2), encoding="utf-8")

        # 9. Atomic promote from staging to target
        for item in staging_dir.iterdir():
            dest = target_root / item.name
            if not dest.exists():
                shutil.move(str(item), str(dest))
            else:
                if item.is_dir():
                    for sub in item.glob("**/*"):
                        if sub.is_file():
                            rel = sub.relative_to(item)
                            tf = dest / rel
                            tf.parent.mkdir(parents=True, exist_ok=True)
                            if not tf.exists():
                                shutil.copy2(sub, tf)

        # 10. Ensure Git initialized
        git_dir = target_root / ".git"
        if not git_dir.exists():
            try:
                subprocess.run(["git", "init"], cwd=str(target_root), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print("[GIT] Initialized repository (unborn HEAD).")
            except Exception as e:
                print(f"[GIT WARNING] Could not run git init: {e}", file=sys.stderr)

        # 11. Write Manifest
        manifest_data = {
            "system": "AI Planner System 3.0",
            "version": "3.0.0",
            "project_mode": mode,
            "status": "READY_FOR_" + initial_phase,
            "ports": {
                "AG1": {"dev": "3000-3099", "api": "8000-8099", "role": "Lead Architect (Argon)"},
                "AG2": {"dev": "3100-3199", "api": "8100-8199", "role": "Backend 3NF/ACID"},
                "AG3": {"dev": "3200-3299", "api": "8200-8299", "role": "Frontend Open Design"},
                "AG4": {"dev": "3300-3399", "api": "8300-8399", "role": "QA Test Harness"},
                "AG5": {"dev": "3400-3499", "api": "8400-8499", "role": "API Endpoints & Middleware"},
                "AG6": {"dev": "3500-3599", "api": "8500-8599", "role": "Data Pipeline & Caching (Argon)"},
                "AG7": {"dev": "3600-3699", "api": "8600-8699", "role": "DevOps, Launcher & Seed Data Factory (Plus)"},
                "AG8": {"dev": "3700-3799", "api": "8700-8799", "role": "The Arbiter: AppSec, concurrency & escalation solver (Argon-capable)"},
            },
            "created_at_utc": now_utc,
        }
        if planner4:
            manifest_data["planner4"] = {"scripts": "scripts/planner4", "state": ".planner",
                                         "note": "4.0 engines are installed; the manifest version stays 3.0.0 until the workspace is migrated deliberately."}
        manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
        print(f"[SUCCESS] Workspace initialized with Planner System 3.0 at {target_root}")

    finally:
        cleanup()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Planner System 3.0 Atomic Bootstrapper")
    parser.add_argument("--target", type=str, default=".", help="Target workspace path")
    parser.add_argument("--planner-root", type=str, default=None, help="Path to planner-system-3.0 root")
    args = parser.parse_args()
    
    bootstrap_project(Path(args.target), Path(args.planner_root) if args.planner_root else None)
