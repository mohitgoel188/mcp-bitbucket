"""HTTP plumbing shared by every Bitbucket tool: auth, logging, error shaping."""

import json
import sys
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from mcp.server.mcpserver.exceptions import ToolError
from requests.auth import HTTPBasicAuth

from . import config

JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


def auth() -> HTTPBasicAuth | None:
    """Build the basic-auth credential, or None when a bearer token is in use."""
    if config.USING_TOKEN_AUTH:
        return None
    return HTTPBasicAuth(config.BITBUCKET_USERNAME, config.BITBUCKET_APP_PASSWORD)


def build_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """Attach the Authorization header when authenticating with a token.

    Injecting here rather than at each call site is deliberate: a caller that
    forgets to pass headers would otherwise send an unauthenticated request and
    get an opaque 401. Every request routes through this function.
    """
    final = dict(headers or {})
    if config.USING_TOKEN_AUTH:
        final["Authorization"] = f"Bearer {config.BITBUCKET_TOKEN}"
    return final


def log_request_as_curl(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    data: dict[str, Any] | None = None,
    json_data: Any = None,
    files: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    credentials: HTTPBasicAuth | None = None,
) -> None:
    """Append the request to the log file as a runnable curl command."""
    if not config.ENABLE_REQUEST_LOGGING:
        return

    try:
        curl_parts = ["curl", f"-X {method.upper()}"]

        full_url = f"{url}?{urllib.parse.urlencode(params)}" if params else url
        curl_parts.append(f"'{full_url}'")

        for key, value in (headers or {}).items():
            # Never write a live credential into the log file.
            shown = "Bearer [REDACTED]" if key.lower() == "authorization" else value
            curl_parts.append(f"-H '{key}: {shown}'")

        if credentials is not None:
            # The username is redacted too -- it identifies a real account, and
            # the log is the artifact most likely to be pasted into a bug report.
            curl_parts.append("-u '[REDACTED]:[REDACTED]'")

        if json_data:
            curl_parts.append("-H 'Content-Type: application/json'")
            curl_parts.append(f"--data '{json.dumps(json_data)}'")
        elif data:
            for key, value in data.items():
                curl_parts.append(f"-F '{key}={value}'")

        for file_path, file_info in (files or {}).items():
            marker = "@<file_content>" if file_info[1] else ""
            curl_parts.append(f"-F '{file_path}={marker}'")

        timestamp = datetime.now(UTC).astimezone().isoformat()
        entry = f"\n[{timestamp}]\n{' \\\n  '.join(curl_parts)}\n{'-' * 80}\n"
        with open(Path(config.REQUEST_LOG_FILE), "a", encoding="utf-8") as handle:
            handle.write(entry)
    except (OSError, TypeError, ValueError) as exc:
        # Request logging is diagnostic only, so it must never break a call --
        # but it must not vanish either. stderr, because stdout is the MCP
        # stdio transport and any stray write there corrupts the protocol.
        print(f"bitbucket request logging failed: {exc}", file=sys.stderr)


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: dict[str, Any] | None = None,
    json_data: Any = None,
    files: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> requests.Response:
    """Issue an authenticated Bitbucket request, logging it as curl first."""
    credentials = auth()
    headers = build_headers(headers)
    log_request_as_curl(
        method, url, headers, data, json_data, files, params, credentials
    )

    verb = method.upper()
    if verb == "GET":
        return requests.get(
            url, auth=credentials, headers=headers, params=params, timeout=120
        )
    if verb == "POST":
        return requests.post(
            url,
            auth=credentials,
            headers=headers,
            data=data,
            json=json_data,
            files=files,
            params=params,
            timeout=120,
        )
    if verb == "PUT":
        return requests.put(
            url,
            auth=credentials,
            headers=headers,
            json=json_data,
            params=params,
            timeout=120,
        )
    if verb == "PATCH":
        return requests.patch(
            url,
            auth=credentials,
            headers=headers,
            json=json_data,
            params=params,
            timeout=120,
        )
    if verb == "DELETE":
        return requests.delete(
            url,
            auth=credentials,
            headers=headers,
            json=json_data,
            params=params,
            timeout=120,
        )
    raise ToolError(f"Unsupported HTTP method: {method}")


