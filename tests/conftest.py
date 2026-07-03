import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def make_symlink(link: Path, target: Path) -> bool:
    """Create a symlink; return False if the OS/privileges disallow it."""
    try:
        link.symlink_to(target)
        return True
    except (OSError, NotImplementedError):
        return False
