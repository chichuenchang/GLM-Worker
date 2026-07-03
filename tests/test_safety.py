import pytest
from pathlib import Path

from glm_worker_mcp.safety import resolve_safe_path, SandboxViolation
from tests.conftest import make_symlink


def test_relative_path_resolves_inside(tmp_path):
    p = resolve_safe_path("a/b.txt", tmp_path)
    assert p == (tmp_path / "a" / "b.txt").resolve()


def test_absolute_inside_ok(tmp_path):
    target = tmp_path / "x.txt"
    assert resolve_safe_path(str(target), tmp_path) == target.resolve()


def test_dotdot_escape_rejected(tmp_path):
    with pytest.raises(SandboxViolation):
        resolve_safe_path("../outside.txt", tmp_path)


def test_absolute_outside_rejected(tmp_path):
    with pytest.raises(SandboxViolation):
        resolve_safe_path(str(tmp_path.parent / "evil.txt"), tmp_path)


def test_empty_rejected(tmp_path):
    with pytest.raises(SandboxViolation):
        resolve_safe_path("", tmp_path)


def test_null_byte_rejected(tmp_path):
    with pytest.raises(SandboxViolation):
        resolve_safe_path("a\x00b", tmp_path)


def test_symlink_out_rejected(tmp_path):
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("s")
    link = tmp_path / "link.txt"
    if not make_symlink(link, outside):
        pytest.skip("symlinks not permitted on this host")
    with pytest.raises(SandboxViolation):
        resolve_safe_path("link.txt", tmp_path)


def test_denylist_blocks_matching(tmp_path):
    with pytest.raises(SandboxViolation):
        resolve_safe_path(".env", tmp_path, denylist=[".env*"])


def test_denylist_blocks_segment(tmp_path):
    with pytest.raises(SandboxViolation):
        resolve_safe_path(".git/config", tmp_path, denylist=[".git"])


def test_denylist_empty_allows(tmp_path):
    assert resolve_safe_path(".env", tmp_path, denylist=[]) == (tmp_path / ".env").resolve()
