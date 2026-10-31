---
name: planner-4-engine
description: Contract-First, Bicameral 8-Worker Multi-Agent Planning Engine 4.0 with Sealed Oracles, AST Mutation Gates, APCA Kinematics, and SQLite WAL Blackboard
---

# Planner 4.0 engine - operating manual

Planner 4.0 turns a plan into signed **Contract-First tickets** and runs them through a fleet of **8 workers** that cannot grade
their own homework: one worker writes the contract, another seals the acceptance tests in a vault the implementers never see,
the implementers write code, an adversary tries to break it, and a mutation gate decides whether the tests are strong enough.
Everything is files plus a SQLite blackboard. Python standard library only (Node + Playwright only for the UI linter, Node +
Stryker only for JS/TS mutation testing).

Talk to the user in their language (default Indonesian); write artifacts in English. Mark uncertainty. Never say "done" while
a check is `INCONCLUSIVE`.

## 0. Entry point

`bin/planner4` (Linux, macOS, WSL2) and `bin/planner4.bat` (Windows) forward to the engine. The engine is found in this order:
`$PLANNER4_HOME`, then the repository this skill lives in, then an `engine/` folder next to this file. `planner4 doctor` prints
what it found: Python, the MASP table, Node, `ast-grep`, the account profiles and whether the HMAC key is set.

| Command | Does |
|---|---|
| `planner4 compile plan.yaml --out .planner/tickets --board .planner/board.db [--flat] [--hmac-env PLANNER_LAWGIVER_KEY]` | plan -> signed tickets, `dag.json`, symbol locks (`dag_planner.py`) |
| `planner4 view --root .` | waves, workers, effective tiers, ports (`orchestrator.py plan`) |
| `planner4 run --root . --executor antigravity --cmd "<headless agent command>" --lead 1 --standby -v` | run the tickets (`orchestrator.py run`) |
| `planner4 status --root .` | blackboard state of every ticket; a rerun resumes |
| `planner4 verify run --ticket T --scope worker_backend --root . --workspace W` | the 12 ordered checks for one scope (`verifier.py`) |
| `planner4 swarm parallel --spec tasks.json` | legacy-compatible fan-out with leases and MASP ports |
| `planner4 fleet table \| ports AG3 T-1 \| scan AG5 . f.js` | fleet table, deterministic ports, static port scan |
| `planner4 bridge profiles \| check \| env --worker AG3` | account profile mapping and the sanitized task environment |
| `planner4 golden` / `planner4 test` | end-to-end golden benchmark / whole unit-test suite |

## 1. The fleet: 8 active workers

All eight work at the same time in non-overlapping domains. A worker writes only inside the `allowed_paths` of its scope in the
signed ticket; the verifier rejects anything else.

| Worker | Role | Scope (`worker_scopes` key) | Dev ports | API ports |
|---|---|---|---|---|
| **AG1** The Lawgiver | writes and signs contracts, shared SCIP symbols and stubs; never tests, never implementation | - | 3000-3099 | 8000-8099 |
| AG2 | Backend DB (3NF DDL, ACID invariants, no floating-point money) | `worker_db` | 3100-3199 | 8100-8199 |
| AG3 | Frontend UI craft (state machine, optical trim, spring kinematics, APCA) | `worker_ui` | 3200-3299 | 8200-8299 |
| AG4 | Adversarial QA guard, zero-sum bounty hunter | `worker_qa_guard` | 3300-3399 | 8300-8399 |
| AG5 | Backend API and services | `worker_backend` | 3400-3499 | 8400-8499 |
| **AG6** The Inquisitor | writes the acceptance tests from the signed contract and seals them; never code | - | 3500-3599 | 8500-8599 |
| AG7 | DevOps, launcher, seed data | `worker_devops` | 3600-3699 | 8600-8699 |
| **AG8** The Arbiter | AppSec and concurrency hardening, arbiter of QA bounty disputes, escalation solver | `worker_security` | 3700-3799 | 8700-8799 |

**MASP port isolation.** Dev ports `3000-3799`, API ports `8000-8799`, 100 of each per worker, never shared. A worker's servers
bind only inside its own ranges and it never connects into another worker's range. The task environment carries `DEV_PORT`,
`PORT`, `API_PORT`, `MASP_DEV_RANGE`, `MASP_API_RANGE`. The verifier scans for literals that break the rule; computed ports are
invisible to a static scan (say so, do not claim enforcement).

**The three Argon Titans** are the architects AG1, AG6 and AG8 - the accounts that can run on the premium tier. The other five
run on the standard tier. A ticket carries a *tier*, never a model name; you map tiers to your models at run time with
`--model-premium NAME --model-standard NAME`.

**Separation of powers** (enforced by `fleet.check_independence`): AG1 authors, AG6 seals, neither implements; AG4 never
implements what it attacks; AG8 may implement only its own `worker_security` scope or as a declared escalation solver, and then
it neither arbitrates, audits nor attacks that ticket. When AG8 took part in a ticket the arbiter is AG6 instead.

## 2. The Chameleon hot-swap (quota rotation)

Premium quota is scarce, so exactly one architect carries it per run: the **lead**. `--lead 1|6|8` chooses which (default 1).
The lead runs on the premium tier, the other two architects on standard, and the roles never change (a lead of 6 still only
seals tests; it does not write contracts). Rotate the lead when one account's premium quota is spent.

