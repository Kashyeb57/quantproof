# QuantProof — Project Brief

> AI reads the fine print of a structured note and shows what it is really worth,
> and how it can lose you money. Every number is computed and proved, never guessed.

Owner: Joyeb Kashyeb (solo, Indie track)
Event: Meta Global AI Developer Hackathon (Cerebral Valley)
Build window: Oct 27 – Nov 4, 2026. **Deadline: Wed Nov 4, 8:00 PM Central (9:00 PM ET).**
Problem statement: **Push the Frontier** (a verifiable AI capability for a real financial product).
Built in: Claude Code (cloud environment). Product AI: Meta models only.

---

## 1. The problem

Banks sell complex derivatives to ordinary investors as **structured notes**: autocallables,
barrier notes, buffered notes on an index or a stock. Each comes with a long pricing supplement
(a 424B2 filing on SEC EDGAR) that few buyers can read. Inside, the bank discloses that its own
**estimated value is lower than the price the investor pays**, citing structuring and hedging costs
and expected profit. Investors rarely see what the note is worth, how it pays off, or how much
they can lose.

General-purpose AI makes this worse if used naively: language models are fluent but invent numbers.

## 2. The idea

1. **Meta's models read the filing** (long text plus payoff diagrams and tables) and turn it into a
   precise, structured payoff specification. **Every field cites where it came from.**
2. **A second, independent model** (open-weight Muse Spark Open) extracts the same terms. Fields
   where the two disagree are flagged, re-checked, and shown to the user to confirm.
3. **Our engine does all the maths:** Monte Carlo pricing of the payoff, Greeks, stress tests.
4. **Proof before trust:** the payoff code must pass a battery of checks (section 7) before any
   price is shown. If a note has a feature no template covers, the agent writes payoff code for it,
   and that code must pass the same battery.
5. **The verdict, in plain English:** a fair-value range under stated assumptions, next to the
   bank's own estimated value and the issue price, plus how and when the investor loses money.

It is **risk analysis and education, never investment advice**. It never says "buy", "sell",
"overpriced" or "don't buy". It reports the bank's own disclosures next to our analysis.

## 3. Who it is for

- Individual investors offered structured notes by a broker or adviser.
- Advisers and students who want to understand these products.
- Anyone who wants an independent, transparent check of a note's value and risks.

## 4. Pipeline

```
EDGAR URL or uploaded 424B2
  -> ingest:   fetch filing (+ referenced product supplement if needed); split text, tables, images
  -> extract:  Muse Spark 1.3 (text + vision) -> TermSheet JSON, every field with a citation
  -> quotes:   code checks every cited quote appears verbatim in the filing (normalised text)
  -> verify:   Muse Spark Open (Baseten) extracts independently -> field-by-field diff
               -> disagreements re-checked by the agent, then confirmed by the user
  -> compile:  TermSheet -> payoff function (template; or agent-written code if no template fits)
  -> prove:    proof battery must pass (section 7)
  -> price:    risk-neutral Monte Carlo -> fair-value range over an assumption grid; Greeks
  -> risk:     separate real-world simulation (labelled) -> loss probabilities; stress heatmap
  -> verdict:  plain-English report; every number traced to an engine output
```

## 5. Architecture

```
quantproof/                  # Python package (pip-installable)
├── core/          # dataclasses: TermSheet (+ Citation per field), MarketInputs, PricingResult (provenance)
├── ingest/        # EDGAR fetch (SEC fair-access rules), correct charset decoding + Unicode
│                  # normalisation, HTML -> text sections, tables, images
├── models/        # pure maths, no AI
│   ├── bsm.py           # Black-Scholes building blocks: vanilla calls/puts, digitals, Greeks, implied vol
│   ├── montecarlo.py    # correlated multi-asset GBM (N >= 1, Cholesky) under risk-neutral or
│   │                    # real-world drift; discrete observation dates; dividend yields;
│   │                    # antithetic variates; standard errors; seeds
│   └── discounting.py   # risk-free curve + issuer credit spread / funding rate
├── payoffs/       # templates (all worst-of over N >= 1 underlyings; N = 1 is a single-asset note):
│                  #   1. autocallable: call premiums or contingent coupons, final barrier
│                  #   2. buffered / barrier note at maturity
├── valuation/     # fair-value range over assumption grid (vol x correlation x spread x dividend);
│                  # bank-value comparison
├── risk/          # real-world simulation; loss probabilities; scenario/stress heatmap
├── validation/    # proof battery, quote verifier (extraction proof), validation-report generator
├── registry/      # trusted payoffs; agent-written payoffs enter only after passing the battery
├── agent/         # Meta Model API client, extraction prompts + schemas, cross-check, synthesis,
│                  # orchestration, provenance check
└── app/           # Streamlit web app (live demo)
tests/             # pytest, offline, deterministic, fixtures from real filings
docs/              # brief, build plan, validation report, methodology, limitations
```

