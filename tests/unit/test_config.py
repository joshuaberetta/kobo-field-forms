import pytest

from kff.config import Settings, normalize_base_url
from kff.errors import ConfigError


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("global", "https://kf.kobotoolbox.org"),
        ("EU", "https://eu.kobotoolbox.org"),
        ("https://Kobo.Example.org/", "https://kobo.example.org"),
        ("https://kobo.example.org:8443", "https://kobo.example.org:8443"),
    ],
)
def test_normalize_base_url(value, expected):
    assert normalize_base_url(value) == expected


def test_http_needs_opt_in():
    with pytest.raises(ConfigError, match="KOBO_ALLOW_HTTP"):
        normalize_base_url("http://kf.kobo.local")
    assert normalize_base_url("http://kf.kobo.local", allow_http=True) == "http://kf.kobo.local"


@pytest.mark.parametrize("value", ["ftp://x.org", "kf.kobotoolbox.org", "https://x.org/api/v2"])
def test_bad_base_urls(value):
    with pytest.raises(ConfigError):
        normalize_base_url(value)


def test_from_env(tmp_path):
    env = {"KOBO_TOKEN": "secret-token-value", "KOBO_BASE_URL": "eu", "KFF_HOME": str(tmp_path)}
    s = Settings.from_env(env)
    assert (s.base_url, s.server, s.home) == (
        "https://eu.kobotoolbox.org",
        "eu.kobotoolbox.org",
        tmp_path,
    )
    assert "secret-token-value" not in repr(s)


def test_missing_token():
    with pytest.raises(ConfigError, match="KOBO_TOKEN"):
        Settings.from_env({})
    assert Settings.from_env({}, need_token=False).base_url == "https://kf.kobotoolbox.org"
