"""MCP tool annotations, so clients can tell a read from a deletion.

Without these every tool looks alike to a host, which means a repository
deletion is presented to the user exactly like a file read. The hints are
advisory -- they drive client-side confirmation prompts, not server-side
enforcement -- so they complement, rather than replace, the guard rails in
``config`` and the routing advice in ``instructions``.

``openWorldHint`` is true for anything that reaches Bitbucket and false for the
one tool that only consults the bundled endpoint index.
"""

from mcp.types import ToolAnnotations

# Reads against the Bitbucket API.
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

# Reads that never leave the process.
READ_ONLY_LOCAL = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

# Creates or modifies something, but does not remove existing data.
WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)

# Removes data. Not idempotent, and not recoverable through this server.
DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)
