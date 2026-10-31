# Engine audit - Planner AI 4.0

Date: 2026-10-25. Scope: the four core engines of the spec (blackboard, sealed gate, trace-slice plugin, CDP linter) plus the
engines added around them (worktree, mutation gate, verifier, fleet, orchestrator). Evidence comes from the deep research in
`docs/RESEARCH_REPORT_ID.md` (Indonesian, 18 repositories read at source level) and from running the code (statements marked
"measured" were executed in this build, everything else is a reasoned claim and says so).

Rule used throughout: **a gate may only be as trustworthy as its worst "pass for the wrong reason" path.** Every engine was
audited for the cases where it returns success on empty, stale, truncated or attacker-controlled input.

## 1. Blackboard (`core/coordination/blackboard.py`)

| Prototype weakness | Evidence | Fix |
|---|---|---|
| Lease TTL alone: a worker that sleeps through its TTL still writes | OpenHands/Letta pattern: last writer wins; no fencing in any surveyed repo | Monotonic **fencing token** per path; `post(..., fence=)` and `require()` reject stale tokens; rows are never deleted so a token never repeats |
| `pull()` advanced the cursor without a transaction | SQLite docs: read-to-write upgrade on a stale snapshot fails with SQLITE_BUSY that waiting cannot cure | Every read-modify-write is `BEGIN IMMEDIATE`; connection uses `isolation_level=None` (no `autocommit=` kwarg, which is a `TypeError` before 3.12); explicit busy retry with jitter |
| Path equality was string equality | Windows and WSL2 workers disagree on case and separators | One shared normaliser (`platform_compat.normalize_rel_path`) and real glob/directory overlap (`patterns_overlap`) |
| WAL on any filesystem | SQLite WAL needs same-host shared memory; WSL2 `/mnt/c` is 9p | `filesystem_kind()` reads `/proc/self/mountinfo`; network/9p filesystems get `journal_mode=DELETE`, WAL on them is refused. Opening the same DB from Windows and WSL2 is documented as forbidden |
| SQLite WAL-reset bug | affects 3.7.0 - 3.51.2, fixed in 3.51.3 (backports 3.50.7, 3.44.6) | When the linked SQLite is in the affected range the board falls back to the rollback journal (`journal_mode=DELETE`, no WAL-reset bug) and records `wal_bug` in its filesystem info; `PLANNER_BOARD_ALLOW_BUGGY_WAL=1` overrides deliberately. `journal_size_limit=64 MiB`. Multi-process mutex and posting tests run several OS processes against one database |
| Summaries were free text read by other agents | prompt-injection channel between workers | Summaries are sanitised (control chars, bidi overrides, newlines, 240-char cap); payloads are pointers (`ref`, `sha`), never file bodies |
| Prune deleted unread events | - | Pruning never removes events past any worker's cursor |
| Ticket state could be clobbered | - | Compare-and-set transitions (`expect=`), explicit state machine; illegal moves raise `IllegalTransition` |

Not done / honest limits: fencing protects state in the DB, **not files in a worktree**; that is why workers write to private
workspace copies and one integrator merges (see ARCHITECTURE.md). Lease time uses wall-clock milliseconds with a grace margin;
a machine whose clock jumps by minutes can still expire a lease early. After a power loss `synchronous=NORMAL` can resurrect an
old lease or cursor.

## 2. Sealed gate (`core/verification/sealed_gate.py`, `weakening.py`, `worktree.py`)

| Prototype weakness | Fix |
|---|---|
| Missing vault returned `passed: True` (oracle silently off) | Fails closed (`NO_SEALED_TESTS`) unless the caller explicitly allows it |
| exit code 0 trusted | JUnit XML must show tests > 0, zero failures/errors, **zero skips**, and at least the count recorded at sealing time. pytest exit 5 is a failure |
| Worker could edit `conftest.py` / `pytest.ini` | The gate supplies its own ini (`-c`), `--rootdir` and `--confcutdir` = the vault; worker conftest is never collected |
| Expected values leaked through assertion text | Default `reveal="ids"`: failing test ids and counts only. `summary` is for the orchestrator/Inquisitor, never forwarded to a worker |
| Vault could be modified | sha256 manifest per file, extra/missing/modified file = `VAULT_TAMPERED`, files made read-only. The orchestrator **refuses to silently re-seal** a damaged vault |
| Expected test count needed a collect-only run, which fails because the module does not exist yet (contract-first) | Count derived statically from the AST at sealing time |
| Counting asserts as an anti-cheat | `weakening.py` analyses every test function (assert strength, skips, `try/except` swallowing, `if False`, shrinking parametrize, tolerance widening, deleted test files, config edits). Used as a tripwire, not as proof |
| Manifest could be rewritten together with the tests (plain sha256 authenticates nothing) | Optional HMAC-SHA256 over the manifest with `PLANNER_INQUISITOR_KEY` (>= 16 bytes, environment only): a stripped MAC is `VAULT_UNAUTHENTICATED`, a forged one `VAULT_HMAC_INVALID`, a bad key `VAULT_KEY_INVALID`; the key is never given to worker processes. Without a key the vault is tamper-evident against accidents only |
| Oracle that already passes on the stub | The orchestrator runs each new vault against the unimplemented stubs: if it passes it is **VACUOUS** and the vault is deleted |
| Subprocess timeouts left grandchildren | `run_process` starts a new process group/session and kills the tree (`killpg` / `taskkill /F /T`), output to bounded buffers, `shell=False`, allow-listed environment |

