"""Build a clean Windows source distribution and verify its ZIP contents."""
from __future__ import annotations

import os
import hashlib
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
RELEASE = ROOT / "release"
NAME = "AI_Rehabilitation_Assistant_Portable"
PACKAGE = RELEASE / NAME
ARCHIVE = RELEASE / f"{NAME}.zip"

FILES = (
    "apps/desktop/main.py", "shared/config.py", "requirements-windows.txt", ".env.example",
    "SETUP.bat", "START.bat", "START_DEBUG.bat", "CHECK_SYSTEM.bat",
    "scripts/check_system.py", "README.txt", "HUONG_DAN_SU_DUNG_KHACH_HANG.docx",
    "deploy/windows/packaging/portable/install_python.ps1", "deploy/windows/packaging/portable/setup.ps1",
)
TREES = ("app", "adapters", "core", "services", "features/face_monitoring/standalone",
         "features/emotion/model", "features/exercise_rehab", "features/fall_risk/hfd_model", "docs/medication_reminder")
EXCLUDED_DIRS = {"__pycache__", ".git", ".venv", "archive", "HumanFallDetection"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".tmp", ".log"}
REQUIRED = (
    "apps/desktop/main.py", "shared/config.py", "SETUP.bat", "START.bat", "scripts/check_system.py",
    "deploy/windows/packaging/portable/install_python.ps1", "deploy/windows/packaging/portable/setup.ps1",
    "requirements-windows.txt", "features/face_monitoring/standalone/models/opencv_face_detector_uint8.pb",
    "features/emotion/model/models/expression_net.pth",
)


def included(path: Path) -> bool:
    return (not any(part in EXCLUDED_DIRS for part in path.parts)
            and path.suffix.lower() not in EXCLUDED_SUFFIXES
            and path.name not in {".env", "medication.sqlite3"})


def build() -> None:
    missing = [name for name in REQUIRED if not (ROOT / name).is_file()]
    if missing:
        raise RuntimeError(f"Missing required source files: {', '.join(missing)}")
    RELEASE.mkdir(exist_ok=True)
    staging = RELEASE / f".{NAME}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    try:
        for name in FILES:
            source = ROOT / name
            if source.is_file():
                destination = staging / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        for name in TREES:
            source = ROOT / name
            if not source.is_dir():
                raise RuntimeError(f"Missing source directory: {name}")
            for parent, dirs, files in os.walk(source):
                dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS
                           and not (name == "features/fall_risk/hfd_model" and d == "models")]
                for filename in files:
                    path = Path(parent) / filename
                    if included(path):
                        target = staging / path.relative_to(ROOT)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, target)
        # Keep profiles, registered faces, credentials and runtime databases local.
        for name in ("runtime/users", "runtime/sessions", "runtime/logs",
                     "runtime/shared/data", "runtime/shared/results"):
            (staging / name).mkdir(parents=True, exist_ok=True)
            (staging / name / ".gitkeep").touch()
        (staging / "VERSION.txt").write_text("SETUP v3 - 2026-10-03\n", encoding="utf-8")
        missing = [name for name in REQUIRED if not (staging / name).is_file()]
        if missing:
            raise RuntimeError(f"Incomplete staged package: {', '.join(missing)}")
        if PACKAGE.exists():
            shutil.rmtree(PACKAGE)
        staging.rename(PACKAGE)
        temporary_zip = RELEASE / f".{NAME}.zip.tmp"
        if temporary_zip.exists():
            temporary_zip.unlink()
        with zipfile.ZipFile(temporary_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as out:
            for path in sorted(PACKAGE.rglob("*")):
                if path.is_file():
                    out.write(path, Path(NAME) / path.relative_to(PACKAGE))
        with zipfile.ZipFile(temporary_zip) as archive:
            bad = archive.testzip()
            if bad:
                raise RuntimeError(f"Corrupt ZIP member: {bad}")
            members = set(archive.namelist())
            for name in REQUIRED:
                if f"{NAME}/{name}" not in members:
                    raise RuntimeError(f"Missing ZIP member: {name}")
        temporary_zip.replace(ARCHIVE)
        versioned = RELEASE / f"{NAME}_SETUP_v3.zip"
        shutil.copy2(ARCHIVE, versioned)
        digest = hashlib.sha256(versioned.read_bytes()).hexdigest()
        versioned.with_suffix(".zip.sha256").write_text(f"{digest}  {versioned.name}\n", encoding="ascii")
        print(f"Package: {PACKAGE}")
        print(f"ZIP: {ARCHIVE} ({ARCHIVE.stat().st_size / 1048576:.1f} MiB)")
    finally:
        if staging.exists():
            shutil.rmtree(staging)


if __name__ == "__main__":
    try:
        build()
    except Exception as error:
        print(f"BUILD FAILED: {error}", file=sys.stderr)
        sys.exit(1)
