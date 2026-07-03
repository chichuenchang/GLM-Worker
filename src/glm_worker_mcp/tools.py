from __future__ import annotations

import glob as _glob
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .safety import SandboxViolation, _denied, resolve_safe_path

MAX_TOOL_OUTPUT = 50_000
MAX_WRITE_BYTES = 5_000_000
MAX_READ_BYTES = 10_000_000
# glob() ignores dot-entries by default; include_hidden only exists on 3.11+.
# On 3.10 hidden files stay invisible to Glob/Grep (Read/Write on explicit
# dot-paths still work).
_GLOB_KWARGS = {"include_hidden": True} if sys.version_info >= (3, 11) else {}


@dataclass
class ChangeTracker:
    changes: dict = field(default_factory=dict)  # path -> {"action", "count"}

    def record(self, path: str, action: str, count: int) -> None:
        existing = self.changes.get(path)
        if existing and existing["action"] == "written":
            # File was created this run. A later re-write refreshes the line
            # count; a later edit keeps it labelled "written" with that count.
            if action == "written":
                existing["count"] = count
            return
        if existing and existing["action"] == "edited" and action == "edited":
            existing["count"] += count
            return
        self.changes[path] = {"action": action, "count": count}

    def manifest(self) -> list[dict]:
        return [{"path": p, **v} for p, v in self.changes.items()]


def _truncate(text: str) -> str:
    if len(text) > MAX_TOOL_OUTPUT:
        return text[:MAX_TOOL_OUTPUT] + (
            f"\n... [truncated, total {len(text)} chars, showing first {MAX_TOOL_OUTPUT}]"
        )
    return text


def _is_binary(path: Path, sniff: int = 8192) -> bool:
    try:
        with path.open("rb") as f:
            return b"\x00" in f.read(sniff)
    except Exception:
        return False


