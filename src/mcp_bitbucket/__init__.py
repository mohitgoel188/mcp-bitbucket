"""Bitbucket Cloud MCP server package."""


def main() -> None:
    """Console-script entry point.

    The import is deliberately lazy: importing .server here would make
    ``python -m mcp_bitbucket.server`` load the module twice (once via the
    package, once as __main__), which registers every tool twice and emits a
    RuntimeWarning.
    """
    from .server import main as run_server

    run_server()


__all__ = ["main"]
