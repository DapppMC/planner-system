# UI craft / perceptual repos: APCA, Leonardo, Capsize, Motion, Zag, taste-engine, and measurement validity (as of 2026-10-25)

Method note: primary sources were fetched directly (raw GitHub files on default branches, npm registry JSON, MDN BCD, Chrome DevTools Protocol JSON, W3C/Playwright docs). GitHub API was not available in this session, so repo stars/commit dates are mostly unknown. Numbers marked "verified by running" were computed in this session (Node 22 running the real apca-w3 source; Python for spring math). The brief said "the spec's code (below)" but no code block was included in the task text; the spec's code is therefore judged only from the fragments quoted in the brief.

---

## 1. What is "taste-engine"? (does it match "12-step cloud pipeline with Vision-LLMs")

### Takeaway
Best match: github.com/ericseandevlin/taste-engine (MIT, tiny: ~5 stars). It is a twelve-stage pipeline that uses vision-LLM passes, but it runs LOCALLY on Node and calls a hosted model API; it is not a cloud service. Identification is plausible but not certain, because the brief names no owner.

### Cited Findings
- README: "Teach an AI your creative taste from a board of references, then have it generate work in that taste and critique itself against it." Extracts "a thesis with forbidden moves" rather than surface tokens — [README raw](https://raw.githubusercontent.com/ericseandevlin/taste-engine/main/README.md)
- README: "A twelve-stage pipeline." The stage table has exactly 12 rows: import, link, measure, tag, cluster, directions, thesis, atlas, fingerprint, generate, critique, score — [README raw](https://raw.githubusercontent.com/ericseandevlin/taste-engine/main/README.md)
- Local vs cloud: "It runs locally through the Google Gemini API (with OpenRouter support for any provider)"; `import`, `measure`, `atlas`, `fingerprint` are local-only; `measure` uses Sharp for palette/contrast/density — [README raw](https://raw.githubusercontent.com/ericseandevlin/taste-engine/main/README.md)
- Vision usage: `tag` is a "Vision pass" assigning rule-tags from a controlled vocabulary (model listed: gemini-3.1-flash-image); `critique` is a "Vision pass over desktop + mobile screenshots" producing 3 to 8 testable rules; `generate` builds one self-contained HTML page — [README raw](https://raw.githubusercontent.com/ericseandevlin/taste-engine/main/README.md)
- The README is internally inconsistent: the intro says Gemini/OpenRouter, but the "Cost" section still says "Haiku for high-volume per-image tagging, Sonnet for analysis and writing, Opus for the hardest synthesis". A WebFetch summary of the GitHub page also reported "runs through the Claude API". Treat the provider as unstable/stale docs — [README raw](https://raw.githubusercontent.com/ericseandevlin/taste-engine/main/README.md); [GitHub page](https://github.com/ericseandevlin/taste-engine)
- Credit: "adapted from the Taste DNA Method, originally shared by Randy Roberts ... in this reel" (Instagram); "an independent open-source implementation" — [README raw](https://raw.githubusercontent.com/ericseandevlin/taste-engine/main/README.md)
- Maturity: GitHub page showed 5 stars, 0 forks, 3 commits on main (last commit date not retrievable; commits page blocked by robots) — [GitHub page](https://github.com/ericseandevlin/taste-engine)
- `score` re-measures a generated page on the same extractors and reports per-axis deltas plus an alignment number, "a diagnostic, never a gate". Measurement is palette/contrast/density via Sharp, NOT APCA — [README raw](https://raw.githubusercontent.com/ericseandevlin/taste-engine/main/README.md)
- Other "taste" repos exist (e.g. Leonxlnx/taste-skill, jaytel0/taste) but none surfaced with a 12-stage vision pipeline — [search results](https://github.com/Leonxlnx/taste-skill/issues/44)

### Inferences
- "12-step" matches the stage count; "cloud" does not (local CLI plus remote API calls); "Vision-LLMs" is true for tag/critique (and nominally score). If the spec treats it as an architecture to copy, it is a loose, single-author, 3-commit project, not an established dependency.
- It is irrelevant to the measurable-linter goals (APCA/Capsize/spring/velocity): its measurement stage is image-statistics based. Do not treat it as a source of algorithms.

### Gaps
- Could not confirm the spec's author meant this repo (no owner in the brief). Could not retrieve last-commit date or true star count at 2026-10-25.

---

## 2. APCA: exact constants, algorithm, correctness of the spec's code, thresholds, license

### Takeaway
The constants in the brief are exactly the 0.0.98G-4g set shipped in apca-w3 0.1.9 (still the latest npm release; unchanged since July 2022). The spec's formulas (simple 2.4 exponent, soft clamp, reverse-polarity branch with -0.1 / +0.027) are correct as quoted. The main risks are alpha handling, wide-gamut/modern-color parsing, silent 0 on out-of-range input, and the non-OSI "W3 license for compliant code only".

### Cited Findings
**Constants (verbatim from source, header "Beta 0.1.9 W3, revision date July 3, 2022", "SAPC-8, 0.0.98G-series constant 4g")** — [apca-w3.js](https://raw.githubusercontent.com/Myndex/apca-w3/master/src/apca-w3.js)
- mainTRC = 2.4 (simple power, NOT piecewise sRGB); sRco/sGco/sBco = 0.2126729 / 0.7151522 / 0.0721750
- normBG 0.56, normTXT 0.57, revTXT 0.62, revBG 0.65
- blkThrs 0.022, blkClmp 1.414, scaleBoW = scaleWoB = 1.14
- loBoWoffset = loWoBoffset = 0.027, deltaYmin 0.0005, loClip 0.1
- (reverse-APCA helper "magic numbers": mFactor 1.94685544331710, mOffsetIn 0.03873938165714010, mExpAdj 0.2833433964208690, mOffsetOut 0.3128657958707580 — only for invertAPCA/reverseAPCA)
- Display-P3 coefficients exist separately: 0.2289829594805780 / 0.6917492625852380 / 0.0792677779341829 with exponent 2.4; Adobe RGB uses exponent 2.35 — [apca-w3.js](https://raw.githubusercontent.com/Myndex/apca-w3/master/src/apca-w3.js)

**Algorithm as coded** — [apca-w3.js](https://raw.githubusercontent.com/Myndex/apca-w3/master/src/apca-w3.js)
- sRGBtoY: Y = sRco*(R/255)^2.4 + sGco*(G/255)^2.4 + sBco*(B/255)^2.4
- Input guard: if txtY or bgY is NaN, < 0 or > 1.1 -> returns 0.0 (silently)
- Soft clamp per color: Y = (Y > 0.022) ? Y : Y + (0.022 - Y)^1.414
- After clamp: if |bgY - txtY| < 0.0005 -> return 0
- Polarity: if bgY > txtY (dark text on light, "BoW"): SAPC = (bgY^0.56 - txtY^0.57) * 1.14; Lc_raw = (SAPC < 0.1) ? 0 : SAPC - 0.027
- else (light text on dark, "WoB"): SAPC = (bgY^0.65 - txtY^0.62) * 1.14; Lc_raw = (SAPC > -0.1) ? 0 : SAPC + 0.027
- Output Lc = Lc_raw * 100 (signed: negative = light text on dark; positive = dark on light). Argument order matters ("Do not swap, polarity is important")
- Alpha: APCAcontrast() takes luminances only. Only the convenience wrapper calcAPCA() handles alpha, by alpha-blending the text color over the bg color (via colorParsley/alphaBlend) when text alpha != 1. A translucent BACKGROUND is not handled; the caller must composite — [apca-w3.js](https://raw.githubusercontent.com/Myndex/apca-w3/master/src/apca-w3.js)
- npm: apca-w3 latest = 0.1.9, published 2022-07-04, `modified` also 2022-07-04; dependency colorparsley ^0.1.8; license field "Limited W3 License"; unpackedSize 179,212 bytes — [npm registry](https://registry.npmjs.org/apca-w3); [npm latest](https://registry.npmjs.org/apca-w3/latest)
- The repo license text says the "current base reference algorithm is 0.0.98G-4g WITH an output clamp at approximately ±Lc10 (for reference, this is the underlying algorithm for apca-w3 versions 0.1.9)" — [LICENSE.md](https://raw.githubusercontent.com/Myndex/apca-w3/master/LICENSE.md)

**Verified by running the real apca-w3 source (Node 22):**
- black text on white = +106.04; white on black = -107.88; #888 on #fff = +63.06; #fff on #888 = -68.54; #767676 on #fff = +71.57
- Low-end: gray 235 on white = 9.35, gray 240 on white = 0.000 (loClip cliff; smallest non-zero |Lc| is 7.3 = (0.1-0.027)*100)
- Piecewise-sRGB TRC instead of 2.4: errors up to ~3.5 Lc (#777 on white: 71.11 vs 67.80; #5a5a5a on black: -18.09 vs -21.61). Skipping the black soft clamp: black on white 111.30 vs 106.04 (+5.3)
- rgba(0,0,0,0.5) over white: 66.90 when alpha is blended first, but 106.04 if alpha is ignored — alpha ignoring is a 39-Lc error

**Recommended thresholds ("Bronze Simple Mode" / APCA in a Nutshell)** — [Nutshell](https://raw.githubusercontent.com/Myndex/SAPC-APCA/master/documentation/APCA_in_a_Nutshell.md)
- Lc 90: preferred for body text; font no smaller than 18px/300 or 14px/400 (non-body no smaller than 12px/400); thin fonts min 24px at weight 200; suggested MAXIMUM for very large/bold fonts (>36px bold) and large color areas
- Lc 75: minimum for body-text columns: no smaller than 24px/300, 18px/400, 16px/500, 14px/700; non-body no smaller than 15px/400
- Lc 60: minimum for content text that is not body: no smaller than 48px/200, 36px/300, 24px/400, 21px/500, 18px/600, 16px/700 (reference font Helvetica); "to use as body text add Lc 15"
- Lc 45: minimum for headlines (36px/400 or 24px bold) and fine-detail pictograms. Lc 30: absolute minimum for spot text (placeholder, disabled, copyright) and solid semantic icons. Lc 15: minimum for non-semantic discernible non-text (dividers, thick focus outlines); "anything below this is invisible"
- "For the equivalent to AAA, simply increase the contrast values by Lc 15."
- The shipped fontLookupAPCA matrix (public beta 0.1.7 G, reference font Barlow) is a finer Lc x weight -> min px table; e.g. weight 400: Lc 60 -> 24px, Lc 75 -> 18px, Lc 90 -> 16px, Lc 45 -> 42px (the Nutshell says 36px/400 at Lc 45 — a small discrepancy between prose and table). Rows: Lc 15/20 -> 777 (non-text only), 0/10 -> 999 (prohibited); "Lc values under 70 should have Lc 15 ADDED if used for body text" — [apca-w3.js](https://raw.githubusercontent.com/Myndex/apca-w3/master/src/apca-w3.js)

**Standards status**
- WCAG 3 draft has NOT chosen a contrast algorithm: the 8 April 2026 Editor's Draft says "The contrast algorithm used in WCAG 3 is yet to be determined"; APCA was marked exploratory and removed from the July 2023 working draft. WCAG 2.x ratio remains the normative/legal measure — [Adrian Roselli, April 2026](https://adrianroselli.com/2026/04/wcag3-contrast-as-of-april-2026.html)

**License**
- LICENSE.md: "W3 License for Compliant Code Only": licensed to W3/AGWG "for use with WCAG accessibility guidelines for web-delivered and web-based content only, and not for any other use". Prohibited uses include medical, clinical, human safety, aerospace, transportation, military and non-web or non-self-illuminated-display uses. Terms "APCA/SAPC" may only be used for correctly implemented, up-to-date code (polarity indication and approved constants are explicitly named); non-compliant implementations "shall be deemed in breech of license". "Right to audit code" for commercial or paywalled integrations. "No alteration; keep current": use without modification to the essential code or approved constants "except as required to port to a given language". "Patent(s) pending". Files outside the W3 agreement fall under "AGPU v3". Dependencies by the author (e.g. Color Parsley) are included by reference — [LICENSE.md](https://raw.githubusercontent.com/Myndex/apca-w3/master/LICENSE.md)

### Inferences
- The brief's constants and both branch formulas are correct. `SAPC > -0.1 ? 0 : SAPC + 0.027` and `(Ybg^0.65 - Ytxt^0.62)*1.14` match source exactly. Per-channel `Math.pow(c/255, 2.4)` (no linear toe) is correct and intentional for APCA; swapping in the WCAG piecewise function would be a deviation.
- Likely spec-code defects to check for: (a) missing polarity sign convention (Lc must be negative for light-on-dark); (b) polarity decided by comparing luminance AFTER the soft clamp (the source does it that way); (c) missing `deltaYmin` early return; (d) no input range check; (e) alpha ignored — the pixel the user sees is blend(text over bg); (f) computed colors from Chromium are not always `rgb()`: modern syntax (oklch, color(display-p3 ...), color-mix) is serialized in its own space and needs conversion to sRGB (clipping/gamut-mapping choice) or use of displayP3toY. A linter should convert via a parser (Culori is a reasonable parser) and then feed 8-bit sRGB into the unmodified apca-w3 functions.
- Backgrounds with gradients, images, backdrop-filter or opacity stacks cannot be resolved from computed style alone; for a robust lint, sample rendered pixels behind the text (screenshot with the text hidden, or `color: transparent`) and take worst-case/percentile bg luminance. Flag as design choice.
- License compatibility: running unmodified `apca-w3` inside an internal Node/Playwright linter that audits web pages is within the stated scope (web content on self-illuminated displays). Risky patterns: re-implementing with tweaked constants while calling it "APCA"; redistributing in a paid product (audit clause); using it for non-web content. It is not an OSI license, so treat as "restricted, not MIT-compatible". This is an engineering reading, not legal advice.
- Because WCAG 3 has no chosen algorithm, any APCA gate should be labeled advisory and kept alongside the WCAG 2.x ratio check.

### Gaps
- Exact apca-w3 test-vector files were not fetched; numeric checks above come from running the source, not from the project's own tests.
- Whether the ±Lc10 "output clamp" wording in LICENSE.md corresponds to loClip=0.1 (raw) or to a separate output clamp: code shows only the raw-SAPC 0.1 clip (min non-zero |Lc| 7.3). Unresolved wording discrepancy.
- Not checked: whether a newer APCA constant series (post-4g) exists in the SAPC-APCA repo/ARC work.

---

## 3. adobe/leonardo: what it actually does vs "heavy bundle; replace with apca-w3 + Culori OKLCH"

### Takeaway
Leonardo is a color GENERATOR (finds colors at target contrast ratios), not a contrast linter. It already supports OKLCH/OKLAB and CAM02, supports both WCAG2 ratios and APCA, and itself depends on apca-w3 (so replacing it with apca-w3 does not remove the APCA license). The "heavy bundle" claim is weak for a Node tool: unpacked size is ~102 KB plus dependencies.

### Cited Findings
- Package @adobe/leonardo-contrast-colors 1.1.0 (published 2026-02-18; registry modified 2026-10-01), license Apache-2.0, ESM; dependencies: apca-w3 ^0.1.9, chroma-js ^3.2.0, ciebase ^0.1.1, ciecam02 ^0.4.6, hsluv ^1.0.1; unpackedSize 101,799 bytes — [npm](https://registry.npmjs.org/@adobe/leonardo-contrast-colors); [package.json](https://raw.githubusercontent.com/adobe/leonardo/main/packages/contrast-colors/package.json)
- Monorepo root: Apache-2.0, requires Node >=24, pnpm@10.30.1 — [root package.json](https://raw.githubusercontent.com/adobe/leonardo/main/package.json)
- Color spaces exposed: CAM02 ('jab'), CAM02p ('jch'), HEX, HSL, HSLuv, HSV, LAB, LCH, RGB, OKLAB, OKLCH — [lib/utils.js](https://raw.githubusercontent.com/adobe/leonardo/main/packages/contrast-colors/lib/utils.js)
- getContrast(color, base, baseV, method) supports 'wcag2' and 'wcag3'; wcag2 uses luminance with the older 0.03928 threshold: `v <= 0.03928 ? v/12.92 : ((v+0.055)/1.055) ** 2.4` and ratio (L1+0.05)/(L2+0.05); 'wcag3' calls apca-w3's `APCAcontrast(sRGBtoY(color), sRGBtoY(base))`, multiplied by -1 when `baseV < 0.5` (dark base) so the result is positive in dark themes; any other method throws — [lib/utils.js](https://raw.githubusercontent.com/adobe/leonardo/main/packages/contrast-colors/lib/utils.js)
- Public exports: Color, BackgroundColor, Theme, createScale, luminance, contrast, minPositive, ratioName, convertColorValue — [index.js](https://raw.githubusercontent.com/adobe/leonardo/main/packages/contrast-colors/index.js)
- Culori 4.0.2 (MIT, last published 2025-06-27, modified 2026-04-03), unpackedSize ~1.1 MB (ES modules, no dependencies) — [npm](https://registry.npmjs.org/culori)
- chroma-js 3.2.0 unpackedSize ~397 KB — [npm](https://registry.npmjs.org/chroma-js/latest)

### Inferences
- unpackedSize is not bundle size; Culori (1.1 MB unpacked) is larger on disk than Leonardo. In a Node CLI, bundle weight is irrelevant; only install/cold-start matters. The claim's premise is therefore not a good reason to drop Leonardo; the better reason is scope: a linter measures, it does not generate.
- Sign convention differs: Leonardo flips APCA sign on dark backgrounds, apca-w3 returns negative for light-on-dark. Do not mix the two conventions in one report.
- Recommended split: apca-w3 for measurement; Culori (or a minimal parser) only for parsing modern color syntax; Leonardo only if the tool must suggest fix colors at a target Lc (generation).

### Gaps
- Real minified/bundled size (esbuild/bundlephobia) was not measured. Leonardo's CAM02 gamut-handling behavior was not inspected.

---

## 4. Capsize formulas, the "38 lines" claim, and text-box-trim / text-box-edge status

### Takeaway
The Capsize math is exactly reproducible (below). "38 lines" is literally true for precomputeValues.ts, but it ignores the surrounding ~5 small files and the metrics data. CSS text-box-trim now ships in Chrome 133+, Safari 18.2+ and Firefox 154+ (Aug 18, 2026), so as of Oct 2026 all three engines support it. The brief's `style.textBoxTrim !== undefined` check only proves support, not use.

### Cited Findings
**Capsize formulas** (@capsizecss/core 4.1.3, MIT, published 2025-11-12, dep: csstype) — [npm](https://registry.npmjs.org/@capsizecss/core); [precomputeValues.ts](https://raw.githubusercontent.com/seek-oss/capsize/master/packages/core/src/precomputeValues.ts)
- absoluteDescent = |descent|; capHeightScale = capHeight/unitsPerEm; descentScale = absoluteDescent/unitsPerEm; ascentScale = ascent/unitsPerEm; lineGapScale = lineGap/unitsPerEm
- contentArea = ascent + lineGap + absoluteDescent; lineHeightScale = contentArea/unitsPerEm; lineHeightNormal = lineHeightScale * fontSize
- allowForLineHeight(trim) = lineHeight ? trim - ((lineHeightNormal - lineHeight)/2)/fontSize : trim
- capHeightTrim = -1 * allowForLineHeight(ascentScale - capHeightScale + lineGapScale/2)   (em)
- baselineTrim  = -1 * allowForLineHeight(descentScale + lineGapScale/2)   (em)
- Output: fontSize "Npx"; lineHeight "Mpx" or "normal"; capHeightTrim "Xem"; baselineTrim "Yem" (all passed through round())
- precomputeValues.ts is 38 lines (wc -l on fetched file) — [precomputeValues.ts](https://raw.githubusercontent.com/seek-oss/capsize/master/packages/core/src/precomputeValues.ts)
- CSS generated: root {font-size; line-height}; `::before {content:''; margin-bottom: capHeightTrim; display:table}`; `::after {content:''; margin-top: baselineTrim; display:table}` — [createStyleObject.ts](https://raw.githubusercontent.com/seek-oss/capsize/master/packages/core/src/createStyleObject.ts)
- Options normalisation: pass `fontSize` OR `capHeight` (fontSize = capHeight / capHeightScale); pass `lineGap` (lineHeight = capHeight + lineGap) OR `leading` (lineHeight = leading); mixing throws — [normaliseOptions.ts](https://raw.githubusercontent.com/seek-oss/capsize/master/packages/core/src/normaliseOptions.ts)
- Package keywords include fallback font / ascent override / descent override / line gap override / size adjust / CLS (i.e. the package also covers fallback-font metric overrides) — [package.json](https://raw.githubusercontent.com/seek-oss/capsize/master/packages/core/package.json)

**CSS text-box support (MDN browser-compat-data, main)** — [text-box-trim.json](https://raw.githubusercontent.com/mdn/browser-compat-data/main/css/properties/text-box-trim.json); [text-box-edge.json](https://raw.githubusercontent.com/mdn/browser-compat-data/main/css/properties/text-box-edge.json); [text-box.json](https://raw.githubusercontent.com/mdn/browser-compat-data/main/css/properties/text-box.json)
- text-box-trim, text-box-edge and shorthand text-box: Chrome 133 (Chrome Android and Edge mirror), Safari 18.2 (iOS mirrors), Firefox 154. Status flags: standard_track true, experimental false, deprecated false
- Values supported: trim-start/trim-end/trim-both/none; edge keywords text/cap/ex/alphabetic/auto. NOT supported in Chrome or Safari: `ideographic` and `ideographic-ink` edges
- `inline_elements` sub-feature: Chrome not supported; Firefox 154; Safari 26.5 partial ("Fails in vertical writing modes")
- Firefox release notes: "The text-box-edge and text-box-trim properties and the text-box shorthand are now supported"; Firefox 154 released August 18, 2026 — [MDN Firefox 154](https://developer.mozilla.org/en-US/Firefox/Releases/154); also [OMG Ubuntu](https://www.omgubuntu.co.uk/2026/08/firefox-154-released)

### Inferences
**Capsize expected-value check for a linter (computed style path):**
```
fs = parseFloat(getComputedStyle(el).fontSize)               // px
lh = computed line-height px, or 'normal' -> undefined
beforeMB = parseFloat(getComputedStyle(el,'::before').marginBottom)   // px, negative
afterMT  = parseFloat(getComputedStyle(el,'::after').marginTop)
expected_before_px = fs * capHeightTrim_em   // capHeightTrim_em from formulas above
expected_after_px  = fs * baselineTrim_em
pass if |actual - expected| <= max(0.5px, 0.01em) and both pseudo display === 'table'
```
Metrics (ascent, descent, lineGap, capHeight, unitsPerEm) must come from the same font file the page renders, via @capsizecss/metrics or fontkit/@capsizecss/unpack; a custom webfont subset or a fallback font changes them.
- Ground truth for "optical trim" should be pixel-based: screenshot the element, find the top ink row of a flat-topped capital (e.g. "H"/"E") and the baseline row, and compare to the element's top/bottom edge within about 1 px. Computed margins can be right while a different font actually renders (fallback) or text wraps to multiple lines (trim applies only at first/last line).
- `text-box-trim` path: the property is `textBoxTrim` (and `textBoxEdge`, `textBox`) on both CSSStyleDeclaration and computed style. `'textBoxTrim' in el.style` or `getComputedStyle(el).textBoxTrim !== undefined` is feature detection only: supporting browsers return the initial value `none`, so "defined" is true on every element in Chrome/Safari/Firefox regardless of use. Use `CSS.supports('text-box-trim','trim-both')` for support and assert `getComputedStyle(el).textBoxTrim` is one of trim-both/trim-start/trim-end AND that `textBoxEdge` is a non-default value such as `cap alphabetic`. I believe the initial text-box-edge `auto` leaves block-container line-box half-leading in place, making trim a visual no-op, but I did not verify this in the spec: confirm empirically in Playwright by measuring content box vs ink box.
- Playwright's bundled WebKit/Firefox builds can lag the release browsers, so a cross-engine lint should gate on `CSS.supports` at runtime rather than on version tables.
- The 38-line claim is fair for the algorithm but each lint still needs a font-metrics source (the data is the heavy part, not the math).

### Gaps
- Which font tables (hhea vs OS/2 typo vs win) Capsize metrics use, and how each browser/OS picks ascent/descent, was not verified; cross-platform mismatches are a known risk class but unconfirmed here.
- Chrome 133 / Safari 18.2 stable-release dates are not in the fetched sources (only version numbers).
- Computed value serialization of `text-box-edge` (e.g. whether `auto` stays `auto`) not checked.

---

## 5. Motion (motiondivision/motion, formerly Framer Motion): springs, linear() easing, interruption velocity

### Takeaway
Motion's spring is an analytic closed-form damped oscillator (three branches by damping ratio), not a numerical integrator. Springs sent to the browser are sampled into a CSS `linear()` easing with 10 ms points. On interruption, physics-defined springs inherit velocity; time-defined springs (duration/bounce/visualDuration) deliberately do NOT (velocity forced to 0). This changes what a velocity-continuity lint should expect.

### Cited Findings
**Versions**: npm `motion` 14.6.0 and `framer-motion` 14.6.0 (both published 2026-10-22; MIT; `motion` depends on framer-motion 14.6.0; framer-motion depends on motion-dom and motion-utils 14.6.0) — [npm motion](https://registry.npmjs.org/motion); [npm framer-motion](https://registry.npmjs.org/framer-motion). Spring source in main: packages/motion-dom/src/animation/generators/spring.ts — [spring.ts](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion-dom/src/animation/generators/spring.ts). (Source is `main`, which may be slightly ahead of 14.6.0.)

**Defaults** — [spring.ts](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion-dom/src/animation/generators/spring.ts)
- stiffness 100, damping 10, mass 1.0 (=> zeta = 10/(2*sqrt(100*1)) = 0.5); duration 800 ms; bounce 0.3; visualDuration 0.3 s
- restSpeed {granular 0.01, default 2}; restDelta {granular 0.005, default 0.5}; "granular" when |target - origin| < 5
- minDuration 0.01 s, maxDuration 10 s, minDamping 0.05

**Damping ratio and mapping**
- dampingRatio = damping / (2 * sqrt(stiffness * mass)); undampedAngularFreq = sqrt(stiffness/mass) converted to rad/ms (÷1000); decay = dampingRatio * undampedAngularFreq
- bounce -> zeta (SwiftUI-style): bounce >= 0: max(1 - bounce, 0.05); bounce < 0: 1 / max(1 + bounce, 0.05)
- Duration/bounce springs (findSpring): zeta from bounce; Newton root search, 12 iterations, safeMin 0.001, initial guess 5/duration; stiffness = undampedFreq^2; damping = zeta * 2 * sqrt(stiffness); mass assumed 1
- visualDuration springs: omega = (2*PI / (visualDuration*1.2)) * overdampedFreqScale; stiffness = omega^2; damping = 2 * zeta * sqrt(stiffness)

**Integrator / closed forms** (t in ms inside the code; displacement written as target - x) — [spring.ts](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion-dom/src/animation/generators/spring.ts)
- Underdamped (zeta < 1): wd = w0*sqrt(1-zeta^2); A = (v + decay*delta)/wd; x(t) = target - e^(-decay*t) * (A*sin(wd*t) + delta*cos(wd*t)); analytic velocity e^(-decay*t) * (sinC*sin + cosC*cos), with sinC = decay*A + delta*wd, cosC = decay*delta - A*wd
- Critically damped (zeta === 1, exact float equality): x(t) = target - e^(-w0*t) * (delta + (v + w0*delta)*t)
- Overdamped (zeta > 1): d = w0*sqrt(zeta^2-1); slow = decay - d; fast = decay + d; P = (v + decay*delta)/d; S = (delta+P)/2; F = (delta-P)/2; x(t) = target - S*e^(-slow*t) - F*e^(-fast*t). Non-physical shortcut for PHYSICS-defined springs: when d >= 1 rad/ms, after t > 300/d ms both terms decay at the damping rate (so heavily overdamped drag springs settle almost instantly); time-defined springs are exempt
- delta = target - origin; v = inherited velocity sign-flipped and converted to per-ms (`-millisecondsToSeconds(velocity)`) because the formula is in target - x space
- Done condition: physics-defined: |velocity| <= restSpeed AND |target - x| <= restDelta, then value snaps to target; time-defined: t >= duration
- toString() emits `${duration}ms linear(...)` with 30 ms resolution; calcGeneratorDuration steps 50 ms until done, max 20,000 ms (-> Infinity)  — [calc-duration.ts](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion-dom/src/animation/generators/utils/calc-duration.ts)

**Spring -> CSS linear() for WAAPI**
- createGeneratorEasing: builds the generator on keyframes [0, 100] with velocity scaled by (v*100)/delta ("Otherwise an interrupted spring hands off the wrong speed, or direction"), computes duration, ease(progress) = generator.next(duration*progress).value/100 — [create-generator-easing.ts](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion-dom/src/animation/generators/utils/create-generator-easing.ts)
- generateLinearEasing(easing, durationMs, resolution=10): numPoints = max(round(duration/10), 2); each point rounded to 4 decimals; returns `linear(p0, p1, ...)` — [linear.ts](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion-dom/src/animation/waapi/utils/linear.ts). mapEasingToNativeEasing returns "ease-out" if `linear()` is unsupported — [map-easing.ts](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion-dom/src/animation/waapi/easing/map-easing.ts)
- WAAPI is used only for accelerated values: opacity, clipPath, filter, transform, backgroundColor (colors only if browser-only formats such as oklch) — [accelerated-values.ts](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion-dom/src/animation/waapi/utils/accelerated-values.ts). WAAPI is skipped when onUpdate is set, repeatDelay, repeatType "mirror", damping 0, type "inertia", or `MotionGlobalConfig.useManualTiming` ("WAAPI runs on the browser's clock, so on a manual clock every animation runs in JS") — [supports/waapi.ts](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion-dom/src/animation/waapi/supports/waapi.ts)
- startWaapiAnimation: `element.animate({[prop]: keyframes}, {delay, duration, easing, fill:"both", iterations, direction})` — [start-waapi-animation.ts](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion-dom/src/animation/waapi/start-waapi-animation.ts)
- Independent transforms (x, scale...) on HTML elements run via JS and are composed into one grouped transform animation when not on WAAPI — [AsyncMotionValueAnimation.ts](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion-dom/src/animation/AsyncMotionValueAnimation.ts)

**Velocity handoff on interruption**
- Time-defined springs ignore inherited velocity: `inheritVelocity = isTimeDefined ? 0 : -ms->s(velocity)`; comment: "Velocity from interrupted animations causes massive oscillation on small-range animations." isTimeDefined = no valid stiffness/damping/mass AND (duration or bounce defined) — [spring.ts](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion-dom/src/animation/generators/spring.ts)
- JS path: generator.retarget(keyframes, newVelocity) re-aims from current position/velocity, reusing exp/sin/cos terms memoized by t; JSAnimation.getGeneratorVelocity() uses analytical generator.velocity(t) (units/s), falling back to a 5 ms backward difference for generators without it — [velocity.ts](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion-dom/src/animation/generators/utils/velocity.ts); [JSAnimation.ts](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion-dom/src/animation/JSAnimation.ts)
- WAAPI path (NativeAnimationExtended): WAAPI has no interruption capability, so on stop it samples a renderless JS animation at elapsed time and at elapsed-10ms (sampleDelta = 10 ms), writes the current value inline, and calls motionValue.setWithVelocity(prev, current, delta). Elapsed uses wall-clock `(time.now() - startTime) * speed` rather than WAAPI currentTime because "under CPU load currentTime may not reflect the actual elapsed time, causing incorrect sampling and visual jumps" — [NativeAnimationExtended.ts](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion-dom/src/animation/NativeAnimationExtended.ts)
- MotionValue.getVelocity() = velocityPerSecond(current - prevFrameValue, delta) where velocityPerSecond(v, frameDuration) = v * (1000/frameDuration); returns 0 if the last update was more than MAX_VELOCITY_DELTA = 30 ms ago; delta clamped to 30 ms — [value/index.ts](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion-dom/src/value/index.ts); [velocity-per-second.ts](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion-utils/src/velocity-per-second.ts)
- NativeAnimation.stop(): commits styles (animation.commitStyles) then cancels, unless the extended path updates the motion value — [NativeAnimation.ts](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion-dom/src/animation/NativeAnimation.ts)

### Inferences
**Textbook equations (y = x - target, m*y'' + c*y' + k*y = 0; w0 = sqrt(k/m); zeta = c/(2*sqrt(k*m))). Verified by running: Motion's target-space form equals the textbook form to <= 2e-14 for zeta = 0.43, 0.5, 0.997, 1, 2.**
```
Underdamped (zeta<1), wd = w0*sqrt(1-zeta^2), sigma = zeta*w0:
  y(t) = e^(-sigma t) * [ y0 cos(wd t) + (v0 + sigma y0)/wd * sin(wd t) ]
Critically damped (zeta=1):
  y(t) = e^(-w0 t) * [ y0 + (v0 + w0 y0) t ]
Overdamped (zeta>1), r1 = -w0(zeta - sqrt(zeta^2-1)), r2 = -w0(zeta + sqrt(zeta^2-1)):
  y(t) = C1 e^(r1 t) + C2 e^(r2 t);  C1 = (v0 - r2 y0)/(r1 - r2);  C2 = (r1 y0 - v0)/(r1 - r2)
```
**Estimating zeta from a sampled trajectory (verified numerically):**
```
Successive same-sign extrema (period Td = 2*pi/wd):  delta = ln(y_n / y_{n+1}) = 2*pi*zeta / sqrt(1-zeta^2)
   => zeta = delta / sqrt(4*pi^2 + delta^2)
Successive opposite-sign extrema (half period):       r = ln(|y_k| / |y_{k+1}|) = pi*zeta / sqrt(1-zeta^2)
   => zeta = r / sqrt(pi^2 + r^2)
Step response overshoot Mp (first peak fraction beyond target, v0 = 0): Mp = exp(-pi*zeta/sqrt(1-zeta^2))
   => zeta = -ln(Mp) / sqrt(pi^2 + ln(Mp)^2)
Peak spacing: t_{k+1} - t_k = pi/wd  =>  w0 = wd/sqrt(1-zeta^2)
```
- Extrema of y(t) are exactly pi/wd apart and their ratio is exactly e^(-sigma*pi/wd) for ANY initial position/velocity, as long as the spring is not re-targeted mid-way, so the decrement works for post-interruption segments too.
- Results on noiseless 60 fps samples with parabolic peak interpolation: true zeta 0.5 -> 0.500; 0.25 -> 0.250; 0.433 -> 0.433; 0.1 -> 0.100 (half-period and overshoot methods). No extrema exist for zeta >= ~1 (0.997 produced 0 peaks): use a nonlinear fit of (w0, zeta, optional v0) to the trajectory instead. With real, noisy data use >= 2 extrema or a fit and report an interval.
- Identifiability: a trajectory yields only zeta and w0 (equivalently c/m and k/m); stiffness, damping and mass cannot be recovered separately. A linter should assert on zeta and w0 (or settling time), not on raw stiffness/damping.
- Motion shortcuts that alter measured zeta: bounce is clamped (zeta >= 0.05); the heavy-overdamped t > 300/d cutoff; and the 'rest' snap to target (|v| <= restSpeed and |dx| <= restDelta) so tail amplitude measurements below the thresholds are invalid — only use peaks well above rest thresholds. For WAAPI springs the curve is the 10 ms piecewise-linear `linear()` approximation (4-decimal rounding) of the analytic curve, which slightly flattens extrema; the measured zeta from 60 fps samples remains accurate to a few percent for zeta in 0.1-0.9 (not tested with real noise).
- Static alternative (no sampling): for Motion WAAPI springs read `animation.effect.getTiming().easing` (a `linear(...)` string) and `getKeyframes()` and solve directly on the parsed points; ratio of successive extrema in the easing function gives zeta exactly (up to 10 ms/4-decimal quantization).

**Velocity continuity lint on interruption — what to assert**
- Define join time t_i (pointer interrupt). Required: position continuity |x_new(0+) - x_old(t_i-)| <= eps (always expected). Velocity continuity v_new(0+) = v_old(t_i-) is expected only for physics-defined (stiffness/damping/mass) springs; for duration/bounce/visualDuration springs Motion sets v_new(0+) = 0 by design, so the lint should report "velocity reset by design (time-defined spring)" rather than a continuity failure, or flag it as a UX finding if continuity is a house rule.
- Verified by running: physics spring (k=170, c=26) interrupted at 0.25 s: analytic v_old = 163.08, v_new(0+) = 162.89 (continuous). But the retarget changes the force instantly (acceleration jumps by k*(target change)), so naive one-sided differences at 60 fps are heavily biased: 1st-order backward est 175.9, 2nd-order backward 162.1 (good), 1st-order forward est 28.5, 2nd-order forward 129.9 (still bad). Never compare raw one-sided 16.7 ms differences across the join; fit the post-join segment with the closed form (unknown v0, w0, zeta) and compare fitted v0 to the pre-join segment's fitted/analytic velocity at t_i, or sample at <= 1-2 ms via virtual time/seek.
- On the WAAPI path Motion's own handoff velocity is a 10 ms backward difference of the JS twin spring and is read through MotionValue.getVelocity() (0 if stale by > 30 ms). Expect small estimation error and wall-clock-vs-timeline skew; use a tolerance (e.g. 5-10% of |v_old|, absolute floor), not equality.

### Gaps
- Whether the brief's "formerly Framer Motion" rename and repo move are documented was not separately sourced (inferred from the dual npm packages at identical versions and the repo name).
- Motion docs pages (motion.dev) were not fetched; all claims are from main-branch source, which may differ slightly from the 14.6.0 tag.
- Not read: `canGroupTransform` internals, keyframes resolver, and the gesture/drag "inertia" generator (drag release velocity uses a different generator, type "inertia", which is excluded from WAAPI).
- visualDuration-only configs (no bounce/duration) may not enter the time-defined branch via an options object (the branch is inside `isTimeDefined`); the exact public-API default for that case was not traced.

---

## 6. chakra-ui/zag: @zag-js/core machine API, rewrite status, component coverage

### Takeaway
Zag is a headless, framework-agnostic state-machine component library (not a measurement tool). @zag-js/core 1.46.0 exposes `createMachine`/`setup`. The core machines were rewritten for 1.0 (2025-02-22), with incremental core features in 2026 but no further rewrite through 1.46; a `2.0.0-next` pre-release tag exists. Dialog and Drawer are covered; the docs cite WAI-ARIA Authoring Practices patterns, not "ARIA 1.2" by name.

### Cited Findings
- @zag-js/core 1.46.0, published 2026-10-11, MIT, described as "A minimal implementation of xstate fsm for UI machines"; deps @zag-js/utils and @zag-js/dom-query; dist-tags: latest 1.46.0, next 2.0.0-next.3, v1-beta 2025-02-22 build — [npm](https://registry.npmjs.org/@zag-js/core); [package.json](https://raw.githubusercontent.com/chakra-ui/zag/main/packages/core/package.json)
- Core exports: merge-props, memo, replace-tracker, create-machine, state, types, scope — [index.ts](https://raw.githubusercontent.com/chakra-ui/zag/main/packages/core/src/index.ts)
- `createMachine<T extends MachineSchema>(config: Machine<T>)` calls `ensureStateIndex(config)` and returns the config (declarative object, not a runtime proxy); `setup<T>()` returns `{ guards: {and, or, not}, createMachine, choose }` — [create-machine.ts](https://raw.githubusercontent.com/chakra-ui/zag/main/packages/core/src/create-machine.ts)
- Machine action/guard params: prop(), context (bindable get/set/initial/hash), refs, computed, track(deps, fn), flush, event (current()/previous()), send, state (matches, hasTag), choose, guard, scope (getRootNode, getById, getActiveElement, getDoc, getWin); `Bindable` has defaultValue/value/isEqual/onChange/sync — [types.ts](https://raw.githubusercontent.com/chakra-ui/zag/main/packages/core/src/types.ts)
- Rewrite: 1.0.0 (2025-02-22): "Core: Rewrite machines for increased performance and initial mount time. The results show roughly 1.5x - 4x performance improvements across components." — [CHANGELOG](https://raw.githubusercontent.com/chakra-ui/zag/main/CHANGELOG.md)
- 2026 core changes: 1.35.1 (2026-02-26) "Support XState-style #id transition targets"; 1.38.1 (2026-03-25) "Validate compound states at machine creation — throw if a state has child states but no initial"; a 1.43.x fix to getExitEnterStates() in browsers without Array.prototype.at() — [CHANGELOG](https://raw.githubusercontent.com/chakra-ui/zag/main/CHANGELOG.md)
- Adapters: React, Vue, Solid, Svelte, plus Preact and vanilla (`VanillaMachine`); README: "Accessible component logic for React, Vue, Solid, and Svelte" — [README](https://raw.githubusercontent.com/chakra-ui/zag/main/README.md); [CHANGELOG](https://raw.githubusercontent.com/chakra-ui/zag/main/CHANGELOG.md)
- Component list in docs (React nav): Accordion, Angle Slider, Avatar, Carousel, Cascade Select (Beta), Checkbox, Clipboard, Collapsible, ColorPicker, Combobox, Date Input (Beta), Date Picker, Dialog, Drawer (Beta), Editable, File Upload, Floating Panel, Hover Card, Image Cropper, Listbox, Marquee, Menu (+Nested, Context), Navigation Menu, Number Input, Pagination, Password Input, Pin Input, Popover, Presence, Progress (Linear/Circular), QR Code, Radio Group, Rating Group, Scroll Area, Segmented Control, Select, Signature Pad, Slider (+Range), Splitter, Steps, Switch, Tabs, Tags Input, Timer, Toast, Toggle, Toggle Group, Tooltip, Tour, Tree View; utilities Async List, Focus Trap, Hotkeys — [Zag docs](https://zagjs.com/components/react/dialog) (from a summarizing fetch of the nav)
- Dialog: `role` "dialog" (default) or "alertdialog"; "Follows the WAI-ARIA Alert and Message Dialogs pattern"; `aria-hidden` on outside content in modal mode; focus trap, Esc closes and restores focus, Enter on trigger opens; React usage `useMachine(dialog.machine, {id})` + `dialog.connect(service, normalizeProps)` — [Zag dialog docs](https://zagjs.com/components/react/dialog)
- Drawer: changelog shows a `description` anatomy part with `aria-describedby` and a `SwipeArea` part (1.38.0, 2026-03-24); changelog also notes an axe `aria-prohibited-attr` violation being fixed on toast group (suggesting axe-based testing) — [CHANGELOG](https://raw.githubusercontent.com/chakra-ui/zag/main/CHANGELOG.md)

### Inferences
- Machine definitions are plain declarative objects keyed by `props/context/initialState/states/on/implementations`-style schema (inferred from the typed params); framework adapters instantiate them. The old (pre-1.0) valtio-proxy style should not be assumed by any generated code.
- A linter doesn't need Zag; if the spec uses it as the "behavior layer" it implies WAI-ARIA APG conformance of the components, but the lint should still assert roles/aria attributes in the DOM (axe) rather than trusting the library.

### Gaps
- "WAI-ARIA 1.2" is not stated in the fetched docs; only APG patterns are referenced.
- What 2.0.0-next.3 changes (breaking?) was not investigated.
- Exact component count/coverage is from a summarizing fetch of the docs nav, not from packages in the repo tree.

---

## 7. Measurement validity: rAF sampling, getComputedStyle, CDP Animation domain, virtual time, page.clock

### Takeaway
Wall-clock rAF sampling through page.evaluate round-trips is the least robust option. The most robust combination is (1) static/seek-based sampling of WAAPI/CSS animations (pause and set currentTime, or CDP Animation.seekAnimations), (2) page.clock-driven stepping for JS/rAF-driven springs, and (3) model fitting rather than finite differences at joins. Pixel-exact compositor capture exists only via HeadlessExperimental.beginFrame in chrome-headless-shell.

### Cited Findings
- W3C Web Animations: the document timeline's time is set once per "update animations and send events" run; "document.timeline.currentTime does not change in between calls to the update animations and send events procedure"; user agents "may delegate the playback of an animation to a separate process or to specialized graphics hardware"; the spec's own test idiom reads `getComputedStyle(elem).opacity` after seeking an animation — [Web Animations 1](https://www.w3.org/TR/web-animations-1/)
- CDP Animation domain (experimental) commands: enable, disable, getCurrentTime(id), getPlaybackRate, setPlaybackRate (document timeline), setPaused(animations[], paused), seekAnimations(animations[], currentTime | currentTimes[]), releaseAnimations, resolveAnimation(animationId) (returns remote object of the Animation), setTiming(animationId, duration, delay). Events: animationCreated, animationStarted, animationUpdated, animationCanceled. Animation type fields: id, name, pausedState, playState, playbackRate, startTime (ms), currentTime, type (CSSTransition | CSSAnimation | WebAnimation), source (AnimationEffect: delay, endDelay, iterationStart, iterations, duration, direction, fill, backendNodeId, keyframesRule, easing), cssId, viewOrScrollTimeline. KeyframeStyle carries only offset and easing (not property values) — [browser_protocol.json](https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/json/browser_protocol.json)
- Emulation.setVirtualTimePolicy is experimental: "Turns on virtual time for all frames (replacing real-time with a synthetic time source)"; policies advance | pause | pauseIfNetworkFetchesPending; params budget, maxVirtualTimeTaskStarvationCount, initialVirtualTime; event virtualTimeBudgetExpired — [browser_protocol.json](https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/json/browser_protocol.json)
- HeadlessExperimental.beginFrame is experimental ("Sends a BeginFrame to the target ... Requires that the target was created with BeginFrameControl"); enable/disable deprecated. puppeteer-capture README: beginFrame is "exclusive to chrome-headless-shell" and "not available in --headless=new"; flags used include --deterministic-mode, --enable-begin-frame-control, --run-all-compositor-stages-before-draw, --disable-threaded-animation, --disable-threaded-scrolling, --disable-new-content-rendering-timeout, --enable-surface-synchronization; and it states that Page.captureScreenshot with JS time virtualization "can achieve determinism for JS-driven content but not for CSS animations or compositor-driven effects" — [puppeteer-capture README](https://cdn.jsdelivr.net/npm/puppeteer-capture@1.58.0/README.md); [browser_protocol.json](https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/json/browser_protocol.json)
- Playwright page.clock overrides Date, Temporal.Now, setTimeout/setInterval, requestAnimationFrame/cancelAnimationFrame, requestIdleCallback, performance, Event.timeStamp. `install` must be called before other clock methods; `pauseAt` stops timers; `runFor(ms)` advances time "firing timers and animation frames along the way"; `fastForward` jumps (due timers fire once); `setFixedTime` only fixes Date. The docs don't mention CSS animations/WAAPI and name no minimum version — [Playwright clock](https://playwright.dev/docs/clock)
- Motion supports a manual-timing mode that forces every animation onto the JS frameloop; WAAPI animations "stay on the browser's clock" — [supports/waapi.ts](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion-dom/src/animation/waapi/supports/waapi.ts)
- Motion's own comment that WAAPI currentTime may not reflect actual elapsed time under CPU load — [NativeAnimationExtended.ts](https://raw.githubusercontent.com/motiondivision/motion/main/packages/motion-dom/src/animation/NativeAnimationExtended.ts)
- Chrome's Page.startScreencast/screencastFrameAck exist as CDP commands for frame capture — [browser_protocol.json](https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/json/browser_protocol.json)

### Inferences
**Pitfalls (reasoned from the sources above; not individually benchmarked here):**
1. getComputedStyle forces a style recalc and reflects the animated value for the document timeline's current frame time (frame-synchronized per the spec). So "stale until recalc" is not the real problem; the real problem is that the value is the main-thread timeline's value, which can differ from the pixels the compositor has actually presented (latency of 1 frame or more). It is a sample of the model, not of pixels. Chromium-specific compositor behavior was NOT verified from a Chromium source.
2. getBoundingClientRect forces layout and perturbs timing (jank) and returns post-transform boxes; for transform-only animations read `new DOMMatrix(getComputedStyle(el).transform)` (m41, m42, a, b) to avoid layout cost, and batch reads inside one rAF callback.
3. Sampling cadence: record `{t: rafTimestamp, v}` in an in-page array inside the rAF callback and return it once at the end. Do not call page.evaluate per frame (CDP round trip jitter of several ms swamps a 16.7 ms frame). Use the rAF timestamp argument (frame time), not performance.now() inside the callback.
4. At 60 fps (Nyquist 30 Hz) a spring with wd of ~2*pi*5-10 Hz is resolvable, but 16.7 ms sampling with one-sided differences is biased at joins (see section 5). Headless frame rate and rAF throttling behavior (headless=new vs headless shell, background/occluded pages) was not sourced; treat 60 Hz as unguaranteed and always timestamp.
5. Playwright mouse.down/up/move are separate CDP Input.dispatchMouseEvent round trips with no frame alignment; their effective times jitter relative to frames. For a deterministic mid-gesture interruption, either dispatch PointerEvents from inside the page at a chosen frame/animation time, or use page.clock paused + runFor so that input is delivered at known virtual times.
6. Animation.getCurrentTime's CDP description ("Current time of the page") is ambiguous; prefer reading `animation.currentTime` from a resolved object via Animation.resolveAnimation + Runtime.callFunctionOn, or the Animation object delivered with animationStarted/animationUpdated.
7. page.clock and Emulation.setVirtualTimePolicy control JS time; per the sources they do not (documentedly) control the document timeline of CSS/WAAPI animations or compositor effects. Motion's WAAPI-accelerated springs therefore keep running on real time under page.clock unless Motion is put in manual-timing mode (needs page-side access to MotionGlobalConfig) or the animations are seeked/paused.

**Recommended approach (ranked):**
- A. Seek-based sampling for WAAPI/CSS animations (Motion's accelerated springs, CSS transitions): in page, `document.getAnimations()` -> `a.pause()`; for each t in a fine grid (e.g. 1 ms to 16.7 ms steps) set `a.currentTime = t` and read getComputedStyle (or DOMMatrix of transform). Equivalent CDP route: Animation.enable -> collect animationStarted -> Animation.setPaused + Animation.seekAnimations (currentTime or per-animation currentTimes) -> read style. Deterministic, wall-clock independent, arbitrary resolution. Even better, avoid sampling: read `a.effect.getKeyframes()` and `a.effect.getTiming().easing` and parse the `linear(...)` string for closed-form zeta/overshoot (section 5).
- B. For JS/rAF-driven motion (Motion JS path, onUpdate springs, drag/inertia): `await page.clock.install()` before navigation/scripts run, `pauseAt`, then loop `runFor(1000/60)` (or finer) and read state after each step; rAF and performance.now are mocked together so Motion's frameloop advances deterministically.
- C. Mid-gesture interruption tests: under (B) dispatch the interrupting pointer events at a chosen virtual time, then step; evaluate velocity continuity via model fit of the pre-/post-join segments (section 5) and report the spring-type rule (physics vs time-defined). Under (A) you can seek the old animation to t_i, observe the new animation created via animationStarted, and read its keyframes/easing to extract v0 analytically.
- D. Wall-clock rAF sampling only as a smoke test, with tolerance bands; never as a pass/fail on small velocity discrepancies.
- E. Ground truth of presented pixels: HeadlessExperimental.beginFrame (chrome-headless-shell only, experimental; run-all-compositor-stages-before-draw and begin-frame flags) or screenshots at paused seek positions. Treat as optional/CI-only because of experimental status and binary constraints.
- Prefer the stable public API (`page.clock`, WAAPI seeking) over CDP-experimental domains (Animation, Emulation.setVirtualTimePolicy, HeadlessExperimental) for long-lived tooling; all three CDP domains are marked experimental in the protocol JSON.

### Gaps
- No source found on headless Chromium rAF cadence/throttling (searches returned nothing relevant); no Chromium-source confirmation of how compositor-thread animations interact with getComputedStyle.
- Minimum Playwright version for page.clock not stated on the docs page (not verified).
- Whether `Animation.seekAnimations` works on animations created by element.animate() with `fill: both` and on Motion-managed animations (Motion may cancel/commit on its own) was not tested.
- Playwright's bundled browser versions as of Oct 2026 (relevant to text-box-trim availability in Firefox/WebKit builds) were not checked.
