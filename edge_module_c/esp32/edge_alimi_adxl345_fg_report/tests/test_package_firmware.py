"""Packaging contracts only; these tests do not flash or validate hardware."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "package_firmware.py"
SPEC = importlib.util.spec_from_file_location("package_firmware", MODULE_PATH)
pack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pack)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.files = {name: (name + " payload").encode() for name in (
            "bootloader.bin", "partitions.bin", "boot_app0.bin", "firmware.bin")}
        self.offsets = {"bootloader.bin": "0x1000", "partitions.bin": "0x8000",
                        "boot_app0.bin": "0xe000", "firmware.bin": "0x10000"}
        self.manifest = {"schema_version": 1, "board": "esp32dev", "framework": ["arduino"],
                         "source": {"dirty": False}, "release": True, "flash": {"size": "4MB"},
                         "files": {name: {"sha256": pack.sha256(data), "bytes": len(data),
                                          "offset": self.offsets[name]}
                                   for name, data in self.files.items()}}

    def tearDown(self):
        self.directory.cleanup()

    def archive(self):
        path = self.root / "package.zip"
        pack.write_archive(path, {**self.files, "manifest.json": pack.json_bytes(self.manifest)})
        return path

    def test_roundtrip_and_deterministic_zip(self):
        first = self.archive()
        self.assertEqual(pack.verify_archive(first), self.manifest)
        second = self.root / "other.zip"
        pack.write_archive(second, {"manifest.json": pack.json_bytes(self.manifest), **self.files})
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_binary_corruption(self):
        self.files["firmware.bin"] = b"changed"
        with self.assertRaisesRegex(ValueError, "Hash/size"):
            pack.verify_archive(self.archive())

    def test_extra_member_rejected(self):
        self.files["unlisted.bin"] = b"not permitted"
        with self.assertRaisesRegex(ValueError, "Unexpected"):
            pack.verify_archive(self.archive())

    def test_false_release_rejected(self):
        self.manifest["source"]["dirty"] = True
        with self.assertRaisesRegex(ValueError, "Release marker"):
            pack.verify_archive(self.archive())

    def test_offset_overlap_and_out_of_flash(self):
        for offset in ("0x1000", "0x400000"):
            self.manifest["files"]["firmware.bin"]["offset"] = offset
            with self.assertRaisesRegex(ValueError, "Invalid flash"):
                pack.verify_archive(self.archive())

    def test_missing_image_rejected(self):
        del self.files["bootloader.bin"]
        del self.manifest["files"]["bootloader.bin"]
        with self.assertRaisesRegex(ValueError, "four flash"):
            pack.verify_archive(self.archive())

    def test_dirty_release_rejected_before_build(self):
        state = {"dirty": True}
        with patch.object(pack, "git_state", return_value=(self.root, state)), patch.object(pack, "run") as build:
            with self.assertRaisesRegex(ValueError, "clean Git"):
                pack.package(self.root, self.root / "dist", "pio", "esp32dev")
            build.assert_not_called()

    def test_validate_actual_image_sizes_and_offsets(self):
        images = []
        for name, data in self.files.items():
            path = self.root / name
            path.write_bytes(data)
            images.append([self.offsets[name], str(path)])
        self.assertEqual(pack.validate_images(images, "4MB"), self.files)
        images[0][0] = "0x10000"
        with self.assertRaisesRegex(ValueError, "overlapping"):
            pack.validate_images(images, "4MB")


if __name__ == "__main__":
    unittest.main()
