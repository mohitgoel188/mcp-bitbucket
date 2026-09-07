"""File-level tools backed by the Bitbucket source endpoints."""

from typing import Annotated

from pydantic import Field

from .. import annotations, config
from ..http_client import api_url, request, require_ok

Workspace = Annotated[str, Field(description="Bitbucket workspace")]
RepoSlug = Annotated[
    str,
    Field(
        description="Repository slug/name; defaults to the repo detected from the current directory's git remote"
    ),
]


def register(server) -> None:
    """Register source-area tools."""

    @server.tool(
        annotations=annotations.READ_ONLY,
        description="Read a file from a Bitbucket repository",
    )
    async def bb_read_file(
        *,
        repo_slug: RepoSlug = config.DEFAULT_REPO_SLUG,
        path: Annotated[
            str, Field(description="Path to the file within the repository")
        ],
        branch: Annotated[
            str, Field(description="Branch or commit to read from")
        ] = "main",
        workspace: Workspace = config.DEFAULT_WORKSPACE,
    ) -> str:
        response = request(
            "GET", api_url(f"/repositories/{workspace}/{repo_slug}/src/{branch}/{path}")
        )
        require_ok(response, f"Read file {path}")
        return response.text

    @server.tool(
        annotations=annotations.WRITE,
        description="Write or update a file in a Bitbucket repository",
    )
    async def bb_write_file(
        *,
        repo_slug: RepoSlug = config.DEFAULT_REPO_SLUG,
        path: Annotated[
            str, Field(description="Path to the file within the repository")
        ],
        content: Annotated[str, Field(description="Full new contents of the file")],
        message: Annotated[
            str, Field(description="Commit message")
        ] = "Update file via MCP",
        branch: Annotated[str, Field(description="Branch to commit to")] = "main",
        workspace: Workspace = config.DEFAULT_WORKSPACE,
    ) -> str:
        response = request(
            "POST",
            api_url(f"/repositories/{workspace}/{repo_slug}/src"),
            files={path: (None, content)},
            data={"message": message, "branch": branch},
        )
        require_ok(response, f"Write file {path}", expected=(200, 201))
        return f"File {path} updated successfully"

    @server.tool(
        annotations=annotations.DESTRUCTIVE,
        description="Delete a file from a Bitbucket repository",
    )
    async def bb_delete_file(
        *,
        repo_slug: RepoSlug = config.DEFAULT_REPO_SLUG,
        path: Annotated[str, Field(description="Path to the file to delete")],
        message: Annotated[
            str, Field(description="Commit message")
        ] = "Delete file via MCP",
        branch: Annotated[str, Field(description="Branch to commit to")] = "main",
        workspace: Workspace = config.DEFAULT_WORKSPACE,
    ) -> str:
        # Deletion requires the `files` form field naming the path. Uploading an
        # empty body at that path (the obvious-looking approach) truncates the
        # file to zero bytes and leaves it in the repository instead.
        response = request(
            "POST",
            api_url(f"/repositories/{workspace}/{repo_slug}/src"),
            data={"files": path, "message": message, "branch": branch},
        )
        require_ok(response, f"Delete file {path}", expected=(200, 201))
        return f"File {path} deleted successfully"
