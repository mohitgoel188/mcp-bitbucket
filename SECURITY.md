# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Report it privately through GitHub's
[private vulnerability reporting](https://github.com/mohitgoel188/mcp-bitbucket/security/advisories/new).
Include what you found, how to reproduce it, and what an attacker could achieve.
Expect an acknowledgement within a week.

## Scope

This server holds Bitbucket credentials and can read and write repositories, so
the things most worth reporting are:

- A path by which credentials leak — into the request log, into a tool result,
  into stdout, or into an error message.
- A way to make the server act on a workspace or repository other than the
  configured or detected one.
- A way to bypass `BITBUCKET_BB_REQUEST_READONLY` or
  `BITBUCKET_ALLOW_DESTRUCTIVE`.
- Any injection through tool arguments into the request path, query or body.

## Known design trade-offs

These are documented behaviours, not vulnerabilities. See
[README > Security](README.md#security) for the detail.

- **The working directory decides the target.** The workspace and repo slug are
  auto-detected from the cwd's git remote at import time.
- **`bb_request` can reach every Bitbucket endpoint.** It is restricted to `GET`
  by default; disabling that grants any write the credential permits.
- **Request logging records request bodies verbatim.** Credentials are redacted;
  bodies are not. It is off by default.
- **The server enforces nothing about permissions.** Your token's scopes are the
  real boundary — grant the narrowest set that works.

## Supported versions

Fixes land on `main`. There are no maintained release branches.
