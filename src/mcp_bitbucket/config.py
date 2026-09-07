"""Environment-derived configuration for the Bitbucket MCP server."""

import os
import re
import subprocess

API_BASE = "https://api.bitbucket.org/2.0"

BITBUCKET_USERNAME = os.getenv("BITBUCKET_USERNAME")
BITBUCKET_APP_PASSWORD = os.getenv("BITBUCKET_APP_PASSWORD")
BITBUCKET_TOKEN = os.getenv("BITBUCKET_TOKEN")

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
        "  2. BITBUCKET_USERNAME and BITBUCKET_APP_PASSWORD (legacy app password)"
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
    root = project_dir or os.getenv("BITBUCKET_PROJECT_DIR") or os.getcwd()
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
DEFAULT_WORKSPACE = os.getenv("BITBUCKET_WORKSPACE") or DETECTED_WORKSPACE or ""

# Empty when the cwd is not a Bitbucket checkout; tools then require repo_slug
# explicitly, and http_client.api_url raises a readable error if it is missing.
DEFAULT_REPO_SLUG = os.getenv("BITBUCKET_REPO_SLUG") or DETECTED_REPO_SLUG or ""

REQUEST_LOG_FILE = os.getenv("BITBUCKET_REQUEST_LOG_FILE", "bitbucket_requests.log")

# Off by default. The log records request bodies verbatim -- which for
# bb_write_file means file contents -- and because the server inherits the
# client's project directory as its cwd, the file lands inside whatever repo the
# session was opened in and grows unrotated. Opt in when debugging.
ENABLE_REQUEST_LOGGING = (
    os.getenv("BITBUCKET_ENABLE_REQUEST_LOGGING", "false").lower() == "true"
)

# Guard rail for the generic proxy, on by default: bb_request can reach every
# Bitbucket endpoint, so arbitrary writes are opt-in rather than opt-out.
BB_REQUEST_READONLY = (
    os.getenv("BITBUCKET_BB_REQUEST_READONLY", "true").lower() == "true"
)

# bb_delete_repository is not registered at all unless this is set. Repository
# deletion is irreversible and rarely what an agent should be able to reach for.
ALLOW_DESTRUCTIVE = os.getenv("BITBUCKET_ALLOW_DESTRUCTIVE", "false").lower() == "true"

MAX_PAGINATED_PAGES = 20
