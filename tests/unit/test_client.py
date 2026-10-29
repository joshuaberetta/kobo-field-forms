import httpx
import pytest
import respx

from kff.errors import AuthError, KoboError, NotFoundError, UnavailableError
from kff.kobo.client import KoboClient, encode_query, path_template
from tests.conftest import BASE, TOKEN


@pytest.fixture
def client(settings):
    with KoboClient(settings, sleep=lambda _: None) as c:
        yield c


def test_url_building(client):
    assert client.url("/api/v2/assets") == f"{BASE}/api/v2/assets/?format=json"
    assert client.url("/api/v2/assets/a1/data.xml") == f"{BASE}/api/v2/assets/a1/data.xml"
    assert client.url("/api/v2/x/", {"format": "xls"}) == f"{BASE}/api/v2/x/?format=xls"
    assert client.url("https://other/x?y=1") == "https://other/x?y=1"


def test_encode_query_json_params():
    assert encode_query({"query": {"a": 1}, "limit": 5, "skip": None}) == (
        "query=%7B%22a%22%3A1%7D&limit=5"
    )


def test_path_template():
    assert path_template("/api/v2/assets/aB3dE5fG7h/data/12/") == "/api/v2/assets/{id}/data/{id}/"


@respx.mock
def test_token_sent_to_own_host_only(client):
    own = respx.get(f"{BASE}/api/v2/assets/a1/").mock(
        return_value=httpx.Response(302, headers={"Location": "https://s3.example.com/f"})
    )
    other = respx.get("https://s3.example.com/f").mock(return_value=httpx.Response(200, json={}))
    client.get("/api/v2/assets/a1/")
    assert own.calls.last.request.headers["Authorization"] == f"Token {TOKEN}"
    assert "Authorization" not in other.calls.last.request.headers


@respx.mock
def test_http_next_link_upgraded(client):
    # Routes match in order, so the more specific second page goes first.
    second = respx.get(f"{BASE}/api/v2/assets/", params={"p": "2"}).mock(
        return_value=httpx.Response(200, json={"results": [2], "next": None})
    )
    respx.get(f"{BASE}/api/v2/assets/").mock(
        return_value=httpx.Response(
            200, json={"results": [1], "next": "http://kf.kobotoolbox.org/api/v2/assets/?p=2"}
        )
    )
    assert list(client.paginate("/api/v2/assets/")) == [1, 2]
    assert second.calls.last.request.url.scheme == "https"


def test_refuses_plain_http_elsewhere(client):
    with pytest.raises(KoboError, match="non-https"):
        client.get("http://evil.example.com/x")


@respx.mock
def test_retries_get_on_503(client):
    route = respx.get(f"{BASE}/api/v2/assets/").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": True})]
    )
    assert client.get("/api/v2/assets/") == {"ok": True}
    assert route.call_count == 2


@respx.mock
def test_no_retry_post_on_503(client):
    route = respx.post(f"{BASE}/submission").mock(return_value=httpx.Response(503))
    with pytest.raises(UnavailableError):
        client.request("POST", f"{BASE}/submission")
    assert route.call_count == 1


@respx.mock
def test_error_mapping(client):
    respx.get(f"{BASE}/api/v2/me/").mock(return_value=httpx.Response(401))
    respx.get(f"{BASE}/api/v2/assets/nope/").mock(
        return_value=httpx.Response(404, json={"detail": "Not found."})
    )
    with pytest.raises(AuthError):
        client.get("/api/v2/me/")
    with pytest.raises(NotFoundError):
        client.get("/api/v2/assets/nope/")


@respx.mock
def test_token_redacted_from_errors(client):
    respx.get(f"{BASE}/api/v2/x/").mock(
        return_value=httpx.Response(400, json={"detail": f"bad token {TOKEN}"})
    )
    with pytest.raises(KoboError) as err:
        client.get("/api/v2/x/")
    assert TOKEN not in str(err.value)
    assert "[redacted]" in str(err.value)
