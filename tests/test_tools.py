import pytest
from pathlib import Path

from glm_worker_mcp.tools import ChangeTracker, execute_tool


def run(name, args, ws, denylist=None):
    tracker = ChangeTracker()
    out = execute_tool(name, args, ws, denylist or [], tracker)
    return out, tracker


def test_write_creates_and_tracks(tmp_path):
    out, tracker = run("Write", {"path": "a/b.txt", "content": "x\ny\n"}, tmp_path)
    assert out.startswith("OK")
    assert (tmp_path / "a" / "b.txt").read_text() == "x\ny\n"
    assert tracker.manifest() == [{"path": "a/b.txt", "action": "written", "count": 2}]


def test_write_too_big_rejected(tmp_path):
    out, _ = run("Write", {"path": "big.txt", "content": "a" * 5_000_001}, tmp_path)
    assert "exceeds" in out


def test_read_happy(tmp_path):
    (tmp_path / "f.txt").write_text("hello", encoding="utf-8")
    out, _ = run("Read", {"path": "f.txt"}, tmp_path)
    assert out == "hello"


def test_read_missing(tmp_path):
    out, _ = run("Read", {"path": "nope.txt"}, tmp_path)
    assert "file not found" in out


def test_read_binary_refused(tmp_path):
    (tmp_path / "b.bin").write_bytes(b"\x00\x01\x02")
    out, _ = run("Read", {"path": "b.bin"}, tmp_path)
    assert "binary" in out


def test_read_offset_limit(tmp_path):
    (tmp_path / "f.txt").write_text("l0\nl1\nl2\nl3", encoding="utf-8")
    out, _ = run("Read", {"path": "f.txt", "offset": 1, "limit": 2}, tmp_path)
    assert out == "l1\nl2"


def test_read_outside_blocked(tmp_path):
    out, _ = run("Read", {"path": "../x"}, tmp_path)
    assert out.startswith("ERROR")


def test_edit_single(tmp_path):
    (tmp_path / "f.txt").write_text("foo bar", encoding="utf-8")
    out, tracker = run("Edit", {"path": "f.txt", "old_string": "foo", "new_string": "baz"}, tmp_path)
    assert out.startswith("OK")
    assert (tmp_path / "f.txt").read_text() == "baz bar"
    assert tracker.manifest()[0]["action"] == "edited"


def test_edit_not_found(tmp_path):
    (tmp_path / "f.txt").write_text("foo", encoding="utf-8")
    out, _ = run("Edit", {"path": "f.txt", "old_string": "zzz", "new_string": "x"}, tmp_path)
    assert "not found" in out


def test_edit_multi_without_replace_all(tmp_path):
    (tmp_path / "f.txt").write_text("a a a", encoding="utf-8")
    out, _ = run("Edit", {"path": "f.txt", "old_string": "a", "new_string": "b"}, tmp_path)
    assert "appears 3 times" in out


def test_edit_replace_all(tmp_path):
    (tmp_path / "f.txt").write_text("a a a", encoding="utf-8")
    out, _ = run("Edit", {"path": "f.txt", "old_string": "a", "new_string": "b", "replace_all": True}, tmp_path)
    assert (tmp_path / "f.txt").read_text() == "b b b"


def test_write_then_edit_stays_written_with_line_count(tmp_path):
    tracker = ChangeTracker()
    execute_tool("Write", {"path": "f.txt", "content": "l0\nl1\nl2\n"}, tmp_path, [], tracker)
    execute_tool("Edit", {"path": "f.txt", "old_string": "l1", "new_string": "X"}, tmp_path, [], tracker)
    # created this run -> reported as written, count is the write's line count (3), not edit occurrences (1)
    assert tracker.manifest() == [{"path": "f.txt", "action": "written", "count": 3}]
    assert (tmp_path / "f.txt").read_text() == "l0\nX\nl2\n"


def test_glob_matches(tmp_path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("y")
    out, _ = run("Glob", {"pattern": "**/*.py"}, tmp_path)
    assert "a.py" in out and "b.py" in out.replace("\\", "/")


def test_grep_matches(tmp_path):
    (tmp_path / "f.txt").write_text("alpha\nbeta\nalgae", encoding="utf-8")
    out, _ = run("Grep", {"pattern": "^al"}, tmp_path)
    assert "alpha" in out and "algae" in out and "beta" not in out


def test_grep_no_match(tmp_path):
    (tmp_path / "f.txt").write_text("xyz", encoding="utf-8")
    out, _ = run("Grep", {"pattern": "qqq"}, tmp_path)
    assert "No matches" in out


def test_grep_bad_regex(tmp_path):
    out, _ = run("Grep", {"pattern": "([", }, tmp_path)
    assert "invalid regex" in out


def test_schemas_only_allowed_and_no_bash():
    from glm_worker_mcp.tools import build_tool_schemas

    schemas = build_tool_schemas(["Read", "Write", "Edit", "Glob", "Grep", "Bash"])
    names = {s["function"]["name"] for s in schemas}
    assert names == {"Read", "Write", "Edit", "Glob", "Grep"}  # Bash never present
    for s in schemas:
        assert s["type"] == "function"
        assert "parameters" in s["function"]
