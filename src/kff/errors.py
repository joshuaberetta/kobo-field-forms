"""Errors shown to the user. Messages must never contain tokens or request headers."""

import html
import json
import re

import httpx

DETAIL_MAX_CHARS = 300


class KffError(Exception):
    """Base for every error the CLI reports. `str(err)` is the message shown."""


class ConfigError(KffError):
    """Missing or invalid settings."""


class KoboError(KffError):
    """A failed request to KoboToolbox."""


class AuthError(KoboError):
    """401: missing, invalid or expired token."""


class PermissionDeniedError(KoboError):
    """403."""


class NotFoundError(KoboError):
    """404."""


class BadRequestError(KoboError):
    """400, with Kobo's detail."""


class UnavailableError(KoboError):
    """429 / 5xx after retries, timeouts and connection failures."""


def _truncate(text: str) -> str:
    return text if len(text) <= DETAIL_MAX_CHARS else text[: DETAIL_MAX_CHARS - 1] + "…"


# OpenRosa (`/submission`) replies in XML: <OpenRosaResponse><message>...</message>.
_OPENROSA_MESSAGE = re.compile(r"<message[^>]*>(.*?)</message>", re.DOTALL)


def response_detail(resp: httpx.Response) -> str:
    """Kobo's error detail: DRF `detail` (or `error`), an OpenRosa `<message>`, else the body."""
    try:
        body = resp.json()
    except ValueError:
        text = resp.text.strip()
        m = _OPENROSA_MESSAGE.search(text)
        return _truncate(html.unescape(m.group(1).strip()) if m else text)
    for key in ("detail", "error"):
        if isinstance(body, dict) and isinstance(body.get(key), str):
            return _truncate(body[key])
    return _truncate(json.dumps(body, ensure_ascii=False))


def error_for_response(resp: httpx.Response, *, server: str) -> KoboError:
    status = resp.status_code
    detail = response_detail(resp)
    if status == 401:
        return AuthError(f"{server} rejected the token (401). Check KOBO_TOKEN and KOBO_BASE_URL.")
    if status == 403:
        return PermissionDeniedError(f"Permission denied by {server} (403): {detail}")
    if status == 404:
        return NotFoundError(f"Not found on {server} (404). Check the uid and that you can see it.")
    if status == 400:
        return BadRequestError(f"{server} refused the request (400): {detail}")
    if status == 429 or status >= 500:
        return UnavailableError(f"{server} is unavailable (HTTP {status}); retry later.")
    return KoboError(f"Unexpected HTTP {status} from {server}: {detail}")
