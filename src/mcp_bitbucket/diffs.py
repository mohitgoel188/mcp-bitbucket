"""Helpers for slicing unified diffs returned by the Bitbucket API."""

# Metadata lines that carry no reviewable content.
_METADATA_PREFIXES = ("index ", "new file mode", "deleted file mode")


def parse_diff_by_file(full_diff: str, include_context: bool = True) -> dict[str, str]:
    """Split a combined diff into a {path: diff_text} mapping.

    Bitbucket returns one concatenated diff per pull request; callers usually
    want it per file. When include_context is False, purely informational
    metadata lines are dropped to save tokens.
    """
    file_diffs: dict[str, str] = {}
    current_file: str | None = None
    current_diff: list[str] = []

    for line in full_diff.split("\n"):
        if line.startswith("diff --git"):
            if current_file and current_diff:
                file_diffs[current_file] = "\n".join(current_diff)

            # Format: diff --git a/path/file.py b/path/file.py
            parts = line.split(" ")
            if len(parts) >= 4:
                current_file = parts[3][2:]  # strip the 'b/' prefix
                current_diff = [line]
            else:
                current_file = None
                current_diff = []
        elif current_file:
            if not include_context and line.startswith(_METADATA_PREFIXES):
                continue
            current_diff.append(line)

    if current_file and current_diff:
        file_diffs[current_file] = "\n".join(current_diff)

    return file_diffs


def extract_file_diff(full_diff: str, file_path: str) -> list[str]:
    """Return the diff lines belonging to one file inside a combined diff."""
    lines: list[str] = []
    in_target = False

    for line in full_diff.split("\n"):
        if line.startswith("diff --git"):
            in_target = file_path in line
            if in_target:
                lines.append(line)
        elif in_target:
            # Every non-header line inside the target section (---, +++, @@,
            # context and changes alike) belongs to it.
            lines.append(line)

    return lines
