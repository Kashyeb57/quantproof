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
