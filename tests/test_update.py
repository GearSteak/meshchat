import io
import unittest
import zipfile
from pathlib import Path

from meshchat.update import (
    UpdateStatus,
    apply_archive,
    local_revision,
    parse_name_status,
    porcelain_paths,
    remote_revision,
    select_updates,
)


class _Body:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


class UpdateTests(unittest.TestCase):
    def test_github_commit_sha_is_read(self) -> None:
        def opener(_request, timeout=0):
            self.assertGreater(timeout, 0)
            return _Body(b'{"sha":"abc1234"}')

        self.assertEqual(remote_revision(opener), "abc1234")

    def test_local_files_are_not_replaced(self) -> None:
        kept = porcelain_paths(" M meshchat/app.py\n?? notes/mine.txt\nR  old.py -> meshchat/link.py\n")
        self.assertEqual(kept, {"meshchat/app.py", "notes/mine.txt", "meshchat/link.py"})
        rows = parse_name_status("M\tmeshchat/app.py\nM\tmeshchat/update.py\nD\tmeshchat/old.py\n")
        take, remove = select_updates(rows, kept)
        self.assertEqual(take, ["meshchat/update.py"])
        self.assertEqual(remove, ["meshchat/old.py"])
        self.assertNotIn("meshchat/app.py", take)

    def test_status_is_available_when_the_commit_differs(self) -> None:
        same = UpdateStatus(local="abc", remote="abc")
        newer = UpdateStatus(local="abc", remote="def")
        unknown = UpdateStatus(local=None, remote="def")
        self.assertFalse(same.available)
        self.assertTrue(newer.available)
        self.assertTrue(unknown.available)

    def test_saved_revision_is_the_local_commit(self) -> None:
        folder = Path("tests") / "_tmp_revision"
        folder.mkdir(parents=True, exist_ok=True)
        try:
            (folder / ".meshchat-revision").write_text("abc123\n", encoding="utf-8")
            self.assertEqual(local_revision(folder), "abc123")
        finally:
            (folder / ".meshchat-revision").unlink(missing_ok=True)
            folder.rmdir()

    def test_archive_replaces_app_files_and_records_the_commit(self) -> None:
        folder = Path("tests") / "_tmp_install"
        folder.mkdir(parents=True, exist_ok=True)
        try:
            (folder / "old.txt").write_text("old", encoding="utf-8")
            blob = io.BytesIO()
            with zipfile.ZipFile(blob, "w") as archive:
                archive.writestr("meshchat-main/meshchat/app.py", "print('new')\n")
                archive.writestr("meshchat-main/.pio/skip.txt", "nope")
                archive.writestr("meshchat-main/.venv/bin/python", "nope")
            apply_archive(folder, blob.getvalue(), "deadbeef")
            self.assertEqual((folder / "meshchat" / "app.py").read_text(encoding="utf-8"), "print('new')\n")
            self.assertFalse((folder / ".pio").exists())
            self.assertFalse((folder / ".venv").exists())
            self.assertEqual((folder / "old.txt").read_text(encoding="utf-8"), "old")
            self.assertEqual((folder / ".meshchat-revision").read_text(encoding="utf-8").strip(), "deadbeef")
        finally:
            for path in sorted(folder.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                else:
                    path.rmdir()
            folder.rmdir()


if __name__ == "__main__":
    unittest.main()
