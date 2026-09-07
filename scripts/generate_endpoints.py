"""Generate the bundled Bitbucket endpoint index from Atlassian's official OpenAPI spec.

The index powers the ``bb_list_endpoints`` tool, which lets the model discover the
correct path/params for any Bitbucket Cloud REST endpoint before calling
``bb_request``. Regenerate whenever Atlassian publishes spec changes:

    uv run --directory . python scripts/generate_endpoints.py

Source of truth is the same spec that renders
https://developer.atlassian.com/cloud/bitbucket/rest/intro/ -- so the index can
never drift from the published docs.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

# The OpenAPI 3 spec behind developer.atlassian.com. The swagger 2.0 mirror at
# api.bitbucket.org/swagger.json carries the same 294 operations and is kept as a
# fallback in case the docs CDN URL moves.
SPEC_URL = "https://dac-static.atlassian.com/cloud/bitbucket/swagger.v3.json"
SPEC_URL_FALLBACK = "https://api.bitbucket.org/swagger.json"

DOCS_BASE = "https://developer.atlassian.com/cloud/bitbucket/rest"
HTTP_METHODS = ("get", "post", "put", "delete", "patch")
MAX_DESCRIPTION_CHARS = 240

# Atlassian revises these specs on a docs-release cadence, so a day-long TTL is
# generous while still keeping repeat generator runs entirely offline.
DEFAULT_MAX_AGE_SECONDS = 24 * 60 * 60


def cache_paths(url: str, cache_dir: Path) -> tuple[Path, Path]:
    """Return the (body, etag) cache file pair for one spec URL."""
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"spec-{key}.json", cache_dir / f"spec-{key}.etag"


def fetch_one(
    url: str,
    cache_dir: Path,
    force: bool,
    max_age_seconds: int,
) -> dict[str, Any] | None:
    """Fetch a spec URL, preferring a warm local cache over the network.

    Two layers, because the validators are not dependable: a TTL gate that skips
    the request entirely while the cached body is fresh, then ETag revalidation.
    The dac-static CDN omits its ETag on the gzip variant it serves to requests
    (it sets ``Vary: Accept-Encoding``), so only the api.bitbucket.org mirror can
    actually answer 304 -- the TTL is what keeps repeat runs off the wire.
    """
    body_path, etag_path = cache_paths(url, cache_dir)
    cached_etag = (
        etag_path.read_text(encoding="utf-8").strip() if etag_path.exists() else ""
    )

    if body_path.exists() and not force:
        age = time.time() - body_path.stat().st_mtime
        if age < max_age_seconds:
            print(f"spec cache fresh ({int(age)}s old, ttl {max_age_seconds}s): {url}")
            return json.loads(body_path.read_text(encoding="utf-8"))

    headers: dict[str, str] = {}
    if cached_etag and body_path.exists() and not force:
        headers["If-None-Match"] = cached_etag

    try:
        response = requests.get(url, timeout=60, headers=headers)
    except requests.RequestException as exc:
        if body_path.exists():
            print(
                f"warning: {url} unreachable ({exc}); using cached spec",
                file=sys.stderr,
            )
            return json.loads(body_path.read_text(encoding="utf-8"))
        print(f"warning: could not fetch {url}: {exc}", file=sys.stderr)
        return None

    if response.status_code == 304 and body_path.exists():
        print(f"spec unchanged (304, cached): {url}")
        return json.loads(body_path.read_text(encoding="utf-8"))

    if not response.ok:
        if body_path.exists():
            print(
                f"warning: {url} returned {response.status_code}; using cached spec",
                file=sys.stderr,
            )
            return json.loads(body_path.read_text(encoding="utf-8"))
        print(f"warning: {url} returned {response.status_code}", file=sys.stderr)
        return None

    try:
        spec = response.json()
    except ValueError as exc:
        print(f"warning: {url} did not return JSON: {exc}", file=sys.stderr)
        return None

    cache_dir.mkdir(parents=True, exist_ok=True)
    body_path.write_text(json.dumps(spec), encoding="utf-8")
    os.utime(body_path, None)
    etag = response.headers.get("ETag", "")
    if etag:
        etag_path.write_text(etag, encoding="utf-8")
    print(f"spec downloaded ({len(response.content) // 1024} KB): {url}")
    return spec


def fetch_spec(
    url: str,
    cache_dir: Path,
    force: bool,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """Download an OpenAPI/Swagger spec, falling back to the api.bitbucket.org mirror."""
    for candidate in (url, SPEC_URL_FALLBACK):
        spec = fetch_one(candidate, cache_dir, force, max_age_seconds)
        if spec is not None:
            return spec
    raise SystemExit("error: could not fetch the Bitbucket spec from any known URL")


def trim(text: str | None) -> str:
    """Collapse whitespace and clip prose so the bundled index stays small."""
    if not text:
        return ""
    flat = re.sub(r"\s+", " ", text).strip()
    if len(flat) <= MAX_DESCRIPTION_CHARS:
        return flat
    return flat[: MAX_DESCRIPTION_CHARS - 1].rstrip() + "…"


def tag_slug(tag: str) -> str:
    """Slugify a spec tag into its docs ``api-group-*`` path segment."""
    return re.sub(r"[^a-z0-9]+", "-", tag.lower()).strip("-")


def doc_url(path: str, method: str, tag: str) -> str:
    """Build the developer.atlassian.com deep link for one operation.

    Atlassian's docs anchors are a deterministic slug of the path with braces
    stripped, so the link is derivable rather than scraped.
    """
    anchor = re.sub(r"[^a-z0-9]+", "-", path.lower().replace("{", "").replace("}", ""))
    anchor = anchor.strip("-")
    return f"{DOCS_BASE}/api-group-{tag_slug(tag)}/#api-{anchor}-{method.lower()}"


def slug_id(summary: str, method: str, path: str) -> str:
    """Build a stable, searchable id for an operation.

    Two thirds of Bitbucket's operations ship without an ``operationId``, so we
    derive one from the summary instead of leaking a raw method+path into the id.
    """
    words = re.sub(r"[^a-z0-9]+", " ", summary.lower()).split()
    if not words:
        return f"{method.lower()}-{re.sub(r'[^a-z0-9]+', '-', path.lower()).strip('-')}"
    head, *rest = words
    return head + "".join(word.capitalize() for word in rest)


def resolve_ref(node: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Follow a local ``$ref`` one hop; return the node unchanged if it is inline."""
    ref = node.get("$ref")
    if not ref or not ref.startswith("#/"):
        return node
    target: Any = spec
    for part in ref[2:].split("/"):
        if not isinstance(target, dict) or part not in target:
            return node
        target = target[part]
    return target if isinstance(target, dict) else node


