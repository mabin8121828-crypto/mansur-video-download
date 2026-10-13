"""Explicit files that may be installed or distributed."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "mansur-video-download"
VERSION = "0.1.1"
SKILL_FILES = (
    "SKILL.md", "agents/openai.yaml", "scripts/download_media.py",
    "references/troubleshooting.md", "requirements.txt", "LICENSE",
)
RELEASE_FILES = SKILL_FILES + (
    "README.md", "scripts/install_skill.py", "scripts/package_skill.py",
    "scripts/validate_skill.py", "scripts/distribution.py",
    "requirements-dev.txt", "tests/test_download_media.py", "tests/test_distribution.py",
)


def checked_files(root: Path, names: tuple[str, ...]) -> list[tuple[str, Path]]:
    files = []
    for name in names:
        path = root / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Missing or unsafe distribution file: {name}")
        files.append((name, path))
    return files
