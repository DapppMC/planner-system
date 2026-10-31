# Progress - Planner AI 3.0 build

- **Last Updated:** 2026-10-25
- **Current Status:** COMPLETED (3.0 + 4.0 build M0-M7 + 8-worker finalization; see sections 10 and 11)

## 1. Completed Milestones
- [x] M0 Skeleton
- [x] M1 Design catalog
- [x] M2 Repo-map and sandbox
- [x] M3 ast-grep recipes
- [x] M4 Gate 3 reflexion
- [x] M5 Ticket compiler
- [x] M6 Evals and dist

## 2. Verification Output

### M0
- Files: CLAUDE.md, progress.md, gates/*.md (7), tests/test_structure.py
```
wc -l CLAUDE.md = 45
----------------------------------------------------------------------
Ran 14 tests in 1.516s

OK
```

### M1
- Files: tools/odcat.py, design/DESIGN_CONSTITUTION.md, design/tokens.css, catalog/FACETS.md (built from MOCK fixtures tests/fixtures/open-design; rebuild with real Open Design: python tools/odcat.py build --od <dir>), tests/test_odcat.py
```
----------------------------------------------------------------------
Ran 14 tests in 1.324s

OK
FACETS.md ~102 tokens (<=350)
```

### M2
- Files: tools/repomap.py, tools/sandbox.py, tests/test_repomap.py, tests/test_sandbox.py
```
----------------------------------------------------------------------
Ran 18 tests in 0.576s

OK
repomap . --budget 1500 => 241 tokens, 0.18s (limits 1500 tokens / 3s)
Ran 32 tests in 1.885s

OK
```

### M3
- Files: tools/sgrecipes/{find_api_routes,extract_sql_queries,find_deprecated_calls}.yml, tests/fixtures/sg/*, tests/test_sgrecipes.py
```
----------------------------------------------------------------------
Ran 5 tests in 0.118s

OK
ast-grep 0.51.0 used for match tests (pip install ast-grep-cli). Without the binary the 2 match tests SKIP; structure tests still run.
```

### M4
- Files: tools/gate3/failure_signature.py, tools/gate3/reflect_check.py, tests/test_reflexion.py
```
----------------------------------------------------------------------
Ran 13 tests in 0.763s

OK
Ran 50 tests in 2.710s

OK
Fixes vs manifest code: per-entry section parsing (hypothesis cannot leak from a later entry), ESCALATION.md actually written at attempt>3, repeated hypothesis blocked, fail-closed on missing args, --hook mode.
```

### M5
- Files: tools/ticketc.py, tests/test_ticketc.py
```
----------------------------------------------------------------------
Ran 19 tests in 0.368s

OK
Ran 69 tests in 3.576s

OK
Manifest code imported PyYAML (breaks the stdlib-only rule) -> replaced by a mini YAML subset parser. Added: frozen sha256 check, protected-test-in-allowed check, too-broad/escaping path checks, glob semantics (* in folder, ** across), diff guard also sees staged + untracked files.
```

### M6
- Files: evals/run.py, evals/tasks/*.json (10), tools/build_dist.py, tests/test_build_dist.py, dist/planner-system-3.0.zip
```
python -m unittest discover tests -> Ran 72 tests, OK (exit 0)
python evals/run.py -> pass rate: 10/10 (5 greenfield + 5 brownfield)
python tools/build_dist.py -> dist/planner-system-3.0.zip, 77 files, deterministic, testzip OK
Known limits: catalog/ is built from MOCK fixtures (rebuild with real Open Design); ast-grep tests SKIP when binary missing.
```

## 3. System State and Invariants
- stdlib only; ast-grep optional binary; build root is planner-system-3.0/

## 4. Next Actionable Steps (Resume Point for Next Model/Agent)
- (Planner 4.0 resume point: section 10.1.) All 3.0 milestones M0-M6 complete. Resume command (re-verify): python -m unittest discover tests && python evals/run.py
- Optional: python tools/odcat.py build --od <real open-design checkout>

## 5. Skill packaging (skill-creator phase, after M6)
- `skills/planner-3-engine/` = SKILL.md + scripts (copies of tools) + references (gates, constitution, tokens) + evals (3 cases with fixtures).
- Iteration 1 benchmark (3 cases, with vs without skill, 1 run each): pass rate 100% vs 64%; see `skills/benchmark-iter1.md`, viewer `skills/review-iter1.html`.
- Package: `skills/planner-3-engine.skill` (rebuilt in Iteration 2). Description optimization (run_loop) is NOT done yet.
- Caveat: assertions were written by the same author as the skill, so the gap is partly format-driven (CARTOGRAPHY.json, buckets).

## 6. Iteration 2 (review notes 1-4)
Date: 2026-10-24. Result: `python -m unittest discover tests` -> Ran 139 tests, OK (exit 0); `python evals/run.py` -> pass rate 10/10.

1. **Cartography in one command.** `tools/cartograph.py <repo> --out CARTOGRAPHY.json` (schema `cartography/1`): repomap ranking + entrypoints (Express/Flask/FastAPI routes, CLI, workers, events) + SQL tables + env key names (never values) + deprecated APIs + orphans. Byte-identical across runs (no timestamps). `--engine ast-grep` adds multi-line matches and falls back to regex with a warning. Tests: `tests/test_cartograph.py` (15). `gates/gate-2_cartography.md`, `CLAUDE.md` and the skill now use it.
2. **Tighter design checks.** `tools/design_lint.py page.html --strict` with rules R01-R09: AI colors (banned hex, Tailwind indigo/purple/violet, hue 245-290), concentric radius (resolves var()/calc(), `na` fails in strict), tabular-nums by inheritance (monospace accepted), backdrop blur, motion (transform/opacity only), cardception, filler copy, contrast, tokens. Tests: `tests/test_design_lint.py` (21) + good/bad fixtures. `evals/run.py` gf-05 now proves the linter by mutation (5 single mutations, 5/5 caught). Skill eval design assertions are now computed in a real browser (see `skills/benchmark-iter2.md`).
3. **Bundled catalog.** 14 curated Open Design systems in `catalog/source/` (Apache-2.0, LICENSE + NOTICE, commit e03d5c2, only manifest.json/DESIGN.md/tokens.css), archetypes editorial / minimal / workspace-light / precision-dark / industrial-warm in `curation.json`. `odcat.py build` needs no clone now; `search --archetype`; `curate` refreshes the subset. Tests: `tests/test_catalog_bundled.py` (14). `odcat build` now deletes cards of systems that are gone (a stale-card bug found while syncing: the 7 old mock cards had survived the rebuild). Brand-replica systems were deliberately not bundled. "Precision Dark" and "Industrial Warm" are Planner archetype labels over Open Design systems, not Open Design names; the closest bundled match for industrial-warm is `brutalism`.
4. **Installer.** `python tools/install_skill.py [--dry-run] [--cli] [--target DIR] [--no-dist]`: detects Windows/Linux/macOS, installs to `~/.gemini/config/skills/planner-3-engine` (Windows `%USERPROFILE%\.gemini\config\skills\`), refuses to overwrite a folder that is not this skill, verifies (frontmatter, compile, smoke run), rebuilds `dist/planner-system-3.0.zip`. Tests: `tests/test_install_skill.py` (15, OS injected, temp HOME). The target path was verified from the Google codelab, not from a live Antigravity install.
5. **Skill iteration 2.** 97% with skill vs 65% without (`skills/benchmark-iter2.md`, viewer `skills/review-iter2.html`).

Known limits: ast-grep tests SKIP without the binary; cartograph regex engine is line-based; design_lint is static (JS-built tables are checked by CSS rule coverage); the install path was not tested on a real Windows machine (simulated); description optimization not done.
Resume: `python -m unittest discover tests && python evals/run.py && python tools/install_skill.py --dry-run`

## 7. Finishing polish (after Iteration 2)
- SKILL.md `description` shortened from 764 to 209 characters, single line, no colon (test: `tests/test_structure.py`). The 250-character limit came from the user; it was not checked against Antigravity documentation.
- `tools/gate3/failure_signature.py --help` and `-h` now print usage to stdout and exit 0. Missing arguments still exit 1 (usage on stderr). A test now checks `--help` and `-h` exit 0 for all 10 CLI tools.
- Dogfooding (first attempt) used this repo itself because `C:\\dev\\cert-slicer` was not connected yet; that run exposed three weaknesses, all fixed in the follow-up below.

## 8. Final polish (follow-up)
- `cartograph.py` ignores `tests, test, __tests__, fixtures, __fixtures__, docs, doc, samples, sample, examples, example, __mocks__, e2e` for entry points AND for top_dependencies (docs copies and test referrers no longer rank). `--include-nonprod` restores them; `--ignore DIR` (repeatable) adds folders. The ignored folders are listed in the JSON `limits`.
- Generic symbols (get, set, add, save, append, join, ... ~60 names), dunder methods and names under 4 characters are skipped in top_dependencies.
- Deprecated-API matches are accepted only when the match starts in code, not inside a string literal or comment (`code_mask`, handles triple quotes and block comments). Scanning the scanner now gives 0 false positives (test).
- `__main__.py` is recognised as a CLI entry point.
- SKILL.md description: 196 characters (limit 198, test enforced). It no longer names dashboards.
- Real dogfood on `C:\dev\cert-slicer` (read-only; 33 pending git changes before and after): `python tools/cartograph.py cert-slicer --ignore scripts --ignore spikes --out evals/sample_cert_slicer_cartography.json` -> 16 entry points: 12 FastAPI routes + `__main__` in `src/certslice/web.py`, `gui.py`, `__main__.py`, and `build_installer.py`. Two runs byte-identical. Top dependencies are `src/certslice` symbols (load_config, InputError, normalize_name, Candidate, Employee...). 0 tables is correct (the project reads Excel, no SQL). Without `--ignore` the plain command also lists 11 dev scripts in `scripts/` and `spikes/ocr_spike.py` as CLI entry points; whether those count is a judgment call, so they are not ignored by default.
- Limits seen on the real project: argparse-only CLIs without a `__main__` guard are not detected (`src/certslice/cli.py`); the sample records the repo path as given (`cert-slicer`).
- Status: COMPLETED. Test suite: 152 tests OK, evals 10/10.

## 9. Antigravity Skill Integration & Master Bootstrapper (2026-10-24)
- Created `tools/bootstrap_project.py` with transactional staging-then-promote, auto Greenfield/Brownfield detection, injecting Planner 3.0 scripts, Design Constitution, bundled Open Design systems, 7 gates, and Swarm config.
- Added `tests/test_bootstrap.py` (4 tests PASS). Total suite: 156 tests OK.
- Rebuilt `dist/planner-system-3.0.zip` (252 files).
- Updated global Antigravity skill `/home/dapp/.gemini/config/skills/planner-system/SKILL.md` to version 3.0 (179-char compliant description). Seamlessly routes between Greenfield (Socratic Discovery) and Brownfield (Zero-LLM Cartography), enforcing the 3.0 Design Constitution, Reflexion verification, and dispatching to Single Agent or Multi-Agent PRO Swarm (4 Google Pro accounts).

## 10. Planner AI 4.0 build (2026-10-25, autonomous run)
Scope: PLANNER_4.0_MASTER_SPECIFICATION milestones M0-M7. Design: `docs/ARCHITECTURE.md`; engine audit with evidence and limits: `docs/ENGINE_AUDIT.md`; research (18 repos, Indonesian): `docs/RESEARCH_REPORT_ID.md` + `docs/research_notes/`.

| Milestone | Delivered | Main files |
|---|---|---|
| M0 foundation | cross-platform primitives (paths, process-tree kill, sandbox env, fs-kind detection, atomic writes) | `core/platform_compat.py`, `core/miniyaml.py` |
| M1 blackboard | SQLite blackboard with fencing tokens, BEGIN IMMEDIATE, journal policy, ticket state machine | `core/coordination/blackboard.py` |
| M2 sealed gate | vault + clean-copy oracle runs, fail-closed, anti-weakening audit, trace slice plugin | `core/verification/{sealed_gate,weakening,trace_slice_plugin}.py` |
| M3 UI linter | CDP kinematic/optical linter (alpha-aware APCA, text-box-trim, seek-based motion) and Python bridge | `core/ui/` , `tools/design_lint.py --cdp` |
| M4 contracts | Contract-First YAML tickets, SCIP symbols (Python + TS), symbol drift check, `dag_planner.py` rewrite | `core/contract_ticket.py`, `core/scip_symbols.py`, `core/symbol_check.py`, `dag_planner.py` |
| M5 fleet | 7 bicameral workers + AG8 standby, MASP port isolation, `swarm.py`, `verifier.py` (12 checks) | `core/fleet.py`, `swarm.py`, `verifier.py`, `core/worktree.py` |
| M6 mutation | own AST mutation engine (diff-scoped, 0.92 gate), Stryker + mutmut adapters verified against real tools | `core/verification/mutation_gate.py`, `mutation_adapters.py` |
| M7 orchestration | `orchestrator.py` (AG1 -> AG6 -> workers -> verifier -> AG4 bounty -> mutation gate), resume, escalation, bootstrap + dist | `orchestrator.py`, `tools/bootstrap_project.py`, `tools/build_dist.py` |

Verification (Linux, Python 3.13, Node 22, Chromium; run on 2026-10-25):
```
python -m unittest discover tests      -> Ran 708 tests in 522s, OK (skipped=3), exit 0 (Linux, Python 3.13)
python evals/run.py                    -> pass rate: 9/10 (1 skipped: ast-grep binary not installed here)
```
Real-tool checks done: mutmut 3.8.0 end to end, StrykerJS end to end (node:test via the `command` runner), Chromium for the UI linter and verifier UI check, multi-process SQLite stress in the blackboard tests.

Bugs found by the new tests while building (all fixed, kept as regression tests): out-of-scope files left in a workspace poisoned every retry (now reverted after a failed attempt); AG4 claim ids from junit did not match the implementer's DISPUTE ids (now canonicalised); disputes were not applied to stored AG4 tests; same-worker tasks ran concurrently because the board lets a worker re-claim its own paths (now serialised per worker); a drifted symbol could hide behind an untouched stub (hinted file is canonical); Stryker has no `--jsonReporter.fileName` CLI flag; mutmut config keys changed in 3.x.

Known limits (honest): not a container; native Windows 11 and WSL2 were NOT executed (Windows paths are injection-tested only); `swarm.py` is a compatible re-implementation because the real `AntigravityProfiles/orchestrator/swarm.py` was unreachable (use `--executor legacy` to delegate); the orchestrator was tested with scripted fake agents, never with live models; equivalent mutants cannot be detected; static port scan sees literals only; TS symbol scan is lexical, not `tsc`; AG4 alpha/beta/gamma defaults (1 / 2 / 1.5) are this build's choice, the spec gives none.

### 10.1 Next steps for Planner 4.0 (supersedes section 4)
- Re-verify: `python -m unittest discover tests && python evals/run.py`
- First real run on Windows 11 and on WSL2 (ext4): `python -m unittest discover tests`; fix path/locking surprises first.
- First live-agent run: write a 1-ticket plan, `python dag_planner.py plan.yaml --out .planner/tickets --board .planner/board.db`, `python orchestrator.py plan --root .`, then `run` with `--executor command --cmd "<agent CLI>"` and watch `.planner/escalations/`.
- Point `--executor legacy --legacy-script <real swarm.py>` at the existing Antigravity orchestrator and compare reports.
- Optional: build a container wrapper for the verifier/gate (stronger isolation), and a real-indexer check of SCIP symbols (`scip-typescript`).

## 11. Planner 4.0 finalization: 8-worker active swarm, Argon hot-swap, golden verification (2026-10-25)
Delivered on top of section 10 (request: `Final_Prompt_to_Claude.md`, phases 1-5).

| Phase | Delivered | Main files |
|---|---|---|
| 1 AG8 elevated | AG8 "The Arbiter" is an active worker (`worker_security`, ports 3700-3799 / 8700-8799); architects AG1/AG6/AG8; `--lead 1|6|8` premium quota rotation (`fleet.effective_tier`); `--standby` / `--standby-after` escalation solver; AG8 arbitrates AG4 disputes (AG6 when AG8 took part); stricter `check_independence` | `core/fleet.py`, `core/contract_ticket.py`, `dag_planner.py`, `swarm.py`, `orchestrator.py`, `tools/bootstrap_project.py` |
| 2 Antigravity bridge | OS detection, AG1..AG8 profile mapping (Windows `AppData\Roaming\Antigravity<N>`, Linux `AntigravityProfiles/Account<N>`), sanitised task env, `--executor antigravity`; invents no Antigravity CLI flags | `core/antigravity_bridge.py`, `tests/test_antigravity_bridge.py` |
| 3 Skill | `skills/planner-4-engine/` (SKILL.md with the requested name/description, `bin/planner4`, `bin/planner4.bat`) | `tests/test_planner4_skill.py` |
| 4 Golden run | `evals/golden_e2e_run.py` (7 steps, random per-run HMAC keys, exit 0 only if all pass), plus `core/verification/appsec_audit.py` (AG8 audit) and HMAC authentication of the sealed vault manifest (`PLANNER_INQUISITOR_KEY`) | `evals/golden_e2e_run.py`, `tests/test_golden_e2e.py`, `tests/test_core_appsec_audit.py`, `core/verification/sealed_gate.py` |
| 5 Release | full suite, evals, dist zip, docs | `tools/build_dist.py`, `docs/*`, `CLAUDE.md`, `evals/run_evals.py` (alias of `evals/run.py`) |

Real engine bugs found by the golden run (fixed, regression tests kept): `register_contract` verified HMAC-signed tickets without the key, so
no HMAC ticket could ever run; `verify_against_board` compared against a row hash the orchestrator overwrites at DONE, so a standalone verify
after DONE always failed the `contract` check. Also fixed while building: the appsec audit treated `with self._lock` as unlocked when the
attribute name did not contain "lock"; `compile()` was wrongly flagged as `eval`; `PRAGMA` strings are low severity (they cannot bind).

Verification (Linux x86-64, Python 3.13, ast-grep 0.51.0, run on 2026-10-25):
```
python -m unittest discover tests      -> Ran 859 tests in 596s, OK, exit 0 (no skips: ast-grep is installed)
python evals/run_evals.py              -> pass rate: 10/10   (alias of evals/run.py)
python evals/golden_e2e_run.py         -> golden run: PASS in ~50s (7/7 steps), exit 0
```

Honest limits: scripted agents only (no live Antigravity run); native Windows 11 and WSL2 never executed (profile mapping unit-tested by
injection); not a container; the bridge cannot start Antigravity by itself (the headless agent command is yours); the AppSec audit is a
pattern audit, Python only, with noisy `SEC-PATH` on file-utility library code; equivalent mutants undetectable; premium/standard tier names
are mapped to models by you at run time (`--model-premium/--model-standard`).

### 11.1 Next Actionable Steps (supersedes 10.1)
- Re-verify: `python -m unittest discover tests && python evals/run_evals.py && python evals/golden_e2e_run.py`
- On the real machine: `bin/planner4 doctor` (Linux/WSL2) or `bin\planner4.bat doctor` (Windows) and `planner4 bridge check` to see which of the 8 account profiles exist.
- First live-agent ticket: `planner4 compile plan.yaml ...`, `planner4 view`, then `planner4 run --executor antigravity --cmd "<your headless agent command>" --lead 1 --standby -v`.
- Run the suite on Windows 11 and on WSL2 (ext4) before trusting those paths.
