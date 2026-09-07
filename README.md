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
- **Safe by default.** Writes through the generic proxy are opt-in, deleting a
  repository or project is refused unless explicitly enabled — through the
  proxy as well as the typed tool — request logging is off, and every tool is
  annotated so your client can tell a read from a deletion.

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
> **Your shell environment does not reach this server directly.** MCP clients
> start it as a subprocess with a minimal environment, and there is no `.env`
> loading. Every setting has to appear in the `env` block — either as a literal
> value or as a `${VAR}` reference, described below.
>
> **Do not launch this server with `uv run --directory`.** That flag changes the
> process working directory to *this* repo, which breaks
> [repository auto-detection](#repository-auto-detection) — every call would
> resolve to `mcp-bitbucket` instead of the repo you are working in. Invoke the
> interpreter directly.

### Recommended: reference your environment, do not paste the token

Claude Code and Claude Desktop both expand `${VAR}` and `${VAR:-default}` inside
the `env` block. Referencing your token rather than pasting it keeps the
credential out of the config file entirely:

```json
{
  "mcpServers": {
    "bitbucket": {
      "command": "/absolute/path/to/mcp-bitbucket/.venv/bin/python",
      "args": ["-m", "mcp_bitbucket.server"],
      "env": {
        "BITBUCKET_TOKEN": "${BITBUCKET_TOKEN}"
      }
    }
  }
}
```

> [!TIP]
> **Why this is the safer form.** The token never appears in the file, so the
> config can be committed to a repo, shared with a teammate, screenshotted in a
> bug report, or synced between machines without leaking anything. That matters
> most for a project-scoped `.mcp.json`, which normally *is* checked in. The
> secret stays in your shell profile or your OS keychain, where a `chmod 600`
> and your existing backup policy already apply to it.
>
> **Pasting the literal value is also fine** if you would rather not manage
> shell variables — the server cannot tell the difference. Just keep the file
> out of version control, since anything in it is readable by every process
> running as you.

Then set the variable where your client will see it:

```bash
# ~/.zshrc, ~/.bashrc, or wherever your login shell reads
export BITBUCKET_TOKEN="your-api-token"
```

Expansion reads the environment of the process that **launched the client**, not
your current terminal. Claude Code started from a shell inherits it. A GUI
launch of Claude Desktop does not read `~/.zshrc` at all — set the variable at
the OS level (`launchctl setenv BITBUCKET_TOKEN ...` on macOS, System
Environment Variables on Windows), start the app from a terminal, or just use a
literal value in that file.

If a variable is unset and has no `:-default`, the client passes the literal
text `${BITBUCKET_TOKEN}` through. The server detects that and tells you which
of the likely causes applies rather than sending it as a token and surfacing an
unexplained 401. `claude mcp list` also flags missing variables.

### Full configuration

Every supported setting. Only the credential is required — each value shown here
is the default you get by omitting the line. Non-secret settings are written
literally because there is nothing to hide; use `${VAR:-default}` for any you
want to vary per machine.

```json
{
  "mcpServers": {
    "bitbucket": {
      "command": "/absolute/path/to/mcp-bitbucket/.venv/bin/python",
      "args": ["-m", "mcp_bitbucket.server"],
      "env": {
        "BITBUCKET_TOKEN": "${BITBUCKET_TOKEN}",

        "BITBUCKET_USERNAME": "${BITBUCKET_USERNAME:-}",
        "BITBUCKET_APP_PASSWORD": "${BITBUCKET_APP_PASSWORD:-}",

        "BITBUCKET_WORKSPACE": "${BITBUCKET_WORKSPACE:-}",
        "BITBUCKET_REPO_SLUG": "",
        "BITBUCKET_PROJECT_DIR": "",

        "BITBUCKET_BB_REQUEST_READONLY": "true",
        "BITBUCKET_ALLOW_DESTRUCTIVE": "false",

        "BITBUCKET_ENABLE_REQUEST_LOGGING": "false",
        "BITBUCKET_REQUEST_LOG_FILE": "bitbucket_requests.log",

        "BITBUCKET_MAX_PAGINATED_PAGES": "20",
        "BITBUCKET_REQUEST_TIMEOUT_SECONDS": "120"
      }
    }
  }
}
```

Values are strings — JSON `true` is not accepted where a string is expected.
Booleans take `true`/`1`/`yes`/`on` or `false`/`0`/`no`/`off`, in any case. An
unrecognised value stops the server with a message naming the variable, rather
than quietly falling back and leaving you to wonder why a switch did nothing.

### Where the config file lives

| Client | File | Expands `${VAR}` |
|---|---|---|
| Claude Code, project scope | `.mcp.json` at the repo root | yes |
| Claude Code, user scope | `~/.claude.json` | yes |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS), `%APPDATA%\Claude\claude_desktop_config.json` (Windows) | yes |
| — | `settings.json` / `.claude/settings.json` | **no — its `env` block is ignored for MCP servers** |

