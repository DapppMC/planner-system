# Planner AI 4.0 - architecture

Planner 4.0 = Planner 3.0 (gates, tools, evals; unchanged, still passing) + a **Contract-First, bicameral multi-agent runtime**.
Python standard library only (Node + Playwright only for the UI linter, Node + Stryker only for JS/TS mutation testing).

```
plan.yaml --dag_planner.py--> .planner/tickets/<id>.yaml (signed contract) + dag.json + symbols.lock.json
                                         |
                                  orchestrator.py  <-- blackboard (.planner/board.db, SQLite)
        AG1 stubs ----> AG6 seals oracle ----> workers (AG2/AG5/AG3/AG7/AG8) in private workspaces
        (deterministic)  (red check, vault)         |  swarm.py: path lease, MASP ports, scrubbed env
                                                    v
                                     verifier.py per scope (12 checks)
                                                    v
                          staging merge -> sealed vaults on merged tree -> AG4 bounty (AG8 arbitrates) -> mutation gate (>= 0.92)
                                                    v
                                         DONE (applied to the real tree)  or  ESCALATED (.planner/escalations/<id>.md)
```

## The fleet (`core/fleet.py`): 8 active workers

| Id | Role | Scope key | Dev ports | API ports | Default tier |
|---|---|---|---|---|---|
| AG1 | The Lawgiver - writes and signs contracts, never tests or code (default lead) | - | 3000-3099 | 8000-8099 | premium |
| AG2 | DB engineer | `worker_db` | 3100-3199 | 8100-8199 | standard |
| AG3 | UI craft | `worker_ui` | 3200-3299 | 8200-8299 | standard |
| AG4 | QA zero-sum bounty hunter | `worker_qa_guard` | 3300-3399 | 8300-8399 | standard |
| AG5 | API / backend | `worker_backend` | 3400-3499 | 8400-8499 | standard |
| AG6 | The Inquisitor - writes and seals acceptance tests, never code (chameleon) | - | 3500-3599 | 8500-8599 | standard |
| AG7 | DevOps (extension) | `worker_devops` | 3600-3699 | 8600-8699 | standard |
| AG8 | The Arbiter - AppSec and concurrency hardening, arbiter of QA bounty disputes, escalation solver (chameleon) | `worker_security` | 3700-3799 | 8700-8799 | standard |

Tickets carry **tiers, never model names** (`--model-premium/--model-standard` map them at run time).

**Argon Titans and the Chameleon hot-swap.** AG1, AG6 and AG8 are the architect accounts that can run on the premium tier. Premium
quota is scarce, so exactly one of them is the **lead** per run (`orchestrator.py run --lead 1|6|8`, default 1): the lead runs
premium, the other two standard, the five other workers always standard. `fleet.effective_tier(worker, lead=, escalated=)` is the
single place that decides; roles never change (a lead of AG6 still only seals tests). AG6 and AG8 are *chameleons*: they are also
promoted to premium when escalated. With `--standby`, once a scope has failed `--standby-after` times (default 2, the Reflexion
retry count) AG8 is summoned as the **escalation solver**: premium tier, running in AG8's own account profile but with the failed
worker's scope, write paths and ports (`Task.standby_for`), for exactly one extra attempt per ticket. If AG8 itself owns the
failing scope it retries it, promoted. Nothing else is ever promoted. All eight workers are otherwise active at the same time.

**MASP port isolation.** Each worker owns one dev and one API range of 100 ports. `allocate_ports()` is deterministic per
(worker, ticket) inside the worker's own range, skips ports taken by live tasks and (with probing) ports that are busy or
Windows-reserved. `worker_env()` hands the worker `DEV_PORT`, `PORT`, `API_PORT`, `MASP_DEV_RANGE`, `MASP_API_RANGE`. The
verifier statically rejects code that binds outside the worker's ranges or connects into another worker's range. A static
scan sees literals only.

