# CLAUDE.md - Planner AI 3.0 runner guide

You are building or running Planner AI 3.0. Files are the only handoff.
Read this file first; read only the gate file you are in.

## Hard rules
1. Python standard library only. The one external binary allowed is `ast-grep`.
   No Docker, no cloud, no vector DB, no DSPy/MetaGPT/aider packages.
2. Work one milestone (M0..M6) at a time. Do not start M(n+1) until every
   acceptance command of M(n) exits 0. "Looks right" is not done.
3. Touch only the milestone's `allowed_paths`. Never edit a protected test to
   make it pass; follow gates/gate3_verification.md (Reflexion) instead.
4. After each milestone update `progress.md`: files done, exact test output,
   system state, next command. Write files to disk immediately.
5. Near a quota limit: flush files, set `Current Status: IN_PROGRESS`, write the
   resume command under "Next Actionable Steps", and stop. Do not burst.
6. No secrets in files. Never run production hosts, ssh, kubectl, DROP DATABASE.

## Gates (deterministic order)
| Gate | File | Use when |
|------|------|----------|
| -2 | gates/gate-2_cartography.md | Brownfield: map code with zero LLM first |
| -1 | gates/gate-1_pinning.md | Brownfield: evidence-bound EARS + golden master |
| 0  | gates/gate0_discovery.md | Any project: Socratic interview, approved brief |
| 1  | gates/gate1_requirements.md | EARS requirements + acceptance criteria |
| 2  | gates/gate2_architecture.md | Design + DESIGN_CONSTITUTION for UI |
| 3  | gates/gate3_verification.md | Traceability + Reflexion loop |
| 4  | gates/gate4_readiness.md | Definition of Ready; deliver only when all checked |

## Tools (all stdlib)
- `tools/cartograph.py <repo> --out CARTOGRAPHY.json`  Gate -2 in one command (map + routes + SQL + env + orphans)
- `tools/repomap.py <repo> --budget 1500`  ranked symbol map
- `tools/sandbox.py create|lock|guard`      hermetic git worktree + deny-list
- `tools/odcat.py build|search|card`        design catalog (14 bundled systems; `search --archetype ...`; never read raw DESIGN.md)
- `tools/design_lint.py page.html --strict`  anti-slop check: AI colors, concentric radius, tabular-nums, motion
- `tools/install_skill.py`                   install planner-3-engine into Antigravity + rebuild dist zip
- `tools/sgrecipes/*.yml`                    ast-grep structural queries
- `tools/gate3/failure_signature.py`         stable failure hash
- `tools/gate3/reflect_check.py`             edit blocker until a reflection exists
- `tools/ticketc.py validate|diff`           ticket schema + diff guard
- `evals/run.py`                             10 golden tasks, pass rate

## Verify
`python -m unittest discover tests` must exit 0 before any milestone is closed.

## Style
Talk to the user in their language (default Indonesian); write artifacts in
English. Mark uncertainty. Never say "ready" before Gate 4 passes.

## Planner 4.0 runtime (additive; the 3.0 rules above still apply)
Contract-First, bicameral 8-worker fleet. Design: `docs/ARCHITECTURE.md`; evidence and limits: `docs/ENGINE_AUDIT.md`; operator manual:
`skills/planner-4-engine/SKILL.md` (`bin/planner4`, `bin/planner4.bat`).
- `dag_planner.py plan.yaml --out .planner/tickets --board .planner/board.db` signed YAML contracts + `dag.json`
- `orchestrator.py plan|run|status --root .`  AG1 stubs -> AG6 sealed oracle -> workers -> verifier -> AG4 bounty (AG8 arbiter) -> mutation gate
  flags: `--lead 1|6|8` (premium quota holder), `--standby` (AG8 escalation solver), `--executor antigravity --cmd "<agent>"`
- `verifier.py run --ticket T --scope worker_backend`  12 checks; INCONCLUSIVE is never a pass; PARTIAL/SELF never close a ticket
- `swarm.py parallel --spec tasks.json`  legacy-compatible fan-out with path leases and MASP ports
- `evals/golden_e2e_run.py`  7-step end-to-end benchmark (exit 0 = all pass); `evals/run_evals.py` = alias of `evals/run.py`
- `core/`  platform_compat, coordination/blackboard, verification/{sealed_gate,weakening,trace_slice_plugin,mutation_gate,
  mutation_adapters,appsec_audit}, ui/ (CDP linter), contract_ticket, scip_symbols, symbol_check, worktree, fleet, antigravity_bridge
Rules: AG1 writes contracts only, AG6 writes sealed tests only, neither implements; AG8 implements only `worker_security` or acts as
the premium escalation solver (then AG6 arbitrates); MASP ports per worker (100 dev + 100 API: AG1=3000/8000 ... AG8=3700/8700);
a worker writes only inside its `allowed_paths`; keys come only from env (`PLANNER_LAWGIVER_KEY`, `PLANNER_INQUISITOR_KEY`) and
never reach a worker; tickets carry tiers, never model names; never edit sealed tests or gate config to get green; never mark a
ticket done on `INCONCLUSIVE`.
Verify: `python -m unittest discover tests` (3.0 + 4.0 suites) must exit 0; `python evals/golden_e2e_run.py` must exit 0.
