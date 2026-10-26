"""Check GitHub and replace this install with the latest Mesh Chat."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO = "GearSteak/meshchat"
BRANCH = "main"
COMMIT_URL = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
ZIP_URL = f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.zip"
REVISION_NAME = ".meshchat-revision"


@dataclass
class UpdateStatus:
    local: str | None
    remote: str

    @property
    def available(self) -> bool:
        return self.local != self.remote


def install_root() -> Path:
    return Path(__file__).resolve().parent.parent


def local_revision(root: Path) -> str | None:
    if (root / ".git").is_dir() and shutil.which("git"):
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            sha = result.stdout.strip()
            if sha:
                return sha
    path = root / REVISION_NAME
    if path.is_file():
        sha = path.read_text(encoding="utf-8").strip()
        if sha:
            return sha
    return None


def remote_revision(opener=urllib.request.urlopen) -> str:
    request = urllib.request.Request(
        COMMIT_URL,
        headers={
            "User-Agent": "MeshChat",
            "Accept": "application/vnd.github+json",
        },
    )
    with opener(request, timeout=20) as response:
        payload = json.load(response)
    sha = payload.get("sha")
    if not isinstance(sha, str) or len(sha) < 7:
        raise RuntimeError("GitHub did not return a commit.")
    return sha


def check_update(root: Path | None = None, opener=urllib.request.urlopen) -> UpdateStatus:
    folder = root or install_root()
    return UpdateStatus(local=local_revision(folder), remote=remote_revision(opener))


def download_archive(opener=urllib.request.urlopen) -> bytes:
    request = urllib.request.Request(ZIP_URL, headers={"User-Agent": "MeshChat"})
    with opener(request, timeout=60) as response:
        return response.read()


def apply_archive(root: Path, blob: bytes, sha: str) -> None:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = [name for name in archive.namelist() if name]
        if not names:
            raise RuntimeError("The download was empty.")
        prefix = names[0].split("/")[0] + "/"
        wrote = False
        for info in archive.infolist():
            if info.is_dir() or not info.filename.startswith(prefix):
                continue
            relative = info.filename[len(prefix) :]
            if _skip(relative):
                continue
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(info))
            wrote = True
    if not wrote:
        raise RuntimeError("The download had no Mesh Chat files.")
    (root / REVISION_NAME).write_text(sha + "\n", encoding="utf-8")


def install_update(root: Path, sha: str, blob: bytes | None = None, opener=urllib.request.urlopen) -> None:
    if (root / ".git").is_dir() and shutil.which("git"):
        result = subprocess.run(
            ["git", "pull", "--ff-only", "origin", BRANCH],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "git pull failed").strip()
            raise RuntimeError(detail)
    else:
        apply_archive(root, blob if blob is not None else download_archive(opener), sha)
    requirements = root / "requirements.txt"
    if requirements.is_file():
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
            cwd=root,
            check=False,
        )


def relaunch() -> None:
    root = install_root()
    os.chdir(root)
    os.execv(sys.executable, [sys.executable, "-m", "meshchat"])


def _skip(relative: str) -> bool:
    parts = Path(relative).parts
    if not parts or parts[0] in {".git", ".venv", ".pio"}:
        return True
    return ".pio" in parts
