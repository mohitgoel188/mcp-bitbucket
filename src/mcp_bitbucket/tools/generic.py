"""The generic escape hatch: endpoint discovery plus a raw API proxy."""

import json
import re
from typing import Annotated, Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .. import annotations, config, endpoint_index
from ..http_client import JSON_HEADERS, api_url, format_error, request
from ..http_client import paginate as merge_pages

# Deleting either of these destroys a whole container of work: a repository, or
# a project and every repository inside it. bb_delete_repository is withheld
# from the schema without BITBUCKET_ALLOW_DESTRUCTIVE, so letting the generic
# proxy reach the same endpoints would make that gate decorative -- the model
# would simply route around it. Matched after normalisation, so the "2.0/" and
# absolute-URL spellings are covered too.
_CONTAINER_DELETIONS = (
    re.compile(r"^/repositories/[^/]+/[^/]+/?$"),
    re.compile(r"^/workspaces/[^/]+/projects/[^/]+/?$"),
)


def normalize_api_path(path: str) -> str:
    """Turn any accepted path spelling into one rooted at the 2.0 API base.

    Accepts a bare path, a leading-slash path, a "2.0/"-prefixed path, or a full
    api.bitbucket.org URL, so the caller does not have to guess the convention.
    """
    candidate = (path or "").strip()
    if not candidate:
        raise ToolError(
            "path is required, e.g. /repositories/{workspace}/{repo_slug}/pullrequests"
        )

    if candidate.startswith(("http://", "https://")):
        if not candidate.startswith(config.API_BASE):
            raise ToolError(
                f"absolute URLs must start with {config.API_BASE}; got {candidate}"
            )
        candidate = candidate[len(config.API_BASE) :]

    candidate = candidate.lstrip("/")
    for prefix in ("2.0/", "api/2.0/"):
        candidate = candidate.removeprefix(prefix)

    # A relative segment could otherwise resolve, server-side, to a path the
    # guard rails below have already decided to refuse.
    if any(segment in {"..", "."} for segment in candidate.split("/")):
        raise ToolError(f"path must not contain relative segments: /{candidate}")

    if "{" in candidate or "}" in candidate:
        raise ToolError(
            f"path still contains an unsubstituted placeholder: /{candidate} - replace "
            "{workspace}, {repo_slug}, {pull_request_id} etc. with real values"
        )

    return "/" + candidate


