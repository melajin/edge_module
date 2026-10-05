"""Pinned public data downloader; never executes archive contents."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

SOURCE_PAGE = "https://data.mendeley.com/datasets/42v3s74gf9/1"
PINNED = {
    "UPATRAS.zip": (
        "https://data.mendeley.com/public-files/datasets/42v3s74gf9/files/1d3a0de7-5df2-40af-9834-4c4a3d944cca/file_downloaded",
        65281180,
        "ef67eab64164ee23ebb584375c734d817d110f722229d6f1db6c5afa74767bfb",
    ),
    "README.pdf": (
        "https://data.mendeley.com/public-files/datasets/42v3s74gf9/files/b72bf9cd-bd22-40f4-84d9-2a9fc16c351e/file_downloaded",
        1034488,
        "99166a4dd63707f7253c6b8004b91bc66d2280493252111b9116b1ec98b12774",
    ),
}

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def verify(path: Path, size: int, digest: str) -> None:
    if path.stat().st_size != size or sha256_file(path) != digest:
        raise ValueError(f"Pinned source size/SHA256 mismatch: {path}")

def prepare(output: Path) -> dict:
    target = output / "sources"
    target.mkdir(parents=True, exist_ok=True)
    records = []
    for name, (url, size, digest) in PINNED.items():
        path = target / name
        if path.exists():
            verify(path, size, digest)
        else:
            partial = path.with_suffix(path.suffix + ".partial")
            request = urllib.request.Request(url, headers={"User-Agent": "UPATRAS-offline-validation/1.0"})
            with urllib.request.urlopen(request, timeout=120) as source, partial.open("wb") as sink:
                while block := source.read(1024 * 1024):
                    sink.write(block)
            verify(partial, size, digest)
            partial.replace(path)
        records.append({"name": name, "url": url, "bytes": size, "sha256": digest})
    with zipfile.ZipFile(target / "UPATRAS.zip") as archive:
        # Central directory only: no locked waveform bytes are consumed here.
        members = [{"member": item.filename, "bytes": item.file_size, "crc32": item.CRC}
                   for item in archive.infolist()]
    manifest = {"source_page": SOURCE_PAGE, "license": "CC BY 4.0", "files": records,
                "archive_directory": members, "raw_opened": False,
                "third_party_code_executed": False}
    (output / "source_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
