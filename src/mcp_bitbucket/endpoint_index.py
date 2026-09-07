"""Access to the bundled index of every Bitbucket Cloud REST endpoint.

The index is produced by ``scripts/generate_endpoints.py`` from Atlassian's
official OpenAPI spec, so it cannot drift from the published documentation.
"""

import json
from pathlib import Path
from typing import Any

INDEX_PATH = Path(__file__).with_name("endpoints.json")

_cache: dict[str, Any] = {}


def load() -> dict[str, Any]:
    """Load the index, caching it per-process and re-reading if regenerated.

    The file is ~160 KB of JSON, so it is parsed once and reused for the life of
    the server. Keying the cache on mtime means a regenerated index is picked up
    without restarting.
    """
    if not INDEX_PATH.exists():
        return {"operations": [], "tags": [], "operation_count": 0, "missing": True}

    mtime = INDEX_PATH.stat().st_mtime
    if _cache.get("mtime") == mtime:
        return _cache["index"]

    with open(INDEX_PATH, encoding="utf-8") as handle:
        index = json.load(handle)

    _cache.clear()
    _cache.update({"mtime": mtime, "index": index})
    return index


def search(
    index: dict[str, Any],
    terms: str = "",
    tag: str = "",
    method: str = "",
) -> list[dict[str, Any]]:
    """Filter operations by free-text terms, tag and HTTP method.

    Every whitespace-separated term must appear somewhere in the entry, so a
    query like "pull request comment" narrows correctly instead of matching
    anything mentioning "comment".
    """
    wanted = [t for t in (terms or "").lower().split() if t]
    tag_filter = (tag or "").lower()
    method_filter = (method or "").upper()
    matches: list[dict[str, Any]] = []

    for operation in index.get("operations", []):
        if method_filter and operation["method"] != method_filter:
            continue
        if tag_filter and tag_filter not in operation["tag"].lower():
            continue
        haystack = " ".join(
            [
                operation["id"],
                operation["method"],
                operation["path"],
                operation["tag"],
                operation.get("summary", ""),
            ]
        ).lower()
        if all(term in haystack for term in wanted):
            matches.append(operation)

    return matches


def summarize(operation: dict[str, Any], verbose: bool) -> str:
    """Render one index entry as a compact, model-readable block."""
    line = f"{operation['method']:<6} {operation['path']}"
    if operation.get("summary"):
        line += f"\n       {operation['summary']}"
    if not verbose:
        return line

    if operation.get("path_params"):
        line += f"\n       path params: {', '.join(operation['path_params'])}"
    query_params = operation.get("query_params") or []
    if query_params:
        rendered = ", ".join(
            f"{q['name']}{'*' if q.get('required') else ''}:{q.get('type', 'string')}"
            for q in query_params
        )
        line += f"\n       query params: {rendered}"
    if operation.get("body"):
        line += "\n       accepts a JSON request body"
    if operation.get("doc_url"):
        line += f"\n       docs: {operation['doc_url']}"
    return line
