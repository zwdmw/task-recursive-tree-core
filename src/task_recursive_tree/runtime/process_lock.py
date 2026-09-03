from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import BinaryIO


class ProcessFileLockError(RuntimeError):
    pass


class ExclusiveProcessFileLock:
    """Advisory process lock released automatically when the process exits."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream: BinaryIO | None = self.path.open("a+b")
        try:
            self._prepare_lock_byte()
            self._acquire()
        except BaseException:
            self._stream.close()
            self._stream = None
            raise

    def close(self) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            self._release(stream)
        finally:
            stream.close()
            self._stream = None

    def _prepare_lock_byte(self) -> None:
        stream = self._require_stream()
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)

    def _acquire(self) -> None:
        stream = self._require_stream()
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(
                    stream.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
        except OSError as exc:
            if _is_lock_conflict(exc):
                raise ProcessFileLockError(
                    f"Process lock is already held: {self.path}"
                ) from exc
            raise

    @staticmethod
    def _release(stream: BinaryIO) -> None:
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass

    def _require_stream(self) -> BinaryIO:
        if self._stream is None:
            raise ProcessFileLockError(
                f"Process lock is closed: {self.path}"
            )
        return self._stream


def _is_lock_conflict(error: OSError) -> bool:
    conflict_codes = {errno.EACCES, errno.EAGAIN}
    deadlock = getattr(errno, "EDEADLK", None)
    if deadlock is not None:
        conflict_codes.add(deadlock)
    return error.errno in conflict_codes