On Windows the `command` is
`C:\\path\\to\\mcp-bitbucket\\.venv\\Scripts\\python.exe` — JSON requires each
backslash to be doubled.

### Claude Code CLI

```bash
claude mcp add bitbucket \
  --env BITBUCKET_TOKEN='${BITBUCKET_TOKEN}' \
  --env BITBUCKET_WORKSPACE=your-workspace \
  -- /absolute/path/to/mcp-bitbucket/.venv/bin/python -m mcp_bitbucket.server
```

Note the single quotes, which stop your shell expanding `${BITBUCKET_TOKEN}`
before the CLI sees it. Check the written file afterwards and confirm it still
contains the `${...}` reference rather than your resolved token — if it was
resolved, edit the file by hand.

### Checking it worked

```bash
claude mcp list
```

A credential error that appears even though the variable is exported in your
shell means the value is not reaching the server — check the table above for
whether that file expands variables, and whether the client was launched from an
environment that has the variable set.

---

## Environment Variables

Every setting, what it does, and its default. All of them belong in the `env`
block of your [MCP client configuration](#configuration), either literally or as
a `${VAR}` reference. The `export` form shown in each subsection is what you put
in your shell profile for `${VAR}` to resolve against — or what you use when
running the server or its tests directly from a terminal.

| Variable | Default | Purpose |
|---|---|---|
| `BITBUCKET_TOKEN` | — | Scoped API token. Preferred; wins over app-password mode. |
| `BITBUCKET_USERNAME` | — | Legacy app-password mode. |
| `BITBUCKET_APP_PASSWORD` | — | Legacy app-password mode. |
| `BITBUCKET_WORKSPACE` | git remote | Workspace to target. |
| `BITBUCKET_REPO_SLUG` | git remote | Repository to target. |
| `BITBUCKET_PROJECT_DIR` | process cwd | Where to look for the git remote. |
| `BITBUCKET_BB_REQUEST_READONLY` | `true` | Refuse non-`GET` through `bb_request`. |
| `BITBUCKET_ALLOW_DESTRUCTIVE` | `false` | Permit repository and project deletion, via the typed tool or `bb_request`. |
| `BITBUCKET_ENABLE_REQUEST_LOGGING` | `false` | Log requests as curl commands. |
| `BITBUCKET_REQUEST_LOG_FILE` | `bitbucket_requests.log` | Where that log goes. |
| `BITBUCKET_MAX_PAGINATED_PAGES` | `20` | Ceiling on pages followed when paginating. |
| `BITBUCKET_REQUEST_TIMEOUT_SECONDS` | `120` | Per-request HTTP timeout. |
| `BITBUCKET_TEST_WORKSPACE` | — | Live integration tests only. See [tests](tests/README.md). |

Only a credential is required. Everything else falls back to the default above
when unset or blank. A **present but unparseable** value is an error rather than
a silent fallback — booleans accept `true`/`1`/`yes`/`on` and
`false`/`0`/`no`/`off` in any case, and anything else stops the server with a
message naming the variable.

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
`pullrequest` cover most usage; add `:write` variants for the write tools. Your
token's scopes are the real boundary; the server enforces nothing about
permissions.

### Targeting

Both are auto-detected from the `origin` remote of the working directory, so
inside a Bitbucket checkout you can usually omit them entirely. Set them when
the client is launched somewhere else, or to pin a fixed target.

```bash
export BITBUCKET_WORKSPACE="your-workspace"
export BITBUCKET_REPO_SLUG="your-repo"

# Point auto-detection at a directory other than the process cwd.
export BITBUCKET_PROJECT_DIR="/path/to/some/checkout"
```

There is no built-in default workspace. When nothing resolves, calls fail with
an explicit message rather than an opaque 404 — see
[auto-detection](#repository-auto-detection).

### Safety switches

```bash
# Allow non-GET requests through bb_request. Default "true" (reads only).
# Leave it on unless you want the model able to call any write endpoint.
export BITBUCKET_BB_REQUEST_READONLY="false"

# Register bb_delete_repository at all. Default "false".
export BITBUCKET_ALLOW_DESTRUCTIVE="true"
```

The two switches interact, and it matters which way round:

- `BITBUCKET_BB_REQUEST_READONLY` bounds `bb_request` to `GET`.
- `BITBUCKET_ALLOW_DESTRUCTIVE` controls whether a repository or a project can
  be deleted **at all** — it withholds `bb_delete_repository` from the schema
  *and* makes `bb_request` refuse `DELETE` on those two endpoints. Enabling
  writes therefore does not quietly re-open repository deletion through the
  proxy.

Every other `DELETE` — a comment, a webhook, a branch, a deploy key — is an
ordinary write, governed by the read-only switch alone.

The typed write tools (`bb_write_file`, `bb_pr_comment`, `bb_pr_approve`, …) are
**not** affected by either switch; they are always available. `bb_delete_file`
and `bb_delete_issue` are likewise always available — they are scoped
deletions, not container deletions.

### Diagnostics and tuning

```bash
# Log every request as a runnable curl command. Default "false".
# Read the Security section before enabling this.
export BITBUCKET_ENABLE_REQUEST_LOGGING="true"

# Where to write it. Default: ./bitbucket_requests.log, relative to the cwd.
export BITBUCKET_REQUEST_LOG_FILE="/tmp/bitbucket_requests.log"

# Raise for very large collections, lower to cap token cost. Default 20.
export BITBUCKET_MAX_PAGINATED_PAGES="20"

# Raise on a slow network, lower to fail fast. Default 120.
export BITBUCKET_REQUEST_TIMEOUT_SECONDS="120"
```

> [!NOTE]
> [`.env.example`](.env.example) lists all of the above in one place, but the
> server does **not** read `.env` files, and neither client loads one for
> `${VAR}` expansion. Treat it as a reference: copy the values into your shell
> profile, or into the `env` block directly.

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
| `bb_delete_repository` | Delete a repository. **Not registered unless `BITBUCKET_ALLOW_DESTRUCTIVE=true`**, and `bb_request` refuses the same endpoint without it. |

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

Two exceptions survive that: `DELETE` on a repository or a project is refused
unless `BITBUCKET_ALLOW_DESTRUCTIVE` is also set. A guard rail expressed only at
the tool layer is not a guard rail, because the proxy is the obvious way around
it — so the flag binds both. Relative path segments are rejected for the same
reason, since a `..` could otherwise resolve server-side to a path just
refused.

**Scope your credential.** The server enforces nothing about permissions — the
token does. A read-scoped token is the strongest available guard rail.

**Keep the token out of the config file.** Both clients expand `${VAR}` in the
`env` block, so the credential can live in your shell profile or OS keychain
instead of in a file that gets committed, synced or pasted into a bug report.
See [Configuration](#recommended-reference-your-environment-do-not-paste-the-token).
The server rejects an unexpanded `${...}` placeholder rather than sending it as
a token, so a misconfiguration fails with an explanation instead of a bare 401.

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

`tests/test_guard_rails.py` is worth reading if you change either switch: it
asserts the proxy cannot route around them.

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
