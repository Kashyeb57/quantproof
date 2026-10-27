# Recorded extraction replies

Real Muse Spark replies to `quantproof.agent.extract.SYSTEM_PROMPT`, saved so tests can check
the JSON -> TermSheet path and the quote verifier offline, without calling the API.

| File | Filing | Model | Recorded | Notes |
|---|---|---|---|---|
| `jpm_424b2-38582.reply.json` | `../filings/jpm_424b2-38582.htm` + `image2.gif`, `image3.gif` | muse-spark-1.3 | 2026-10-27 | 30 s, 12,023 in / 10,804 out tokens, ~$0.06. 77/77 quotes verify; gearing not stated (flagged) |

If the prompt changes, the recording stays a valid test of parsing and verification; re-record
it when the schema changes.
