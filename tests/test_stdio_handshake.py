"""Smoke test for the stdio entry point.

This exists because a broken ``main()`` is invisible to tests that drive the
server in-process: an earlier regression called a method MCPServer does not
have, so every in-process test passed while the real server failed to start
with a bare JSON-RPC -32000. This test spawns the server the way a client does.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# A floor rather than an exact count: the tool surface grows, and
# bb_delete_repository is only registered when BITBUCKET_ALLOW_DESTRUCTIVE is
# set, so an equality assertion here breaks on unrelated changes. What matters
# is that registration ran and the headline tools are present.
MINIMUM_TOOL_COUNT = 20


def _rpc(payload: dict) -> str:
    return json.dumps(payload) + "\n"


class StdioHandshakeTest(unittest.TestCase):
    """Drive a real subprocess over stdio, as an MCP client would."""

    def setUp(self) -> None:
        self.env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(Path.home()),
            # Credentials only need to be present; the handshake makes no API call.
            "BITBUCKET_USERNAME": "test-user",
            "BITBUCKET_APP_PASSWORD": "test-password",
            "BITBUCKET_ENABLE_REQUEST_LOGGING": "false",
        }

    def _handshake(self) -> dict[int, dict]:
        stdin = "".join(
            [
                _rpc(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "1"},
                        },
                    }
                ),
                _rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                _rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            ]
        )

        proc = subprocess.run(
            [sys.executable, "-m", "mcp_bitbucket.server"],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=REPO_ROOT,
            env={**self.env, "PYTHONPATH": str(REPO_ROOT / "src")},
            check=False,
        )

        responses: dict[int, dict] = {}
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            message = json.loads(line)  # non-JSON on stdout would corrupt the transport
            if message.get("id") is not None:
                responses[message["id"]] = message
        self.stderr = proc.stderr
        return responses

    def test_initialize_returns_server_info_and_instructions(self) -> None:
        responses = self._handshake()
        self.assertIn(1, responses, f"no initialize response; stderr:\n{self.stderr}")
        result = responses[1]["result"]
        self.assertEqual(result["serverInfo"]["name"], "bitbucket-api")
        self.assertTrue(
            result.get("instructions"), "server instructions were not delivered"
        )
        self.assertIn("bb_list_endpoints", result["instructions"])

    def test_tools_list_exposes_every_tool(self) -> None:
        responses = self._handshake()
        self.assertIn(2, responses, f"no tools/list response; stderr:\n{self.stderr}")
        names = {tool["name"] for tool in responses[2]["result"]["tools"]}
        self.assertGreaterEqual(len(names), MINIMUM_TOOL_COUNT)
        for required in (
            "bb_request",
            "bb_list_endpoints",
            "bb_pr_comment",
            "bb_get_pr_all_file_diffs",
        ):
            self.assertIn(required, names)

    def test_destructive_tool_is_withheld_unless_enabled(self) -> None:
        """bb_delete_repository must not even appear in the schema by default."""
        responses = self._handshake()
        names = {tool["name"] for tool in responses[2]["result"]["tools"]}
        self.assertNotIn("bb_delete_repository", names)

    def test_every_tool_carries_annotations(self) -> None:
        """Hosts rely on these hints to tell a read from a deletion."""
        responses = self._handshake()
        for tool in responses[2]["result"]["tools"]:
            self.assertIn("annotations", tool, f"{tool['name']} has no annotations")

    def test_no_runtime_warnings_on_startup(self) -> None:
        self._handshake()
        self.assertNotIn("RuntimeWarning", self.stderr)


if __name__ == "__main__":
    unittest.main()
