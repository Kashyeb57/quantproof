Live app: https://kashyeb57-quantproof-app-snew9v.streamlit.app/

# QuantProof

QuantProof reads the SEC pricing supplement (a 424B2 filing) of a structured note and shows, under stated assumptions, an independently computed estimated-value range next to the bank's own estimated value and the issue price, plus how and when the note can lose money. Meta models extract the note's terms with a filing quote for every field and a second extraction cross-checks them; our own engine does all the maths, and no payoff is priced until its code passes a proof battery of closed-form, limiting-case and monotonicity checks. Every number in a report comes from an engine output or a cited filing value, never from a model.

Demo video: _link to be added on Day 8_.

## The problem

Banks sell complex derivatives to ordinary investors as structured notes: autocallables, barrier notes and buffered notes on an index or a stock. Each comes with a long pricing supplement filed on SEC EDGAR that few buyers can read. Inside, the bank discloses that its own estimated value is lower than the price the investor pays, citing structuring and hedging costs and expected profit. Investors rarely see what the note is worth, how it pays off, or how much they can lose. A general-purpose AI used naively makes this worse: language models are fluent but invent numbers. QuantProof is built so that a model can read the filing but can never state a number.

## How it works

1. **Ingest.** An EDGAR link or an uploaded 424B2 is downloaded under SEC fair-access rules and decoded into cleaned text, tables and images.
2. **Extract.** Muse Spark 1.3 reads the filing and returns a structured term sheet; every field carries the exact filing quote it came from.
3. **Verify quotes.** Code (not a model) confirms each cited quote appears verbatim in the normalised filing text and states its value. Anything unverified is flagged to the user, never used silently.
4. **Cross-check.** A second, independent extraction — Muse Spark Open on Baseten — is diffed field by field against the first; up to 3 disagreements are re-checked against the filing, and every remaining disagreement is shown to the user to confirm. Without a Baseten key, or when the second extraction fails or comes back incomplete, the app shows the cross-check as unavailable, never as agreement.
5. **Confirm.** The user reviews the term sheet with its quotes, may edit fields (edited fields are labelled "user-entered"), and must confirm before any pricing.
6. **Compile.** The term sheet becomes a payoff function: a worst-of autocallable or buffered/barrier template, or, for a feature no template covers, agent-written code (step 8).
7. **Prove.** The payoff must pass the proof battery (below) before any price is shown.
8. **Synthesise (offline only).** Muse Wonder can write payoff code for an unsupported feature. The code runs only in a sandbox (subprocess, timeout, no network, import allow-list) and enters the registry only after passing the full synthesized-payoff battery. The public app never calls Muse Wonder; it shows the unsupported feature with its quote instead.
9. **Price, risk, verdict.** Risk-neutral Monte Carlo gives a fair-value range over an assumption grid; a separate, labelled real-world simulation gives loss odds and a stress heatmap. A provenance check confirms every number in generated text matches an engine output or a cited filing value.

## Proof and validation

The validation report is generated from the proof battery over four real JPMorgan filings; maths checks and quote checks are reported separately, because a field needing user confirmation is not a maths failure:

| Note | Maths | Quotes |
| --- | --- | --- |
| jpm_424b2-38582 | Maths 13/13 pass | Quotes 77/78, 1 field needs user confirmation |
| jpm_424b2-ea0309017 | Maths 14/14 pass | Quotes 74/74 verified |
| jpm_424b2-ea0308498 | Maths 12/12 pass | Quotes 71/73, 2 fields need user confirmation |
| jpm_424b2-ea0306095 | Maths 6/6 pass | Quotes 38/38 verified |

The maths checks compare the Monte Carlo engine against independent closed forms, textbook values, deterministic limits and economic monotonicity properties (the full list, with charts, is in the validation report). The ea0306095 dual-directional note is priced in the report through its registered agent-written payoff, which passed the synthesized-payoff battery (filing payout examples to the cent, exact template match on called paths, determinism, convergence). Full details: [docs/VALIDATION.md](docs/VALIDATION.md). End-to-end example: [docs/examples/jpm_38582_analysis.md](docs/examples/jpm_38582_analysis.md).

## Results on the JPM 38582 note

From the generated example report ([docs/examples/jpm_38582_analysis.md](docs/examples/jpm_38582_analysis.md); worst-of autocallable on the Russell 2000 and S&P 500, based on preliminary terms):

