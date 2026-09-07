"""Curated pull-request review tools: listing, comments, approvals.

These wrap endpoints `bb_request` can also reach, but they exist because the
payload shapes are easy to get wrong (inline comments need a path *and* a line),
because the raw responses are far larger than a reviewer needs, and because the
approval verbs are visible to other people and deserve explicit arguments.
"""

from typing import Annotated, Any

from pydantic import Field

from .. import annotations, config
from ..http_client import JSON_HEADERS, api_url, paginate, request, require_ok

Workspace = Annotated[str, Field(description="Bitbucket workspace")]
RepoSlug = Annotated[
    str,
    Field(
        description="Repository slug/name; defaults to the repo detected from the current directory's git remote"
    ),
]
PrId = Annotated[int, Field(description="Pull request ID")]

# Only the fields a reviewer actually reads -- the full payload is ~2 KB per PR
# of links and rendered markup.
_PR_FIELDS = (
    "size,page,next,"
    "values.id,values.title,values.state,values.author.display_name,"
    "values.source.branch.name,values.destination.branch.name,"
    "values.created_on,values.updated_on,values.links.html.href"
)


def register(server) -> None:
    """Register curated PR review tools."""

    def pr_path(workspace: str, repo_slug: str, pr_id: int) -> str:
        return f"/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}"

    @server.tool(
        annotations=annotations.READ_ONLY,
        description=(
            "List pull requests in a repository, optionally filtered by state, source or "
            "destination branch, or author. Use the source_branch filter to resolve a "
            "branch name to its PR number when the user names a branch instead of a PR."
        ),
    )
    async def bb_list_pull_requests(
        *,
        repo_slug: RepoSlug = config.DEFAULT_REPO_SLUG,
        state: Annotated[
            str,
            Field(
                description="PR state to list",
                pattern="^(OPEN|MERGED|DECLINED|SUPERSEDED)$",
            ),
        ] = "OPEN",
        source_branch: Annotated[
            str, Field(description="Only PRs opened from this branch")
        ] = "",
        destination_branch: Annotated[
            str, Field(description="Only PRs targeting this branch")
        ] = "",
        author: Annotated[
            str,
            Field(description="Filter by author nickname or display name substring"),
        ] = "",
        limit: Annotated[
            int, Field(description="Maximum PRs to return", ge=1, le=100)
        ] = 25,
        workspace: Workspace = config.DEFAULT_WORKSPACE,
    ) -> str:
        clauses = [f'state="{state}"']
        if source_branch:
            clauses.append(f'source.branch.name="{source_branch}"')
        if destination_branch:
            clauses.append(f'destination.branch.name="{destination_branch}"')
        if author:
            clauses.append(f'author.nickname~"{author}"')

        response = request(
            "GET",
            api_url(f"/repositories/{workspace}/{repo_slug}/pullrequests"),
            headers=JSON_HEADERS,
            params={
                "q": " AND ".join(clauses),
                "pagelen": min(limit, 50),
                "fields": _PR_FIELDS,
                "sort": "-updated_on",
            },
        )
        require_ok(response, "List pull requests")

        values = response.json().get("values", [])[:limit]
        if not values:
            described = ", ".join(clauses)
            return f"No pull requests matched ({described}) in {workspace}/{repo_slug}."

        lines = [f"{len(values)} pull request(s) in {workspace}/{repo_slug}:", ""]
        for pr in values:
            src = pr.get("source", {}).get("branch", {}).get("name")
            dst = pr.get("destination", {}).get("branch", {}).get("name")
            lines.append(f"#{pr.get('id')}  [{pr.get('state')}]  {pr.get('title')}")
            lines.append(
                f"       {pr.get('author', {}).get('display_name', 'Unknown')}"
                f" | {src} → {dst} | updated {pr.get('updated_on')}"
            )
            lines.append(
                f"       {pr.get('links', {}).get('html', {}).get('href', '')}"
            )
        return "\n".join(lines)

    @server.tool(
        annotations=annotations.READ_ONLY,
        description=(
            "List comments on a pull request, showing which are inline (with file and "
            "line), which are replies, and which threads are resolved"
        ),
    )
    async def bb_pr_list_comments(
        *,
        repo_slug: RepoSlug = config.DEFAULT_REPO_SLUG,
        pr_id: PrId,
        include_resolved: Annotated[
            bool, Field(description="Include comments on resolved threads")
        ] = True,
        max_pages: Annotated[
            int, Field(description="Comment pages to merge", ge=1, le=20)
        ] = 5,
        workspace: Workspace = config.DEFAULT_WORKSPACE,
    ) -> str:
        response = request(
            "GET",
            api_url(f"{pr_path(workspace, repo_slug, pr_id)}/comments"),
            headers=JSON_HEADERS,
            params={"pagelen": 100},
        )
        require_ok(response, "List PR comments")

        comments: list[dict[str, Any]] = paginate(response, max_pages)["values"]
        rendered: list[str] = []
        skipped_resolved = 0

        for comment in comments:
            if comment.get("deleted"):
                continue
            resolved = bool(comment.get("resolution"))
            if resolved and not include_resolved:
                skipped_resolved += 1
                continue

            author = comment.get("user", {}).get("display_name", "Unknown")
            inline = comment.get("inline") or {}
            where = ""
            if inline:
                line = inline.get("to") or inline.get("from")
                where = (
                    f" @ {inline.get('path')}:{line}"
                    if line
                    else f" @ {inline.get('path')}"
                )
            markers = []
            if resolved:
                markers.append("resolved")
            if comment.get("parent"):
                markers.append(f"reply to {comment['parent'].get('id')}")
            suffix = f" ({', '.join(markers)})" if markers else ""

            body = (comment.get("content", {}).get("raw") or "").strip()
            rendered.append(
                f"[{comment.get('id')}] {author}{where}{suffix}\n    {body}"
            )

        if not rendered:
            return f"No comments on PR #{pr_id}."

        header = f"{len(rendered)} comment(s) on PR #{pr_id}"
        if skipped_resolved:
            header += f" ({skipped_resolved} resolved hidden)"
        return f"{header}:\n\n" + "\n\n".join(rendered)

    @server.tool(
        annotations=annotations.WRITE,
        description=(
            "Add a comment to a pull request. Pass file_path and line together to anchor "
            "it inline on that line of the diff; pass parent_id to reply within an "
            "existing thread; pass neither for a general PR comment."
        ),
    )
    async def bb_pr_comment(
        *,
        repo_slug: RepoSlug = config.DEFAULT_REPO_SLUG,
        pr_id: PrId,
        content: Annotated[str, Field(description="Comment body (markdown)")],
        file_path: Annotated[
            str, Field(description="File to anchor an inline comment to")
        ] = "",
        line: Annotated[
            int,
            Field(
                description="Line number in the new file for an inline comment", ge=1
            ),
        ] = 0,
        on_old_version: Annotated[
            bool,
            Field(description="Anchor the line to the old file instead of the new one"),
        ] = False,
        parent_id: Annotated[
            int, Field(description="Comment ID to reply to", ge=1)
        ] = 0,
        workspace: Workspace = config.DEFAULT_WORKSPACE,
    ) -> str:
        payload: dict[str, Any] = {"content": {"raw": content}}

        if file_path:
            inline: dict[str, Any] = {"path": file_path}
            if line:
                inline["from" if on_old_version else "to"] = line
            payload["inline"] = inline
        if parent_id:
            payload["parent"] = {"id": parent_id}

        response = request(
            "POST",
            api_url(f"{pr_path(workspace, repo_slug, pr_id)}/comments"),
            headers=JSON_HEADERS,
            json_data=payload,
        )
        require_ok(response, "Create PR comment", expected=(200, 201))

        data = response.json()
        anchor = (
            f" on {file_path}:{line}"
            if file_path and line
            else (f" on {file_path}" if file_path else "")
        )
        link = data.get("links", {}).get("html", {}).get("href", "")
        return f"Comment {data.get('id')} added to PR #{pr_id}{anchor}\n{link}"

    @server.tool(
        annotations=annotations.WRITE,
        description="Mark a pull request comment thread as resolved",
    )
    async def bb_pr_resolve_thread(
        *,
        repo_slug: RepoSlug = config.DEFAULT_REPO_SLUG,
        pr_id: PrId,
        comment_id: Annotated[
            int, Field(description="ID of the thread's root comment")
        ],
        workspace: Workspace = config.DEFAULT_WORKSPACE,
    ) -> str:
        response = request(
            "POST",
            api_url(
                f"{pr_path(workspace, repo_slug, pr_id)}/comments/{comment_id}/resolve"
            ),
            headers=JSON_HEADERS,
        )
        require_ok(
            response, f"Resolve comment thread {comment_id}", expected=(200, 201)
        )
        return f"Comment thread {comment_id} on PR #{pr_id} marked resolved"

    @server.tool(
        annotations=annotations.WRITE,
        description=(
            "Approve a pull request as the authenticated user. This is visible to the "
            "team - only call it when the user has asked for an approval."
        ),
    )
    async def bb_pr_approve(
        *,
        repo_slug: RepoSlug = config.DEFAULT_REPO_SLUG,
        pr_id: PrId,
        workspace: Workspace = config.DEFAULT_WORKSPACE,
    ) -> str:
        response = request(
            "POST",
            api_url(f"{pr_path(workspace, repo_slug, pr_id)}/approve"),
            headers=JSON_HEADERS,
        )
        require_ok(response, f"Approve PR {pr_id}", expected=(200, 201))
        return f"PR #{pr_id} approved"

    @server.tool(
        annotations=annotations.WRITE,
        description=(
            "Request changes on a pull request as the authenticated user. Visible to the "
            "team - only call it when the user has asked to request changes."
        ),
    )
    async def bb_pr_request_changes(
        *,
        repo_slug: RepoSlug = config.DEFAULT_REPO_SLUG,
        pr_id: PrId,
        workspace: Workspace = config.DEFAULT_WORKSPACE,
    ) -> str:
        response = request(
            "POST",
            api_url(f"{pr_path(workspace, repo_slug, pr_id)}/request-changes"),
            headers=JSON_HEADERS,
        )
        require_ok(response, f"Request changes on PR {pr_id}", expected=(200, 201))
        return f"Changes requested on PR #{pr_id}"
