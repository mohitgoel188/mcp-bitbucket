# MCP Bitbucket 🦊

An [MCP](https://modelcontextprotocol.io) server for **Bitbucket Cloud** — pull
request review, repository and source tools, and a searchable index of all 294
REST endpoints so the model can reach anything the typed tools do not wrap.

Derived from and substantially rewritten out of
[Kallows/mcp-bitbucket](https://github.com/Kallows/mcp-bitbucket) — see
[Credits & Acknowledgments](#credits--acknowledgments).

---

## Features

- **Complete API coverage.** Two tools — `bb_list_endpoints` and `bb_request` —
  reach every Bitbucket Cloud 2.0 endpoint, so a missing typed tool is never a
  dead end. The endpoint index is generated from Atlassian's official OpenAPI
  spec and cannot drift from the published docs.
- **Pull request review tools** built for reviewing rather than for API
  completeness: per-file diffs, trimmed PR listings, inline comments, approvals.
- **Zero-config repo targeting.** The workspace and repo slug are read from the
  git remote of the directory the client was launched in, so one registration
  serves every Bitbucket checkout.
- **Two auth modes.** Scoped API tokens (preferred) or legacy app passwords.
- **Safe by default.** Writes through the generic proxy are opt-in, repository
  deletion is not registered unless explicitly enabled, request logging is off,
  and every tool is annotated so your client can tell a read from a deletion.

---

## Installation

Requires Python **3.12+**.

```bash
git clone https://github.com/mohitgoel188/mcp-bitbucket.git
cd mcp-bitbucket

# with uv (recommended)
uv sync

# or with pip
pip install -e .
```

---

## Configuration

Register the server with your MCP client, pointing at the interpreter from the
virtualenv you just created.

> [!IMPORTANT]
> **Do not launch this server with `uv run --directory`.** That flag changes the
> process working directory to *this* repo, which breaks
> [repository auto-detection](#repository-auto-detection) — every call would
> resolve to `mcp-bitbucket` instead of the repo you are working in. Invoke the
> interpreter directly, as shown below.

### Claude Code

```bash
claude mcp add bitbucket \
  --env BITBUCKET_TOKEN=your-api-token \
  -- /absolute/path/to/mcp-bitbucket/.venv/bin/python -m mcp_bitbucket.server
```

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS,
`%APPDATA%\Claude\claude_desktop_config.json` on Windows:

```json
{
  "mcpServers": {
    "bitbucket": {
      "command": "/absolute/path/to/mcp-bitbucket/.venv/bin/python",
      "args": ["-m", "mcp_bitbucket.server"],
      "env": {
        "BITBUCKET_TOKEN": "your-api-token"
      }
    }
  }
}
```

On Windows the `command` is
`C:\\path\\to\\mcp-bitbucket\\.venv\\Scripts\\python.exe` — note that JSON
requires each backslash to be doubled.

---

## Environment Variables

Copy [`.env.example`](.env.example) as a starting point.

### Credentials — one of these two is required

```bash
# Preferred: a scoped API token or repository access token. No username needed.
# A token wins when both modes are configured, because Atlassian is migrating
# Bitbucket Cloud off app passwords onto scoped tokens.
export BITBUCKET_TOKEN="your-api-token"

# Legacy: username + app password.
export BITBUCKET_USERNAME="your-username"
export BITBUCKET_APP_PASSWORD="your-app-password"
```

The server **refuses to start** with neither, naming what to provide.

To create a token: Bitbucket → Settings → **API tokens** (or *App passwords* for
the legacy mode). Grant only the scopes you need — `repository` and
`pullrequest` cover most usage; add `:write` variants for the write tools.

### Targeting

```bash
# Workspace. Optional inside a Bitbucket checkout (read from the git remote),
# required otherwise. There is no built-in default.
export BITBUCKET_WORKSPACE="your-workspace"

# Repo slug. Same rule — auto-detected inside a checkout.
export BITBUCKET_REPO_SLUG="your-repo"

# Point auto-detection at a directory other than the process cwd.
export BITBUCKET_PROJECT_DIR="/path/to/some/checkout"
```

### Safety switches

```bash
# Allow non-GET requests through bb_request. Default "true" (reads only).
# Leave it on unless you want the model able to call any write endpoint.
export BITBUCKET_BB_REQUEST_READONLY="false"

# Register bb_delete_repository at all. Default "false".
export BITBUCKET_ALLOW_DESTRUCTIVE="true"
```

The typed write tools (`bb_write_file`, `bb_pr_comment`, `bb_pr_approve`, …) are
**not** affected by these switches — they are always available. The switches
govern the generic proxy and repository deletion specifically.

### Diagnostics

```bash
# Log every request as a runnable curl command. Default "false".
# Read the Security section before enabling this.
export BITBUCKET_ENABLE_REQUEST_LOGGING="true"

# Where to write it. Default: ./bitbucket_requests.log, relative to the cwd.
export BITBUCKET_REQUEST_LOG_FILE="/tmp/bitbucket_requests.log"
```

---

## Tools

23 tools. Two cover the entire REST API; the rest are typed conveniences for the
highest-traffic operations. `workspace` and `repo_slug` are optional wherever
they appear — see [auto-detection](#repository-auto-detection).

### Generic API access

| Tool | What it does |
|---|---|
| `bb_list_endpoints` | Search all 294 endpoints for the right method, path and params. Every whitespace-separated term must match, so `"pull request comment"` narrows better than `"comment"`. |
| `bb_request` | Call any Bitbucket Cloud 2.0 endpoint. The escape hatch for merges, commit comparison, branches and tags, pipelines, code search, webhooks, permissions. |

The intended flow is discover, then call:

```
bb_list_endpoints(search="comment pull request", method="POST")
  -> POST /repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/comments

bb_request(method="POST",
           path="/repositories/myworkspace/myrepo/pullrequests/123/comments",
           body={"content": {"raw": "Looks good"},
                 "inline": {"path": "src/example.py", "to": 42}})
```

`paginate=true` follows Bitbucket's `next` links and merges every page's
`values`, capped by `max_pages`. `raw=true` returns the body verbatim, which is
what the diff, patch and pipeline-log endpoints need since they answer in
`text/plain`. The `fields` query param trims large responses and materially cuts
token cost.

### Pull requests and review

| Tool | What it does |
|---|---|
| `bb_list_pull_requests` | List PRs by state, source/destination branch or author. The `source_branch` filter resolves a branch name to its PR number. |
| `bb_get_pull_request` | PR details, optionally with comments, commits and diff. |
| `bb_create_pull_request` | Open a PR from a source branch. |
| `bb_get_pr_diffstat` | Per-file added/removed counts without the diff bodies. |
| `bb_get_pr_file_diff` | One file's diff out of the combined PR diff. |
| `bb_get_pr_all_file_diffs` | The PR diff split per file. |
| `bb_get_pr_file_content` | A file's full content at the PR's source commit. |
| `bb_pr_list_comments` | Comments, flagging inline location, replies and resolved threads. |
| `bb_pr_comment` | Comment on a PR. `file_path` + `line` anchors it inline; `parent_id` replies in a thread; neither gives a general comment. |
| `bb_pr_resolve_thread` | Mark a comment thread resolved. |
| `bb_pr_approve` | Approve, as the authenticated user. |
| `bb_pr_request_changes` | Request changes, as the authenticated user. |

The last five are **visible to your team and mostly irreversible.** The server
tells the model to confirm intent first, but that is guidance, not enforcement.

### Repositories, source and issues

| Tool | What it does |
|---|---|
| `bb_search_repositories` | Search with Bitbucket's query syntax, e.g. `name ~ "api"`, `project.key = "PROJ"`. |
| `bb_create_repository` | Create a repository. `workspace="~"` targets your personal workspace. |
| `bb_create_branch` | Create a branch from a start point. |
| `bb_read_file` | Read a file at a branch or commit. |
| `bb_write_file` | Create or update a file, with a commit message. |
| `bb_delete_file` | Delete a file. |
| `bb_create_issue` | Create an issue with kind and priority. |
| `bb_delete_issue` | Delete an issue. |
| `bb_delete_repository` | Delete a repository. **Not registered unless `BITBUCKET_ALLOW_DESTRUCTIVE=true`.** |

---

## Repository auto-detection

The server reads the workspace and repo slug from the **git remote of its working
directory**, so one user-level registration serves every Bitbucket checkout —
`repo_slug` becomes optional and defaults to whichever repo the session was
opened in. Both SSH and HTTPS remote forms are recognised.

Precedence is explicit env → detected → unset:

| Setting | Env override | Detected from | If neither |
|---|---|---|---|
| Workspace | `BITBUCKET_WORKSPACE` | `origin` remote | required per call |
| Repo slug | `BITBUCKET_REPO_SLUG` | `origin` remote | required per call |

There is deliberately **no fallback workspace**. When nothing resolves, calls
fail with an explicit "pass workspace and repo_slug explicitly, or set
`BITBUCKET_WORKSPACE`" message rather than an opaque 404 — or worse, a silent
request against somebody else's workspace.

---

## Endpoint index

`bb_list_endpoints` reads `src/mcp_bitbucket/endpoints.json`, generated from the
same OpenAPI spec that renders
[the official REST docs](https://developer.atlassian.com/cloud/bitbucket/rest/intro/).
Each entry carries the method, path, tag, summary, path/query parameters, whether
it accepts a body, and a deep link to its docs section.

Regenerate it when Atlassian ships API changes:

```bash
# Uses a cached copy of the spec if it is less than 24h old
uv run python scripts/generate_endpoints.py

# Bypass the cache entirely
uv run python scripts/generate_endpoints.py --force

# Revalidate against the server instead of trusting the TTL
uv run python scripts/generate_endpoints.py --max-age 0
```

The spec is cached under `.spec-cache/` with its ETag. A TTL gate keeps repeat
runs off the network; ETag revalidation is attempted too, but the `dac-static`
CDN drops its ETag on the gzip variant it serves (it sets
`Vary: Accept-Encoding`), so only the `api.bitbucket.org/swagger.json` mirror can
actually answer `304`. The generator refuses to overwrite the index if the spec
yields zero operations, and skips the write entirely when nothing changed.

---

## Security

Worth understanding before you point this at a repository you care about.

**The working directory decides the target.** Auto-detection runs at import time
against the process cwd, which the client sets to your project. That is what
makes one registration work everywhere, but it also means the model can act on
whichever repo the session was opened in without naming it. Set
`BITBUCKET_WORKSPACE` / `BITBUCKET_REPO_SLUG` explicitly if you want a fixed
target.

**`bb_request` reaches every endpoint.** With `BITBUCKET_BB_REQUEST_READONLY`
at its default of `true` it refuses everything but `GET`. Turning it off grants
the model any write your credential can perform, including endpoints no typed
tool wraps.

**Scope your credential.** The server enforces nothing about permissions — the
token does. A read-scoped token is the strongest available guard rail.

**Request logging writes bodies to disk.** When
`BITBUCKET_ENABLE_REQUEST_LOGGING` is on, each request is appended as a runnable
curl command. Credentials are redacted — the `Authorization` header and both
halves of basic auth — but **request bodies are written verbatim**, which for
`bb_write_file` means file contents and for `bb_pr_comment` means comment text.
The file is relative to the cwd by default, so it lands inside your project and
grows unrotated. It is off by default for these reasons; review a log before
sharing it.

**Tool annotations are hints.** Every tool carries `readOnlyHint` /
`destructiveHint` so your client can prompt appropriately, and
`bb_delete_repository` is withheld from the schema entirely unless enabled.
These help a well-behaved client; they are not server-side enforcement.

To report a vulnerability, see [SECURITY.md](SECURITY.md).

---

## Development

### Running tests

```bash
# Everything that runs offline (fully mocked, no credentials, no network)
python -m unittest tests.test_bb_api tests.test_auth_modes tests.test_stdio_handshake

# Lint and format
uv run ruff check .
uv run ruff format .
```

`tests/test_bb_integration.py` is excluded above deliberately: it hits the
**real** Bitbucket API and creates and deletes real repositories. It skips
unless credentials and `BITBUCKET_TEST_WORKSPACE` are set. See
[tests/README.md](tests/README.md).

### Adding a tool

1. Pick the module under `src/mcp_bitbucket/tools/` by API area, or add one and
   register it in `tools/__init__.py`.
2. Inside `register(server)`, add `@server.tool(annotations=..., description=...)`
   on an `async def bb_*` with keyword-only params, each
   `Annotated[T, Field(description=...)]`. The signature *is* the JSON Schema.
3. Default `workspace` / `repo_slug` to the `config` values.
4. Call through `http_client.request()` + `api_url()`, then `require_ok()`.
5. Return trimmed, human-readable text — every token lands in someone's context.

Descriptions are prompt engineering, not documentation: they are the only thing
the model sees when choosing a tool.

### Project structure

```
src/mcp_bitbucket/
├── server.py           # MCPServer assembly + stdio entry point
├── config.py           # environment-derived settings, repo auto-detection
├── http_client.py      # auth, curl logging, error shaping, pagination
├── instructions.py     # instructions sent to the client on initialize
├── annotations.py      # MCP tool annotations (read-only / destructive)
├── endpoint_index.py   # loads and searches endpoints.json
├── endpoints.json      # generated index of all 294 endpoints
├── diffs.py            # unified-diff slicing
└── tools/
    ├── generic.py      # bb_list_endpoints / bb_request
    ├── pr_review.py    # curated review tools
    ├── pullrequests.py
    ├── repositories.py
    ├── source.py
    └── issues.py

scripts/generate_endpoints.py   # rebuilds endpoints.json from Atlassian's spec
tests/                          # 3 offline suites + 1 credential-gated suite
```

### Implementation notes

Built on `MCPServer` from the official MCP Python SDK (`mcp>=2.1.1`) — the
high-level API formerly called FastMCP, renamed in SDK v2. Tool schemas are
derived from type hints rather than hand-written, `ToolError` carries readable
failures back to the model, and `MCPServer.run` owns the stdio transport and the
initialize handshake.

Because stdout *is* the MCP transport, nothing may be printed there — diagnostics
go to stderr. `tests/test_stdio_handshake.py` spawns the server as a real
subprocess and asserts every stdout line is parseable JSON, because a broken
entry point is invisible to in-process tests.

---

## License

[MIT](LICENSE).

Copyright (c) 2025 Kevin Kreger (Kallows) for the original work, and
copyright (c) 2026 Mohit Goel for the modifications.

---

## Credits & Acknowledgments

This project combines concepts and code from several open-source projects:

- **[Kallows/mcp-bitbucket](https://github.com/Kallows/mcp-bitbucket)** by
  **Kevin Kreger** — the original Bitbucket MCP server (MIT License). It
  established the tool surface this still builds on, and the upstream tool names
  are retained for compatibility. Thank you for publishing it.
- **[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)** —
  server framework, transport and the `MCPServer` tool API (MIT License).
- **[Atlassian Bitbucket Cloud OpenAPI specification](https://developer.atlassian.com/cloud/bitbucket/rest/intro/)**
  — the source the bundled 294-endpoint index in `endpoints.json` is generated
  from, which is why it cannot drift from the published docs.

### What changed from the original

- Migrated from the low-level `Server` SDK API — hand-written JSON schemas and a
  single `call_tool` dispatch — to `MCPServer`, with schemas derived from typed
  signatures.
- Split one 856-line module into focused modules with a shared HTTP layer.
- Added the generated 294-endpoint index and the `bb_list_endpoints` /
  `bb_request` pair, taking coverage from 10 endpoints to the whole API.
- Added the pull request review toolset, per-file diff handling, and response
  trimming.
- Added workspace/repo auto-detection from the git remote, scoped-token auth,
  redacted request logging, pagination, tool annotations and the safety switches.

---

## Contributing

Contributions are welcome. Please:

1. Open an issue first for anything substantial.
2. Keep the offline test suites green, and add tests for new behaviour.
3. Run `ruff check .` and `ruff format .`.
4. Update the README when you change a tool's surface or an env variable.

See [.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md).
