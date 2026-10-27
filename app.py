"""Streamlit entry point for the QuantProof app (Stage A demo mode).

Run from the repository root:

    streamlit run app.py

The app itself lives in ``quantproof/app/main.py``; all logic is in
``quantproof/app/demo.py`` and ``quantproof/report/``.  Stage A makes no
API calls and reads no keys.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.app.main import run  # noqa: E402

run()