def api_url(path: str) -> str:
    """Join a 2.0-relative path onto the API base.

    An empty path segment means a caller relied on an auto-detected repo slug or
    workspace that could not be resolved. Catching it here -- the single place
    every URL is built -- turns a confusing 404 into an actionable message.
    """
    joined = f"{config.API_BASE}/{path.lstrip('/')}"
    if "//" in joined[len("https://") :]:
        if config.DEFAULT_WORKSPACE:
            remedy = (
                "so pass repo_slug explicitly (workspace defaults to "
                f"{config.DEFAULT_WORKSPACE!r})."
            )
        else:
            remedy = (
                "so pass workspace and repo_slug explicitly, or set "
                "BITBUCKET_WORKSPACE. Auto-detection only works when the client "
                "was started inside a Bitbucket checkout."
            )
        raise ToolError(
            f"Incomplete Bitbucket path: {path}\n"
            "A workspace or repo_slug could not be resolved from the current "
            f"directory's git remote, {remedy}"
        )
    return joined


def format_error(response_text: str) -> str:
    """Turn a Bitbucket error body into the most useful message available."""
    try:
        error_data = json.loads(response_text)
        if "error" in error_data:
            detail = error_data["error"].get("detail")
            detail = detail if isinstance(detail, dict) else {}
            required = detail.get("required", [])
            granted = detail.get("granted", [])

            # Only scope errors carry required/granted. Without them this is an
            # ordinary 400/404/409, so return its own message instead of
            # misreporting it as a permissions problem.
            if not required and not granted:
                return error_data["error"].get("message") or response_text

            remedy = (
                [
                    "1. Create a new Bitbucket API token with the required scopes",
                    "2. Update your BITBUCKET_TOKEN environment variable",
                ]
                if config.USING_TOKEN_AUTH
                else [
                    "1. Go to Bitbucket Settings > App passwords",
                    "2. Create a new app password with the required permissions",
                    "3. Update your BITBUCKET_APP_PASSWORD environment variable",
                ]
            )
            return "\n".join(
                [
                    "Permission Error:",
                    f"Required permissions: {', '.join(required)}",
                    f"Granted permissions: {', '.join(granted)}",
                    f"\nAuthenticating with: {config.AUTH_MODE}",
                    "\nTo fix this:",
                    *remedy,
                ]
            )
    except (ValueError, TypeError, AttributeError, KeyError):
        return response_text
    return response_text


def require_ok(
    response: requests.Response, what: str, expected: tuple[int, ...] = ()
) -> None:
    """Raise a readable ToolError unless the response carries a success status.

    ToolError (rather than a bare exception) is what makes the message reach the
    model as an error result instead of an opaque "Error executing tool".
    """
    ok = response.status_code in expected if expected else response.ok
    if not ok:
        raise ToolError(
            f"{what} failed: {response.status_code}\n{format_error(response.text)}"
        )


def paginate(response: requests.Response, max_pages: int) -> dict[str, Any]:
    """Follow Bitbucket's `next` links and merge every page's `values`.

    Bitbucket paginates with an absolute `next` URL rather than an offset, so the
    links are followed as given instead of being reconstructed.
    """
    payload = response.json()
    values = list(payload.get("values", []))
    pages = 1
    next_url = payload.get("next")

    while next_url and pages < max_pages:
        page = request("GET", next_url, headers=JSON_HEADERS)
        if not page.ok:
            return {
                "values": values,
                "size": len(values),
                "pages_fetched": pages,
                "truncated": True,
                "pagination_error": (
                    f"page {pages + 1} failed: {page.status_code} {page.text[:200]}"
                ),
            }
        payload = page.json()
        values.extend(payload.get("values", []))
        pages += 1
        next_url = payload.get("next")

    return {
        "values": values,
        "size": len(values),
        "pages_fetched": pages,
        "truncated": bool(next_url),
        **({"next": next_url} if next_url else {}),
    }


def fetch_all(
    path: str, params: dict[str, Any] | None = None, max_pages: int = 5
) -> list[Any]:
    """GET a paginated collection and return every merged value."""
    response = request("GET", api_url(path), headers=JSON_HEADERS, params=params)
    require_ok(response, f"GET /{path.lstrip('/')}")
    return paginate(response, max_pages)["values"]