- Issue price: $1,000.00. Bank's estimated value: $954.00. Our estimated value range under the stated assumptions: $774.88 - $974.06, with the bank's value inside the range.
- Base estimated value: $904.66 +- $1.46 (1.96 SE, SE = $0.74; seed 0, 100,000 paths).
- The assumption that moves the estimated value most is volatility ($951.33 to $859.93 across its stated range).
- Real-world loss odds (separate REAL-WORLD measure, not a value; drift = risk-free rate + 5.0% equity risk premium): P(loss) 10.17% (SE 0.10%), P(loss > 50%) 3.42% (SE 0.06%), P(called early) 81.72% (SE 0.12%), expected cash $1,080.84 (SE $0.66). Issuer default is not simulated: if the issuer defaults, you can lose money regardless of the index.

## Market audit and synthesis benchmark

Two generated reports summarise what the pipeline did on real filings: [docs/MARKET_AUDIT.md](docs/MARKET_AUDIT.md) and [docs/SYNTHESIS_BENCHMARK.md](docs/SYNTHESIS_BENCHMARK.md). Headlines (every figure from those files and the saved rows behind them):

- Market audit: 44 notes read across 11 issuers; 42 extracted; 11 priced with templates and 2 held by the plausibility gate for review; quote verification 95.2%; the bank's estimated value inside our range on 11 of 13 priced-or-held notes (84.6%); median cost to investor 1.9% of issue price ($19.15 per $1,000, the bank's own figure).
- Cross-check: every extraction compared with a second extraction by Muse Spark Open (Baseten). 12 of 42 agreed on every field; the 62 differing fields were re-checked against the filing with a verified quote (36 resolved to the second extraction, 20 to the first) and 6 stayed unresolved for the user to confirm.
- Synthesis: templates priced 11 of 42 extracted notes; with proven AI-written payoffs, 26 of 42. Of 31 attempted notes, 24 passed (77.4%): 19 on the first attempt, 5 after repair; 6 failed, 1 skipped (fewer than 3 filing examples). The battery failed 13 attempts on filing examples and 5 of those notes were repaired (for example a rounded 1.1429 gearing fixed to the exact 1/0.875). On the 2 template-covered notes the AI payoff matched the template within 3 standard errors, and both matched the filing tables to the cent.
- Model cost: market audit $8.52 (of which $3.03 Baseten credits), synthesis benchmark $3.55 (of which $2.22 Baseten credits); $12.07 combined.

## Methodology

- **Two measures, never mixed.** Fair value uses a risk-neutral simulation. "How likely am I to lose money?" uses a separate real-world simulation with a stated, adjustable drift assumption, labelled as such everywhere. Risk-neutral probabilities are never presented as real-world odds.
- **Issuer credit risk.** A structured note is unsecured debt of the bank, so cash flows are discounted at the risk-free rate plus an issuer credit spread. Banks value their own notes with an internal funding rate, usually below the market credit spread; our value can therefore legitimately differ from theirs, and the report states which rate is used and why. The bank's estimated value is a comparison point, never a calibration target.
- **Risk-free rate.** The real U.S. Treasury par yield for the filing's pricing date, interpolated to the note's maturity (see Data sources). For the 38582 note: U.S. Treasury par yield, 2025-11-25, 5.01y interpolated between 5 Yr and 7 Yr, r = 0.035515 per year.
- **Volatility, skew, dividends, correlation** are inputs with stated ranges, not point estimates. For index underlyings the range is centred on the 30-day implied-volatility close on the pricing date (VIX/VXN/RVX via FRED), a stated simplification; single stocks and other underlyings keep stated volatilities. These notes are economically short a downside put, so a single flat volatility would understate the put and overstate the note; a stated skew adjustment (extra volatility at the downside level) is included in the range. For worst-of notes, lower correlation makes the worst performer worse and the note worth less, so correlation is varied over its range too.
- **Pricing-date inputs.** All inputs are as of the filing's pricing date; the initial level comes from the filing itself.
- **Preliminary terms.** Many filings are preliminary (amounts are minimums, dates "on or about"). The note is priced on the stated minimum terms and labelled as based on preliminary terms.
- **A range, not "the true value".** One set of assumptions gives one over-precise number. The range over the stated assumption grid, with the standard error, seed and path count, is the honest output.

## Assumptions and limitations

- Flat volatility with a simple, stated skew adjustment; no volatility surface or smile model.
- Constant interest rates, dividend yields and correlations over the life of the note; the Treasury rate is a single interpolated par yield, not a fitted curve.
- Correlation in the app is a single all-pairs value; volatilities, dividends and correlation are user-entered stated assumptions, not licensed market data.
- No issuer-default simulation in the loss odds or stress grid (the issuer-risk line states the consequence separately).
- Monte Carlo values carry simulation error; every reported value states its standard error, seed and number of paths.
- Extraction depends on the filing's text layer; fields without a verified quote are flagged for the user to confirm, and a note with a feature no template covers is not priced in the public app.
- Model risk: the engine and its stated assumptions may not match realised outcomes.

