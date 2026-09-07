"""Issue-tracker tools."""

from typing import Annotated

from pydantic import Field

from .. import annotations, config
from ..http_client import JSON_HEADERS, api_url, request, require_ok

Workspace = Annotated[str, Field(description="Bitbucket workspace")]
RepoSlug = Annotated[
    str,
    Field(
        description="Repository slug/name; defaults to the repo detected from the current directory's git remote"
    ),
]


def register(server) -> None:
    """Register issue-area tools."""

    @server.tool(
        annotations=annotations.WRITE,
        description="Create an issue in a Bitbucket repository",
    )
    async def bb_create_issue(
        *,
        repo_slug: RepoSlug = config.DEFAULT_REPO_SLUG,
        title: Annotated[str, Field(description="Issue title")],
        content: Annotated[str, Field(description="Issue body (markdown)")] = "",
        kind: Annotated[
            str,
            Field(
                description="Issue kind", pattern="^(bug|enhancement|proposal|task)$"
            ),
        ] = "task",
        priority: Annotated[
            str,
            Field(
                description="Issue priority",
                pattern="^(trivial|minor|major|critical|blocker)$",
            ),
        ] = "minor",
        workspace: Workspace = config.DEFAULT_WORKSPACE,
    ) -> str:
        response = request(
            "POST",
            api_url(f"/repositories/{workspace}/{repo_slug}/issues"),
            headers=JSON_HEADERS,
            json_data={
                "title": title,
                "content": {"raw": content},
                "kind": kind,
                "priority": priority,
            },
        )
        require_ok(response, "Create issue", expected=(200, 201))
        data = response.json()
        issue_url = data.get("links", {}).get("html", {}).get("href", "")
        return f"Issue created successfully\nID: {data.get('id')}\nURL: {issue_url}"

    @server.tool(
        annotations=annotations.DESTRUCTIVE,
        description="Delete an issue from a Bitbucket repository",
    )
    async def bb_delete_issue(
        *,
        repo_slug: RepoSlug = config.DEFAULT_REPO_SLUG,
        issue_id: Annotated[int, Field(description="Numeric issue ID")],
        workspace: Workspace = config.DEFAULT_WORKSPACE,
    ) -> str:
        response = request(
            "DELETE",
            api_url(f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}"),
            headers=JSON_HEADERS,
        )
        require_ok(response, f"Delete issue {issue_id}", expected=(204,))
        return f"Issue {issue_id} deleted successfully"
