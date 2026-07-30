"""Minimal JSON-over-HTTP helper built on urllib, so the app has no dependencies."""

import gzip
import json
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "todo-dashboard/1.0"


class ApiError(Exception):
    """An HTTP or transport failure, carrying enough context to show the user."""

    def __init__(self, message, status=None, headers=None, code=None):
        super().__init__(message)
        self.status = status
        # GitHub explains SAML-SSO problems in X-GitHub-SSO, so keep the headers.
        self.headers = headers
        # Machine-readable code (OAuth "error", Slack "error"), which callers need
        # to tell apart failures whose human text reads much the same.
        self.code = code


def _decode(response):
    raw = response.read()
    if response.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8", "replace"))


def _error_code(body):
    """The machine-readable code from an OAuth-style error body, if any."""
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return None
    if isinstance(payload, dict) and isinstance(payload.get("error"), str):
        return payload["error"]
    return None


def _summarize_error(body, status):
    """Pull a human message out of a Jira/GitHub error payload."""
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        text = (body or "").strip()
        return text[:200] or f"HTTP {status}"

    if isinstance(payload, dict):
        for key in ("message", "error_description", "error"):
            if isinstance(payload.get(key), str):
                return payload[key][:300]
        messages = payload.get("errorMessages")
        if isinstance(messages, list) and messages:
            return str(messages[0])[:300]
        errors = payload.get("errors")
        if isinstance(errors, dict) and errors:
            key, value = next(iter(errors.items()))
            return f"{key}: {value}"[:300]
    return f"HTTP {status}"


def request_json(url, *, method="GET", headers=None, body=None, timeout=20, with_headers=False):
    """Returns the decoded body, or `(body, headers)` when `with_headers` is set.

    The headers object is case-insensitive, as urllib returns it.
    """
    payload = None
    request_headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip",
    }
    request_headers.update(headers or {})

    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url, data=payload, headers=request_headers, method=method
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = _decode(response)
            return (payload, response.headers) if with_headers else payload
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            body_bytes = exc.read()
            # Error bodies honour our Accept-Encoding too.
            if exc.headers.get("Content-Encoding") == "gzip":
                body_bytes = gzip.decompress(body_bytes)
            raw = body_bytes.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - body is best-effort context only
            pass
        if exc.code == 401:
            raise ApiError("Unauthorized — check your token", 401, exc.headers) from exc
        if exc.code == 403:
            detail = _summarize_error(raw, 403)
            raise ApiError(f"Forbidden — {detail}", 403, exc.headers) from exc
        raise ApiError(
            _summarize_error(raw, exc.code), exc.code, exc.headers, _error_code(raw)
        ) from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"Network error: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise ApiError(f"Unexpected non-JSON response: {exc}") from exc
    except TimeoutError as exc:
        raise ApiError(f"Request timed out after {timeout}s") from exc


def request_form(url, fields, *, timeout=20):
    """POST application/x-www-form-urlencoded, decode a JSON reply.

    OAuth 2 token endpoints require form encoding, not JSON.
    """
    payload = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return _decode(response)
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - body is best-effort context only
            pass
        raise ApiError(
            _summarize_error(raw, exc.code), exc.code, exc.headers, _error_code(raw)
        ) from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"Network error: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise ApiError(f"Unexpected non-JSON response: {exc}") from exc
    except TimeoutError as exc:
        raise ApiError(f"Request timed out after {timeout}s") from exc
