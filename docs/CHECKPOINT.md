# Checkpoint (read this after a /compact or in a new session)

## Live spending guard follow-up
- Corrected duplicate live notice. Live calls in one app process are serialized
  around the budget check and run; failed Meta and Baseten request attempts
  are recorded with an estimated charge. The display calls this estimated usage.
- The limits remain best-effort: estimates can differ from provider billing,
  separate app instances are not coordinated, and local logs can reset on
  restart. Check provider billing; no live model calls were used for this fix.

Updated: Wed Oct 28, 2026 (new direction; Part A market audit done = checkpoint 1). Repo: https://github.com/Kashyeb57/quantproof (public, `main`).
Live app: https://kashyeb57-quantproof-app-snew9v.streamlit.app/

## Working arrangement (from Oct 28, owner's choice)
- **Muse Code on Joyeb's PC is the main builder** (Meta credits). **Claude Code is the reviewer**:
  pricing maths (`models/`, `payoffs/`, `valuation/`, `risk/`, `validation/`), security (sandbox,
  secrets, cost guard, uploads) and anything Joyeb asks.
- Only one of them works at a time: **always `git pull` first and push when done**. Never force-push.
- Commit as Joyeb Kashyeb <168053382+Kashyeb57@users.noreply.github.com>, no Co-Authored-By trailers.


## Done
- Day 1 go/no-go **GO**: Muse Spark 1.3 extracted the JPM 424B2 (worst-of autocallable, RTY + SPX,
  preliminary, CUSIP 48136LQH5) with 0 wrong fields, 17/17 dates and call amounts, 93/97 quotes
  verbatim (4 line joins), diagrams read correctly; 22 s, 10,667 in / 8,673 out tokens, ~$0.05.
- Package skeleton, pyproject (Python >= 3.11, numpy + scipy), `.gitignore`, CI (3.11 + 3.13, green).
- `core/`: TermSheet (every value `Cited(value, Citation)`, `unverified_fields()`), MarketInputs
  (correlation validated, PSD), PricingResult (measure required; MC needs seed + paths + std_error).
- `models/bsm.py`: calls/puts, cash-or-nothing digitals, Greeks, implied vol (Brent), bounds.
  Hull check: call 4.7594, put 0.8086. Tests: 1,058 pass.
- `agent/client.py`: only the auth-header rule so far.
- Fixture: `tests/fixtures/filings/jpm_424b2-38582.htm` (declares **windows-1252**) + payoff images.
- `tests/conftest.py`: `jpm_38582_termsheet` fixture built by hand from the filing.
- `scripts/muse_draft.py`: Muse drafts files (`--task-file`, `--context`, `--apply`, `--dry-run`,
  `--total`); **streams** (non-streamed calls over ~30 s get 502). Cost per call in
  `docs/MUSE_DEV_LOG.md`; prices in `agent/costs.py`. Dev spend so far ~$0.18.
- `ingest/html.py` (Muse draft, reviewed): charset from BOM/meta/xml declaration, NFKC + invisible
  chars, browser-style whitespace, layout-table rows keep lines, data rows `a | b`, `[image: x]`.
- `validation/quotes.py` (Muse draft, reviewed): normalise to [a-z0-9.%$], ellipsis pieces in
  order, word boundaries (a sentence-final period is not a token). All 78 fixture citations verify.
- Tests: 1,129 pass.

## Day 2 so far (Tue Oct 27, evening)
- `agent/client.py` (Muse draft, reviewed): streaming `chat()`, retries (429/5xx, network, and
  the "stop with no content and no usage" glitch), `image_part()`, JSON-lines cost log in
  `.cache/meta_calls.jsonl`, `spend()`. Missing usage is charged at the upper bound.
- `agent/extract.py` (Muse draft, reviewed and rewritten for clarity): `SYSTEM_PROMPT` defines
  every field; `to_termsheet()` reports every problem at once; `extract_termsheet()`.
