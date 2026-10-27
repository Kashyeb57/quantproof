# QuantProof — Build Plan (Oct 27 – Nov 4, 2026)

Deadline: **Wed Nov 4, 8:00 PM Central**. Target submission: **Nov 4, noon Central**.
Builder: **Claude Code (cloud environment)**. Joyeb reviews, understands and tests everything.
Optional: Muse Code locally for side tasks; if used, note what it built (for the submission form).

All times Central. Live workshops 11:00 AM; Discord office hours weekdays 9–10 AM.

## Environment setup (once, before Day 1 coding)
- [ ] Public GitHub repo `quantproof` created; these brief files committed
- [ ] Cloud environment secrets: `MODEL_API_KEY` (done), `SEC_USER_AGENT` ("Name email"),
      `BASETEN_API_KEY` (when Baseten credits arrive)
- [ ] Allowed domains: `api.meta.ai`, `www.sec.gov`, `efts.sec.gov`, `data.sec.gov`,
      the Baseten inference host (from Baseten's quickstart), plus package managers

---

## Day 1 — Tue Oct 27: go/no-go + foundation
- [x] **Extraction test: GO.** Muse Spark 1.3 on a real JPM worst-of autocallable 424B2 (preliminary):
      zero wrong fields, 17/17 dates and call amounts, 93/97 quotes verbatim (4 line-join issues),
      diagrams read correctly, ~22 s and ~$0.05 per filing. Product supplement not needed for pricing.
- [ ] Repo `quantproof` created (public); starter pack committed first
      (committed locally first; waiting for the empty GitHub repo to push)
- [x] `pyproject.toml`, package skeleton (brief section 5), `.gitignore`, CI running pytest
- [x] `core/`: TermSheet (+ Citation; N >= 1 underlyings; preliminary flag; call schedule; barrier
      type and observation), MarketInputs, PricingResult (with measure + provenance)
- [x] `scripts/muse_draft.py` (Muse Spark drafts code; cost logged in `docs/MUSE_DEV_LOG.md`), first,
      then use it for the items below (AGENTS.md: "Muse drafts, Claude verifies"). Streams replies.
- [x] `ingest/`: filing parser with correct charset decoding and Unicode normalisation
      (fixes the garbled "×"); the test filing saved as a fixture (Muse draft, reviewed)
- [x] `validation/quotes.py` quote verifier, with tests: all 78 cited fields of the fixture verify
- [x] `agent/extract.py` extraction prompt + JSON schema -> TermSheet (done on Day 2): live run on the
      JPM filing matches the hand-built term sheet on every value, 77/77 quotes verify
- [ ] Joyeb: read the test filing's key terms yourself and compare with the extraction
- [ ] Watch: Agents 101 (11:00 AM)

## Day 2 — Wed Oct 28: building blocks + reading more filings
- [x] `models/bsm.py` (done early, on Day 1): vanilla calls/puts, cash-or-nothing digitals, Greeks, implied vol; tests
      (textbook value S=42, K=40, r=10%, σ=20%, T=0.5 → call ≈ 4.76, put ≈ 0.81; parity; bounds)
- [x] `ingest/edgar.py` EDGAR fetch (User-Agent, rate limit, cache, full-text search, filing index,
      images); live run: search + index + one filing with 10 images in 10 s
- [x] `agent/client.py`: Meta API client (streaming Chat Completions, retries, prompt caching, token/cost
      log). Chat Completions instead of the Responses API: tested with images and streaming
- [x] Extraction + quote verification on at least 3 more real filings: one single-underlying, one with
      contingent coupons, one final (not preliminary); saved as fixtures (done Oct 27: single stock
      with monthly coupons, buffered worst-of-3 with coupons, dual-directional buffered; all final)
- [ ] Joyeb: derive d1/d2; explain each Greek and the digital option
- [ ] Watch: Agents 102 — Verifiable Agents (11:00 AM)

## Day 3 — Thu Oct 29: the pricing engine
- [x] `models/montecarlo.py`: correlated multi-asset GBM (Cholesky), risk-neutral and real-world drift,
      discrete observation dates, dividend yields, antithetic variates, standard errors, seeds
- [x] `models/discounting.py`: risk-free + issuer credit spread
- [x] `payoffs/`: worst-of autocallable (call premiums or contingent coupons, final barrier);
      worst-of buffered/barrier note at maturity (N = 1 covers single-asset notes)
- [x] Tests: each payoff on hand-computed paths
- [ ] Joyeb: explain why the note is "short a put", why worst-of lowers the value, and why autocall
      dates need path simulation

## Day 4 — Fri Oct 30: proof battery
- [x] `validation/`: every check in brief section 7 (incl. N = 1, correlation = 1, correlation
      monotonicity), applied to both templates
- [x] `docs/VALIDATION.md` generator with convergence and decomposition charts
- [ ] Joyeb: explain the never-callable decomposition and the barrier-at-maturity closed form
- [ ] Watch: Fireworks workshop on Muse open models (11:00 AM)

## Day 5 — Sat Oct 31: value + risk — **must-haves done today**
- [x] `valuation/`: fair-value range over vol × correlation × spread × dividend grid, skew adjustment,
      bank comparison (comparison only, never a calibration target)
- [x] `risk/`: real-world simulation (labelled, stated drift), loss probabilities, stress heatmap
- [x] End-to-end run on the test filing: ingest → extract → verify quotes → price → report (CLI is enough);
      note where our range sits relative to the bank's ~$954 and explain why

## Day 6 — Sun Nov 1: the verifiable agent — **CHECKPOINT**
- [ ] `agent/crosscheck.py`: Muse Spark Open (Baseten) second extraction, field diff, re-check loop
- [ ] `agent/synthesis.py` + `registry/`: Muse Wonder writes payoff code → sandbox → battery → register
- [ ] `agent/provenance.py`: every number in generated text matches an engine output or citation
- [ ] **Checkpoint (evening):** all must-haves green? If not, cut stretch items now. No new features after tonight except the app.

## Day 7 — Mon Nov 2: the app
- [ ] `app/` Streamlit: EDGAR link/upload → TermSheet with citations and disagreements → confirm →
      proof checks → verdict (range vs bank value vs price) → real-world risk (labelled) → heatmap
- [ ] Cost guard: per-session limit, daily spend cap, demo mode with cached runs
- [ ] Deploy; secrets in the host; live URL tested on phone and laptop

## Day 8 — Tue Nov 3: polish + record
- [ ] README as a research note (brief section 13), including assumptions and limitations
- [ ] Final validation report committed
- [ ] Joyeb: rehearse and record the 3-minute demo (brief section 11)
- [ ] Joyeb: last submission questions in Discord (last full support day)

## Day 9 — Wed Nov 4: ship
- [ ] Video uploaded (public link); X post tagging @AIatMeta
- [ ] Submit by noon: Indie track, Push the Frontier, description (live URL first line), repo, video,
      Meta usage, why Meta's models matter, X link
- [ ] Freeze the repo after submitting
