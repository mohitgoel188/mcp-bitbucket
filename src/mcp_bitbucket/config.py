"""Environment-derived configuration for the Bitbucket MCP server."""

import os
import re
import subprocess

API_BASE = "https://api.bitbucket.org/2.0"

# Every setting below is optional except the credentials: absent means "use the
# default". Values that are present but unparseable are a different matter and
# raise, because silently falling back would leave someone convinced they had
# enabled something they had not.

_TRUTHY = frozenset({"1", "true", "yes", "y", "on"})
_FALSEY = frozenset({"0", "false", "no", "n", "off"})


# An MCP client that supports ${VAR} expansion substitutes the real value
# before launching us. One that does not passes the text through verbatim, and
# a literal "${BITBUCKET_TOKEN}" sent as a bearer token comes back as an
# uninformative 401. Catching it here names the actual problem.
_UNEXPANDED = re.compile(r"\$\{[^}]*\}")


def text(name: str, default: str = "") -> str:
    """Read a string setting, rejecting an unexpanded ``${VAR}`` placeholder."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip()
    if _UNEXPANDED.search(value):
        raise ValueError(
            f"{name} is still the literal text {value!r}, so your MCP client "
            "did not expand it. Usually one of:\n"
            f"  1. {value} is not set in the environment the client was "
            "launched from (a GUI app does not inherit your terminal's shell), or\n"
            "  2. the server is configured in settings.json, whose env block is "
            "ignored for MCP servers -- use .mcp.json or the client's own config, or\n"
            "  3. a typo in the variable name.\n"
            "Set a fallback with ${NAME:-value}, or put the literal value in the "
            "`env` block instead."
        )
    return value


def flag(name: str, default: bool) -> bool:
    """Read a boolean setting, accepting the spellings people actually type.

    A plain ``== "true"`` check silently treats ``1``, ``yes`` and ``on`` as
    false, which is the worst possible outcome for a switch that gates writes:
    the setting looks applied and is not.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSEY:
        return False
    raise ValueError(f"{name} must be one of {sorted(_TRUTHY | _FALSEY)}; got {raw!r}")


def positive_int(name: str, default: int) -> int:
    """Read a positive integer setting, falling back when it is unset."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise ValueError(f"{name} must be an integer; got {raw!r}") from None
    if value < 1:
        raise ValueError(f"{name} must be at least 1; got {value}")
    return value


BITBUCKET_USERNAME = text("BITBUCKET_USERNAME") or None
BITBUCKET_APP_PASSWORD = text("BITBUCKET_APP_PASSWORD") or None
BITBUCKET_TOKEN = text("BITBUCKET_TOKEN") or None

# Atlassian is migrating Bitbucket Cloud off app passwords onto scoped API
# tokens, so a token wins when both are configured. Basic auth stays supported
# for as long as existing app passwords keep working.
if BITBUCKET_TOKEN:
    AUTH_MODE = "token"
elif BITBUCKET_USERNAME and BITBUCKET_APP_PASSWORD:
    AUTH_MODE = "app_password"
else:
    raise ValueError(
        "Missing Bitbucket credentials. Provide either:\n"
        "  1. BITBUCKET_TOKEN (scoped API token / repository access token), or\n"
        "  2. BITBUCKET_USERNAME and BITBUCKET_APP_PASSWORD (legacy app password)\n"
        "\n"
        "MCP clients start this server with a minimal environment, so exporting "
        "these in your shell is not enough -- they have to be set in the `env` "
        "block of the server entry in your client's configuration."
    )

USING_TOKEN_AUTH = AUTH_MODE == "token"


# Bitbucket remotes in either SSH or HTTPS form:
#   git@bitbucket.org:workspace/repo.git
#   https://user@bitbucket.org/workspace/repo.git
_REMOTE_PATTERN = re.compile(
    r"bitbucket\.org[:/](?P<workspace>[^/]+)/(?P<slug>[^/]+?)(?:\.git)?/?$"
)


def detect_repo(project_dir: str | None = None) -> tuple[str | None, str | None]:
    """Read the workspace and repo slug from the working directory's git remote.

    This is what makes one user-level server registration work across every
    Bitbucket checkout: the server inherits the project directory as its cwd, so
    the repo it is talking about is whichever repo the session was opened in.

    Note the server must NOT be launched via ``uv run --directory`` -- that
    changes cwd to the server's own repo and detection would find this project
    instead of the user's.
    """
    root = project_dir or text("BITBUCKET_PROJECT_DIR") or os.getcwd()
    try:
        result = subprocess.run(
            ["git", "-C", root, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None

    if result.returncode != 0:
        return None, None

    match = _REMOTE_PATTERN.search(result.stdout.strip())
    if not match:
        return None, None
    return match.group("workspace"), match.group("slug")


DETECTED_WORKSPACE, DETECTED_REPO_SLUG = detect_repo()

# Explicit env always wins; otherwise follow the checkout we were opened in.
# Deliberately no hardcoded fallback: a server that silently defaults to some
# other organisation's workspace is worse than one that asks to be configured.
# Empty is handled the same way as an empty repo slug -- http_client.api_url
# turns it into a readable error rather than an opaque 404.
DEFAULT_WORKSPACE = text("BITBUCKET_WORKSPACE") or DETECTED_WORKSPACE or ""

# Empty when the cwd is not a Bitbucket checkout; tools then require repo_slug
# explicitly, and http_client.api_url raises a readable error if it is missing.
DEFAULT_REPO_SLUG = text("BITBUCKET_REPO_SLUG") or DETECTED_REPO_SLUG or ""

REQUEST_LOG_FILE = text("BITBUCKET_REQUEST_LOG_FILE", "bitbucket_requests.log")

# Off by default. The log records request bodies verbatim -- which for
# bb_write_file means file contents -- and because the server inherits the
# client's project directory as its cwd, the file lands inside whatever repo the
# session was opened in and grows unrotated. Opt in when debugging.
ENABLE_REQUEST_LOGGING = flag("BITBUCKET_ENABLE_REQUEST_LOGGING", False)

# Guard rail for the generic proxy, on by default: bb_request can reach every
# Bitbucket endpoint, so arbitrary writes are opt-in rather than opt-out.
BB_REQUEST_READONLY = flag("BITBUCKET_BB_REQUEST_READONLY", True)

# bb_delete_repository is not registered at all unless this is set. Repository
# deletion is irreversible and rarely what an agent should be able to reach for.
ALLOW_DESTRUCTIVE = flag("BITBUCKET_ALLOW_DESTRUCTIVE", False)

# Ceiling on pages followed when paginating, and the per-request HTTP timeout.
# Both are generous defaults rather than hard limits, so a large workspace or a
# slow network is a configuration change and not a code change.
MAX_PAGINATED_PAGES = positive_int("BITBUCKET_MAX_PAGINATED_PAGES", 20)
REQUEST_TIMEOUT_SECONDS = positive_int("BITBUCKET_REQUEST_TIMEOUT_SECONDS", 120)