def _line_count(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _safe_match(
    path_str: str, ws_resolved: Path, denylist: list | None = None
) -> Path | None:
    """Confine a glob match to the workspace and the denylist; None = filtered."""
    try:
        p = Path(path_str).resolve()
        rel = p.relative_to(ws_resolved)
    except (ValueError, OSError):
        return None
    if denylist and _denied(rel.as_posix(), denylist):
        return None
    return p


def _execute_read(args, workspace, denylist, tracker):
    path = args.get("path", "")
    if not path:
        return "ERROR: missing required 'path' argument"
    try:
        abs_path = resolve_safe_path(path, workspace, denylist)
    except SandboxViolation as e:
        return f"ERROR: {e}"
    if not abs_path.exists():
        return f"ERROR: file not found: {path}"
    if not abs_path.is_file():
        return f"ERROR: not a file: {path}"
    if _is_binary(abs_path):
        return f"ERROR: {path} appears to be binary; refusing to read as text."
    try:
        size = abs_path.stat().st_size
        if size > MAX_READ_BYTES:
            return f"ERROR: {path} is too large to read ({size} bytes; cap {MAX_READ_BYTES})."
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"ERROR: failed to read {path}: {e}"
    offset = int(args.get("offset", 0) or 0)
    limit = args.get("limit")
    if offset or limit:
        lines = text.splitlines()
        end = offset + int(limit) if limit else len(lines)
        text = "\n".join(lines[offset:end])
    return _truncate(text)


def _execute_write(args, workspace, denylist, tracker):
    path = args.get("path", "")
    content = args.get("content", "")
    if not path:
        return "ERROR: missing required 'path' argument"
    if not isinstance(content, str):
        return "ERROR: 'content' must be a string"
    if len(content.encode("utf-8", errors="replace")) > MAX_WRITE_BYTES:
        return f"ERROR: content exceeds {MAX_WRITE_BYTES} bytes; split into smaller writes."
    try:
        abs_path = resolve_safe_path(path, workspace, denylist)
    except SandboxViolation as e:
        return f"ERROR: {e}"
    try:
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8")
    except Exception as e:
        return f"ERROR: failed to write {path}: {e}"
    tracker.record(path, "written", _line_count(content))
    return f"OK: wrote {len(content)} chars to {path}"


def _execute_edit(args, workspace, denylist, tracker):
    path = args.get("path", "")
    old = args.get("old_string", "")
    new = args.get("new_string", "")
    replace_all = bool(args.get("replace_all", False))
    if not path or old == "":
        return "ERROR: missing required 'path' or 'old_string'"
    try:
        abs_path = resolve_safe_path(path, workspace, denylist)
    except SandboxViolation as e:
        return f"ERROR: {e}"
    if not abs_path.exists():
        return f"ERROR: file not found: {path}"
    if _is_binary(abs_path):
        return f"ERROR: {path} appears to be binary; refusing to edit as text."
    try:
        size = abs_path.stat().st_size
        if size > MAX_READ_BYTES:
            return f"ERROR: {path} is too large to edit ({size} bytes; cap {MAX_READ_BYTES})."
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"ERROR: failed to read {path}: {e}"
    count = text.count(old)
    if count == 0:
        return f"ERROR: old_string not found in {path}"
    if count > 1 and not replace_all:
        return (
            f"ERROR: old_string appears {count} times in {path}. "
            f"Use replace_all=true or add surrounding context to make it unique."
        )
    new_text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    try:
        abs_path.write_text(new_text, encoding="utf-8")
    except Exception as e:
        return f"ERROR: failed to write {path}: {e}"
    n = count if replace_all else 1
    tracker.record(path, "edited", n)
    return f"OK: replaced {n} occurrence(s) in {path}"


def _execute_glob(args, workspace, denylist, tracker):
    pattern = args.get("pattern", "")
    if not pattern:
        return "ERROR: missing required 'pattern' argument"
    base = args.get("path", "")
    try:
        base_path = resolve_safe_path(base, workspace, denylist) if base else workspace.resolve()
    except SandboxViolation as e:
        return f"ERROR: {e}"
    ws_resolved = workspace.resolve()
    raw = sorted(_glob.glob(str(base_path / pattern), recursive=True, **_GLOB_KWARGS))
    safe, rejected = [], 0
    for m in raw:
        p = _safe_match(m, ws_resolved, denylist)
        if p is None:
            rejected += 1
            continue
        safe.append(p)
    rel = []
    for p in safe[:500]:
        try:
            rel.append(str(p.relative_to(ws_resolved)))
        except ValueError:
            rel.append(str(p))
    summary = f"Found {len(safe)} match(es)"
    if len(safe) > 500:
        summary += " (showing first 500)"
    if rejected:
        summary += f" [{rejected} hidden: outside workspace or denylisted]"
    return summary + ":\n" + "\n".join(rel)


def _execute_grep(args, workspace, denylist, tracker):
    pattern = args.get("pattern", "")
    if not pattern:
        return "ERROR: missing required 'pattern' argument"
    base = args.get("path", "")
    file_glob = args.get("glob", "**/*")
    try:
        max_matches = max(1, min(int(args.get("max_matches", 100)), 1000))
    except (TypeError, ValueError):
        max_matches = 100
    try:
        base_path = resolve_safe_path(base, workspace, denylist) if base else workspace.resolve()
    except SandboxViolation as e:
        return f"ERROR: {e}"
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"ERROR: invalid regex: {e}"
    ws_resolved = workspace.resolve()
    results = []
    for fp in _glob.iglob(str(base_path / file_glob), recursive=True, **_GLOB_KWARGS):
        p = _safe_match(fp, ws_resolved, denylist)
        if p is None or not p.is_file() or _is_binary(p):
            continue
        try:
            if p.stat().st_size > MAX_READ_BYTES:
                continue
            rel = p.relative_to(ws_resolved)
            for lineno, line in enumerate(
                p.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if regex.search(line):
                    results.append(f"{rel}:{lineno}: {line}")
                    if len(results) >= max_matches:
                        break
        except Exception:
            continue
        if len(results) >= max_matches:
            break
    if not results:
        return f"No matches found for pattern: {pattern}"
    header = f"Found {len(results)} match(es)"
    if len(results) >= max_matches:
        header += f" (limit {max_matches} reached)"
    return header + ":\n" + "\n".join(results)


TOOL_REGISTRY = {
    "Read": _execute_read,
    "Write": _execute_write,
    "Edit": _execute_edit,
    "Glob": _execute_glob,
    "Grep": _execute_grep,
}


def execute_tool(name, args, workspace, denylist, tracker):
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return f"ERROR: unknown tool '{name}'. Available: {list(TOOL_REGISTRY)}"
    return fn(args, workspace, denylist, tracker)


_SCHEMAS = {
    "Read": {
        "name": "Read",
        "description": "Read a UTF-8 text file. Use before editing.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the workspace."},
                "offset": {"type": "integer", "description": "Start line (0-indexed). Optional."},
                "limit": {"type": "integer", "description": "Number of lines. Optional."},
            },
            "required": ["path"],
        },
    },
    "Write": {
        "name": "Write",
        "description": "Write/overwrite a file (UTF-8). Creates parent dirs. Max 5MB.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    "Edit": {
        "name": "Edit",
        "description": "Exact string replace. Fails on multiple matches unless replace_all=true.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    "Glob": {
        "name": "Glob",
        "description": "Find files by glob pattern (e.g. **/*.py). Paths outside the workspace are filtered.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string", "description": "Base dir, relative. Optional."},
            },
            "required": ["pattern"],
        },
    },
    "Grep": {
        "name": "Grep",
        "description": "Regex search file contents. Binary files skipped.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "glob": {"type": "string", "description": "File glob filter (default **/*)."},
                "max_matches": {"type": "integer", "description": "Default 100, cap 1000."},
            },
            "required": ["pattern"],
        },
    },
}


def build_tool_schemas(allowed):
    return [
        {"type": "function", "function": _SCHEMAS[name]}
        for name in allowed
        if name in _SCHEMAS
    ]
