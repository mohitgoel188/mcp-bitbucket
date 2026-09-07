"""The generic proxy must not route around the typed tools' guard rails.

bb_request can reach all 294 endpoints, which makes it the obvious way to
defeat a restriction expressed at the tool layer. bb_delete_repository is
withheld from the schema unless BITBUCKET_ALLOW_DESTRUCTIVE is set, and before
these tests existed the proxy would happily issue the same DELETE, leaving that
gate decorative.
"""

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("BITBUCKET_TOKEN", "tok-example-not-real")
os.environ["BITBUCKET_ENABLE_REQUEST_LOGGING"] = "false"

from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from mcp_bitbucket import config  # noqa: E402
from mcp_bitbucket.server import server  # noqa: E402

BB_REQUEST = server._tool_manager.get_tool("bb_request")

# Deleting either of these takes a whole container of work with it. The second
# is the worse of the two: a project holds repositories.
CONTAINER_DELETIONS = (
    "/repositories/myworkspace/myrepo",
    "/workspaces/myworkspace/projects/PROJ",
)

# Equivalent spellings of the same repository deletion. The guard runs after
# normalisation so that all of them are covered by one check.
SPELLINGS = (
    "/repositories/myworkspace/myrepo",
    "/repositories/myworkspace/myrepo/",
    "repositories/myworkspace/myrepo",
    "2.0/repositories/myworkspace/myrepo",
    "https://api.bitbucket.org/2.0/repositories/myworkspace/myrepo",
)


def _call(**kwargs) -> str:
    return asyncio.run(BB_REQUEST.fn(**kwargs))


def _no_content() -> Mock:
    return Mock(status_code=204, ok=True, content=b"", headers={}, text="")


class ReadOnlyGuardTest(unittest.TestCase):
    """BITBUCKET_BB_REQUEST_READONLY refuses everything but GET."""

    def test_writes_are_refused_while_read_only(self) -> None:
        with patch.object(config, "BB_REQUEST_READONLY", True):
            for verb in ("POST", "PUT", "PATCH", "DELETE"):
                with self.subTest(verb=verb), self.assertRaises(ToolError) as caught:
                    _call(method=verb, path="/repositories/myworkspace/myrepo/issues")
                self.assertIn("read-only", str(caught.exception))

    def test_get_still_works_while_read_only(self) -> None:
        response = Mock(
            status_code=200,
            ok=True,
            content=b"{}",
            headers={"Content-Type": "application/json"},
        )
        response.json.return_value = {"values": []}
        with (
            patch.object(config, "BB_REQUEST_READONLY", True),
            patch("requests.get", return_value=response),
        ):
            self.assertIn("200", _call(path="/repositories/myworkspace/myrepo"))


class DestructiveGuardTest(unittest.TestCase):
    """BITBUCKET_ALLOW_DESTRUCTIVE has to bind the proxy, not just the schema."""

    def setUp(self) -> None:
        self.writes_allowed = patch.object(config, "BB_REQUEST_READONLY", False)
        self.writes_allowed.start()
        self.addCleanup(self.writes_allowed.stop)

    def test_container_deletions_are_refused(self) -> None:
        with patch.object(config, "ALLOW_DESTRUCTIVE", False):
            for path in CONTAINER_DELETIONS:
                with self.subTest(path=path), self.assertRaises(ToolError) as caught:
                    _call(method="DELETE", path=path)
                self.assertIn("BITBUCKET_ALLOW_DESTRUCTIVE", str(caught.exception))

    def test_no_spelling_of_the_path_gets_through(self) -> None:
        """The check runs post-normalisation, so every accepted form is covered."""
        with (
            patch.object(config, "ALLOW_DESTRUCTIVE", False),
            patch("requests.delete", return_value=_no_content()) as sent,
        ):
            for path in SPELLINGS:
                with self.subTest(path=path), self.assertRaises(ToolError):
                    _call(method="DELETE", path=path)
            sent.assert_not_called()

    def test_relative_segments_are_rejected(self) -> None:
        """A '..' could resolve server-side to a path the guard just refused."""
        with (
            patch.object(config, "ALLOW_DESTRUCTIVE", False),
            patch("requests.delete", return_value=_no_content()) as sent,
        ):
            for path in (
                "/repositories/myworkspace/myrepo/x/..",
                "/repositories/myworkspace/./myrepo",
            ):
                with self.subTest(path=path), self.assertRaises(ToolError) as caught:
                    _call(method="DELETE", path=path)
                self.assertIn("relative segments", str(caught.exception))
            sent.assert_not_called()

    def test_ordinary_deletes_are_unaffected(self) -> None:
        """The gate is about containers, not about DELETE as a verb."""
        ordinary = (
            "/repositories/myworkspace/myrepo/pullrequests/1/comments/2",
            "/repositories/myworkspace/myrepo/hooks/abc",
            "/repositories/myworkspace/myrepo/refs/branches/feature",
            "/workspaces/myworkspace/projects/PROJ/deploy-keys/1",
        )
        with (
            patch.object(config, "ALLOW_DESTRUCTIVE", False),
            patch("requests.delete", return_value=_no_content()),
        ):
            for path in ordinary:
                with self.subTest(path=path):
                    self.assertIn("succeeded", _call(method="DELETE", path=path))

    def test_enabling_the_flag_permits_container_deletion(self) -> None:
        with (
            patch.object(config, "ALLOW_DESTRUCTIVE", True),
            patch("requests.delete", return_value=_no_content()),
        ):
            for path in CONTAINER_DELETIONS:
                with self.subTest(path=path):
                    self.assertIn("succeeded", _call(method="DELETE", path=path))


class RegisteredToolsMatchTheGuardsTest(unittest.TestCase):
    """What the schema advertises has to match what the server will do."""

    def test_destructive_tools_are_annotated(self) -> None:
        for name in ("bb_delete_file", "bb_delete_issue"):
            with self.subTest(name=name):
                tool = server._tool_manager.get_tool(name)
                self.assertTrue(tool.annotations.destructive_hint)

    def test_read_tools_are_not_marked_destructive(self) -> None:
        for name in ("bb_read_file", "bb_get_pull_request", "bb_list_pull_requests"):
            with self.subTest(name=name):
                tool = server._tool_manager.get_tool(name)
                self.assertTrue(tool.annotations.read_only_hint)
                self.assertFalse(tool.annotations.destructive_hint)


if __name__ == "__main__":
    unittest.main()