### Design rules

1. `models/`, `payoffs/`, `valuation/`, `risk/`, `validation/` contain **no AI and no network calls**.
2. Every pricing function has a docstring: formula, one or two sentences of intuition, reference.
3. `PricingResult` carries provenance: model, measure (risk-neutral / real-world), inputs, seed,
   paths, standard error, package version.
4. **Generated code is untrusted:** agent-written payoffs run in a subprocess with a timeout,
   no network, and an import allow-list (numpy, scipy, math). They enter `registry/` only after
   passing every applicable proof check.
5. **The model never states a number.** The provenance check extracts every number in an answer
   and confirms it matches an engine output or a cited filing value; otherwise the answer is rejected.
6. **Every extracted term is backed by a verified quote.** Code checks each citation's quote appears
   verbatim in the (normalised) filing text. Unverified fields are flagged to the user, never used silently.

## 6. Quant correctness requirements (non-negotiable)

1. **Two measures, never mixed.** Fair value uses risk-neutral simulation. "How likely am I to
   lose money?" uses a **separate real-world simulation** with a stated, user-adjustable drift
   assumption. UI and report label which is which. Risk-neutral probabilities are never presented
   as real-world odds.
2. **Volatility.** Implied-volatility data is often licence-restricted. Use user-entered or
   historical volatility from a licensed source, always shown as a **sensitivity range**.
3. **Volatility skew.** These notes are economically short a downside put. A single flat volatility
   undervalues that put and **overstates the note's value**. Include a skew adjustment as a stated
   assumption in the range (e.g. higher vol at the barrier level), and state it as a limitation.
4. **Dividends.** The underlying's dividend yield moves the forward and strongly affects
   autocallable values. It is an extracted or entered input, with a range.
5. **Issuer credit risk.** A structured note is unsecured debt of the bank. Cash flows are
   discounted with the risk-free rate **plus an issuer credit spread**. The report states that the
   investor can lose money if the issuer defaults.
6. **Funding rate vs market spread.** Banks value their own notes with an **internal funding rate**,
   usually lower than the market credit spread. Our value can therefore legitimately differ from
   theirs. The report states which rate we use and why.
7. **Compare like with like.** All inputs are as of the filing's **pricing date**. The initial level
   comes from the filing itself.
8. **A range, not "the true value".** Fair value is a range over the assumption grid, with the
   assumptions listed, plus a Limitations section, as a model-validation team would write it.
9. **Correlation (worst-of notes).** The value of a worst-of note depends strongly on the correlation
   between the underlyings: lower correlation means the worst performer is worse, so the note is worth
   less. Correlation is an input with a sensitivity range, estimated from properly licensed data or
   entered by the user, and stated in the report.
10. **Preliminary filings.** Many filings are preliminary: some terms are minimums ("at least",
    "approximately") and the pricing date is in the future. Price with the stated minimum terms and the
    filing's reference levels, and label the result as based on preliminary terms.
11. **Never tune to the bank's number.** The bank's estimated value is a comparison point, not a
    calibration target. Assumptions are chosen from data and stated before pricing; they are never
    adjusted to make our value match the bank's.

## 7. Proof battery

A payoff is trusted only after passing every applicable check:

| Check | What it proves |
|---|---|
| Known values | BSM building blocks: S=42, K=40, r=10%, σ=20%, T=0.5 → call ≈ 4.76, put ≈ 0.81 |
| Put-call parity | Building blocks are consistent |
| Barrier removed | Payoff reduces to the equivalent vanilla structure; price matches closed form |
| Never callable | Autocallable with an unreachable trigger values as bond + options (closed form / decomposition) |
| Barrier observed only at maturity | Matches the closed-form decomposition (zero-coupon bond − put/digital terms) |
| Zero coupon, no downside | Note values as a zero-coupon bond discounted at the issuer rate |
| Zero-volatility limit | Deterministic payoff, exact value |
| Single asset (N = 1) | Worst-of engine on one underlying equals the single-asset result |
| Identical assets, correlation = 1 | Worst-of of two identical, perfectly correlated assets equals the single-asset note |
| Correlation monotonicity | For standard parameter ranges, worst-of note value rises as correlation rises |
| Quotes verified | Every cited quote is found verbatim in the filing (extraction proof) |
| Monotonicity | For standard parameter ranges: value falls as vol rises (short-put notes) and as credit spread rises; rises with coupon |
| Convergence | Monte Carlo error shrinks ~ 1/√N; result within 3 standard errors of closed form where one exists |
| Determinism | Same seed → identical result |