## Data sources and licences

- SEC EDGAR 424B2 filings (public filings, accessed under SEC fair-access rules with a declared User-Agent; downloads cached). Test fixtures are saved filings under `tests/fixtures/`.
- U.S. Treasury Daily Par Yield Curve Rates (public U.S. government data, home.treasury.gov), saved at `docs/data/treasury_daily_par_yield_curve_2025-11.csv` with its source URL. Works of the U.S. federal government are not subject to copyright in the United States.
- Volatilities, dividend yields, credit spread and correlation in the app and reports are stated assumptions entered by the user or used illustratively for testing, labelled "Illustrative inputs, not market data" wherever they appear (the Treasury risk-free rate is the exception above).

## Meta models used

- Muse Spark 1.3: first filing extraction (text and images) and cross-check re-checks in the public app.
- Muse Spark Open on Baseten (`meta-models/muse-spark-open-1026`): the independent second extraction in the public app; needs a `BASETEN_API_KEY` (environment or Streamlit secrets), otherwise the cross-check is shown as unavailable.
- Muse Wonder 1.0: payoff synthesis for unsupported features, offline only, behind the sandbox and proof battery; never called from the public app.
- Cost guard in the app: at most 3 live extractions per browser session; Meta and Baseten calls are logged apart (`.cache/meta_calls.jsonl`, `.cache/baseten_calls.jsonl`) but the daily cap of $5 (`QP_DAILY_CAP_USD`) and lifetime cap of $20 (`QP_TOTAL_CAP_USD`) apply to their combined spend. A pre-flight worst-case estimate (first extraction, Baseten second extraction, up to 3 re-checks, every call assumed to retry to the limit at maximum output) must fit under the caps or the run is refused before it starts; the same filing is then served from cache. Calls that fail after all retries log nothing on either provider, so their cost is covered only by the estimate, not the caps — and both logs reset when the host restarts, so a Meta Billing spend notification is the final backstop (see `docs/DEPLOY.md`). A measured live run on the deployed app took ~80 s (`docs/BUILD_PLAN.md`, Day 7); its ~$0.13 figure predates the Baseten second extraction — the app shows the worst-case estimate before each run.

## How to run

```bash
pip install -e ".[dev]"     # package + tests
python -m pytest -q         # offline, deterministic
pip install -e ".[app]"     # plus Streamlit for the app
streamlit run app.py        # then open http://localhost:8501
```

Offline end-to-end analysis of the saved JPMorgan filing:

```bash
python scripts/analyse.py tests/fixtures/filings/jpm_424b2-38582.htm \
    --reply tests/fixtures/extractions/jpm_424b2-38582.reply.json \
    --rate 0.035515 --spread 0.006 --vols 0.2336,0.1856 \
    --dividends 0.012,0.013 --corr 0.6 --battery \
    --out report.md
```

Deployment steps (Streamlit Community Cloud, or Hugging Face Spaces): [docs/DEPLOY.md](docs/DEPLOY.md).

## Built during the hackathon

Built during the Meta Global AI Developer Hackathon, Oct 27 – Nov 4, 2026 (Push the Frontier). Development ran largely on Meta models: Muse Wonder drafted code through `scripts/muse_draft.py` under the rules in `AGENTS.md`; every draft was reviewed line by line, tested, and only then committed. The per-call log of that drafting (task, files, tokens, estimated cost) is `docs/MUSE_DEV_LOG.md`. Pricing maths was checked against its docstrings, references and independent tests before acceptance. Tools used are disclosed here and in that log, per the owner's choice (no co-author trailers in commits).

## References

- J. C. Hull, *Options, Futures, and Other Derivatives* (Black-Scholes, barrier and autocallable payoffs, credit and funding discussion).
- P. Glasserman, *Monte Carlo Methods in Financial Engineering* (simulation, standard errors, common random numbers).
- SEC EDGAR filings used as fixtures, including JPMorgan Chase Financial Co. LLC 424B2 pricing supplements (e.g. CUSIP 48136LQH5), and the U.S. Treasury Daily Par Yield Curve Rates.
- Project documents: [docs/PROJECT_BRIEF.md](docs/PROJECT_BRIEF.md), [docs/VALIDATION.md](docs/VALIDATION.md), [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md).

QuantProof is risk analysis under stated assumptions, not investment advice. It makes no recommendation about any security; it reports the bank's own disclosures next to an independently computed analysis so the reader can judge.
