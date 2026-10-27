"""Tests for quantproof.ingest.edgar (offline, deterministic)."""
import gzip
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import pytest

from quantproof.ingest.edgar import EdgarClient, EdgarError, FilingRef, MIN_INTERVAL_S, user_agent

UA = "Test Person test@example.com"
ENV = {"SEC_USER_AGENT": UA}
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"


class FakeResp:
    def __init__(self, body, encoding=None):
        self._body = body
        self.headers = {"Content-Encoding": encoding} if encoding else {}
        self.status = 200

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeNet:
    def __init__(self, func):
        self.func = func
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        return self.func(req)


def _client(tmp_path, func, sleeps=None, clock=None):
    net = FakeNet(func)
    kw = {"env": ENV, "urlopen": net}
    if sleeps is not None:
        kw["sleep"] = sleeps.append
    if clock is not None:
        kw["clock"] = clock
    return EdgarClient(cache_dir=tmp_path, **kw), net


def _http_error(url, code):
    return urllib.error.HTTPError(url, code, "msg", {}, None)


def test_user_agent_missing():
    with pytest.raises(EdgarError) as e:
        user_agent({})
    assert "@" not in str(e.value)  # value never echoed


def test_user_agent_blank():
    with pytest.raises(EdgarError) as e:
        user_agent({"SEC_USER_AGENT": "   "})
    assert "   " not in str(e.value)


def test_user_agent_no_at():
    with pytest.raises(EdgarError) as e:
        user_agent({"SEC_USER_AGENT": "No Email Here"})
    assert "No Email Here" not in str(e.value)


def test_repr_hides_user_agent(tmp_path):
    client, _ = _client(tmp_path, lambda req: FakeResp(b"x"))
    assert UA not in repr(client)


def test_sends_headers(tmp_path):
    client, net = _client(tmp_path, lambda req: FakeResp(b"x"))
    client.get("https://www.sec.gov/a.txt")
    req = net.requests[0]
    assert req.get_header("User-agent") == UA and req.get_header("Accept-encoding") == "gzip, deflate"


def test_gzip_decompressed(tmp_path):
    client, _ = _client(tmp_path, lambda req: FakeResp(gzip.compress(b"hello"), "gzip"))
    assert client.get("https://www.sec.gov/a.txt") == b"hello"


def test_http_scheme_rejected(tmp_path):
    client, net = _client(tmp_path, lambda req: FakeResp(b"x"))
    with pytest.raises(ValueError):
        client.get("http://www.sec.gov/a.txt")
    assert net.requests == []


def test_other_host_rejected(tmp_path):
    client, net = _client(tmp_path, lambda req: FakeResp(b"x"))
    with pytest.raises(ValueError):
        client.get("https://example.com/a.txt")
    assert net.requests == []


def test_cache_folder_index(tmp_path):
    client, _ = _client(tmp_path, lambda req: FakeResp(b"x"))
    p = client.cache_path("https://www.sec.gov/Archives/edgar/data/1665650/000121390026112627/")
    assert p == tmp_path / "www.sec.gov" / "Archives" / "edgar" / "data" / "1665650" / "000121390026112627" / "index.html"


def test_cache_query_keeps_extension(tmp_path):
    client, _ = _client(tmp_path, lambda req: FakeResp(b"x"))
    p = client.cache_path("https://www.sec.gov/Archives/edgar/data/1665650/doc.htm?x=1&y=2")
    assert p.suffix == ".htm" and "__" in p.stem


def test_cache_dotdot_rejected(tmp_path):
    client, _ = _client(tmp_path, lambda req: FakeResp(b"x"))
    with pytest.raises(ValueError):
        client.cache_path("https://www.sec.gov/a/../b")


def test_second_get_from_cache(tmp_path):
    client, net = _client(tmp_path, lambda req: FakeResp(b"hello"))
    first = client.get("https://www.sec.gov/a.txt")
    assert client.get("https://www.sec.gov/a.txt") == first and len(net.requests) == 1


def test_stale_refetched(tmp_path):
    client, net = _client(tmp_path, lambda req: FakeResp(b"d"))
    url = "https://www.sec.gov/a.txt"
    client.get(url, max_age_s=60)
    old = time.time() - 3600
    os.utime(client.cache_path(url), (old, old))
    client.get(url, max_age_s=60)
    assert len(net.requests) == 2


