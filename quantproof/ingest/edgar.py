"""Download SEC filings under SEC fair-access rules (AGENTS.md rule 9).

- Declared User-Agent from env SEC_USER_AGENT (a name and an email); never logged or shown.
- At most 5 requests/second (half the SEC limit of 10/s).
- Every download cached on disk: archive documents forever (they never change), the filing
  index and full-text search for a day (they change daily).
- Only SEC hosts: www.sec.gov, efts.sec.gov, data.sec.gov.
"""

import gzip
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from quantproof.ingest.html import read_filing

ALLOWED_HOSTS = frozenset({"www.sec.gov", "efts.sec.gov", "data.sec.gov"})
USER_AGENT_ENV = "SEC_USER_AGENT"
MIN_INTERVAL_S = 0.2
DEFAULT_CACHE = Path(".cache") / "edgar"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3
INDEX_MAX_AGE_S = 24 * 3600
TIMEOUT_S = 60


class EdgarError(RuntimeError):
    """Raised for SEC download or configuration problems."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def user_agent(env: Mapping[str, str] | None = None) -> str:
    """Return the declared SEC User-Agent string.

    Reads and strips SEC_USER_AGENT (env defaults to os.environ).
    Raises EdgarError if missing, blank, or without "@".
    The value itself is never included in the message.
    """
    env = os.environ if env is None else env
    value = env.get(USER_AGENT_ENV, "").strip()
    if "@" not in value:
        raise EdgarError("SEC fair access needs a name and email in SEC_USER_AGENT")
    return value


@dataclass(frozen=True)
class FilingRef:
    """Pointer to one SEC filing document."""

    cik: str
    accession: str
    filing_date: date
    form: str
    primary_document: str
    description: str = ""

    def __post_init__(self) -> None:
        # Normalise CIK to 10 zero-padded digits.
        padded = str(self.cik).strip().zfill(10)
        object.__setattr__(self, "cik", padded)

    @property
    def folder_url(self) -> str:
        """Archive folder URL for this accession."""
        accession_nodash = self.accession.replace("-", "")
        return (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{int(self.cik)}/{accession_nodash}/"
        )

    @property
    def url(self) -> str:
        """Full URL of the primary document."""
        return self.folder_url + self.primary_document


class EdgarClient:
    """SEC download client with cache and rate limiting."""

    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE,
        *,
        env: Mapping[str, str] | None = None,
        urlopen=urllib.request.urlopen,
        sleep=time.sleep,
        clock=time.monotonic,
        wall=time.time,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._user_agent = user_agent(env)
        self._urlopen = urlopen
        self._sleep = sleep
        self._clock = clock
        self._wall = wall
        self._last_start: float | None = None

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(cache_dir={self._cache_dir!r})"

    def cache_path(self, url: str) -> Path:
        """Map an https SEC URL to a cache file path.

        cache_dir / host / path segments. A trailing "/" gets
        "index.html". A query string becomes "__" + sha256(query)[:16]
        on the last segment, keeping its extension.
        """
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            raise ValueError(f"only https URLs are allowed: {url!r}")
        host = parsed.hostname or ""
        if not host:
            raise ValueError(f"URL has no host: {url!r}")
        if host not in ALLOWED_HOSTS:
            raise ValueError(f"host not allowed: {host!r}")
        segments = [seg for seg in parsed.path.split("/") if seg]
        if any(seg in (".", "..") for seg in segments):
            raise ValueError(f"URL path contains '.' or '..': {url!r}")
        if not segments or parsed.path.endswith("/"):
            segments.append("index.html")
        query = parsed.query
        if query:
            digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
            last = segments[-1]
            if "." in last:
                stem, ext = last.rsplit(".", 1)
                last = f"{stem}__{digest}.{ext}"
            else:
                last = f"{last}__{digest}"
            segments[-1] = last
        return self._cache_dir / host / Path(*segments)

    @staticmethod
    def _decompress(body: bytes, encoding: str | None) -> bytes:
        if not encoding:
            return body
        enc = encoding.strip().lower()
        if enc in ("", "identity", "none"):
            return body
        if enc == "gzip":
            return gzip.decompress(body)
        if enc == "deflate":
            try:
                return zlib.decompress(body)
            except zlib.error:
                return zlib.decompress(body, -15)
        return body

    def _write_cache(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / f"{path.name}.{os.getpid()}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)

    def get(self, url: str, *, max_age_s: float | None = None) -> bytes:
        """Download a URL, using the cache when fresh.

        If the cached file exists and max_age_s is None it is returned
        without any request (archive documents never change). If
        max_age_s is given, the cache is used when
        wall() - mtime <= max_age_s.
        """
        path = self.cache_path(url)
        if path.is_file():
            if max_age_s is None:
                return path.read_bytes()
            try:
                mtime = path.stat().st_mtime
            except OSError:
                pass
            else:
                if self._wall() - mtime <= max_age_s:
                    return path.read_bytes()
        headers = {"User-Agent": self._user_agent, "Accept-Encoding": "gzip, deflate"}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._wait_for_rate_limit()
            try:
                req = urllib.request.Request(url, headers=headers)
                with self._urlopen(req, timeout=TIMEOUT_S) as resp:
                    data = self._decompress(resp.read(), resp.headers.get("Content-Encoding"))
                self._write_cache(path, data)
                return data
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    raise EdgarError("SEC refused the request (403). Check SEC_USER_AGENT and "
                                     "the request rate.", status=403) from e
                if e.code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                    raise EdgarError(f"SEC request failed with status {e.code}.", status=e.code) from e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt == MAX_ATTEMPTS:
                    raise EdgarError(f"SEC request failed: {e.__class__.__name__}.") from e
            self._sleep(2**attempt)  # back off before retrying: 2 s, then 4 s
        raise AssertionError("unreachable")  # every path above returns or raises

    def _wait_for_rate_limit(self) -> None:
        """Keep at least MIN_INTERVAL_S between the starts of consecutive requests."""
        if self._last_start is not None:
            remaining = MIN_INTERVAL_S - (self._clock() - self._last_start)
            if remaining > 0:
                self._sleep(remaining)
        self._last_start = self._clock()

    def get_json(self, url: str, *, max_age_s: float | None = None) -> dict:
        """GET a URL and parse it as JSON."""
        raw = self.get(url, max_age_s=max_age_s)
        return json.loads(raw.decode("utf-8"))

    def recent_filings(
        self, cik: str | int, forms: tuple[str, ...] = ("424B2",)
    ) -> list[FilingRef]:
        """List recent filings for a CIK, filtered by form.

        Uses https://data.sec.gov/submissions/CIK{cik:010}.json,
        cached for INDEX_MAX_AGE_S. Returns most-recent-first.
        """
        cik_int = int(str(cik).strip())
        padded = f"{cik_int:010d}"
        url = f"https://data.sec.gov/submissions/CIK{padded}.json"
        data = self.get_json(url, max_age_s=INDEX_MAX_AGE_S)
        cik_json = str(data.get("cik", padded)).zfill(10)
        recent = data["filings"]["recent"]  # parallel arrays, most recent first
        refs: list[FilingRef] = []
        for accession, filed, form, doc, desc in zip(
            recent["accessionNumber"], recent["filingDate"], recent["form"],
            recent["primaryDocument"], recent["primaryDocDescription"],
        ):
            if form in forms:
                refs.append(FilingRef(cik=cik_json, accession=accession,
                                      filing_date=date.fromisoformat(filed), form=form,
                                      primary_document=doc, description=desc))
        return refs

    def search(
        self, phrase: str, *, start: date, end: date, forms: str = "424B2"
    ) -> list[FilingRef]:
        """Full-text search for an exact phrase via EFTS search-index."""
        params = {
            "q": f'"{phrase}"',
            "forms": forms,
            "startdt": start.isoformat(),
            "enddt": end.isoformat(),
        }
        query = urllib.parse.urlencode(params)
        url = "https://efts.sec.gov/LATEST/search-index?" + query
        data = self.get_json(url, max_age_s=INDEX_MAX_AGE_S)
        hits = data.get("hits", {}).get("hits", [])
        refs: list[FilingRef] = []
        for hit in hits:
            hit_id = hit["_id"]
            accession, filename = hit_id.split(":", 1)
            source = hit["_source"]
            ciks = source["ciks"]
            cik = str(ciks[0]).zfill(10)
            filing_date = date.fromisoformat(source["file_date"])
            form = source["form"]
            description = source.get("file_description", "")
            refs.append(
                FilingRef(
                    cik=cik,
                    accession=accession,
                    filing_date=filing_date,
                    form=form,
                    primary_document=filename,
                    description=description,
                )
            )
        return refs

    def fetch_filing(
        self, ref: FilingRef, *, images: bool = True
    ) -> tuple[bytes, list[Path]]:
        """Download a filing document and optionally its local images.

        Images are the distinct local srcs from read_filing in document
        order: relative only (no scheme, no leading "/", no "..").
        Each image is downloaded from ref.folder_url + src.
        Returns (raw_bytes, image_cache_paths).
        """
        raw = self.get(ref.url)
        if not images:
            return raw, []
        filing = read_filing(raw)
        seen: set[str] = set()
        paths: list[Path] = []
        for img in filing.images:
            src = img.src.strip()
            # Only files in the filing's own folder: no scheme (http:, data:), no absolute path,
            # no "..", and each distinct file once.
            if (not src or src.startswith("/") or ":" in src
                    or ".." in src.replace("\\", "/").split("/") or src in seen):
                continue
            seen.add(src)
            img_url = ref.folder_url + src
            self.get(img_url)
            paths.append(self.cache_path(img_url))
        return raw, paths
