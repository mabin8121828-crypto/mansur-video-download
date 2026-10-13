"""Build a Skill ZIP from an explicit file list, not a directory snapshot."""

import argparse
import hashlib
from pathlib import Path
import zipfile

from distribution import NAME, RELEASE_FILES, ROOT, VERSION, checked_files
from validate_skill import validate


def package(destination: Path, root: Path = ROOT) -> Path:
    validate(root)
    files = checked_files(root, RELEASE_FILES)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{NAME}-{VERSION}.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, source in files:
            entry = zipfile.ZipInfo(f"{NAME}/{name}", date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, source.read_bytes())
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(".zip.sha256").write_text(f"{checksum}  {target.name}\n", encoding="ascii")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(ROOT / "dist"))
    args = parser.parse_args()
    print(package(Path(args.output_dir)))