AG6 and AG8 are **chameleons**: standard normally, promoted to premium for a rescue. With `--standby`, once a scope has failed
`--standby-after` times (default 2 - Reflexion retries >= 2) AG8 is summoned as the **escalation solver**: premium tier, running
in AG8's own account but with the failed worker's scope, write paths and ports, for exactly one extra attempt. If AG8 itself owns
the failing scope it retries it, promoted. Nothing else is ever promoted. If the solver also fails the ticket is ESCALATED with
a written report (`.planner/escalations/<ticket>.md`); `planner4 run` again resumes with a fresh round budget and the same oracle.

## 3. Workflow

1. **Compile.** Write `plan.yaml` (items with `id`, `goal`, `touches`, `depends_on`, `symbols`, optional `scopes`). Run
   `planner4 compile plan.yaml --out .planner/tickets --board .planner/board.db`. Each ticket has `ticket_meta`,
   `shared_contracts` (SCIP symbols with exact signatures locked by hash) and `worker_scopes`. Unknown keys are errors. Paths
   are routed to scopes by heuristics (tests -> AG4, `.sql` -> AG2, UI files -> AG3, Docker/CI -> AG7, a `security/` directory
   -> AG8, the rest -> AG5); `roles:` overrides them. The `sha256:` signature is tamper-evidence; `hmac-sha256:` needs the key in
   `PLANNER_LAWGIVER_KEY` (environment only, never in a file or on a command line).
2. **View.** `planner4 view` lists waves, workers, effective tiers and ports. Read it before spending quota.
3. **Run.** The orchestrator, per ticket: AG1 stubs -> AG6 seals the oracle (every `INV-NN` named, and it must FAIL on the
   stubs or it is vacuous and deleted) -> workers in private workspaces with path leases -> per-scope verifier -> staging merge
   with conflict detection -> sealed vaults on the merged tree -> AG4 bounty -> mutation gate (kill score >= 0.92 by default)
   -> apply to the real tree -> DONE. A failure returns oracle-safe feedback (ids and counts, never expected values); the same
   failure signature twice, or `--max-attempts` rounds, escalates.
4. **Verify by hand** when needed: `planner4 verify run --ticket T --scope S --root . --workspace W`. Twelve ordered checks:
   `contract, scope, secrets, ports, forbidden, symbols, invariants, weakening, visible, sealed, mutation, ui`. Exit 0 pass,
   1 fail, 3 inconclusive, 2 tooling error. `SELF_PASS` and `PARTIAL_PASS` exit 0 but never close a ticket. Only
   `mutation, symbols, invariants, ui, visible, forbidden` can be waived, explicitly and on the record; `contract, scope, sealed`
   never.
5. **Resume / inspect.** `planner4 status`; a crashed run restarts from the stored state with the same command.

**AG4's bounty.** `U = alpha*falsified + beta*kill_ratio - gamma*false_positives` (defaults 1 / 2 / 1.5; the weights are this
implementation's choice and are configurable in `Policy`). The implementer may answer a failing claim with
`DISPUTE: <test id> - <reason>`; the arbiter (AG8, or AG6 when AG8 took part) answers `VERDICT: UPHELD|REJECTED`. A rejected claim
costs AG4 and its test is dropped.

## 4. Running real agents: the Antigravity bridge

One worker is one agent account, and an account is one profile directory:

| | AG1 | AG2..AG8 |
|---|---|---|
| Windows | `%USERPROFILE%\AppData\Roaming\Antigravity` | `%USERPROFILE%\AppData\Roaming\Antigravity<N>` |
| Linux / WSL2 | `<home>` (config in `~/.gemini/antigravity`) | `<home>/AntigravityProfiles/Account<N>` |

`<home>` is the current user's home (or `PLANNER_AG_HOME`); `PLANNER_PROFILE_ROOT` moves the root of AG2..AG8. On Linux an
account profile is selected by pointing `HOME` at it; on Windows the profile is exported as `PLANNER_PROFILE_DIR` and offered
as `{profile_dir}` in your command template. **The bridge does not know Antigravity's command-line flags and invents none:**
the headless agent command is yours (`--cmd` or `PLANNER_EXECUTOR_CMD`), with placeholders `{prompt_file} {workspace} {model}
{effort} {worker} {ticket} {task} {profile_dir} {account}`. The prompt travels through a file, never a shell string. A missing
profile directory is an error before anything runs. Check it with `planner4 bridge check`.

Each task gets a scrubbed environment: allow-listed variables only, no tokens or keys. Credentials a CLI needs are named
explicitly with `--pass-env NAME,...`. Nothing here tells you to skip permission prompts, and nothing may.

## 5. Hard rules for every worker

- Write only inside your scope's `allowed_paths`; never `.env`, keys, `.planner/**` or `.git/**`.
- Never edit sealed tests, `conftest.py`, `pytest.ini` or gate configuration to get green. Never weaken a test: the verifier's
  weakening audit catches removed asserts, skips, swallowed exceptions and widened tolerances.
- Bind servers only in your own MASP ranges. No mocks of the unit under test unless the contract allows them.
- Before a retry, state a falsifiable reflection (`REFLECTION: ...`) that differs from the earlier attempts.
- `INCONCLUSIVE` is not a pass. A ticket is DONE only when the orchestrator says so.

## 6. Honest limits (state them to the user)

- Not a container: agents run as your OS user. For untrusted agents use a container or a separate user.
- Tested on Linux with scripted fake agents; native Windows 11 and WSL2 paths are unit-tested through injection, not on those machines.
  Real-agent quota and cost behaviour is unmeasured.
- Equivalent mutants cannot be detected; the 0.92 threshold plus the bounty loop mitigate, they do not prove.
- The static port scan sees literals only. The TypeScript symbol scan is lexical, not `tsc`.
- Full design: `docs/ARCHITECTURE.md`; audit of every engine and its limits: `docs/ENGINE_AUDIT.md`.