def test_rate_limit_sleeps(tmp_path):
    sleeps = []
    client, _ = _client(tmp_path, lambda req: FakeResp(b"x"), sleeps, lambda: 42.0)
    client.get("https://www.sec.gov/a.txt")
    client.get("https://www.sec.gov/b.txt")
    assert sleeps == [MIN_INTERVAL_S]


def test_retry_503_then_200(tmp_path):
    sleeps = []
    state = {"n": 0}

    def handler(req):
        if state["n"] == 0:
            state["n"] += 1
            raise _http_error(req.full_url, 503)
        return FakeResp(b"ok")

    client, net = _client(tmp_path, handler, sleeps, lambda: float(state["n"]))
    assert client.get("https://www.sec.gov/a.txt") == b"ok" and sleeps == [2] and len(net.requests) == 2


def test_403_no_retry(tmp_path):
    def handler(req):
        raise _http_error(req.full_url, 403)

    client, net = _client(tmp_path, handler)
    with pytest.raises(EdgarError) as e:
        client.get("https://www.sec.gov/a.txt")
    assert e.value.status == 403 and "SEC_USER_AGENT" in str(e.value) and len(net.requests) == 1


def test_404_status(tmp_path):
    def handler(req):
        raise _http_error(req.full_url, 404)

    client, _ = _client(tmp_path, handler)
    with pytest.raises(EdgarError) as e:
        client.get("https://www.sec.gov/a.txt")
    assert e.value.status == 404


def _submissions_handler(req):
    assert "submissions" in req.full_url
    return FakeResp((FIXTURES / "submissions_CIK0001665650.json").read_bytes())


def test_recent_filings_count(tmp_path):
    client, _ = _client(tmp_path, _submissions_handler)
    assert len(client.recent_filings(1665650)) == 4  # 424B2 only of 6 entries


def test_recent_filings_first_url(tmp_path):
    client, _ = _client(tmp_path, _submissions_handler)
    url = "https://www.sec.gov/Archives/edgar/data/1665650/000121390026112627/ea0309744-01_424b2.htm"
    assert client.recent_filings(1665650)[0].url == url


def _search_client(tmp_path):
    def handler(req):
        return FakeResp((FIXTURES / "efts_search_lesser_performing.json").read_bytes())

    return _client(tmp_path, handler)


def test_search_query(tmp_path):
    client, net = _search_client(tmp_path)
    client.search("Lesser Performing", start=date(2025, 1, 1), end=date(2026, 10, 27))
    q = urllib.parse.parse_qs(urllib.parse.urlparse(net.requests[0].full_url).query)
    assert q["q"] == ['"Lesser Performing"'] and q["forms"] == ["424B2"] and "startdt" in q and "enddt" in q


def test_search_first_hit(tmp_path):
    client, _ = _search_client(tmp_path)
    ref = client.search("Lesser Performing", start=date(2025, 1, 1), end=date(2026, 10, 27))[0]
    assert ref.accession == "0001213900-25-112248" and ref.cik == "0000019617"
    assert ref.url == "https://www.sec.gov/Archives/edgar/data/19617/000121390025112248/ea0266331-01_424b2.htm"


def test_fetch_filing_images_once(tmp_path):
    ref = FilingRef(cik="0001665650", accession="0001213900-26-112627",
                    filing_date=date(2026, 10, 27), form="424B2",
                    primary_document="ea0309744-01_424b2.htm")
    html = (b'<html><body><img src="image_001.jpg"><img src="image_001.jpg">'
            b'<img src="https://example.com/p.png"><img src="../x.jpg"></body></html>')

    def handler(req):
        if req.full_url == ref.url:
            return FakeResp(html)
        if req.full_url == ref.folder_url + "image_001.jpg":
            return FakeResp(b"IMG")
        raise _http_error(req.full_url, 404)

    client, net = _client(tmp_path, handler)
    raw, paths = client.fetch_filing(ref)
    got = [r.full_url for r in net.requests]
    assert got == [ref.url, ref.folder_url + "image_001.jpg"] and paths == [client.cache_path(ref.folder_url + "image_001.jpg")] and raw == html
