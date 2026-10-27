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
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0:
            sha = result.stdout.strip()
            if sha:
                return sha
    path = root / REVISION_NAME
    if path.is_file():
        try:
            sha = path.read_text(encoding="utf-8").strip()
        except OSError:
            sha = ""
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


def install_update(
    root: Path,
    sha: str,
    blob: bytes | None = None,
    opener=urllib.request.urlopen,
    on_status=None,
) -> None:
    def report(text: str) -> None:
        if on_status is not None:
            on_status(text)

    if (root / ".git").is_dir() and shutil.which("git"):
        report("Downloading")
        _update_git(root)
    else:
        report("Downloading")
        apply_archive(root, blob if blob is not None else download_archive(opener), sha)
    requirements = root / "requirements.txt"
    if requirements.is_file() and _missing_packages():
        report("Installing packages")
        env = os.environ.copy()
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "-r", str(requirements)],
                cwd=root,
                stdin=subprocess.DEVNULL,
                timeout=180,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Installing the update packages took too long.") from exc


def _git(root: Path, args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Downloading the update took too long.") from exc


def _update_git(root: Path) -> None:
    fetched = _git(root, ["fetch", "origin", BRANCH], timeout=90)
    if fetched.returncode != 0:
        detail = (fetched.stderr or fetched.stdout or "Could not download the update.").strip()
        raise RuntimeError(detail)
    moved = _replace_with_origin(root)
    if moved.returncode == 0:
        return
    cleaned = _git(root, ["clean", "-fd"])
    if cleaned.returncode != 0:
        detail = (cleaned.stderr or cleaned.stdout or "Could not replace the local files.").strip()
        raise RuntimeError(detail)
    moved = _replace_with_origin(root)
    if moved.returncode != 0:
        detail = (moved.stderr or moved.stdout or "Could not replace the local files.").strip()
        raise RuntimeError(detail)


def _replace_with_origin(root: Path) -> subprocess.CompletedProcess[str]:
    return _git(root, ["reset", "--hard", f"origin/{BRANCH}"])


def _missing_packages() -> bool:
    try:
        import esptool  # noqa: F401
        import serial  # noqa: F401
    except ImportError:
        return True
    return False


def relaunch() -> None:
    root = install_root()
    os.chdir(root)
    env = os.environ.copy()
    previous = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(root) if not previous else str(root) + os.pathsep + previous
    os.execve(sys.executable, [sys.executable, "-m", "meshchat"], env)


def _skip(relative: str) -> bool:
    parts = Path(relative).parts
    if not parts or parts[0] in {".git", ".venv", ".pio"}:
        return True
    return ".pio" in parts
