"""Install only the declared Skill files; preserve unrelated target files."""

import argparse
import os
from pathlib import Path
import shutil
import tempfile

from distribution import NAME, ROOT, SKILL_FILES, checked_files


def install(destination: Path, update: bool = False, source: Path = ROOT) -> Path:
    destination = destination.expanduser().absolute()
    files = checked_files(source, SKILL_FILES)
    if destination.is_symlink():
        raise ValueError("Refusing to install through a symlinked Skill directory")
    if destination.exists() and not update:
        raise FileExistsError("Skill directory exists; review it before using --update")
    for name, _ in files:
        target = destination / name
        if target.is_symlink() or any(p.is_symlink() for p in target.parents if p != destination.parent):
            raise ValueError("Refusing to update through a symlink")
        if target.exists() and not target.is_file():
            raise ValueError(f"Target is not a regular file: {name}")
    destination.mkdir(parents=True, exist_ok=True)
    for name, path in files:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".skill_install_", dir=target.parent)
        os.close(descriptor)
        try:
            shutil.copyfile(path, temporary)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", default=str(Path.home() / ".agents" / "skills" / NAME))
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    print(f"Installed {NAME} to {install(Path(args.dest), args.update)}")


if __name__ == "__main__":
    main()
