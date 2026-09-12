"""Build a Linux x64 extension with locked CPython 3.13 wheels."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", default="blender")
    args = parser.parse_args()
    stage = ROOT / "build/extension"
    if stage.exists():
        shutil.rmtree(stage)
    shutil.copytree(
        ROOT / "src/tyvrana_blender",
        stage,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    wheels = stage / "wheels"
    wheels.mkdir()
    temporary = ROOT / "build/temp"
    temporary.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "UV_CACHE_DIR": str(ROOT / ".uv-cache"),
        "UV_PYTHON_DOWNLOADS": "never",
        "PIP_CACHE_DIR": str(ROOT / ".uv-cache/pip"),
        "TMPDIR": str(temporary),
        "SOURCE_DATE_EPOCH": os.environ.get("SOURCE_DATE_EPOCH", "315532800"),
        "TZ": "UTC",
    }

    def run(command: list[str], *, quiet: bool = False) -> None:
        subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            check=True,
            stdout=subprocess.DEVNULL if quiet else None,
        )

    requirements = ROOT / "build/runtime-requirements.txt"
    run(
        [
            "uv",
            "export",
            "--locked",
            "--no-dev",
            "--no-emit-project",
            "--no-emit-package",
            "tyvrana-protocol",
            "--output-file",
            str(requirements),
        ],
        quiet=True,
    )
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--require-hashes",
            "--no-deps",
            "--only-binary=:all:",
            "--python-version",
            "3.13",
            "--implementation",
            "cp",
            "--abi",
            "cp313",
            "--platform",
            "manylinux_2_28_x86_64",
            "--platform",
            "manylinux_2_17_x86_64",
            "--platform",
            "manylinux2014_x86_64",
            "--dest",
            str(wheels),
            "--requirement",
            str(requirements),
        ]
    )
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    protocol = next(
        dep
        for dep in project["project"]["dependencies"]
        if dep.startswith("tyvrana-protocol @ ")
    )
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-cache-dir",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheels),
            protocol,
        ]
    )
    manifest = (ROOT / "blender_manifest.toml").read_text()
    assert tomllib.loads(manifest)["version"] == project["project"]["version"]
    bundled = sorted("./wheels/" + wheel.name for wheel in wheels.glob("*.whl"))
    assert bundled
    manifest = manifest.replace(
        "\n[permissions]",
        "\nwheels = " + json.dumps(bundled, indent=2) + "\n\n[permissions]",
    )
    (stage / "blender_manifest.toml").write_text(manifest)
    for name in ("LICENSE", "README.md"):
        shutil.copyfile(ROOT / name, stage / name)
    epoch = max(315532800, int(env["SOURCE_DATE_EPOCH"]))
    for path in stage.rglob("*"):
        if path.is_file():
            os.utime(path, (epoch, epoch))
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    run([args.blender, "--command", "extension", "validate", str(stage)])
    run(
        [
            args.blender,
            "--command",
            "extension",
            "build",
            "--source-dir",
            str(stage),
            "--output-dir",
            str(output),
        ]
    )
    archive = output / f"tyvrana_blender-{project['project']['version']}.zip"
    run([args.blender, "--command", "extension", "validate", str(archive)])
    print(f"Built {archive.name} with {len(bundled)} dependency wheels")


if __name__ == "__main__":
    main()
