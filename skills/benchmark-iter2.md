# Skill benchmark, iteration 2 (planner-3-engine)

3 cases, 1 run each. With-skill runs are new (skill with cartograph.py, design_lint.py, bundled catalog).
Baselines (without skill) are the iteration-1 outputs, re-graded with the tightened assertions; their time and tokens are carried over from iteration 1.

| Case | With skill | Without skill |
|---|---|---|
| brownfield-cert-service-map (13 assertions) | 12/13 | 5/13 |
| design-admin-dashboard-tokens (13 assertions) | 13/13 | 10/13 |
| reflexion-failing-padding-test (5 assertions) | 5/5 | 4/5 |
| Mean pass rate | 97% | 65% |

## What changed in the design assertions
Iteration 1 graded the design page with greps (11/12 without the skill). Iteration 2 renders the page in Chromium and checks computed styles:
- G-01: no element color/background/border with hue 245-290 (saturated); source has no indigo-500 / purple-600 defaults.
- L3.1: the page must contain at least one nested rounded parent/child with padding, and every such pair satisfies R_outer = R_inner + padding (within 1px). A page with no nested rounded pair FAILS, because the rule is then not demonstrated.
- L3.3: every numeric element (2+ digits) uses tabular-nums or a monospace font.
- L4: only transform/opacity have a non-zero transition; no keyframes on layout properties.
The without-skill page now fails 3 of 13 (no nested radius pair, `flex-grow` transition). The grader is independent of tools/design_lint.py for these checks, so a page cannot pass just by satisfying the linter's regexes.

## Honest notes
- The with-skill design page passed the browser grader without the author having a browser (it ran design_lint.py only), which is the evidence that the linter and the grader agree.
- Brownfield with-skill fails 1 assertion: it wrote anomalies as a table (A01..A14) without the literal tag `[LEGACY_ANOMALY]`. SKILL.md now requires the literal tag; not re-run.
- Two grader fixes made after seeing results, both disclosed: the reflexion "log written before edit" check (file mtimes cannot prove order once an outcome line is appended) was removed; the secret-leak check now flags only credential-bearing values (`postgres://user:pass`), not the placeholder `change-me` quoted as a finding.
- One run per case: differences of one assertion are within noise. Assertions were written by the skill's author; the brownfield gap is partly format-driven (CARTOGRAPHY.json, buckets).
- Description optimization (run_loop) is still not done.
