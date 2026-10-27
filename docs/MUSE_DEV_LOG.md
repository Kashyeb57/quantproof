# Muse development log

Every Meta Model API call made while **building** QuantProof (not calls made by the product).
It is the evidence for the submission answer "use of Meta products to build the project".

- Rows are appended by `scripts/muse_draft.py`; Muse Code side tasks on Joyeb's PC are added by hand.
- Cost is estimated from returned token counts and `quantproof/agent/costs.py` prices.
- Budget: up to **$80** for development, keep at least $50 for the product and demo.
  `python scripts/muse_draft.py --total` prints the running total.
- "Review" says what happened to the draft after Claude Code reviewed and tested it.

| When (UTC) | Model | Task | Files sent | Files returned | In (cached) | Out (reasoning) | Est. cost | Review |
|---|---|---|---|---|---|---|---|---|
| 2026-10-27 | muse-spark-1.3 | Day 1 go/no-go: extract the JPM 424B2 term sheet with quotes | filing text + 3 payoff images | extract.json | 10,667 (0) | 8,673 (0) | $0.0502 | GO: 0 wrong fields, 93/97 quotes verbatim |
| 2026-10-27 12:55 | muse-spark-1.3 | Write two files: `quantproof/ingest/html.py` and `tests/ingest/test_html.py`. Standard library only (codecs, re, unicodedata, html.parser, dataclasses). No new  | quantproof/ingest/__init__.py, tests/agent/test_client.py | quantproof/ingest/html.py, tests/ingest/test_html.py | 3,380 (3,313) | 21,248 (16,592) | $0.0909 | Accepted after fixes: added the missing handle_data (no text was kept), U+2028 -> U+200B, HTML whitespace collapse, layout-table rule; rewrote 1 wrong test (scripts do not nest), added 6. Two earlier attempts got 502 after ~30 s (non-streamed); the script now streams |
| 2026-10-27 12:58 | muse-spark-1.3 | Write two files: `quantproof/validation/quotes.py` and `tests/validation/test_quotes.py`. Pure Python standard library (re, unicodedata, dataclasses). No AI, no | quantproof/core/termsheet.py, quantproof/validation/__init__.py | quantproof/validation/quotes.py, tests/validation/test_quotes.py | 4,378 (0) | 8,517 (5,813) | $0.0417 | Accepted after fixes: word-boundary rule now lets a sentence-final period match and treats a period inside a number as part of the token; '....' counts as an ellipsis; added 3 tests. All 78 cited fields of the JPM fixture verify |
| 2026-10-27 13:08 | muse-spark-1.3 | Extend `quantproof/agent/client.py` and `tests/agent/test_client.py` (both given as context). Keep BASE_URL, API_KEY_ENV, auth_headers, request_headers and the  | quantproof/agent/client.py, tests/agent/test_client.py, quantproof/agent/costs.py | quantproof/agent/client.py, tests/agent/test_client.py | 4,197 (0) | 15,005 (9,442) | $0.0690 | Accepted after fixes: response closed with `with`; a malformed stream event now raises instead of being skipped; unknown model fails before the call; added 2 tests |
