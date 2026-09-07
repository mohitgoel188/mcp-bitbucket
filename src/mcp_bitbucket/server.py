"""Bitbucket Cloud MCP server.

Tool implementations live under ``tools/`` grouped by API area. Two of them --
``bb_list_endpoints`` and ``bb_request`` -- together cover every Bitbucket Cloud
2.0 endpoint; the rest are typed conveniences for high-traffic operations.
"""

from importlib.metadata import PackageNotFoundError, version

from mcp.server import MCPServer

from .instructions import SERVER_INSTRUCTIONS
from .tools import register_all

try:
    # Single source of truth: the version declared in pyproject.toml. Hardcoding
    # it here as well is how the two drifted apart before.
    SERVER_VERSION = version("mcp-bitbucket")
except PackageNotFoundError:  # running from a source tree with no install
    SERVER_VERSION = "0.0.0+source"

server = MCPServer(
    name="bitbucket-api",
    version=SERVER_VERSION,
    instructions=SERVER_INSTRUCTIONS,
)

register_all(server)


def main() -> None:
    """Run the server over stdio.

    MCPServer.run owns the transport plumbing and the initialize handshake, so
    there is no stdio context manager or InitializationOptions to build here.
    """
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