Not done / honest limits: this is not a container. Code running inside the pytest process can still lie to pytest, read the
file system as the same OS user and (without `unshare`) open sockets. Run the gate under a separate OS user or container for
untrusted agents. Network isolation uses `unshare -rn` on Linux/WSL2 when available (`isolate_network="auto"`); on native
Windows there is no equivalent and the gate says so.

## 3. Trace slice (`core/verification/trace_slice_plugin.py`)

| Prototype weakness | Fix |
|---|---|
| Output cut at the budget with `raw[:N]` produced **broken JSON exactly when the failure was big** | The payload is shrunk step by step until the serialised line fits; always valid JSON |
| Rendering values called user `__repr__` | Never called; objects are summarised from their instance dict; numpy-like from shape/dtype |
| Secrets in locals | Redaction by name (password, token, ...) and by value (AWS/GitHub/Slack/JWT/PEM, long high-entropy strings) before anything is written |
| Append races under xdist | One file per process, single `O_APPEND` write per record; `read_slices()` merges and dedupes by signature |
| Windows path case, fixture errors | Case-insensitive "is this my code"; setup-time errors captured; settings per session, not import-time globals |
| Plugin failing the run | Whole hook body guarded; the plugin never changes the report outcome |

The plugin loads in a project that ships its own `core` package (the helper that decides "secret-looking name" is loaded by file
path as a fallback).

Not done: Python 3.12+ `sys.monitoring` is not used (the post-mortem approach works on 3.10-3.13 without it).

## 4. CDP kinematic/optical linter (`core/ui/`)

| Claim in the spec | Reality found | Handling |
|---|---|---|
| APCA on `getComputedStyle` colours | `APCAcontrast()` takes luminance only; transparent text/backgrounds are not blended. Ignoring alpha gives Lc 106 instead of 66.9 for `rgba(0,0,0,.5)` on white | Text and every background layer are composited to sRGB before calling the APCA maths (`lib/apca.mjs`); gradients/images/backdrop-filter => **inconclusive** (never pass) |
| `textBoxTrim !== undefined` detects trimming | True in every 2026 engine | `CSS.supports('text-box-trim','trim-both')` plus a non-default computed value and edge |
| Sample motion with rAF | Wall-clock, biased by one-sided differences | Seek-based sampling of Web Animations / CSS where possible, `page.clock` for JS; spring zeta estimated from successive extremes; interruption continuity checked by fitting the post-join segment |
| APCA as a hard gate | WCAG 3 has not selected an algorithm | APCA findings are labelled advisory next to WCAG 2.x ratios |
| ESM / Playwright pitfalls | `networkidle`, missing timeouts, unresolved ESM | Explicit timeouts, `createRequire` resolution, executable existence check, documented exit codes (0 verified, 1 rejected, 2 tooling error, 3 inconclusive) with JSON on stdout |

`cdp_linter.py` is the Python bridge: verdicts `HUMAN_CRAFT_VERIFIED | REJECTED_AI_SLOP | INCONCLUSIVE | TOOLING_ERROR`;
only the first is a pass. Real Chromium runs are part of `tests/test_verifier.py` and `tests/test_core_cdp_linter.py`.

Honest limits: only `transform/translate/scale/opacity` of the first matched element are sampled; "feel" cannot be judged by
a headless run; the three CDP domains that would help are experimental, so the public APIs are preferred.

## 5. Engines added in this build

* **`core/worktree.py`** - baseline snapshots and hash-based diffs. Needed because `git diff` misses untracked files, where a
  worker hides things. `.planner` is excluded on purpose (a worker cannot hide changes in it, and cannot have them merged).
* **`core/verification/mutation_gate.py`** - own AST mutation engine, diff-scoped. Rules that cannot be gamed: baseline must be
  green; mutants only on changed lines; `# pragma: no mutate` is ignored **and** reported; exit 5 (no tests) is not a kill;
  timeouts count as kills; zero mutants for executable changes fails; INCONCLUSIVE is never a pass. Measured: equivalent
  boundary mutants (`<` -> `<=` where both return the same value) survive for perfect tests, which is why the production
  threshold is 0.92 and not 1.0.
