# Deploying the QuantProof app

The app entry point is `app.py` at the repository root (Streamlit).
Demo mode needs no keys and spends nothing. Live mode additionally
needs the secrets below. Nothing in this guide deploys anything by
itself; deployment happens only when you follow the website steps.

## Streamlit Community Cloud (primary)

The repository is already prepared:

- `requirements.txt` at the root: numpy, scipy, streamlit. The
  `quantproof` package is imported from the repository root, which
  `app.py` puts on `sys.path`, so no package install step is needed.
- `.python-version` pins Python 3.11 (the package requires 3.11+;
  tests also run on 3.13 in CI).

Steps in the Streamlit Community Cloud website:

1. Sign in at https://share.streamlit.io with the GitHub account that
   owns `Kashyeb57/quantproof`.
2. Click **New app**, then **Deploy a public app from GitHub**.
3. Repository: `Kashyeb57/quantproof`. Branch: `main`.
   Main file path: `app.py`.
4. Open **Advanced settings** and set Python version to **3.11**.
5. In the same dialog, under **Secrets**, paste the secrets in TOML
   format (real values, replacing the placeholders):

   ```toml
   MODEL_API_KEY = "your Meta Model API key"
   SEC_USER_AGENT = "Your Name your-email@example.com"
   QP_DAILY_CAP_USD = "5"
   QP_TOTAL_CAP_USD = "20"
   ```

   `MODEL_API_KEY` is the Meta Model API key for Muse Spark 1.3 live
   extraction. `SEC_USER_AGENT` is the name-and-email User-Agent SEC
   fair access requires. `QP_DAILY_CAP_USD` and `QP_TOTAL_CAP_USD` are optional; the daily
   spend cap defaults to $5 when unset. Never paste a key anywhere
   else, and never commit `.streamlit/secrets.toml` (it is gitignored;
   `.streamlit/secrets.toml.example` shows the format).
6. Click **Deploy**. First build installs `requirements.txt` and takes
   a few minutes. Later pushes to `main` redeploy automatically.
7. Open the app URL on a phone and a laptop. Check demo mode first
   (pick the 38582 note, confirm terms, verdict renders), then one
   live extraction. Watch the cost guard: at most 3 live extractions
   per browser session, and live mode switches off when the daily cap
   is reached.
8. Put the live URL at the top of the README and in the submission
   description (Day 8).

The app allows at most 3 live extractions per browser session. Its daily
and total limits compare an estimated allowance with the combined Meta and
Baseten local logs before a run. Failed requests are recorded at an estimated
cost, and runs in the same app process are serialized. The limits are not
billing guarantees: token estimates can be inaccurate, separate app
instances do not share a lock, and local logs can reset on restart or
redeployment. Check both providers' billing and use provider-side controls
where available.

If live mode says it is off, today's logged spend has reached the cap;
raise `QP_DAILY_CAP_USD` in the app secrets only after checking the
spend is expected. If EDGAR downloads fail, `SEC_USER_AGENT` is
missing or has no email address in it.

## Hugging Face Spaces (alternative)

1. At https://huggingface.co/spaces click **Create new Space**.
   SDK: **Streamlit**. Hardware: CPU basic (free) is enough; the app
   is deliberately sized to finish in a few seconds.
2. Push this repository to the Space (add the Space as a git remote
   and push `main`), or recreate it with `app.py` as the app file.
   `requirements.txt` and `.python-version` are used the same way.
3. In the Space **Settings → Variables and secrets**, add the same
   three values as secrets: `MODEL_API_KEY`, `SEC_USER_AGENT`, and
   optionally `QP_DAILY_CAP_USD`. The app reads them from the
   environment there, so no code change is needed.
4. The Space rebuilds on each push. Use its direct URL
   (`https://<space-name>.hf.space`) for the demo link.

QuantProof is risk analysis under stated assumptions, not investment
advice, on either host.
