"""Repository and branch tools."""

from typing import Annotated

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .. import annotations, config
from ..http_client import JSON_HEADERS, api_url, format_error, request, require_ok

Workspace = Annotated[
    str,
    Field(description="Bitbucket workspace; '~' targets your personal workspace"),
]
RepoSlug = Annotated[
    str,
    Field(
        description="Repository slug/name; defaults to the repo detected from the current directory's git remote"
    ),
]


def _resolve_workspace(workspace: str) -> str:
    """Expand '~' to the authenticated user's own workspace."""
    if workspace != "~":
        return workspace
    response = request("GET", api_url("/user"), headers=JSON_HEADERS)
    require_ok(response, "Get user info")
    return response.json().get("username")


def register(server) -> None:
    """Register repository-area tools."""

    @server.tool(
        annotations=annotations.WRITE,
        description="Create a new repository in Bitbucket",
    )
    async def bb_create_repository(
        *,
        name: Annotated[str, Field(description="Repository name")],
        description: Annotated[str, Field(description="Repository description")] = "",
        project_key: Annotated[
            str, Field(description="Project key; required for workspace repos")
        ] = "",
        is_private: Annotated[
            bool, Field(description="Keep the repository private")
        ] = True,
        has_issues: Annotated[
            bool, Field(description="Enable the issue tracker")
        ] = True,
        workspace: Workspace = config.DEFAULT_WORKSPACE,
    ) -> str:
        resolved = _resolve_workspace(workspace)
        payload = {
            "scm": "git",
            "name": name,
            "is_private": is_private,
            "description": description,
            "has_issues": has_issues,
        }
        if project_key:
            payload["project"] = {"key": project_key}

        response = request(
            "POST",
            api_url(f"/repositories/{resolved}/{name.lower()}"),
            headers=JSON_HEADERS,
            json_data=payload,
        )
        if response.status_code not in (200, 201):
            message = format_error(response.text)
            if resolved == config.DEFAULT_WORKSPACE and "permission" in message.lower():
                message += "\n\nTip: try workspace='~' to create it in your personal workspace."
            raise ToolError(
                f"Failed to create repository: {response.status_code}\n{message}"
            )

        repo_url = response.json().get("links", {}).get("html", {}).get("href", "")
        return f"Repository created successfully in workspace '{resolved}'\nURL: {repo_url}"

    # Repository deletion is irreversible and is not something an agent should
    # be able to reach for by default, so the tool is not registered at all
    # unless it is explicitly enabled. Withholding it from the schema is
    # stronger than refusing at call time: the model never sees the option.
    if config.ALLOW_DESTRUCTIVE:

        @server.tool(
            annotations=annotations.DESTRUCTIVE,
            description="Delete a repository from Bitbucket",
        )
        async def bb_delete_repository(
            *,
            repo_slug: RepoSlug = config.DEFAULT_REPO_SLUG,
            workspace: Workspace = config.DEFAULT_WORKSPACE,
        ) -> str:
            resolved = _resolve_workspace(workspace)
            response = request(
                "DELETE",
                api_url(f"/repositories/{resolved}/{repo_slug}"),
                headers=JSON_HEADERS,
            )
            if response.status_code != 204:
                message = format_error(response.text)
                if (
                    resolved == config.DEFAULT_WORKSPACE
                    and "permission" in message.lower()
                ):
                    message += "\n\nTip: try workspace='~' for a personal-workspace repository."
                raise ToolError(
                    f"Failed to delete repository: {response.status_code}\n{message}"
                )
            return f"Repository {repo_slug} deleted successfully from workspace '{resolved}'"

    @server.tool(
        annotations=annotations.READ_ONLY,
        description=(
            'Search repositories using Bitbucket\'s query syntax: name (name ~ "pattern"), '
            'project key (project.key = "PROJ"), language (language = "python"), or dates '
            '(updated_on >= "2024-01-19", ISO 8601 only). To search file contents instead, '
            "use bb_list_endpoints for the code-search endpoints."
        ),
    )
    async def bb_search_repositories(
        *,
        query: Annotated[
            str,
            Field(
                description="Query, e.g. 'name ~ \"test\"' or 'project.key = \"PROJ\"'"
            ),
        ],
        page: Annotated[int, Field(description="Page number", ge=1)] = 1,
        pagelen: Annotated[
            int, Field(description="Results per page", ge=1, le=100)
        ] = 10,
        workspace: Workspace = config.DEFAULT_WORKSPACE,
    ) -> str:
        response = request(
            "GET",
            api_url(f"/repositories/{workspace}"),
            headers=JSON_HEADERS,
            params={"q": query, "page": page, "pagelen": min(pagelen, 100)},
        )
        require_ok(response, "Search repositories")

        repos = response.json()
        values = repos.get("values", [])
        blocks = [
            f"• {r.get('name')}\n"
            f"  Description: {r.get('description') or 'No description'}\n"
            f"  Language: {r.get('language') or 'Unknown'}\n"
            f"  URL: {r.get('links', {}).get('html', {}).get('href', '')}"
            for r in values
        ]
        has_next = "next" in repos.get("links", {}) or bool(repos.get("next"))
        tail = (
            f"\n\nPage {page} | Total results: {repos.get('size', 0)} | "
            f"{'More results available' if has_next else 'End of results'}"
        )
        return f"Found {len(values)} repositories:\n\n" + "\n\n".join(blocks) + tail

    @server.tool(
        annotations=annotations.WRITE,
        description="Create a new branch in a Bitbucket repository",
    )
    async def bb_create_branch(
        *,
        repo_slug: RepoSlug = config.DEFAULT_REPO_SLUG,
        branch: Annotated[str, Field(description="Name for the new branch")],
        start_point: Annotated[
            str, Field(description="Branch or commit to create from")
        ] = "main",
        workspace: Workspace = config.DEFAULT_WORKSPACE,
    ) -> str:
        ref = request(
            "GET",
            api_url(
                f"/repositories/{workspace}/{repo_slug}/refs/branches/{start_point}"
            ),
            headers=JSON_HEADERS,
        )
        require_ok(ref, f"Get start point reference '{start_point}'")

        response = request(
            "POST",
            api_url(f"/repositories/{workspace}/{repo_slug}/refs/branches"),
            headers=JSON_HEADERS,
            json_data={
                "name": branch,
                "target": {"hash": ref.json()["target"]["hash"]},
            },
        )
        require_ok(response, "Create branch", expected=(200, 201))
        branch_url = response.json().get("links", {}).get("html", {}).get("href", "")
        return f"Branch '{branch}' created successfully\nURL: {branch_url}"