- Recorded live reply `tests/fixtures/extractions/jpm_424b2-38582.reply.json`: every value equals
  the hand-built term sheet; 77/77 quotes verify; gearing flagged (not in the filing).
- `scripts/muse_draft.py` now uses `client.chat`. Dev spend $0.60 of $80. Tests: 1,167 pass.

## Done since (Oct 27, late)
- Value-support check in `validation/quotes.py`: a quote must show its value (number forms,
  percents, dates, call premiums as % of notional); reordered passages reported "out of order".
  It caught 55 label-only quotes in the hand-built fixture (fixed) and "..."-shortened lists in
  Muse's quotes (prompt now requires full lists, passages in filing order). Recorded JPM reply
  re-recorded: 77/77 quotes found and show their value; gearing flagged.
- `ingest/edgar.py`: `EdgarClient` (`get`, `get_json`, `recent_filings`, `search`,
  `fetch_filing` with images), cache in `.cache/edgar/`, 5 requests/s, 60 s timeout.
- API glitch seen twice: no usage + reply cut off despite "stop"; callers check completeness.
- `muse_draft.py`: per-call tagged markers (a file containing markers no longer splits a reply);
  `--apply` never overwrites a file not sent as `--context`; `--pytest "<args>"` sends failures.
- AGENTS.md: Meta credits do the work; Muse fixes review findings and failing tests; Claude
  reviews from diffs and tests, reads whole files only for pricing maths and security.
- Dev spend $1.14 of $80. Tests: 1,212 pass.

## Done since (Oct 27, night)
- Three new final JPM 424B2s from EDGAR saved as fixtures with recorded replies (text only):
  A `ea0308498` single stock (Expand Energy), monthly contingent coupons, callable from month 6;
  B `ea0309017` worst of 3 indices, 17.5% buffer with downside leverage 1.21212, coupons;
  C `ea0306095` Dual Directional Buffered Return Enhanced Notes (upside leverage 2.0, 40% buffer).
  A: 2 fields flagged (paraphrased dependency quote, no gearing); B and C: every quote verified.
- Schema fixes they forced (Muse drafts, reviewed):
  `ContingentCoupon.observations` (coupon dates separate from call dates; on a coupon note
  call_amount is principal only); `TermSheet.upside` (`Upside(leverage, cap)`);
  `TermSheet.unsupported_features` (cited; C flags "Absolute Stock Return"); required keys.