**Separation of powers** (`check_independence`): AG1 authors, AG6 seals, neither implements; the bounty hunter never implements
what it attacks; AG8 may implement only its own `worker_security` scope or act as a declared escalation solver, and on a ticket
where it did either it does not arbitrate, audit or attack that ticket (the arbiter is then AG6).

## Contract-First tickets (`core/contract_ticket.py`, `dag_planner.py`)

`dag_planner.py plan.yaml --out .planner/tickets --board .planner/board.db [--hmac-env PLANNER_LAWGIVER_KEY] [--flat]`
compiles a plan into per-ticket YAML with three sections: `ticket_meta` (id, epoch, `touches`, goal, signature),
`shared_contracts` (SCIP symbols + exact signatures, locked by hash) and `worker_scopes` (exclusive `allowed_paths`, invariants
`INV-NN`, forbidden calls, UI kinematic spec, QA `mutation_gate`). Unknown keys are errors, so a typo cannot silently disable a
guard. `sha256:` signatures are tamper-evidence; `hmac-sha256:` (key only from the environment) authenticates AG1; the
registration step and the verifier both read `PLANNER_LAWGIVER_KEY`, and past `CONTRACTED` the root of trust is the newest
`contract_signed` event (the row hash is reused for the DONE change-set fingerprint). The
authoritative record is the blackboard `contract_signed` event: a ticket edited after signing no longer matches it and the
orchestrator quarantines the ticket. `--flat` also writes Planner 3.0 flat tickets so `tools/ticketc.py` keeps working.
`dag.json` holds waves and predecessors; SCIP symbols are generated for Python (`scip-python`) and TypeScript.

## Blackboard state machine (`core/coordination/blackboard.py`)

`PLANNED -> CONTRACTED -> SEALED -> LEASED -> IN_PROGRESS -> VERIFYING -> DONE`, plus `FAILED`, `ESCALATED`, `QUARANTINED`.
Transitions are compare-and-set; an orchestrator that crashes resumes from the stored state with the same command.

## A ticket's life (`orchestrator.py`)

1. **Register** - contract verified against the board hash; `QUARANTINED` on mismatch.
2. **AG1 stubs** - `symbol_check.materialize_stubs` writes `contracts/<ticket>/<Name>.<ext>` (never overwrites).
3. **AG6 seals** - the Inquisitor writes tests under `tests/acceptance/<AGx>/` in its own workspace (path rule:
   `fleet.INQUISITOR_PATHS`); every `INV-NN` must be named; the tests must **fail** on the stubs, otherwise the vault is
   deleted as vacuous. Human-written tests can be supplied with `--sealed-dir <dir>/<ticket>/<AGx>/` (AG6 is then skipped).
4. **Lease** - baseline snapshot of the integration tree; each implementing scope gets a private workspace copy.
5. **Dispatch** - `swarm.py` runs the scopes in parallel (one worker = one agent account = one task at a time), takes path
   leases on the scope's `allowed_paths`, allocates MASP ports, runs under a scrubbed environment, heartbeats the lease.
6. **Verify per scope** - `verifier.py` (below). Failing scopes get oracle-safe feedback and a Reflexion retry. Out-of-scope
   files a failed attempt left behind are reverted to the baseline automatically.
7. **Integrate** - passing scopes are merged into a staging copy (conflict check against the baseline hash), the sealed vaults
   of all scopes run on the merged tree, stored AG4 tests re-run, the **AG4 bounty** attacks, and the diff-scoped mutation
   gate (default kill score 0.92) decides. Success applies the staging result to the real tree (again conflict-checked).
8. **Escalate** - same failure signature twice, or `max_attempts` rounds, writes `.planner/escalations/<ticket>.md` and sets
   `ESCALATED`. `--standby` gives a failed scope one extra, premium-tier attempt by AG8 (the escalation solver) first.

