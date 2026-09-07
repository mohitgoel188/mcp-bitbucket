"""Pull request creation, inspection and diff tools."""

from typing import Annotated

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .. import annotations, config
from ..diffs import extract_file_diff, parse_diff_by_file
from ..http_client import JSON_HEADERS, api_url, format_error, request, require_ok

Workspace = Annotated[str, Field(description="Bitbucket workspace")]
RepoSlug = Annotated[
    str,
    Field(
        description="Repository slug/name; defaults to the repo detected from the current directory's git remote"
    ),
]
PrId = Annotated[int, Field(description="Pull request ID")]

_STATUS_CHARS = {"modified": "M", "added": "A", "removed": "D", "removed_file": "D"}


def register(server) -> None:
    """Register pull-request-area tools."""

    def pr_path(workspace: str, repo_slug: str, pr_id: int) -> str:
        return f"/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}"

    @server.tool(
        annotations=annotations.WRITE,
        description="Create a new pull request in a Bitbucket repository",
    )
    async def bb_create_pull_request(
        *,
        repo_slug: RepoSlug = config.DEFAULT_REPO_SLUG,
        title: Annotated[str, Field(description="Pull request title")],
        source_branch: Annotated[
            str, Field(description="Branch containing the changes")
        ],
        destination_branch: Annotated[
            str, Field(description="Branch to merge into")
        ] = "main",
        description: Annotated[str, Field(description="Pull request description")] = "",
        close_source_branch: Annotated[
            bool, Field(description="Delete the source branch on merge")
        ] = True,
        workspace: Workspace = config.DEFAULT_WORKSPACE,
    ) -> str:
        response = request(
            "POST",
            api_url(f"/repositories/{workspace}/{repo_slug}/pullrequests"),
            headers=JSON_HEADERS,
            json_data={
                "title": title,
                "description": description,
                "source": {"branch": {"name": source_branch}},
                "destination": {"branch": {"name": destination_branch}},
                "close_source_branch": close_source_branch,
            },
        )
        require_ok(response, "Create pull request", expected=(200, 201))
        data = response.json()
        pr_url = data.get("links", {}).get("html", {}).get("href", "")
        return f"Pull request created successfully\nID: {data.get('id')}\nURL: {pr_url}"

    @server.tool(
        annotations=annotations.READ_ONLY,
        description="Get details of a pull request in a Bitbucket repository",
    )
    async def bb_get_pull_request(
        *,
        repo_slug: RepoSlug = config.DEFAULT_REPO_SLUG,
        pr_id: PrId,
        include_comments: Annotated[
            bool, Field(description="Append the comment list")
        ] = False,
        include_commits: Annotated[
            bool, Field(description="Append the commit list")
        ] = False,
        include_diff: Annotated[
            bool, Field(description="Append the complete diff (can be very large)")
        ] = False,
        workspace: Workspace = config.DEFAULT_WORKSPACE,
    ) -> str:
        base = pr_path(workspace, repo_slug, pr_id)
        response = request("GET", api_url(base), headers=JSON_HEADERS)
        require_ok(response, f"Get pull request {pr_id}")
        pr = response.json()

        source = pr.get("source", {}).get("branch", {}).get("name")
        destination = pr.get("destination", {}).get("branch", {}).get("name")
        lines = [
            f"Pull Request #{pr_id}: {pr.get('title')}",
            f"State: {pr.get('state')}",
            f"Author: {pr.get('author', {}).get('display_name', 'Unknown')}",
            f"Source: {source} → {destination}",
            f"URL: {pr.get('links', {}).get('html', {}).get('href', '')}",
            f"Created: {pr.get('created_on')}",
            f"Updated: {pr.get('updated_on')}",
        ]
        output = "\n".join(lines) + "\n"

        if pr.get("description"):
            output += f"\nDescription:\n{pr['description']}\n"

        if include_comments:
            comments = request("GET", api_url(f"{base}/comments"), headers=JSON_HEADERS)
            if comments.ok:
                values = comments.json().get("values", [])
                output += f"\nComments ({len(values)}):\n"
                for comment in values:
                    author = comment.get("user", {}).get("display_name", "Unknown")
                    output += (
                        f"- {author}: {comment.get('content', {}).get('raw', '')}\n"
                    )

        if include_commits:
            commits = request("GET", api_url(f"{base}/commits"), headers=JSON_HEADERS)
            if commits.ok:
                values = commits.json().get("values", [])
                output += f"\nCommits ({len(values)}):\n"
                for commit in values:
                    subject = commit.get("message", "").split("\n")[0]
                    output += f"- {commit.get('hash', '')[:7]}: {subject}\n"

        if include_diff:
            diff = request("GET", api_url(f"{base}/diff"))
            if diff.ok:
                output += f"\nComplete Diff:\n{diff.text}"

        return output

    @server.tool(
        annotations=annotations.READ_ONLY,
        description=(
            "Get the diffstat for a pull request: every modified file with its "
            "added/removed line counts and a totals summary"
        ),
    )
    async def bb_get_pr_diffstat(
        *,
        repo_slug: RepoSlug = config.DEFAULT_REPO_SLUG,
        pr_id: PrId,
        workspace: Workspace = config.DEFAULT_WORKSPACE,
    ) -> str:
        response = request(
            "GET",
            api_url(f"{pr_path(workspace, repo_slug, pr_id)}/diffstat"),
            headers=JSON_HEADERS,
        )
        require_ok(response, "Get PR diffstat")
        data = response.json()

        output = f"Pull Request #{pr_id} - File Changes:\n\n"
        values = data.get("values")
        if not values:
            return output + "No file changes found."

        total_added = 0
        total_removed = 0
        for entry in values:
            path = entry.get("new", {}).get("path") or entry.get("old", {}).get(
                "path", "unknown"
            )
            added = entry.get("lines_added", 0)
            removed = entry.get("lines_removed", 0)
            total_added += added
            total_removed += removed
            status = _STATUS_CHARS.get(entry.get("status", "modified"), "M")
            output += f"{status} {path}\n  +{added} -{removed} lines\n\n"

        output += (
            "Summary:\n"
            f"Total files changed: {len(values)}\n"
            f"Total additions: +{total_added}\n"
            f"Total deletions: -{total_removed}\n"
        )
        return output

    @server.tool(
        annotations=annotations.READ_ONLY,
        description=(
            "Get one file's content at both the source and destination branches of a "
            "pull request, for side-by-side review"
        ),
    )
    async def bb_get_pr_file_content(
        *,
        repo_slug: RepoSlug = config.DEFAULT_REPO_SLUG,
        pr_id: PrId,
        file_path: Annotated[str, Field(description="Path of the file to fetch")],
        include_both: Annotated[
            bool, Field(description="Fetch both sides; false fetches only the source")
        ] = True,
        workspace: Workspace = config.DEFAULT_WORKSPACE,
    ) -> str:
        pr = request(
            "GET", api_url(pr_path(workspace, repo_slug, pr_id)), headers=JSON_HEADERS
        )
        require_ok(pr, "Get PR details")
        data = pr.json()
        source = data.get("source", {}).get("branch", {}).get("name")
        destination = data.get("destination", {}).get("branch", {}).get("name")

        def fetch(branch: str) -> tuple[int, str]:
            resp = request(
                "GET",
                api_url(
                    f"/repositories/{workspace}/{repo_slug}/src/{branch}/{file_path}"
                ),
            )
            return resp.status_code, resp.text

        output = (
            f"File Content Comparison - {file_path}\n"
            f"PR #{pr_id}: {source} → {destination}\n\n"
        )

        if not include_both:
            status, text = fetch(source)
            if status != 200:
                raise ToolError(
                    f"Get file content failed: {status}\n{format_error(text)}"
                )
            return output + f"=== CONTENT ({source}) ===\n{text}"

        for label, branch, missing_hint in (
            ("SOURCE", source, "likely a new file"),
            ("DESTINATION", destination, "likely deleted"),
        ):
            status, text = fetch(branch)
            output += f"=== {label} ({branch}) ===\n"
            if status == 200:
                output += text + "\n\n"
            elif status == 404:
                output += f"[FILE NOT FOUND - {missing_hint}]\n\n"
            else:
                output += f"[ERROR: {status}]\n\n"

        return output.rstrip("\n") + "\n"

    @server.tool(
        annotations=annotations.READ_ONLY,
        description="Get the line-by-line diff for one specific file in a pull request",
    )
    async def bb_get_pr_file_diff(
        *,
        repo_slug: RepoSlug = config.DEFAULT_REPO_SLUG,
        pr_id: PrId,
        file_path: Annotated[str, Field(description="Path of the file to diff")],
        workspace: Workspace = config.DEFAULT_WORKSPACE,
    ) -> str:
        response = request(
            "GET", api_url(f"{pr_path(workspace, repo_slug, pr_id)}/diff")
        )
        require_ok(response, "Get PR diff")

        lines = extract_file_diff(response.text, file_path)
        if not lines:
            return f"No diff found for file: {file_path} in PR #{pr_id}"
        return f"File Diff - {file_path} (PR #{pr_id})\n\n" + "\n".join(lines)

    @server.tool(
        annotations=annotations.READ_ONLY,
        description=(
            "Get the complete diff for every modified file in a pull request in a single "
            "call - much more efficient than calling bb_get_pr_file_diff per file"
        ),
    )
    async def bb_get_pr_all_file_diffs(
        *,
        repo_slug: RepoSlug = config.DEFAULT_REPO_SLUG,
        pr_id: PrId,
        include_context: Annotated[
            bool, Field(description="Keep diff metadata/context lines")
        ] = True,
        workspace: Workspace = config.DEFAULT_WORKSPACE,
    ) -> str:
        response = request(
            "GET", api_url(f"{pr_path(workspace, repo_slug, pr_id)}/diff")
        )
        require_ok(response, "Get PR diff")

        file_diffs = parse_diff_by_file(response.text, include_context)
        if not file_diffs:
            return f"No file changes found in PR #{pr_id}"

        separator = "=" * 80
        output = (
            f"Complete Diff for PR #{pr_id}\n"
            f"Found {len(file_diffs)} modified files\n\n{separator}\n\n"
        )
        for path, diff_content in file_diffs.items():
            output += f"FILE: {path}\n{'-' * 60}\n{diff_content}\n\n{separator}\n\n"
        return output
