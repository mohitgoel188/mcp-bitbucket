"""Smoke test for the stdio entry point.

This exists because a broken ``main()`` is invisible to tests that drive the
server in-process: an earlier regression called a method MCPServer does not
have, so every in-process test passed while the real server failed to start
with a bare JSON-RPC -32000. This test spawns the server the way a client does.
"""

import json
import subprocess
import sys
import time
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

    # The handshake is run once and shared. Each test used to spawn its own
    # server, which was both slow and the source of a race: writing every
    # message and immediately closing stdin let the server see EOF and shut
    # down before it had dispatched tools/list. A fast machine always won that
    # race; a cold CI runner lost it. The reader below waits for the responses
    # instead of assuming they arrived.
    _result: tuple[dict[int, dict], str] | None = None

    ENV = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(Path.home()),
        # Credentials only need to be present; the handshake makes no API call.
        "BITBUCKET_USERNAME": "test-user",
        "BITBUCKET_APP_PASSWORD": "test-password",
        "BITBUCKET_ENABLE_REQUEST_LOGGING": "false",
        "PYTHONPATH": str(REPO_ROOT / "src"),
    }

    @classmethod
    def _run(cls) -> tuple[dict[int, dict], str]:
        """Spawn the server, complete the handshake, return responses + stderr."""
        proc = subprocess.Popen(
            [sys.executable, "-m", "mcp_bitbucket.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=REPO_ROOT,
            env=cls.ENV,
        )
        assert proc.stdin and proc.stdout
        proc.stdin.write(
            "".join(
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
        )
        proc.stdin.flush()

        responses: dict[int, dict] = {}
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:  # the server exited or died
                break
            line = line.strip()
            if not line:
                continue
            # Non-JSON on stdout would corrupt the transport, so parsing every
            # line is itself the assertion.
            message = json.loads(line)
            if message.get("id") is not None:
                responses[message["id"]] = message
            if 1 in responses and 2 in responses:
                break

        proc.stdin.close()
        try:
            _, stderr = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, stderr = proc.communicate()
        return responses, stderr

    @classmethod
    def _handshake_result(cls) -> tuple[dict[int, dict], str]:
        if cls._result is None:
            cls._result = cls._run()
        return cls._result

    def _handshake(self) -> dict[int, dict]:
        responses, self.stderr = self._handshake_result()
        self.assertIn(2, responses, f"no tools/list response; stderr:\n{self.stderr}")
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
