"""KoboClient: httpx wrapper with auth, retries, pagination and a host guard.

A synchronous, cut-down port of kobo-mcp's `kobo/client.py` (no MCP auth context, no
address guard: the server is the one the operator configured).
"""

import email.utils
import json
import logging
import random
import re
import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any, Self
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

import httpx

from kff.config import Settings
from kff.errors import KoboError, UnavailableError, error_for_response

logger = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({429, 502, 503, 504})
RETRY_DELAYS = (0.5, 1.0, 2.0)
RETRY_AFTER_CAP = 10.0
MAX_REDIRECTS = 5
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})

# Last path segment with a format suffix (data.json, uid.xml): no trailing slash.
_SUFFIXED = re.compile(r"\.[a-z0-9]{2,5}$", re.IGNORECASE)
# Path segments that look like ids (uids, numeric ids) are templated out of logs.
_ID_SEGMENT = re.compile(r"^(?=.*\d)[A-Za-z0-9_-]{6,}$|^\d+$")


def path_template(path: str) -> str:
    return "/".join("{id}" if _ID_SEGMENT.match(seg) else seg for seg in path.split("/"))


def encode_query(params: Mapping[str, Any]) -> str:
    """URL-encode params; dicts and lists (Kobo's `query`, `fields`, `sort`) as JSON."""
    flat = {
        k: json.dumps(v, separators=(",", ":")) if isinstance(v, dict | list) else v
        for k, v in params.items()
        if v is not None
    }
    return urlencode(flat)


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        seconds = when.timestamp() - time.time()
    return min(max(seconds, 0.0), RETRY_AFTER_CAP)


def _backoff(attempt: int) -> float:
    base = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
    return base * random.uniform(0.8, 1.2)


