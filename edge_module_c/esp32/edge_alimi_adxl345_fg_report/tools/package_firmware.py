"""Build an ESP32 installation archive without opening a serial port.

Only Python's standard library is required. PlatformIO is supplied explicitly.
Release builds reject any tracked or untracked repository changes by default.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import subprocess
import tempfile
import zipfile
from pathlib import Path


# Runs as a PlatformIO post extra_script, after its platform builder configured
# the image offsets and esptool flags. This never invokes an upload target.
CAPTURE_SCRIPT = '''
Import("env")
import json
from pathlib import Path
platform = env.PioPlatform()
packages = {}
for name in ("framework-arduinoespressif32", "toolchain-xtensa-esp32", "tool-esptoolpy"):
    directory = platform.get_package_dir(name)
    if not directory:
        raise RuntimeError("Required installed package missing: " + name)
    packages[name] = json.loads((Path(directory) / "package.json").read_text())
flags = [env.subst(str(value)) for value in env["UPLOADERFLAGS"]]
metadata = {
    "board": env["BOARD"], "framework": env["PIOFRAMEWORK"],
    "platform": json.loads((Path(platform.get_dir()) / "platform.json").read_text()),
    "packages": packages, "flags": flags,
    "python": env.subst("$PYTHONEXE"), "esptool": env.subst("$UPLOADER"),
    "build_type": env["BUILD_TYPE"], "upload_protocol": env["UPLOAD_PROTOCOL"],
    "images": [[str(offset), env.subst(str(path))] for offset, path in env["FLASH_EXTRA_IMAGES"]]
        + [[env.subst("$ESP32_APP_OFFSET"), env.subst("$BUILD_DIR/${PROGNAME}.bin")]],
}
Path(OUTPUT_PATH).write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
'''


def run(command: list[str], cwd: Path) -> str:
    result = subprocess.run(command, cwd=cwd, text=True, encoding="utf-8",
                            errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {command}\n{result.stdout}")
    return result.stdout


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_state(project: Path) -> tuple[Path, dict]:
    root = Path(run(["git", "rev-parse", "--show-toplevel"], project).strip())
    commit = run(["git", "rev-parse", "HEAD"], root).strip()
    status = run(["git", "status", "--porcelain=v1", "--untracked-files=all"], root)
    # Include unstaged and staged changes and untracked bytes to detect a source
    # change during compilation, even if its list of dirty paths stays identical.
    diff = run(["git", "diff", "HEAD", "--binary"], root).encode()
    untracked = run(["git", "ls-files", "--others", "--exclude-standard", "-z"], root)
    entries = []
    for name in sorted(filter(None, untracked.split("\0"))):
        entries.append([name, sha256((root / name).read_bytes())])
    fingerprint = sha256(diff + json.dumps(entries, ensure_ascii=False).encode())
    return root, {"commit": commit, "dirty": bool(status), "status": status.splitlines(),
                  "change_fingerprint_sha256": fingerprint}


def validate_images(images: list[list[str]], flash_size: str) -> dict[str, bytes]:
    if len(images) != 4 or {Path(p).name for _, p in images} != {
            "bootloader.bin", "partitions.bin", "boot_app0.bin", "firmware.bin"}:
        raise ValueError("Expected exactly the four ESP32 Arduino installation images")
    if not flash_size.endswith("MB") or not flash_size[:-2].isdigit():
        raise ValueError("An explicit numeric MB flash size is required")
    limit = int(flash_size[:-2]) * 1024 * 1024
    payload = {}
    previous_end = 0
    for offset, path in sorted(images, key=lambda item: int(item[0], 0)):
        address = int(offset, 0)
        data = Path(path).read_bytes()
        if not data or address < previous_end or address < 0 or address + len(data) > limit:
            raise ValueError(f"Empty, overlapping, or out-of-flash image: {path}")
        previous_end = address + len(data)
        payload[Path(path).name] = data
    return payload


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_archive(path: Path, files: dict[str, bytes]) -> None:
    # Stable ZIP metadata: same package inputs produce identical archive bytes.
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)


def verify_archive(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive members")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("schema_version") != 1 or manifest.get("board") != "esp32dev" or manifest.get("framework") != ["arduino"]:
            raise ValueError("Unsupported manifest schema/board/framework")
        if manifest["release"] != (not manifest["source"]["dirty"]):
            raise ValueError("Release marker contradicts Git dirty state")
        expected = {"manifest.json", *manifest["files"]}
        if set(names) != expected:
            raise ValueError("Unexpected or missing archive members")
        images = []
        previous_end = 0
        limit = int(manifest["flash"]["size"][:-2]) * 1024 * 1024
        for name, description in manifest["files"].items():
            if Path(name).name != name:
                raise ValueError("Archive filenames must be plain basenames")
            data = archive.read(name)
            if len(data) != description["bytes"] or sha256(data) != description["sha256"]:
                raise ValueError(f"Hash/size mismatch: {name}")
            if "offset" in description:
                images.append((int(description["offset"], 0), len(data)))
        for offset, size in sorted(images):
            if not size or offset < previous_end or offset + size > limit:
                raise ValueError("Invalid flash layout in manifest")
            previous_end = offset + size
        image_names = {name for name, entry in manifest["files"].items() if "offset" in entry}
        if image_names != {"bootloader.bin", "partitions.bin", "boot_app0.bin", "firmware.bin"}:
            raise ValueError("Manifest must describe four flash images")
        return manifest


def package(project: Path, output: Path, pio: str, environment: str,
            allow_dirty: bool = False) -> Path:
    project = project.resolve()
    output = output.resolve()
    root, source = git_state(project)
    if source["dirty"] and not allow_dirty:
        raise ValueError("Release packaging requires a clean Git tree; --allow-dirty is development only")
    # Keep binary outputs inside an explicitly ignored artifact directory.
    relative_output = output.relative_to(root)
    if not any(part in {"dist", "firmware-artifacts"} for part in relative_output.parts):
        raise ValueError("Output must be inside repository dist/ or firmware-artifacts/")
    run(["git", "check-ignore", str(output / "probe.bin")], root)
    config_path = project / "platformio.ini"
    config_data = config_path.read_bytes()
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(config_data.decode("utf-8-sig"))
    section = f"env:{environment}"
    if section not in config:
        raise ValueError(f"Unknown environment: {environment}")
    (project / ".pio").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="package-", dir=project / ".pio") as temporary:
        temporary = Path(temporary)
        evidence = temporary / "environment.json"
        hook = temporary / "capture.py"
        hook.write_text("OUTPUT_PATH = " + repr(str(evidence)) + "\n" + CAPTURE_SCRIPT, encoding="utf-8")
        if "platformio" not in config:
            config.add_section("platformio")
        # Own build products: another worker's normal `pio run` must not clean
        # or modify the files packaged here. This also forces a fresh build.
        config.set("platformio", "build_dir", (temporary / "build").as_posix())
        old_scripts = config.get(section, "extra_scripts", fallback=config.get("env", "extra_scripts", fallback=""))
        config.set(section, "extra_scripts", old_scripts + "\npost:" + hook.as_posix())
        generated_config = temporary / "platformio.ini"
        with generated_config.open("w", encoding="utf-8") as stream:
            config.write(stream)
        command = [pio, "run", "--project-dir", str(project), "--project-conf", str(generated_config), "-e", environment]
        log = run(command, project)
        metadata = json.loads(evidence.read_text(encoding="utf-8"))
        flags = metadata["flags"]
        flash = {name: flags[flags.index(option) + 1] for name, option in (
            ("chip", "--chip"), ("mode", "--flash_mode"), ("frequency", "--flash_freq"), ("size", "--flash_size"))}
        if any("$" in value for value in flash.values()):
            raise ValueError("Unresolved PlatformIO flash settings")
        payload = validate_images(metadata["images"], flash["size"])
    if metadata["board"] != "esp32dev" or metadata["framework"] != ["arduino"] or metadata["upload_protocol"] != "esptool":
        raise ValueError("This packager supports esp32dev / Arduino / esptool only")
    if git_state(project)[1] != source or config_path.read_bytes() != config_data:
        raise ValueError("Repository sources changed during build; retry after workers finish")
    offset_by_name = {Path(path).name: hex(int(offset, 0)) for offset, path in metadata["images"]}
    flash_args = ["--chip", flash["chip"], "--port", "<명시적으로_확인한_PORT>", "--baud", "115200",
                  "--before", "default_reset", "--after", "hard_reset", "write_flash", "-z",
                  "--flash_mode", flash["mode"], "--flash_freq", flash["frequency"], "--flash_size", flash["size"]]
    for name in sorted(offset_by_name, key=lambda name: int(offset_by_name[name], 0)):
        flash_args.extend([offset_by_name[name], name])
    command_text = "python -m esptool " + " ".join(flash_args)
    installation = f'''# ESP32 측정 펌웨어 설치 안내

대상: esp32dev, Arduino. 장치 업로드·실측 검증은 수행하지 않았습니다.
이 펌웨어는 측정용이며 모터 출력 제어가 없습니다. 부팅은 paused 상태입니다.
설치 후 시리얼 명령 start로 측정을 시작합니다.

1. manifest.json의 보드, flash 크기와 파일 SHA256을 확인합니다.
2. 실제 보드 모델, 명시적 포트, 전원·전압·배선 허용 조건, 모터 구동 조건을 확인합니다.
   하드웨어 조건을 확인하기 전에는 연결·구동하지 않습니다.
3. 기존 flash 전체를 별도 백업합니다. 쓰기 영역은 섹터 단위로 지워질 수 있습니다.
   이 패키지는 전체 erase를 요청하지 않지만 기존 NVS/설정 보존을 보장하지 않습니다.
4. 패키지를 풀고 manifest의 tool-esptoolpy 버전에 대응하는 esptool을 준비합니다.
   다음은 검토용 명령이며 자동 실행하지 않습니다. 포트 자리표시자를 실제 확인값으로 바꿉니다.

{command_text}

명령 실행 뒤 장치 부팅·센서·통신은 별도 실제 보드 시험이 필요합니다.
동일 입력의 ZIP 재생성은 결정적입니다. 펌웨어 컴파일의 바이트 단위 완전 재현성을 보증하지 않습니다.
'''
    payload["INSTALL_KO.txt"] = installation.encode("utf-8")
    payload["platformio.ini"] = config_data
    manifest = {"schema_version": 1, "environment": environment, "source": source,
                "release": not source["dirty"], "board": metadata["board"], "framework": metadata["framework"],
                "platform": metadata["platform"], "packages": metadata["packages"],
                "build_type": metadata["build_type"], "flash": flash,
                "build_evidence": {"method": "successful PlatformIO run with post-script environment capture",
                                   "log_sha256": sha256(log.encode()), "serial_access": False},
                "flash_command_argv": ["python", "-m", "esptool", *flash_args],
                "reproducibility": {"archive_same_inputs": True, "firmware_compile_verified": False},
                "files": {name: {"bytes": len(data), "sha256": sha256(data),
                                 **({"offset": offset_by_name[name]} if name in offset_by_name else {})}
                          for name, data in payload.items()}}
    payload["manifest.json"] = json_bytes(manifest)
    output.mkdir(parents=True, exist_ok=True)
    label = source["commit"][:12] + ("-dirty-" + source["change_fingerprint_sha256"][:8] if source["dirty"] else "")
    archive_path = output / f"edge-fg-{environment}-{label}.zip"
    write_archive(archive_path, payload)
    verify_archive(archive_path)
    archive_path.with_suffix(".manifest.json").write_bytes(payload["manifest.json"])
    archive_path.with_suffix(".sha256").write_text(sha256(archive_path.read_bytes()) + "  " + archive_path.name + "\n", encoding="ascii")
    return archive_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pio", default="pio")
    parser.add_argument("--environment", default="esp32dev")
    parser.add_argument("--allow-dirty", action="store_true", help="Development artifact; never marked release")
    parser.add_argument("--verify", type=Path, help="Verify an existing package without building")
    args = parser.parse_args()
    try:
        if args.verify:
            verify_archive(args.verify)
            print("Archive hashes, members and flash layout verified")
        else:
            print(package(args.project, args.output or args.project / "dist", args.pio,
                          args.environment, args.allow_dirty))
    except (OSError, ValueError, RuntimeError, KeyError, zipfile.BadZipFile) as error:
        parser.exit(1, f"Packaging failed: {error}\n")


if __name__ == "__main__":
    main()
