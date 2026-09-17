"""Shared parameter annotations for the typed tools.

These live in one place because the `repo_slug` description is load-bearing: it is
the only thing telling the model when the default is safe to rely on, and five
copies of it drift.
"""

from typing import Annotated

from pydantic import Field

Workspace = Annotated[str, Field(description="Bitbucket workspace")]

RepoSlug = Annotated[
    str,
    Field(
        description=(
            "Repository slug/name. Defaults to the repo this server was STARTED in "
            "— detected once at startup from that directory's git remote, not per "
            "call, so it does not follow the directory you are working in now. "
            "Pass it explicitly whenever the target repo may differ from the one "
            "the session was opened in; otherwise a cross-repo call silently "
            "succeeds against the wrong repository."
        )
    ),
]

PrId = Annotated[int, Field(description="Pull request ID")]
