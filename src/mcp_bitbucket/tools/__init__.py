"""Tool registrations, grouped by Bitbucket API area."""

from . import generic, issues, pr_review, pullrequests, repositories, source

REGISTRARS = (
    repositories.register,
    source.register,
    issues.register,
    pullrequests.register,
    pr_review.register,
    generic.register,
)


def register_all(server) -> None:
    """Attach every tool group to the server."""
    for registrar in REGISTRARS:
        registrar(server)
