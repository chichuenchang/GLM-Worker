from __future__ import annotations

import fnmatch
from pathlib import Path


class SandboxViolation(Exception):
    """A tool call violated the workspace sandbox."""


def _denied(rel_posix: str, denylist: list[str]) -> str | None:
    parts = rel_posix.split("/")
    for raw in denylist:
        pattern = raw.rstrip("/")
        if not pattern:
            continue
        if fnmatch.fnmatch(rel_posix, pattern):
            return raw
        if any(fnmatch.fnmatch(part, pattern) for part in parts):
            return raw
    return None


def resolve_safe_path(
    rel_or_abs: str, workspace: Path, denylist: list[str] | None = None
) -> Path:
    """Resolve a worker-supplied path and confine it to the workspace.

    Returns the resolved absolute path. Raises SandboxViolation if the path
    escapes the workspace or matches a denylist pattern.
    """
    if not rel_or_abs:
        raise SandboxViolation("empty path is not allowed")
    if "\x00" in rel_or_abs:
        raise SandboxViolation("null byte in path is not allowed")

    p = Path(rel_or_abs).expanduser()
    if not p.is_absolute():
        p = workspace / p
    abs_path = p.resolve()
    ws_resolved = workspace.resolve()

    try:
        rel = abs_path.relative_to(ws_resolved)
    except ValueError as e:
        raise SandboxViolation(
            f"Path {abs_path} is outside workspace {ws_resolved}."
        ) from e

    if denylist:
        hit = _denied(rel.as_posix(), denylist)
        if hit is not None:
            raise SandboxViolation(
                f"Path {rel.as_posix()} matches denylist pattern '{hit}'."
            )

    return abs_path
