# Checkpoint (read this after a /compact or in a new session)

Updated: Tue Oct 27, 2026 (Day 1 done, Day 2 started early). Repo: https://github.com/Kashyeb57/quantproof (public, `main`).

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

## Next (in order)
1. Value-support check (Day 4 proof battery, or sooner): a quote must contain its value (dates
   as written, percent forms of fractions, call premium %). Hand-built fixture quotes such as
   "Review Dates*:" would fail it; the model's quotes are stronger.
2. `ingest/edgar.py` EDGAR fetch: User-Agent from `SEC_USER_AGENT` (required, never logged), well
   under 10 requests/s, every download cached in `.cache/edgar/`; tests offline with fakes.
3. Extraction on 3 more filings (single-underlying, contingent coupon, buffered): needs filings.
4. Day 3 engine: `models/montecarlo.py`, `models/discounting.py`, `payoffs/`.
5. Joyeb: read the JPM key terms himself (table in the chat); derive d1/d2.

## Environment facts
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
