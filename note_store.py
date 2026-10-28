"""Read/edit the existing Markdown vault; never accept an arbitrary client path."""
import hashlib
import os
import tempfile
from pathlib import Path
import vault_files

MAX_NOTE_CHARS = 200_000
MAX_NOTE_BYTES = 800_000
MAX_NOTE_REQUEST_BYTES = MAX_NOTE_CHARS * 12 + 4096
PRIVATE_MODE = 0o600


class NoteError(ValueError):
    pass


def revision(text, identity=""):
    """Bind the opaque edit token to its server-owned vault-relative filename."""
    return hashlib.sha256(identity.encode("utf-8") + b"\0" + text.encode("utf-8")).hexdigest()


def stable_id(identity):
    """Opaque vault-relative identity survives numeric graph rebuilds."""
    return hashlib.sha256(identity.encode('utf-8')).hexdigest()


def locate(note, directory):
    root = Path(directory).resolve()
    relative = note.get("_path")
    if not isinstance(relative, str) or note.get("capture_hub"):
        raise NoteError("This derived constellation has no editable note file.")
    path = root / relative
    if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root and root in p.parents):
        raise NoteError("The note path changed. Refresh your notes.")
    if root not in path.resolve().parents or not path.is_file():
        raise NoteError("The note file is unavailable. Refresh your notes.")
    return path


def read(note, directory):
    path = locate(note, directory)
    identity = path.relative_to(Path(directory).resolve()).as_posix()
    try:
        text, info = vault_files.read_file(Path(directory).resolve(), identity)
    except (vault_files.VaultError, UnicodeError) as error:
        raise NoteError("This note cannot be read safely in the editor.") from error
    title = text.splitlines()[0][2:].strip() or path.stem if path.parent.name in ("captures", "imports") and text.startswith("# ") else path.stem
    return dict(id=note["id"], title=title, group=note["group"],
                text=text, stable_id=stable_id(identity), revision=revision(text, identity), edited_at=info.st_mtime)


def save(note, directory, text, expected_revision):
    if not isinstance(text, str) or len(text) > MAX_NOTE_CHARS or len(text.encode("utf-8")) > MAX_NOTE_BYTES:
        raise NoteError("The note exceeds the editor limit.")
    current = read(note, directory)
    if not isinstance(expected_revision, str) or current["revision"] != expected_revision:
        raise NoteError("The note changed on disk. Reopen it before saving.")
    path = locate(note, directory)
    identity = path.relative_to(Path(directory).resolve()).as_posix()
    temporary = None
    parent_fd = None
    try:
        root = Path(directory).resolve()
        parent_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for component in path.relative_to(root).parts[:-1]:
            child_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = child_fd
        # The descriptor anchors the final replacement to the original safe directory.
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".jarvis-edit-", delete=False) as output:
            temporary = Path(output.name)
            locate(note, directory)
            if os.stat(path.parent).st_ino != os.fstat(parent_fd).st_ino:
                raise NoteError("The note directory changed. Reopen it before saving.")
            os.chmod(temporary, path.stat().st_mode & 0o777 or PRIVATE_MODE)
            output.write(text.encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        # Check a second time after preparing the replacement to avoid stale edits.
        if read(note, directory)['revision'] != expected_revision:
            raise NoteError("The note changed while saving. Reopen it before retrying.")
        locate(note, directory)
        if os.stat(path.parent).st_ino != os.fstat(parent_fd).st_ino:
            raise NoteError("The note directory changed. Reopen it before saving.")
        os.replace(temporary.name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
        verified = read(note, directory)
        if verified["revision"] != revision(text, identity):
            raise NoteError("The saved note could not be verified. Inspect it before retrying.")
        return verified
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if parent_fd is not None:
            os.close(parent_fd)
