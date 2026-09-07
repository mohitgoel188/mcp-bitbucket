"""Both auth modes must credential every request.

The prototype this was ported from applied its bearer token at each call site,
so the two multipart tools (write_file, delete_file) passed `auth` without
`headers` and sent unauthenticated requests. These tests assert the credential
is present for every verb and every content type, regardless of mode.
"""

import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

TOKEN_ENV = {"BITBUCKET_TOKEN": "tok-example-not-real"}
BASIC_ENV = {
    "BITBUCKET_USERNAME": "alice",
    "BITBUCKET_APP_PASSWORD": "pw-example-not-real",
}


def _reload(env: dict[str, str]):
    """Re-import config/http_client under a specific credential environment."""
    keys = ["BITBUCKET_TOKEN", "BITBUCKET_USERNAME", "BITBUCKET_APP_PASSWORD"]
    clean = {k: v for k, v in os.environ.items() if k not in keys}
    with patch.dict(os.environ, {**clean, **env}, clear=True):
        config = importlib.reload(importlib.import_module("mcp_bitbucket.config"))
        http_client = importlib.reload(
            importlib.import_module("mcp_bitbucket.http_client")
        )
    return config, http_client


class AuthModeTest(unittest.TestCase):
    """Credential presence and precedence across both modes."""

    def test_token_mode_sets_bearer_and_no_basic_auth(self) -> None:
        config, http = _reload(TOKEN_ENV)
        self.assertEqual(config.AUTH_MODE, "token")
        self.assertIsNone(http.auth())
        self.assertEqual(
            http.build_headers(None)["Authorization"], "Bearer tok-example-not-real"
        )

    def test_app_password_mode_sets_basic_auth_and_no_bearer(self) -> None:
        config, http = _reload(BASIC_ENV)
        self.assertEqual(config.AUTH_MODE, "app_password")
        self.assertEqual(http.auth().username, "alice")
        self.assertNotIn("Authorization", http.build_headers(None))

    def test_token_takes_precedence_over_app_password(self) -> None:
        config, _ = _reload({**BASIC_ENV, **TOKEN_ENV})
        self.assertEqual(config.AUTH_MODE, "token")

    def test_missing_credentials_raises(self) -> None:
        with self.assertRaises(ValueError):
            _reload({})

    def test_every_verb_carries_the_token_even_without_headers(self) -> None:
        _, http = _reload(TOKEN_ENV)
        for verb in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            with patch(f"requests.{verb.lower()}") as mock:
                http.request(verb, "https://api.bitbucket.org/2.0/user")
                sent = mock.call_args.kwargs["headers"]
                self.assertEqual(
                    sent.get("Authorization"),
                    "Bearer tok-example-not-real",
                    f"{verb} sent no bearer token",
                )

    def test_multipart_upload_carries_the_token(self) -> None:
        """The exact shape that was broken in the prototype: files= and no headers."""
        _, http = _reload(TOKEN_ENV)
        with patch("requests.post") as mock:
            http.request(
                "POST",
                "https://api.bitbucket.org/2.0/repositories/w/r/src",
                files={"a.txt": (None, "x")},
                data={"message": "m", "branch": "main"},
            )
            sent = mock.call_args.kwargs["headers"]
            self.assertEqual(sent.get("Authorization"), "Bearer tok-example-not-real")


class LogRedactionTest(unittest.TestCase):
    """The curl log must never contain a live credential."""

    def _log(self, env: dict[str, str], tmp: Path) -> str:
        _, http = _reload(env)
        http.config.ENABLE_REQUEST_LOGGING = True
        http.config.REQUEST_LOG_FILE = str(tmp)
        with patch("requests.get"):
            http.request("GET", "https://api.bitbucket.org/2.0/user")
        return tmp.read_text()

    def test_bearer_token_is_redacted(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            written = self._log(TOKEN_ENV, Path(d) / "log.txt")
        self.assertNotIn("tok-example-not-real", written)
        self.assertIn("[REDACTED]", written)

    def test_app_password_is_redacted(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            written = self._log(BASIC_ENV, Path(d) / "log.txt")
        self.assertNotIn("pw-example-not-real", written)
        self.assertIn("[REDACTED]", written)


if __name__ == "__main__":
    unittest.main()


class RepoDetectionTest(unittest.TestCase):
    """Workspace/slug detection from the working directory's git remote.

    This is what lets a single user-level server registration serve every
    Bitbucket checkout, so the parsing needs to cover both remote spellings.
    """

    def _detect(self, remote: str | None) -> tuple[str | None, str | None]:
        import subprocess
        import tempfile

        _reload(TOKEN_ENV)  # ensures config imports cleanly under known creds
        config = importlib.import_module("mcp_bitbucket.config")
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", d], check=True)
            if remote:
                subprocess.run(
                    ["git", "-C", d, "remote", "add", "origin", remote], check=True
                )
            return config.detect_repo(d)

    def test_ssh_remote(self) -> None:
        self.assertEqual(
            self._detect("git@bitbucket.org:myworkspace/myrepo.git"),
            ("myworkspace", "myrepo"),
        )

    def test_https_remote(self) -> None:
        self.assertEqual(
            self._detect("https://alice@bitbucket.org/myworkspace/other-repo.git"),
            ("myworkspace", "other-repo"),
        )

    def test_remote_without_git_suffix(self) -> None:
        self.assertEqual(
            self._detect("https://bitbucket.org/otherworkspace/some-repo"),
            ("otherworkspace", "some-repo"),
        )

    def test_non_bitbucket_remote_is_ignored(self) -> None:
        self.assertEqual(
            self._detect("https://github.com/example/some-repo.git"), (None, None)
        )

    def test_no_remote(self) -> None:
        self.assertEqual(self._detect(None), (None, None))

    def test_not_a_git_directory(self) -> None:
        import tempfile

        _reload(TOKEN_ENV)
        config = importlib.import_module("mcp_bitbucket.config")
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(config.detect_repo(d), (None, None))
