"""Both auth modes must credential every request.

The prototype this was ported from applied its bearer token at each call site,
so the two multipart tools (write_file, delete_file) passed `auth` without
`headers` and sent unauthenticated requests. These tests assert the credential
is present for every verb and every content type, regardless of mode.
"""

import importlib
import json
import os
import re
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


class SettingParsingTest(unittest.TestCase):
    """Optional settings fall back to defaults; bad values are not guessed at.

    A plain `== "true"` check used to read BITBUCKET_ALLOW_DESTRUCTIVE=1 as
    false, so the switch looked applied while writes stayed blocked. These
    assert every spelling people actually type.
    """

    def _config(self, env: dict[str, str]):
        return _reload({**TOKEN_ENV, **env})[0]

    def test_truthy_spellings(self) -> None:
        for value in ("1", "true", "True", "TRUE", "yes", "y", "on", " on "):
            with self.subTest(value=value):
                config = self._config({"BITBUCKET_ALLOW_DESTRUCTIVE": value})
                self.assertTrue(config.ALLOW_DESTRUCTIVE, f"{value!r} read as false")

    def test_falsey_spellings(self) -> None:
        for value in ("0", "false", "False", "no", "n", "off"):
            with self.subTest(value=value):
                config = self._config({"BITBUCKET_BB_REQUEST_READONLY": value})
                self.assertFalse(config.BB_REQUEST_READONLY, f"{value!r} read as true")

    def test_unset_and_blank_fall_back_to_defaults(self) -> None:
        for env in ({}, {"BITBUCKET_ALLOW_DESTRUCTIVE": "   "}):
            with self.subTest(env=env):
                config = self._config(env)
                self.assertFalse(config.ALLOW_DESTRUCTIVE)
                self.assertTrue(config.BB_REQUEST_READONLY)
                self.assertFalse(config.ENABLE_REQUEST_LOGGING)
                self.assertEqual(config.MAX_PAGINATED_PAGES, 20)
                self.assertEqual(config.REQUEST_TIMEOUT_SECONDS, 120)

    def test_unparseable_flag_raises_rather_than_defaulting(self) -> None:
        with self.assertRaises(ValueError):
            self._config({"BITBUCKET_ALLOW_DESTRUCTIVE": "maybe"})

    def test_integer_settings_are_read_and_validated(self) -> None:
        config = self._config(
            {
                "BITBUCKET_MAX_PAGINATED_PAGES": "5",
                "BITBUCKET_REQUEST_TIMEOUT_SECONDS": "30",
            }
        )
        self.assertEqual(config.MAX_PAGINATED_PAGES, 5)
        self.assertEqual(config.REQUEST_TIMEOUT_SECONDS, 30)
        for bad in ("nope", "0", "-1"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self._config({"BITBUCKET_MAX_PAGINATED_PAGES": bad})

    def test_timeout_setting_reaches_the_request(self) -> None:
        _, http = _reload({**TOKEN_ENV, "BITBUCKET_REQUEST_TIMEOUT_SECONDS": "7"})
        with patch("requests.get") as mock:
            http.request("GET", "https://api.bitbucket.org/2.0/user")
            self.assertEqual(mock.call_args.kwargs["timeout"], 7)


class UnexpandedPlaceholderTest(unittest.TestCase):
    """A client that does not expand ${VAR} passes the text through verbatim.

    Sent as a bearer token that comes back as a bare 401, which tells the user
    nothing. These assert the failure names the real cause instead.
    """

    def test_credential_placeholder_raises_with_an_explanation(self) -> None:
        with self.assertRaises(ValueError) as caught:
            _reload({"BITBUCKET_TOKEN": "${BITBUCKET_TOKEN}"})
        message = str(caught.exception)
        self.assertIn("BITBUCKET_TOKEN", message)
        self.assertIn("did not expand", message)

    def test_targeting_placeholder_raises(self) -> None:
        with self.assertRaises(ValueError):
            _reload({**TOKEN_ENV, "BITBUCKET_WORKSPACE": "${MY_WORKSPACE}"})

    def test_default_form_is_also_caught(self) -> None:
        with self.assertRaises(ValueError):
            _reload({**TOKEN_ENV, "BITBUCKET_WORKSPACE": "${MY_WORKSPACE:-fallback}"})

    def test_a_real_value_containing_a_dollar_is_accepted(self) -> None:
        """Only the ${...} form is a placeholder; a bare $ is legitimate."""
        config, _ = _reload({"BITBUCKET_TOKEN": "tok$with$dollars"})
        self.assertEqual(config.BITBUCKET_TOKEN, "tok$with$dollars")


class SettingsAreDocumentedTest(unittest.TestCase):
    """Every setting the code reads must be documented and configurable.

    The README's client-configuration block is the only place a setting can
    actually be set -- MCP clients start the server with a minimal environment,
    so a variable missing from that block is unreachable no matter what the
    user exports. This test is what keeps the two from drifting.
    """

    REPO_ROOT = Path(__file__).resolve().parent.parent

    def _vars_read_by_config(self) -> set[str]:
        source = (self.REPO_ROOT / "src" / "mcp_bitbucket" / "config.py").read_text()
        return set(re.findall(r'"(BITBUCKET_[A-Z_]+)"', source))

    def test_every_setting_is_in_the_readme_table(self) -> None:
        readme = (self.REPO_ROOT / "README.md").read_text()
        # assertIn would dump the whole README into the failure message.
        undocumented = [
            name
            for name in sorted(self._vars_read_by_config())
            if f"`{name}`" not in readme
        ]
        self.assertEqual(undocumented, [], "settings missing from the README")

    def test_every_setting_is_in_the_full_config_example(self) -> None:
        readme = (self.REPO_ROOT / "README.md").read_text()
        blocks = [
            json.loads(block)
            for block in re.findall(r"```json\n(.*?)```", readme, re.S)
        ]
        configured: set[str] = set()
        for block in blocks:
            for entry in block.get("mcpServers", {}).values():
                configured |= set(entry.get("env", {}))
        # Test-only settings have no place in a user's client configuration.
        expected = self._vars_read_by_config() - {"BITBUCKET_TEST_WORKSPACE"}
        self.assertEqual(
            expected - configured,
            set(),
            "settings the code reads but no config example passes",
        )

    def test_every_setting_is_in_env_example(self) -> None:
        env_example = (self.REPO_ROOT / ".env.example").read_text()
        missing = [
            name
            for name in sorted(self._vars_read_by_config())
            if name not in env_example
        ]
        self.assertEqual(missing, [], "settings missing from .env.example")


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
