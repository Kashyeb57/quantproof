# Checkpoint (read this after a /compact or in a new session)

Updated: Tue Oct 27, 2026 (Day 1). Repo: https://github.com/Kashyeb57/quantproof (public, `main`).

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

## Next (in order)
1. `scripts/muse_draft.py` + `docs/MUSE_DEV_LOG.md` (Muse drafts, Claude verifies; AGENTS.md).
2. `ingest/`: charset from the meta tag, Unicode normalisation, HTML -> text (fixes "×").
3. `validation/quotes.py`: verify each Citation quote in the normalised text; set `verified`.
4. Then Day 2 of `docs/BUILD_PLAN.md` (bsm already done early).

## Environment facts
- Meta key is a **network secret** (proxy adds the header); `MODEL_API_KEY` not set. `/v1/models`
  returns 200 with 9 models: muse-spark-1.3 (default), 1.2, 1.1, -contributor variants (avoid),
  muse-wonder-1.0, muse-image-1.0, muse-voice-transcribe-1.0, sam-3.1.
- Muse Spark reasons before answering: give `max_tokens` >= 4000 or replies come back empty.
- Chat Completions works (`/v1/chat/completions`, OpenAI-style image parts as base64 data URIs).
- **Blocked domains:** `www.sec.gov`, `efts.sec.gov`, `data.sec.gov` (not yet allowed).
  `SEC_USER_AGENT` and `BASETEN_API_KEY` not set. Local venv: `.venv/` (numpy, scipy, pytest).
- Force-push to `main` is blocked by the environment; Joyeb did the one history cleanup himself.

## Decisions to keep
- Commit as Joyeb (noreply address), **no Co-Authored-By trailers**.
- Development mostly on Meta credits: up to $80 dev, keep >= $50 for product/demo.
- Credits: Meta $149.98 left (Oct 27). Claude cloud credit expires Nov 5, 1:59 AM CST.
- Deadline Wed Nov 4, 8:00 PM Central; target noon. Problem statement: Push the Frontier.
