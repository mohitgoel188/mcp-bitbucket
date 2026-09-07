# CLAUDE.md

Repo knowledge that is not obvious from the code and does not survive a clone
otherwise.

## What this is

An MCP server for Bitbucket Cloud. Derived from
[Kallows/mcp-bitbucket](https://github.com/Kallows/mcp-bitbucket) and
substantially rewritten — see README > Acknowledgments. MIT, dual copyright;
the upstream notice must stay in `LICENSE`.

## The central design bet

Two tools (`bb_list_endpoints`, `bb_request`) cover all 294 Bitbucket endpoints;
~21 typed tools wrap only high-traffic operations. This exists because **the real
constraint is the model's context window, not Bitbucket's API** — every tool
schema sits in context permanently. Wrapping all 294 would be unusable; wrapping
20 and stopping would leave dead ends.

A typed tool earns its place when the payload is easy to get wrong (inline
comments need a path *and* a line), the raw response is far larger than needed,
or the operation is visible to other people. Otherwise: `bb_request`.

## Traps that have actually bitten

- **`uv run --directory` breaks everything.** `config.detect_repo()` reads the
  git remote of the process cwd at import time. That flag changes cwd to *this*
  repo, so every call resolves to `mcp-bitbucket` instead of the user's project.
  Launch the venv interpreter directly. Documented in README > Configuration and
  in `config.detect_repo`'s docstring; people still hit it.
- **stdout is the MCP transport.** One stray `print()` corrupts the stream and
  the client dies on a parse error. Diagnostics go to stderr — see the
  `except` block in `http_client.log_request_as_curl`.
- **Double tool registration.** `__init__.main()` imports `.server` lazily. At
  module top level, `python -m mcp_bitbucket.server` loads the module twice
  (once as the package, once as `__main__`), registering all tools twice and
  emitting a `RuntimeWarning`. Guarded by
  `test_stdio_handshake.test_no_runtime_warnings_on_startup`.
- **In-process tests cannot see a broken entry point.** A regression once called
  a method `MCPServer` does not have; every mocked test passed while the real
  server failed with a bare `-32000`. Hence the subprocess handshake suite.
- **Auth must be injected in one place.** `http_client.build_headers` attaches
  the bearer token for every request. The prototype did it per call site, so the
  two multipart tools sent unauthenticated requests. Never credential at a call
  site.
- **`format_error` must not mislabel ordinary 4xx.** Only scope errors carry
  `required`/`granted`. Without them, return the API's own message — the early
  return at `http_client.py` exists precisely for this.
- **f-string with a backslash in the expression** (`http_client.py`, the curl log
  entry) requires Python 3.12. That, plus `datetime.UTC` (3.11), sets the floor
  in `pyproject.toml`. Do not lower it without checking both.

## Configuration reaches the server only through the client

MCP clients start the server with a **minimal environment**. A shell `export`
does not reach it, and there is no `.env` loading. Every setting has to be in
the `env` block of the client's server entry. This is the single most common
support question, so the credential error message says it explicitly, and
`SettingsAreDocumentedTest` fails if a setting `config.py` reads is absent from
the README table, the README config example, or `.env.example`.

Both Claude Code and Claude Desktop expand `${VAR}` and `${VAR:-default}` in the
`env` block, reading the environment of whatever launched the client -- so a GUI
launch does not see `~/.zshrc`. Expansion works in `.mcp.json`, `~/.claude.json`
and `claude_desktop_config.json`, but **not** in `settings.json`, whose `env`
block is ignored for MCP servers. When a variable is unset with no default, the
client passes the literal `${VAR}` text through; `config.text` rejects that with
the likely causes, because sending it as a bearer token yields an unexplained
401. This is why the README recommends `${VAR}` for the credential: a committed
`.mcp.json` then holds no secret.

Settings are optional except the credentials: unset or blank falls back to the
default. A value that is *present but unparseable* raises instead, because
silently defaulting a switch like `ALLOW_DESTRUCTIVE` leaves someone convinced
they enabled something they did not. `config.flag` accepts
true/1/yes/y/on and false/0/no/n/off — a bare `== "true"` check read
`ALLOW_DESTRUCTIVE=1` as false, which is the worst possible failure for a gate.

## Safety posture

Defaults are deliberately restrictive because this ships publicly:

- `BB_REQUEST_READONLY` defaults **true** — the generic proxy is reads-only.
- `ALLOW_DESTRUCTIVE` defaults **false** — `bb_delete_repository` is not
  registered at all. Withholding it from the schema beats refusing at call time:
  the model never sees the option. **The same flag also gates `DELETE` on
  `/repositories/{ws}/{slug}` and `/workspaces/{ws}/projects/{key}` inside
  `bb_request`.** It originally did not, which made the gate decorative: with
  writes enabled the model could delete a repository through the proxy while the
  typed tool was withheld. A guard rail at the tool layer only is not a guard
  rail — `bb_request` reaches all 294 endpoints and is the obvious way around
  one. `normalize_api_path` also rejects `.`/`..` segments so a path cannot
  resolve server-side to one just refused. Covered by `test_guard_rails.py`.
- `ENABLE_REQUEST_LOGGING` defaults **false** — the log writes request bodies
  verbatim, which for `bb_write_file` means file contents.
- **No fallback workspace.** Falling back to some hardcoded workspace means a
  stranger's install silently targets it. Empty is handled by
  `http_client.api_url`, which turns the resulting `//` into a readable error.

Every tool carries an annotation from `annotations.py`. Adding a tool without
one fails `test_every_tool_carries_annotations`.

## Endpoint index

`endpoints.json` is generated by `scripts/generate_endpoints.py` from Atlassian's
official OpenAPI spec, so it cannot drift from the published docs. Regenerate
with `uv run python scripts/generate_endpoints.py`; `--force` bypasses the cache.

Non-obvious bits: the generator handles **both** OpenAPI 3 and Swagger 2 shapes
because the fallback mirror is Swagger 2; two thirds of Bitbucket's operations
ship with no `operationId`, so ids are derived from summaries and de-duplicated;
docs anchors are computed from the path, not scraped; it refuses to write a
zero-operation index and no-ops when the output is byte-identical.

The `dac-static` CDN drops its ETag on the gzip variant it serves, so only the
`api.bitbucket.org` mirror can answer 304 — the TTL gate is what actually keeps
repeat runs off the network.

`endpoints.json` is a non-`.py` data file inside the package, so
`[tool.hatch.build.targets.wheel] artifacts` in `pyproject.toml` is what puts it
in the wheel. **A wheel without it leaves `bb_list_endpoints` permanently
empty**, and nothing else will tell you.

## Conventions

- Tool `description=` text is **prompt engineering**, not documentation. It is
  the only thing the model sees when choosing a tool.
- Server `instructions` (`instructions.py`) reach the model on every session, so
  they stay short: routing order and the gotchas that cause wrong calls.
  Longer-form workflow material belongs in a skill.
- Return trimmed human-readable text, not raw payloads. `pr_review._PR_FIELDS`
  is the pattern — Bitbucket's `fields` param cuts ~2 KB of links per PR.
- `ToolError`, not bare exceptions: it reaches the model as a readable error
  result rather than "Error executing tool".
- Commits: Conventional Commits, lowercase type, no scope, explanatory body,
  **no trailers**.
- Tests use `unittest`, not pytest. Placeholder identifiers only — never a real
  workspace, repo or username.