def register(server) -> None:
    """Register the discovery and proxy tools."""

    @server.tool(
        annotations=annotations.READ_ONLY_LOCAL,
        description=(
            "Search the full Bitbucket Cloud REST API (all endpoints, generated from "
            "Atlassian's official OpenAPI spec) to find the right method, path and params "
            "for a task. Call this FIRST whenever no purpose-built bb_* tool covers what "
            "you need, then pass the result to bb_request. Every whitespace-separated term "
            "in `search` must match, so 'pull request comment' narrows better than 'comment'."
        ),
    )
    async def bb_list_endpoints(
        *,
        search: Annotated[
            str,
            Field(
                description="Terms matched against id, method, path, tag and summary"
            ),
        ] = "",
        tag: Annotated[
            str,
            Field(
                description=(
                    "Restrict to one API group, e.g. Pullrequests, Commits, Pipelines, "
                    "Refs, Source, Search, Workspaces"
                )
            ),
        ] = "",
        method: Annotated[
            str,
            Field(
                description="Restrict to one HTTP method",
                pattern="^(|GET|POST|PUT|DELETE|PATCH)$",
            ),
        ] = "",
        verbose: Annotated[
            bool, Field(description="Include params, body flag and docs link per match")
        ] = True,
        limit: Annotated[
            int, Field(description="Maximum matches to return", ge=1, le=100)
        ] = 25,
    ) -> str:
        index = endpoint_index.load()
        if index.get("missing"):
            raise ToolError(
                "Endpoint index not found. Generate it with:\n"
                "  uv run --directory <repo> python scripts/generate_endpoints.py"
            )

        matches = endpoint_index.search(index, search, tag, method)
        if not matches:
            available = ", ".join(index.get("tags", []))
            return (
                f"No endpoints matched (search={search!r}, tag={tag!r}, method={method!r}).\n"
                f"Available tags: {available}"
            )

        shown = matches[:limit]
        suffix = f" (showing {len(shown)})" if len(shown) < len(matches) else ""
        header = (
            f"{len(matches)} of {index.get('operation_count', 0)} "
            f"Bitbucket endpoints matched{suffix}:"
        )
        body = "\n".join(endpoint_index.summarize(op, verbose) for op in shown)
        return (
            f"{header}\n\n{body}\n"
            "\nCall these with bb_request(method=..., path=..., query=..., body=...)."
        )

    @server.tool(
        # Honest annotation for a tool whose blast radius is configuration
        # dependent: reads only with the guard on, and genuinely destructive
        # once it is allowed to reach repository deletion.
        annotations=(
            annotations.READ_ONLY
            if config.BB_REQUEST_READONLY
            else annotations.DESTRUCTIVE
            if config.ALLOW_DESTRUCTIVE
            else annotations.WRITE
        ),
        description=(
            "Call ANY Bitbucket Cloud 2.0 REST endpoint directly. This is the escape hatch "
            "for everything the purpose-built bb_* tools do not wrap - merges, commit "
            "comparison, branches and tags, pipelines, code search, webhooks, permissions. "
            "Use bb_list_endpoints first to get the exact path and params. Paths are "
            "relative to https://api.bitbucket.org/2.0 and must have real values substituted "
            "for {placeholders}. Set paginate=true on list endpoints to follow Bitbucket's "
            "`next` links and merge all pages."
        ),
    )
    async def bb_request(
        *,
        path: Annotated[
            str,
            Field(
                description=(
                    "API path relative to the 2.0 base, e.g. "
                    "/repositories/myworkspace/myrepo/pullrequests/123/comments"
                )
            ),
        ],
        method: Annotated[
            str,
            Field(description="HTTP method", pattern="^(GET|POST|PUT|DELETE|PATCH)$"),
        ] = "GET",
        query: Annotated[
            dict[str, Any] | None, Field(description="Query string parameters")
        ] = None,
        body: Annotated[
            dict[str, Any] | None,
            Field(description="JSON request body for POST/PUT/PATCH"),
        ] = None,
        paginate: Annotated[
            bool,
            Field(
                description="GET only: follow `next` links and merge every page's `values`"
            ),
        ] = False,
        max_pages: Annotated[
            int, Field(description="Page cap when paginating", ge=1, le=20)
        ] = 10,
        raw: Annotated[
            bool,
            Field(
                description=(
                    "Return the response body verbatim instead of pretty-printed JSON "
                    "(use for diff/patch/log endpoints)"
                )
            ),
        ] = False,
    ) -> str:
        verb = method.upper()
        if config.BB_REQUEST_READONLY and verb != "GET":
            raise ToolError(
                "bb_request is in read-only mode "
                f"(BITBUCKET_BB_REQUEST_READONLY=true); {verb} is refused."
            )

        api_path = normalize_api_path(path)

        if (
            verb == "DELETE"
            and not config.ALLOW_DESTRUCTIVE
            and any(pattern.match(api_path) for pattern in _CONTAINER_DELETIONS)
        ):
            raise ToolError(
                f"DELETE {api_path} would destroy a whole repository or project "
                "and is refused (BITBUCKET_ALLOW_DESTRUCTIVE is not enabled).\n"
                "Every other DELETE endpoint remains available."
            )

        response = request(
            verb,
            api_url(api_path),
            headers=JSON_HEADERS,
            json_data=body or None,
            params=query or None,
        )

        if not response.ok:
            raise ToolError(
                f"{verb} {api_path} failed: {response.status_code}\n{format_error(response.text)}"
            )

        if response.status_code == 204 or not response.content:
            return f"{verb} {api_path} succeeded ({response.status_code}, empty body)"

        if raw:
            return response.text

        if "json" not in response.headers.get("Content-Type", "").lower():
            # Diff, patch and log endpoints answer in text/plain.
            return response.text

        try:
            payload = (
                merge_pages(response, min(max_pages, config.MAX_PAGINATED_PAGES))
                if (paginate and verb == "GET")
                else response.json()
            )
        except ValueError:
            return response.text

        return f"{verb} {api_path} -> {response.status_code}\n{json.dumps(payload, indent=2)}"
