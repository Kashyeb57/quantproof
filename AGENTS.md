# AGENTS.md — rules for every coding agent working on QuantProof

Read `docs/PROJECT_BRIEF.md` (what we build and why) and `docs/BUILD_PLAN.md` (today's tasks)
before starting any task. If a request conflicts with them, ask before acting.

## Who you are working for

Joyeb is a CS + Mathematics student on a quant path. He must be able to **explain every line of the
pricing maths** in a graduate-school interview. So:

- Prefer clear code over clever code. No unnecessary abstraction layers.
- Every pricing function gets a docstring: formula, one or two sentences of intuition, reference.
- When you add maths, end your reply with a plain-English explanation and the test that proves it.

## Hard rules

1. **New work only.** Never copy code from other repositories or Joyeb's older projects.
   Licensed open-source libraries are fine as dependencies.
2. **No AI or network calls** in `models/`, `payoffs/`, `valuation/`, `risk/`, `validation/`.
   Pure Python + NumPy + SciPy.
3. **The AI never states a number.** Every number shown to a user comes from an engine output or a
   cited filing value. Keep the provenance check working.
4. **Generated code is untrusted.** Agent-written payoffs run in a subprocess with a timeout,
   no network, and an import allow-list (numpy, scipy, math). They enter `registry/` only after
   passing the full proof battery.
5. **Quant correctness (brief section 6) is non-negotiable:** risk-neutral for value, a separate
   labelled real-world simulation for loss odds; issuer credit spread in discounting; dividends and
   correlation as inputs with ranges; skew stated; all inputs as of the filing's pricing date;
   preliminary terms labelled; fair value as a range. **Never adjust assumptions to match the bank's
   estimated value**: it is a comparison point, not a calibration target.
5a. **Every extracted term needs a verified quote.** The quote verifier must confirm each citation
   appears verbatim in the normalised filing text; unverified fields are flagged, never used silently.
6. **Meta models only inside the product.** Meta Model API: `https://api.meta.ai/v1`. Send
   `Authorization: Bearer` only when env `MODEL_API_KEY` is set; otherwise send no auth header and rely on
   the cloud environment's network secret (see CLAUDE.md). Muse Spark Open via Baseten, key from env `BASETEN_API_KEY`.
   No OpenAI / Anthropic / Google model calls in product code.
7. **Secrets come from environment variables or host secrets only.** Never print, log, or commit a key.
   `.env` is in `.gitignore`. If a key appears anywhere in code or output, stop and tell Joyeb.
8. **No investment advice.** Never "buy", "sell", "overpriced", "don't buy". Use "risk analysis",
   "estimated value", "under these assumptions". Always show the bank's own estimated value alongside ours.
9. **SEC EDGAR fair access.** Send `User-Agent` from env `SEC_USER_AGENT`, stay well under the rate
   limit, cache every download. No scraping of any other site.

## Engineering standards

- Python 3.11+. Package layout and module names as in brief section 5.
- Type hints on public functions; dataclasses for TermSheet, MarketInputs, PricingResult.
- pytest in `tests/` mirroring the package. Tests run **offline** and **deterministically**
  (fixed seeds, saved filing fixtures, mocked API calls). Every new model or payoff ships with tests
  in the same commit.
- Monte Carlo results always report standard error, seed and number of paths.
- Test tolerances are explicit and justified in a comment (e.g. "3 standard errors").
- Run `pytest` before saying a task is done. Report results honestly, including failures.
- Minimal dependencies: numpy, scipy, pandas (if needed), beautifulsoup4/lxml (ingest), httpx,
  plotly, streamlit, pytest. Ask before adding anything else.

## Git workflow

- Small commits, one logical change each: `area: what changed`
  (e.g. `payoffs: add autocallable template with never-callable proof test`).
- Pull before starting; never force-push; never rewrite history on `main`.
- Commit as `Joyeb Kashyeb <168053382+Kashyeb57@users.noreply.github.com>`. **No `Co-Authored-By`
  trailers** in commit messages (owner's choice). Tools used are disclosed in the README instead.
- Do not delete or weaken tests to make them pass. Fix the code, or explain why the test is wrong.

## Development workflow: Muse drafts, Claude verifies

Joyeb wants development to run mostly on Meta credits, not Claude cloud credits.

- **Muse Spark 1.3 drafts new code** through `scripts/muse_draft.py`: send it the task, the relevant
  files and these rules; it returns complete files. Claude Code then reviews every line, runs the
  tests, fixes problems and commits. Never commit Muse output that has not been reviewed and tested.
- **Pricing maths** (`models/`, `payoffs/`, `valuation/`, `risk/`, `validation/`): Muse may draft, but
  Claude checks each formula against its docstring, the reference and independent tests before
  accepting it. Correctness beats credit savings here.
- **Claude credits are the scarce ones; Meta credits do the work.** Concretely:
  - Specs to Muse are short: the interfaces, the rules that matter, the tests that must pass.
  - Review findings go back to Muse as a short issue list (`muse_draft.py` with the file as
    `--context`); Claude edits by hand only one-line fixes and pricing maths.
  - Failing tests go to Muse with `muse_draft.py --pytest <test path>`, which runs pytest and sends
    the failure output with the task.
  - Claude reviews from test results and `git diff`, and reads whole files only for pricing maths
    (`models/`, `payoffs/`, `valuation/`, `risk/`, `validation/`) and security-sensitive code.
  - Live API experiments run as one script, not as many interactive steps.
- **Log every development call** (task, files, tokens, estimated cost) in `docs/MUSE_DEV_LOG.md`. It
  feeds the submission answer "use of Meta products to build the project".
- **Budget:** up to $80 of Meta credits for development; keep at least $50 for the product and demo.
  Stop and tell Joyeb if the log shows development spend above $80.
- **Keep context small.** Long sessions cost far more per step: update `docs/CHECKPOINT.md` at the
  end of each task, and Joyeb runs `/compact` (or starts a fresh session) at checkpoints. After a
  compact, re-read `docs/CHECKPOINT.md`. Keep replies and tool output short.
- Joyeb may use **Muse Code** on his PC for side tasks; note what it built in the same log.

## Cost awareness (~$150 of Meta credits)

- Live API calls only when a task needs them; mock them in tests.
- Default `muse-spark-1.3`; use `muse-wonder-1.0` only for payoff synthesis.
- Use prompt caching for filing text. Log tokens and estimated cost for every live call.
- The public app keeps its cost guard: per-session limit, daily spend cap, demo mode with cached runs.

## When you finish a task

Reply with: what you built, files changed, test results, anything left undone, and a plain-English
explanation of any maths you added.
