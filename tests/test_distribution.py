"""Verify public packaging and installation boundaries."""

from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from distribution import NAME, RELEASE_FILES, ROOT, SKILL_FILES
from install_skill import install
from package_skill import package


class DistributionTests(unittest.TestCase):
    def test_install_copies_only_declared_runtime_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / NAME
            install(destination)
            files = {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()}
            self.assertEqual(files, set(SKILL_FILES))

    def test_existing_install_requires_explicit_update(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / NAME
            install(destination)
            with self.assertRaises(FileExistsError):
                install(destination)

    def test_update_preserves_unrelated_local_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / NAME
            install(destination)
            private = destination / "private_settings.txt"
            private.write_text("local-only-data")
            install(destination, update=True)
            self.assertEqual(private.read_text(), "local-only-data")

    def test_zip_contains_only_declared_public_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = package(Path(temporary))
            with zipfile.ZipFile(archive) as file:
                self.assertEqual(set(file.namelist()), {f"{NAME}/{name}" for name in RELEASE_FILES})
                self.assertIsNone(file.testzip())
            self.assertTrue(archive.with_suffix(".zip.sha256").is_file())

    def test_zip_is_reproducible(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            first = package(destination / "first")
            second = package(destination / "second")
            self.assertEqual(first.read_bytes(), second.read_bytes())


if __name__ == "__main__":
    unittest.main()