- Verifier (`validation/quotes.py` `_level_support`, hand-written): a buffer quoted as
  "Buffer Amount: 40.00%" must have level 0.60 (it caught Muse's 0.40); "at or above the Initial
  Value" supports a level of 1.0. Prompt: table values quote the whole row.
- `EdgarClient.search(..., cik=)`: EFTS hits list the parent (19617) first, so filter in the query.
- JPM 38582 reply re-recorded with the new schema (78 checks, only gearing flagged).
- Dev spend $2.21 of $80. Tests: 1,246 pass.

## Day 3 engine (Oct 27, late night)
- `models/montecarlo.py`: `correlation_factor` (Cholesky, eigen fallback for correlation 1),
  `simulate_performance` (exact GBM steps between observation dates, drifts passed in, antithetic
  rows i and i + n/2), `mc_mean_and_se` (antithetic SE from pair means).
- `models/discounting.py`: Act/365F `year_fraction`, `discount_factor` = exp(-(r + s) t).
- `payoffs/worst_of.py`: `check_supported` (refuses `unsupported_features` and DAILY barriers),
  `observation_dates`, `pathwise_cashflows` (coupons with memory on their own dates, autocall,
  barrier final/each-review, buffer with gearing, capped upside), `price_note` (risk-neutral,
  needs `market.as_of == pricing_date`). Call and coupon amounts are cash per note.
- Closed-form check: one-asset barrier note = bond - put(0.7) - 0.3 x digital put; MC within 3 SE.
- Sanity (not a test, made-up inputs r 3.7%, vols 24%/17%, spread 0.6%): JPM 38582 ~$900 (rho 0.5)
  to ~$926 (rho 0.8) vs bank $954. Real inputs and the range come on Day 5.
- Dev spend $2.47 of $80. Tests: 1,286 pass.

## Day 4 proof battery (Oct 27, night)
- Drafts now by **Muse Wonder** (owner's choice) with `--reasoning-effort low|medium`: without it
  Wonder reasoned ~30k tokens and the gateway cut the stream at ~300 s (only $0.70 lost).
- `validation/battery.py`: `run_battery(ts, market, n_paths, seed, filing_text)` -> `CheckResult`s:
  BSM known values, parity, determinism, barrier removed (buffer 100% = bond - ATM put), barrier at
  maturity (bond - put - (1-K) digital), never callable (+ digital coupons), zero coupon, zero vol,
  correlation 1, correlation / vol / spread / coupon monotonicity, convergence (SE ratio 0.5 +- 0.1),
  quotes. N = 1 is covered by the closed-form checks (they run on asset 0 alone).
- Stress run: 3 real term sheets x 6 seeds x 100k paths, no failures.
- `validation/report.py` + `scripts/validation_report.py` -> `docs/VALIDATION.md` and
  `docs/validation/*.svg` (hand-written SVG, no new dependency). 38582 13/14, ea0309017 15/15,
  ea0308498 12/13 (the misses are quote flags: gearing not stated), ea0306095 refused.
- Dev spend $4.12 of $80. Tests: 1,301 pass.

## Day 5 value + risk + CLI (Oct 27, night)
- `valuation/fair_value.py`: `fair_value_range(ts, market, grid, n_paths, seed, n_paths_base)` over
  vol x correlation x spread x dividend x skew (162 points, common random numbers). Correlation is
  blended towards all-ones or identity (stays PSD). Skew = extra vol at the barrier strike,
  `skew_per_10pct * (1 - K) / 0.10`, applied to the whole note (stated simplification). The bank
  value is only compared, never an input (test proves it).
- `risk/real_world.py`: `loss_odds` (REAL-WORLD, drift r + 5% ERP by default, undiscounted cash,
  binomial SEs, issuer default not simulated) and `stress_heatmap` (drift x vol scale).
  Closed-form tests: P(W_T < x) and E[cash] for a one-asset barrier note.
- `scripts/analyse.py`: filing + recorded reply (or `--live`, or `--url`) -> Markdown/JSON report.
  Example: `docs/examples/jpm_38582_analysis.md`. JPM 38582 (illustrative inputs): range
  $778-$975, base $908, bank $954 inside the range. Why below the bank's: we discount at r + 60 bp
  market spread (the bank uses its lower internal funding rate) and the range includes skew.
- Battery fix: convergence used n/16 paths (odd for 2,000) -> m, 4m, 16m with m even.
- Dev spend $5.03 of $80. Tests: all pass.

## Day 6 verifiable agent (Oct 27, night)
- `agent/provenance.py`: every number/date in generated text must match an engine output, a
  cited filing value or a stated methodology constant (rounding rule |x - v| <= 0.5*10^-d);
  numbers inside verified quotes and verified cited strings are exempt. The 38582 report passes.
- `agent/crosscheck.py`: second extraction (muse-spark-1.2 until BASETEN_API_KEY exists), field
  diff, re-check accepted only with a verified quote; non-first-wins go to the user. Live on
  38582: 0 disagreements ($0.05), recorded in `tests/fixtures/crosscheck/`.
- `registry/sandbox.py` + `_sandbox_child.py`: static allow-list, subprocess, timeout, rlimits,
  empty env, network blocked, proxy modules (no submodules), audit hook. Security-reviewed;
  found and closed: `from numpy import load`, generator-frame escape, module reach via f2py etc.
- `payoffs/synthesized.py` (price sandboxed cash flows, risk-neutral, issuer spread) and
  `validation/synth_battery.py` (filing payout examples to the cent with verified quotes,
  exact template match on paths called before maturity, sanity, determinism, convergence).
- `agent/synthesis.py` + `registry/store.py` (sha256 tamper check). Live on the refused
  ea0306095 dual-directional note: Wonder passed all 6 checks on attempt 1, 20/20 filing
  examples, $0.14; registered as `synthesized_46661pwm5`. Base value $1,083.27 +- 0.54 with
  illustrative vols 30/35/28%; $978.55 at 1.5x vols; bank $980. Use real implied vols.
- Suite: 1461 tests, ~80 s (sandbox subprocesses).

## New direction (Oct 28, agreed after two external reviews)
Frontier = **verified AI-written financial code**; structured notes are the proving ground. Two
additive features, each ending at a checkpoint where Claude stops and reports. Budgets (Meta
credits): Part A <= $12, Part B <= $15, total <= $27. Never touch the live app's reserve.

## Part A market audit (checkpoint 1, Oct 28) — done
- `scripts/market_audit.py` + `quantproof/report/audit_run.py`: EDGAR full-text search ("estimated
  value", 424B2, last 60 days) across 13 issuer CIKs; filters before any model call (424B2 header,
  size, preliminary, disclosed estimated value); per note: Spark 1.3 extraction, quote check,
  cross-check with **muse-spark-1.2** (Baseten still missing), template support + `template_gaps`
  (principal protection, excess-return index: not modelled -> unsupported with the filing's
  wording), proof battery gate, pricing under stated assumptions (r 3.7%, spread 0.6%, vol 25%,
  div 1%, rho 0.6, ERP 5%). Hard budget guard (pre-flight worst case). Recorder/Replay: everything in
  `data/market_audit/` (filings gz, calls, results) and `--replay` re-runs it offline at $0.
  Audit calls log to `.cache/audit_calls.jsonl`, never the app's cost log.
- `quantproof/report/market_audit.py` + `scripts/market_audit_export.py`: per-note rows,
  aggregates from saved rows only, `data/market_audit/notes.csv|json` (every field's quote),
  `docs/MARKET_AUDIT.md`, neutral-language check. App: sidebar page "Market Audit" (saved data only).
- Result: 44 notes, 11 issuers; 34 extracted (10 schema failures recorded); 8 priced, 26
  unsupported (digital, absolute-return, threshold settlement, protection...); quote verification
  95.2%; median cost to investor 1.6% of issue price ($15.96 per $1,000, bank's figures); bank
  value inside our range 2 of 8 (ours mostly below under the flat 25% vol assumption). Model time
  44 min, cost $4.88. Review caught 3 Morgan Stanley principal-protected notes mis-mapped to a 100%
  barrier and priced at ~$1,100-1,330; now gated as unsupported.
- Spend Part A: $4.88 audit + $1.15 Muse drafts = **$6.03 of $12**. Dev log total $9.27.
- Local note: a test read the real local cost log (audit spend made "today" look capped); it now
  runs from a temp dir.

## Checkpoint 2 (Oct 28): gate, re-extraction, synthesis benchmark — done
- **Plausibility gate** (`audit_run.plausibility_issues`): base > issue price +2%, or bank estimate
  above our whole range -> status `needs_review` (not counted as priced). Test replays the 3 MS
  misreads with `template_gaps` switched off: the gate alone stops all 3 (base 19-23% above issue).
  Under the flat 25% vol it also holds 6 of the 8 template-priced audit notes (bank above our range).
- **Schema extension**: payment dates stated as "N business days after" (cited lag, verified against
  the review date; `core/calendar.py`, weekdays only, holidays not modelled); downside level 0 =
  principal protection -> unsupported feature with the filing's quote. `--redo` re-ran the 10 failed
  notes live ($1.30): **8 now extract** (34 -> 42); 1 GS invalid JSON again; TD review dates only
  relative ("third business day prior to"). One protected Citi note came back as a 100% barrier and
  the gate caught it (needs review). Audit now: 6 priced, 7 needs review, 29 unsupported, 2 failed.
- **Synthesis benchmark** (`report/synth_run.py`, `scripts/synthesis_benchmark.py`, replay $0):
  templates disabled; Muse Wonder 1.0 (medium), sandbox, battery, up to 3 repairs with a concrete
  counterexample; repairs stop when only filing evidence is missing. 10 notes (8 unsupported + 2
  template-covered): **9 of 10 pass the battery**, 8 first try, 1 repaired (BMO threshold settlement:
  rounded gearing, cents off, fixed to 1/0.85). Coverage: templates 6 of 42; with proven AI payoffs
  **12 of 42**. Generalisation: both template notes AI = template (diff $0.00, within 3 SE), filing
  tables to the cent (AI and template). GS bearish note held by the gate: code correct (Black-Scholes
  check $1,052.96 vs $1,053.43 +/- 0.48), value high because flat 25% vol (net long puts) — needs
  real vols. MS 61781THB2 fails: filing has only 2 worked examples (battery needs 3).
  `docs/SYNTHESIS_BENCHMARK.md`; app page "Synthesis replay".
- Fixes found on the way (general): terms dict sent "DownsideKind.BARRIER" (now "barrier");
  example verifier reads percent-of-principal, total-return and prose examples (cell by cell, signs
  kept); ingest drops glued "o" bullets ("oIf the underlier"); audit replay unchanged by each.
- **Not done: per-note Treasury rate.** `home.treasury.gov` is blocked by the network policy
  (Joyeb to allow it); audit and benchmark still use the flat stated r 3.7%.
- Spend since checkpoint 1: gate $0; re-extraction $1.67 of $2 ($1.30 redo + $0.36 draft); Part B
  $3.24 of $15 ($2.39 benchmark calls incl. superseded runs kept in `superseded/`, $0.85 drafts).
  Dev log $10.49 of $80.
- Next checkpoint: Treasury rate per pricing date (once allowed); wire Muse's anchored vols from
  `quantproof/market/` (do not edit it here); rerun gate; MS prose-examples note with 3+ examples.

## Day 7 app + polish (Oct 28, built by Muse Code on Joyeb's PC, reviewed and tested)
- Code moved from `scripts/analyse.py` into `quantproof/report` (CLI re-exports; example byte-identical).
- `app/` Streamlit: **demo mode** (default; saved filings, cited term sheet with cross-check,
  confirm/edit, illustrative inputs, proof-battery gate, verdict, real-world risk, heatmap) and
  **live mode** (EDGAR link or upload -> Muse Spark 1.3 extraction -> quote check -> cross-check ->
  confirm -> proof battery -> verdict), with step-by-step progress. Unsupported features are shown
  with their quotes and never priced. Dollar signs escaped (Streamlit LaTeX).
- Cross-check: Baseten credits have not arrived, so it runs **Muse Spark 1.3 twice** and says so.
- **Cost guard:** 3 live runs per session; daily cap $5 (`QP_DAILY_CAP_USD`); lifetime cap $20
  (`QP_TOTAL_CAP_USD`, over the cost log); pre-flight worst-case estimate (2 extractions + 3 re-checks
  at max output) refuses a run that would breach the cap; uploads <= 2 MB; 424B2 only; filing
  <= 400k chars (`QP_MAX_FILING_CHARS`); same filing served from the session cache. Keys only from env
  or Streamlit secrets. The cost log resets on host restart; Meta billing alerts are the backstop.
- **Deployed** on Streamlit Community Cloud (steps in `DEPLOY.md`). A live run takes ~80 s and
  cost ~$0.13.
- 38582 now uses the real risk-free rate (U.S. Treasury par curve 2025-11-25, interpolated to
  maturity: 3.5515%, CSV in `docs/data`); vols, dividends and correlation remain stated assumptions.
  Example report regenerated.
- Validation report includes the registered ea0306095 payoff (via the synth battery).
- README rewritten as a research note, live URL first. Demo video link still a placeholder.
- Suite: 1,500 passed, 13 skipped.

## Since Day 7 (Oct 28-29, from git log + generated docs)

- Anchored per-note inputs: Treasury par yield per pricing date, FRED implied-vol centres (VIX/VXN/RVX, relative ±15% band), FRED realised correlation; audit re-priced offline; `docs/MARKET_AUDIT.md` regenerated.
- Cross-check now Muse Spark Open via Baseten as second extractor; audit re-cross-checked (42 notes: 12 fully agreed; 62 differing fields: 36 resolved second, 20 first, 6 unresolved).
- Synthesis benchmark second round (21 more notes) with `--model wonder|open`; open-model results committed.
- Final numbers (`docs/MARKET_AUDIT.md`, `docs/SYNTHESIS_BENCHMARK.md`): audit 44 notes read / 11 issuers / 42 extracted / 11 priced + 2 needs-review / quotes 95.2% / bank in range 11 of 13 / median investor cost 1.9%; synthesis coverage 11 of 42 -> 26 of 42, 24 of 31 pass (77.4%), 19 first-try, 5 repaired, 6 failed, 1 skipped; cost audit $8.5246 (Baseten $3.0296) + synthesis $3.5490 (open $2.2196, wonder $1.3293).
- Repair-loop note (Oct 29): 5 notes failed `filing_examples` after 1 attempt with 0-2 verified examples (truncated example quotes missing the payment sentence; model-written example rows with hypothetical numbers absent from the filing, e.g. 17334DBW1's "$105.00 / 5.00%" vs the filing's generic sentences; one note with only 2 prose examples in the filing). The stop rule (`_evidence_missing`: only verified < 3 stops) already does the right thing: disagreements with verified evidence do repair (5 notes repaired to pass). No code change.
- Correction (Oct 30, freeze review): there is NO "×" decoding bug and NO mangled characters anywhere: 0 U+FFFD in all 44 filing texts, all recorded model replies and all saved results (checked byte-level); filing `×` arrives only via `&#215;` entities, resolved correctly by HTMLParser; `normalise_for_match` maps × to a space on both sides so × cannot break verification by construction. The `�` in the Oct 29 report was a display artifact of the reporter's tool output, not data. Offline full-`verify_termsheet` replay over all saved notes reproduces the saved counters exactly (978/1027 = 95.2%, 0 notes differ; the 2 extraction_failed notes fail identically). No ingest change made.
- App opens on an Overview page (headline numbers, links to Market Audit / Synthesis replay).

## Freeze review (Oct 30, no model calls; no code changes — nothing to fix)

- Maths (`models/`, `payoffs/`, `valuation/`, `risk/`): BSM/Greeks/digitals, exact GBM steps with caller-labelled drifts, Act/365F + issuer-spread discounting, worst-of template (memory coupons, autocall-before-maturity ordering, EACH_REVIEW breach flags on alive paths only, upside-before-downside split), fair-value grid (common random numbers, PSD-safe correlation blend, anchored relative ±15% vol band), real-world loss odds (undiscounted cash, iid + binomial SEs, `prob_called` before final only) — all correct against Hull/Glasserman. Battery closed forms re-derived: buffer-100% = bond − ATM put; barrier = bond − put(K) − (1−K) digital; never-callable digitals use payment-date DF × review-date Q; zero-vol cases exact. No bugs.
- `market/anchored.py`: VIX/100 conversion correct; constant-series raises (no NaN leak); misaligned closes fall back to stated with a label. Two non-bugs: `vol_anchored` is label-sniffed but pinned by tests — left alone; average-realised rho has no equicorrelation PSD clamp — unreachable in practice (equity rhos > 0), left alone.
- Sandbox: allow-list + proxies + audit hook + empty env + rlimits/netns/timeout reviewed adversarially (unicode-identifier NFKC, star-imports, bytes-built dunders, pre-imported os/sys via audit hook, cash-shape/dtype/finiteness gates). No escape found; child/host allow-lists have parity tests. Windows runs degrade honestly via the reported isolation list.
- Provenance: rounding rule, verified-quote blanking, bool exclusion all correct. Two known leniencies, not bugs: ×100 candidates also match bare numbers (pinned by tests), and exotic notations (scientific/hex, year-less dates) are invisible to the scanner — flagged, regex untouched at freeze.
- Edge audited and dismissed: `check_zero_vol` would divide by zero at downside level 0, but `TermSheet.__post_init__` rejects level ≤ 0 (`termsheet.py:170-171`), so it is unreachable — no change.
- Flaky test: NOT reproduced. 5+ full-suite greens plus hash-seed (1/7/13/42/99) variation across the whole suite; hunted every len-assert, recorded-calls load, set-ordering, wall-clock, cache and data-dir dependence. No code change — a blind edit at freeze would risk real harm. Full hunt in the session report.
- Docs: README/MARKET_AUDIT/SYNTHESIS_BENCHMARK every number re-checked against generated docs and saved rows (cross-check 12/42, 62 = 36+20+6; coverage 11+15 = 26 of 42; costs $8.52+$3.55 = $12.07; proof table and 38582 example figures match VALIDATION.md and the example report; cost-guard constants match `live.py`). No unbacked claims; no wording changes needed.
- Suite at freeze: 1630 passed, 13 skipped, 0 failed (canonical `python -m pytest -q`).

## Independent model-validation review (Oct 29, no model calls)
- Fixed (with tests): `market/anchored.py` paired the two FRED close series by position, not date;
  FRED drops missing values, so equal-length series with different gaps were correlated across
  different days (perfectly correlated test series gave rho 0.68). Now aligned on common dates.
  No saved result used a FRED correlation (12 single-underlying, 1 stated 0.6), so no number moves.
- Fixed (with test): `agent/provenance.py` did not read U+2212 ("−5.2%") as a minus sign, so a
  negative stated number passed against a positive engine value. Now a sign.
- Reviewed, correct: GBM exact steps + PSD factor + antithetic pair SE (`models/montecarlo.py`);
  BSM/Greeks/digitals/parity (`models/bsm.py`); worst-of template (memory coupons, call before
  maturity, per-review breach flags on live paths, payment-date discounting); fair-value grid (CRN,
  convex correlation blend); real-world loss odds (undiscounted cash vs issue price, binomial SE);
  synthesized pricing reuses the trusted simulation/discounting; sandbox (proxies without
  submodules, audit hook, netns, rlimits, empty env, dtype/shape/finite gates) held against escape
  probes; provenance rounding rule.
- Noted, not changed: `fair_value.py` skew docstring says skew always lowers value (true only for
  net-short-vol notes) and base (100k paths) need not lie inside [low, high] (20k-path grid);
  `synthesized.payment_dates` discounts a final column that is also an autocall review at the
  autocall payment date (normally = maturity); sandbox exposes `scipy.stats.*.random_state`
  (unseeded randomness; the determinism check catches it); provenance docstring says "$908" must
  not match 907.724 but the rounding rule (correctly) allows it; `quotes.normalise_for_match` drops
  signs, so value support is sign-blind (extracted levels are positive).
- Suite: 1645 passed.

## Live second extraction on Baseten Open (Oct 30, no live calls; fakes only)

- App live mode now cross-checks with Muse Spark Open (`meta-models/muse-spark-open-1026`)
  via the existing Baseten client; first extraction stays Muse Spark 1.3. Key from env or
  Streamlit secrets (`BASETEN_API_KEY`), sent only as the auth header, never logged.
- Missing key / failure / incomplete second extraction -> cross-check shown as unavailable,
  never agreement (`LiveNote.agreed=False`, `crosscheck_error` shown; UI agreement line names
  both models). Disagreements stay visible; at most 3 re-checked (new `max_rechecks` in
  `crosscheck.recheck`/`crosscheck`; audit path unchanged, default unbounded).
- Guard: Meta + Baseten logs stay separate files; daily $5 / lifetime $20 / 3-per-session
  apply to COMBINED spend. Pre-flight estimate now per-provider and assumes every planned
  call retries to the limit (3x) at max output (fixture filing: $0.77; 400k-char max: <$5).
- Limits that cannot be enforced reliably: calls that fail after all retries log nothing on
  either provider (covered only by the estimate); both cost logs reset on host restart
  (Meta billing alerts are the backstop). Audit's own guard still assumes 3 rechecks while
  three recorded notes used 4-6 (pre-existing; audit untouched for replay determinism).
- README updated (two-model wording, combined guard, stale $0.13 retired, repair 1/0.875).
- Remaining risks: (1) Joyeb must add `BASETEN_API_KEY` to Streamlit secrets or every live
  cross-check shows unavailable; (2) no live end-to-end run yet (fakes only) — do one
  watched run before submission; (3) estimate is deliberately ~3x the old figure, so heavy
  filings near a nearly-spent cap refuse earlier than before.
- Suite: full run after the change (below).

## Left to do (in order)
1. ~~By the **Nov 1 checkpoint**: decide on Modal / Muse Glimmer as a second-model cross-check~~
   DONE Oct 30: Baseten Open wired as the live second extraction (above).
   (fallback while Baseten credits are missing); otherwise keep Spark 1.3 twice, stated openly.
2. Final check of `docs/VALIDATION.md` (regenerate, every row explained) before submission.
3. Joyeb: rehearse and record the 3-minute demo; put the public video link in the README
   (replace "_link to be added on Day 8_"); phone/laptop check of the live app.
4. Submit **Nov 4 by noon Central** (deadline 8 PM): Indie track, Push the Frontier, live URL first,
   repo, video, Meta usage (log Muse Code's work in `docs/MUSE_DEV_LOG.md`), X post tagging @AIatMeta.
   Freeze the repo after submitting.
5. Joyeb: read the JPM key terms himself; derive d1/d2; explain "short a put" and worst-of.
6. Open limitation: synthesized payoffs get a base value only (no fair-value range / loss odds yet).

## Environment facts
- Recent JPM filings are PDF-to-HTML conversions: `bg*.jpg` are whole-page backgrounds (not
  diagrams), text lines wrap like the PDF, table cells come one per line. Extract them text-only.
- Meta key is a **network secret** (proxy adds the header); `MODEL_API_KEY` not set. `/v1/models`
  returns 200 with 9 models: muse-spark-1.3 (default), 1.2, 1.1, -contributor variants (avoid),
  muse-wonder-1.0, muse-image-1.0, muse-voice-transcribe-1.0, sam-3.1.
- Muse Spark reasons before answering: give `max_tokens` >= 4000 or replies come back empty.
  Reasoning is ~70% of output tokens. Non-streamed calls over ~30 s fail with 502: stream.
- Chat Completions works (`/v1/chat/completions`, OpenAI-style image parts as base64 data URIs).
- SEC domains **allowed** (Oct 27): `www.sec.gov`, `efts.sec.gov`, `data.sec.gov` all return 200
  with a declared User-Agent. `SEC_USER_AGENT` is set in the environment (visible in new
  sessions); in an older session pass it on the command only. Never write its value to the repo.
  JPMorgan Chase Financial Co. LLC (issuer) is CIK 0001665650. `BASETEN_API_KEY` not set. Local venv: `.venv/` (numpy, scipy, pytest).
- Force-push to `main` is blocked by the environment; Joyeb did the one history cleanup himself.

## Decisions to keep
- Commit as Joyeb (noreply address), **no Co-Authored-By trailers**.
- Development mostly on Meta credits: up to $80 dev, keep >= $50 for product/demo.
- Credits: Meta $149.98 left (Oct 27). Claude cloud credit expires Nov 5, 1:59 AM CST.
- Deadline Wed Nov 4, 8:00 PM Central; target noon. Problem statement: Push the Frontier.