class KoboClient:
    """Talks to one Kobo server with one token.

    The token is only attached to requests on the configured host, and only over that
    host's configured scheme. Redirects are followed manually so a hop to another host
    (e.g. S3) never carries it.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = settings.base_url
        self._token = settings.token
        self._netloc = urlsplit(settings.base_url).netloc
        self._https = settings.base_url.startswith("https://")
        self._sleep = sleep
        self._http = httpx.Client(
            transport=transport,
            follow_redirects=False,
            timeout=httpx.Timeout(settings.timeout),
            headers={"Accept": "application/json", "User-Agent": "kobo-field-forms"},
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # -- URLs ---------------------------------------------------------------

    def url(self, path: str, params: Mapping[str, Any] | None = None) -> str:
        """Build a request URL.

        Relative paths get the base URL, a trailing slash (unless suffixed like
        `data.xml`) and `format=json` (unless suffixed or given). Absolute URLs, e.g.
        pagination `next` links, are returned as-is.
        """
        if "://" in path:
            if params:
                raise ValueError("params can't be combined with an absolute URL")
            return path
        if not path.startswith("/"):
            path = "/" + path
        suffixed = bool(_SUFFIXED.search(path.rsplit("/", 1)[-1]))
        if not suffixed and not path.endswith("/"):
            path += "/"
        query = dict(params or {})
        if not suffixed:
            query.setdefault("format", "json")
        qs = encode_query(query)
        return f"{self.base_url}{path}" + (f"?{qs}" if qs else "")

    def _is_own_host(self, url: str) -> bool:
        return urlsplit(url).netloc.lower() == self._netloc

    def _prepare_url(self, url: str) -> str:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if self._is_own_host(url) and scheme == "http" and self._https:
            # DRF behind a proxy can emit http:// next links; never send the token over http.
            return urlunsplit(parts._replace(scheme="https"))
        if scheme == "https" or (scheme == "http" and not self._https and self._is_own_host(url)):
            return url
        raise KoboError("Refusing to fetch a non-https URL.")

    def _headers_for(self, url: str) -> dict[str, str]:
        return {"Authorization": f"Token {self._token}"} if self._is_own_host(url) else {}

    # -- requests -----------------------------------------------------------

    def _send(self, method: str, url: str, **kw: Any) -> httpx.Response:
        """One logical request, following redirects manually."""
        extra_headers = kw.pop("headers", None) or {}
        for _ in range(MAX_REDIRECTS + 1):
            url = self._prepare_url(url)
            started = time.monotonic()
            headers = {**extra_headers, **self._headers_for(url)}
            resp = self._http.request(method, url, headers=headers, **kw)
            logger.info(
                "kobo_request method=%s path=%s status=%d duration_ms=%d",
                method,
                path_template(resp.request.url.path),
                resp.status_code,
                (time.monotonic() - started) * 1000,
            )
            location = resp.headers.get("Location")
            if not (resp.is_redirect and location):
                return resp
            url = urljoin(url, location)
            if resp.status_code in (301, 302, 303) and method != "HEAD":
                method = "GET"
                kw = {k: v for k, v in kw.items() if k not in {"json", "files", "data"}}
        raise UnavailableError("KoboToolbox redirected too many times.")

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        idempotent: bool = False,
        **kw: Any,
    ) -> httpx.Response:
        """Send a request with retries; return the 2xx response or raise KoboError.

        5xx responses are retried for idempotent methods, or when the caller says the
        request is (`idempotent=True`). Extra keywords (`json`, `files`, `data`,
        `headers`) go to httpx.
        """
        method = method.upper()
        url = self.url(path, params)
        for attempt in range(len(RETRY_DELAYS) + 1):
            last = attempt == len(RETRY_DELAYS)
            try:
                resp = self._send(method, url, **kw)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # Nothing reached the server, so retrying is safe for any method.
                if last:
                    raise UnavailableError(f"Could not reach {self.base_url}.") from exc
                self._sleep(_backoff(attempt))
                continue
            except httpx.TimeoutException as exc:
                raise UnavailableError(f"{self.base_url} did not respond in time.") from exc
            except httpx.HTTPError as exc:
                raise UnavailableError(f"Network error talking to {self.base_url}.") from exc

            retryable = resp.status_code == 429 or (
                resp.status_code in RETRY_STATUSES and (method in IDEMPOTENT_METHODS or idempotent)
            )
            if retryable and not last:
                delay = _retry_after_seconds(resp)
                self._sleep(delay if delay is not None else _backoff(attempt))
                continue
            if resp.is_success:
                return resp
            raise self._redact(error_for_response(resp, server=self.base_url))
        raise AssertionError("unreachable")

    def _redact(self, err: KoboError) -> KoboError:
        # Kobo shouldn't echo the token, but make sure nothing we surface does.
        if self._token and self._token in str(err):
            return type(err)(str(err).replace(self._token, "[redacted]"))
        return err

    def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        resp = self.request("GET", path, params=params)
        try:
            return resp.json()
        except ValueError:
            raise KoboError("KoboToolbox returned a non-JSON response.") from None

    def post(self, path: str, json_body: Any = None) -> Any:
        resp = self.request("POST", path, json=json_body)
        return resp.json() if resp.content else None

    def patch(self, path: str, json_body: Any = None) -> Any:
        resp = self.request("PATCH", path, json=json_body)
        return resp.json() if resp.content else None

    def submit(self, xml: bytes, attachments: dict[str, tuple[bytes, str]]) -> httpx.Response:
        """POST an OpenRosa submission to `{server}/submission` (KB §5.6): 201 created, 202
        a duplicate instanceID. Each attachment is a part named like its file."""
        files = [("xml_submission_file", ("submission.xml", xml, "text/xml"))] + [
            (name, (name, data, mime)) for name, (data, mime) in attachments.items()
        ]
        # An absolute URL: OpenRosa's path takes no trailing slash or `format=json`. Safe to
        # retry: the instanceID makes a repeat a 202 duplicate, never a second record.
        return self.request("POST", f"{self.base_url}/submission", files=files, idempotent=True)

    def paginate(
        self, path: str, *, params: Mapping[str, Any] | None = None, page_size: int = 100
    ) -> Iterator[Any]:
        """Yield every item of a DRF `{count, next, results}` listing, following `next`."""
        next_url: str | None = self.url(path, {**(params or {}), "limit": page_size})
        while next_url:
            data = self.get(next_url)
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise KoboError("KoboToolbox returned an unexpected listing format.")
            yield from data["results"]
            next_url = data.get("next") or None
