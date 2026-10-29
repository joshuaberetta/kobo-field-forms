"""Settings from the environment. Nothing here is ever written to disk."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from kff.errors import ConfigError

SERVER_SHORTCUTS = {
    "global": "https://kf.kobotoolbox.org",
    "eu": "https://eu.kobotoolbox.org",
}


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def normalize_base_url(value: str, *, allow_http: bool = False) -> str:
    """`global`, `eu` or a server URL → `scheme://host[:port]` with no path."""
    value = value.strip()
    url = SERVER_SHORTCUTS.get(value.lower(), value)
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"} or not parts.hostname:
        raise ConfigError(f"KOBO_BASE_URL must be global, eu or an http(s) URL, not {value!r}.")
    if parts.scheme == "http" and not allow_http:
        raise ConfigError(
            "KOBO_BASE_URL is plain http, which would send the token unencrypted. "
            "Set KOBO_ALLOW_HTTP=true if this is a closed network (e.g. kobo-in-a-box)."
        )
    if parts.path.strip("/") or parts.query or parts.fragment:
        raise ConfigError("KOBO_BASE_URL must be just the server, e.g. https://kf.kobotoolbox.org")
    return f"{parts.scheme}://{parts.netloc.lower()}"


@dataclass(frozen=True)
class Settings:
    base_url: str
    token: str = field(repr=False)
    home: Path
    timeout: float = 30.0

    @property
    def server(self) -> str:
        """The server's host[:port], as used in the store and the QR payload."""
        return urlsplit(self.base_url).netloc

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, *, need_token: bool = True) -> "Settings":
        env = dict(os.environ if env is None else env)
        token = env.get("KOBO_TOKEN", "").strip()
        if need_token and not token:
            raise ConfigError("KOBO_TOKEN is not set. Get one at <server>/token/ while signed in.")
        try:
            timeout = float(env.get("KOBO_TIMEOUT") or 30)
        except ValueError:
            raise ConfigError("KOBO_TIMEOUT must be a number of seconds.") from None
        return cls(
            base_url=normalize_base_url(
                env.get("KOBO_BASE_URL") or "global", allow_http=_flag(env.get("KOBO_ALLOW_HTTP"))
            ),
            token=token,
            home=Path(env.get("KFF_HOME") or "data"),
            timeout=timeout,
        )