The validation report (`docs/VALIDATION.md` + charts), generated from this battery, is a headline artifact.

## 8. Meta models — each with a real job

| Model | Job |
|---|---|
| Muse Spark 1.3 (`muse-spark-1.3`) | Reads the long filing (text + images of payoff diagrams and charts); extracts TermSheet with citations; orchestrates tools; writes the plain-English report under the provenance check. Day-1 test on a real JPM filing: zero wrong fields, 17/17 dates and call amounts, ~22 s, ~$0.05 per filing |
| Muse Spark Open (`meta-models/muse-spark-open-1026`, on Baseten) | Independent second extraction; the disagreement check that makes extraction trustworthy (open-weight bonus) |
| Muse Wonder (`muse-wonder-1.0`) | Writes payoff code for note features no template covers, which must then pass the proof battery (used only when needed; expensive) |
| Muse Code | Optional coding agent during development |

Meta Model API (verified on dev.meta.ai): base URL `https://api.meta.ai/v1`, Responses API at
`/v1/responses`, header `Authorization: Bearer $MODEL_API_KEY` when that variable is set (local or
deployed); in the cloud environment the network secret adds the header instead. Docs: https://dev.meta.ai/docs
(tool calling, structured output, image understanding, prompt caching).
Prices per million tokens: Spark 1.3 $1.25 in / $0.15 cached / $4.25 out; Wonder $4.00 in / $20.00 out.
Budget: ~$150 free credits. Prompt-cache the filing text; Spark for almost everything.

## 9. Data

- **SEC EDGAR filings are public.** Access follows SEC fair-access rules: declare a User-Agent with
  a contact (env `SEC_USER_AGENT`), stay under the request-rate limit, cache downloads.
- The **initial level and pricing date come from the filing**, so no price feed is needed for them.
- Volatility, dividend yield and credit spread: user-entered with defaults and ranges; any external
  data source must have terms allowing use in a public repo and demo. Document each source and its licence.
- Risk-free rates: a public-domain source (e.g. U.S. Treasury yield data), documented.
- No scraping of paid or restricted sites.

## 10. Scope

**Must have (end of Oct 31)**
- Ingest a real JPMorgan 424B2 from EDGAR (correct encoding; product supplement only if needed)
- TermSheet extraction with citations (Muse Spark) + quote verifier, validated on real filings
  (preliminary and final)
- Correlated multi-asset Monte Carlo (N >= 1) with discrete observations, dividends, issuer
  discounting, standard errors
- Two worst-of payoff templates: autocallable (call premiums or contingent coupons, final barrier);
  buffered/barrier note at maturity
- Proof battery + tests + CI; fair-value range; real-world loss simulation (labelled)

**Should have (end of Nov 2)**
- Muse Spark Open cross-check extraction with disagreement handling
- Agent-written payoffs for unsupported features (Muse Wonder) through sandbox + battery
- Provenance check on all generated text
- Streamlit app deployed with cost guard; validation report

**Stretch (cut first)**
- Daily-observed (American) barriers
- MCP server / skill pack exposing the engine
- Richer skew model

**Out of scope:** trading, orders, recommendations, real-time market feeds.

## 11. The 3-minute demo

1. **Problem (20 s):** a real bank note: $1,000 price, 30-page filing, "estimated value" buried inside.
2. **Read (40 s):** paste the EDGAR link → TermSheet appears field by field, each with its citation;
   the second model disagrees on one field → flagged → resolved.
3. **Prove (40 s):** payoff compiled → proof checks turn green one by one.
4. **Verdict (50 s):** fair-value range (stated assumptions) vs the bank's estimated value (e.g. ~$954)
   vs the $1,000 price; real-world chance of
   losing money (labelled, with its assumption); issuer credit risk; stress heatmap.
5. **Frontier moment (20 s):** a note with an unusual feature → agent writes the payoff → battery passes.
6. **Close (10 s):** validation report, repo, live URL.

## 12. Hackathon rules the code must respect

- New work only: fresh repo, no copied code (including older personal projects). Licensed libraries OK.
- Meta models do all AI work inside the product. No other providers' models in product code.
- Public repo; the demo shows only hackathon-built work.
- Live URL: first line of the submission description, top of the README, last frame of the video.

## 13. Definition of done

- `pip install -e .` and `pytest` pass on a clean machine; CI green.
- Validation report generated and committed.
- Live app deployed with cost guard (per-session limit, daily spend cap, demo mode with cached runs).
- README written as a research note: problem, method, validation results, assumptions,
  limitations (skew, funding rate, model risk), references, how to run, live URL,
  "Built during the hackathon" section, and data sources with licences.
- Demo video ≤ 3 min; X post tagging @AIatMeta; submitted before Nov 4, 8:00 PM Central (aim for noon).