**Zero-sum bounty.** AG4 earns by *falsifying* (a new failing test the implementer cannot refute) and by *killing survivors*;
it loses on false positives (a failing test the arbiter rejects). Implementers may answer a claim with
`DISPUTE: <test id> - <reason>`; AG8 arbitrates (AG6 when AG8 took part in the ticket; `--arbiter AG6|AG8|auto`) and answers
`VERDICT: UPHELD|REJECTED`; a rejected claim costs AG4 and its test is dropped.
`U_QA = alpha*falsified + beta*kill_ratio - gamma*false_positives`, stored in `.planner/runs/<ticket>.json`. The spec fixes the
shape, not the weights: defaults are 1 / 2 / 1.5 (this implementation's choice, configurable in `Policy`).

## Verifier (`verifier.py`)

Twelve ordered checks: `contract`, `scope`, `secrets`, `ports`, `forbidden`, `symbols`, `invariants`, `weakening`, `visible`,
`sealed`, `mutation`, `ui`. Statuses: `pass | fail | inconclusive | na | waived`. **Inconclusive is never a pass.**
Verdicts and exit codes: `PASS` 0, `FAIL` 1, `INCONCLUSIVE` 3, usage/tooling error 2; `SELF_PASS` (level `self`, no sealed oracle
reachable) and `PARTIAL_PASS` (`--only/--skip`) exit 0 but **can never close a ticket**. Only `mutation, symbols, invariants, ui,
visible, forbidden` can be waived (explicitly, recorded in the report); `contract, scope, sealed` never. Feedback returned to a
worker is capped (1800 chars) and oracle-safe: ids and counts, never expected values or sealed source.

## Sealed vault authentication (`core/verification/sealed_gate.py`)

The vault manifest (file hashes of AG6's tests) can carry an HMAC-SHA256 made with a key that only AG6's side holds:
`PLANNER_INQUISITOR_KEY` (environment only, >= 16 bytes). Without a key the manifest is plain sha256 (tamper evidence against an
accident, not against someone who can rewrite the manifest too). With a key, a stripped MAC (`VAULT_UNAUTHENTICATED`), a forged
one (`VAULT_HMAC_INVALID`) and a malformed key (`VAULT_KEY_INVALID`) all fail closed. The key is never passed to worker processes
(`swarm` builds the task environment from an allow-list).

## Deterministic AppSec / concurrency audit (`core/verification/appsec_audit.py`)

AG8's duties need a floor that is not a model's opinion. `appsec_audit` is a stdlib-`ast` audit with rule ids `SEC-*` (eval/exec,
shell, SQL built by formatting, unsafe deserialisation, parameter-to-path taint without a sanitiser, hard-coded secrets, TLS off,
weak randomness for tokens, debug servers, world-writable modes) and `CONC-*` (a lock-owning class mutating state without the lock,
unlocked module state in a threaded module, check-then-act on the file system, blocking calls in `async def`). No inline
suppression pragmas (the operator may pass an echoed `--allow RULE:PATH`). Python only; other languages are reported as *not
audited*, never as clean. It is a pattern audit, not proof of security.

## Antigravity bridge (`core/antigravity_bridge.py`)

One worker is one agent account and an account is one profile directory. The bridge detects the OS and maps AG1..AG8 to their
profiles (Windows: `%USERPROFILE%\AppData\Roaming\Antigravity` for AG1, `...\Antigravity<N>` for AG2..AG8; Linux/WSL2: the home
for AG1 and `<home>/AntigravityProfiles/Account<N>` for the rest; `PLANNER_AG_HOME` and `PLANNER_PROFILE_ROOT` override). It builds a
sanitised task environment (`PLANNER_WORKER`, `DEV_PORT`, `API_PORT`, `MASP_DEV_RANGE`, `MASP_API_RANGE`, plus `HOME` on Linux/macOS
or `PLANNER_PROFILE_DIR` on Windows) and an execution helper behind `--executor antigravity`. **It invents no Antigravity
command-line flags**: the headless agent command is the operator's (`--cmd`), with placeholders including `{profile_dir}`.

## Operator entry point (`skills/planner-4-engine/`)

`SKILL.md` is the operating manual; `bin/planner4` (Python launcher, Linux/macOS/WSL2) and `bin/planner4.bat` (Windows) forward
to the engine (`compile`, `view`, `run`, `status`, `verify`, `swarm`, `fleet`, `bridge`, `golden`, `test`, `doctor`, `where`).

## Golden end-to-end benchmark (`evals/golden_e2e_run.py`, alias `planner4 golden`)

Seven steps on a throw-away project with scripted agents: (1) AG1 HMAC-signed two-scope contract, stubs, SCIP lock, tamper and
wrong-key refusal; (2) AG6 sealed oracle with an HMAC-authenticated vault, red on the stubs, negative controls; (3) AG5 and AG8
implement in parallel (the first AG5 attempt is lazy and must be rejected by the oracle) with disjoint MASP ports and no key in any
worker environment; (4) AG4 property tests that falsify a broken tree; (5) the mutation gate at >= 0.92 and a <= 400-token redacted
trace slice; (6) the AG8 audit, a vulnerable negative control and a threaded stress run; (7) `verifier.py` 12 ordered checks per
scope, exit 0. It proves the plumbing and the gates, not any model's quality, and it found two real engine bugs while it was built
(HMAC contracts could not be registered; the contract check failed after DONE).

## Quick start

```
python dag_planner.py plan.yaml --out .planner/tickets --board .planner/board.db --flat
python orchestrator.py plan   --root .                       # waves, workers, tiers, ports
python orchestrator.py run    --root . --executor command --cmd "<your agent CLI with {prompt_file} {workspace} {model}>" \
        --pass-env MY_AGENT_TOKEN --model-premium <name> --model-standard <name> -v
python orchestrator.py status --root .
python verifier.py run --ticket T-001 --scope worker_backend --root . --workspace .planner/work/T-001/AG5
python swarm.py parallel --spec tasks.json --executor command --cmd "..."   # legacy contract: {worker_id,prompt,workspace,...}
python tools/bootstrap_project.py <target>                   # also installs scripts/planner4 and .planner/
```

`--executor dry-run` (default) executes nothing: it is for `plan`, tests and reviewing prompts. Nothing here skips permission
prompts or runs agents with elevated rights; agent credentials reach a child process only through `--pass-env NAMES`.

## Files

```
core/platform_compat.py    paths, process-tree kill, sandbox env, filesystem kind, atomic writes (Linux / WSL2 / Windows)
core/coordination/blackboard.py   SQLite WAL blackboard: events, leases + fencing, cursors, tickets
core/verification/sealed_gate.py  vault + clean-copy oracle runs      weakening.py  anti-weakening AST audit
core/verification/trace_slice_plugin.py   <=400-token failure context for workers
core/verification/mutation_gate.py / mutation_adapters.py   diff-scoped mutation testing (own engine, Stryker, mutmut)
core/ui/                   CDP kinematic/optical linter (Node + Playwright) and its Python bridge
core/verification/appsec_audit.py   deterministic AppSec + concurrency audit (AG8)
core/antigravity_bridge.py   account profiles, sanitised task environment, executor helper
core/contract_ticket.py  scip_symbols.py  symbol_check.py  miniyaml.py  worktree.py  fleet.py
dag_planner.py  verifier.py  swarm.py  orchestrator.py
skills/planner-4-engine/   SKILL.md + bin/planner4 (+ .bat)       evals/golden_e2e_run.py   7-step golden benchmark
tests/                     3.0 suite + 4.0 suites (python -m unittest discover tests)
docs/                      ENGINE_AUDIT.md, ARCHITECTURE.md, RESEARCH_REPORT_ID.md
```
