# Prompts for Claude Code (cloud)

Claude Code reads `CLAUDE.md` automatically, which pulls in the rules, so prompts can stay short.

---

## 0. The test filing (done)

The Day 1 go/no-go test passed on the JPMorgan worst-of autocallable 424B2:
https://www.sec.gov/Archives/edgar/data/19617/000183988225066171/jpm_424b2-38582.htm

If Claude Code ever needs a filing it can't download (blocked domain), save it in Chrome with
**Ctrl+S → "Webpage, Complete"**, zip the `_files` folder, and attach the `.htm` and `.zip` in the chat.
Once `www.sec.gov` is allowed and `SEC_USER_AGENT` is set, Claude Code fetches filings itself.

---

## 1. First build session — Day 1 (same session that ran the test)

Attach `quantproof-brief.zip` (this starter pack) in the chat, then paste:

```
Yes, create Kashyeb57/quantproof (public) and attach it to this session.

1. First commit: unzip the attached quantproof-brief.zip into the repo root exactly as it is
   (AGENTS.md, CLAUDE.md, docs/). Commit message: "docs: project brief, rules and build plan".
2. Read CLAUDE.md, AGENTS.md, docs/PROJECT_BRIEF.md and docs/BUILD_PLAN.md fully. They already
   include your test findings (worst-of in core scope, preliminary terms, quote verifier,
   encoding fix, bank value as comparison only).
3. Do the remaining Day 1 tasks from docs/BUILD_PLAN.md, in order: package skeleton + CI,
   core dataclasses, ingest parser with the encoding fix, extraction schema and quote verifier
   with tests (turn your test script into proper tested code; save the filing as a fixture).

Commit in small steps. Run pytest and report results. Then tick the finished Day 1 boxes in
docs/BUILD_PLAN.md and commit that too.
```

---

## 2. Every later session

```
Read CLAUDE.md and docs/BUILD_PLAN.md. Pull first. Do the tasks for Day N, in order.
Commit in small steps, run pytest, report results, then explain any new maths in plain English.
If anything in the plan looks wrong or risky, tell me before building it.
```
(Replace N with the day number. Tick the boxes in BUILD_PLAN.md as tasks finish.)

## 3. When you want a check on the maths

```
Review quantproof/<module>.py as a model-validation analyst would. List anything that is wrong,
untested or not explained, citing lines. Do not change code yet.
```

---

## Habits that keep Claude Code on track

- **One day's tasks per session.** Long sessions drift; start fresh each day.
- **If it drifts** (adds features, rewrites tests, adds dependencies): "Stop. Re-read AGENTS.md and
  stay within today's tasks."
- **Before accepting maths code, ask:** "Which test proves this is correct?"
- **Read every pricing function yourself** the same day it's written. That is your interview prep.
- **Keys:** stored as environment secrets only. Never paste a key into any chat or file.
- **Blocked domain?** Add it in the environment's network settings (allowed domains), then start
  a new session if the old one doesn't pick it up.