def param_type(param: dict[str, Any]) -> str:
    """Read a parameter's scalar type from either OpenAPI 3 or Swagger 2 shape."""
    schema = param.get("schema")
    if isinstance(schema, dict) and schema.get("type"):
        return str(schema["type"])
    return str(param.get("type", "string"))


def collect_params(
    operation: dict[str, Any],
    shared: list[dict[str, Any]],
    spec: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Split an operation's parameters into path names and query descriptors."""
    path_params: list[str] = []
    query_params: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for raw in [*shared, *operation.get("parameters", [])]:
        param = resolve_ref(raw, spec) if isinstance(raw, dict) else {}
        name = param.get("name")
        location = param.get("in")
        if not name or (name, location) in seen:
            continue
        seen.add((name, location))

        if location == "path":
            path_params.append(name)
        elif location == "query":
            query_params.append(
                {
                    "name": name,
                    "type": param_type(param),
                    "required": bool(param.get("required", False)),
                    "description": trim(param.get("description")),
                }
            )

    return path_params, query_params


def has_body(operation: dict[str, Any], spec: dict[str, Any]) -> bool:
    """Report whether an operation accepts a request body (either spec dialect)."""
    if operation.get("requestBody"):
        return True
    for raw in operation.get("parameters", []):
        param = resolve_ref(raw, spec) if isinstance(raw, dict) else {}
        if param.get("in") == "body":
            return True
    return False


def build_index(spec: dict[str, Any]) -> dict[str, Any]:
    """Flatten a Bitbucket spec into the compact operation index we bundle."""
    operations: list[dict[str, Any]] = []

    for path, path_item in sorted(spec.get("paths", {}).items()):
        if not isinstance(path_item, dict):
            continue
        shared = [p for p in path_item.get("parameters", []) if isinstance(p, dict)]

        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue

            tags = operation.get("tags") or ["Other"]
            tag = tags[0]
            path_params, query_params = collect_params(operation, shared, spec)
            summary = trim(operation.get("summary")) or trim(
                operation.get("description")
            )

            operations.append(
                {
                    "id": operation.get("operationId")
                    or slug_id(summary, method, path),
                    "method": method.upper(),
                    "path": path,
                    "tag": tag,
                    "summary": summary,
                    "path_params": path_params,
                    "query_params": query_params,
                    "body": has_body(operation, spec),
                    "doc_url": doc_url(path, method, tag),
                }
            )

    operations.sort(key=lambda op: (op["tag"], op["path"], op["method"]))

    counts: dict[str, int] = {}
    for op in operations:
        seen_count = counts.get(op["id"], 0) + 1
        counts[op["id"]] = seen_count
        if seen_count > 1:
            op["id"] = f"{op['id']}{seen_count}"

    return {
        "source": spec.get("info", {}).get("title", "Bitbucket API"),
        "spec_version": spec.get("openapi") or spec.get("swagger") or "unknown",
        "api_version": spec.get("info", {}).get("version", "2.0"),
        "base_url": "https://api.bitbucket.org/2.0",
        "docs": f"{DOCS_BASE}/intro/",
        "operation_count": len(operations),
        "tags": sorted({op["tag"] for op in operations}),
        "operations": operations,
    }


def main() -> None:
    """Fetch the spec and write ``src/mcp_bitbucket/endpoints.json``."""
    default_out = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "mcp_bitbucket"
        / "endpoints.json"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec-url", default=SPEC_URL, help="OpenAPI spec URL to read")
    parser.add_argument(
        "--spec-file", help="Read a local spec file instead of fetching"
    )
    parser.add_argument(
        "--out", type=Path, default=default_out, help="Output JSON path"
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / ".spec-cache",
        help="Where to cache the downloaded spec and its ETag",
    )
    parser.add_argument(
        "--max-age",
        type=int,
        default=DEFAULT_MAX_AGE_SECONDS,
        help="Reuse the cached spec without any network call while it is younger than this (seconds)",
    )
    parser.add_argument(
        "--force", action="store_true", help="Ignore the cache and refetch"
    )
    args = parser.parse_args()

    if args.spec_file:
        spec = json.loads(Path(args.spec_file).read_text(encoding="utf-8"))
    else:
        spec = fetch_spec(args.spec_url, args.cache_dir, args.force, args.max_age)

    index = build_index(spec)
    if not index["operations"]:
        raise SystemExit(
            "error: spec produced zero operations; refusing to write index"
        )

    payload = json.dumps(index, indent=1, sort_keys=False)
    if args.out.exists() and args.out.read_text(encoding="utf-8") == payload:
        print(f"{args.out} already up to date ({index['operation_count']} operations)")
        return

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(payload, encoding="utf-8")

    print(
        f"wrote {args.out} "
        f"({index['operation_count']} operations, {len(index['tags'])} tags, "
        f"{args.out.stat().st_size // 1024} KB)"
    )


if __name__ == "__main__":
    main()
