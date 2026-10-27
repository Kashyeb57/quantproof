from quantproof.agent import client


def test_no_auth_header_when_variable_absent():
    # Cloud environment: the network secret adds the header, so we must not send one.
    assert client.auth_headers({}) == {}
    assert client.request_headers({}) == {"Content-Type": "application/json"}


def test_blank_variable_counts_as_absent():
    assert client.auth_headers({"MODEL_API_KEY": "   "}) == {}


def test_bearer_header_when_variable_set():
    headers = client.request_headers({"MODEL_API_KEY": "test-key-not-real"})
    assert headers["Authorization"] == "Bearer test-key-not-real"
    assert headers["Content-Type"] == "application/json"


def test_reads_os_environ_by_default(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    assert client.auth_headers() == {}
    monkeypatch.setenv("MODEL_API_KEY", "test-key-not-real")
    assert client.auth_headers() == {"Authorization": "Bearer test-key-not-real"}


def test_base_url():
    assert client.BASE_URL == "https://api.meta.ai/v1"