* **`core/verification/mutation_adapters.py`** - StrykerJS (checked against a real Stryker run: `--mutate file:a-b`, report
  schema 1.0, JSON reporter default path) and mutmut 3.8.0 (checked against a real run: `results --all true`, `show NAME`).
  mutmut is Linux/macOS/WSL2 only and the adapter says so on Windows.
* **`verifier.py`** - 12 ordered checks, statuses pass/fail/inconclusive/na/waived; waivers are explicit and only for
  waivable checks (`contract`, `scope`, `sealed` can never be waived).
* **`core/fleet.py`, `swarm.py`, `orchestrator.py`** - see ARCHITECTURE.md.
* **`core/verification/appsec_audit.py`** - AG8's deterministic AppSec + concurrency audit (stdlib `ast`; rules `SEC-*`, `CONC-*`).
  Measured on the engine itself: no high finding in `core/`; `PRAGMA` strings (which cannot bind parameters) are low, `compile()` is
  not treated as `eval()`. It reports many `SEC-PATH` mediums on file-utility library code, because a parameter that reaches
  `open()` is exactly what such code does; that rule is for application code. Taint is intra-function and trusts sanitiser
  NAMES. Python only: other languages are listed as *not audited*.
* **`core/antigravity_bridge.py`** - OS detection, AG1..AG8 profile mapping, sanitised task environment, `--executor antigravity`.
  It invents no Antigravity flags; the Windows branch is unit-tested through injected `ntpath` paths, not run on Windows.
* **`evals/golden_e2e_run.py`** - the 7-step golden benchmark (see ARCHITECTURE.md). Bugs it found in the engine and that are now fixed
  with regression tests: `register_contract` verified HMAC-signed tickets without the key (so no HMAC ticket could ever be run), and
  `verify_against_board` compared against a row hash that the orchestrator overwrites at DONE (a standalone verify after DONE
  always failed the `contract` check).
* **`core/symbol_check.py`** - locked-signature drift detection for TypeScript (token comparison) and Python (AST interface),
  with the rule that drift cannot hide behind an untouched stub.

## 6. Platform matrix

| Capability | Linux | WSL2 (ext4) | WSL2 `/mnt/c` | Windows 11 native |
|---|---|---|---|---|
| Blackboard (WAL) | yes | yes | falls back to DELETE journal | yes (local NTFS), never share the DB with WSL2 |
| Process-tree kill | `killpg` | `killpg` | same | `taskkill /F /T` |
| Network isolation of the gate | `unshare -rn` if allowed | same | same | not available |
| Own mutation engine | yes | yes | slow | yes |
| mutmut adapter | yes | yes | - | **no** (needs `os.fork`) |
| Stryker adapter | yes | yes | slow | yes (needs Node) |
| CDP linter | yes | yes (WSLg only for headed) | - | yes |

What was actually executed: Linux (kernel 6.18, Python 3.13, Node 22, Chromium via Playwright). Native Windows 11 and WSL2 were
**not** executed in this build; the Windows paths (`taskkill`, `SO_EXCLUSIVEADDRUSE`, reserved names, `MAX_PATH` handling,
`os.replace` retry on AV locks) are written against documented behaviour and unit-tested through injection, not on a Windows
machine. First thing to run there: `python -m unittest discover tests`.

## 7. Known gaps (deliberately not hidden)

1. The verifier and gate are not containers. Use a container or a separate OS user for untrusted agents.
2. `swarm.py` is a compatible re-implementation of the contract the 3.0 orchestrator uses; the user's real
   `AntigravityProfiles/orchestrator/swarm.py` was not reachable from the build environment. Use `--executor legacy`
   (`PLANNER_LEGACY_SWARM`) to delegate to it.
3. A static port scan sees literals only; computed ports (`3000 + n`) are invisible. Dynamic enforcement needs a netns.
4. The TypeScript symbol scanner is lexical, not `tsc`; unusual declaration forms may be reported `unsupported` (never `found`).
5. Equivalent mutants cannot be detected; the 0.92 threshold and the AG4 bounty loop are the mitigation, not a proof.
6. SCIP symbol shapes for nested directories and Windows separators follow the documented grammar and the scip-typescript
   snapshots; they were not checked against a real indexer run.
7. Unmeasured quota/cost behaviour of real agents: the orchestrator was tested with scripted fake agents, not with live models.
8. The Antigravity bridge maps profiles and builds environments but cannot start Antigravity headlessly by itself: the agent command
   is the operator's. The 8-account profile layout was taken from the operator's description, not from a machine here.
9. The mutation threshold is judged per scope by `verifier.py` (that scope's vault only) and across all scopes by the orchestrator
   (union of vaults). A multi-scope ticket can pass the union and still fail a single scope's standalone mutation check; that is
   intended, and the golden benchmark verifies each scope in its own workspace.
10. Linux x86-64 is the only platform where the full suite, the evals (ast-grep 0.51.0) and the golden run were executed.
