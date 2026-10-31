---
name: planner-3-engine
description: Plan work for cheaper coding models. Map legacy repos, pin behaviour with golden tests, stop retry loops (Reflexion), scope tickets by allowed_paths, lint UI for AI slop. For legacy and migration.
---

# Planner 3.0 engine

Cheaper executor models fail in three predictable ways: they trust their own guesses about legacy code, they retry the same failing fix, and they wander outside the files they were given. This skill removes those failure modes with small stdlib-only scripts and short rules. Use the scripts instead of reasoning by hand; they are deterministic and cost almost no tokens.

Everything you produce goes into files (specs, JSON, tickets). Chat gets a short summary. Reply in the user's language (default Indonesian) and write artifacts in English unless asked otherwise.

All scripts live in `scripts/` next to this file. Call them as `python <skill_dir>/scripts/<name>.py`. Python 3.8+, no packages. `ast-grep` is optional (`pip install ast-grep-cli`; the binary is `ast-grep`, because `sg` on Linux is often the unrelated shadow-utils command). Without it fall back to grep with the same patterns and say so in the output.

## 1. Pick the path

| Situation | Path |
|---|---|
| Existing code to modernize, port, upgrade or understand | Brownfield: section 2, then tickets (section 5) |
| A test or build fails and an agent keeps retrying | Reflexion: section 3 |
| Any screen, dashboard, page or component to design | Design: section 4 |
| Work to hand to an executor model | Tickets: section 5 |

A brownfield job usually needs all four, in this order.

## 2. Brownfield: map first, then claim

Never let a model read legacy code and "summarize what it does" first. Anything it concludes from reading is a hypothesis; only things pinned by a test or a line reference count as facts.

**Gate -2, cartography (zero LLM).** Run one command: `python <skill_dir>/scripts/cartograph.py <repo> --out CARTOGRAPHY.json`. It ranks files and symbols, finds entrypoints (HTTP routes, CLI mains, workers, event subscribers) with file and line, collects SQL tables and env key names (never values), flags deprecated APIs and lists orphan files. The output is byte-identical on every run, so re-run it after changes and diff. `references/gate-2_cartography.md` has the schema. Add `--engine ast-grep` to also catch multi-line calls (it falls back to regex with a warning when the binary is missing). Code reachable only through dynamic dispatch, reflection or string-built imports shows up as `unknown`, never `dead`. Read the `limits` field: it states what the scan cannot know.

**Gate -1, pinning.** Read `references/gate-1_pinning.md`. Every requirement is an EARS sentence with an evidence pointer and one of three buckets:

`REQ-BF-014 [INVARIANT] [ref: src/services/checkout.ts:L88-L112]: WHEN <trigger> THE SYSTEM SHALL <response>`

- `[INVARIANT]` is business logic that must survive the rewrite.
- `[LEGACY_ANOMALY]` is anything that looks like a hack: hardcoded ids, magic numbers, odd workarounds (`if (user.id == 42) return true`). Do not change, drop or "clean up" an anomaly. List each one with the question the user must answer (keep, change, or ask the domain owner). Silent cleanup is how migrations break production.
- `[DEAD_WEIGHT]` is code the cartography proves unreachable. Removal gets its own ticket.

Write the literal tag on every line that carries a classification, including rows of an anomaly table (`A01 [LEGACY_ANOMALY] [ref: ...]`), so a script can count them. A requirement without a file and line range is not allowed; if you cannot find the lines, it stays a question.

**Golden master.** Record what the original code actually does on realistic inputs into `tests/golden/`. Those fixtures are the regression oracle. Freeze them: `ticketc.py hash tests/golden/*.json` and paste the hashes into each ticket's `frozen:` map so a later edit is detected.

**Hermetic twin.** Work in a throwaway worktree, never the live tree: `sandbox.py create --repo <repo> --path <twin>` writes a deterministic `sandbox.lock`. Pipe agent shell commands through `sandbox.py guard "<cmd>"`; it blocks ssh, scp, kubectl, DROP DATABASE, `rm -rf /` and production hosts (exit 2).

## 3. Reflexion: no edit without a theory

When a test fails, an agent's instinct is to try another edit. Three blind edits in a row almost never converge. Instead:

1. `gate3/failure_signature.py "<test id>" "<error type>" "<file:line>"` gives a 12-char signature that stays the same while it is the same failure.
2. Append to `reflection_log.md`:
   ```
   ## <signature> / attempt <n>
   Hypothesis: <falsifiable cause, different from earlier attempts>
   ```
3. `gate3/reflect_check.py <signature> <n>` must exit 0 before you edit code. It exits 2 when the hypothesis is missing, too short, a placeholder, or a repeat of an earlier attempt.
4. After attempt 3 fails, `reflect_check.py <signature> 4` writes `ESCALATION.md` and exits 3. Stop there and hand it to a human or a stronger model; a fourth variation is not progress.

Never edit a protected test to make it pass. Classify the failure first (test wrong, code wrong, spec wrong, environment); only "spec wrong" changes requirements. See `references/gate3_verification.md`.

## 4. Design: classify, use tokens, then lint

Read `references/DESIGN_CONSTITUTION.md` before writing any frontend code; it is under 1,000 tokens. The short version: classify the screen as MODE A (workspace: dashboards, tables, admin, editors) or MODE B (editorial: landing, portfolio, deck) and apply that mode's rules; keep to the anti-slop guardrails; resolve every color and size to a variable (see `references/tokens.css`); use tabular numerals for data, concentric radii (outer = inner + padding), and animate only transform and opacity.

For inspiration, query the bundled catalog of 14 curated systems instead of opening raw design files: `odcat.py search --archetype precision-dark` (also `editorial`, `minimal`, `workspace-light`, `industrial-warm`), then `odcat.py card <id>`. Pick one system, copy its tokens into the page's `:root`, and keep to them.

**Before you call the page done, run the linter and fix every failure:** `python <skill_dir>/scripts/design_lint.py page.html --strict` (add `--css file.css` for external styles). It fails on indigo/purple defaults, non-concentric nested radii, numbers without `tabular-nums` (a monospace font also counts), `transition: all` or animating anything but transform/opacity, backdrop blur outside headers and modals, a card inside a card, filler marketing copy, text contrast under 4.5:1, and raw hex colors outside tokens. Quote the exit code in your summary. If the brief explicitly demands purple, say so and use `--allow-hue`. The linter reads static HTML and CSS; for tables built by JavaScript it checks that the CSS rule exists, not each rendered cell.

## 5. Tickets for the executor

A ticket is a small YAML file: `id, goal, frozen, allowed_paths, forbidden_paths, protected_tests, acceptance, stop_if`. Acceptance items are shell commands whose exit code decides done. `stop_if` says when the executor must halt instead of improvising. Keep `allowed_paths` as narrow as the goal allows and keep protected tests outside it.

- `ticketc.py validate T-001.yml` checks the schema, rejects empty acceptance, over-broad globs and tampered frozen files.
- `ticketc.py diff T-001.yml --repo <repo>` after the executor finishes: fails if any changed or new file is outside `allowed_paths` or inside `forbidden_paths`.

One ticket, one goal. Prefer five small tickets over one large one.

## Finishing

Deliver the files, then say in a few sentences what exists, what is still a hypothesis, and what the user must decide (especially each `[LEGACY_ANOMALY]`). Do not claim "ready" unless every validate and test command you ran exited 0; quote the commands.
