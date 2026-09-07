# Tests

Four suites. Three run offline with no credentials and no network; one hits the
real Bitbucket API and is skipped unless you deliberately opt in.

| File | Class | Offline | What it covers |
|---|---|---|---|
| `test_bb_api.py` | `TestBitbucketApiTools` | yes | Every tool's happy path and error handling, with `requests` fully mocked. |
| `test_auth_modes.py` | `AuthModeTest`, `LogRedactionTest`, `RepoDetectionTest` | yes | Both auth modes, credential presence per verb, log redaction, git-remote parsing. |
| `test_stdio_handshake.py` | `StdioHandshakeTest` | yes | The real stdio entry point, driven as an MCP client would. |
| `test_bb_integration.py` | `BitbucketAPITest` | **no** | Live API calls. **Creates and deletes real repositories.** |

## Running them

```bash
# The offline suites -- this is what CI runs
python -m unittest tests.test_bb_api tests.test_auth_modes tests.test_stdio_handshake

# Everything, with the live suite skipping itself
python -m unittest discover tests

# Verbose
python -m unittest discover tests -v

# One class, or one test
python -m unittest tests.test_auth_modes.AuthModeTest
python -m unittest tests.test_auth_modes.AuthModeTest.test_token_takes_precedence_over_app_password
```

Coverage:

```bash
uv run coverage run -m unittest tests.test_bb_api tests.test_auth_modes tests.test_stdio_handshake
uv run coverage report -m --include="src/*"
```

## The live suite

`test_bb_integration.py` skips unless **both** are set:

```bash
export BITBUCKET_TOKEN="..."                      # or USERNAME + APP_PASSWORD
export BITBUCKET_TEST_WORKSPACE="scratch-workspace"
python -m unittest tests.test_bb_integration
```

> [!WARNING]
> It creates repositories, writes files and issues into them, and deletes them in
> `tearDown`. Point it at a scratch workspace. The two-key requirement exists so
> a credential alone cannot trigger it — which is also what keeps it out of CI.

It sets `BITBUCKET_ALLOW_DESTRUCTIVE=true` for the child server, because its own
cleanup path needs `bb_delete_repository`, which is otherwise not registered.

## Why the stdio suite exists separately

`test_stdio_handshake.py` spawns `python -m mcp_bitbucket.server` as a real
subprocess and hand-writes the JSON-RPC `initialize` →
`notifications/initialized` → `tools/list` sequence.

It exists because a broken entry point is **invisible** to in-process tests: an
earlier regression called a method `MCPServer` does not have, so every mocked
test passed while the real server failed to start with a bare `-32000`. The
suite asserts that the handshake returns `serverInfo` and instructions, that
every stdout line is parseable JSON (anything else corrupts the transport), that
startup emits no `RuntimeWarning` (which catches double tool registration), that
every tool carries annotations, and that `bb_delete_repository` is absent from
the schema by default.

Tool count is asserted as a **floor**, not an exact number — the surface grows,
and `bb_delete_repository` registration is conditional.

## Writing tests

- Mock at the `requests` boundary (`patch("requests.get")` and friends), not at
  the tool boundary — the HTTP layer is where the interesting bugs have been.
- Assert on the credential actually sent. The bug that motivated
  `test_auth_modes.py` was two multipart tools passing `auth=` without
  `headers=`, silently sending unauthenticated requests while tests passed.
- Use placeholder identifiers (`myworkspace`, `myrepo`, `alice`). Never a real
  workspace, repository, username or token, even a revoked one.
- `config` reads the environment at import time, so tests that change
  credentials must `importlib.reload` it — see `_reload` in `test_auth_modes.py`.
