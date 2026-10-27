# Recorded extraction replies

Real Muse Spark replies to `quantproof.agent.extract.SYSTEM_PROMPT`, saved so tests can check
the JSON -> TermSheet path and the quote verifier offline, without calling the API.

| File | Filing | Model | Recorded | Notes |
|---|---|---|---|---|
| `jpm_424b2-38582.reply.json` | `../filings/jpm_424b2-38582.htm` + `image2.gif`, `image3.gif` | muse-spark-1.3 | 2026-10-27 | 34 s, 12,607 in / 12,356 out tokens, ~$0.07. Re-recorded after upside / unsupported_features schema change. 78/78 quotes are in the filing and show their value; gearing not stated (flagged) |
| `jpm_424b2-ea0308498.reply.json` | `../filings/jpm_424b2-ea0308498.htm` | muse-spark-1.3 | 2026-10-27 | 30 s, 12,777 in / 10,985 out tokens, ~$0.06. Final single stock (Expand Energy, EXE). Text only, no images. EDGAR 0001213900-26-109100. dependency paraphrased and gearing not stated (flagged) |
| `jpm_424b2-ea0309017.reply.json` | `../filings/jpm_424b2-ea0309017.htm` | muse-spark-1.3 | 2026-10-27 | 48 s, 13,748 in / 18,127 out tokens, ~$0.09. Final worst of S&P 500, Russell 2000, Nasdaq-100. Text only, no images. EDGAR 0001213900-26-110708. All quotes verified |
| `jpm_424b2-ea0306095.reply.json` | `../filings/jpm_424b2-ea0306095.htm` | muse-spark-1.3 | 2026-10-27 | 28 s, 13,771 in / 10,523 out tokens, ~$0.06. Final Dual Directional Buffered Return Enhanced Notes, worst of three stocks (AMZN, TSCO, ORCL). Text only, no images. EDGAR 0001213900-26-101828. All quotes verified; Absolute Stock Return unsupported |

If the prompt changes, the recording stays a valid test of parsing and verification; re-record
it when the schema changes.
